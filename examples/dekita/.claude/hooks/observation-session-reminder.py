#!/usr/bin/env python3
"""セッション開始時に未確認の動作確認Issueをリマインドする。

Why:
    セッション開始時に未確認の動作確認Issueを表示することで、
    CI待ちや関連作業中に自然と確認する機会を提供する。

What:
    - オープンな動作確認Issueを一覧取得
    - Issue番号と件数を簡潔に表示
    - 確認方法（gh issue close）を案内

Remarks:
    - リマインド型フック（ブロックしない、stderrで情報表示）
    - SessionStartで発火
    - observation-reminder.pyはマージ後リマインド（責務分離）
    - 簡潔な表示でセッション開始時の負担を軽減

Changelog:
    - silenvx/dekita#2583: フック追加
"""

from lib.execution import log_hook_execution
from lib.github import get_observation_issues
from lib.session import parse_hook_input

HOOK_NAME = "observation-session-reminder"


def main() -> None:
    """Main hook logic."""
    input_data = parse_hook_input()
    if not input_data:
        return

    # Get pending observation issues
    issues = get_observation_issues()
    if not issues:
        log_hook_execution(
            HOOK_NAME,
            "approve",
            "no pending observation issues at session start",
        )
        return

    # Build reminder message - concise for session start
    count = len(issues)
    issue_list = ", ".join(f"#{i.get('number', '?')}" for i in issues)

    print(f"\n📋 動作確認Issue {count}件: {issue_list}")
    print("   → CI待ちや関連作業中に確認できれば `gh issue close <番号>`")

    log_hook_execution(
        HOOK_NAME,
        "approve",
        f"reminded about {count} observation issue(s) at session start",
    )


if __name__ == "__main__":
    main()
