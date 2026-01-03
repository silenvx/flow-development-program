#!/usr/bin/env python3
"""Unit tests for ci_monitor.wait_stable module."""

import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add scripts directory to path so we can import ci_monitor package
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the ci_monitor package for attribute access (Issue #2624)
import ci_monitor

# Import directly from ci_monitor package (Issue #2624)


class TestWaitForMainStable:
    """Tests for wait_for_main_stable function (Issue #1239)."""

    def test_returns_true_when_main_already_stable(self):
        """Test returns True immediately when main hasn't been updated recently."""
        # Main was updated 10 minutes ago, stable duration is 5 minutes
        old_commit_time = int(time.time()) - 600  # 10 minutes ago

        with patch.object(ci_monitor, "get_main_last_commit_time", return_value=old_commit_time):
            result = ci_monitor.wait_for_main_stable(
                stable_duration_minutes=5,
                check_interval=1,
                timeout_minutes=1,
                json_mode=True,
            )

        assert result is True

    def test_returns_false_on_timeout(self):
        """Test returns False when timeout is reached."""
        # Main keeps being updated (always 30 seconds ago)
        recent_commit_time = int(time.time()) - 30

        with (
            patch.object(ci_monitor, "get_main_last_commit_time", return_value=recent_commit_time),
            patch.object(time, "sleep"),  # Speed up test
        ):
            result = ci_monitor.wait_for_main_stable(
                stable_duration_minutes=5,
                check_interval=1,
                timeout_minutes=0,  # Immediate timeout
                json_mode=True,
            )

        assert result is False

    def test_waits_for_stability(self):
        """Test waits until main becomes stable."""
        call_count = [0]
        base_time = int(time.time())

        def mock_commit_time():
            call_count[0] += 1
            if call_count[0] <= 2:
                # First two calls: recent commit (1 minute ago)
                return base_time - 60
            else:
                # After that: old commit (10 minutes ago)
                return base_time - 600

        with (
            patch.object(ci_monitor, "get_main_last_commit_time", side_effect=mock_commit_time),
            patch.object(time, "sleep"),  # Speed up test
        ):
            result = ci_monitor.wait_for_main_stable(
                stable_duration_minutes=5,
                check_interval=1,
                timeout_minutes=10,
                json_mode=True,
            )

        assert result is True
        assert call_count[0] >= 3  # Should have checked at least 3 times


class TestGetMainLastCommitTime:
    """Tests for get_main_last_commit_time function (Issue #1239)."""

    def test_returns_timestamp_on_success(self):
        """Test returns Unix timestamp when git commands succeed."""
        expected_time = 1703721600  # Some timestamp

        with patch("subprocess.run") as mock_run:
            # First call: git fetch
            mock_run.side_effect = [
                MagicMock(returncode=0),
                MagicMock(returncode=0, stdout=f"{expected_time}\n"),
            ]

            result = ci_monitor.get_main_last_commit_time()

        assert result == expected_time

    def test_returns_none_on_fetch_timeout(self):
        """Test returns None when git fetch times out."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("git", 30)

            result = ci_monitor.get_main_last_commit_time()

        assert result is None

    def test_returns_none_on_fetch_nonzero_returncode(self):
        """Test returns None when git fetch returns non-zero (e.g., network error)."""
        with patch("subprocess.run") as mock_run:
            # fetch fails with non-zero return code
            mock_run.return_value = MagicMock(returncode=1)

            result = ci_monitor.get_main_last_commit_time()

        assert result is None
        # Should only call fetch, not proceed to git log
        assert mock_run.call_count == 1

    def test_returns_none_on_log_failure(self):
        """Test returns None when git log fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # fetch succeeds
                MagicMock(returncode=1, stdout=""),  # log fails
            ]

            result = ci_monitor.get_main_last_commit_time()

        assert result is None
