#!/usr/bin/env python3
"""locked-worktree-guard用の低レベルシェルトークン化ユーティリティ。

Why:
    シェルコマンドを正確に解析するには、シェル演算子やリダイレクト、
    引用符を適切に処理する必要がある。低レベルのトークン化を一箇所に
    まとめることで、コマンド解析の信頼性を向上させる。

What:
    - シェル演算子の正規化（&&, ||, ;, |）
    - シェルリダイレクト検出
    - cdターゲット抽出
    - rmコマンドパス抽出
    - gitグローバルフラグからのベースディレクトリ抽出

Remarks:
    - command_parser.py: 高レベルのコマンド固有解析を担当
    - 本モジュールは汎用的なシェルトークン化に特化
    - 引用符内の演算子処理は妥協（検出のみで実行はしないため許容）

Changelog:
    - silenvx/dekita#1106: シェルリダイレクト検出追加
    - silenvx/dekita#1676: cdターゲット抽出追加
"""

import re
import shlex
from pathlib import Path


def normalize_shell_operators(command: str) -> str:
    """Add spaces around shell operators for proper tokenization.

    This ensures operators like '&&', '||', ';', '|' are separated from
    adjacent tokens so shlex.split() can tokenize them properly.

    Example: 'foo&&git bar' -> 'foo && git bar'

    Note: This may modify operators inside quoted strings, but since this
    is only used for command detection (not execution), this trade-off
    is acceptable for security purposes.
    """
    # Process longer operators first to avoid partial matches
    for op in ["&&", "||"]:
        # Add space before if preceded by non-whitespace
        command = re.sub(rf"(\S)({re.escape(op)})", r"\1 \2", command)
        # Add space after if followed by non-whitespace
        command = re.sub(rf"({re.escape(op)})(\S)", r"\1 \2", command)

    # Handle single | carefully (not matching || or already-spaced |)
    # Use [^\s|] to exclude pipe characters from matching as adjacent non-whitespace
    command = re.sub(r"([^\s|])\|(?!\|)", r"\1 |", command)
    command = re.sub(r"(?<!\|)\|([^\s|])", r"| \1", command)

    # Handle ;
    command = re.sub(r"(\S);", r"\1 ;", command)
    command = re.sub(r";(\S)", r"; \1", command)

    return command


def is_shell_redirect(token: str) -> bool:
    """Check if a token is a shell redirect operator.

    Detects patterns like:
    - 2>&1, >&2, 1>&2 (fd redirection)
    - >file, >>file, 2>file, 2>>file (output redirection with target)
    - >, >> (bare redirect operators, target is next token)
    - <file (input redirection)

    Issue #1106: Shell redirects were being passed as arguments to gh command.

    Args:
        token: A single token from shlex.split()

    Returns:
        True if the token is a shell redirect, False otherwise.
    """
    # Pattern: digit(s) followed by > or >> and optional target
    # e.g., 2>&1, 1>&2, 2>file, 2>>file, >, >>
    if re.match(r"^\d*>{1,2}", token):
        return True
    # Pattern: < for input redirection
    # e.g., <file, 0<file, <
    if re.match(r"^\d*<", token):
        return True
    # Pattern: >&digit (shorthand for 1>&digit)
    # e.g., >&2
    if re.match(r"^>&\d", token):
        return True
    return False


def is_bare_redirect_operator(token: str) -> bool:
    """Check if a token is a bare redirect operator (without target).

    These are operators like '>', '>>', '<' that have their target
    as the next token when spaced apart (e.g., '> output.log').

    Issue #1106: When redirects are written with spaces, shlex.split produces
    separate tokens for the operator and target. We need to skip both.

    Args:
        token: A single token from shlex.split()

    Returns:
        True if the token is a bare redirect operator, False otherwise.
    """
    # Bare redirect operators: >, >>, <, 2>, 2>>, etc.
    return bool(re.match(r"^\d*>{1,2}$", token) or re.match(r"^\d*<$", token))


