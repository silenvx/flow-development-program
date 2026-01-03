#!/usr/bin/env python3
"""セッション開始時にオープンIssueをリマインド表示する。

Why:
    オープンIssueを把握せずに作業を始めると、重複作業や優先度の
    低いタスクに時間を費やしてしまう。セッション開始時にリマインド
    することで、優先度の高いIssueへの対応を促す。

What:
    - セッションの最初のBash実行時にオープンIssueを表示
    - 未アサインのIssueのみを表示
    - 高優先度（P1/P2）のIssueを先頭に表示
    - systemMessageで情報提供（ブロックしない）

State:
    reads/writes: .claude/state/session-marker/*.json（common.pyの共通機構）

Remarks:
    - task-start-checklistは要件確認、本フックはIssue確認
    - ファイルロックで並行実行時の競合を防止

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import subprocess
import sys

from common import check_and_update_session_marker
from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.session import parse_hook_input


def get_open_issues() -> list[dict]:
    """Get list of open issues from GitHub that are unassigned."""
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "open",
                "--json",
                "number,title,labels,assignees",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            issues = json.loads(result.stdout)
            # Filter out issues that have assignees (already being worked on)
            return [issue for issue in issues if not issue.get("assignees")]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass  # Best effort - gh command may fail
    return []


# Labels considered high priority (P1/P2 bugs should be fixed before new features)
HIGH_PRIORITY_LABELS = ("P1", "P2", "priority:high", "priority:critical")


def has_priority_label(issue: dict, priority: str) -> bool:
    """Check if issue has a specific priority label."""
    labels = issue.get("labels", [])
    return any(label.get("name") == f"priority:{priority}" for label in labels)


def is_high_priority_issue(issue: dict) -> bool:
    """Check if issue has any high priority label (P1, P2, priority:high, etc.)."""
    labels = issue.get("labels", [])
    label_names = [label.get("name", "") for label in labels]
    return any(name in HIGH_PRIORITY_LABELS for name in label_names)


def format_issues_message(issues: list[dict]) -> str:
    """Format issues into a readable message.

    High priority issues are shown first with emphasis.
    """
    if not issues:
        return ""

    # Separate high priority issues (P1, P2, priority:high, etc.)
    high_priority = [i for i in issues if is_high_priority_issue(i)]
    other_issues = [i for i in issues if not is_high_priority_issue(i)]

    lines = []

    # Show high priority issues first with strong emphasis
    if high_priority:
        lines.append("🚨 **高優先度Issue（優先対応必須）**:")
        for issue in high_priority:
            number = issue.get("number", "?")
            title = issue.get("title", "No title")
            labels = issue.get("labels", [])
            label_names = [label.get("name", "") for label in labels]
            label_str = f" [{', '.join(label_names)}]" if label_names else ""
            lines.append(f"  → #{number}: {title}{label_str}")
        lines.append("")

    # Show other unassigned issues
    if other_issues:
        lines.append("📋 **未アサインのオープンIssue** (対応検討してください):")
        for issue in other_issues[:5]:  # Show max 5 issues
            number = issue.get("number", "?")
            title = issue.get("title", "No title")
            labels = issue.get("labels", [])
            label_names = [label.get("name", "") for label in labels]
            label_str = f" [{', '.join(label_names)}]" if label_names else ""
            lines.append(f"  - #{number}: {title}{label_str}")

        if len(other_issues) > 5:
            lines.append(f"  ... 他 {len(other_issues) - 5} 件")

    if lines:
        lines.append("")
        lines.append("詳細: `gh issue list --state open`")

    return "\n".join(lines)


def main():
    """
    PreToolUse hook for Bash commands.

    Shows open issues reminder on first Bash execution of each session.
    Uses atomic check-and-update to prevent race conditions.
    """
    # Set session_id for proper logging
    parse_hook_input()

    result = {"decision": "approve"}

    try:
        # Atomically check if new session and update marker
        # Returns True only for the first caller when concurrent calls occur
        if check_and_update_session_marker("open-issue-check"):
            issues = get_open_issues()
            if issues:
                message = format_issues_message(issues)
                result["systemMessage"] = message

    except Exception as e:
        # Don't block on errors, just skip the reminder
        print(f"[open-issue-reminder] Error: {e}", file=sys.stderr)

    log_hook_execution(
        "open-issue-reminder", result.get("decision", "approve"), result.get("reason")
    )
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
