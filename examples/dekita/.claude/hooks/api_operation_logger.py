#!/usr/bin/env python3
"""外部APIコマンド（gh, git, npm）の実行詳細をログ記録する。

Why:
    API操作の実行時間やエラー率を分析することで、ワークフローの
    ボトルネックや障害パターンを特定できる。

What:
    - コマンドタイプと操作種別を記録
    - 実行時間（ms）を計測
    - 終了コードと成功/失敗を記録
    - レート制限エラーを検出・フラグ付け

State:
    - writes: .claude/logs/execution/api-operations-{session}.jsonl

Remarks:
    - ログ記録型フック（ブロックしない、記録のみ）
    - api-operation-timerと連携（開始時刻を記録）
    - gh/git/npmコマンドを対象

Changelog:
    - silenvx/dekita#1269: APIエラーとフォールバックのトレーサビリティ
    - silenvx/dekita#1564: レート制限検出の改善
    - silenvx/dekita#1581: URL除去によるパターンマッチング改善
    - silenvx/dekita#1840: セッション別ファイル出力
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Add hooks directory to path for imports
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))

from common import EXECUTION_LOG_DIR
from lib.command_parser import extract_result_from_output, is_target_command, parse_command
from lib.execution import log_hook_execution
from lib.git import get_current_branch
from lib.hook_input import get_tool_result
from lib.logging import log_to_session_file
from lib.session import create_hook_context, parse_hook_input

# Directory for temporary timing files (cross-platform)
TIMING_DIR = Path(tempfile.gettempdir()) / "claude-hooks" / "api-timing"

# Rate limit detection patterns (case-insensitive)
# Issue #1564: Improved patterns to reduce false positives from URLs/docs
# Pattern categories:
# 1. Exact API error codes (most reliable)
# 2. Error messages with action verbs (exceeded, triggered, etc.)
RATE_LIMIT_PATTERNS = [
    # Exact API error codes (highest confidence)
    "rate_limited",  # GraphQL API error code
    # Error messages with action verbs
    "rate limit exceeded",
    "secondary rate limit",
    "abuse detection",
    "too many requests",
]


def _remove_urls_from_line(line: str) -> str:
    """Remove URLs from a line to allow rate limit pattern matching.

    Issue #1581: Instead of skipping entire lines with URLs, remove URL parts
    to detect rate limit errors even when URL and error are on the same line.

    The regex ``r"https?://\\S+"`` matches URLs including trailing non-whitespace
    characters (like colons), which is acceptable for pattern matching purposes.

    Example:
        "GET https://api.github.com/graphql: 403 rate limit exceeded"
        -> "GET  403 rate limit exceeded"
    """
    # Remove http:// and https:// URLs (non-whitespace sequences)
    # Use IGNORECASE to also match HTTP:// and HTTPS://
    return re.sub(r"https?://\S+", "", line, flags=re.IGNORECASE)


def detect_rate_limit(stdout: str, stderr: str) -> bool:
    """Detect if the output indicates a rate limit error.

    Issue #1564: Improved detection to reduce false positives.
    Issue #1581: Remove URLs from lines instead of skipping entire lines.
    - Removes URL parts before pattern matching
    - Detects rate limit errors even when URL and error are on the same line
    - Uses more specific patterns that indicate actual errors

    Args:
        stdout: Command stdout
        stderr: Command stderr

    Returns:
        True if rate limit error is detected, False otherwise.
    """
    combined = stdout + stderr

    # Check line by line, removing URLs before pattern matching
    for line in combined.split("\n"):
        # Remove URLs from line to avoid false positives from doc URLs
        # while still detecting errors on the same line as URLs
        line_without_urls = _remove_urls_from_line(line)
        line_lower = line_without_urls.lower()

        if any(pattern in line_lower for pattern in RATE_LIMIT_PATTERNS):
            return True

    return False


def truncate_stderr_bytes(stderr: str, max_bytes: int = 1000) -> str:
    """Truncate stderr to max_bytes using UTF-8 encoding.

    Issue #1564: Changed from character-based to byte-based truncation.
    This ensures consistent log file sizes regardless of character encoding.

    Args:
        stderr: The stderr string to truncate
        max_bytes: Maximum bytes to keep (default: 1000)

    Returns:
        Truncated string that fits within max_bytes when UTF-8 encoded.
    """
    encoded = stderr.encode("utf-8")
    if len(encoded) <= max_bytes:
        return stderr
    # Truncate and decode, ignoring incomplete characters at the end
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def get_tool_use_id(hook_input: dict) -> str | None:
    """Extract tool_use_id from hook input."""
    return hook_input.get("tool_use_id")


def load_start_time(session_id: str, tool_use_id: str | None, command: str) -> datetime | None:
    """Load the start time for an API operation.

    Returns the start time if found, otherwise None.
    Also cleans up the timing file after reading.
    """
    if not TIMING_DIR.exists():
        return None

    timing_file: Path | None = None

    # Try tool_use_id first (exact match)
    if tool_use_id:
        candidate = TIMING_DIR / f"{session_id}-{tool_use_id}.json"
        if candidate.exists():
            timing_file = candidate
    else:
        # Fallback: use command hash with glob pattern (Issue #1176)
        # Timer adds timestamp to avoid overwrites, so we search by pattern
        # and use the most recent file
        cmd_hash = hashlib.md5(command.encode()).hexdigest()[:8]
        pattern = f"{session_id}-cmd-{cmd_hash}-*.json"
        matching_files = list(TIMING_DIR.glob(pattern))

        # Also check for legacy format without timestamp
        legacy_file = TIMING_DIR / f"{session_id}-cmd-{cmd_hash}.json"
        if legacy_file.exists():
            matching_files.append(legacy_file)

        if matching_files:
            # Use the most recently created file
            timing_file = max(matching_files, key=lambda f: f.stat().st_mtime)

    if timing_file is None or not timing_file.exists():
        return None

    try:
        with open(timing_file, encoding="utf-8") as f:
            timing_data = json.load(f)

        # Parse start time
        start_time_str = timing_data.get("start_time")
        if start_time_str:
            # Parse ISO format with timezone
            start_time = datetime.fromisoformat(start_time_str)
            # Cleanup timing file
            try:
                timing_file.unlink()
            except OSError:
                # Best-effort cleanup: deletion failure is non-critical
                pass
            return start_time
    except (json.JSONDecodeError, OSError, ValueError):
        # Timing file may be corrupted or deleted; duration will be None
        pass

    return None


def calculate_duration_ms(start_time: datetime | None) -> int | None:
    """Calculate duration in milliseconds from start time to now."""
    if start_time is None:
        return None

    now = datetime.now(UTC)
    # Ensure both are timezone-aware
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=UTC)

    duration = now - start_time
    return int(duration.total_seconds() * 1000)


def log_api_operation(
    command_type: str,
    operation: str,
    command: str,
    duration_ms: int | None,
    exit_code: int,
    success: bool,
    parsed: dict[str, Any],
    result: dict[str, Any],
    session_id: str,
    branch: str | None,
    stderr: str | None = None,
    rate_limit_detected: bool = False,
) -> None:
    """Log an API operation to the operations log file.

    Issue #1840: Now writes to session-specific file.

    Args:
        command_type: Type of command (gh, git, npm, etc.)
        operation: Specific operation (pr create, commit, install, etc.)
        command: The full command string (truncated to 500 chars)
        duration_ms: Execution duration in milliseconds
        exit_code: Command exit code
        success: Whether the command succeeded (exit_code == 0)
        parsed: Parsed command arguments
        result: Extracted result information
        session_id: Current Claude session ID
        branch: Current git branch
        stderr: Error output (included when success is False)
        rate_limit_detected: True if rate limit error was detected
    """
    log_entry: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": session_id,
        "type": command_type,
        "operation": operation,
        "command": command[:500],  # Truncate long commands
        "exit_code": exit_code,
        "success": success,
    }

    if duration_ms is not None:
        log_entry["duration_ms"] = duration_ms

    if parsed:
        # Include parsed info but exclude redundant fields
        parsed_info = {k: v for k, v in parsed.items() if k not in ("type", "operation")}
        if parsed_info:
            log_entry["parsed"] = parsed_info

    if result:
        log_entry["result"] = result

    if branch:
        log_entry["branch"] = branch

    # Issue #1269: Include error details when operation fails
    # Issue #1564: Changed to byte-based truncation for consistent log sizes
    if not success and stderr:
        log_entry["error"] = truncate_stderr_bytes(stderr)

    # Issue #1269: Flag rate limit errors for analysis
    if rate_limit_detected:
        log_entry["rate_limit_detected"] = True

    # Issue #1840: Write to session-specific file
    log_to_session_file(EXECUTION_LOG_DIR, "api-operations", session_id, log_entry)


def main() -> None:
    """Log API operation details after execution."""
    hook_input = parse_hook_input()

    ctx = create_hook_context(hook_input)
    if not hook_input:
        print(json.dumps({"continue": True}))
        return

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    raw_result = get_tool_result(hook_input)
    tool_result = raw_result if isinstance(raw_result, dict) else {}

    # Only process Bash tool calls
    if tool_name != "Bash":
        print(json.dumps({"continue": True}))
        return

    command = tool_input.get("command", "")

    # Check if this is a target command
    if not is_target_command(command):
        print(json.dumps({"continue": True}))
        return

    # Parse the command
    parsed = parse_command(command)
    if not parsed:
        print(json.dumps({"continue": True}))
        return

    # Get session and timing info
    session_id = ctx.get_session_id()
    tool_use_id = get_tool_use_id(hook_input)
    start_time = load_start_time(session_id, tool_use_id, command)
    duration_ms = calculate_duration_ms(start_time)

    # Extract result info
    stdout = tool_result.get("stdout", "")
    stderr = tool_result.get("stderr", "")
    exit_code = tool_result.get("exit_code", 0)
    success = exit_code == 0

    result = extract_result_from_output(parsed, stdout, stderr)

    # Issue #1269: Detect rate limit errors (only for failed commands)
    rate_limit_detected = False if success else detect_rate_limit(stdout, stderr)

    # Get branch context
    branch = get_current_branch()

    # Log the operation
    log_api_operation(
        command_type=parsed.get("type", "unknown"),
        operation=parsed.get("operation", "unknown"),
        command=command,
        duration_ms=duration_ms,
        exit_code=exit_code,
        success=success,
        parsed=parsed,
        result=result,
        session_id=session_id,
        branch=branch,
        stderr=stderr if not success else None,  # Only include stderr on failure
        rate_limit_detected=rate_limit_detected,
    )

    # Also log to hook execution log for consistency
    hook_details: dict[str, Any] = {
        "type": parsed.get("type"),
        "operation": parsed.get("operation"),
        "success": success,
        "duration_ms": duration_ms,
    }
    if rate_limit_detected:
        hook_details["rate_limit_detected"] = True
    if not success:
        hook_details["exit_code"] = exit_code

    log_hook_execution("api-operation-logger", "approve", None, hook_details)

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
