#!/usr/bin/env python3
"""タスク開始時に確認チェックリストをリマインド表示する。

Why:
    タスク開始時に要件・設計の確認を怠ると、実装後の手戻りが発生する。
    チェックリストをリマインドすることで、確認漏れを防ぐ。

What:
    - セッションの最初のツール実行時にチェックリストを表示
    - 要件確認、設計判断、影響範囲、前提条件のチェック項目を提示
    - systemMessageで情報提供（ブロックしない）

State:
    reads/writes: .claude/state/session-marker/*.json（common.pyの共通機構）

Remarks:
    - open-issue-reminderはIssue確認、本フックは要件・設計確認
    - common.pyの統一セッションマーカー機構を使用（排他制御付き）

Changelog:
    - silenvx/dekita#1234: フック追加
"""

import json
import sys

from common import check_and_update_session_marker
from lib.execution import log_hook_execution
from lib.session import parse_hook_input


def get_checklist_message() -> str:
    """Generate the task start checklist message."""
    lines = [
        "📋 **タスク開始前の確認チェックリスト**",
        "",
        "以下の点を確認してからタスクを開始してください:",
        "",
        "**⚠️ セッション開始時ファイル確認（最重要）**:",
        "  [ ] セッション開始時にファイルを読み込んだか？",
        "  [ ] 読み込んだファイルの内容は**タスク**か？",
        "  [ ] タスクなら、他の作業より先に実行すること",
        "",
        "**要件確認**:",
        "  [ ] 要件は明確か？曖昧な点があれば質問する",
        "  [ ] ユーザーの意図を正しく理解しているか？",
        "  [ ] 「〜したい」の背景・目的は何か？",
        "",
        "**設計判断**:",
        "  [ ] 設計上の選択肢がある場合、ユーザーに確認する",
        "  [ ] 既存のコードパターン・規約を把握しているか？",
        "  [ ] 事前に決めておくべきことはないか？",
        "",
        "**影響範囲**:",
        "  [ ] 変更の影響範囲を把握しているか？",
        "  [ ] 破壊的変更はないか？あれば事前に確認する",
        "",
        "**前提条件**:",
        "  [ ] 必要な環境・依存関係は整っているか？",
        "  [ ] Context7/Web検索で最新情報を確認すべきか？",
        "",
        "💡 不明点があれば、実装前に必ず質問してください。",
    ]
    return "\n".join(lines)


def main():
    """
    PreToolUse hook for Edit/Write/Bash commands.

    Shows task start checklist on first tool execution of each session.
    Uses atomic check-and-update to prevent race conditions.
    """
    # Set session_id for proper logging
    parse_hook_input()

    result = {"decision": "approve"}

    try:
        # Atomically check if new session and update marker
        # Returns True only for the first caller when concurrent calls occur
        if check_and_update_session_marker("task-start-checklist"):
            result["systemMessage"] = get_checklist_message()

    except Exception as e:
        # Don't block on errors, just skip the reminder
        print(f"[task-start-checklist] Error: {e}", file=sys.stderr)

    log_hook_execution(
        "task-start-checklist", result.get("decision", "approve"), result.get("reason")
    )
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
