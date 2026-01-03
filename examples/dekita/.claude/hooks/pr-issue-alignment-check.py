#!/usr/bin/env python3
# - 責務: PR作成時に対象Issueの受け入れ条件を検証
# - 重複なし: closes-validation.pyはキーワード検証、このフックは内容検証
# - 警告型: 未完了の受け入れ条件がある場合、systemMessageで強い警告
"""
PreToolUse hook to verify Issue acceptance criteria when creating PRs.

When `gh pr create` is detected with Closes/Fixes keywords,
this hook:
1. Fetches the Issue content
2. Extracts acceptance criteria (checkbox items in body)
3. Warns if any criteria are incomplete

Background (Issue #543, #592):
- Issue #538 was closed with a PR that implemented something different
- Issue #590 was closed with a PR that only added debug logs (not a fix)
This hook ensures acceptance criteria are visible and verified.
"""

import json
import re
import subprocess

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import print_continue_and_log_skip
from lib.session import create_hook_context, parse_hook_input
from lib.strings import strip_quoted_strings

# Maximum length for issue body display (truncated if longer)
MAX_ISSUE_BODY_LENGTH = 1000


def extract_acceptance_criteria(body: str) -> list[tuple[bool, str]]:
    """Extract acceptance criteria (checkbox items) from Issue body.

    Args:
        body: The Issue body text.

    Returns:
        List of (is_completed, criteria_text) tuples.
    """
    criteria = []

    # Match checkbox items: - [ ] or - [x] or - [X]
    # Also handles * [ ] format
    pattern = r"^[\s]*[-*]\s*\[([ xX])\]\s*(.+)$"

    for line in body.split("\n"):
        match = re.match(pattern, line)
        if match:
            is_completed = match.group(1).lower() == "x"
            criteria_text = match.group(2).strip()
            criteria.append((is_completed, criteria_text))

    return criteria


def format_acceptance_criteria_message(
    issue_num: str, title: str, criteria: list[tuple[bool, str]], is_closed: bool = False
) -> str:
    """Format status message for Issue acceptance criteria.

    Includes both incomplete (warning) and completed criteria.

    Args:
        issue_num: The issue number.
        title: The issue title.
        criteria: List of (is_completed, text) tuples.
        is_closed: Whether the issue is already closed.

    Returns:
        Formatted status message summarizing acceptance criteria.
    """
    incomplete = [text for is_completed, text in criteria if not is_completed]
    completed_items = [text for is_completed, text in criteria if is_completed]

    header = f"### Issue #{issue_num}: {title}"
    if is_closed:
        header += " (CLOSED)"
    lines = [header]
    lines.append("")

    if is_closed and incomplete:
        lines.append("ℹ️ *このIssueは既にクローズ済みです。`Closes #N` は効果がありません。*")
        lines.append("")

    if incomplete:
        lines.append(f"❌ **未完了の受け入れ条件: {len(incomplete)}件**")
        for text in incomplete:
            lines.append(f"  - [ ] {text}")
        lines.append("")

    if completed_items:
        lines.append(f"✅ 完了済み: {len(completed_items)}件")
        for text in completed_items:
            lines.append(f"  - [x] {text}")

    return "\n".join(lines)


def extract_issue_numbers_from_body(command: str) -> list[str]:
    """Extract issue numbers from Closes/Fixes keywords in PR body.

    Args:
        command: The bash command string.

    Returns:
        List of issue numbers found.
    """
    body = None

    # Try HEREDOC pattern first (most common in this project)
    # Matches: --body "$(cat <<'EOF' ... EOF )"
    heredoc_match = re.search(
        r'--body\s+"\$\(cat\s+<<[\'"]?EOF[\'"]?\s*(.*?)\s*EOF\s*\)"',
        command,
        re.DOTALL,
    )
    if heredoc_match:
        body = heredoc_match.group(1)

    # Try double-quoted body (may contain escaped quotes)
    if body is None:
        dq_match = re.search(r'--body\s+"((?:[^"\\]|\\.)*)"', command)
        if dq_match:
            body = dq_match.group(1)

    # Try single-quoted body (may contain any chars except single quote)
    if body is None:
        sq_match = re.search(r"--body\s+'([^']*)'", command)
        if sq_match:
            body = sq_match.group(1)

    if body is None:
        return []

    # Find Closes #XXX, Fixes #XXX, Resolves #XXX patterns
    # Case insensitive, handles multiple issues, allows optional colon (e.g., "Closes: #123")
    pattern = r"(?:closes?|fix(?:es)?|resolves?):?\s+#(\d+)"
    matches = re.findall(pattern, body, re.IGNORECASE)

    return list(set(matches))  # Remove duplicates


def is_pr_create_command(command: str) -> bool:
    """Check if command is gh pr create.

    Args:
        command: The bash command string.

    Returns:
        True if this is a gh pr create command.
    """
    cmd = strip_quoted_strings(command)
    return bool(re.search(r"gh\s+pr\s+create\b", cmd))


