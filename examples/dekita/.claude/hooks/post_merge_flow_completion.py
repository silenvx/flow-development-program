#!/usr/bin/env python3
# - 責務: PRマージ後のフローステップ（issue_updated）を自動完了
# - 重複なし: pr-merge-pull-reminderはpull、本フックはフローステップ完了
# - 自動化型: マージ成功後にIssueコメントを自動追加
"""
PostToolUse hook to auto-complete flow steps after PR merge.

When `gh pr merge` succeeds, this hook:
1. Extracts the Issue number from the PR
2. Adds a completion comment to the Issue (triggers issue_updated step)

This ensures flow steps are completed without manual intervention.
"""

import json
import re
import subprocess

from lib.constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.github import extract_pr_number as common_extract_pr_number
from lib.hook_input import get_exit_code, get_tool_result
from lib.repo import is_merge_success
from lib.session import parse_hook_input


def is_pr_merge_command(tool_input: str) -> bool:
    """Check if the command is a PR merge command."""
    return "gh pr merge" in tool_input


def _check_merge_success(tool_output: str, exit_code: int, command: str) -> bool:
    """Check if the merge was successful.

    Wrapper around common.is_merge_success for backward compatibility.
    """
    return is_merge_success(exit_code, tool_output, command)


def extract_pr_number(command: str) -> int | None:
    """Extract PR number from merge command or current branch PR.

    Uses common.extract_pr_number for command parsing, with fallback
    to querying the current branch's PR if no number in command.
    """
    # Use common.py's parser for extracting from command
    pr_str = common_extract_pr_number(command)
    if pr_str:
        return int(pr_str)

    # If no PR number in command, get PR for current branch
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "number"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("number")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        # gh CLI not available, network error, or invalid JSON
        # Fall through to return None
        pass

    return None


def get_issue_from_pr(pr_number: int) -> int | None:
    """Get the linked Issue number from a PR.

    Searches for issue references in PR body, title, and branch name.
    Uses a single API call for efficiency.
    """
    try:
        # Get all needed fields in one API call
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "body,title,headRefName"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        body = data.get("body", "") or ""
        title = data.get("title", "") or ""
        branch = data.get("headRefName", "") or ""

        # Search for issue references in body and title
        for text in [body, title]:
            # Match "Closes #123", "Fixes #123", "Resolves #123"
            match = re.search(r"(?:closes?|fixes?|resolves?)\s+#(\d+)", text, re.IGNORECASE)
            if match:
                return int(match.group(1))

        # Also check branch name for issue number
        # Match "issue-123", "feat/issue-123-xxx"
        match = re.search(r"issue-(\d+)", branch, re.IGNORECASE)
        if match:
            return int(match.group(1))

        return None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        # gh CLI not available, network error, or invalid JSON
        return None


def add_completion_comment(issue_number: int, pr_number: int) -> bool:
    """Add a completion comment to the Issue."""
    try:
        comment = f"PR #{pr_number} マージ完了。フローステップ自動完了。"
        result = subprocess.run(
            ["gh", "issue", "comment", str(issue_number), "--body", comment],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def main() -> None:
    """Main hook logic."""
    input_data = parse_hook_input()
    if not input_data:
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name != "Bash":
        return

    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")

    if not is_pr_merge_command(command):
        return

    tool_output = input_data.get("tool_output", "")
    # Issue #2203: Use get_exit_code() for consistent default value
    # Use get_tool_result() to handle both tool_result and tool_response
    tool_result = get_tool_result(input_data) or {}
    exit_code = get_exit_code(tool_result)

    if not _check_merge_success(tool_output, exit_code, command):
        return

    pr_number = extract_pr_number(command)
    if not pr_number:
        log_hook_execution(
            "post-merge-flow-completion",
            "approve",
            "skipped: could not extract PR number",
        )
        return

    issue_number = get_issue_from_pr(pr_number)
    if not issue_number:
        log_hook_execution(
            "post-merge-flow-completion",
            "approve",
            f"skipped: no issue linked to PR #{pr_number}",
        )
        return

    success = add_completion_comment(issue_number, pr_number)
    if success:
        print(
            f"[post-merge-flow-completion] Issue #{issue_number} にフロー完了コメントを追加しました"
        )
        log_hook_execution(
            "post-merge-flow-completion",
            "approve",
            f"comment added to issue #{issue_number} for PR #{pr_number}",
        )
    else:
        print(f"[post-merge-flow-completion] Issue #{issue_number} へのコメント追加に失敗しました")
        log_hook_execution(
            "post-merge-flow-completion",
            "approve",
            f"failed to add comment to issue #{issue_number}",
        )


if __name__ == "__main__":
    main()
