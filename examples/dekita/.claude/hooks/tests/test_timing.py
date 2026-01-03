#!/usr/bin/env python3
"""Tests for lib/timing.py module.

Issue #1882: Hook timing utilities tests.
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path for imports
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from lib.timing import HookTimer, timed_hook

# Patch target for log_hook_execution (imported inside wrapper)
MOCK_LOG_HOOK = "lib.execution.log_hook_execution"


class TestHookTimer:
    """Tests for HookTimer class."""

    def test_elapsed_ms_returns_integer(self):
        """elapsed_ms should return an integer."""
        timer = HookTimer("test-hook")
        time.sleep(0.01)  # 10ms
        elapsed = timer.elapsed_ms()
        assert isinstance(elapsed, int)
        assert elapsed >= 10

    def test_elapsed_seconds_returns_float(self):
        """elapsed_seconds should return a float."""
        timer = HookTimer("test-hook")
        time.sleep(0.01)  # 10ms
        elapsed = timer.elapsed_seconds()
        assert isinstance(elapsed, float)
        assert elapsed >= 0.01

    def test_hook_name_stored(self):
        """Hook name should be stored in the timer."""
        timer = HookTimer("my-custom-hook")
        assert timer.hook_name == "my-custom-hook"

    def test_multiple_elapsed_calls(self):
        """Multiple calls to elapsed should return increasing values."""
        timer = HookTimer("test-hook")
        elapsed1 = timer.elapsed_ms()
        time.sleep(0.005)  # 5ms
        elapsed2 = timer.elapsed_ms()
        assert elapsed2 >= elapsed1


class TestTimedHookDecorator:
    """Tests for timed_hook decorator."""

    @patch(MOCK_LOG_HOOK)
    def test_approve_decision_from_return(self, mock_log):
        """Should use return value as decision when it's a string."""

        @timed_hook("test-hook")
        def sample_hook():
            return "approve"

        result = sample_hook()
        assert result == "approve"
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][0] == "test-hook"
        assert call_args[0][1] == "approve"
        assert "duration_ms" in call_args[1]

    @patch(MOCK_LOG_HOOK)
    def test_block_decision_from_return(self, mock_log):
        """Should use 'block' as decision when returned."""

        @timed_hook("blocking-hook")
        def blocking_hook():
            return "block"

        result = blocking_hook()
        assert result == "block"
        mock_log.assert_called_once()
        assert mock_log.call_args[0][1] == "block"

    @patch(MOCK_LOG_HOOK)
    def test_default_approve_for_non_string_return(self, mock_log):
        """Should default to 'approve' when return value is not a string."""

        @timed_hook("test-hook")
        def hook_with_dict_return():
            return {"status": "ok"}

        result = hook_with_dict_return()
        assert result == {"status": "ok"}
        assert mock_log.call_args[0][1] == "approve"

    @patch(MOCK_LOG_HOOK)
    def test_system_exit_zero_is_approve(self, mock_log):
        """SystemExit with code 0 should be logged as 'approve'."""

        @timed_hook("exit-hook")
        def hook_with_exit():
            raise SystemExit(0)

        with pytest.raises(SystemExit) as exc_info:
            hook_with_exit()

        assert exc_info.value.code == 0
        assert mock_log.call_args[0][1] == "approve"

    @patch(MOCK_LOG_HOOK)
    def test_system_exit_none_is_approve(self, mock_log):
        """SystemExit with code None should be logged as 'approve'."""

        @timed_hook("exit-hook")
        def hook_with_exit_none():
            raise SystemExit(None)

        with pytest.raises(SystemExit) as exc_info:
            hook_with_exit_none()

        assert exc_info.value.code is None
        assert mock_log.call_args[0][1] == "approve"

    @patch(MOCK_LOG_HOOK)
    def test_system_exit_nonzero_is_block(self, mock_log):
        """SystemExit with non-zero code should be logged as 'block'."""

        @timed_hook("blocking-exit-hook")
        def hook_with_nonzero_exit():
            raise SystemExit(1)

        with pytest.raises(SystemExit) as exc_info:
            hook_with_nonzero_exit()

        assert exc_info.value.code == 1
        assert mock_log.call_args[0][1] == "block"

    @patch(MOCK_LOG_HOOK)
    def test_exception_logged_as_error(self, mock_log):
        """Exceptions should be logged as 'error'."""

        @timed_hook("error-hook")
        def hook_with_exception():
            raise ValueError("test error")

        with pytest.raises(ValueError):
            hook_with_exception()

        assert mock_log.call_args[0][1] == "error"
        assert mock_log.call_args[1]["details"] == {"error": "exception_raised"}

    @patch(MOCK_LOG_HOOK)
    def test_duration_is_recorded(self, mock_log):
        """Duration should be recorded in milliseconds."""

        @timed_hook("timed-hook")
        def slow_hook():
            time.sleep(0.02)  # 20ms
            return "approve"

        slow_hook()
        duration = mock_log.call_args[1]["duration_ms"]
        assert duration >= 20

    @patch(MOCK_LOG_HOOK)
    def test_preserves_function_name(self, mock_log):
        """Decorator should preserve the original function name."""

        @timed_hook("test-hook")
        def my_named_function():
            return "approve"

        assert my_named_function.__name__ == "my_named_function"

    @patch(MOCK_LOG_HOOK)
    def test_with_arguments(self, mock_log):
        """Decorator should work with functions that have arguments."""

        @timed_hook("args-hook")
        def hook_with_args(a, b, c=None):
            return f"{a}-{b}-{c}"

        result = hook_with_args("x", "y", c="z")
        assert result == "x-y-z"
        mock_log.assert_called_once()
