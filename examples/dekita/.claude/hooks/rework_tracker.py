#!/usr/bin/env python3
from __future__ import annotations

# - 責務: 同一ファイルへの短時間複数編集（手戻り）を追跡
# - 重複なし: 他のフックにはファイル編集追跡機能なし
# - 記録型: 編集履歴をファイルに記録、閾値超過で警告
"""
PostToolUse hook to track rework (multiple edits to the same file).

When the same file is edited multiple times within a short window (5 minutes),
this indicates potential rework that could have been avoided with better planning.

Metrics tracked:
- File path
- Edit timestamps
- Number of edits within window
- Session ID for grouping
"""

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from common import METRICS_LOG_DIR
from lib.execution import log_hook_execution
from lib.results import print_continue_and_log_skip
from lib.session import create_hook_context, get_session_id, parse_hook_input

# Time window for detecting rework (edits within this window count as rework)
REWORK_WINDOW_MINUTES = 5

# Threshold for warning (more than N edits to same file in window)
REWORK_THRESHOLD = 3

# Threshold for strong warning (significantly more edits indicating trial-and-error)
# Issue #1335: Add stronger warning when this threshold is exceeded
REWORK_HIGH_THRESHOLD = 5

# Threshold for critical warning (stop and review plan)
# Issue #1362: Add stop recommendation when this threshold is exceeded
REWORK_CRITICAL_THRESHOLD = 7

# Tracking file location (use TMPDIR for sandbox compatibility)
TRACKING_DIR = Path(tempfile.gettempdir()) / "claude-hooks"
TRACKING_FILE = TRACKING_DIR / "edit-history.json"

# Persistent log for analysis
REWORK_LOG = METRICS_LOG_DIR / "rework-metrics.log"


def load_edit_history() -> dict:
    """Load existing edit history."""
    if TRACKING_FILE.exists():
        try:
            return json.loads(TRACKING_FILE.read_text())
        except Exception:
            pass  # Best effort - corrupted tracking data is ignored
    return {"edits": {}, "session_id": None}


def save_edit_history(data: dict) -> None:
    """Save edit history."""
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    TRACKING_FILE.write_text(json.dumps(data, indent=2))


def generate_warning_message(file_path: str, edit_count: int, window_minutes: int) -> str | None:
    """Generate warning message based on edit count.

    Three-tier warning system (Issue #1362):
    - REWORK_THRESHOLD (3): Light warning
    - REWORK_HIGH_THRESHOLD (5): Strong warning with root cause analysis
    - REWORK_CRITICAL_THRESHOLD (7): Stop recommendation with plan review

    Args:
        file_path: Path to the edited file
        edit_count: Number of edits within the time window
        window_minutes: Size of the time window in minutes

    Returns:
        Warning message string, or None if below threshold
    """
    if edit_count < REWORK_THRESHOLD:
        return None

    file_name = Path(file_path).name

    # Issue #1362: Critical threshold - stop and review plan
    if edit_count >= REWORK_CRITICAL_THRESHOLD:
        return (
            f"🛑 停止推奨: {file_name} を"
            f"{window_minutes}分以内に{edit_count}回編集。\n\n"
            "これは試行錯誤による非効率な作業パターンです。\n"
            "一度立ち止まって、以下を実行してください:\n\n"
            "1. 作業を一時停止する\n"
            "2. 現在のアプローチを振り返る\n"
            "3. 必要に応じてプランを見直す\n\n"
            "続行する前に、変更の全体設計を明確にしてください。"
        )
    # Issue #1335: High threshold - strong warning with root cause analysis
    elif edit_count >= REWORK_HIGH_THRESHOLD:
        return (
            f"⚠️ 高頻度編集検出: {file_name} を"
            f"{window_minutes}分以内に{edit_count}回編集。\n\n"
            "このパターンは試行錯誤アプローチを示唆しています。\n"
            "以下を確認してください:\n"
            "- テストを先に書いていますか？\n"
            "- 変更の要件は明確ですか？\n"
            "- 設計を見直す必要はありませんか？"
        )
    # Default: Light warning
    else:
        return (
            f"📊 手戻り検出: {file_name} を"
            f"{window_minutes}分以内に{edit_count}回編集。\n"
            "事前の調査・計画で編集回数を減らせるかもしれません。"
        )


def log_rework_event(file_path: str, edit_count: int, window_minutes: int) -> None:
    """Log rework event for later analysis."""
    try:
        METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": get_session_id(),
            "type": "rework_detected",
            "file_path": file_path,
            "edit_count": edit_count,
            "window_minutes": window_minutes,
        }
        with open(REWORK_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # ログ書き込み失敗はサイレントに無視（メトリクスは必須ではない）


def main():
    """PostToolUse hook for Edit tool.

    Tracks edits to detect rework patterns.
    """
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(input_data)
        tool_input = input_data.get("tool_input", {})

        # Get the file being edited
        file_path = tool_input.get("file_path", "")
        if not file_path:
            print_continue_and_log_skip("rework-tracker", "no file path", ctx=ctx)
            return

        now = datetime.now(UTC)
        current_session = get_session_id()

        # Load history
        history = load_edit_history()

        # Reset if session changed
        if history.get("session_id") != current_session:
            history = {"edits": {}, "session_id": current_session}

        # Get edit timestamps for this file
        edits = history["edits"].get(file_path, [])

        # Filter to only edits within the window
        window_start = now - timedelta(minutes=REWORK_WINDOW_MINUTES)
        recent_edits = [ts for ts in edits if datetime.fromisoformat(ts) > window_start]

        # Add current edit
        recent_edits.append(now.isoformat())
        history["edits"][file_path] = recent_edits

        # Save updated history
        save_edit_history(history)

        # Check for rework pattern
        edit_count = len(recent_edits)
        warning_message = generate_warning_message(file_path, edit_count, REWORK_WINDOW_MINUTES)
        if warning_message:
            log_rework_event(file_path, edit_count, REWORK_WINDOW_MINUTES)
            result["systemMessage"] = warning_message

    except Exception:
        # フック実行の失敗でClaude Codeをブロックしない
        pass

    log_hook_execution(
        "rework-tracker",
        "approve",
        details={"type": "edit_tracked"},
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
