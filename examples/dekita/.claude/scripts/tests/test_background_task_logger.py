#!/usr/bin/env python3
"""
Unit tests for background-task-logger.py

Tests cover:
- log_background_event function
- read_logs function with filters
- rotate_logs_if_needed function
- get_summary function
- CLI argument handling

Issue #1422: バックグラウンドタスクログの永続化
"""

import json
import sys
from datetime import timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path for importing the module
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import using importlib to handle hyphenated filename
from importlib.util import module_from_spec, spec_from_file_location

spec = spec_from_file_location(
    "background_task_logger",
    Path(__file__).parent.parent / "background-task-logger.py",
)
module = module_from_spec(spec)
spec.loader.exec_module(module)

# Import functions from the module
log_background_event = module.log_background_event
read_logs = module.read_logs
rotate_logs_if_needed = module.rotate_logs_if_needed
get_summary = module.get_summary
ensure_log_dir = module.ensure_log_dir

JST = timezone(timedelta(hours=9))


@pytest.fixture
def temp_log_dir(tmp_path):
    """Create a temporary log directory for testing."""
    log_dir = tmp_path / "logs" / "background-tasks"
    log_dir.mkdir(parents=True)

    # Patch the module's constants
    with (
        patch.object(module, "BACKGROUND_LOGS_DIR", log_dir),
        patch.object(module, "LOG_FILE", log_dir / "events.jsonl"),
    ):
        yield log_dir


class TestLogBackgroundEvent:
    """Tests for log_background_event function."""

    def test_log_creates_event_file(self, temp_log_dir):
        """Test that logging creates the event file."""
        log_background_event("test-task", "TEST_EVENT", {"key": "value"})

        log_file = temp_log_dir / "events.jsonl"
        assert log_file.exists()

        with open(log_file) as f:
            event = json.loads(f.read().strip())

        assert event["task_name"] == "test-task"
        assert event["event_type"] == "TEST_EVENT"
        assert event["details"]["key"] == "value"
        assert "timestamp" in event
        assert "session_id" in event

    def test_log_appends_multiple_events(self, temp_log_dir):
        """Test that multiple events are appended."""
        log_background_event("task1", "EVENT1")
        log_background_event("task2", "EVENT2")

        log_file = temp_log_dir / "events.jsonl"
        with open(log_file) as f:
            lines = f.readlines()

        assert len(lines) == 2
        assert json.loads(lines[0])["task_name"] == "task1"
        assert json.loads(lines[1])["task_name"] == "task2"

    def test_log_with_custom_session_id(self, temp_log_dir):
        """Test logging with custom session ID."""
        log_background_event("task", "EVENT", session_id="custom-session")

        log_file = temp_log_dir / "events.jsonl"
        with open(log_file) as f:
            event = json.loads(f.read().strip())

        assert event["session_id"] == "custom-session"


