#!/usr/bin/env python3
"""PR作成時に未完了タスクのあるIssueをCloseしようとしていないかチェックする。

Why:
    受け入れ条件が未完了のIssueをPRでCloseすると、タスクが未完のままクローズされる。
    PR作成時に検出することで、マージ前に対処できる。

What:
    - gh pr createコマンドを検出
    - PRボディからCloses/Fixes #xxxを抽出
    - 対象Issueの受け入れ条件（チェックボックス）を確認
    - 未完了項目がありIssue参照がない場合はブロック

Remarks:
    - ブロック型フック（未完了タスクClose時はブロック）
    - PreToolUse:Bashで発火（gh pr createコマンド）
    - 取り消し線付き項目は完了扱い（Issue #823）
    - 未完了項目に別Issue参照があれば許可（段階的実装パターン）

Changelog:
    - silenvx/dekita#1986: フック追加
    - silenvx/dekita#823: 取り消し線の扱い
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from check_utils import ISSUE_REFERENCE_PATTERN
from issue_checker import (
    extract_issue_numbers_from_pr_body,
    fetch_issue_acceptance_criteria,
)
from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input
from lib.strings import split_command_chain, strip_quoted_strings

HOOK_NAME = "closes-scope-check"


def extract_pr_body_from_command(command: str) -> str | None:
    """Extract PR body from gh pr create command.

    Handles:
    - --body "text" or --body 'text'
    - -b "text" or -b 'text'
    - HEREDOC patterns: --body "$(cat <<'EOF' ... EOF)"

    Args:
        command: The full command string.

    Returns:
        PR body text, or None if not found.
    """
    # Try to extract --body or -b argument
    # Pattern: --body "..." or --body '...' or -b "..." or -b '...'
    body_pattern = r"(?:--body|-b)\s+[\"'](.+?)[\"']"
    match = re.search(body_pattern, command, re.DOTALL)
    if match:
        return match.group(1)

    # Try HEREDOC pattern: --body "$(cat <<'EOF' ... EOF)"
    heredoc_pattern = r"--body\s+\"\$\(cat\s+<<['\"]?(\w+)['\"]?\s*\n(.*?)\n\s*\1\s*\)\""
    match = re.search(heredoc_pattern, command, re.DOTALL)
    if match:
        return match.group(2)

    return None


def has_issue_reference(text: str) -> bool:
    """Check if text contains an Issue reference.

    Args:
        text: The text to check.

    Returns:
        True if text contains a valid Issue reference like "#123", "Issue #123".
    """
    return bool(ISSUE_REFERENCE_PATTERN.search(text))


def check_issues_for_incomplete_items(issue_numbers: list[str]) -> list[dict]:
    """Check each Issue for unchecked items without Issue references.

    Uses fetch_issue_acceptance_criteria from issue_checker.py for consistency
    with merge-time checks (including strikethrough handling per Issue #823).

    Args:
        issue_numbers: List of issue numbers to check.

    Returns:
        List of issues with problems.
        Each item contains: issue_number, title, unchecked_items
    """
    issues_with_problems = []

    for issue_num in issue_numbers:
        success, title, criteria = fetch_issue_acceptance_criteria(issue_num)
        if not success:
            continue

        # Find unchecked items that don't have Issue references
        # criteria is a list of (is_completed, is_strikethrough, text) tuples
        # is_completed already handles [x] marks and strikethrough items
        problematic_items = []
        for is_completed, _is_strikethrough, text in criteria:
            if not is_completed and not has_issue_reference(text):
                problematic_items.append(text)

        if problematic_items:
            issues_with_problems.append(
                {
                    "issue_number": issue_num,
                    "title": title,
                    "unchecked_items": problematic_items[:5],  # Show max 5
                    "total_unchecked": len(problematic_items),
                }
            )

    return issues_with_problems


def is_pr_create_command(command: str) -> bool:
    """Check if command is a gh pr create invocation.

    Args:
        command: The command string.

    Returns:
        True if this is a PR create command.
    """
    # Strip quoted content and split by operators
    stripped = strip_quoted_strings(command)
    parts = split_command_chain(stripped)

    for part in parts:
        if re.match(r"^\s*gh\s+pr\s+create\b", part):
            return True
    return False


def format_block_message(issues_with_problems: list[dict]) -> str:
    """Format the blocking message with issues and guidance.

    Args:
        issues_with_problems: List of issues with incomplete items.

    Returns:
        Formatted message string.
    """
    lines = [
        "[closes-scope-check] PRが未完了タスクのあるIssueをクローズしようとしています。",
        "",
    ]

    for issue in issues_with_problems:
        issue_num = issue["issue_number"]
        title = issue["title"]
        unchecked = issue["unchecked_items"]
        total = issue["total_unchecked"]

        lines.append(f"**Issue #{issue_num}**: {title}")
        lines.append(f"未完了項目（{total}件）:")
        for item in unchecked:
            # Truncate long items
            display_item = item[:60] + "..." if len(item) > 60 else item
            lines.append(f"  - [ ] {display_item}")
        if total > len(unchecked):
            lines.append(f"  - ... 他 {total - len(unchecked)} 件")
        lines.append("")

    lines.extend(
        [
            "**対処方法（いずれかを選択）**:",
            "",
            "1. **全タスクを完了する場合**",
            "   → 全てのチェックボックスをチェック状態にしてからPR作成",
            "",
            "2. **一部のみ実装する場合**",
            "   a. 残りタスク用のIssueを作成: `gh issue create`",
            "   b. 元のIssueで未完了項目に「→ #XXXX」とリンクを追記",
            "   c. 「Closes」の代わりに「Refs」を使用",
            "   d. 新Issueで残りを対応後、元Issueを手動クローズ",
            "",
            "3. **サブIssueパターンを使用する場合**",
            "   a. 各タスクをサブIssueとして作成",
            "   b. PRは「Closes #(サブIssue番号)」を使用",
            "   c. 親Issueは全サブIssue完了後に手動クローズ",
            "",
            "**重要**: 未完了項目を「→ スコープ外」と書くだけでは不十分です。",
            "必ず別Issueへのリンク（#番号）を含めてください。",
        ]
    )

    return "\n".join(lines)


def main() -> None:
    """Main entry point for the hook."""
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only check gh pr create commands
        if not is_pr_create_command(command):
            log_hook_execution(HOOK_NAME, "skip", "Not a PR create command")
            sys.exit(0)

        # Extract PR body from command
        pr_body = extract_pr_body_from_command(command)
        if not pr_body:
            log_hook_execution(HOOK_NAME, "skip", "No PR body found")
            sys.exit(0)

        # Extract Issue numbers from Closes/Fixes patterns
        issue_numbers = extract_issue_numbers_from_pr_body(pr_body)
        if not issue_numbers:
            log_hook_execution(HOOK_NAME, "skip", "No Closes/Fixes Issues found")
            sys.exit(0)

        # Check each Issue for incomplete items
        issues_with_problems = check_issues_for_incomplete_items(issue_numbers)

        if issues_with_problems:
            message = format_block_message(issues_with_problems)
            result = make_block_result(HOOK_NAME, message)
            log_hook_execution(
                HOOK_NAME,
                "block",
                f"Issues with incomplete items: {[i['issue_number'] for i in issues_with_problems]}",
            )
            print(json.dumps(result))
        else:
            log_hook_execution(HOOK_NAME, "approve", "All Issues have complete criteria")
            sys.exit(0)

    except Exception as e:
        # On error, don't block (fail open)
        log_hook_execution(HOOK_NAME, "error", str(e))
        sys.exit(0)


if __name__ == "__main__":
    main()
