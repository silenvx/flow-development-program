"""Rate limit management for ci-monitor.

This module handles GitHub API rate limit checking, caching, and interval adjustment.
Extracted from ci-monitor.py as part of Issue #1754 refactoring.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ci_monitor.constants import (
    RATE_LIMIT_ADJUST_THRESHOLD,
    RATE_LIMIT_CACHE_TTL,
    RATE_LIMIT_CRITICAL_THRESHOLD,
    RATE_LIMIT_REST_PRIORITY_THRESHOLD,
    RATE_LIMIT_WARNING_THRESHOLD,
)
from ci_monitor.github_api import run_gh_command
from ci_monitor.models import RateLimitEventType

# Add parent directory to path for importing lib modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hooks"))
from common import EXECUTION_LOG_DIR  # noqa: E402
from lib.execution import log_hook_execution  # noqa: E402
from lib.logging import log_to_session_file  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

# Rate limit cache configuration (Issue #1291)
RATE_LIMIT_FILE_CACHE_PATH = Path(".claude/logs/execution/rate-limit-cache.json")

# In-memory cache for rate limit data (Issue #1347)
_rate_limit_cache: tuple[int, int, int, float] | None = None
_rate_limit_cache_lock = threading.Lock()

# Issue #1360: Track REST priority mode state to avoid repeated logging
_rest_priority_mode_active = False
_rest_priority_mode_lock = threading.Lock()


def format_reset_time(reset_timestamp: int) -> tuple[int, str]:
    """Format reset timestamp to human-readable time.

    Issue #1096: Helper to format time until rate limit resets.

    Args:
        reset_timestamp: Unix timestamp when the limit resets.

    Returns:
        Tuple of (seconds_until_reset, human_readable_time).
    """
    if reset_timestamp == 0:
        return 0, "不明"

    now = datetime.now(UTC).timestamp()
    seconds_until_reset = max(0, int(reset_timestamp - now))

    if seconds_until_reset <= 0:
        return 0, "まもなく"
    elif seconds_until_reset < 60:
        return seconds_until_reset, f"{seconds_until_reset}秒"
    else:
        minutes = seconds_until_reset // 60
        return seconds_until_reset, f"{minutes}分"


def _read_rate_limit_file_cache() -> tuple[int, int, int, float] | None:
    """Read rate limit from file cache for cross-session sharing.

    Issue #1291: Multiple ci-monitor sessions can share the same cache file
    to reduce redundant API calls.

    Returns:
        Tuple of (remaining, limit, reset_timestamp, cached_at) if cache is valid,
        None if cache doesn't exist or is stale/corrupted.
    """
    try:
        if not RATE_LIMIT_FILE_CACHE_PATH.exists():
            return None
        data = json.loads(RATE_LIMIT_FILE_CACHE_PATH.read_text())
        cached_at = data["timestamp"]
        # Validate timestamp: reject non-numeric, negative, or future values
        if not isinstance(cached_at, (int, float)) or cached_at < 0:
            return None
        now = time.time()
        if cached_at > now:
            return None  # Future timestamp (clock skew or malicious data)
        if now - cached_at < RATE_LIMIT_CACHE_TTL:
            return data["remaining"], data["limit"], data["reset"], cached_at
    except (json.JSONDecodeError, KeyError, OSError, TypeError):
        # Cache file corrupted/missing/invalid - return None to trigger API call
        pass
    return None


def _write_rate_limit_file_cache(remaining: int, limit: int, reset: int) -> None:
    """Write rate limit to file cache for cross-session sharing.

    Issue #1291: Cache is written after each successful API call.
    Write failures are silently ignored as caching is best-effort.
    """
    try:
        RATE_LIMIT_FILE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        RATE_LIMIT_FILE_CACHE_PATH.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "remaining": remaining,
                    "limit": limit,
                    "reset": reset,
                }
            )
        )
    except OSError:
        # Cache write failure is non-critical
        pass


def check_rate_limit(use_cache: bool = True) -> tuple[int, int, int]:
    """Check GitHub API rate limit (Issue #896).

    Issue #1347: Added caching to reduce API calls. Cache is valid for 60 seconds.
    The remaining count may be slightly stale, but since rate limit resets hourly
    and we use it for conservative decisions, this is acceptable.

    Issue #1291: Added file-based cache for cross-session sharing.
    Cache priority: 1) in-memory, 2) file, 3) API call.

    Thread-safety: Uses a lock to protect cache access for monitor_multiple_prs().

    Args:
        use_cache: If True, return cached value if available and fresh.
                   Set to False to force a fresh API call.

    Returns:
        Tuple of (remaining, limit, reset_timestamp).
        Returns (0, 0, 0) if the check fails.
    """
    global _rate_limit_cache

    # 1. Check in-memory cache first (thread-safe)
    with _rate_limit_cache_lock:
        if use_cache and _rate_limit_cache is not None:
            remaining, limit, reset_ts, cached_at = _rate_limit_cache
            if time.time() - cached_at < RATE_LIMIT_CACHE_TTL:
                return remaining, limit, reset_ts

    # 2. Check file cache for cross-session sharing (Issue #1291)
    if use_cache:
        file_cache = _read_rate_limit_file_cache()
        if file_cache is not None:
            remaining, limit, reset_ts, cached_at = file_cache
            # Update in-memory cache from file cache (preserve original timestamp)
            with _rate_limit_cache_lock:
                _rate_limit_cache = (remaining, limit, reset_ts, cached_at)
            return remaining, limit, reset_ts

    # 3. API call outside the lock to avoid blocking other threads
    success, output = run_gh_command(
        [
            "api",
            "rate_limit",
            "--jq",
            ".resources.graphql | [.remaining, .limit, .reset] | @tsv",
        ]
    )
    if success and output:
        try:
            parts = output.strip().split("\t")
            if len(parts) >= 3:
                result = int(parts[0]), int(parts[1]), int(parts[2])
                # Update both caches
                with _rate_limit_cache_lock:
                    _rate_limit_cache = (*result, time.time())
                _write_rate_limit_file_cache(*result)  # Issue #1291
                return result
        except (ValueError, IndexError):
            # Invalid format, return zeros to indicate failure
            pass
    return 0, 0, 0


def get_rate_limit_reset_time() -> tuple[int, str]:
    """Get the time until rate limit resets.

    Issue #1096: Show user when they can retry.

    Returns:
        Tuple of (seconds_until_reset, human_readable_time).
    """
    _, _, reset_timestamp = check_rate_limit()
    return format_reset_time(reset_timestamp)


def get_adjusted_interval(base_interval: int, remaining: int) -> int:
    """Get adjusted polling interval based on rate limit (Issue #896).

    Args:
        base_interval: The base polling interval in seconds.
        remaining: The remaining API calls.

    Returns:
        Adjusted interval in seconds.
    """
    if remaining < RATE_LIMIT_CRITICAL_THRESHOLD:
        return base_interval * 6  # 3 minutes if critical
    elif remaining < RATE_LIMIT_WARNING_THRESHOLD:
        return base_interval * 4  # 2 minutes if low
    elif remaining < RATE_LIMIT_ADJUST_THRESHOLD:
        return base_interval * 2  # 1 minute if moderately low
    return base_interval


def should_prefer_rest_api(
    log_transition: bool = True,
    log_event_fn: Callable[[RateLimitEventType, int, int, int, dict[str, Any] | None], None]
    | None = None,
) -> bool:
    """Check if we should proactively use REST API instead of GraphQL.

    Issue #1360: Proactive REST API fallback to avoid hitting rate limit.

    When remaining GraphQL requests fall below RATE_LIMIT_REST_PRIORITY_THRESHOLD,
    this function returns True, indicating that REST API should be preferred
    over GraphQL for operations that support both.

    This is more proactive than waiting for rate limit errors to trigger fallback.

    Note: REST API does not provide isResolved status for review threads.
    Callers that need accurate resolution status should use GraphQL directly.

    Thread-safety: Uses _rest_priority_mode_lock to protect global state.

    Args:
        log_transition: If True, log when entering/exiting REST priority mode.
        log_event_fn: Optional callback function to log rate limit events.
            Signature: (event_type, remaining, limit, reset_ts, details) -> None

    Returns:
        True if REST API should be preferred, False otherwise.
    """
    global _rest_priority_mode_active

    remaining, limit, reset_ts = check_rate_limit()

    # API check failed - don't switch modes
    if limit == 0:
        return False

    should_prefer_rest = remaining < RATE_LIMIT_REST_PRIORITY_THRESHOLD

    # Log state transitions (thread-safe)
    if log_transition:
        with _rest_priority_mode_lock:
            if should_prefer_rest and not _rest_priority_mode_active:
                # Entering REST priority mode
                _rest_priority_mode_active = True
                print(
                    f"⚡ REST優先モードに切り替え (残り: {remaining}/{limit})",
                    file=sys.stderr,
                )
                if log_event_fn:
                    log_event_fn(
                        RateLimitEventType.REST_PRIORITY_ENTERED,
                        remaining,
                        limit,
                        reset_ts,
                        {"threshold": RATE_LIMIT_REST_PRIORITY_THRESHOLD},
                    )
            elif not should_prefer_rest and _rest_priority_mode_active:
                # Exiting REST priority mode
                _rest_priority_mode_active = False
                print(
                    f"✓ GraphQLモードに復帰 (残り: {remaining}/{limit})",
                    file=sys.stderr,
                )
                if log_event_fn:
                    log_event_fn(
                        RateLimitEventType.REST_PRIORITY_EXITED,
                        remaining,
                        limit,
                        reset_ts,
                        {"threshold": RATE_LIMIT_REST_PRIORITY_THRESHOLD},
                    )

    return should_prefer_rest


def print_rate_limit_warning(
    log_event_fn: Callable[[RateLimitEventType, int, int, int, dict[str, Any] | None], None]
    | None = None,
) -> None:
    """Print a warning message when rate limited.

    Issue #1096: Common function for rate limit warning message.
    Issue #1244: Also logs to hook-execution.log for post-session analysis.

    Note: Uses a single check_rate_limit() call to avoid redundant API requests.

    Args:
        log_event_fn: Optional callback function to log rate limit events.
            Signature: (event_type, remaining, limit, reset_ts, details) -> None
    """
    remaining, limit, reset_timestamp = check_rate_limit()
    _, human_time = format_reset_time(reset_timestamp)
    print(
        f"⚠️ GraphQL APIレート制限に達しました。リセットまで: {human_time}",
        file=sys.stderr,
    )
    # Log to hook-execution.log for post-session analysis (Issue #1244)
    if log_event_fn:
        log_event_fn(RateLimitEventType.LIMIT_REACHED, remaining, limit, reset_timestamp, None)


def log_rate_limit_warning_to_console(
    remaining: int,
    limit: int,
    reset_timestamp: int,
    json_mode: bool = False,
    log_fn: Callable[[str, bool, dict[str, Any] | None], None] | None = None,
    log_event_fn: Callable[[RateLimitEventType, int, int, int, dict[str, Any] | None], None]
    | None = None,
) -> None:
    """Log a warning about rate limit status (Issue #896).

    Issue #1244: Also logs to hook-execution.log for post-session analysis.

    Args:
        remaining: Remaining API calls.
        limit: Total API call limit.
        reset_timestamp: Unix timestamp when the limit resets.
        json_mode: If True, output structured JSON instead of plain text.
        log_fn: Optional logging function for JSON output.
            Signature: (message, json_mode, data) -> None
        log_event_fn: Optional callback function to log rate limit events.
            Signature: (event_type, remaining, limit, reset_ts, details) -> None
    """
    # Intentionally using local time for user-facing display
    # Users expect to see when the limit resets in their local timezone
    reset_time = datetime.fromtimestamp(reset_timestamp).strftime("%H:%M:%S")
    percentage = (remaining / limit * 100) if limit > 0 else 0

    if remaining < RATE_LIMIT_CRITICAL_THRESHOLD:
        message = f"[CRITICAL] API rate limit very low: {remaining}/{limit} ({percentage:.1f}%), resets at {reset_time}"
        data = {
            "remaining": remaining,
            "limit": limit,
            "reset_time": reset_time,
            "level": "critical",
        }
        if json_mode and log_fn:
            log_fn(message, json_mode, data)
        else:
            print(f"⚠️  {message}")
            print("   Consider pausing operations.")
        # Log to hook-execution.log for post-session analysis (Issue #1244)
        if log_event_fn:
            log_event_fn(
                RateLimitEventType.WARNING, remaining, limit, reset_timestamp, {"level": "critical"}
            )
    elif remaining < RATE_LIMIT_WARNING_THRESHOLD:
        message = (
            f"API rate limit low: {remaining}/{limit} ({percentage:.1f}%), resets at {reset_time}"
        )
        data = {
            "remaining": remaining,
            "limit": limit,
            "reset_time": reset_time,
            "level": "warning",
        }
        if json_mode and log_fn:
            log_fn(message, json_mode, data)
        else:
            print(f"⚠️  {message}")
        # Log to hook-execution.log for post-session analysis (Issue #1244)
        if log_event_fn:
            log_event_fn(
                RateLimitEventType.WARNING, remaining, limit, reset_timestamp, {"level": "warning"}
            )


def _log_rate_limit_to_api_operations(
    event_type: RateLimitEventType,
    details: dict[str, Any],
    reset_timestamp: int,
) -> None:
    """Log rate limit event to api-operations.jsonl.

    Issue #1292: Records rate limit events in api-operations.jsonl for
    analysis with analyze-api-operations.py.

    Args:
        event_type: Type of event (RateLimitEventType enum).
        details: Event details including remaining, limit, etc.
        reset_timestamp: Unix timestamp when the limit resets.
    """
    try:
        from ci_monitor.session import get_session_id

        session_id = get_session_id()
        if not session_id:
            return  # No session ID, skip logging

        # Format reset_at as ISO8601 with timezone
        reset_at = datetime.fromtimestamp(reset_timestamp, tz=UTC).isoformat()

        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": session_id,
            "type": "rate_limit",
            # Use .value to get the lowercase string (e.g., "limit_reached")
            # instead of str() which returns "RateLimitEventType.LIMIT_REACHED"
            "operation": event_type.value if hasattr(event_type, "value") else str(event_type),
            # Mark as success so analyze-api-operations.py doesn't count as failure
            "success": True,
            "details": {
                "remaining": details.get("remaining", 0),
                "limit": details.get("limit", 0),
                "reset_timestamp": reset_timestamp,
                "reset_at": reset_at,
                "usage_percent": details.get("usage_percent", 0),
            },
        }

        # Add additional details if present
        for key in [
            "direction",
            "old_interval",
            "new_interval",
            "base_interval",
            "previous_interval",
            "level",
        ]:
            if key in details:
                log_entry["details"][key] = (
                    str(details[key]) if isinstance(details[key], Enum) else details[key]
                )

        # Issue #2189: Write to session-specific file instead of global file
        log_to_session_file(EXECUTION_LOG_DIR, "api-operations", session_id, log_entry)
    except Exception:
        pass  # Ignore all errors - this is non-critical logging


def log_rate_limit_event(
    event_type: RateLimitEventType,
    remaining: int,
    limit: int,
    reset_timestamp: int,
    details: dict[str, Any] | None = None,
) -> None:
    """Log rate limit event to hook-execution.log and api-operations.jsonl.

    Issue #1244: Rate limit events are logged to the centralized log file
    so they can be analyzed after the session ends.

    Issue #1292: Also logs to api-operations.jsonl for API operation analysis.

    Issue #1385: Added "recovered" event type and "direction" field for
    adjusted_interval events to track when rate limit recovers.

    Issue #1427: Changed event_type from str to RateLimitEventType enum.

    Args:
        event_type: Type of event (RateLimitEventType enum).
        remaining: Remaining API calls.
        limit: Total API call limit.
        reset_timestamp: Unix timestamp when the limit resets.
        details: Additional details to include in the log entry.
            For ADJUSTED_INTERVAL: includes "direction" (IntervalDirection enum).
            For RECOVERED: includes "base_interval" and "previous_interval".
    """
    event_details: dict[str, Any] = {
        "remaining": remaining,
        "limit": limit,
        "reset_timestamp": reset_timestamp,
        "usage_percent": round((1 - remaining / limit) * 100, 1) if limit > 0 else 0,
    }
    if details:
        event_details.update(details)

    # Log to hook-execution.log (Issue #1244)
    log_hook_execution(
        hook_name="ci-monitor",
        decision=event_type,
        reason=f"Rate limit: {remaining}/{limit}",
        details=event_details,
    )

    # Issue #1292: Also log to api-operations.jsonl
    _log_rate_limit_to_api_operations(event_type, event_details, reset_timestamp)
