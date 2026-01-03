#!/usr/bin/env python3
"""Tests for lib/logging.py log level separation and error context management.

Issue #1367: Structured log visualization improvement.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Add parent directories to path for module imports
hooks_dir = str(Path(__file__).parent.parent)
if hooks_dir not in sys.path:
    sys.path.insert(0, hooks_dir)

from lib.logging import (
    LOG_LEVEL_DEBUG,
    LOG_LEVEL_ERROR,
    LOG_LEVEL_INFO,
    LOG_LEVEL_WARN,
    ErrorContextManager,
    cleanup_old_context_files,
    get_log_level,
    log_to_level_file,
)


class TestGetLogLevel:
    """Tests for get_log_level function."""

    def test_block_is_error(self):
        """Block decision should map to ERROR level."""
        assert get_log_level("block") == LOG_LEVEL_ERROR

    def test_error_is_error(self):
        """Error decision should map to ERROR level."""
        assert get_log_level("error") == LOG_LEVEL_ERROR

    def test_warn_is_warn(self):
        """Warn decision should map to WARN level."""
        assert get_log_level("warn") == LOG_LEVEL_WARN

    def test_warning_is_warn(self):
        """Warning decision should map to WARN level."""
        assert get_log_level("warning") == LOG_LEVEL_WARN

    def test_approve_is_info(self):
        """Approve decision should map to INFO level."""
        assert get_log_level("approve") == LOG_LEVEL_INFO

    def test_skip_is_info(self):
        """Skip decision should map to INFO level."""
        assert get_log_level("skip") == LOG_LEVEL_INFO

    def test_track_is_info(self):
        """Track decision should map to INFO level."""
        assert get_log_level("track") == LOG_LEVEL_INFO

    def test_success_is_info(self):
        """Success decision should map to INFO level."""
        assert get_log_level("success") == LOG_LEVEL_INFO

    def test_monitor_start_is_debug(self):
        """Monitor start decision should map to DEBUG level."""
        assert get_log_level("monitor_start") == LOG_LEVEL_DEBUG

    def test_monitor_complete_is_debug(self):
        """Monitor complete decision should map to DEBUG level."""
        assert get_log_level("monitor_complete") == LOG_LEVEL_DEBUG

    def test_info_is_debug(self):
        """Info decision should map to DEBUG level."""
        assert get_log_level("info") == LOG_LEVEL_DEBUG

    def test_rebase_is_debug(self):
        """Rebase decision should map to DEBUG level."""
        assert get_log_level("rebase") == LOG_LEVEL_DEBUG

    def test_unknown_is_info(self):
        """Unknown decision should default to INFO level."""
        assert get_log_level("unknown") == LOG_LEVEL_INFO
        assert get_log_level("custom") == LOG_LEVEL_INFO


class TestLogToLevelFile:
    """Tests for log_to_level_file function."""

    def test_writes_error_to_error_log(self):
        """Should write ERROR level entries to hook-errors.log."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            entry = {"hook": "test-hook", "decision": "block", "reason": "test"}

            log_to_level_file(log_dir, entry, LOG_LEVEL_ERROR)

            error_log = log_dir / "hook-errors.log"
            assert error_log.exists()

            with open(error_log) as f:
                content = f.read()
            logged_entry = json.loads(content.strip())
            assert logged_entry["hook"] == "test-hook"
            assert logged_entry["decision"] == "block"

    def test_writes_warn_to_warnings_log(self):
        """Should write WARN level entries to hook-warnings.log."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            entry = {"hook": "test-hook", "decision": "warn", "reason": "test"}

            log_to_level_file(log_dir, entry, LOG_LEVEL_WARN)

            warn_log = log_dir / "hook-warnings.log"
            assert warn_log.exists()

            with open(warn_log) as f:
                content = f.read()
            logged_entry = json.loads(content.strip())
            assert logged_entry["hook"] == "test-hook"
            assert logged_entry["decision"] == "warn"

    def test_debug_not_written_without_env(self):
        """Should not write DEBUG level without HOOK_DEBUG_LOG=1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            entry = {"hook": "test-hook", "decision": "info"}

            # Ensure env var is not set
            os.environ.pop("HOOK_DEBUG_LOG", None)

            log_to_level_file(log_dir, entry, LOG_LEVEL_DEBUG)

            debug_log = log_dir / "hook-debug.log"
            assert not debug_log.exists()

    def test_debug_written_with_env(self):
        """Should write DEBUG level when HOOK_DEBUG_LOG=1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            entry = {"hook": "test-hook", "decision": "info"}

            # Set env var
            os.environ["HOOK_DEBUG_LOG"] = "1"
            try:
                log_to_level_file(log_dir, entry, LOG_LEVEL_DEBUG)

                debug_log = log_dir / "hook-debug.log"
                assert debug_log.exists()
            finally:
                os.environ.pop("HOOK_DEBUG_LOG", None)

    def test_creates_directory_if_needed(self):
        """Should create log directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "nested" / "logs"
            entry = {"hook": "test-hook", "decision": "block"}

            log_to_level_file(log_dir, entry, LOG_LEVEL_ERROR)

            assert log_dir.exists()
            assert (log_dir / "hook-errors.log").exists()


