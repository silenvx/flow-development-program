#!/usr/bin/env python3
"""gh issue viewコマンド実行時にコメントを自動表示する。

Why:
    Issueコメントに重要な解決策や追加情報があっても見落とされ、
    無駄な時間を費やすことがある。コメントを自動表示して
    情報の見落としを防ぐ。

What:
    - gh issue view <number> コマンドを検出
    - --commentsフラグがない場合、自動でコメントを取得
    - systemMessageでコメント内容を表示

Remarks:
    - 非ブロック型（情報提供のみ）
    - --comments付きのコマンドはそのまま通過

Changelog:
    - silenvx/dekita#538: フック追加（コメント見落とし防止）
"""

import json
import re
import subprocess

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import print_continue_and_log_skip
from lib.session import create_hook_context, parse_hook_input
from lib.strings import strip_quoted_strings


def extract_issue_number(command: str) -> str | None:
    """Extract issue number from gh issue view command.

    Args:
        command: The bash command string.

    Returns:
        Issue number as string, or None if not found.

    Handles various flag positions:
    - gh issue view 123
    - gh issue view #123
    - gh issue view --web 123
    - gh issue view 123 --web
    """
    # Remove quoted strings to avoid false positives
    cmd = strip_quoted_strings(command)

    # Check if this is a gh issue view command
    if not re.search(r"gh\s+issue\s+view\b", cmd):
        return None

    # Extract all arguments after "gh issue view"
    match = re.search(r"gh\s+issue\s+view\s+(.+)", cmd)
    if not match:
        return None

    args = match.group(1)

    # Find issue number (with or without #) among the arguments
    # Skip flags (--flag or -f) and their values
    for part in args.split():
        # Skip flags and flag values
        if part.startswith("-"):
            continue
        # Match issue number (with optional # prefix)
        num_match = re.match(r"#?(\d+)$", part)
        if num_match:
            return num_match.group(1)

    return None


def has_comments_flag(command: str) -> bool:
    """Check if command already has --comments flag.

    Args:
        command: The bash command string.

    Returns:
        True if --comments flag is present as a standalone flag.
    """
    # Remove quoted strings to avoid matching flags inside quotes
    cmd = strip_quoted_strings(command)
    # Match --comments as a standalone flag (bounded by start/end or whitespace)
    return re.search(r"(?:^|\s)--comments(?:\s|$)", cmd) is not None


def fetch_issue_comments(issue_number: str) -> tuple[bool, str]:
    """Fetch issue comments using gh CLI.

    Args:
        issue_number: The issue number.

    Returns:
        Tuple of (success, comments):
        - (True, comments) if successful with comments
        - (True, "") if successful but no comments
        - (False, "") if error occurred
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                issue_number,
                "--json",
                "comments",
                "--jq",
                '.comments[] | "---\\n**" + .author.login + "** (" + .createdAt[:10] + "):\\n" + .body + "\\n"',
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if result.returncode != 0:
            return (False, "")

        return (True, result.stdout.strip())

    except (subprocess.TimeoutExpired, OSError):
        return (False, "")


def main():
    """
    PreToolUse hook for Bash commands.

    Automatically fetches and displays issue comments when `gh issue view`
    is called without --comments flag.
    """
    result = {"decision": "approve"}

    try:
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(input_data)
        tool_name = input_data.get("tool_name", "")

        if tool_name != "Bash":
            print_continue_and_log_skip("issue-comments-check", f"not Bash: {tool_name}", ctx=ctx)
            return

        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Check if this is a gh issue view command
        issue_number = extract_issue_number(command)
        if not issue_number:
            print_continue_and_log_skip("issue-comments-check", "no issue number found", ctx=ctx)
            return

        # If --comments is already present, let it through
        if has_comments_flag(command):
            log_hook_execution(
                "issue-comments-check",
                "approve",
                f"--comments付き: Issue #{issue_number}",
            )
            print(json.dumps(result))
            return

        # Fetch comments and display via systemMessage
        success, comments = fetch_issue_comments(issue_number)

        if not success:
            # Don't show misleading message on error
            log_hook_execution(
                "issue-comments-check",
                "approve",
                f"コメント取得エラー: Issue #{issue_number}",
            )
        elif comments:
            result["systemMessage"] = (
                f"📝 **Issue #{issue_number} のコメント** (自動取得)\n\n"
                f"{comments}\n\n"
                f"💡 Issueに取り組む前に、必ずコメントを確認してください。"
            )
            log_hook_execution(
                "issue-comments-check",
                "approve",
                f"コメント自動表示: Issue #{issue_number}",
            )
        else:
            result["systemMessage"] = f"ℹ️ Issue #{issue_number} にはコメントがありません。"
            log_hook_execution(
                "issue-comments-check",
                "approve",
                f"コメントなし: Issue #{issue_number}",
            )

    except Exception as e:
        # Don't block on errors
        log_hook_execution(
            "issue-comments-check",
            "error",
            f"フックエラー: {e}",
        )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
