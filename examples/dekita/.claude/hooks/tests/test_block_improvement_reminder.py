#!/usr/bin/env python3
"""Tests for block-improvement-reminder.py."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the hook module
import importlib.util

from lib.logging import log_to_session_file

spec = importlib.util.spec_from_file_location(
    "block_improvement_reminder",
    Path(__file__).parent.parent / "block-improvement-reminder.py",
)
hook_module = importlib.util.module_from_spec(spec)


class TestGetConsecutiveBlocks:
    """Tests for get_consecutive_blocks function."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.log_dir = Path(self.temp_dir) / ".claude" / "logs" / "execution"
        self.log_dir.mkdir(parents=True)
        self.session_id = "test-session-123"

    def teardown_method(self) -> None:
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_log_entry(self, hook: str, decision: str) -> None:
        """Write a log entry to the session log."""
        entry = {
            "hook": hook,
            "decision": decision,
            "timestamp": "2026-01-02T12:00:00+09:00",
        }
        log_to_session_file(self.log_dir, "hook-execution", self.session_id, entry)

    def test_returns_empty_when_no_logs(self) -> None:
        """No logs returns empty dict."""
        # Load module with patched log dir
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": self.temp_dir}):
            spec.loader.exec_module(hook_module)
            result = hook_module.get_consecutive_blocks(self.session_id)
        assert result == {}

    def test_counts_consecutive_blocks(self) -> None:
        """Counts consecutive blocks correctly."""
        # Write 3 consecutive blocks from same hook
        self._write_log_entry("test-hook", "block")
        self._write_log_entry("test-hook", "block")
        self._write_log_entry("test-hook", "block")

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": self.temp_dir}):
            spec.loader.exec_module(hook_module)
            result = hook_module.get_consecutive_blocks(self.session_id)

        assert result.get("test-hook") == 3

    def test_resets_count_on_approve(self) -> None:
        """Approve resets the consecutive count."""
        self._write_log_entry("test-hook", "block")
        self._write_log_entry("test-hook", "block")
        self._write_log_entry("test-hook", "approve")  # Reset
        self._write_log_entry("test-hook", "block")

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": self.temp_dir}):
            spec.loader.exec_module(hook_module)
            result = hook_module.get_consecutive_blocks(self.session_id)

        # Only 1 consecutive block after the approve
        assert result.get("test-hook") == 1

    def test_tracks_multiple_hooks(self) -> None:
        """Tracks multiple hooks independently."""
        self._write_log_entry("hook-a", "block")
        self._write_log_entry("hook-a", "block")
        self._write_log_entry("hook-b", "block")
        self._write_log_entry("hook-a", "block")

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": self.temp_dir}):
            spec.loader.exec_module(hook_module)
            result = hook_module.get_consecutive_blocks(self.session_id)

        assert result.get("hook-a") == 3
        assert result.get("hook-b") == 1


class TestHasShownReminder:
    """Tests for has_shown_reminder function."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.session_dir = Path(self.temp_dir) / ".claude" / "logs" / "session"
        self.session_dir.mkdir(parents=True)
        self.session_id = "test-session-456"

    def teardown_method(self) -> None:
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_returns_false_when_no_marker(self) -> None:
        """Returns False when no marker exists."""
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": self.temp_dir}):
            spec.loader.exec_module(hook_module)
            result = hook_module.has_shown_reminder(self.session_id, "test-hook")
        assert result is False

    def test_returns_true_when_marker_exists(self) -> None:
        """Returns True when marker exists."""
        # Create marker file
        marker_file = self.session_dir / f"block-reminder-{self.session_id}-test-hook.marker"
        marker_file.write_text("1")

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": self.temp_dir}):
            spec.loader.exec_module(hook_module)
            result = hook_module.has_shown_reminder(self.session_id, "test-hook")
        assert result is True


class TestBuildReminderMessage:
    """Tests for build_reminder_message function."""

    def test_includes_hook_name(self) -> None:
        """Message includes hook name."""
        spec.loader.exec_module(hook_module)
        message = hook_module.build_reminder_message("test-hook", 3)
        assert "test-hook" in message

    def test_includes_block_count(self) -> None:
        """Message includes block count."""
        spec.loader.exec_module(hook_module)
        message = hook_module.build_reminder_message("test-hook", 5)
        assert "5回" in message

    def test_includes_improvement_suggestions(self) -> None:
        """Message includes improvement suggestions."""
        spec.loader.exec_module(hook_module)
        message = hook_module.build_reminder_message("test-hook", 3)
        assert "SKIP" in message
        assert "拒否メッセージ" in message
        assert "誤検知" in message


class TestMainFunction:
    """Tests for main function behavior."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.log_dir = Path(self.temp_dir) / ".claude" / "logs" / "execution"
        self.log_dir.mkdir(parents=True)
        self.session_dir = Path(self.temp_dir) / ".claude" / "logs" / "session"
        self.session_dir.mkdir(parents=True)

    def teardown_method(self) -> None:
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_skips_non_bash_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Skips non-Bash tools."""
        hook_input = {"tool_name": "Read", "session_id": "test-session"}

        with (
            patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": self.temp_dir}),
            patch("sys.stdin.read", return_value=json.dumps(hook_input)),
        ):
            spec.loader.exec_module(hook_module)
            hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result.get("continue") is True

    def test_skips_when_no_session_id(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Skips when no valid session ID."""
        hook_input = {"tool_name": "Bash"}  # No session_id

        with (
            patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": self.temp_dir}),
            patch("sys.stdin.read", return_value=json.dumps(hook_input)),
        ):
            spec.loader.exec_module(hook_module)
            hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result.get("continue") is True

    def test_shows_reminder_when_threshold_met(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Shows reminder when consecutive block threshold is met."""
        session_id = "test-session-789"

        # Write 3 consecutive blocks
        for _ in range(3):
            entry = {
                "hook": "test-blocker",
                "decision": "block",
                "timestamp": "2026-01-02T12:00:00+09:00",
            }
            log_to_session_file(self.log_dir, "hook-execution", session_id, entry)

        hook_input = {"tool_name": "Bash", "session_id": session_id}

        with (
            patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": self.temp_dir}),
            patch("sys.stdin.read", return_value=json.dumps(hook_input)),
        ):
            spec.loader.exec_module(hook_module)
            hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)

        # Should show reminder
        assert result.get("decision") == "approve"
        assert "test-blocker" in result.get("systemMessage", "")

    def test_does_not_repeat_reminder(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Does not show reminder again after it was shown."""
        session_id = "test-session-repeat"

        # Write 3 consecutive blocks
        for _ in range(3):
            entry = {
                "hook": "test-blocker",
                "decision": "block",
                "timestamp": "2026-01-02T12:00:00+09:00",
            }
            log_to_session_file(self.log_dir, "hook-execution", session_id, entry)

        # Create marker to indicate reminder was already shown
        marker_file = self.session_dir / f"block-reminder-{session_id}-test-blocker.marker"
        marker_file.write_text("1")

        hook_input = {"tool_name": "Bash", "session_id": session_id}

        with (
            patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": self.temp_dir}),
            patch("sys.stdin.read", return_value=json.dumps(hook_input)),
        ):
            spec.loader.exec_module(hook_module)
            hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)

        # Should not show reminder (just continue)
        assert result.get("continue") is True