def extract_cd_target_before_git(command: str) -> str | None:
    """Extract cd target directory that precedes a git worktree remove command.

    Handles patterns like:
    - cd /path && git worktree remove ...
    - cd /path ; git worktree remove ...
    - cd "/path with spaces" && git worktree remove ...

    This fixes Issue #665: cd command's effect is not recognized by the guard,
    causing path resolution to fail when using relative paths after cd.

    Args:
        command: The full command string.

    Returns:
        The cd target directory if found before git worktree remove, None otherwise.
    """
    # Normalize shell operators first
    normalized = normalize_shell_operators(command)
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        tokens = normalized.split()

    # Look for 'cd' followed by path, then separator, then git worktree remove
    # Pipeline handling: cd in a pipeline (e.g., "cd /path | git" or "echo | cd /tmp && git")
    # runs in a subshell and doesn't affect the parent shell where subsequent commands run.
    cd_target = None
    in_pipeline = False  # True when we're inside a pipeline (after |)
    i = 0
    while i < len(tokens):
        token = tokens[i]

        # Found cd command
        if token == "cd":
            # Find the effective cd target, skipping cd flags like -P or -L.
            # Preserve special handling for `cd` with no args and `cd -` (previous dir).
            j = i + 1
            potential_target = None
            found_target = False
            while j < len(tokens) and tokens[j] not in ("&&", "||", ";", "|"):
                t = tokens[j]
                # Skip cd flags that start with '-' except for a lone '-',
                # which is a valid target meaning "previous directory".
                if t.startswith("-") and t != "-":
                    j += 1
                    continue
                # First non-flag, non-separator token is the target
                potential_target = t
                found_target = True
                break

            if found_target:
                # Check what separator follows the cd command
                # Find the next separator to determine if cd is in a pipeline
                k = j + 1
                while k < len(tokens) and tokens[k] not in ("&&", "||", ";", "|"):
                    k += 1

                # Only set cd_target if:
                # 1. Not currently in a pipeline context (after a previous |)
                # 2. This cd is not followed by | (which would make it part of a new pipeline)
                separator = tokens[k] if k < len(tokens) else None
                if not in_pipeline and separator != "|":
                    cd_target = potential_target
                i = j + 1
            else:
                # No valid cd target found before a separator; advance past 'cd' only.
                i += 1
            continue

        # Check if this is git worktree remove
        if token == "git":
            # If we found cd before this git command, return the cd target
            # Check if this git command is worktree remove
            j = i + 1
            # Skip git global flags
            flags_with_args = {"-C", "--git-dir", "--work-tree", "-c"}
            while j < len(tokens):
                t = tokens[j]
                if t in ("&&", "||", ";", "|"):
                    break
                if t.startswith("-"):
                    if "=" in t:
                        j += 1
                    elif t in flags_with_args:
                        j += 2
                    else:
                        j += 1
                else:
                    break

            # Check for 'worktree remove'
            if j < len(tokens) and tokens[j] == "worktree":
                if j + 1 < len(tokens) and tokens[j + 1] == "remove":
                    return cd_target

        # Handle shell operators for pipeline and separator tracking
        if token == "|":
            # Enter pipeline context: subsequent cd commands run in subshells
            # and don't affect the parent shell where later commands run
            in_pipeline = True
        elif token in ("&&", "||", ";"):
            # Exit pipeline context: commands after these run in parent shell
            in_pipeline = False

        i += 1

    return None


def check_single_git_worktree_remove(tokens: list[str], start_idx: int) -> bool:
    """Check if tokens starting at start_idx form a git worktree remove command.

    Args:
        tokens: List of command tokens.
        start_idx: Index of 'git' token to check.

    Returns:
        True if this is a git worktree remove command, False otherwise.
    """
    if start_idx >= len(tokens) or tokens[start_idx] != "git":
        return False

    # Skip global flags to find 'worktree'
    # Flags that take arguments (exhaustive list for git global options we handle)
    flags_with_args = {"-C", "--git-dir", "--work-tree", "-c"}
    i = start_idx + 1
    while i < len(tokens):
        token = tokens[i]
        # Stop at command separators
        if token in ("&&", "||", ";", "|"):
            return False
        if token.startswith("-"):
            # Check for --flag=value format
            if "=" in token:
                i += 1
            elif token in flags_with_args:
                # Skip flag and its argument, but only if argument exists
                if i + 1 < len(tokens) and tokens[i + 1] not in ("&&", "||", ";", "|"):
                    i += 2
                else:
                    # Malformed command: flag expects argument but none present
                    break
            else:
                # Unknown flag, skip just the flag
                i += 1
        else:
            break

    # Check if we found 'worktree' followed by 'remove'
    if i < len(tokens) and tokens[i] == "worktree":
        if i + 1 < len(tokens) and tokens[i + 1] == "remove":
            return True

    return False


