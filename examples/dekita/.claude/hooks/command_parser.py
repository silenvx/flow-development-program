#!/usr/bin/env python3
"""locked-worktree-guard用のコマンド解析ユーティリティ。

Why:
    gh prコマンドやgit worktreeコマンドを正確に解析し、
    ロック中worktreeへの操作を検出する必要がある。

What:
    - Git worktreeコマンド検出・パス抽出
    - gh prコマンド解析（変更系/読み取り系の判定）
    - ci-monitor.pyコマンド検出

Remarks:
    - shell_tokenizer.py: 低レベルシェルトークン化を担当
    - 本モジュールはコマンド固有の解析ロジックに特化
    - shlex.split()で引用符内の誤検知を防止

Changelog:
    - silenvx/dekita#608: ci-monitor.pyコマンド検出追加
    - silenvx/dekita#649: --delete-branch検出追加
"""

import shlex
from pathlib import Path

from lib.github import parse_gh_pr_command
from shell_tokenizer import (
    check_single_git_worktree_remove,
    extract_base_dir_from_git_segment,
    extract_cd_target_before_git,
    extract_rm_paths,
    is_bare_redirect_operator,
    is_shell_redirect,
    normalize_shell_operators,
)

# Re-export for backward compatibility
__all__ = [
    "normalize_shell_operators",
    "extract_cd_target_before_git",
    "is_shell_redirect",
    "is_bare_redirect_operator",
    "check_single_git_worktree_remove",
    "extract_base_dir_from_git_segment",
    "is_modifying_command",
    "has_delete_branch_flag",
    "get_merge_positional_arg",
    "has_merge_positional_arg",
    "extract_first_merge_command",
    "is_gh_pr_command",
    "is_ci_monitor_command",
    "is_worktree_remove_command",
    "extract_git_base_directory",
    "extract_worktree_path_from_git_command",
    "extract_unlock_path_from_git_command",
    "extract_unlock_targets_from_command",
    "find_git_worktree_remove_position",
    "extract_worktree_path_from_command",
    "extract_rm_paths",
]


def is_modifying_command(command: str) -> bool:
    """Check if the command modifies PR state.

    Handles global flags that may appear before 'pr':
    - gh pr merge 123
    - gh --repo owner/repo pr merge 123

    Read-only commands (allowed):
    - gh pr view
    - gh pr list
    - gh pr checks
    - gh pr diff
    - gh pr status

    Modifying commands (blocked if locked):
    - gh pr merge
    - gh pr checkout
    - gh pr close
    - gh pr reopen
    - gh pr edit
    - gh pr comment
    - gh pr review

    Uses shlex.split() to avoid false positives from quoted strings.
    """
    modifying_subcommands = {
        "merge",
        "checkout",
        "close",
        "reopen",
        "edit",
        "comment",
        "review",
    }

    subcommand, _ = parse_gh_pr_command(command)
    return subcommand in modifying_subcommands


def has_delete_branch_flag(command: str) -> bool:
    """Check if gh pr merge command has --delete-branch or -d flag.

    Handles:
    - gh pr merge 123 --delete-branch
    - gh pr merge --delete-branch 123
    - gh pr merge 123 -d
    - gh pr merge -d 123
    - gh pr merge 123 --squash --delete-branch
    - gh pr merge --delete-branch&&echo ok (operators glued to tokens)

    Args:
        command: The full command string

    Returns:
        True if --delete-branch or -d flag is present, False otherwise.
    """
    # Normalize shell operators (&&, ||, ;, |) to ensure proper tokenization
    # e.g., "gh pr merge --delete-branch&&echo ok" -> "gh pr merge --delete-branch && echo ok"
    normalized = normalize_shell_operators(command)
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        tokens = normalized.split()

    # Find 'gh' and 'pr' and 'merge' tokens
    gh_start = None
    for i, token in enumerate(tokens):
        if token == "gh":
            gh_start = i
            break

    if gh_start is None:
        return False

    # Look for --delete-branch or -d in tokens after 'gh'
    # Note: -d is used by gh pr merge for --delete-branch
    for token in tokens[gh_start + 1 :]:
        if token in ("|", ";", "&&", "||"):
            break
        if token == "--delete-branch" or token == "-d":
            return True

    return False


