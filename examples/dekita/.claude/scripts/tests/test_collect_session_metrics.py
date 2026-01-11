#!/usr/bin/env python3
"""Tests for collect_session_metrics.py script."""

import importlib.util
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
scripts_dir = Path(__file__).parent.parent
sys.path.insert(0, str(scripts_dir))

# Load module from file path (handles hyphenated filenames)
script_path = scripts_dir / "collect_session_metrics.py"
spec = importlib.util.spec_from_file_location("collect_session_metrics", script_path)
collect_session_metrics = importlib.util.module_from_spec(spec)
sys.modules["collect_session_metrics"] = collect_session_metrics
spec.loader.exec_module(collect_session_metrics)


class TestGetFallbackSessionId:
    """Tests for get_fallback_session_id function.

    Issue #2496: Updated to test the new PPID-based fallback function.
    """

    def test_returns_ppid_based_session_id(self):
        """Returns a PPID-based session ID."""
        import os

        result = collect_session_metrics.get_fallback_session_id()

        expected = f"ppid-{os.getppid()}"
        assert result == expected


class TestAnalyzeSessionFromHooks:
    """Tests for analyze_session_from_hooks function.

    Issue #2190: Updated to use read_session_log_entries mock.
    """

    def test_returns_default_values_when_no_entries(self):
        """Returns default values when no log entries exist."""
        with patch.object(collect_session_metrics, "read_session_log_entries", return_value=[]):
            result = collect_session_metrics.analyze_session_from_hooks("session-1")

        assert result["hook_executions"] == 0
        assert result["blocks"] == 0
        assert result["approves"] == 0
        assert result["hooks_triggered"] == []
        assert result["branches_touched"] == []

    def test_counts_hook_executions(self):
        """Counts hook executions for session."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "hook-a",
                "decision": "approve",
                "timestamp": "2025-01-01T00:00:00+00:00",
            },
            {
                "session_id": "session-1",
                "hook": "hook-b",
                "decision": "approve",
                "timestamp": "2025-01-01T00:01:00+00:00",
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_session_from_hooks("session-1")

        assert result["hook_executions"] == 2
        assert "hook-a" in result["hooks_triggered"]
        assert "hook-b" in result["hooks_triggered"]

    def test_counts_blocks_and_approves(self):
        """Correctly counts blocks and approves."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "hook-a",
                "decision": "approve",
                "timestamp": "2025-01-01T00:00:00+00:00",
            },
            {
                "session_id": "session-1",
                "hook": "hook-b",
                "decision": "block",
                "reason": "Test block reason",
                "timestamp": "2025-01-01T00:01:00+00:00",
            },
            {
                "session_id": "session-1",
                "hook": "hook-c",
                "decision": "block",
                "reason": "Another reason",
                "timestamp": "2025-01-01T00:02:00+00:00",
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_session_from_hooks("session-1")

        assert result["approves"] == 1
        assert result["blocks"] == 2
        assert len(result["block_reasons"]) == 2

    def test_tracks_branches_touched(self):
        """Tracks branches touched during session."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "hook-a",
                "decision": "approve",
                "branch": "main",
                "timestamp": "2025-01-01T00:00:00+00:00",
            },
            {
                "session_id": "session-1",
                "hook": "hook-b",
                "decision": "approve",
                "branch": "feature/test",
                "timestamp": "2025-01-01T00:01:00+00:00",
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_session_from_hooks("session-1")

        assert "main" in result["branches_touched"]
        assert "feature/test" in result["branches_touched"]

    def test_calculates_session_duration(self):
        """Calculates first and last timestamp."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "hook-a",
                "decision": "approve",
                "timestamp": "2025-01-01T00:00:00+00:00",
            },
            {
                "session_id": "session-1",
                "hook": "hook-b",
                "decision": "approve",
                "timestamp": "2025-01-01T02:30:00+00:00",
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_session_from_hooks("session-1")

        duration = (result["last_timestamp"] - result["first_timestamp"]).total_seconds()
        assert duration == 2.5 * 3600  # 2.5 hours

    def test_handles_entries_without_timestamp(self):
        """Handles entries without timestamp gracefully (KeyError)."""
        entries = [
            {"session_id": "session-1", "hook": "hook-a", "decision": "approve"},
            {
                "session_id": "session-1",
                "hook": "test",
                "decision": "approve",
                "timestamp": "2025-01-01T00:00:00+00:00",
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_session_from_hooks("session-1")

        # First entry is skipped due to missing timestamp
        assert result["hook_executions"] == 1


class TestAnalyzeReviewThreadsFromHooks:
    """Tests for analyze_review_threads_from_hooks function (Issue #1419).

    Issue #2190: Updated to use read_session_log_entries mock.
    """

    def test_returns_default_values_when_no_entries(self):
        """Returns default values when no log entries exist."""
        with patch.object(collect_session_metrics, "read_session_log_entries", return_value=[]):
            result = collect_session_metrics.analyze_review_threads_from_hooks("session-1")

        assert result["batch_resolve_used"] is False
        assert result["resolved_count"] == 0

    def test_tracks_batch_resolve_threads(self):
        """Tracks batch_resolve_threads.py execution."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "batch-resolve-threads",
                "decision": "approve",
                "timestamp": "2025-01-01T00:00:00+00:00",
                "details": {
                    "pr_number": "123",
                    "total_threads": 10,
                    "resolved_count": 8,
                    "failed_count": 2,
                    "dry_run": False,
                },
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_review_threads_from_hooks("session-1")

        assert result["batch_resolve_used"] is True
        assert result["resolved_count"] == 8

    def test_ignores_dry_run(self):
        """Ignores dry_run executions for resolved_count."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "batch-resolve-threads",
                "decision": "approve",
                "timestamp": "2025-01-01T00:00:00+00:00",
                "details": {
                    "pr_number": "123",
                    "total_threads": 10,
                    "resolved_count": 10,
                    "failed_count": 0,
                    "dry_run": True,
                },
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_review_threads_from_hooks("session-1")

        assert result["batch_resolve_used"] is False
        assert result["resolved_count"] == 0

    def test_accumulates_multiple_batch_resolves(self):
        """Accumulates resolved_count from multiple batch_resolve runs."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "batch-resolve-threads",
                "decision": "approve",
                "timestamp": "2025-01-01T00:00:00+00:00",
                "details": {"resolved_count": 5, "dry_run": False},
            },
            {
                "session_id": "session-1",
                "hook": "batch-resolve-threads",
                "decision": "approve",
                "timestamp": "2025-01-01T00:10:00+00:00",
                "details": {"resolved_count": 8, "dry_run": False},
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_review_threads_from_hooks("session-1")

        assert result["resolved_count"] == 13  # 5 + 8
        assert result["batch_resolve_used"] is True


class TestAnalyzeCiMonitoringFromHooks:
    """Tests for analyze_ci_monitoring_from_hooks function (Issue #1419).

    Issue #2190: Updated to use read_session_log_entries mock.
    """

    def test_returns_default_values_when_no_entries(self):
        """Returns default values when no log entries exist."""
        with patch.object(collect_session_metrics, "read_session_log_entries", return_value=[]):
            result = collect_session_metrics.analyze_ci_monitoring_from_hooks("session-1")

        assert result["rebase_count"] == 0
        assert result["ci_wait_minutes"] == 0
        assert result["pr_rebases"] == {}

    def test_counts_rebases(self):
        """Counts successful rebases."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:00:00+00:00",
                "details": {
                    "action": "rebase",
                    "result": "success",
                    "pr_number": "123",
                },
            },
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:01:00+00:00",
                "details": {
                    "action": "rebase",
                    "result": "success",
                    "pr_number": "123",
                },
            },
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:02:00+00:00",
                "details": {
                    "action": "rebase",
                    "result": "failure",
                    "pr_number": "123",
                },
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_ci_monitoring_from_hooks("session-1")

        assert result["rebase_count"] == 2  # Only successful rebases

    def test_calculates_ci_wait_minutes_from_elapsed_seconds(self):
        """Calculates CI wait time from elapsed_seconds (fallback)."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:00:00+00:00",
                "details": {
                    "action": "monitor_complete",
                    "pr_number": "123",
                    "elapsed_seconds": 600,  # 10 minutes
                },
            },
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:30:00+00:00",
                "details": {
                    "action": "monitor_complete",
                    "pr_number": "456",
                    "elapsed_seconds": 300,  # 5 minutes
                },
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_ci_monitoring_from_hooks("session-1")

        assert result["ci_wait_minutes"] == 15  # 10 + 5 minutes

    def test_prefers_total_wait_seconds_over_elapsed_seconds(self):
        """Prefers total_wait_seconds when both are present."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:00:00+00:00",
                "details": {
                    "action": "monitor_complete",
                    "pr_number": "123",
                    "total_wait_seconds": 1200,  # 20 minutes (preferred)
                    "elapsed_seconds": 600,  # 10 minutes (fallback)
                },
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_ci_monitoring_from_hooks("session-1")

        assert result["ci_wait_minutes"] == 20  # Uses total_wait_seconds

    def test_uses_latest_monitor_complete_per_pr(self):
        """Uses the latest monitor_complete for each PR."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:00:00+00:00",
                "details": {
                    "action": "monitor_complete",
                    "pr_number": "123",
                    "elapsed_seconds": 600,
                },
            },
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:30:00+00:00",
                "details": {
                    "action": "monitor_complete",
                    "pr_number": "123",  # Same PR, later entry
                    "elapsed_seconds": 900,  # 15 minutes (overrides previous)
                },
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_ci_monitoring_from_hooks("session-1")

        assert result["ci_wait_minutes"] == 15  # Uses latest (900 seconds = 15 min)

    def test_tracks_rebases_per_pr(self):
        """Issue #1289: Tracks rebases per PR number."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:00:00+00:00",
                "details": {
                    "action": "rebase",
                    "result": "success",
                    "pr_number": "123",
                },
            },
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:01:00+00:00",
                "details": {
                    "action": "rebase",
                    "result": "success",
                    "pr_number": "123",
                },
            },
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:02:00+00:00",
                "details": {
                    "action": "rebase",
                    "result": "success",
                    "pr_number": "456",
                },
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_ci_monitoring_from_hooks("session-1")

        assert result["rebase_count"] == 3
        assert result["pr_rebases"] == {"123": 2, "456": 1}

    def test_generates_warning_for_frequent_rebases(self):
        """Issue #1289: Generates warning for PRs with >= 3 rebases."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": f"2025-01-01T00:0{i}:00+00:00",
                "details": {
                    "action": "rebase",
                    "result": "success",
                    "pr_number": "123",
                },
            }
            for i in range(4)  # 4 rebases for PR #123
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_ci_monitoring_from_hooks("session-1")

        assert result["pr_rebases"] == {"123": 4}
        assert "pr_rebase_warnings" in result
        assert len(result["pr_rebase_warnings"]) == 1
        assert "PR #123" in result["pr_rebase_warnings"][0]
        assert "4回のリベース" in result["pr_rebase_warnings"][0]

    def test_no_warning_for_low_rebase_count(self):
        """Issue #1289: No warning for PRs with < 3 rebases."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": f"2025-01-01T00:0{i}:00+00:00",
                "details": {
                    "action": "rebase",
                    "result": "success",
                    "pr_number": "123",
                },
            }
            for i in range(2)  # Only 2 rebases
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_ci_monitoring_from_hooks("session-1")

        assert result["pr_rebases"] == {"123": 2}
        assert "pr_rebase_warnings" not in result

    def test_handles_none_pr_number(self):
        """Issue #1289: Handles None pr_number without false positives."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:00:00+00:00",
                "details": {
                    "action": "rebase",
                    "result": "success",
                    "pr_number": None,  # Explicitly None
                },
            },
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:01:00+00:00",
                "details": {
                    "action": "rebase",
                    "result": "success",
                    # pr_number key missing entirely
                },
            },
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:02:00+00:00",
                "details": {
                    "action": "rebase",
                    "result": "success",
                    "pr_number": "123",  # Valid PR number
                },
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_ci_monitoring_from_hooks("session-1")

        # Total rebase count includes all rebases
        assert result["rebase_count"] == 3
        # But pr_rebases only counts the one with valid PR number
        assert result["pr_rebases"] == {"123": 1}
        # No "None" key should exist
        assert "None" not in result["pr_rebases"]
        assert "" not in result["pr_rebases"]

    def test_handles_none_pr_number_in_monitor_complete(self):
        """Issue #1289: Ignores monitor_complete with None/missing pr_number."""
        entries = [
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:00:00+00:00",
                "details": {
                    "action": "monitor_complete",
                    "pr_number": None,  # Explicitly None
                    "elapsed_seconds": 600,
                },
            },
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:01:00+00:00",
                "details": {
                    "action": "monitor_complete",
                    # pr_number key missing entirely
                    "elapsed_seconds": 300,
                },
            },
            {
                "session_id": "session-1",
                "hook": "ci-monitor",
                "decision": "approve",
                "timestamp": "2025-01-01T00:02:00+00:00",
                "details": {
                    "action": "monitor_complete",
                    "pr_number": "123",  # Valid PR number
                    "elapsed_seconds": 120,  # 2 minutes
                },
            },
        ]

        with patch.object(
            collect_session_metrics, "read_session_log_entries", return_value=entries
        ):
            result = collect_session_metrics.analyze_ci_monitoring_from_hooks("session-1")

        # Only the valid pr_number entry should contribute to ci_wait_minutes
        assert result["ci_wait_minutes"] == 2  # Only 120 seconds from PR #123