class TestReadLogs:
    """Tests for read_logs function."""

    def test_read_empty_logs(self, temp_log_dir):
        """Test reading when no logs exist."""
        events = read_logs()
        assert events == []

    def test_read_logs_returns_events(self, temp_log_dir):
        """Test reading logged events."""
        log_background_event("task", "EVENT1")
        log_background_event("task", "EVENT2")

        events = read_logs()
        assert len(events) == 2

    def test_read_logs_filter_by_session(self, temp_log_dir):
        """Test filtering by session ID."""
        log_background_event("task", "EVENT1", session_id="session-a")
        log_background_event("task", "EVENT2", session_id="session-b")

        events = read_logs(session_id="session-a")
        assert len(events) == 1
        assert events[0]["session_id"] == "session-a"

    def test_read_logs_filter_by_task(self, temp_log_dir):
        """Test filtering by task name."""
        log_background_event("ci-monitor", "EVENT1")
        log_background_event("codex-review", "EVENT2")

        events = read_logs(task_name="ci-monitor")
        assert len(events) == 1
        assert events[0]["task_name"] == "ci-monitor"

    def test_read_logs_limit(self, temp_log_dir):
        """Test limiting returned events."""
        for i in range(10):
            log_background_event("task", f"EVENT{i}")

        events = read_logs(limit=5)
        assert len(events) == 5

    def test_read_logs_sorted_by_timestamp_desc(self, temp_log_dir):
        """Test that events are sorted newest first."""
        log_background_event("task", "OLD")
        log_background_event("task", "NEW")

        events = read_logs()
        assert events[0]["event_type"] == "NEW"
        assert events[1]["event_type"] == "OLD"

    def test_read_logs_filter_by_since(self, temp_log_dir):
        """Test filtering by since datetime (Issue #1425)."""
        from datetime import datetime

        # Write events directly with specific timestamps
        log_file = temp_log_dir / "events.jsonl"
        old_event = {
            "timestamp": "2025-12-01T10:00:00+09:00",
            "session_id": "s1",
            "task_name": "task",
            "event_type": "OLD_EVENT",
            "details": {},
        }
        new_event = {
            "timestamp": "2025-12-28T10:00:00+09:00",
            "session_id": "s1",
            "task_name": "task",
            "event_type": "NEW_EVENT",
            "details": {},
        }
        with open(log_file, "w") as f:
            f.write(json.dumps(old_event) + "\n")
            f.write(json.dumps(new_event) + "\n")

        # Filter for events since 2025-12-15
        since = datetime(2025, 12, 15, tzinfo=JST)
        events = read_logs(since=since)

        assert len(events) == 1
        assert events[0]["event_type"] == "NEW_EVENT"


class TestRotateLogs:
    """Tests for rotate_logs_if_needed function."""

    def test_no_rotation_when_file_small(self, temp_log_dir):
        """Test that rotation doesn't happen for small files."""
        log_file = temp_log_dir / "events.jsonl"
        log_file.write_text('{"test": "data"}\n')

        with patch.object(module, "LOG_FILE", log_file):
            rotate_logs_if_needed()

        # File should still exist and not be rotated
        assert log_file.exists()
        assert not (temp_log_dir / "events.jsonl.1").exists()

    def test_rotation_when_file_large(self, temp_log_dir):
        """Test that rotation happens for large files."""
        log_file = temp_log_dir / "events.jsonl"

        # Create a file larger than MAX_LOG_SIZE_MB
        with patch.object(module, "MAX_LOG_SIZE_MB", 0.001):  # 1KB threshold
            log_file.write_text("x" * 2000)  # 2KB file

            with patch.object(module, "LOG_FILE", log_file):
                rotate_logs_if_needed()

        # Original should be moved to .1
        assert not log_file.exists()
        assert (temp_log_dir / "events.jsonl.1").exists()

    def test_rotation_deletes_oldest_file(self, temp_log_dir):
        """Test that oldest file is deleted during rotation."""
        log_file = temp_log_dir / "events.jsonl"

        # Create existing rotated files
        for i in range(1, 6):
            (temp_log_dir / f"events.jsonl.{i}").write_text(f"old-{i}")

        with (
            patch.object(module, "MAX_LOG_SIZE_MB", 0.001),
            patch.object(module, "MAX_LOG_FILES", 5),
            patch.object(module, "LOG_FILE", log_file),
        ):
            log_file.write_text("x" * 2000)
            rotate_logs_if_needed()

        # .5 should be deleted, .4 -> .5, etc.
        assert (temp_log_dir / "events.jsonl.5").read_text() == "old-4"
        assert not (temp_log_dir / "events.jsonl.6").exists()