def get_merge_positional_arg(command: str) -> str | None:
    """Get the positional PR selector argument from a gh pr merge command.

    The selector may be a PR number, branch name, URL, or any other form that
    `gh pr merge` accepts. This function does not interpret the selector; it
    simply returns the first positional argument after ``merge``.

    Extracts:
    - gh pr merge 123 -> "123"
    - gh pr merge feature-branch -> "feature-branch"
    - gh pr merge https://github.com/owner/repo/pull/123 -> "https://..."
    - gh pr merge --delete-branch -> None (no positional arg)
    - gh pr merge --squash --delete-branch -> None (no positional arg)

    Args:
        command: The full command string.

    Returns:
        The raw positional selector string (PR number, branch name, URL, etc.),
        or None if no positional argument is present.
    """
    # Normalize shell operators (&&, ||, ;, |) to ensure proper tokenization
    # e.g., "gh pr merge --delete-branch&&echo ok" -> "gh pr merge --delete-branch && echo ok"
    normalized = normalize_shell_operators(command)
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        tokens = normalized.split()

    # Find 'merge' token position
    merge_idx = None
    in_gh_pr = False
    for i, token in enumerate(tokens):
        if token == "gh":
            in_gh_pr = True
        elif in_gh_pr and token == "pr":
            continue
        elif in_gh_pr and token == "merge":
            merge_idx = i
            break
        elif token in ("|", ";", "&&", "||"):
            in_gh_pr = False

    if merge_idx is None:
        return None

    # Look for positional argument after 'merge'
    # Skip flags (--flag, -f) and their values
    # All gh pr merge flags that take arguments (from gh pr merge --help):
    flags_with_args = {
        "--body",
        "-b",  # Body text for merge commit
        "--body-file",
        "-F",  # Read body from file
        "--match-head-commit",  # Commit SHA to match
        "--subject",
        "-t",  # Subject text for merge commit
        "--author-email",
        "-A",  # Email for merge commit author
        "--repo",
        "-R",  # Select another repository
    }
    i = merge_idx + 1
    while i < len(tokens):
        token = tokens[i]
        # Stop at command separators
        if token in ("|", ";", "&&", "||"):
            break
        # Skip flags
        if token.startswith("-"):
            if "=" in token:
                # --flag=value format
                i += 1
            elif token in flags_with_args:
                # Skip flag and its value
                i += 2
            else:
                # Boolean flag
                i += 1
        else:
            # Found a positional argument (PR number or branch name)
            return token

    return None


def has_merge_positional_arg(command: str) -> bool:
    """Check if gh pr merge command has a positional argument.

    Convenience wrapper around get_merge_positional_arg.

    Returns:
        True if there's a positional argument, False otherwise.
    """
    return get_merge_positional_arg(command) is not None