class TestErrorContextManager:
    """Tests for ErrorContextManager class."""

    def test_add_entry_creates_buffer(self):
        """Should create buffer for new session."""
        manager = ErrorContextManager()
        entry = {"hook": "test", "decision": "approve"}

        manager.add_entry("session-1", entry)

        assert "session-1" in manager._buffers
        assert len(manager._buffers["session-1"]) == 1

    def test_add_entry_respects_buffer_size(self):
        """Should respect buffer size limit."""
        manager = ErrorContextManager()

        # Add more than buffer size
        for i in range(15):
            manager.add_entry("session-1", {"hook": f"test-{i}"})

        # Should only keep last 10 (ERROR_CONTEXT_BUFFER_SIZE)
        assert len(manager._buffers["session-1"]) == 10

    def test_add_entry_ignores_empty_session(self):
        """Should ignore entries with empty session ID."""
        manager = ErrorContextManager()
        manager.add_entry("", {"hook": "test"})
        manager.add_entry(None, {"hook": "test"})

        assert "" not in manager._buffers
        assert None not in manager._buffers

    def test_on_error_starts_pending_capture(self):
        """Should start pending capture on error.

        Note: In actual usage, add_entry is called before on_error for error entries.
        on_error should exclude the error entry from before_entries to avoid duplication.
        """
        manager = ErrorContextManager()

        # Add some context (before error)
        for i in range(4):
            manager.add_entry("session-1", {"hook": f"before-{i}"})

        # Add error entry (simulating log_hook_execution calling add_entry first)
        error_entry = {"hook": "error-hook", "decision": "block"}
        manager.add_entry("session-1", error_entry)

        # Trigger error - should capture before_entries excluding the error entry
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            manager.on_error("session-1", error_entry, log_dir)

            assert "session-1" in manager._pending_captures
            # before_entries should have 4 entries (excluding the error entry)
            assert len(manager._pending_captures["session-1"]["before_entries"]) == 4

    def test_save_context_creates_file(self):
        """Should create context file with correct structure."""
        manager = ErrorContextManager()

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            before = [{"hook": "before-1"}, {"hook": "before-2"}]
            error = {"hook": "error", "decision": "block", "timestamp": "2025-01-01T00:00:00+00:00"}
            after = [{"hook": "after-1"}]

            result = manager.save_context(log_dir, "session-1", error, before, after)

            assert result is not None
            assert result.exists()

            # Verify file content
            with open(result) as f:
                lines = f.readlines()

            assert len(lines) == 4  # metadata, before, error, after

            metadata = json.loads(lines[0])
            assert metadata["type"] == "metadata"
            assert metadata["session_id"] == "session-1"

            before_data = json.loads(lines[1])
            assert before_data["type"] == "context_before"
            assert len(before_data["entries"]) == 2

            error_data = json.loads(lines[2])
            assert error_data["type"] == "error"
            assert error_data["entry"]["hook"] == "error"

            after_data = json.loads(lines[3])
            assert after_data["type"] == "context_after"
            assert len(after_data["entries"]) == 1

    def test_flush_pending_saves_partial_context(self):
        """Should save partial context when session ends."""
        manager = ErrorContextManager()

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)

            # Add context and trigger error
            for i in range(3):
                manager.add_entry("session-1", {"hook": f"before-{i}"})

            error_entry = {
                "hook": "error",
                "decision": "block",
                "timestamp": "2025-01-01T00:00:00+00:00",
            }
            manager.on_error("session-1", error_entry, log_dir)

            # Add only 2 after entries (less than ERROR_CONTEXT_AFTER_SIZE)
            manager.add_entry("session-1", {"hook": "after-1"})
            manager.add_entry("session-1", {"hook": "after-2"})

            # Flush should save with partial after context
            result = manager.flush_pending("session-1")

            assert result is not None
            assert result.exists()

    def test_clear_session_removes_buffers(self):
        """Should clear buffer and pending captures for session."""
        manager = ErrorContextManager()

        manager.add_entry("session-1", {"hook": "test"})
        manager._pending_captures["session-1"] = {"test": "data"}

        manager.clear_session("session-1")

        assert "session-1" not in manager._buffers
        assert "session-1" not in manager._pending_captures


class TestCleanupOldContextFiles:
    """Tests for cleanup_old_context_files function."""

    def test_deletes_old_files(self):
        """Should delete context files older than retention period."""
        import time

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            context_dir = log_dir / "error-context"
            context_dir.mkdir()

            # Create an old file
            old_file = context_dir / "error-context-old.jsonl"
            old_file.touch()
            # Set modification time to 10 days ago
            old_time = time.time() - (10 * 24 * 60 * 60)
            os.utime(old_file, (old_time, old_time))

            # Create a new file
            new_file = context_dir / "error-context-new.jsonl"
            new_file.touch()

            deleted = cleanup_old_context_files(log_dir, max_age_days=7)

            assert deleted == 1
            assert not old_file.exists()
            assert new_file.exists()

    def test_handles_missing_directory(self):
        """Should handle missing error-context directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            # Don't create error-context directory

            deleted = cleanup_old_context_files(log_dir)

            assert deleted == 0
