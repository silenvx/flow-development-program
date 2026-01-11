#!/usr/bin/env python3
"""存在しないIssue参照をブロックする。

Why:
    存在しないIssue番号を参照すると、後から追跡できなくなり
    誤った情報がコメントに残る。参照前に存在確認を強制する。

What:
    - gh pr comment/gh issue comment等のコメント投稿を検出
    - コメント本文から#1234形式のIssue参照を抽出
    - gh issue viewで存在確認、不存在ならブロック

Remarks:
    - ブロック型フック（存在しないIssue参照時はブロック）
    - PreToolUse:Bashで発火（gh pr/issue comment、gh api replies）
    - Closes/Fixes/Resolvesパターンは除外（Issue作成用途）
    - cross-repoコマンド（--repo）はスキップ（他リポジトリ検証困難）

Changelog:
    - silenvx/dekita#2059: フック追加
"""

import json
import re
import subprocess
import sys

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input

# Pattern to detect Issue references: #1234
ISSUE_REF_PATTERN = re.compile(r"#(\d+)")

# Pattern to detect Closes/Fixes/Resolves keywords (allowed even for non-existent Issues)
CLOSES_PATTERN = re.compile(r"(?:closes?|fixes?|resolves?)\s*#\d+", re.IGNORECASE)

# Pattern to detect PR references (allowed, validated differently from Issues)
PR_PATTERN = re.compile(r"PR\s*#\d+", re.IGNORECASE)

# Patterns to detect comment commands
COMMENT_COMMAND_PATTERNS = [
    # gh pr comment
    r"gh\s+pr\s+comment\b",
    # gh api .../replies
    r"gh\s+api\s+.*?/replies\b",
    # gh api graphql with addPullRequestReviewThreadReply
    r"gh\s+api\s+graphql.*addPullRequestReviewThreadReply",
    # gh issue comment
    r"gh\s+issue\s+comment\b",
]


def is_comment_command(command: str) -> bool:
    """Check if the command is a comment-posting command."""
    return any(
        re.search(pattern, command, re.IGNORECASE | re.DOTALL)
        for pattern in COMMENT_COMMAND_PATTERNS
    )


