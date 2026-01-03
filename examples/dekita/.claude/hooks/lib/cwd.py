#!/usr/bin/env python3
"""カレントワーキングディレクトリの検出・検証を行う。

Why:
    cdコマンド実行後のworktree削除を正しくブロックするため、
    複数ソースからの効果的なcwd検出が必要。

What:
    - get_effective_cwd(): 環境変数・コマンド内cdを考慮したcwd取得
    - check_cwd_inside_path(): cwdが指定パス内にあるか判定
    - extract_cd_target_from_command(): コマンド内cdターゲット抽出

Remarks:
    - 優先順: コマンド内cd > CLAUDE_WORKING_DIRECTORY > PWD > cwd()
    - shlex.splitでクォート・エスケープを正しく処理
    - OSエラー時はfail-close（削除をブロック）

Changelog:
    - silenvx/dekita#671: cd考慮によるworktree削除ブロック
    - silenvx/dekita#679: 非先頭cd（export && cd && ...）対応
    - silenvx/dekita#680: エスケープ引用符パス対応
    - silenvx/dekita#1035: base_cwdパラメータ追加（相対パス解決）
"""

import os
import re
import shlex
from pathlib import Path


def extract_cd_target_from_command(command: str) -> str | None:
    """Extract the target directory from a 'cd <path> &&' pattern in command.

    When a command starts with 'cd /some/path && git worktree remove ...',
    the cd will execute first, so the effective cwd for the git command
    will be the cd target, not the current environment cwd.

    Issue #671: 'cd /main/repo && git worktree remove' パターンを検出し、
    cdの効果を考慮する。
    Issue #680: エスケープ引用符を含むパスも正しくパースする。

    Args:
        command: The full command string

    Returns:
        The cd target path if found, None otherwise.
    """
    # Normalize shell operators by adding spaces around them
    # shlex doesn't recognize && ; | || as operators
    # Note: This may modify operators inside quoted strings, but since this
    # is only used for command detection (not execution), this trade-off
    # is acceptable (same approach as locked-worktree-guard.py).
    normalized = command
    for op in ("&&", "||", ";"):
        normalized = normalized.replace(op, f" {op} ")

    # Use shlex to properly handle escaped quotes
    # e.g., cd "/path/with\"escaped/quotes" && git worktree remove
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        # Unbalanced quotes or other parse error - fall back to regex
        # Note: Fallback only detects leading cd (^cd), not non-leading cd.
        # This is acceptable as shlex failures are rare edge cases.
        match = re.match(r'^cd\s+(["\']?)([^"\'&;]+)\1\s*(?:&&|;)', command)
        if match:
            return match.group(2).strip()
        return None

    # Find 'cd' token and extract the path that follows
    # Handle patterns like:
    # - cd /path && ...
    # - cd /path ; ...
    # - export VAR=val && cd /path && ...  (Issue #679: non-leading cd)
    # Must be followed by && or ; to be considered a cd that changes directory
    # cd must be at command start or after && or ; (not | or ||)
    # - Pipe (|): cd runs in subshell, doesn't affect subsequent commands
    # - Or (||): cd only runs if previous command fails, not guaranteed
    cd_target = None
    # Operators that start a new command where cd affects subsequent commands
    valid_predecessors = ("&&", ";")
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "cd" and i + 1 < len(tokens):
            # cd must be at start or after && or ; (not | or ||)
            is_valid_start = i == 0 or tokens[i - 1] in valid_predecessors
            if is_valid_start:
                next_token = tokens[i + 1]
                # Skip shell operators - cd must have a path argument
                if next_token not in ("&&", ";", "|", "||"):
                    # Check if there's a separator after the path
                    if i + 2 < len(tokens) and tokens[i + 2] in ("&&", ";"):
                        cd_target = next_token
        i += 1

    return cd_target


def _get_env_cwd() -> Path:
    """Get cwd from environment variables or process cwd.

    Internal helper for get_effective_cwd().
    Priority: CLAUDE_WORKING_DIRECTORY > PWD > Path.cwd()
    """
    # Try CLAUDE_WORKING_DIRECTORY first (set by Claude Code)
    claude_wd = os.environ.get("CLAUDE_WORKING_DIRECTORY")
    if claude_wd:
        claude_path = Path(claude_wd)
        if claude_path.exists():
            return claude_path.resolve()

    # Try PWD (shell's tracked working directory after cd)
    pwd = os.environ.get("PWD")
    if pwd:
        pwd_path = Path(pwd)
        if pwd_path.exists():
            return pwd_path.resolve()

    # Fallback to process cwd
    return Path.cwd().resolve()


def get_effective_cwd(command: str | None = None, base_cwd: str | Path | None = None) -> Path:
    """Get effective current working directory.

    Considers multiple sources in priority order:
    1. 'cd <path> &&' prefix in command (if command provided)
    2. CLAUDE_WORKING_DIRECTORY (set by Claude Code after cd commands)
    3. PWD (shell's tracked working directory)
    4. Path.cwd() (process working directory, fallback)

    Issue #671: cdコマンドで移動した後のworktree削除をブロックするため、
    環境変数を優先して使用する。また、コマンド内のcdも考慮する。

    Issue #1035: 相対パスcdの解決時、base_cwdが指定されていればそれを基準にする。
    フックプロセスはメインリポジトリから実行されるため、hook_cwdを渡すことで
    正しい相対パス解決が可能になる。

    Args:
        command: Optional command string to check for 'cd <path> &&' pattern
        base_cwd: Optional base directory for resolving relative cd paths.
                  If not provided, falls back to _get_env_cwd().

    Returns:
        Resolved Path of effective working directory.
    """
    # Check for 'cd <path> &&' pattern in command first
    if command:
        cd_target = extract_cd_target_from_command(command)
        if cd_target:
            cd_path = Path(cd_target).expanduser()  # Expand ~ to home directory
            if not cd_path.is_absolute():
                # Issue #1035: Use base_cwd if provided for relative path resolution
                if base_cwd:
                    resolved_base = Path(base_cwd).resolve()
                else:
                    resolved_base = _get_env_cwd()
                cd_path = resolved_base / cd_path
            cd_path = cd_path.resolve()
            if cd_path.exists():
                return cd_path

    # Issue #1035: If base_cwd provided and no cd pattern, use base_cwd
    # Check existence for consistency with cd pattern handling above
    if base_cwd:
        base_path = Path(base_cwd).resolve()
        if base_path.exists():
            return base_path

    return _get_env_cwd()


def check_cwd_inside_path(target_path: Path, command: str | None = None) -> bool:
    """Check if effective current working directory is inside the target path.

    This is critical for worktree operations because deleting a worktree
    while cwd is inside it will cause all subsequent Bash commands to fail.

    Uses get_effective_cwd() to properly detect cd command effects.
    Issue #671, #682: cdコマンドで移動した後もworktree削除をブロック

    Args:
        target_path: The target path to check against (e.g., worktree path).
        command: Optional command string to check for 'cd <path> &&' pattern

    Returns:
        True if cwd is inside the target path (should block deletion).
    """
    try:
        cwd = get_effective_cwd(command)
        target_resolved = target_path.resolve()

        # Check if cwd is the target or a subdirectory
        return cwd == target_resolved or target_resolved in cwd.parents

    except OSError:
        # If we can't determine cwd, fail-close: block deletion
        return True