class TestAnalyzeEfficiencyFromLogs:
    """Tests for analyze_efficiency_from_logs function (Issue #1409)."""

    def test_returns_empty_when_logs_missing(self):
        """Returns default values when efficiency logs don't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            efficiency_log = Path(tmpdir) / "tool-efficiency-metrics.log"
            rework_log = Path(tmpdir) / "rework-metrics.log"

            with (
                patch.object(collect_session_metrics, "TOOL_EFFICIENCY_LOG", efficiency_log),
                patch.object(collect_session_metrics, "REWORK_METRICS_LOG", rework_log),
            ):
                result = collect_session_metrics.analyze_efficiency_from_logs("session-1")

            assert result["read_edit_loop_count"] == 0
            assert result["repeated_search_count"] == 0
            assert result["bash_retry_count"] == 0
            assert result["rework_file_count"] == 0
            assert result["top_rework_files"] == []

    def test_counts_efficiency_patterns(self):
        """Counts each type of inefficiency pattern."""
        with tempfile.TemporaryDirectory() as tmpdir:
            efficiency_log = Path(tmpdir) / "tool-efficiency-metrics.log"
            rework_log = Path(tmpdir) / "rework-metrics.log"
            entries = [
                {"session_id": "session-1", "pattern_name": "read_edit_loop"},
                {"session_id": "session-1", "pattern_name": "read_edit_loop"},
                {"session_id": "session-1", "pattern_name": "repeated_search"},
                {"session_id": "session-1", "pattern_name": "bash_retry"},
                {"session_id": "other-session", "pattern_name": "read_edit_loop"},
            ]
            efficiency_log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

            with (
                patch.object(collect_session_metrics, "TOOL_EFFICIENCY_LOG", efficiency_log),
                patch.object(collect_session_metrics, "REWORK_METRICS_LOG", rework_log),
            ):
                result = collect_session_metrics.analyze_efficiency_from_logs("session-1")

            assert result["read_edit_loop_count"] == 2
            assert result["repeated_search_count"] == 1
            assert result["bash_retry_count"] == 1

    def test_counts_rework_files(self):
        """Counts and ranks rework files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            efficiency_log = Path(tmpdir) / "tool-efficiency-metrics.log"
            rework_log = Path(tmpdir) / "rework-metrics.log"
            entries = [
                {"session_id": "session-1", "file_path": "/path/to/ci-monitor.py"},
                {"session_id": "session-1", "file_path": "/path/to/ci-monitor.py"},
                {"session_id": "session-1", "file_path": "/path/to/ci-monitor.py"},
                {"session_id": "session-1", "file_path": "/path/to/merge-check.py"},
                {"session_id": "session-1", "file_path": "/path/to/merge-check.py"},
                {"session_id": "session-1", "file_path": "/path/to/test.py"},
                {"session_id": "other-session", "file_path": "/path/to/other.py"},
            ]
            rework_log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

            with (
                patch.object(collect_session_metrics, "TOOL_EFFICIENCY_LOG", efficiency_log),
                patch.object(collect_session_metrics, "REWORK_METRICS_LOG", rework_log),
            ):
                result = collect_session_metrics.analyze_efficiency_from_logs("session-1")

            assert result["rework_file_count"] == 3
            # フルパスで記録される
            assert result["top_rework_files"][0] == "/path/to/ci-monitor.py"  # Most reworked
            assert result["top_rework_files"][1] == "/path/to/merge-check.py"

    def test_limits_top_rework_files(self):
        """Limits top_rework_files to configured limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            efficiency_log = Path(tmpdir) / "tool-efficiency-metrics.log"
            rework_log = Path(tmpdir) / "rework-metrics.log"
            # Create entries for more than TOP_REWORK_FILES_LIMIT files
            entries = [
                {"session_id": "session-1", "file_path": f"/path/to/file{i}.py"} for i in range(10)
            ]
            rework_log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

            with (
                patch.object(collect_session_metrics, "TOOL_EFFICIENCY_LOG", efficiency_log),
                patch.object(collect_session_metrics, "REWORK_METRICS_LOG", rework_log),
            ):
                result = collect_session_metrics.analyze_efficiency_from_logs("session-1")

            assert result["rework_file_count"] == 10
            assert len(result["top_rework_files"]) == collect_session_metrics.TOP_REWORK_FILES_LIMIT

    def test_handles_invalid_json_lines(self):
        """Handles invalid JSON lines gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            efficiency_log = Path(tmpdir) / "tool-efficiency-metrics.log"
            rework_log = Path(tmpdir) / "rework-metrics.log"
            content = (
                'invalid json\n{"session_id": "session-1", "pattern_name": "read_edit_loop"}\n'
            )
            efficiency_log.write_text(content)

            with (
                patch.object(collect_session_metrics, "TOOL_EFFICIENCY_LOG", efficiency_log),
                patch.object(collect_session_metrics, "REWORK_METRICS_LOG", rework_log),
            ):
                result = collect_session_metrics.analyze_efficiency_from_logs("session-1")

            assert result["read_edit_loop_count"] == 1

    def test_filters_by_session_id(self):
        """Only counts data for the specified session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            efficiency_log = Path(tmpdir) / "tool-efficiency-metrics.log"
            rework_log = Path(tmpdir) / "rework-metrics.log"
            entries = [
                {"session_id": "session-1", "pattern_name": "read_edit_loop"},
                {"session_id": "other-session", "pattern_name": "read_edit_loop"},
                {"session_id": "other-session", "pattern_name": "read_edit_loop"},
            ]
            efficiency_log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

            with (
                patch.object(collect_session_metrics, "TOOL_EFFICIENCY_LOG", efficiency_log),
                patch.object(collect_session_metrics, "REWORK_METRICS_LOG", rework_log),
            ):
                result = collect_session_metrics.analyze_efficiency_from_logs("session-1")

            assert result["read_edit_loop_count"] == 1


class TestCollectGitStats:
    """Tests for collect_git_stats function."""

    @patch("subprocess.run")
    def test_collects_current_branch(self, mock_run):
        """Collects current branch name."""
        # First call: git rev-parse (branch)
        # Second call: git status (changes)
        # Third call: git worktree list
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="feature/test\n"),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout="worktree /path/to/main\n"),
        ]

        result = collect_session_metrics.collect_git_stats()

        assert result.get("current_branch") == "feature/test"

    @patch("subprocess.run")
    def test_collects_uncommitted_changes(self, mock_run):
        """Counts uncommitted changes."""
        # First call: git rev-parse (branch)
        # Second call: git status (changes)
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="main\n"),
            MagicMock(returncode=0, stdout=" M file1.py\n M file2.py\n"),
            MagicMock(returncode=0, stdout="worktree /path/to/main\n"),
        ]

        result = collect_session_metrics.collect_git_stats()

        assert result.get("uncommitted_changes") == 2

    @patch("subprocess.run")
    def test_handles_git_failure(self, mock_run):
        """Handles git command failure gracefully."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = collect_session_metrics.collect_git_stats()

        # Should return empty dict or partial stats
        assert isinstance(result, dict)

    @patch("subprocess.run")
    def test_collects_worktree_count(self, mock_run):
        """Correctly counts worktrees."""
        # First call: git rev-parse (branch)
        # Second call: git status (changes)
        # Third call: git worktree list
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="main\n"),
            MagicMock(returncode=0, stdout=""),
            MagicMock(
                returncode=0,
                stdout="worktree /path/to/main\nbranch refs/heads/main\n\nworktree /path/to/worktree1\nbranch refs/heads/feature1\n",
            ),
        ]

        result = collect_session_metrics.collect_git_stats()

        assert result.get("worktree_count") == 2


