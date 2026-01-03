#!/usr/bin/env python3
"""Unit tests for ci_monitor.events module."""

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

# Add scripts directory to path so we can import ci_monitor package
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the ci_monitor package for attribute access (Issue #2624)
import ci_monitor

# Import directly from ci_monitor package (Issue #2624)
from ci_monitor import (
    CheckStatus,
    EventType,
    MergeState,
    MultiPREvent,
    PRState,
    create_event,
)


class TestCreateEvent:
    """Tests for create_event function."""

    def test_create_event_basic(self):
        """Test basic event creation with required fields."""
        event = create_event(
            EventType.CI_PASSED,
            "123",
            "All checks passed",
        )

        assert event.event_type == EventType.CI_PASSED
        assert event.pr_number == "123"
        assert event.message == "All checks passed"
        assert event.details == {}
        assert event.suggested_action == ""
        # Timestamp should be a valid ISO format
        datetime.fromisoformat(event.timestamp)

    def test_create_event_with_details(self):
        """Test event creation with details and suggested action."""
        details = {"failed_checks": ["lint", "test"]}
        event = create_event(
            EventType.CI_FAILED,
            "456",
            "CI failed",
            details=details,
            suggested_action="Fix failing tests",
        )

        assert event.event_type == EventType.CI_FAILED
        assert event.pr_number == "456"
        assert event.details == details
        assert event.suggested_action == "Fix failing tests"

    def test_event_to_dict(self):
        """Test MonitorEvent.to_dict() method."""
        event = create_event(
            EventType.BEHIND_DETECTED,
            "789",
            "Branch is behind",
            details={"merge_state": "BEHIND"},
            suggested_action="gh pr update-branch 789 --rebase",
        )

        result = event.to_dict()

        assert result["event"] == "BEHIND_DETECTED"
        assert result["pr_number"] == "789"
        assert result["message"] == "Branch is behind"
        assert result["details"] == {"merge_state": "BEHIND"}
        assert result["suggested_action"] == "gh pr update-branch 789 --rebase"
        assert "timestamp" in result

    def test_event_to_json(self):
        """Test MonitorEvent.to_json() method."""
        event = create_event(
            EventType.CI_PASSED,
            "123",
            "All checks passed",
        )

        json_str = event.to_json()
        parsed = json.loads(json_str)

        assert parsed["event"] == "CI_PASSED"
        assert parsed["pr_number"] == "123"

    def test_create_event_logs_to_background_logger(self, monkeypatch):
        """Test that create_event calls log_background_event.

        Issue #1673: Ensure log_background_event is called with correct arguments.
        """
        mock = MagicMock()
        monkeypatch.setattr(ci_monitor, "log_background_event", mock)

        event = create_event(
            event_type=EventType.CI_PASSED,
            pr_number="123",
            message="Test message",
            details={"extra": "info"},
        )

        mock.assert_called_once_with(
            task_name="ci-monitor",
            event_type="CI_PASSED",
            details={
                "pr_number": "123",
                "message": "Test message",
                "extra": "info",
            },
        )
        assert event.event_type == EventType.CI_PASSED

    def test_create_event_continues_on_logging_failure(self, monkeypatch, capsys):
        """Test that create_event continues even if logging fails.

        Issue #1673: Exception in log_background_event should not interrupt event creation.
        """
        mock = MagicMock(side_effect=Exception("Logging failed"))
        monkeypatch.setattr(ci_monitor, "log_background_event", mock)

        event = create_event(
            event_type=EventType.CI_FAILED,
            pr_number="456",
            message="Test failure",
        )

        # Event should be created successfully despite logging failure
        assert event.event_type == EventType.CI_FAILED
        assert event.pr_number == "456"
        assert event.message == "Test failure"

        # Warning should be printed to stderr
        captured = capsys.readouterr()
        assert "Warning: Failed to log background event" in captured.err
        assert "Logging failed" in captured.err


class TestEventType:
    """Tests for EventType enum."""

    def test_event_type_values(self):
        """Test that all expected event types exist."""
        assert EventType.BEHIND_DETECTED.value == "BEHIND_DETECTED"
        assert EventType.DIRTY_DETECTED.value == "DIRTY_DETECTED"
        assert EventType.REVIEW_COMPLETED.value == "REVIEW_COMPLETED"
        assert EventType.CI_FAILED.value == "CI_FAILED"
        assert EventType.CI_PASSED.value == "CI_PASSED"
        assert EventType.TIMEOUT.value == "TIMEOUT"
        assert EventType.ERROR.value == "ERROR"


class TestEventTypeValues:
    """Test that all EventType values are defined correctly."""

    def test_review_error_event_type_exists(self):
        """Test that REVIEW_ERROR event type exists."""
        assert EventType.REVIEW_ERROR.value == "REVIEW_ERROR"


class TestMultiPREvent:
    """Tests for MultiPREvent dataclass."""

    def test_multi_pr_event_creation(self):
        """Test MultiPREvent creation with all fields."""
        event = create_event(EventType.CI_PASSED, "123", "CI passed")
        state = PRState(
            merge_state=MergeState.CLEAN,
            pending_reviewers=[],
            check_status=CheckStatus.SUCCESS,
        )
        multi_event = MultiPREvent(pr_number="123", event=event, state=state)

        assert multi_event.pr_number == "123"
        assert multi_event.event.event_type == EventType.CI_PASSED
        assert multi_event.state.check_status == CheckStatus.SUCCESS

    def test_multi_pr_event_with_none_event(self):
        """Test MultiPREvent can be created with None event."""
        multi_event = MultiPREvent(pr_number="456", event=None, state=None)

        assert multi_event.pr_number == "456"
        assert multi_event.event is None
        assert multi_event.state is None
