#!/usr/bin/env python3
"""1つのworktree/PRで複数Issueを同時に対応しようとした場合に警告する。

Why:
    1 worktree = 1 Issue、1 PR = 1 Issueの原則を維持することで、
    変更の追跡性とレビューの容易さを確保する。

What:
    - git worktree addのブランチ名/パスから複数Issue番号を検出
    - gh pr createのbodyから複数Closes/Fixes/Resolvesを検出
    - 複数Issueを検出したらsystemMessageで警告

Remarks:
    - 警告型フック（ブロックしない、正当なケースがあるため）
    - PreToolUse:Bashで発火（git worktree add、gh pr create）
    - issue-auto-assignは単一Issue検出（責務分離）
    - 関連Issue同時修正や親子関係など正当なケースも存在

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import os
import re

from lib.execution import log_hook_execution
from lib.results import make_approve_result
from lib.session import parse_hook_input
from lib.strings import extract_inline_skip_env, is_skip_env_enabled

SKIP_ENV = "SKIP_MULTI_ISSUE_GUARD"
HOOK_NAME = "multi-issue-guard"


def extract_all_issue_numbers(text: str) -> list[int]:
    """Extract all unique Issue numbers from text.

    Uses the same patterns as issue-auto-assign.py but collects all matches.

    Args:
        text: Text to search for Issue numbers.

    Returns:
        List of unique Issue numbers found (sorted for deterministic output).
    """
    patterns = [
        r"#(\d+)",  # #123
        r"issue[_-](\d+)",  # issue-123, issue_123
        r"/(\d+)[-_]",  # /123-description
        r"[-_](\d+)[-_]",  # feature-123-name
        r"[-_](\d+)$",  # feature-123 (at end)
    ]

    issue_numbers: set[int] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            issue_numbers.add(int(match.group(1)))

    return sorted(issue_numbers)


def extract_closing_issue_numbers(body: str) -> list[int]:
    """Extract Issue numbers from closing keywords in PR body.

    Args:
        body: PR body text.

    Returns:
        List of unique Issue numbers from closing keywords.
    """
    pattern = r"(?:closes|fixes|resolves)\s*#?(\d+)"
    matches = re.findall(pattern, body, re.IGNORECASE)
    return sorted(set(int(m) for m in matches))


def parse_worktree_add_command(command: str) -> tuple[str | None, str | None]:
    """Parse git worktree add command and extract branch name and path.

    Copied from issue-auto-assign.py for consistency.

    Returns:
        Tuple of (branch_name, worktree_path).
    """
    if "git worktree add" not in command:
        return None, None

    branch_name = None
    worktree_path = None

    # Look for -b <branch> pattern
    branch_match = re.search(r"-b\s+([^\s]+)", command)
    if branch_match:
        branch_name = branch_match.group(1)

    # Parse command parts to find positional arguments
    parts = command.split()

    try:
        add_idx = parts.index("add")
    except ValueError:
        return branch_name, worktree_path

    # Collect positional arguments (non-option arguments after 'add')
    positional_args = []
    skip_next = False
    for part in parts[add_idx + 1 :]:
        if skip_next:
            skip_next = False
            continue
        if part.startswith("-"):
            if part in ("-b", "--reason"):
                skip_next = True
            continue
        positional_args.append(part)

    if len(positional_args) >= 1:
        worktree_path = positional_args[0]

    if len(positional_args) >= 2 and not branch_name:
        branch_name = positional_args[1]

    return branch_name, worktree_path


def extract_pr_body(command: str) -> str | None:
    """Extract --body argument from gh pr create command.

    Args:
        command: The full command string.

    Returns:
        The body content if found, None otherwise.
    """
    # Match --body "..." or --body '...'
    # Handle escaped quotes within
    patterns = [
        r'--body\s+"((?:[^"\\]|\\.)*)"',  # --body "..."
        r"--body\s+'((?:[^'\\]|\\.)*)'",  # --body '...'
    ]

    for pattern in patterns:
        match = re.search(pattern, command, re.DOTALL)
        if match:
            return match.group(1)

    return None


def check_worktree_command(command: str) -> dict:
    """Check git worktree add command for multiple Issues.

    Args:
        command: The command string.

    Returns:
        Dict with 'warn' (bool) and 'message' (str) keys.
    """
    if "git worktree add" not in command:
        return {"warn": False, "message": ""}

    branch_name, worktree_path = parse_worktree_add_command(command)

    # Collect Issue numbers from both branch and path
    all_issues: set[int] = set()

    if branch_name:
        all_issues.update(extract_all_issue_numbers(branch_name))

    if worktree_path:
        all_issues.update(extract_all_issue_numbers(worktree_path))

    if len(all_issues) > 1:
        issue_list = ", ".join(f"#{i}" for i in sorted(all_issues))
        message = (
            f"⚠️ 複数Issueの同時対応を検出: {issue_list}\n\n"
            f"1 worktree = 1 Issue を推奨します。\n"
            f"本当に複数Issueを同時に対応しますか？\n\n"
            f"意図的な場合は続行してください。"
        )
        return {"warn": True, "message": message, "issues": sorted(all_issues)}

    return {"warn": False, "message": ""}


def check_pr_command(command: str) -> dict:
    """Check gh pr create command for multiple closing keywords.

    Args:
        command: The command string.

    Returns:
        Dict with 'warn' (bool) and 'message' (str) keys.
    """
    if "gh pr create" not in command:
        return {"warn": False, "message": ""}

    body = extract_pr_body(command)
    if not body:
        return {"warn": False, "message": ""}

    issue_numbers = extract_closing_issue_numbers(body)

    if len(issue_numbers) > 1:
        issue_list = ", ".join(f"#{i}" for i in issue_numbers)
        message = (
            f"⚠️ 複数Issueを同時にクローズしようとしています: {issue_list}\n\n"
            f"1 PR = 1 Issue を推奨します。\n"
            f"複数Issueを同時にクローズするのは意図的ですか？\n\n"
            f"意図的な場合は続行してください。"
        )
        return {"warn": True, "message": message, "issues": issue_numbers}

    return {"warn": False, "message": ""}


def main() -> None:
    """Entry point for the hook."""
    try:
        input_data = parse_hook_input()
    except (json.JSONDecodeError, Exception):
        # Fail open: allow on parse errors
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name != "Bash":
        return  # Not a Bash command

    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")

    if not command:
        return  # Empty command

    # Check SKIP environment variable (exported)
    if is_skip_env_enabled(os.environ.get(SKIP_ENV)):
        log_hook_execution(HOOK_NAME, "skip", f"{SKIP_ENV}=1: チェックをスキップ")
        result = make_approve_result(HOOK_NAME)
        print(json.dumps(result))
        return

    # Check inline SKIP environment variable
    inline_value = extract_inline_skip_env(command, SKIP_ENV)
    if is_skip_env_enabled(inline_value):
        log_hook_execution(HOOK_NAME, "skip", f"{SKIP_ENV}=1: チェックをスキップ（インライン）")
        result = make_approve_result(HOOK_NAME)
        print(json.dumps(result))
        return

    # Check worktree add command
    worktree_result = check_worktree_command(command)
    if worktree_result["warn"]:
        log_hook_execution(
            HOOK_NAME,
            "warn",
            f"複数Issue検出（worktree）: {worktree_result.get('issues', [])}",
        )
        result = {"decision": "approve", "systemMessage": worktree_result["message"]}
        print(json.dumps(result))
        return

    # Check PR create command
    pr_result = check_pr_command(command)
    if pr_result["warn"]:
        log_hook_execution(
            HOOK_NAME,
            "warn",
            f"複数Issue検出（PR）: {pr_result.get('issues', [])}",
        )
        result = {"decision": "approve", "systemMessage": pr_result["message"]}
        print(json.dumps(result))
        return

    # No warning needed - output nothing (per hooks-reference pattern)


if __name__ == "__main__":
    main()