class TestGetSummary:
    """Tests for get_summary function."""

    def test_summary_empty_logs(self, temp_log_dir):
        """Test summary with no logs."""
        summary = get_summary()
        assert summary["total"] == 0
        assert summary["sessions"] == 0
        assert summary["by_task"] == {}
        assert summary["by_event_type"] == {}

    def test_summary_with_events(self, temp_log_dir):
        """Test summary with logged events."""
        log_background_event("ci-monitor", "CI_PASSED", session_id="s1")
        log_background_event("ci-monitor", "MERGE_COMPLETE", session_id="s1")
        log_background_event("codex-review", "REVIEW_STARTED", session_id="s2")

        summary = get_summary()
        assert summary["total"] == 3
        assert summary["sessions"] == 2
        assert summary["by_task"]["ci-monitor"] == 2
        assert summary["by_task"]["codex-review"] == 1
        assert summary["by_event_type"]["CI_PASSED"] == 1


class TestCLI:
    """Tests for command-line interface."""

    def test_no_args_shows_help(self, temp_log_dir, capsys):
        """Test that no arguments shows help."""
        with pytest.raises(SystemExit) as exc_info:
            with patch.object(sys, "argv", ["background-task-logger.py"]):
                module.main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "usage:" in captured.out.lower()

    def test_list_flag_shows_logs(self, temp_log_dir, capsys):
        """Test --list flag shows logs."""
        log_background_event("task", "EVENT")

        with patch.object(sys, "argv", ["background-task-logger.py", "--list"]):
            module.main()

        captured = capsys.readouterr()
        assert "task" in captured.out
        assert "EVENT" in captured.out

    def test_summary_flag_shows_summary(self, temp_log_dir, capsys):
        """Test --summary flag shows summary."""
        log_background_event("task", "EVENT")

        with patch.object(sys, "argv", ["background-task-logger.py", "--summary"]):
            module.main()

        captured = capsys.readouterr()
        assert "総イベント数" in captured.out

    def test_json_output(self, temp_log_dir, capsys):
        """Test --json flag outputs JSON."""
        log_background_event("task", "EVENT")

        with patch.object(sys, "argv", ["background-task-logger.py", "--list", "--json"]):
            module.main()

        captured = capsys.readouterr()
        events = json.loads(captured.out)
        assert isinstance(events, list)
        assert len(events) == 1

    def test_filter_flags_work(self, temp_log_dir, capsys):
        """Test --session and --task filter flags."""
        log_background_event("ci-monitor", "EVENT1", session_id="s1")
        log_background_event("codex-review", "EVENT2", session_id="s2")

        with patch.object(sys, "argv", ["background-task-logger.py", "--task", "ci-monitor"]):
            module.main()

        captured = capsys.readouterr()
        assert "ci-monitor" in captured.out
        assert "codex-review" not in captured.out

    def test_since_flag_filters_events(self, temp_log_dir, capsys):
        """Test --since flag filters events by date (Issue #1425)."""
        # Write events with specific timestamps
        log_file = temp_log_dir / "events.jsonl"
        old_event = {
            "timestamp": "2025-12-01T10:00:00+09:00",
            "session_id": "s1",
            "task_name": "task",
            "event_type": "OLD_EVENT",
            "details": {},
        }
        new_event = {
            "timestamp": "2025-12-28T10:00:00+09:00",
            "session_id": "s1",
            "task_name": "task",
            "event_type": "NEW_EVENT",
            "details": {},
        }
        with open(log_file, "w") as f:
            f.write(json.dumps(old_event) + "\n")
            f.write(json.dumps(new_event) + "\n")

        with patch.object(sys, "argv", ["background-task-logger.py", "--since", "2025-12-15"]):
            module.main()

        captured = capsys.readouterr()
        assert "NEW_EVENT" in captured.out
        assert "OLD_EVENT" not in captured.out

    def test_since_flag_invalid_date(self, temp_log_dir, capsys):
        """Test --since flag with invalid date format."""
        with pytest.raises(SystemExit) as exc_info:
            with patch.object(
                sys, "argv", ["background-task-logger.py", "--since", "invalid-date"]
            ):
                module.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "日付形式が不正です" in captured.err