def read_body_from_file(file_path: str) -> str | None:
    """Read comment body from a file."""
    try:
        with open(file_path, encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def extract_comment_body(command: str) -> str | None:
    """Extract comment body from command.

    Handles:
    - HEREDOC patterns (checked first, highest priority)
    - --body-file <file> (reads from file)
    - -F body=@<file> (reads from file, gh api format)
    - -b "message" or --body "message"
    - -b $'message' (bash quoting)
    - GraphQL body parameter
    """
    # HEREDOC pattern (check first - highest priority)
    match = re.search(r"<<['\"]?EOF['\"]?\s*\n?(.*?)EOF", command, re.DOTALL)
    if match:
        return match.group(1)

    # --body-file <file> (gh pr comment / gh issue comment)
    match = re.search(r"--body-file\s+[\"']?([^\s\"']+)[\"']?", command)
    if match:
        return read_body_from_file(match.group(1))

    # -F body=@<file> (gh api format, file reference)
    match = re.search(r"-F\s+body=@[\"']?([^\s\"']+)[\"']?", command)
    if match:
        return read_body_from_file(match.group(1))

    # -f body="message" or -F body="message" (gh api inline format)
    match = re.search(r"-[fF]\s+body=[\"'](.+?)[\"']", command, re.DOTALL)
    if match:
        return match.group(1)

    # -b/--body $'message' (bash $'' quoting)
    match = re.search(r"(?:-b|--body)\s+\$'(.+?)'", command, re.DOTALL)
    if match:
        return match.group(1)

    # -b/--body "message" or -b/--body 'message'
    # Exclude $(cat <<) patterns to avoid matching HEREDOC wrappers
    match = re.search(r"(?:-b|--body)\s+[\"']([^$].+?)[\"']", command, re.DOTALL)
    if match:
        return match.group(1)

    # Fallback for simple quoted strings starting with $
    match = re.search(r"(?:-b|--body)\s+[\"'](.+?)[\"']", command, re.DOTALL)
    if match and "<<" not in match.group(1):
        return match.group(1)

    # GraphQL body parameter (in mutation)
    match = re.search(r'body:\s*["\'](.+?)["\']', command, re.DOTALL)
    if match:
        return match.group(1)

    return None


def check_issue_exists(issue_number: int) -> bool:
    """Check if an Issue exists using gh CLI.

    Returns True (fail-open) for any error except explicit "not found".
    """
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(issue_number), "--json", "number"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            return True

        # Only return False if stderr indicates "not found" (Issue doesn't exist)
        # Other errors (network, auth, API) should fail-open
        stderr_lower = result.stderr.lower()
        if "not found" in stderr_lower or "could not resolve" in stderr_lower:
            return False

        # Unknown error - fail-open to avoid blocking valid comments
        return True
    except Exception:
        # On any error, assume Issue exists to avoid false positives (fail-open)
        return True


def extract_issue_references(text: str) -> list[int]:
    """Extract Issue numbers from text, excluding Closes/Fixes and PR patterns."""
    # Remove Closes/Fixes patterns from text first
    text_without_closes = CLOSES_PATTERN.sub("", text)

    # Remove PR #xxx patterns (PRs are validated differently from Issues)
    text_without_pr = PR_PATTERN.sub("", text_without_closes)

    # Find all Issue references
    matches = ISSUE_REF_PATTERN.findall(text_without_pr)
    return [int(m) for m in matches]


def main():
    """Entry point for the Issue reference check hook."""
    try:
        input_json = parse_hook_input()
        tool_name = input_json.get("tool_name", "")

        # Only check Bash commands
        if tool_name != "Bash":
            result = make_approve_result("issue-reference-check")
            log_hook_execution("issue-reference-check", "approve", "not Bash")
            print(json.dumps(result))
            return

        tool_input = input_json.get("tool_input") or {}
        command = tool_input.get("command") or ""

        # Check if it's a comment command
        if not is_comment_command(command):
            result = make_approve_result("issue-reference-check")
            log_hook_execution("issue-reference-check", "approve", "not comment command")
            print(json.dumps(result))
            return

        # Skip validation for cross-repo commands (--repo flag)
        # We can't reliably check Issues in other repos, so fail-open
        if re.search(r"--repo\s+\S+", command):
            result = make_approve_result("issue-reference-check")
            log_hook_execution("issue-reference-check", "approve", "cross-repo command")
            print(json.dumps(result))
            return

        # Extract comment body
        body = extract_comment_body(command)
        if not body:
            result = make_approve_result("issue-reference-check")
            log_hook_execution("issue-reference-check", "approve", "no body found")
            print(json.dumps(result))
            return

        # Extract Issue references
        issue_numbers = extract_issue_references(body)
        if not issue_numbers:
            result = make_approve_result("issue-reference-check")
            log_hook_execution("issue-reference-check", "approve", "no Issue refs")
            print(json.dumps(result))
            return

        # Check each Issue exists
        non_existent = []
        for issue_num in issue_numbers:
            if not check_issue_exists(issue_num):
                non_existent.append(issue_num)

        if non_existent:
            issues_str = ", ".join(f"#{n}" for n in non_existent)
            reason = (
                f"存在しないIssueを参照しています: {issues_str}\n\n"
                "Issueを参照する前に、まず `gh issue create` で作成してください。\n"
                "Issue作成後、実際のIssue番号を使用してコメントを再投稿してください。\n\n"
                "背景: Issue番号を推測して参照すると、\n"
                "実際のIssue番号との不一致が発生します（Issue #2059）。"
            )
            result = make_block_result("issue-reference-check", reason)
            log_hook_execution(
                "issue-reference-check",
                "block",
                reason,
                {"non_existent": non_existent, "command": command[:100]},
            )
            print(json.dumps(result))
            return

        # All Issues exist
        result = make_approve_result("issue-reference-check")
        log_hook_execution(
            "issue-reference-check",
            "approve",
            f"verified: {issue_numbers}",
        )
        print(json.dumps(result))

    except Exception as e:
        # On error, approve to avoid blocking legitimate commands
        print(f"[issue-reference-check] Hook error: {e}", file=sys.stderr)
        result = make_approve_result("issue-reference-check", f"Hook error: {e}")
        log_hook_execution("issue-reference-check", "approve", f"Hook error: {e}")
        print(json.dumps(result))


if __name__ == "__main__":
    main()
