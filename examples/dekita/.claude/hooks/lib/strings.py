#!/usr/bin/env python3
"""純粋な文字列操作ユーティリティを提供する。

Why:
    コマンドパース、ブランチ名サニタイズ、環境変数抽出など、
    外部依存なしの文字列操作を一箇所に集約する。

What:
    - strip_quoted_strings(): クォート内文字列を除去
    - split_command_chain(): コマンドチェーンを分割（&&, ||, ;）
    - sanitize_branch_name(): ブランチ名をファイル名用にサニタイズ
    - is_skip_env_enabled(): SKIP_*環境変数の有効判定
    - extract_inline_skip_env(): インラインSKIP_*変数を抽出

Remarks:
    - エスケープクォートは未対応（Claude Code用途では許容）
    - 外部依存なし（reモジュールのみ）
    - 各関数はステートレスで副作用なし

Changelog:
    - silenvx/dekita#813: sanitize_branch_name仕様策定
    - silenvx/dekita#956: SKIP_*環境変数のクォート処理追加
"""

import re


def strip_quoted_strings(cmd: str) -> str:
    """Remove quoted strings from command to avoid false positives.

    This prevents detecting commands inside echo/printf strings like:
    - echo 'gh pr create'
    - printf "gh pr create"

    Note: This does not handle escaped quotes (e.g., echo 'it\\'s quoted').
    For Claude Code's typical usage patterns, this is acceptable.
    """
    # Remove double-quoted strings
    result = re.sub(r'"[^"]*"', "", cmd)
    # Remove single-quoted strings
    result = re.sub(r"'[^']*'", "", result)
    return result


def split_command_chain(command: str) -> list[str]:
    """Split a command chain into individual commands.

    Splits on shell operators: &&, ||, ;

    Note: This function expects the input to have quoted strings already stripped
    (via strip_quoted_strings). This prevents splitting on operators inside quotes.

    Example:
        >>> stripped = strip_quoted_strings("git worktree remove --force && git push")
        >>> split_command_chain(stripped)
        ['git worktree remove --force', 'git push']

    Args:
        command: Command string (should have quoted strings stripped first)

    Returns:
        List of individual commands
    """
    parts = re.split(r"\s*(?:&&|\|\||;)\s*", command)
    return [p.strip() for p in parts if p.strip()]


def sanitize_branch_name(branch: str) -> str:
    """Sanitize branch name for use in filename.

    This function is used for marker FILENAMES only. The marker file CONTENT
    should use the original (non-sanitized) branch name to preserve accuracy.
    See MARKERS_LOG_DIR comment for the full specification (Issue #813).

    Example:
        branch = "feat/issue-123"
        filename = f"codex-review-{sanitize_branch_name(branch)}.done"
        # filename = "codex-review-feat-issue-123.done"
        content = f"{branch}:{commit_hash}"
        # content = "feat/issue-123:abc1234"

    Replaces characters that are problematic in filenames:
    - / (slash) -> - (commonly used in feature/xxx branches)
    - \\ (backslash) -> - (problematic on all systems)
    - : (colon) -> - (problematic on Windows)
    - * (asterisk) -> - (wildcard character)
    - ? (question mark) -> - (wildcard character)
    - " (double quote) -> - (problematic in paths)
    - < > (angle brackets) -> - (redirection characters)
    - | (pipe) -> - (shell pipe character)
    - space -> _ (to avoid path issues)

    Additional post-processing:
    - Consecutive dashes are collapsed to single dash
    - Leading/trailing dashes are removed

    Args:
        branch: The git branch name to sanitize.

    Returns:
        A sanitized string safe for use in filenames.
    """
    # Replace problematic characters with - or _
    result = branch
    # Replace slash and backslash first (most common)
    result = result.replace("/", "-")
    result = result.replace("\\", "-")
    # Replace other problematic characters
    result = re.sub(r'[:<>"|?*]', "-", result)
    # Replace spaces with underscore
    result = result.replace(" ", "_")
    # Remove consecutive dashes
    result = re.sub(r"-+", "-", result)
    # Remove leading/trailing dashes
    result = result.strip("-")

    return result


def is_skip_env_enabled(value: str | None) -> bool:
    """Check if a SKIP_* environment variable value indicates enabled.

    Only explicit truthy values ("1", "true", "True") are considered enabled.
    This prevents accidental skips from empty strings, "0", "false", etc.

    Issue #956: Consistent validation for SKIP_* environment variables.

    Args:
        value: The environment variable value (may be None if not set).

    Returns:
        True only if value is "1", "true", or "True".
        False for all other values including None, "", "0", "false", "False".

    Examples:
        >>> is_skip_env_enabled("1")
        True
        >>> is_skip_env_enabled("true")
        True
        >>> is_skip_env_enabled("0")
        False
        >>> is_skip_env_enabled(None)
        False
        >>> is_skip_env_enabled("")
        False
    """
    return value in ("1", "true", "True")


def extract_inline_skip_env(command: str, env_name: str) -> str | None:
    """Extract inline environment variable value from command, handling quotes.

    This function:
    1. First checks if the env var exists outside quoted strings (to avoid
       false positives from commands like: echo 'SKIP_PLAN=1')
    2. Then extracts the value from the original command, handling quoted values
       like SKIP_PLAN="1" or SKIP_PLAN='true'

    Issue #956: Handle quoted inline SKIP_* values correctly.

    Args:
        command: The command string to search in.
        env_name: The environment variable name (e.g., "SKIP_PLAN").

    Returns:
        The unquoted value if found outside quoted strings, None otherwise.

    Examples:
        >>> extract_inline_skip_env("SKIP_PLAN=1 git worktree add", "SKIP_PLAN")
        '1'
        >>> extract_inline_skip_env('SKIP_PLAN="true" git worktree', "SKIP_PLAN")
        'true'
        >>> extract_inline_skip_env("echo 'SKIP_PLAN=1'", "SKIP_PLAN")  # Inside quotes
    """
    # First check if the pattern exists outside quoted strings
    stripped = strip_quoted_strings(command)
    if not re.search(rf"\b{env_name}=", stripped):
        return None  # Pattern is inside quotes, ignore it

    # Extract the value from the original command (may be quoted)
    match = re.search(rf"\b{env_name}=(\S+)", command)
    if not match:
        return None

    value = match.group(1)

    # Remove surrounding quotes if present (both single and double)
    if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
        value = value[1:-1]

    return value
