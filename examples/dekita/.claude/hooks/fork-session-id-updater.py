#!/usr/bin/env python3
"""UserPromptSubmit時にsession_idをコンテキストに出力する。

Why:
    Fork-session判定には、SessionStartとUserPromptSubmitのsession_idを
    比較する必要がある。この2つが異なる場合がfork-sessionである。

What:
    - UserPromptSubmit時にsession_idを取得
    - タイムスタンプ付きでadditionalContextに出力
    - Claudeがコンテキスト内の2つのIDを比較してfork判定

Remarks:
    - ファイルベースのステートフルな設計は避ける
    - タイムスタンプで最新のsession_id情報を特定可能
    - SessionStartのSession ID = fork元（古いセッション）
    - USER_PROMPT_SESSION_IDの最新 = 現在のセッション

Changelog:
    - silenvx/dekita#2363: フック追加
    - silenvx/dekita#2372: タイムスタンプと説明を追加
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Import session utilities from lib/session
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.session import parse_hook_input


def get_current_timestamp() -> str:
    """現在のタイムスタンプをISO形式で取得する。"""
    try:
        tz = ZoneInfo("Asia/Tokyo")
        now = datetime.now(tz)
    except ZoneInfoNotFoundError:
        now = datetime.now(UTC)
    return now.isoformat(timespec="seconds")


def main() -> None:
    """UserPromptSubmitフックのメイン処理。"""
    # 標準のhook入力パーサーを使用
    hook_input = parse_hook_input()

    # hook inputからsession_idを取得
    session_id = hook_input.get("session_id")

    # session_idがない場合は何も出力しない
    if not session_id:
        return

    # Issue #2372: タイムスタンプと説明を追加
    # 日付が新しいものが最新のsession_id情報
    timestamp = get_current_timestamp()
    explanation = (
        "(現在のsession_id。日付が新しいほど最新。"
        "SessionStartのSession IDはfork元。異なる場合はfork-session)"
    )
    context = f"[USER_PROMPT_SESSION_ID] {timestamp} | {session_id} | {explanation}"
    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
