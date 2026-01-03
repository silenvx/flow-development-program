#!/usr/bin/env python3
"""PRマージや一定アクション後に振り返りをリマインド。

Why:
    タスク完了後やセッションが長時間続いた際に振り返りを促し、
    学習機会を逃さないようにする。

What:
    - gh pr merge / git merge 成功を検出しリマインド
    - 10アクションごとに定期リマインド
    - セッション状態ファイルでアクション回数を追跡

State:
    - writes: /tmp/claude-hooks/reflection-state-{session_id}.json

Remarks:
    - 非ブロック型（リマインダー表示のみ）
    - PostToolUse:Bash フック
    - PRマージリマインドと定期リマインドは排他（マージ優先）

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#1842: get_tool_result()ヘルパー使用に統一
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

# 共通モジュール
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.session import create_hook_context, parse_hook_input

# 振り返りリマインドの間隔（アクション回数）
REMINDER_INTERVAL_ACTIONS = 10

# セッション状態ディレクトリ
SESSION_DIR = Path(tempfile.gettempdir()) / "claude-hooks"


def get_reflection_state_file(session_id: str) -> Path:
    """Get the file path for storing reflection state.

    Args:
        session_id: The Claude session ID to scope the file.

    Returns:
        Path to session-specific reflection state file.
    """
    return SESSION_DIR / f"reflection-state-{session_id}.json"


def load_reflection_state(session_id: str) -> dict:
    """振り返り状態を読み込み

    Args:
        session_id: The Claude session ID.

    Returns:
        Reflection state dictionary.
    """
    state_file = get_reflection_state_file(session_id)
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except Exception:
            pass  # Best effort - corrupted state is ignored
    return {"action_count": 0, "last_reminder_action": 0, "pr_merged_count": 0}


def save_reflection_state(session_id: str, state: dict) -> None:
    """振り返り状態を保存

    Args:
        session_id: The Claude session ID.
        state: State dictionary to save.
    """
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        state_file = get_reflection_state_file(session_id)
        state_file.write_text(json.dumps(state))
    except Exception:
        # State persistence is best-effort; failures here should not
        # block the hook or affect Claude Code operation
        pass


def increment_action_count(state: dict) -> int:
    """アクションカウントをインクリメントして返す

    セッション中のBashコマンド実行回数を追跡する。
    状態ファイルに保存されたカウントを使用する。
    """
    state["action_count"] = state.get("action_count", 0) + 1
    return state["action_count"]


def is_pr_merge_command(command: str) -> bool:
    """PRマージコマンドかどうかを判定"""
    # gh pr merge パターン
    if re.search(r"gh\s+pr\s+merge", command):
        return True
    # git merge with PR branch パターン
    if re.search(r"git\s+merge.*(?:feat|fix|docs|refactor|test)/", command):
        return True
    return False


def check_pr_merge_result(tool_result: dict) -> bool:
    """PRマージが成功したかどうかを確認"""
    # 終了コードが0ならマージ成功
    if tool_result.get("exit_code", 1) != 0:
        return False

    stdout = tool_result.get("stdout", "")
    # マージ成功のメッセージを確認
    # gh pr merge: "Merged", "Pull request"
    # git merge: "Merge made by", "Fast-forward"
    merge_indicators = [
        "Merged",
        "merged",
        "Pull request",
        "Merge made by",
        "Fast-forward",
    ]
    return any(indicator in stdout for indicator in merge_indicators)


def main():
    """Remind about reflection after merge operations."""
    result = {"continue": True}

    try:
        input_data = parse_hook_input()

        ctx = create_hook_context(input_data)
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Issue #1842: Use standardized helper for tool result extraction
        # Ensure we have a dict for .get() calls (tool_result can be a string)
        raw_result = get_tool_result(input_data)
        tool_result = raw_result if isinstance(raw_result, dict) else {}

        # Bash以外はスキップ
        if tool_name != "Bash":
            print(json.dumps(result))
            return

        command = tool_input.get("command", "")
        session_id = ctx.get_session_id()

        # 状態を読み込み（セッションIDでファイルが分離されるためリセット不要）
        state = load_reflection_state(session_id)

        reminder_message = None

        # 1. PRマージ検出
        if is_pr_merge_command(command) and check_pr_merge_result(tool_result):
            state["pr_merged_count"] = state.get("pr_merged_count", 0) + 1
            reminder_message = (
                "🎉 PRがマージされました！\n"
                "タスク完了後は振り返り（五省）を行うと効果的です:\n"
                "- 要件を正確に理解できたか\n"
                "- 実装品質は十分か\n"
                "- 検証は適切に行ったか\n"
                "- 効率的に作業できたか"
            )

        # 2. 定期リマインダー（一定回数のアクション後）
        # アクションカウントをインクリメント
        current_action_count = increment_action_count(state)
        last_reminder_count = state.get("last_reminder_action", 0)
        # REMINDER_INTERVAL_ACTIONS回ごとにリマインド
        if (
            current_action_count // REMINDER_INTERVAL_ACTIONS
            > last_reminder_count // REMINDER_INTERVAL_ACTIONS
        ):
            state["last_reminder_action"] = current_action_count
            if not reminder_message:  # PRマージメッセージがない場合のみ
                reminder_message = (
                    f"📊 セッション進行中（{current_action_count}回のアクション）\n"
                    "定期的な振り返りを推奨します。\n"
                    "ログ: .claude/logs/execution/hook-execution-*.jsonl, .claude/logs/metrics/*.jsonl"
                )

        # 状態を保存
        save_reflection_state(session_id, state)

        # リマインダーメッセージがあれば表示
        if reminder_message:
            result["systemMessage"] = reminder_message
            log_hook_execution(
                "reflection-reminder",
                "approve",
                "Reflection reminder shown",
                {"trigger": "pr_merge" if "PR" in reminder_message else "periodic"},
            )
        else:
            log_hook_execution(
                "reflection-reminder", "approve", "No reminder triggered", {"type": "no_reminder"}
            )

    except Exception:
        # フック実行の失敗でClaude Codeをブロックしない
        pass

    print(json.dumps(result))


if __name__ == "__main__":
    main()
