#!/usr/bin/env python3
"""同一フックの連続ブロックを検知し、フック改善を提案する。

Why:
    同じフックが3回以上連続でブロックする場合、フック自体に改善の余地がある
    可能性が高い。SKIP環境変数やメッセージ改善を提案する。

What:
    - セッション内の連続ブロックをフック別にカウント
    - 閾値（3回連続）超過で改善リマインダーを表示
    - セッション内で同一フックへのリマインダーは1回のみ

State:
    - reads: .claude/logs/execution/hook-execution-{session}.jsonl
    - writes: .claude/logs/session/block-reminder-{session}-{hook}.marker

Remarks:
    - 警告型フック（ブロックしない、改善提案を表示）
    - PreToolUseで発火（次のツール実行前にチェック）
    - マーカーファイルで同一フックへの重複リマインダーを防止

Changelog:
    - silenvx/dekita#2432: フック追加
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from lib.execution import log_hook_execution
from lib.logging import read_session_log_entries
from lib.results import make_approve_result
from lib.session import create_hook_context, parse_hook_input

HOOK_NAME = "block-improvement-reminder"

# Threshold for consecutive blocks to trigger reminder
CONSECUTIVE_BLOCK_THRESHOLD = 3


def get_execution_log_dir() -> Path:
    """Get execution log directory path."""
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        return Path(env_dir) / ".claude" / "logs" / "execution"
    return Path.cwd() / ".claude" / "logs" / "execution"


def get_session_marker_dir() -> Path:
    """Get session marker directory path."""
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        return Path(env_dir) / ".claude" / "logs" / "session"
    return Path.cwd() / ".claude" / "logs" / "session"


def get_consecutive_blocks(session_id: str) -> dict[str, int]:
    """Count consecutive blocks from each hook in the session.

    Scans the session's hook execution log and counts how many times
    each hook has blocked consecutively (without any approve in between
    from the same hook).

    Args:
        session_id: Claude session identifier.

    Returns:
        Dict mapping hook_name to consecutive block count.
    """
    log_dir = get_execution_log_dir()
    entries = read_session_log_entries(log_dir, "hook-execution", session_id)

    # Track consecutive blocks per hook
    consecutive_counts: dict[str, int] = {}
    last_decision: dict[str, str] = {}

    for entry in entries:
        hook = entry.get("hook", "")
        decision = entry.get("decision", "")

        if not hook or not decision:
            continue

        # Reset count if hook approved (or any non-block decision)
        if decision != "block":
            if hook in consecutive_counts:
                consecutive_counts[hook] = 0
            last_decision[hook] = decision
        else:
            # Increment count on block
            if hook not in consecutive_counts:
                consecutive_counts[hook] = 0
            consecutive_counts[hook] += 1
            last_decision[hook] = "block"

    return consecutive_counts


def has_shown_reminder(session_id: str, hook_name: str) -> bool:
    """Check if reminder was already shown for this hook in this session.

    Uses a marker file to track which hooks have received reminders
    to avoid showing the same reminder multiple times.

    Args:
        session_id: Claude session identifier.
        hook_name: Name of the hook to check.

    Returns:
        True if reminder was already shown, False otherwise.
    """
    marker_dir = get_session_marker_dir()
    marker_file = marker_dir / f"block-reminder-{session_id}-{hook_name}.marker"
    return marker_file.exists()


def mark_reminder_shown(session_id: str, hook_name: str) -> None:
    """Mark that reminder was shown for this hook in this session.

    Args:
        session_id: Claude session identifier.
        hook_name: Name of the hook.
    """
    marker_dir = get_session_marker_dir()
    try:
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker_file = marker_dir / f"block-reminder-{session_id}-{hook_name}.marker"
        marker_file.write_text("1")
    except OSError:
        pass  # Best effort - don't fail if marker can't be written


def build_reminder_message(hook_name: str, block_count: int) -> str:
    """Build the improvement reminder message.

    Args:
        hook_name: Name of the hook that blocked repeatedly.
        block_count: Number of consecutive blocks.

    Returns:
        Formatted reminder message.
    """
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"💡 フック改善リマインダー: {hook_name}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"このセッションで `{hook_name}` が{block_count}回連続でブロックしています。",
        "",
        "**検討すべき改善策:**",
        "",
        "1. **SKIP環境変数のサポート追加**",
        f"   - `SKIP_{hook_name.upper().replace('-', '_')}=1` でバイパス可能に",
        "",
        "2. **拒否メッセージの改善**",
        "   - 具体的な解決策を提示",
        "   - 何をすべきか明確に説明",
        "",
        "3. **誤検知パターンの修正**",
        "   - 正当なケースをブロックしていないか確認",
        "   - 検出ロジックの精度を改善",
        "",
        "詳細は `hooks-reference` Skill を参照してください。",
    ]
    return "\n".join(lines)


def main() -> None:
    """Main entry point for the hook."""
    # Parse input (required by hook framework)
    hook_input = parse_hook_input()

    ctx = create_hook_context(hook_input)

    # Only process Bash tool (where most blocks occur)
    tool_name = hook_input.get("tool_name", "")
    if tool_name != "Bash":
        # Skip non-Bash tools silently
        print(json.dumps({"continue": True}))
        return

    # Get session ID
    session_id = ctx.get_session_id()
    if not session_id or session_id.startswith("ppid-"):
        # Skip if no valid session ID
        log_hook_execution(HOOK_NAME, "skip", "No valid session ID")
        print(json.dumps({"continue": True}))
        return

    # Get consecutive block counts
    consecutive_blocks = get_consecutive_blocks(session_id)

    # Find hooks that exceeded threshold and haven't been reminded yet
    hooks_to_remind = []
    for hook, count in consecutive_blocks.items():
        if count >= CONSECUTIVE_BLOCK_THRESHOLD:
            if not has_shown_reminder(session_id, hook):
                hooks_to_remind.append((hook, count))

    if not hooks_to_remind:
        # No reminders needed
        print(json.dumps({"continue": True}))
        return

    # Build reminder message for the first hook that needs it
    # (only show one at a time to avoid information overload)
    hook_name, block_count = hooks_to_remind[0]
    message = build_reminder_message(hook_name, block_count)

    # Mark reminder as shown
    mark_reminder_shown(session_id, hook_name)

    # Log the reminder
    log_hook_execution(
        HOOK_NAME,
        "remind",
        f"Showing improvement reminder for {hook_name} ({block_count} consecutive blocks)",
        {"target_hook": hook_name, "block_count": block_count},
    )

    # Return with systemMessage
    result = make_approve_result(HOOK_NAME, message)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