def extract_first_merge_command(command: str) -> str:
    """Extract only the first gh pr merge command from a potentially chained command.

    This is critical for safe execution - we must NOT run any chained commands
    (like && echo done, || rm -rf, etc.) that may have been in the original command.

    Issue #1106: Also removes shell redirects (like 2>&1) which should not be
    passed as arguments to the gh command.

    Args:
        command: The original command which may include chained commands.

    Returns:
        Only the first gh pr merge command portion, without redirects.
    """
    # Shell operators that indicate command chaining
    shell_operators = {"&&", "||", ";", "|", "&"}

    # Normalize shell operators for tokenization
    normalized = normalize_shell_operators(command)
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        tokens = normalized.split()

    # Extract tokens up to the first shell operator
    first_command_tokens = []
    for token in tokens:
        if token in shell_operators:
            break
        first_command_tokens.append(token)

    # Now remove --delete-branch/-d and shell redirects from the first command only
    result_tokens = []
    i = 0
    while i < len(first_command_tokens):
        token = first_command_tokens[i]
        if token in ("--delete-branch", "-d"):
            i += 1
            continue
        # Issue #1106: Skip shell redirect tokens
        if is_shell_redirect(token):
            # If this is a bare redirect operator (e.g., '>'), skip the next token too
            # because it's the redirect target (e.g., 'output.log' in '> output.log')
            if is_bare_redirect_operator(token) and i + 1 < len(first_command_tokens):
                i += 2  # Skip both operator and target
            else:
                i += 1  # Skip only the redirect (target is attached, e.g., '>file')
            continue
        result_tokens.append(token)
        i += 1

    # Use shlex.quote for proper shell escaping of all tokens
    # This handles special characters like $, `, !, spaces, etc.
    return " ".join(shlex.quote(t) for t in result_tokens)


def is_gh_pr_command(command: str) -> bool:
    """Check if command is a gh pr command.

    Handles global flags that may appear before 'pr':
    - gh pr merge 123
    - gh --repo owner/repo pr merge 123
    - gh -R owner/repo pr merge 123

    Uses shlex.split() to avoid false positives from quoted strings.
    """
    subcommand, _ = parse_gh_pr_command(command)
    return subcommand is not None


