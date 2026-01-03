"""Tests for ci_monitor.events module."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

from ci_monitor.events import create_event, emit_event, log
from ci_monitor.models import EventType, MonitorEvent


class TestEmitEvent:
    """Tests for emit_event function."""

    def test_emits_json_to_stdout(self, capsys):
        """Test that event is emitted as JSON to stdout."""
        event = MonitorEvent(
            event_type=EventType.CI_PASSED,
            pr_number="123",
            timestamp="2024-01-01T12:00:00",
            message="CI passed",
            details={},
            suggested_action="",
        )

        emit_event(event)

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert output["event"] == "CI_PASSED"
        assert output["pr_number"] == "123"
        assert output["message"] == "CI passed"


class TestCreateEvent:
    """Tests for create_event function."""

    def test_creates_event_with_timestamp(self):
        """Test that event is created with current timestamp."""
        event = create_event(
            event_type=EventType.CI_PASSED,
            pr_number="123",
            message="Test message",
        )

        assert event.event_type == EventType.CI_PASSED
        assert event.pr_number == "123"
        assert event.message == "Test message"
        # Timestamp should be ISO format
        datetime.fromisoformat(event.timestamp)  # Should not raise

    def test_creates_event_with_details(self):
        """Test that event includes details."""
        details = {"key": "value", "count": 42}
        event = create_event(
            event_type=EventType.CI_FAILED,
            pr_number="456",
            message="Status check",
            details=details,
        )

        assert event.details == details

    def test_creates_event_with_suggested_action(self):
        """Test that event includes suggested action."""
        event = create_event(
            event_type=EventType.ERROR,
            pr_number="789",
            message="Merge blocked",
            suggested_action="Resolve conflicts",
        )

        assert event.suggested_action == "Resolve conflicts"

    def test_calls_background_logger(self):
        """Test that background logger is called when provided."""
        mock_logger = MagicMock()
        create_event(
            event_type=EventType.CI_PASSED,
            pr_number="123",
            message="Test",
            log_background_fn=mock_logger,
        )

        mock_logger.assert_called_once()
        call_args = mock_logger.call_args[0]
        assert call_args[0] == "ci-monitor"
        assert call_args[1] == "CI_PASSED"
        assert call_args[2]["pr_number"] == "123"

    def test_handles_background_logger_error(self, capsys):
        """Test that background logger errors don't interrupt event creation."""
        mock_logger = MagicMock(side_effect=Exception("Logger failed"))
        event = create_event(
            event_type=EventType.CI_PASSED,
            pr_number="123",
            message="Test",
            log_background_fn=mock_logger,
        )

        # Event should still be created
        assert event.pr_number == "123"
        # Warning should be printed
        captured = capsys.readouterr()
        assert "Warning" in captured.err


class TestLog:
    """Tests for log function."""

    def test_prints_message_with_timestamp(self, capsys):
        """Test that message is printed with timestamp prefix."""
        with patch("ci_monitor.events.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "12:34:56"
            log("Test message")

        captured = capsys.readouterr()
        assert "[12:34:56] Test message" in captured.out

    def test_json_mode_outputs_to_stderr(self, capsys):
        """Test that JSON mode outputs to stderr."""
        with patch("ci_monitor.events.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "12:34:56"
            log("Test message", json_mode=True)

        captured = capsys.readouterr()
        assert captured.out == ""  # Nothing to stdout
        output = json.loads(captured.err.strip())
        assert output["timestamp"] == "12:34:56"
        assert output["message"] == "Test message"
        assert output["type"] == "log"

    def test_json_mode_includes_data(self, capsys):
        """Test that JSON mode includes additional data."""
        with patch("ci_monitor.events.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "12:34:56"
            log("Test message", json_mode=True, data={"key": "value"})

        captured = capsys.readouterr()
        output = json.loads(captured.err.strip())
        assert output["key"] == "value"
