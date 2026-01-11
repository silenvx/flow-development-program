#!/usr/bin/env python3
"""マージ成功後に未確認の動作確認Issueをリマインドする。

Why:
    マージ後に動作確認を忘れると本番で問題が発生する可能性がある。
    マージ成功のタイミングでリマインドし確認漏れを防止する。

What:
    - gh pr mergeの成功を検出
    - オープンな動作確認Issueを一覧表示
    - 確認手順と対応方法を案内

Remarks:
    - リマインド型フック（ブロックしない、stderrで情報表示）
    - PostToolUse:Bashで発火（gh pr mergeコマンド成功時）
    - post-merge-observation-issue.pyがIssue作成（補完関係）
    - observation-session-reminder.pyはセッション開始時（責務分離）

Changelog:
    - silenvx/dekita#2547: フック追加
    - silenvx/dekita#2588: createdAtで経過時間表示
"""

from datetime import UTC, datetime

from lib.execution import log_hook_execution
from lib.github import get_observation_issues
from lib.hook_input import get_exit_code, get_tool_result
from lib.repo import is_merge_success
from lib.session import parse_hook_input

HOOK_NAME = "observation-reminder"


def is_pr_merge_command(command: str) -> bool:
    """Check if the command is a PR merge command."""
    return "gh pr merge" in command


def format_issue_age(created_at: str | None) -> str:
    """Format issue age in a human-readable way."""
    if created_at is None or created_at == "":
        return "不明"
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        delta = now - created
        hours = delta.total_seconds() / 3600

        if hours < 1:
            return "1時間以内"
        elif hours < 24:
            return f"{int(hours)}時間前"
        else:
            days = int(hours / 24)
            return f"{days}日前"
    except (ValueError, TypeError, AttributeError):
        return "不明"


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
    tool_result = get_tool_result(input_data) or {}
    exit_code = get_exit_code(tool_result)

    if not is_merge_success(exit_code, tool_output, command):
        return

    # Get pending observation issues
    # Issue #2588: Use shared function with createdAt for age display
    issues = get_observation_issues(limit=10, fields=["number", "title", "createdAt"])
    if not issues:
        log_hook_execution(
            HOOK_NAME,
            "approve",
            "no pending observation issues after merge",
        )
        return

    # Build reminder message
    print("\n" + "=" * 60)
    print("[動作確認リマインダー] 未確認の動作確認Issueがあります")
    print("=" * 60)
    print()

    for issue in issues:
        number = issue.get("number", "?")
        title = issue.get("title", "")
        created_at = issue.get("createdAt", "")
        age = format_issue_age(created_at)

        print(f"  #{number}: {title}")
        print(f"         作成: {age}")
        print()

    print("確認手順:")
    print("  1. 該当機能が期待通り動作することを確認")
    print("  2. 問題なければ `gh issue close <番号>` でクローズ")
    print("  3. 問題があれば別途バグIssueを作成")
    print()
    print("=" * 60)

    log_hook_execution(
        HOOK_NAME,
        "approve",
        f"reminded about {len(issues)} observation issue(s) after merge",
    )


if __name__ == "__main__":
    main()