def extract_base_dir_from_git_segment(tokens: list[str], git_idx: int) -> str | None:
    """Extract base directory (-C, --work-tree, --git-dir) from a git command segment.

    Args:
        tokens: List of command tokens.
        git_idx: Index of 'git' token.

    Returns:
        The base directory if found, None otherwise.
    """
    if git_idx >= len(tokens) or tokens[git_idx] != "git":
        return None

    i = git_idx + 1
    while i < len(tokens):
        token = tokens[i]

        # Stop at command separators or worktree subcommand
        if token in ("&&", "||", ";", "|", "worktree"):
            break

        # -C flag
        if token == "-C":
            if i + 1 < len(tokens) and tokens[i + 1] not in ("&&", "||", ";", "|"):
                return tokens[i + 1]
            break

        # --work-tree flag (two forms: --work-tree=/path or --work-tree /path)
        if token.startswith("--work-tree="):
            return token[len("--work-tree=") :]
        if token == "--work-tree":
            if i + 1 < len(tokens) and tokens[i + 1] not in ("&&", "||", ";", "|"):
                return tokens[i + 1]
            break

        # --git-dir flag (extract parent directory)
        if token.startswith("--git-dir="):
            git_dir = token[len("--git-dir=") :]
            if git_dir.endswith(".git"):
                return str(Path(git_dir).parent)
            return git_dir
        if token == "--git-dir":
            if i + 1 < len(tokens) and tokens[i + 1] not in ("&&", "||", ";", "|"):
                git_dir = tokens[i + 1]
                if git_dir.endswith(".git"):
                    return str(Path(git_dir).parent)
                return git_dir
            break

        i += 1

    return None


def extract_rm_paths(command: str) -> list[str]:
    """Extract all paths from rm commands in a command string.

    This is a shared helper for get_rm_target_worktrees() and
    get_rm_target_orphan_worktrees() to avoid code duplication.

    Handles:
    - Basic rm commands: rm -rf foo
    - Chained commands: rm A && rm B, rm A; rm B
    - Sudo: sudo rm -rf foo, sudo -u root rm foo
    - Environment variables: FOO=1 rm -rf bar
    - Full paths: /bin/rm, /usr/bin/rm

    Args:
        command: The command string to parse.

    Returns:
        List of path strings extracted from rm commands.
    """
    # Normalize shell operators first (handles cases like 'rm -rf foo&&rm bar')
    normalized = normalize_shell_operators(command)
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        tokens = normalized.split()

    if not tokens:
        return []

    # Collect paths from ALL rm commands in chained commands
    paths: list[str] = []
    i = 0

    # Helper to check if a token is an rm command (handles full paths)
    def is_rm_command(token: str) -> bool:
        if token == "rm":
            return True
        if token.endswith("/rm"):
            return True
        return False

    at_command_start = True
    after_sudo = False

    while i < len(tokens):
        token = tokens[i]

        # Command separators mark the start of a new command segment
        if token in ("|", ";", "&&", "||"):
            at_command_start = True
            after_sudo = False
            i += 1
            continue

        # Skip environment variable assignments (e.g., FOO=1 rm -rf)
        if at_command_start and "=" in token and not token.startswith("-"):
            i += 1
            continue

        # Handle sudo
        if token == "sudo" and at_command_start:
            after_sudo = True
            i += 1
            continue

        # While in sudo context, look for rm command
        if after_sudo:
            sudo_flags_with_args = {"-u", "-g", "-r", "-p", "-D", "-h", "-C", "-T"}

            if token.startswith("-"):
                if token in sudo_flags_with_args:
                    i += 1
                    if i < len(tokens) and tokens[i] not in ("|", ";", "&&", "||"):
                        i += 1
                else:
                    i += 1
                continue

            if is_rm_command(token):
                i += 1
                at_command_start = False
                after_sudo = False
                while i < len(tokens):
                    arg = tokens[i]
                    if arg in ("|", ";", "&&", "||"):
                        break
                    if not arg.startswith("-"):
                        paths.append(arg)
                    i += 1
            else:
                at_command_start = False
                after_sudo = False
                i += 1
            continue

        # Detect rm command at the start of a segment
        if is_rm_command(token) and at_command_start:
            i += 1
            at_command_start = False
            while i < len(tokens):
                arg = tokens[i]
                if arg in ("|", ";", "&&", "||"):
                    break
                if not arg.startswith("-"):
                    paths.append(arg)
                i += 1
        else:
            at_command_start = False
            i += 1

    return paths