def is_ci_monitor_command(command: str) -> tuple[bool, list[str]]:
    """Check if command is a ci-monitor.py command that operates on PRs.

    Detects:
    - python3 .claude/scripts/ci-monitor.py 602
    - ci-monitor.py 602 603 604 (multi-PR mode)
    - ./scripts/ci-monitor.py 602

    Returns:
        Tuple of (is_ci_monitor, pr_numbers) where pr_numbers is a list of all PR numbers.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    # Find ci-monitor.py in tokens
    for i, token in enumerate(tokens):
        if token.endswith("ci-monitor.py"):
            # Look for all PR numbers in subsequent tokens (skip flags)
            pr_numbers: list[str] = []
            for j in range(i + 1, len(tokens)):
                arg = tokens[j]
                if arg.startswith("-"):
                    continue
                # Collect all numeric arguments as PR numbers
                if arg.isdigit():
                    pr_numbers.append(arg)
            return True, pr_numbers

    return False, []


def is_worktree_remove_command(command: str) -> bool:
    """Check if command contains a git worktree remove command.

    Handles git global flags:
    - git worktree remove path
    - git -C /path worktree remove path
    - git --work-tree=/path worktree remove path
    - git --work-tree="/path with spaces" worktree remove path

    Also handles chained commands (Issue #612):
    - git worktree unlock path && git worktree remove path
    - cmd1 ; git worktree remove path
    - cmd1 || git worktree remove path

    Uses shlex.split() to properly handle quoted arguments.
    This fixes Issue #313: Edge case handling for quoted paths
    """
    # Normalize shell operators first (handles cases like 'foo&&git')
    normalized = normalize_shell_operators(command)
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        # If shlex fails (e.g., unbalanced quotes), fall back to simple split
        tokens = normalized.split()

    # Check ALL 'git' commands in the token list, not just the first one
    # This handles chained commands like: git unlock path && git worktree remove path
    for i, token in enumerate(tokens):
        if token == "git":
            if check_single_git_worktree_remove(tokens, i):
                return True

    return False


def extract_git_base_directory(command: str) -> str | None:
    """Extract base directory from git global flags if present.

    Handles:
    - git -C /path worktree remove
    - git -C "/path with spaces" worktree remove
    - git --git-dir=/path/.git worktree remove
    - git --git-dir="/path/.git" worktree remove
    - git --work-tree=/path worktree remove
    - git --work-tree="/path with spaces" worktree remove

    Returns the directory that should be used for git operations.

    This fixes Issue #313: Edge case handling for quoted paths
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        # If shlex fails, fall back to simple split
        tokens = command.split()

    # Find 'git' command position
    git_idx = None
    for i, token in enumerate(tokens):
        if token == "git":
            git_idx = i
            break

    if git_idx is None:
        return None

    # Look for flags in tokens after 'git'
    i = git_idx + 1
    while i < len(tokens):
        token = tokens[i]

        # Stop at worktree command
        if token == "worktree":
            break

        # -C flag
        if token == "-C":
            if i + 1 < len(tokens):
                return tokens[i + 1]
            break

        # --work-tree flag (two forms: --work-tree=/path or --work-tree /path)
        if token.startswith("--work-tree="):
            return token[len("--work-tree=") :]
        if token == "--work-tree":
            if i + 1 < len(tokens):
                return tokens[i + 1]
            break

        # --git-dir flag (extract parent directory)
        if token.startswith("--git-dir="):
            git_dir = token[len("--git-dir=") :]
            # Handle paths ending with .git (covers both /repo/.git and bare repos like project.git)
            if git_dir.endswith(".git"):
                # Return parent directory without resolve() for consistency
                return str(Path(git_dir).parent)
            return git_dir
        if token == "--git-dir":
            if i + 1 < len(tokens):
                git_dir = tokens[i + 1]
                if git_dir.endswith(".git"):
                    return str(Path(git_dir).parent)
                return git_dir
            break

        i += 1

    return None


def extract_worktree_path_from_git_command(
    tokens: list[str], git_idx: int
) -> tuple[str | None, str | None]:
    """Extract worktree path and base_dir from a single git worktree remove command.

    Args:
        tokens: List of command tokens.
        git_idx: Index of 'git' token.

    Returns:
        Tuple of (worktree_path, base_dir).
        Returns (None, None) if not a git worktree remove command.
    """
    if git_idx >= len(tokens) or tokens[git_idx] != "git":
        return None, None

    # Skip global flags to find 'worktree'
    flags_with_args = {"-C", "--git-dir", "--work-tree", "-c"}
    i = git_idx + 1
    while i < len(tokens):
        token = tokens[i]
        # Stop at command separators
        if token in ("&&", "||", ";", "|"):
            return None, None
        if token.startswith("-"):
            if "=" in token:
                i += 1
            elif token in flags_with_args:
                # Only increment by 2 if there is an argument after the flag
                if i + 1 < len(tokens) and tokens[i + 1] not in ("&&", "||", ";", "|"):
                    i += 2
                else:
                    i += 1
            else:
                i += 1
        else:
            break

    # Check for 'worktree remove'
    if i >= len(tokens) or tokens[i] != "worktree":
        return None, None
    if i + 1 >= len(tokens) or tokens[i + 1] != "remove":
        return None, None

    # Find the worktree path (first non-flag argument after 'remove')
    j = i + 2
    while j < len(tokens):
        token = tokens[j]
        # Stop at command separators
        if token in ("&&", "||", ";", "|"):
            break
        if token.startswith("-"):
            # Skip flags (--force, -f, etc.)
            j += 1
            continue
        # Found the path - extract base_dir from THIS git segment
        base_dir = extract_base_dir_from_git_segment(tokens, git_idx)
        return token, base_dir

    return None, None


def extract_unlock_path_from_git_command(
    tokens: list[str], git_idx: int
) -> tuple[str | None, str | None]:
    """Extract worktree path and base_dir from a single git worktree unlock command.

    Args:
        tokens: List of command tokens.
        git_idx: Index of 'git' token.

    Returns:
        Tuple of (worktree_path, base_dir).
        Returns (None, None) if not a git worktree unlock command.

    This fixes Issue #700: unlock && remove pattern detection.
    """
    if git_idx >= len(tokens) or tokens[git_idx] != "git":
        return None, None

    # Skip global flags to find 'worktree'
    flags_with_args = {"-C", "--git-dir", "--work-tree", "-c"}
    i = git_idx + 1
    while i < len(tokens):
        token = tokens[i]
        # Stop at command separators
        if token in ("&&", "||", ";", "|"):
            return None, None
        if token.startswith("-"):
            if "=" in token:
                i += 1
            elif token in flags_with_args:
                # Only increment by 2 if there is an argument after the flag
                if i + 1 < len(tokens) and tokens[i + 1] not in ("&&", "||", ";", "|"):
                    i += 2
                else:
                    i += 1
            else:
                i += 1
        else:
            break

    # Check for 'worktree unlock'
    if i >= len(tokens) or tokens[i] != "worktree":
        return None, None
    if i + 1 >= len(tokens) or tokens[i + 1] != "unlock":
        return None, None

    # Find the worktree path (first non-flag argument after 'unlock')
    j = i + 2
    while j < len(tokens):
        token = tokens[j]
        # Stop at command separators
        if token in ("&&", "||", ";", "|"):
            break
        if token.startswith("-"):
            # Skip flags
            j += 1
            continue
        # Found the path - extract base_dir from THIS git segment
        base_dir = extract_base_dir_from_git_segment(tokens, git_idx)
        return token, base_dir

    return None, None


def extract_unlock_targets_from_command(
    command: str, hook_cwd: str | None = None, before_position: int | None = None
) -> list[Path]:
    """Extract resolved worktree paths from 'git worktree unlock' commands.

    Args:
        command: The full command string (may contain chained commands).
        hook_cwd: Current working directory from hook input.
        before_position: If specified, only extract unlocks that appear before this
                         token position. This ensures unlock runs before remove.
                         (Fixes Codex review P2: order matters)

    Returns:
        List of resolved Path objects that will be unlocked by the command.

    This fixes Issue #700: Detect unlock in chained commands like
    'git worktree unlock path && git worktree remove path'.
    """
    normalized = normalize_shell_operators(command)
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        tokens = normalized.split()

    unlock_paths: list[Path] = []

    # Track cd target per command segment (Fixes Codex review P3: cd scoping)
    # Reset cd_target when crossing command separators
    cd_target: str | None = None

    i = 0
    while i < len(tokens):
        token = tokens[i]

        # Reset cd_target only for pipe (|) which runs in subshell
        # For &&, ||, and ; the cd effect carries over in the same shell
        # Codex review: cd /repo; git worktree unlock should work in /repo
        if token == "|":
            # Pipe runs in subshell, cd effect doesn't carry over
            cd_target = None
            i += 1
            continue

        # Track cd command - update cd_target for subsequent git commands
        if token == "cd":
            j = i + 1
            while j < len(tokens) and tokens[j] not in ("&&", "||", ";", "|"):
                t = tokens[j]
                if t.startswith("-") and t != "-":
                    j += 1
                    continue
                cd_target = t
                break
            i += 1
            continue

        # Check for git worktree unlock
        if token == "git":
            # P2 fix: Only consider unlocks before the remove position
            if before_position is not None and i >= before_position:
                i += 1
                continue

            # P1 fix: Only allow bypass when connected by && (remove runs when unlock succeeds)
            # Disallow bypass for:
            # - || : remove runs when unlock FAILS
            # - ; : remove runs regardless of unlock outcome
            # - | : remove runs in separate pipeline (shouldn't happen but be safe)
            if before_position is not None:
                has_unsafe_connector = False
                for j in range(i, before_position):
                    if j < len(tokens) and tokens[j] in ("||", ";", "|"):
                        has_unsafe_connector = True
                        break
                if has_unsafe_connector:
                    i += 1
                    continue

            path_str, base_dir = extract_unlock_path_from_git_command(tokens, i)
            if path_str is not None:
                # Resolve the path similar to check_worktree_remove
                worktree_path = Path(path_str)
                if not worktree_path.is_absolute():
                    try:
                        # Priority: -C flag > cd target > hook_cwd
                        # Codex review: relative -C should be resolved against cd_target
                        if base_dir:
                            base_dir_path = Path(base_dir)
                            if not base_dir_path.is_absolute():
                                # Resolve relative -C against cd_target if present
                                if cd_target:
                                    cd_path = Path(cd_target)
                                    if not cd_path.is_absolute() and hook_cwd:
                                        cd_path = Path(hook_cwd) / cd_path
                                    base_dir_path = cd_path / base_dir_path
                                elif hook_cwd:
                                    base_dir_path = Path(hook_cwd) / base_dir_path
                            worktree_path = base_dir_path / worktree_path
                        elif cd_target:
                            # Use cd target as base directory
                            cd_path = Path(cd_target)
                            if not cd_path.is_absolute() and hook_cwd:
                                cd_path = Path(hook_cwd) / cd_path
                            worktree_path = cd_path / worktree_path
                        elif hook_cwd:
                            worktree_path = Path(hook_cwd) / worktree_path
                        else:
                            worktree_path = Path.cwd() / worktree_path
                    except Exception:
                        i += 1
                        continue
                try:
                    worktree_path = worktree_path.resolve()
                except OSError:
                    # Use path as-is if resolve() fails (e.g., broken symlink)
                    pass
                unlock_paths.append(worktree_path)

        i += 1

    return unlock_paths


def find_git_worktree_remove_position(command: str) -> int | None:
    """Find the token position of 'git' in 'git worktree remove' command.

    Args:
        command: The full command string.

    Returns:
        Token index of 'git' for the remove command, or None if not found.

    This is used to determine unlock positions relative to remove.
    """
    normalized = normalize_shell_operators(command)
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        tokens = normalized.split()

    for i, token in enumerate(tokens):
        if token == "git":
            # Check if this is a worktree remove command
            path, _ = extract_worktree_path_from_git_command(tokens, i)
            if path is not None:
                return i

    return None


def extract_worktree_path_from_command(command: str) -> tuple[str | None, str | None]:
    """Extract worktree path and base directory from git worktree remove command.

    Handles:
    - git worktree remove path
    - git worktree remove --force path
    - git worktree remove -f path
    - git worktree remove path --force
    - git worktree remove "path with spaces"
    - git -C /repo worktree remove path
    - git -C "/repo with spaces" worktree remove path
    - cd /path && git worktree remove .relative (Issue #665)
    - cd "/path with spaces" && git worktree remove .relative

    Also handles chained commands (Issue #612):
    - git worktree unlock path && git worktree remove path
    - cmd1 ; git worktree remove path
    - git -C /repo1 worktree unlock foo && git -C /repo2 worktree remove bar

    Returns:
        Tuple of (worktree_path, base_directory)
        base_directory is the directory specified by:
        1. -C, --work-tree, or --git-dir flag (highest priority)
        2. cd command before git worktree remove (Issue #665)
        or None if neither is present

    This fixes Issue #313: Edge case handling for quoted paths
    This fixes Issue #612: base_dir extraction for chained commands
    This fixes Issue #665: cd command's effect is not recognized
    """
    # Normalize shell operators first (handles cases like 'foo&&git')
    normalized = normalize_shell_operators(command)
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        # If shlex fails, fall back to simple split
        tokens = normalized.split()

    # Check ALL 'git' commands in the token list, not just the first one
    # This handles chained commands like: git unlock path && git worktree remove path
    for i, token in enumerate(tokens):
        if token == "git":
            path, base_dir = extract_worktree_path_from_git_command(tokens, i)
            if path is not None:
                # If no -C flag, check for cd command before git (Issue #665)
                if base_dir is None:
                    cd_target = extract_cd_target_before_git(command)
                    if cd_target:
                        base_dir = cd_target
                return path, base_dir

    return None, None
