#!/usr/bin/env python3
"""シェルコマンドパース用の共通ユーティリティを提供する。

Why:
    command_parser.pyとgithub.py間の循環インポートを解消し、
    共通のコマンド処理ロジックを一箇所に集約する。

What:
    - get_command_name(): パス付きコマンドからコマンド名を抽出
    - normalize_shell_separators(): シェル区切り文字を正規化
    - is_command_wrapper(): sudo/time等のラッパーコマンド判定
    - COMMAND_WRAPPERS: 既知のコマンドラッパー一覧

Remarks:
    - shlex.splitの制限（区切り文字が前トークンに付く）を補完
    - クォート内・エスケープされた区切り文字は保持
    - 絶対パスコマンド（/usr/bin/git等）に対応

Changelog:
    - silenvx/dekita#1337: command_parser/githubから分離、循環インポート解消
"""

from __future__ import annotations

import os


def get_command_name(token: str) -> str:
    """Extract the command name from a token (handles absolute paths).

    Issue #1258: Commands may be called with absolute paths like
    /usr/bin/git or /opt/homebrew/bin/gh. This function extracts
    just the command name (basename) for matching.

    Args:
        token: A command token (e.g., "git", "/usr/bin/git")

    Returns:
        The command name without path (e.g., "git")
    """
    return os.path.basename(token)


def ends_with_shell_separator(token: str) -> bool:
    """Check if a token ends with a shell separator.

    When shlex.split processes 'echo foo;git status', it produces
    ['echo', 'foo;', 'git', 'status'] - the separator is attached to
    the previous token. This function checks for that case.

    Args:
        token: A command token to check

    Returns:
        True if the token ends with a shell separator (;, |, &)
    """
    # Check for common separators
    # Note: && and || become single tokens, but ; and | can be attached
    return token.endswith(";") or token.endswith("|") or token.endswith("&")


# Common command wrappers that precede the actual command
COMMAND_WRAPPERS = frozenset(
    {
        "sudo",
        "time",
        "command",
        "nice",
        "nohup",
        "strace",
        "ltrace",
        "exec",
        "env",  # 'env' without VAR= is a wrapper
        "doas",
        "pkexec",
        "timeout",
        "watch",
        "caffeinate",  # macOS
    }
)


def is_command_wrapper(token: str) -> bool:
    """Check if a token is a common command wrapper.

    Commands like sudo, time, nice, etc. precede the actual command
    and should be treated as valid command positions.

    Args:
        token: A command token to check

    Returns:
        True if the token is a known command wrapper
    """
    return token in COMMAND_WRAPPERS


def normalize_shell_separators(command: str) -> str:
    """Add spaces around shell separators for proper tokenization.

    shlex.split doesn't treat shell metacharacters as separators,
    so 'echo foo;gh pr list' becomes ['echo', 'foo;gh', 'pr', 'list'].
    This function adds spaces to make it ['echo', 'foo', ';', 'gh', ...].

    Preserves separators inside quotes and escaped separators (e.g., \\;).

    Args:
        command: The raw command string

    Returns:
        Command string with spaces around unquoted shell separators
    """
    result = []
    in_single_quote = False
    in_double_quote = False
    i = 0

    while i < len(command):
        char = command[i]

        # Check if previous character was an escape (backslash)
        # Count consecutive backslashes at end of result to handle \\; correctly
        # - Odd count: next char is escaped (e.g., \; -> literal semicolon)
        # - Even count: next char is NOT escaped (e.g., \\; -> literal \ + separator)
        # Note: In single quotes, backslash is literal, so only check outside
        consecutive_backslashes = 0
        if not in_single_quote:
            for j in range(len(result) - 1, -1, -1):
                if result[j] == "\\":
                    consecutive_backslashes += 1
                else:
                    break
        prev_is_escape = consecutive_backslashes % 2 == 1

        # Track quote state
        if char == "'" and not in_double_quote and not prev_is_escape:
            in_single_quote = not in_single_quote
            result.append(char)
            i += 1
        elif char == '"' and not in_single_quote and not prev_is_escape:
            in_double_quote = not in_double_quote
            result.append(char)
            i += 1
        # Handle multi-char operators (&&, ||)
        elif not in_single_quote and not in_double_quote:
            if command[i : i + 2] in ("&&", "||") and not prev_is_escape:
                result.append(" ")
                result.append(command[i : i + 2])
                result.append(" ")
                i += 2
            elif char in (";", "|") and not prev_is_escape:
                result.append(" ")
                result.append(char)
                result.append(" ")
                i += 1
            else:
                result.append(char)
                i += 1
        else:
            result.append(char)
            i += 1

    return "".join(result)
