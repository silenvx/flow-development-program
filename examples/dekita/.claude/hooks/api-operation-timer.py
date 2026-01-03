#!/usr/bin/env python3
"""外部APIコマンドの開始時刻を記録する（api-operation-loggerと連携）。

Why:
    APIコマンドの実行時間を計測するため、開始時刻を記録しておく必要がある。
    PostToolUseフックで終了時刻と比較して実行時間を算出する。

What:
    - gh, git, npmコマンドを検出
    - 開始時刻を一時ファイルに記録
    - 古いタイミングファイルをクリーンアップ

State:
    - writes: /tmp/claude-hooks/api-timing/{session}-{tool_use_id}.json

Remarks:
    - 記録型フック（ブロックしない、開始時刻の記録のみ）
    - api-operation-loggerと連携（本フックがPre、loggerがPost）
    - 1時間以上経過した古いファイルは自動クリーンアップ

Changelog:
    - silenvx/dekita#1176: コマンドハッシュ+タイムスタンプでの一意性確保
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

# Add hooks directory to path for imports
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))

from lib.command_parser import is_target_command
from lib.execution import log_hook_execution
from lib.session import create_hook_context, parse_hook_input

# Directory for temporary timing files (cross-platform)
TIMING_DIR = Path(tempfile.gettempdir()) / "claude-hooks" / "api-timing"


def get_tool_use_id(hook_input: dict) -> str | None:
    """Extract tool_use_id from hook input.

    The tool_use_id uniquely identifies a specific tool invocation,
    allowing us to match PreToolUse and PostToolUse events.
    """
    # Try different possible locations for tool_use_id
    tool_use_id = hook_input.get("tool_use_id")
    if tool_use_id:
        return tool_use_id

    # Fallback: use a hash of session_id + timestamp for uniqueness
    # This is less reliable but better than nothing
    return None


def save_start_time(session_id: str, tool_use_id: str | None, command: str) -> None:
    """Save the start time for an API operation.

    Creates a temporary file with timing information that will be read
    by the PostToolUse hook to calculate duration.
    """
    TIMING_DIR.mkdir(parents=True, exist_ok=True)

    # Use tool_use_id if available, otherwise use command hash + timestamp
    if tool_use_id:
        filename = f"{session_id}-{tool_use_id}.json"
    else:
        # Fallback: use command hash + timestamp for uniqueness (Issue #1176)
        # Without timestamp, same command executed multiple times would overwrite
        import hashlib

        cmd_hash = hashlib.md5(command.encode()).hexdigest()[:8]
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        filename = f"{session_id}-cmd-{cmd_hash}-{timestamp}.json"

    timing_file = TIMING_DIR / filename

    timing_data = {
        "start_time": datetime.now(UTC).isoformat(),
        "session_id": session_id,
        "command_preview": command[:200],
    }

    try:
        with open(timing_file, "w", encoding="utf-8") as f:
            json.dump(timing_data, f)
    except OSError:
        # Ignore write errors - timing is best-effort
        pass


def cleanup_old_timing_files() -> None:
    """Remove timing files older than 1 hour.

    This prevents accumulation of orphaned timing files from
    interrupted operations.
    """
    if not TIMING_DIR.exists():
        return

    now = datetime.now(UTC)
    max_age_seconds = 3600  # 1 hour

    try:
        for timing_file in TIMING_DIR.glob("*.json"):
            try:
                age = now.timestamp() - timing_file.stat().st_mtime
                if age > max_age_seconds:
                    timing_file.unlink()
            except OSError:
                # Best-effort cleanup: ignore individual file stat/unlink failures
                pass
    except OSError:
        # Best-effort cleanup: ignore directory iteration failures
        pass


def main() -> None:
    """Record start time for API operations."""
    hook_input = parse_hook_input()

    ctx = create_hook_context(hook_input)
    if not hook_input:
        print(json.dumps({"decision": "approve"}))
        return

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    # Only process Bash tool calls
    if tool_name != "Bash":
        print(json.dumps({"decision": "approve"}))
        return

    command = tool_input.get("command", "")

    # Check if this is a target command (gh, git, npm)
    if not is_target_command(command):
        print(json.dumps({"decision": "approve"}))
        return

    # Get session and tool IDs
    session_id = ctx.get_session_id()
    tool_use_id = get_tool_use_id(hook_input)

    # Save start time
    save_start_time(session_id, tool_use_id, command)

    # Periodically cleanup old timing files
    cleanup_old_timing_files()

    # Log the timing start
    log_hook_execution(
        "api-operation-timer",
        "approve",
        "Started timing for API operation",
        {"command_preview": command[:100]},
    )

    # Always approve - this hook only records timing
    print(json.dumps({"decision": "approve"}))


if __name__ == "__main__":
    main()