class TestCollectPrStatsForSession:
    """Tests for collect_pr_stats_for_session function."""

    @patch("subprocess.run")
    def test_counts_todays_prs(self, mock_run):
        """Counts PRs created today."""
        today = datetime.now(UTC)
        yesterday = today - timedelta(days=1)

        prs = [
            {
                "number": 1,
                "state": "OPEN",
                "createdAt": today.isoformat(),
                "mergedAt": None,
            },
            {
                "number": 2,
                "state": "MERGED",
                "createdAt": today.isoformat(),
                "mergedAt": today.isoformat(),
            },
            {
                "number": 3,
                "state": "OPEN",
                "createdAt": yesterday.isoformat(),
                "mergedAt": None,
            },
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(prs))

        result = collect_session_metrics.collect_pr_stats_for_session()

        assert result["prs_created"] == 2  # Only today's PRs
        assert result["prs_merged"] == 1  # Only merged PR

    @patch("subprocess.run")
    def test_returns_defaults_on_failure(self, mock_run):
        """Returns default values on API failure."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = collect_session_metrics.collect_pr_stats_for_session()

        assert result["prs_created"] == 0
        assert result["prs_merged"] == 0


class TestRecordSessionMetrics:
    """Tests for record_session_metrics function."""

    def test_writes_metrics_to_file(self):
        """Successfully writes metrics to log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "session-metrics.log"

            with (
                patch.object(collect_session_metrics, "SESSION_METRICS_LOG", log_file),
                patch.object(collect_session_metrics, "LOGS_DIR", Path(tmpdir)),
            ):
                metrics = {"session_id": "test-123", "hook_executions": 5}
                collect_session_metrics.record_session_metrics(metrics)

            assert log_file.exists()
            content = log_file.read_text()
            data = json.loads(content.strip())
            assert data["session_id"] == "test-123"

    def test_handles_datetime_serialization(self):
        """Handles datetime objects in metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "session-metrics.log"

            with (
                patch.object(collect_session_metrics, "SESSION_METRICS_LOG", log_file),
                patch.object(collect_session_metrics, "LOGS_DIR", Path(tmpdir)),
            ):
                metrics = {
                    "session_id": "test-123",
                    "timestamp": datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
                }
                collect_session_metrics.record_session_metrics(metrics)

            # Should not raise error and should write file
            assert log_file.exists()


class TestMain:
    """Tests for main function."""

    @patch("sys.argv", ["collect_session_metrics.py"])
    @patch.object(collect_session_metrics, "handle_session_id_arg")
    @patch.object(collect_session_metrics, "get_fallback_session_id")
    @patch.object(collect_session_metrics, "analyze_session_from_hooks")
    @patch.object(collect_session_metrics, "collect_git_stats")
    @patch.object(collect_session_metrics, "collect_pr_stats_for_session")
    @patch.object(collect_session_metrics, "analyze_review_threads_from_hooks")
    @patch.object(collect_session_metrics, "analyze_ci_monitoring_from_hooks")
    @patch.object(collect_session_metrics, "analyze_efficiency_from_logs")
    @patch.object(collect_session_metrics, "record_session_metrics")
    def test_collects_and_records_metrics(
        self,
        mock_record,
        mock_efficiency,
        mock_ci,
        mock_review,
        mock_pr,
        mock_git,
        mock_hooks,
        mock_fallback,
        mock_handle,
    ):
        """Collects metrics from all sources and records."""
        mock_handle.return_value = "test-session-123"
        mock_fallback.return_value = "ppid-12345"  # Won't be used since handle returns valid
        mock_hooks.return_value = {
            "hook_executions": 5,
            "blocks": 1,
            "approves": 4,
            "hooks_triggered": ["hook-a", "hook-b"],
            "branches_touched": ["main"],
            "block_reasons": ["test reason"],
            "first_timestamp": datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
            "last_timestamp": datetime(2025, 1, 1, 1, 0, 0, tzinfo=UTC),
        }
        mock_git.return_value = {"current_branch": "main", "uncommitted_changes": 0}
        mock_pr.return_value = {"prs_created": 2, "prs_merged": 1}
        # Issue #1419: Add mocks for new functions
        mock_review.return_value = {"resolved_count": 10, "batch_resolve_used": True}
        mock_ci.return_value = {"rebase_count": 2, "ci_wait_minutes": 15}
        # Issue #1409: Add mock for efficiency metrics
        mock_efficiency.return_value = {
            "read_edit_loop_count": 3,
            "repeated_search_count": 1,
            "bash_retry_count": 0,
            "rework_file_count": 2,
            "top_rework_files": ["ci_monitor.py", "merge_check.py"],
        }

        captured_output = StringIO()
        sys.stdout = captured_output
        try:
            collect_session_metrics.main()
        finally:
            sys.stdout = sys.__stdout__

        mock_record.assert_called_once()
        recorded_metrics = mock_record.call_args[0][0]
        assert recorded_metrics["session_id"] == "test-session-123"
        assert recorded_metrics["hook_executions"] == 5
        # Issue #1419: Verify new metrics are included
        assert recorded_metrics["review_threads"]["resolved_count"] == 10
        assert recorded_metrics["ci_monitoring"]["rebase_count"] == 2
        # Issue #1409: Verify efficiency metrics are included
        assert recorded_metrics["efficiency"]["read_edit_loop_count"] == 3
        assert recorded_metrics["efficiency"]["rework_file_count"] == 2

        output = captured_output.getvalue()
        assert "Session Metrics Recorded" in output
        assert "test-session-123" in output

    @patch.object(collect_session_metrics, "handle_session_id_arg")
    @patch("sys.argv", ["collect_session_metrics.py"])
    @patch.object(collect_session_metrics, "get_fallback_session_id")
    @patch.object(collect_session_metrics, "analyze_session_from_hooks")
    @patch.object(collect_session_metrics, "collect_git_stats")
    @patch.object(collect_session_metrics, "collect_pr_stats_for_session")
    @patch.object(collect_session_metrics, "analyze_review_threads_from_hooks")
    @patch.object(collect_session_metrics, "analyze_ci_monitoring_from_hooks")
    @patch.object(collect_session_metrics, "analyze_efficiency_from_logs")
    @patch.object(collect_session_metrics, "record_session_metrics")
    def test_handles_empty_session_data(
        self,
        mock_record,
        mock_efficiency,
        mock_ci,
        mock_review,
        mock_pr,
        mock_git,
        mock_hooks,
        mock_fallback,
        mock_handle,
    ):
        """Handles case with no session data gracefully."""
        mock_handle.return_value = "new-session"
        mock_fallback.return_value = "ppid-12345"  # Won't be used
        mock_hooks.return_value = {}
        mock_git.return_value = {}
        mock_pr.return_value = {"prs_created": 0, "prs_merged": 0}
        # Issue #1419: Empty data for new functions
        mock_review.return_value = {}
        mock_ci.return_value = {}
        # Issue #1409: Empty efficiency data
        mock_efficiency.return_value = {
            "read_edit_loop_count": 0,
            "repeated_search_count": 0,
            "bash_retry_count": 0,
            "rework_file_count": 0,
            "top_rework_files": [],
        }

        captured_output = StringIO()
        sys.stdout = captured_output
        try:
            collect_session_metrics.main()
        finally:
            sys.stdout = sys.__stdout__

        mock_record.assert_called_once()
        recorded_metrics = mock_record.call_args[0][0]
        assert recorded_metrics["hook_executions"] == 0
        assert recorded_metrics["blocks"] == 0


class TestIsSessionEndRecorded:
    """Tests for is_session_end_recorded function (Issue #1281)."""

    def test_returns_false_when_log_missing(self):
        """Returns False when session metrics log doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "nonexistent.log"

            with patch.object(collect_session_metrics, "SESSION_METRICS_LOG", log_file):
                result = collect_session_metrics.is_session_end_recorded("session-1")

            assert result is False

    def test_returns_false_when_session_not_recorded(self):
        """Returns False when session_id has no session_end record."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "session-metrics.log"
            entries = [
                {
                    "session_id": "other-session",
                    "type": "session_end",
                    "timestamp": "2025-01-01T00:00:00+00:00",
                },
            ]
            log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

            with patch.object(collect_session_metrics, "SESSION_METRICS_LOG", log_file):
                result = collect_session_metrics.is_session_end_recorded("session-1")

            assert result is False

    def test_returns_true_when_session_end_exists(self):
        """Returns True when session_id already has session_end record."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "session-metrics.log"
            entries = [
                {
                    "session_id": "session-1",
                    "type": "session_end",
                    "timestamp": "2025-01-01T00:00:00+00:00",
                },
            ]
            log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

            with patch.object(collect_session_metrics, "SESSION_METRICS_LOG", log_file):
                result = collect_session_metrics.is_session_end_recorded("session-1")

            assert result is True

    def test_returns_false_when_only_snapshot_exists(self):
        """Returns False when session_id only has session_snapshot record."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "session-metrics.log"
            entries = [
                {
                    "session_id": "session-1",
                    "type": "session_snapshot",
                    "timestamp": "2025-01-01T00:00:00+00:00",
                },
            ]
            log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

            with patch.object(collect_session_metrics, "SESSION_METRICS_LOG", log_file):
                result = collect_session_metrics.is_session_end_recorded("session-1")

            assert result is False

    def test_handles_invalid_json_lines(self):
        """Handles invalid JSON lines gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "session-metrics.log"
            content = 'invalid json\n{"session_id": "session-1", "type": "session_end", "timestamp": "2025-01-01T00:00:00+00:00"}\n'
            log_file.write_text(content)

            with patch.object(collect_session_metrics, "SESSION_METRICS_LOG", log_file):
                result = collect_session_metrics.is_session_end_recorded("session-1")

            assert result is True


class TestMainDuplicatePrevention:
    """Tests for duplicate session_end prevention in main function (Issue #1281).

    Issue #2496: Updated to mock handle_session_id_arg and get_fallback_session_id.
    """

    @patch("sys.argv", ["collect_session_metrics.py"])
    @patch.object(collect_session_metrics, "handle_session_id_arg")
    @patch.object(collect_session_metrics, "get_fallback_session_id")
    @patch.object(collect_session_metrics, "is_session_end_recorded")
    @patch.object(collect_session_metrics, "analyze_session_from_hooks")
    @patch.object(collect_session_metrics, "collect_git_stats")
    @patch.object(collect_session_metrics, "collect_pr_stats_for_session")
    @patch.object(collect_session_metrics, "analyze_review_threads_from_hooks")
    @patch.object(collect_session_metrics, "analyze_ci_monitoring_from_hooks")
    @patch.object(collect_session_metrics, "analyze_efficiency_from_logs")
    @patch.object(collect_session_metrics, "record_session_metrics")
    def test_records_session_end_for_first_record(
        self,
        mock_record,
        mock_efficiency,
        mock_ci,
        mock_review,
        mock_pr,
        mock_git,
        mock_hooks,
        mock_is_recorded,
        mock_fallback,
        mock_handle,
    ):
        """Records type='session_end' when session not previously recorded."""
        mock_handle.return_value = "new-session"  # Validated session ID
        mock_fallback.return_value = "ppid-12345"  # Won't be used
        mock_is_recorded.return_value = False
        mock_hooks.return_value = {}
        mock_git.return_value = {}
        mock_pr.return_value = {"prs_created": 0, "prs_merged": 0}
        mock_review.return_value = {}
        mock_ci.return_value = {}
        mock_efficiency.return_value = {
            "read_edit_loop_count": 0,
            "repeated_search_count": 0,
            "bash_retry_count": 0,
            "rework_file_count": 0,
            "top_rework_files": [],
        }

        captured_output = StringIO()
        sys.stdout = captured_output
        try:
            collect_session_metrics.main()
        finally:
            sys.stdout = sys.__stdout__

        mock_record.assert_called_once()
        recorded_metrics = mock_record.call_args[0][0]
        assert recorded_metrics["type"] == "session_end"

        output = captured_output.getvalue()
        assert "Session Metrics Recorded (End):" in output

    @patch("sys.argv", ["collect_session_metrics.py"])
    @patch.object(collect_session_metrics, "handle_session_id_arg")
    @patch.object(collect_session_metrics, "get_fallback_session_id")
    @patch.object(collect_session_metrics, "is_session_end_recorded")
    @patch.object(collect_session_metrics, "analyze_session_from_hooks")
    @patch.object(collect_session_metrics, "collect_git_stats")
    @patch.object(collect_session_metrics, "collect_pr_stats_for_session")
    @patch.object(collect_session_metrics, "analyze_review_threads_from_hooks")
    @patch.object(collect_session_metrics, "analyze_ci_monitoring_from_hooks")
    @patch.object(collect_session_metrics, "analyze_efficiency_from_logs")
    @patch.object(collect_session_metrics, "record_session_metrics")
    def test_records_session_snapshot_for_duplicate(
        self,
        mock_record,
        mock_efficiency,
        mock_ci,
        mock_review,
        mock_pr,
        mock_git,
        mock_hooks,
        mock_is_recorded,
        mock_fallback,
        mock_handle,
    ):
        """Records type='session_snapshot' when session already has session_end."""
        mock_handle.return_value = "existing-session"  # Validated session ID
        mock_fallback.return_value = "ppid-12345"  # Won't be used
        mock_is_recorded.return_value = True
        mock_hooks.return_value = {}
        mock_git.return_value = {}
        mock_pr.return_value = {"prs_created": 0, "prs_merged": 0}
        mock_review.return_value = {}
        mock_ci.return_value = {}
        mock_efficiency.return_value = {
            "read_edit_loop_count": 0,
            "repeated_search_count": 0,
            "bash_retry_count": 0,
            "rework_file_count": 0,
            "top_rework_files": [],
        }

        captured_output = StringIO()
        sys.stdout = captured_output
        try:
            collect_session_metrics.main()
        finally:
            sys.stdout = sys.__stdout__

        mock_record.assert_called_once()
        recorded_metrics = mock_record.call_args[0][0]
        assert recorded_metrics["type"] == "session_snapshot"

        output = captured_output.getvalue()
        assert "Session Metrics Recorded (Snapshot):" in output


class TestGetMainProjectRoot:
    """Tests for _get_main_project_root function.

    Issue #2198: Test worktree detection logic.
    """

    @patch("subprocess.run")
    def test_handles_relative_git_path(self, mock_run):
        """Handles relative .git path from main repository."""
        mock_run.return_value = MagicMock(returncode=0, stdout=".git\n")

        result = collect_session_metrics._get_main_project_root()

        # Should resolve relative path and return parent of .git
        assert result.is_absolute()
        # Verify the result is the parent of the resolved .git directory
        expected = (collect_session_metrics.SCRIPT_DIR / ".git").resolve().parent
        assert result == expected

    @patch("subprocess.run")
    def test_handles_absolute_git_path(self, mock_run):
        """Handles absolute .git path from main repository."""
        mock_run.return_value = MagicMock(returncode=0, stdout="/path/to/repo/.git\n")

        result = collect_session_metrics._get_main_project_root()

        assert result == Path("/path/to/repo")

    @patch("subprocess.run")
    def test_handles_worktree_path(self, mock_run):
        """Handles worktree path format: /path/to/main/.git/worktrees/<name>."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/path/to/main/.git/worktrees/issue-123\n"
        )

        result = collect_session_metrics._get_main_project_root()

        # Should return main repo root, not worktree
        assert result == Path("/path/to/main")

    @patch("subprocess.run")
    def test_falls_back_on_git_failure(self, mock_run):
        """Falls back to SCRIPT_DIR parent when git fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = collect_session_metrics._get_main_project_root()

        # Should use fallback
        assert result.is_absolute()

    @patch("subprocess.run")
    def test_handles_git_exception(self, mock_run):
        """Handles exception from git command gracefully."""
        mock_run.side_effect = Exception("Git not found")

        result = collect_session_metrics._get_main_project_root()

        # Should use fallback
        assert result.is_absolute()
