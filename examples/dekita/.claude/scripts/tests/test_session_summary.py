#!/usr/bin/env python3
"""Tests for session-summary.py script (Issue #1418)."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for imports
scripts_dir = Path(__file__).parent.parent
sys.path.insert(0, str(scripts_dir))

# Load module from file path (handles hyphenated filenames)
script_path = scripts_dir / "session-summary.py"
spec = importlib.util.spec_from_file_location("session_summary", script_path)
if spec is None or spec.loader is None:
    raise ImportError(
        f"Cannot load module 'session_summary' from {script_path}: spec or loader is None"
    )
session_summary = importlib.util.module_from_spec(spec)
sys.modules["session_summary"] = session_summary
spec.loader.exec_module(session_summary)


class TestParseLogEntries:
    """Tests for parse_log_entries function."""

    def test_returns_empty_list_for_nonexistent_file(self):
        """Returns empty list when log file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "nonexistent.log"

            result = session_summary.parse_log_entries(log_file)

            assert result == []

    def test_parses_valid_json_entries(self):
        """Parses valid JSON lines from log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            entries = [
                {"session_id": "session-1", "hook": "hook-a"},
                {"session_id": "session-2", "hook": "hook-b"},
            ]
            log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

            result = session_summary.parse_log_entries(log_file)

            assert len(result) == 2
            assert result[0]["session_id"] == "session-1"
            assert result[1]["hook"] == "hook-b"

    def test_skips_invalid_json_lines(self):
        """Skips lines that are not valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            content = 'invalid json\n{"session_id": "session-1"}\nnot json either\n'
            log_file.write_text(content)

            result = session_summary.parse_log_entries(log_file)

            assert len(result) == 1
            assert result[0]["session_id"] == "session-1"

    def test_skips_empty_lines(self):
        """Skips empty lines in log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            content = '{"id": 1}\n\n\n{"id": 2}\n'
            log_file.write_text(content)

            result = session_summary.parse_log_entries(log_file)

            assert len(result) == 2

    def test_handles_empty_file(self):
        """Returns empty list for empty file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            log_file.write_text("")

            result = session_summary.parse_log_entries(log_file)

            assert result == []


class TestGetSessionEntries:
    """Tests for get_session_entries function."""

    def test_filters_by_session_id(self):
        """Filters entries by provided session ID."""
        entries = [
            {"session_id": "session-1", "hook": "hook-a"},
            {"session_id": "session-2", "hook": "hook-b"},
            {"session_id": "session-1", "hook": "hook-c"},
        ]

        result = session_summary.get_session_entries(entries, "session-1")

        assert len(result) == 2
        assert all(e["session_id"] == "session-1" for e in result)

    def test_returns_empty_when_session_not_found(self):
        """Returns empty list when session ID not found."""
        entries = [
            {"session_id": "session-1", "hook": "hook-a"},
        ]

        result = session_summary.get_session_entries(entries, "nonexistent")

        assert result == []

    @patch.dict("os.environ", {"CLAUDE_SESSION_ID": "env-session"})
    def test_uses_env_variable_when_session_id_none(self):
        """Uses CLAUDE_SESSION_ID env variable when session_id is None."""
        entries = [
            {"session_id": "env-session", "hook": "hook-a"},
            {"session_id": "other", "hook": "hook-b"},
        ]

        result = session_summary.get_session_entries(entries, None)

        assert len(result) == 1
        assert result[0]["session_id"] == "env-session"

    @patch.dict("os.environ", {}, clear=True)
    def test_returns_empty_when_no_session_id_provided_or_env(self):
        """Returns empty list when no session ID and no env variable."""
        entries = [
            {"session_id": "session-1", "hook": "hook-a"},
        ]

        result = session_summary.get_session_entries(entries, None)

        assert result == []


