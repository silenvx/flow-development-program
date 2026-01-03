"""Data models for ci-monitor.

This module contains all Enum and dataclass definitions used by ci-monitor.
Extracted from ci-monitor.py as part of Issue #1754 refactoring.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class EventType(Enum):
    """Types of events that can be emitted during monitoring."""

    BEHIND_DETECTED = "BEHIND_DETECTED"
    DIRTY_DETECTED = "DIRTY_DETECTED"
    REVIEW_COMPLETED = "REVIEW_COMPLETED"
    REVIEW_ERROR = "REVIEW_ERROR"
    CI_FAILED = "CI_FAILED"
    CI_PASSED = "CI_PASSED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


class CheckStatus(Enum):
    """CI check status values."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"


class MergeState(Enum):
    """PR merge state values."""

    CLEAN = "CLEAN"
    BEHIND = "BEHIND"
    DIRTY = "DIRTY"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class RetryWaitStatus(Enum):
    """Status for retry wait operations.

    Issue #1463: Enum for retry wait status (type safety improvement).
    """

    CONTINUE = "CONTINUE"
    TIMEOUT = "TIMEOUT"


class RateLimitEventType(str, Enum):
    """Types of rate limit events for logging.

    Issue #1427: Using str mixin allows direct use in f-strings and JSON.
    Issue #1360: Added REST_PRIORITY_ENTERED/EXITED for proactive fallback.
    """

    WARNING = "warning"
    LIMIT_REACHED = "limit_reached"
    ADJUSTED_INTERVAL = "adjusted_interval"
    RECOVERED = "recovered"
    REST_PRIORITY_ENTERED = "rest_priority_entered"
    REST_PRIORITY_EXITED = "rest_priority_exited"


class IntervalDirection(str, Enum):
    """Direction of polling interval adjustment.

    Issue #1427: Used with RateLimitEventType.ADJUSTED_INTERVAL.
    """

    INCREASE = "increase"
    DECREASE = "decrease"


@dataclass
class PRState:
    """State of a PR at a point in time."""

    merge_state: MergeState
    pending_reviewers: list[str]
    check_status: CheckStatus
    check_details: list[dict[str, Any]] = field(default_factory=list)
    review_comments: list[dict[str, Any]] = field(default_factory=list)
    unresolved_threads: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MonitorEvent:
    """An event emitted by the monitor."""

    event_type: EventType
    pr_number: str
    timestamp: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    suggested_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary."""
        return {
            "event": self.event_type.value,
            "pr_number": self.pr_number,
            "timestamp": self.timestamp,
            "message": self.message,
            "details": self.details,
            "suggested_action": self.suggested_action,
        }

    def to_json(self) -> str:
        """Convert event to JSON string."""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


@dataclass
class MonitorResult:
    """Result of a monitoring session."""

    success: bool
    message: str
    rebase_count: int = 0
    final_state: PRState | None = None
    review_completed: bool = False
    ci_passed: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClassifiedComments:
    """Comments classified by whether they're within PR scope."""

    in_scope: list[dict[str, Any]]
    out_of_scope: list[dict[str, Any]]


@dataclass
class RebaseResult:
    """Result of a PR rebase operation.

    Issue #1348: Enhanced logging for rebase operations.

    Attributes:
        success: Whether the overall rebase operation succeeded.
        conflict: Whether the failure was due to a merge conflict.
        error_message: Error message captured during the rebase.
    """

    success: bool
    conflict: bool = False
    error_message: str | None = None


@dataclass
class CodexReviewRequest:
    """Information about an @codex review request comment."""

    comment_id: int
    created_at: str
    has_eyes_reaction: bool


@dataclass
class MultiPREvent:
    """Event from multi-PR monitoring."""

    pr_number: str
    event: MonitorEvent | None
    state: PRState | None


def has_unresolved_threads(result: MonitorResult) -> bool:
    """Check if a monitor result has unresolved review threads."""
    return bool(result.final_state and result.final_state.unresolved_threads)
