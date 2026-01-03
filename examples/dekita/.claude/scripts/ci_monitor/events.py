"""Event emission and logging for ci-monitor.

This module handles MonitorEvent creation, emission, and general logging.
Extracted from ci-monitor.py as part of Issue #1754 refactoring.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ci_monitor.models import EventType, MonitorEvent

if TYPE_CHECKING:
    from collections.abc import Callable


def emit_event(event: MonitorEvent) -> None:
    """Emit an event to stdout in JSON format.

    Args:
        event: The MonitorEvent to emit.
    """
    print(event.to_json(), flush=True)


def create_event(
    event_type: EventType,
    pr_number: str,
    message: str,
    details: dict[str, Any] | None = None,
    suggested_action: str = "",
    log_background_fn: Callable[[str, str, dict[str, Any]], None] | None = None,
) -> MonitorEvent:
    """Create a MonitorEvent with current timestamp.

    Issue #1663: Also logs the event to background task logger for persistence.

    Args:
        event_type: The type of event (from EventType enum).
        pr_number: The PR number this event relates to.
        message: Human-readable message describing the event.
        details: Optional dictionary with additional event details.
        suggested_action: Optional suggested action for the user.
        log_background_fn: Optional callback to log to background task logger.
            Signature: (task_name, event_type, details) -> None

    Returns:
        A new MonitorEvent instance with current timestamp.
    """
    event = MonitorEvent(
        event_type=event_type,
        pr_number=pr_number,
        timestamp=datetime.now(UTC).isoformat(),
        message=message,
        details=details or {},
        suggested_action=suggested_action,
    )

    # Issue #1663: Log to background task logger for persistence
    if log_background_fn:
        try:
            log_background_fn(
                "ci-monitor",
                event_type.value,
                {
                    "pr_number": pr_number,
                    "message": message,
                    **(details or {}),
                },
            )
        except Exception as e:  # noqa: BLE001 - Don't interrupt monitoring
            # Log warning for debugging - don't interrupt monitoring
            print(f"Warning: Failed to log background event: {e}", file=sys.stderr)

    return event


def log(message: str, json_mode: bool = False, data: dict[str, Any] | None = None) -> None:
    """Print a log message.

    Args:
        message: The message to log.
        json_mode: If True, output as JSON to stderr. If False, output to stdout.
        data: Optional additional data to include in JSON output.
    """
    # Intentionally using local time for user-facing console output.
    # Users expect to see timestamps in their local timezone (e.g., "[03:12:12]").
    timestamp = datetime.now().strftime("%H:%M:%S")
    if json_mode:
        log_data: dict[str, Any] = {"timestamp": timestamp, "message": message, "type": "log"}
        if data:
            log_data.update(data)
        print(json.dumps(log_data, ensure_ascii=False), file=sys.stderr, flush=True)
    else:
        print(f"[{timestamp}] {message}", flush=True)