class TestCalculateDuration:
    """Tests for calculate_duration function."""

    def test_returns_na_for_empty_entries(self):
        """Returns N/A for empty entry list."""
        start, end, duration_str = session_summary.calculate_duration([])

        assert start is None
        assert end is None
        assert duration_str == "N/A"

    def test_calculates_duration_in_seconds(self):
        """Calculates duration for short sessions (seconds only)."""
        entries = [
            {"timestamp": "2025-01-01T00:00:00+00:00"},
            {"timestamp": "2025-01-01T00:00:45+00:00"},
        ]

        start, end, duration_str = session_summary.calculate_duration(entries)

        assert start is not None
        assert end is not None
        assert duration_str == "45s"

    def test_calculates_duration_in_minutes(self):
        """Calculates duration for medium sessions (minutes)."""
        entries = [
            {"timestamp": "2025-01-01T00:00:00+00:00"},
            {"timestamp": "2025-01-01T00:05:30+00:00"},
        ]

        start, end, duration_str = session_summary.calculate_duration(entries)

        assert duration_str == "5m 30s"

    def test_calculates_duration_in_hours(self):
        """Calculates duration for long sessions (hours)."""
        entries = [
            {"timestamp": "2025-01-01T00:00:00+00:00"},
            {"timestamp": "2025-01-01T02:30:00+00:00"},
        ]

        start, end, duration_str = session_summary.calculate_duration(entries)

        assert duration_str == "2h 30m"

    def test_handles_entries_without_timestamps(self):
        """Returns N/A when entries have no valid timestamps."""
        entries = [
            {"session_id": "session-1"},
            {"hook": "hook-a"},
        ]

        start, end, duration_str = session_summary.calculate_duration(entries)

        assert start is None
        assert end is None
        assert duration_str == "N/A"

    def test_handles_invalid_timestamp_format(self):
        """Skips entries with invalid timestamp format."""
        entries = [
            {"timestamp": "not-a-date"},
            {"timestamp": "2025-01-01T00:00:00+00:00"},
            {"timestamp": "2025-01-01T00:01:00+00:00"},
        ]

        start, end, duration_str = session_summary.calculate_duration(entries)

        assert duration_str == "1m 0s"


class TestCountBlocks:
    """Tests for count_blocks function."""

    def test_counts_blocks_by_hook(self):
        """Counts block decisions grouped by hook name."""
        entries = [
            {"hook": "hook-a", "decision": "block"},
            {"hook": "hook-a", "decision": "block"},
            {"hook": "hook-b", "decision": "block"},
            {"hook": "hook-a", "decision": "approve"},
        ]

        result = session_summary.count_blocks(entries)

        assert result["hook-a"] == 2
        assert result["hook-b"] == 1
        assert "approve" not in result

    def test_returns_empty_when_no_blocks(self):
        """Returns empty dict when no block decisions."""
        entries = [
            {"hook": "hook-a", "decision": "approve"},
        ]

        result = session_summary.count_blocks(entries)

        assert result == {}

    def test_handles_missing_hook_field(self):
        """Uses 'unknown' for entries without hook field."""
        entries = [
            {"decision": "block"},
        ]

        result = session_summary.count_blocks(entries)

        assert result["unknown"] == 1


class TestCountApproves:
    """Tests for count_approves function."""

    def test_counts_approves_by_hook(self):
        """Counts approve decisions grouped by hook name."""
        entries = [
            {"hook": "hook-a", "decision": "approve"},
            {"hook": "hook-a", "decision": "approve"},
            {"hook": "hook-b", "decision": "approve"},
            {"hook": "hook-a", "decision": "block"},
        ]

        result = session_summary.count_approves(entries)

        assert result["hook-a"] == 2
        assert result["hook-b"] == 1

    def test_returns_empty_when_no_approves(self):
        """Returns empty dict when no approve decisions."""
        entries = [
            {"hook": "hook-a", "decision": "block"},
        ]

        result = session_summary.count_approves(entries)

        assert result == {}

    def test_handles_missing_hook_field(self):
        """Uses 'unknown' for entries without hook field."""
        entries = [
            {"decision": "approve"},
        ]

        result = session_summary.count_approves(entries)

        assert result["unknown"] == 1