def fetch_issue_content(issue_number: str) -> tuple[bool, str, str, str]:
    """Fetch issue title, body and state using gh CLI.

    Args:
        issue_number: The issue number.

    Returns:
        Tuple of (success, title, body, state).
        state is "OPEN" or "CLOSED".
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                issue_number,
                "--json",
                "title,body,state",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if result.returncode != 0:
            return (False, "", "", "")

        # Parse JSON output directly instead of using jq
        data = json.loads(result.stdout)
        title = data.get("title") or ""
        body = data.get("body") or ""  # Handle null body (issues with no description)
        state = data.get("state") or "OPEN"
        return (True, title, body, state)

    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return (False, "", "", "")


def main():
    """
    PreToolUse hook for Bash commands.

    Displays Issue content when creating PRs with Closes/Fixes keywords.
    """
    result = {"decision": "approve"}

    try:
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(input_data)
        tool_name = input_data.get("tool_name", "")

        if tool_name != "Bash":
            print_continue_and_log_skip(
                "pr-issue-alignment-check", f"not Bash: {tool_name}", ctx=ctx
            )
            return

        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Check if this is a gh pr create command
        if not is_pr_create_command(command):
            print_continue_and_log_skip("pr-issue-alignment-check", "not gh pr create", ctx=ctx)
            return

        # Extract issue numbers from body
        issue_numbers = extract_issue_numbers_from_body(command)
        if not issue_numbers:
            print_continue_and_log_skip(
                "pr-issue-alignment-check", "no issue numbers in body", ctx=ctx
            )
            return

        # Fetch and analyze issue content for each issue
        messages = []
        has_incomplete_open_criteria = False
        total_incomplete_open = 0
        closed_issues_with_incomplete = []

        for issue_num in issue_numbers:
            success, title, body, state = fetch_issue_content(issue_num)
            if not success:
                continue

            is_closed = state == "CLOSED"

            # Extract and check acceptance criteria
            criteria = extract_acceptance_criteria(body)

            if criteria:
                # Has acceptance criteria - format with completion status
                incomplete_count = sum(1 for is_completed, _ in criteria if not is_completed)
                if incomplete_count > 0:
                    if is_closed:
                        closed_issues_with_incomplete.append(issue_num)
                    else:
                        has_incomplete_open_criteria = True
                        total_incomplete_open += incomplete_count

                status_msg = format_acceptance_criteria_message(
                    issue_num, title, criteria, is_closed=is_closed
                )
                messages.append(status_msg)
            else:
                # No acceptance criteria - show issue content for reference
                display_body = body
                if len(display_body) > MAX_ISSUE_BODY_LENGTH:
                    display_body = display_body[:MAX_ISSUE_BODY_LENGTH] + "\n..."
                header = f"### Issue #{issue_num}: {title}"
                if is_closed:
                    header += " (CLOSED)"
                messages.append(f"{header}\n\n（受け入れ条件なし）\n\n{display_body}")

        if messages:
            if has_incomplete_open_criteria:
                # Strong warning for incomplete criteria on OPEN issues
                result["systemMessage"] = (
                    "🚨 **警告: 未完了の受け入れ条件があります！**\n\n"
                    f"❌ このPRでクローズされる全てのIssueの受け入れ条件のうち、"
                    f"合計 {total_incomplete_open} 件が未完了です。\n\n"
                    + "\n\n---\n\n".join(messages)
                    + "\n\n"
                    "⚠️ **このPRをマージすると、Issueが不完全な状態で"
                    "クローズされる可能性があります。**\n\n"
                    "確認してください:\n"
                    "1. 実装内容がIssueの全ての要求を満たしていますか？\n"
                    "2. 未完了の項目は意図的に対象外としていますか？\n"
                    "3. Issueの受け入れ条件を更新する必要がありますか？"
                )
                log_hook_execution(
                    "pr-issue-alignment-check",
                    "approve",
                    f"未完了の受け入れ条件あり: {total_incomplete_open}件 "
                    f"(#{', #'.join(issue_numbers)})",
                )
            elif closed_issues_with_incomplete:
                # Info message for closed issues with incomplete criteria
                result["systemMessage"] = (
                    "ℹ️ **PR作成前のIssue確認**\n\n" + "\n\n---\n\n".join(messages) + "\n\n"
                    f"💡 Issue #{', #'.join(closed_issues_with_incomplete)} は既に"
                    "クローズ済みのため、`Closes #N` は効果がありません。"
                )
                log_hook_execution(
                    "pr-issue-alignment-check",
                    "approve",
                    f"CLOSED Issueへの参照: #{', #'.join(closed_issues_with_incomplete)}",
                )
            else:
                # Info message when all criteria complete or no criteria
                result["systemMessage"] = (
                    "✅ **PR作成前のIssue確認**\n\n" + "\n\n---\n\n".join(messages) + "\n\n"
                    "💡 実装内容がIssueの要求と一致していることを確認してください。"
                )
                log_hook_execution(
                    "pr-issue-alignment-check",
                    "approve",
                    f"受け入れ条件確認: #{', #'.join(issue_numbers)}",
                )

    except Exception as e:
        # Don't block on errors
        log_hook_execution(
            "pr-issue-alignment-check",
            "error",
            f"フックエラー: {e}",
        )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
