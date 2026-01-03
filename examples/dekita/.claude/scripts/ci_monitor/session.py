"""Session management for ci-monitor.

This module handles session ID tracking for proper log identification.
Extracted from ci-monitor.py as part of Issue #2624 refactoring.
"""

from __future__ import annotations

# Module-level session ID storage
_session_id: str | None = None


def set_session_id(session_id: str | None) -> None:
    """Set the session ID for logging.

    This should be called once at startup with the session ID from --session-id argument.

    Args:
        session_id: Session ID string (UUID format) or None.
    """
    global _session_id
    _session_id = session_id


def get_session_id() -> str | None:
    """Get the current session ID.

    Returns:
        Session ID string or None if not set.
    """
    return _session_id
