#!/usr/bin/env python3
"""Tests for workflow_verifier.py - Workflow execution verification."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path for module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


class TestWorkflowVerifierBasic:
    """Basic tests for WorkflowVerifier class."""

    def setup_method(self):
        """Set up test fixtures."""
        # Use UUID format for session_id (Issue #2496: security validation)
        self.session_id = "00000000-0000-0000-0000-000000000001"

    def test_verifier_loads_empty_log(self):
        """Verifier should handle empty/missing log file."""
        import workflow_verifier

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=[]):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            assert len(verifier.executions) == 0

    def test_verifier_loads_log_entries(self):
        """Verifier should load log entries for current session."""
        import workflow_verifier

        mock_entries = [
            {
                "timestamp": "2025-12-21T00:00:00Z",
                "session_id": self.session_id,
                "hook": "merge-check",
                "decision": "approve",
            },
            {
                "timestamp": "2025-12-21T00:01:00Z",
                "session_id": self.session_id,
                "hook": "worktree-warning",
                "decision": "block",
            },
        ]

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            assert len(verifier.executions) == 2

    def test_get_execution_count(self):
        """get_execution_count should return correct count."""
        import workflow_verifier

        mock_entries = [
            {
                "timestamp": "2025-12-21T00:00:00Z",
                "session_id": self.session_id,
                "hook": "merge-check",
                "decision": "approve",
            },
            {
                "timestamp": "2025-12-21T00:01:00Z",
                "session_id": self.session_id,
                "hook": "merge-check",
                "decision": "block",
            },
        ]

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            assert verifier.get_execution_count("merge-check") == 2
            assert verifier.get_execution_count("unknown-hook") == 0

    def test_get_decision_summary(self):
        """get_decision_summary should return approve/block counts."""
        import workflow_verifier

        mock_entries = [
            {
                "timestamp": "2025-12-21T00:00:00Z",
                "session_id": self.session_id,
                "hook": "merge-check",
                "decision": "approve",
            },
            {
                "timestamp": "2025-12-21T00:01:00Z",
                "session_id": self.session_id,
                "hook": "merge-check",
                "decision": "block",
            },
            {
                "timestamp": "2025-12-21T00:02:00Z",
                "session_id": self.session_id,
                "hook": "merge-check",
                "decision": "approve",
            },
        ]

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            summary = verifier.get_decision_summary("merge-check")
            assert summary["approve"] == 2
            assert summary["block"] == 1


class TestVerifyHook:
    """Tests for verify_hook method."""

    def setup_method(self):
        """Set up test fixtures."""
        # Use UUID format for session_id (Issue #2496: security validation)
        self.session_id = "00000000-0000-0000-0000-000000000002"

    def test_verify_hook_not_fired(self):
        """verify_hook should return not_fired status for unfired hooks."""
        import workflow_verifier

        mock_entries = []

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            result = verifier.verify_hook("merge-check")
            assert result.status == "not_fired"
            assert result.execution_count == 0

    def test_verify_hook_ok(self):
        """verify_hook should return ok status for correctly behaving hooks."""
        import workflow_verifier

        mock_entries = [
            {
                "timestamp": "2025-12-21T00:00:00Z",
                "session_id": self.session_id,
                "hook": "merge-check",
                "decision": "approve",
            },
        ]

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            result = verifier.verify_hook("merge-check")
            assert result.status == "ok"
            assert result.execution_count == 1

    def test_verify_hook_unknown(self):
        """verify_hook should return unknown status for undefined hooks."""
        import workflow_verifier

        mock_entries = []

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            result = verifier.verify_hook("unknown-hook-xyz")
            assert result.status == "unknown"

    def test_verify_hook_expected_block_with_approves(self):
        """verify_hook should return unexpected_approve when expected=block but has approves.

        This tests the fix for Issue #699: If a hook has expected_decision="block"
        but approves > 0, it should be flagged as unexpected_approve regardless
        of whether blocks > 0.
        """
        import workflow_verifier
        from flow_definitions import EXPECTED_HOOK_BEHAVIORS, ExpectedHookBehavior

        # Create a mock hook with expected_decision="block"
        mock_hook = ExpectedHookBehavior(
            hook_name="test-block-hook",
            phase_id="session_start",
            trigger_type="PreToolUse",
            trigger_tool="Bash",
            expected_decision="block",
            description="Test hook that should block",
        )

        # Create log entries: 3 approves, 1 block
        mock_entries = [
            {
                "timestamp": "2025-12-21T00:00:00Z",
                "session_id": self.session_id,
                "hook": "test-block-hook",
                "decision": "approve",
            },
            {
                "timestamp": "2025-12-21T00:01:00Z",
                "session_id": self.session_id,
                "hook": "test-block-hook",
                "decision": "approve",
            },
            {
                "timestamp": "2025-12-21T00:02:00Z",
                "session_id": self.session_id,
                "hook": "test-block-hook",
                "decision": "approve",
            },
            {
                "timestamp": "2025-12-21T00:03:00Z",
                "session_id": self.session_id,
                "hook": "test-block-hook",
                "decision": "block",
            },
        ]

        # Temporarily add mock hook to registry
        original_behaviors = EXPECTED_HOOK_BEHAVIORS.copy()
        EXPECTED_HOOK_BEHAVIORS["test-block-hook"] = mock_hook

        try:
            with patch.object(
                workflow_verifier, "read_session_log_entries", return_value=mock_entries
            ):
                verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
                result = verifier.verify_hook("test-block-hook")

                # Should be unexpected_approve because approves > 0
                # even though blocks > 0
                assert result.status == "unexpected_approve"
                assert result.expected_decision == "block"
                assert result.actual_decision == "approve"
        finally:
            # Restore original behaviors
            EXPECTED_HOOK_BEHAVIORS.clear()
            EXPECTED_HOOK_BEHAVIORS.update(original_behaviors)


class TestVerifyPhase:
    """Tests for verify_phase method."""

    def setup_method(self):
        """Set up test fixtures."""
        # Use UUID format for session_id (Issue #2496: security validation)
        self.session_id = "00000000-0000-0000-0000-000000000003"

    def test_verify_phase_not_started(self):
        """verify_phase should return not_started for phases with no hooks fired."""
        import workflow_verifier

        mock_entries = []

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            result = verifier.verify_phase("session_start")
            assert result.status == "not_started"
            assert result.hooks_fired == 0

    def test_verify_phase_partial(self):
        """verify_phase should return partial for phases with some hooks fired."""
        import workflow_verifier

        # Fire one hook from session_start phase
        mock_entries = [
            {
                "timestamp": "2025-12-21T00:00:00Z",
                "session_id": self.session_id,
                "hook": "date-context-injector",
                "decision": "approve",
            },
        ]

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            result = verifier.verify_phase("session_start")
            assert result.status == "partial"
            assert result.hooks_fired == 1
            assert result.hooks_expected > 1

    def test_verify_phase_complete(self):
        """verify_phase should return complete when all hooks fired."""
        import workflow_verifier
        from flow_definitions import get_phase

        phase = get_phase("session_start")

        # Fire all hooks from session_start phase
        mock_entries = [
            {
                "timestamp": "2025-12-21T00:00:00Z",
                "session_id": self.session_id,
                "hook": hook_name,
                "decision": "approve",
            }
            for hook_name in phase.expected_hooks
        ]

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            result = verifier.verify_phase("session_start")
            assert result.status == "complete"
            assert result.hooks_fired == len(phase.expected_hooks)


class TestGenerateReport:
    """Tests for generate_report method."""

    def setup_method(self):
        """Set up test fixtures."""
        # Use UUID format for session_id (Issue #2496: security validation)
        self.session_id = "00000000-0000-0000-0000-000000000004"

    def test_generate_report_empty_session(self):
        """generate_report should work with empty session."""
        import workflow_verifier

        mock_entries = []

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            report = verifier.generate_report()

            assert "ワークフロー検証レポート" in report
            assert self.session_id in report
            assert "フェーズ進捗" in report

    def test_generate_report_includes_phases(self):
        """generate_report should include all phases."""
        import workflow_verifier

        mock_entries = []

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            report = verifier.generate_report()

            # Check some phase names are present
            assert "セッション開始" in report
            assert "セッション終了" in report


class TestGetSummaryDict:
    """Tests for get_summary_dict method."""

    def setup_method(self):
        """Set up test fixtures."""
        # Use UUID format for session_id (Issue #2496: security validation)
        self.session_id = "00000000-0000-0000-0000-000000000005"

    def test_get_summary_dict_structure(self):
        """get_summary_dict should return expected structure."""
        import workflow_verifier

        mock_entries = []

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            summary = verifier.get_summary_dict()

            # Check required keys
            assert "session_id" in summary
            assert "timestamp" in summary
            assert "execution_count" in summary
            assert "phases" in summary
            assert "fired_hooks" in summary
            assert "unfired_hooks" in summary
            assert "issues" in summary
            assert "has_issues" in summary

    def test_get_summary_dict_phases_count(self):
        """get_summary_dict should include all 13 phases."""
        import workflow_verifier

        mock_entries = []

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            summary = verifier.get_summary_dict()

            assert len(summary["phases"]) == 13


class TestGetFiredAndUnfiredHooks:
    """Tests for get_fired_hooks and get_unfired_hooks methods."""

    def setup_method(self):
        """Set up test fixtures."""
        # Use UUID format for session_id (Issue #2496: security validation)
        self.session_id = "00000000-0000-0000-0000-000000000006"

    def test_get_fired_hooks_empty(self):
        """get_fired_hooks should return empty list for empty session."""
        import workflow_verifier

        mock_entries = []

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            assert verifier.get_fired_hooks() == []

    def test_get_fired_hooks_returns_unique(self):
        """get_fired_hooks should return unique hook names."""
        import workflow_verifier

        # Fire same hook twice
        mock_entries = [
            {
                "timestamp": "2025-12-21T00:00:00Z",
                "session_id": self.session_id,
                "hook": "merge-check",
                "decision": "approve",
            },
            {
                "timestamp": "2025-12-21T00:01:00Z",
                "session_id": self.session_id,
                "hook": "merge-check",
                "decision": "block",
            },
        ]

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            fired = verifier.get_fired_hooks()
            assert len(fired) == 1
            assert "merge-check" in fired

    def test_get_unfired_hooks_returns_complement(self):
        """get_unfired_hooks should return hooks not in fired list."""
        import workflow_verifier

        mock_entries = [
            {
                "timestamp": "2025-12-21T00:00:00Z",
                "session_id": self.session_id,
                "hook": "merge-check",
                "decision": "approve",
            },
        ]

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id)
            unfired = verifier.get_unfired_hooks()
            assert "merge-check" not in unfired
            # Should have many unfired hooks
            assert len(unfired) > 50


class TestVerifyCurrentSession:
    """Tests for verify_current_session convenience function."""

    def setup_method(self):
        """Set up test fixtures."""
        # Use UUID format for session_id (Issue #2496: security validation)
        self.session_id = "00000000-0000-0000-0000-000000000007"

    def test_verify_current_session_returns_string(self):
        """verify_current_session should return a report string."""
        import workflow_verifier

        mock_entries = []

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            report = workflow_verifier.verify_current_session()
            assert isinstance(report, str)
            assert "ワークフロー検証レポート" in report


class TestSinceHoursFilter:
    """Tests for since_hours filtering functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        # Use UUID format for session_id (Issue #2496: security validation)
        self.session_id = "00000000-0000-0000-0000-000000000008"

    def test_since_hours_filters_old_entries(self):
        """since_hours should filter out entries older than the cutoff."""
        import workflow_verifier

        now = datetime.now(UTC)

        # Old entry (2 hours ago)
        old_time = (now - timedelta(hours=2)).isoformat()
        # Recent entry (30 minutes ago)
        recent_time = (now - timedelta(minutes=30)).isoformat()

        mock_entries = [
            {
                "timestamp": old_time,
                "session_id": self.session_id,
                "hook": "old-hook",
                "decision": "approve",
            },
            {
                "timestamp": recent_time,
                "session_id": self.session_id,
                "hook": "recent-hook",
                "decision": "approve",
            },
        ]

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            # With since_hours=1, should only include the recent entry
            verifier = workflow_verifier.WorkflowVerifier(
                session_id=self.session_id, since_hours=1.0
            )
            assert len(verifier.executions) == 1
            assert verifier.executions[0].hook == "recent-hook"

    def test_since_hours_none_includes_all(self):
        """since_hours=None should include all entries."""
        import workflow_verifier

        now = datetime.now(UTC)

        # Entries at different times
        mock_entries = [
            {
                "timestamp": (now - timedelta(hours=hours_ago)).isoformat(),
                "session_id": self.session_id,
                "hook": f"hook-{i}",
                "decision": "approve",
            }
            for i, hours_ago in enumerate([24, 12, 6, 1])
        ]

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            # With since_hours=None, should include all entries
            verifier = workflow_verifier.WorkflowVerifier(
                session_id=self.session_id, since_hours=None
            )
            assert len(verifier.executions) == 4

    def test_report_includes_since_hours_info(self):
        """Report should include since_hours information when set."""
        import workflow_verifier

        mock_entries = []

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            verifier = workflow_verifier.WorkflowVerifier(
                session_id=self.session_id, since_hours=2.5
            )
            report = verifier.generate_report()
            assert "直近 2.5 時間" in report

    def test_since_hours_zero_includes_nothing(self):
        """since_hours=0 should include nothing (cutoff is now)."""
        import workflow_verifier

        now = datetime.now(UTC)

        # Entry 1 second ago
        recent_time = (now - timedelta(seconds=1)).isoformat()

        mock_entries = [
            {
                "timestamp": recent_time,
                "session_id": self.session_id,
                "hook": "test-hook",
                "decision": "approve",
            },
        ]

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            # With since_hours=0, cutoff is now, so even recent entries are excluded
            verifier = workflow_verifier.WorkflowVerifier(session_id=self.session_id, since_hours=0)
            assert len(verifier.executions) == 0

    def test_since_hours_negative_raises_error(self):
        """since_hours with negative value should raise ValueError."""
        import workflow_verifier

        mock_entries = []

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            with pytest.raises(ValueError) as context:
                workflow_verifier.WorkflowVerifier(session_id=self.session_id, since_hours=-1.0)
            assert "non-negative" in str(context.value)

    def test_since_hours_skips_entries_without_timestamp(self):
        """Entries without timestamp should be skipped when since_hours is set."""
        import workflow_verifier

        now = datetime.now(UTC)

        # Entry without timestamp
        # Entry with timestamp (30 minutes ago)
        recent_time = (now - timedelta(minutes=30)).isoformat()

        mock_entries = [
            {
                "session_id": self.session_id,
                "hook": "no-timestamp-hook",
                "decision": "approve",
            },
            {
                "timestamp": recent_time,
                "session_id": self.session_id,
                "hook": "with-timestamp-hook",
                "decision": "approve",
            },
        ]

        with patch.object(workflow_verifier, "read_session_log_entries", return_value=mock_entries):
            # With since_hours set, entries without timestamp should be skipped
            verifier = workflow_verifier.WorkflowVerifier(
                session_id=self.session_id, since_hours=1.0
            )
            assert len(verifier.executions) == 1
            assert verifier.executions[0].hook == "with-timestamp-hook"
