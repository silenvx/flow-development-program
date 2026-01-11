#!/usr/bin/env python3
"""ユーザーフィードバック（問題指摘・懸念）を検出し、セッション状態に記録する。

Why:
    ユーザーが動作確認や問題を指摘した場合、類似問題を将来検出できるよう
    振り返り観点の追加を促す。また、セッション状態に記録することで、
    セッション終了時に仕組み化の確認を可能にする。

What:
    - ユーザー入力から否定的フィードバックパターンを検出
    - 「動いてる？」「おかしい」「バグ」等のパターンをマッチ
    - 検出時はACTION_REQUIREDを出力し、/add-perspective実行を促す
    - セッション状態ファイルに `user_feedback_detected: true` を記録

Remarks:
    - type: "command"を使用（type: "prompt"はクラッシュ問題があるため）
    - 1文字入力は誤検知防止のため除外
    - セッション状態はStop hookで仕組み化確認に使用される

Changelog:
    - silenvx/dekita#2506: UserPromptSubmit type:promptクラッシュ対応
    - silenvx/dekita#2754: セッション状態への記録機能追加
"""

import json
import re
import sys

from flow_state_updater import load_state, save_state
from lib.execution import log_hook_execution
from lib.session import create_hook_context, parse_hook_input

# Negative feedback patterns (問題指摘パターン)
# NOTE: 行末アンカー$を削除して「動いてる？何か」のようなテキストも検出可能に
NEGATIVE_PATTERNS = [
    # 動作確認・疑問形
    r"動いてる[？?]?",
    r"正常[？?]?",
    r"大丈夫[？?]?",
    r"問題ない[？?]?",
    # 問題指摘
    r"おかしい",
    r"おかしく",
    r"バグ",
    r"壊れ",
    r"動かない",
    r"動作しない",
    r"エラー",
    r"失敗",
    r"期待通りじゃない",
    r"意図した動作ではない",
    r"想定と違う",
    # 確認要求
    r"確認した[？?]",
    r"テストした[？?]",
    r"検証した[？?]",
    r"チェックした[？?]",
]

# Patterns to exclude (false positive prevention)
EXCLUDE_PATTERNS = [
    r"^(PRを|機能を|ファイルを|コードを)",  # 作業指示
    r"(追加して|作成して|修正して|削除して)$",  # 作業指示
    r"(読んで|確認して|見て)$",  # 調査指示
    r"^こんにちは",  # 挨拶
    r"^ありがとう",  # お礼
]

# Pre-compile patterns for performance (called once at module load)
_COMPILED_EXCLUDE_PATTERN = re.compile("|".join(EXCLUDE_PATTERNS), re.IGNORECASE)
_COMPILED_NEGATIVE_PATTERN = re.compile("|".join(NEGATIVE_PATTERNS), re.IGNORECASE)


def is_feedback(text: str | None) -> bool:
    """Check if the text contains negative feedback patterns.

    Args:
        text: User input text (can be None)

    Returns:
        True if feedback pattern detected, False otherwise

    Note:
        フィードバック検出は日本語の問題指摘表現（例: 「バグ」「壊れ」「おかしい」など）
        を前提としており、NEGATIVE_PATTERNS も実質的に2文字以上の語のみを対象としている。
        1文字だけの入力（例: "?", "w", "笑" など）はノイズになりやすく、誤検知を避けるため
        最小長を2文字に制限している。
    """
    if not text or len(text) < 2:
        return False

    # Check exclusion patterns first (compiled for performance)
    if _COMPILED_EXCLUDE_PATTERN.search(text):
        return False

    # Check negative patterns (compiled for performance)
    if _COMPILED_NEGATIVE_PATTERN.search(text):
        return True

    return False


def _record_user_feedback(session_id: str) -> None:
    """Record user feedback detection in session state.

    Uses flow_state_updater to preserve the expected state schema including
    'workflows' key that other hooks depend on.

    Args:
        session_id: The Claude Code session ID.
    """
    if not session_id:
        return

    # Use flow_state_updater to maintain schema compatibility
    state = load_state(session_id)
    state["user_feedback_detected"] = True
    save_state(session_id, state)


def main():
    """Detect user feedback and output ACTION_REQUIRED if found."""
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        ctx = create_hook_context(input_data)
        user_prompt = input_data.get("user_prompt", "")

        if not user_prompt:
            log_hook_execution("feedback-detector", "approve", "empty prompt")
            print(json.dumps(result))
            return

        if is_feedback(user_prompt):
            # Record feedback detection in session state for Stop hook verification
            session_id = ctx.get_session_id()
            _record_user_feedback(session_id)

            message = (
                "🔍 ユーザーフィードバック検出\n\n"
                "ユーザーが動作確認や問題を指摘しています。\n\n"
                "[ACTION_REQUIRED: /add-perspective]\n\n"
                "類似問題を将来検出できるよう、振り返り観点の追加を検討してください。"
            )
            result["systemMessage"] = message
            log_hook_execution("feedback-detector", "approve", "feedback detected")
        else:
            log_hook_execution("feedback-detector", "approve", "no feedback pattern")

    except Exception as e:
        # Log to stderr for debugging, but don't block user interaction
        print(f"feedback-detector: {e}", file=sys.stderr)
        log_hook_execution("feedback-detector", "error", str(e))

    print(json.dumps(result))


if __name__ == "__main__":
    main()