class TestExtractPrInfo:
    """Tests for extract_pr_info function."""

    def test_extracts_completed_prs(self):
        """Extracts PR numbers from successful ci-monitor completions."""
        entries = [
            {
                "hook": "ci-monitor",
                "details": {
                    "pr_number": "123",
                    "action": "monitor_complete",
                    "result": "success",
                },
            },
            {
                "hook": "ci-monitor",
                "details": {
                    "pr_number": "456",
                    "action": "monitor_complete",
                    "result": "success",
                },
            },
        ]

        result = session_summary.extract_pr_info(entries)

        assert "123" in result["completed"]
        assert "456" in result["completed"]

    def test_ignores_non_success_completions(self):
        """Ignores ci-monitor completions that are not successful."""
        entries = [
            {
                "hook": "ci-monitor",
                "details": {
                    "pr_number": "123",
                    "action": "monitor_complete",
                    "result": "timeout",
                },
            },
        ]

        result = session_summary.extract_pr_info(entries)

        assert result["completed"] == []

    def test_ignores_non_monitor_complete_actions(self):
        """Ignores ci-monitor events that are not monitor_complete."""
        entries = [
            {
                "hook": "ci-monitor",
                "details": {
                    "pr_number": "123",
                    "action": "ci_state_change",
                    "result": "success",
                },
            },
        ]

        result = session_summary.extract_pr_info(entries)

        assert result["completed"] == []

    def test_ignores_non_ci_monitor_hooks(self):
        """Ignores entries from other hooks."""
        entries = [
            {
                "hook": "other-hook",
                "details": {
                    "pr_number": "123",
                    "action": "monitor_complete",
                    "result": "success",
                },
            },
        ]

        result = session_summary.extract_pr_info(entries)

        assert result["completed"] == []

    def test_handles_missing_details(self):
        """Handles entries without details field."""
        entries = [
            {"hook": "ci-monitor"},
            {"hook": "ci-monitor", "details": None},
        ]

        result = session_summary.extract_pr_info(entries)

        assert result["completed"] == []

    def test_deduplicates_pr_numbers(self):
        """Deduplicates PR numbers in completed list."""
        entries = [
            {
                "hook": "ci-monitor",
                "details": {
                    "pr_number": "123",
                    "action": "monitor_complete",
                    "result": "success",
                },
            },
            {
                "hook": "ci-monitor",
                "details": {
                    "pr_number": "123",
                    "action": "monitor_complete",
                    "result": "success",
                },
            },
        ]

        result = session_summary.extract_pr_info(entries)

        assert result["completed"] == ["123"]

    def test_sorts_pr_numbers(self):
        """Sorts PR numbers in completed list."""
        entries = [
            {
                "hook": "ci-monitor",
                "details": {
                    "pr_number": "456",
                    "action": "monitor_complete",
                    "result": "success",
                },
            },
            {
                "hook": "ci-monitor",
                "details": {
                    "pr_number": "123",
                    "action": "monitor_complete",
                    "result": "success",
                },
            },
        ]

        result = session_summary.extract_pr_info(entries)

        assert result["completed"] == ["123", "456"]


class TestListRecentSessions:
    """Tests for list_recent_sessions function."""

    def test_lists_sessions_with_info(self):
        """Lists sessions with basic information."""
        entries = [
            {
                "session_id": "session-1",
                "timestamp": "2025-01-01T00:00:00+00:00",
                "decision": "block",
                "hook": "hook-a",
            },
            {
                "session_id": "session-1",
                "timestamp": "2025-01-01T00:01:00+00:00",
                "decision": "approve",
                "hook": "hook-b",
            },
            {
                "session_id": "session-2",
                "timestamp": "2025-01-01T01:00:00+00:00",
                "decision": "approve",
                "hook": "hook-a",
            },
        ]

        result = session_summary.list_recent_sessions(entries)

        assert len(result) == 2
        # Should be sorted by start time (most recent first)
        assert result[0]["session_id"] == "session-2"
        assert result[1]["session_id"] == "session-1"
        assert result[1]["entry_count"] == 2
        assert result[1]["block_count"] == 1

    def test_respects_limit(self):
        """Respects limit parameter."""
        entries = [
            {"session_id": f"session-{i}", "timestamp": f"2025-01-{(i + 1):02d}T00:00:00+00:00"}
            for i in range(5)
        ]

        result = session_summary.list_recent_sessions(entries, limit=3)

        # Verify both limit and correct sorting (most recent first)
        assert [s["session_id"] for s in result] == ["session-4", "session-3", "session-2"]

    def test_handles_entries_without_session_id(self):
        """Skips entries without session_id."""
        entries = [
            {"timestamp": "2025-01-01T00:00:00+00:00"},
            {"session_id": "session-1", "timestamp": "2025-01-01T00:00:00+00:00"},
        ]

        result = session_summary.list_recent_sessions(entries)

        assert len(result) == 1
        assert result[0]["session_id"] == "session-1"

    def test_handles_entries_without_timestamps(self):
        """Handles sessions without timestamps."""
        entries = [
            {"session_id": "session-1"},
        ]

        result = session_summary.list_recent_sessions(entries)

        assert len(result) == 1
        assert result[0]["start"] is None
        assert result[0]["duration"] == "N/A"
