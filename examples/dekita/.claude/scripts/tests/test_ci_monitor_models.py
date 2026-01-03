#!/usr/bin/env python3
"""Unit tests for ci_monitor.models module."""

import sys
from pathlib import Path

# Add scripts directory to path so we can import ci_monitor package
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the ci_monitor package for attribute access (Issue #2624)

# Import directly from ci_monitor package (Issue #2624)
from ci_monitor import (
    CheckStatus,
    MergeState,
    PRState,
)

# Issue #2454: TestMonitorNotifyOnly removed - monitor_notify_only was removed


class TestPRState:
    """Tests for PRState dataclass."""

    def test_pr_state_defaults(self):
        """Test PRState with default values."""
        state = PRState(
            merge_state=MergeState.CLEAN,
            pending_reviewers=[],
            check_status=CheckStatus.PENDING,
        )

        assert state.check_details == []
        assert state.review_comments == []

    def test_pr_state_with_details(self):
        """Test PRState with all fields populated."""
        state = PRState(
            merge_state=MergeState.BEHIND,
            pending_reviewers=["Copilot", "user1"],
            check_status=CheckStatus.FAILURE,
            check_details=[{"name": "build", "state": "FAILURE"}],
            review_comments=[{"body": "Fix this"}],
        )

        assert state.merge_state == MergeState.BEHIND
        assert len(state.pending_reviewers) == 2
        assert state.check_status == CheckStatus.FAILURE
        assert len(state.check_details) == 1
        assert len(state.review_comments) == 1


class TestMergeState:
    """Tests for MergeState enum."""

    def test_merge_state_values(self):
        """Test that all expected merge states exist."""
        assert MergeState.CLEAN.value == "CLEAN"
        assert MergeState.BEHIND.value == "BEHIND"
        assert MergeState.DIRTY.value == "DIRTY"
        assert MergeState.BLOCKED.value == "BLOCKED"
        assert MergeState.UNKNOWN.value == "UNKNOWN"


class TestCheckStatus:
    """Tests for CheckStatus enum."""

    def test_check_status_values(self):
        """Test that all expected check statuses exist."""
        assert CheckStatus.PENDING.value == "pending"
        assert CheckStatus.SUCCESS.value == "success"
        assert CheckStatus.FAILURE.value == "failure"
        assert CheckStatus.CANCELLED.value == "cancelled"
