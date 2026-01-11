#!/usr/bin/env python3
"""Tests for analyze-api-operations.py.

Issue #1175: Comprehensive tests for API operation logging.
"""

import argparse

# Import the module - need to handle the hyphenated filename
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location(
    "analyze_api_operations",
    Path(__file__).parent.parent / "analyze_api_operations.py",
)
analyze_api_operations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyze_api_operations)

parse_duration = analyze_api_operations.parse_duration
format_duration = analyze_api_operations.format_duration
load_operations = analyze_api_operations.load_operations
cmd_summary = analyze_api_operations.cmd_summary
cmd_errors = analyze_api_operations.cmd_errors
cmd_duration_stats = analyze_api_operations.cmd_duration_stats


class TestParseDuration:
    """Tests for parse_duration function."""

    def test_hours(self):
        """Parse hours."""
        assert parse_duration("1h") == timedelta(hours=1)
        assert parse_duration("24h") == timedelta(hours=24)
        assert parse_duration("2H") == timedelta(hours=2)  # Case insensitive

    def test_days(self):
        """Parse days."""
        assert parse_duration("1d") == timedelta(days=1)
        assert parse_duration("7d") == timedelta(days=7)
        assert parse_duration("30D") == timedelta(days=30)

    def test_weeks(self):
        """Parse weeks."""
        assert parse_duration("1w") == timedelta(weeks=1)
        assert parse_duration("2W") == timedelta(weeks=2)

    def test_minutes(self):
        """Parse minutes."""
        assert parse_duration("5m") == timedelta(minutes=5)
        assert parse_duration("60M") == timedelta(minutes=60)

    def test_default_unit_is_days(self):
        """Default unit is days when no suffix."""
        assert parse_duration("7") == timedelta(days=7)
        assert parse_duration("1") == timedelta(days=1)

    def test_with_spaces(self):
        """Handle spaces in duration string."""
        assert parse_duration("2 h") == timedelta(hours=2)
        assert parse_duration("3 d") == timedelta(days=3)

    def test_invalid_format(self):
        """Invalid format raises ValueError."""
        with pytest.raises(ValueError):
            parse_duration("abc")
        with pytest.raises(ValueError):
            parse_duration("")
        with pytest.raises(ValueError):
            parse_duration("h")


class TestFormatDuration:
    """Tests for format_duration function."""

    def test_none_value(self):
        """None returns N/A."""
        assert format_duration(None) == "N/A"

    def test_milliseconds(self):
        """Sub-second durations show milliseconds."""
        assert format_duration(100) == "100ms"
        assert format_duration(999) == "999ms"

    def test_seconds(self):
        """Second-range durations show seconds."""
        assert format_duration(1000) == "1.0s"
        assert format_duration(5500) == "5.5s"
        assert format_duration(59999) == "60.0s"

    def test_minutes(self):
        """Minute-range durations show minutes."""
        assert format_duration(60000) == "1.0m"
        assert format_duration(90000) == "1.5m"
        assert format_duration(300000) == "5.0m"


class TestLoadOperations:
    """Tests for load_operations function."""

    def test_nonexistent_file(self, tmp_path):
        """Return empty list for nonexistent file."""
        with patch.object(
            analyze_api_operations, "API_OPERATIONS_LOG", tmp_path / "nonexistent.jsonl"
        ):
            result = load_operations()
            assert result == []

    def test_empty_file(self, tmp_path):
        """Return empty list for empty file."""
        log_file = tmp_path / "api-operations.jsonl"
        log_file.touch()
        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            result = load_operations()
            assert result == []

    def test_load_valid_entries(self, tmp_path):
        """Load valid JSON entries."""
        log_file = tmp_path / "api-operations.jsonl"
        entries = [
            {"type": "gh", "operation": "pr_view", "timestamp": "2025-01-01T00:00:00+00:00"},
            {"type": "git", "operation": "push", "timestamp": "2025-01-01T01:00:00+00:00"},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries))

        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            result = load_operations()
            assert len(result) == 2
            assert result[0]["operation"] == "pr_view"
            assert result[1]["operation"] == "push"

    def test_skip_invalid_json(self, tmp_path):
        """Skip invalid JSON lines."""
        log_file = tmp_path / "api-operations.jsonl"
        log_file.write_text(
            '{"valid": "entry", "timestamp": "2025-01-01T00:00:00+00:00"}\n'
            "invalid json\n"
            '{"another": "valid", "timestamp": "2025-01-01T01:00:00+00:00"}\n'
        )

        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            result = load_operations()
            assert len(result) == 2

    def test_filter_by_since(self, tmp_path):
        """Filter entries by since parameter."""
        log_file = tmp_path / "api-operations.jsonl"
        now = datetime.now(UTC)
        old_entry = {
            "type": "gh",
            "operation": "old",
            "timestamp": (now - timedelta(days=10)).isoformat(),
        }
        new_entry = {
            "type": "gh",
            "operation": "new",
            "timestamp": (now - timedelta(hours=1)).isoformat(),
        }
        log_file.write_text(json.dumps(old_entry) + "\n" + json.dumps(new_entry))

        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            result = load_operations(since=timedelta(days=1))
            assert len(result) == 1
            assert result[0]["operation"] == "new"

    def test_handle_empty_lines(self, tmp_path):
        """Skip empty lines."""
        log_file = tmp_path / "api-operations.jsonl"
        log_file.write_text(
            '{"type": "gh", "timestamp": "2025-01-01T00:00:00+00:00"}\n'
            "\n"
            "   \n"
            '{"type": "git", "timestamp": "2025-01-01T01:00:00+00:00"}\n'
        )

        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            result = load_operations()
            assert len(result) == 2

    def test_handle_timezone_naive_timestamps(self, tmp_path):
        """Handle timezone-naive timestamps."""
        log_file = tmp_path / "api-operations.jsonl"
        now = datetime.now(UTC)
        entry = {
            "type": "gh",
            "operation": "test",
            "timestamp": now.replace(tzinfo=None).isoformat(),  # No timezone
        }
        log_file.write_text(json.dumps(entry))

        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            result = load_operations(since=timedelta(hours=1))
            assert len(result) == 1


class TestCmdSummary:
    """Tests for cmd_summary command."""

    def test_no_operations(self, tmp_path, capsys):
        """Handle no operations."""
        log_file = tmp_path / "api-operations.jsonl"
        log_file.touch()

        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            args = argparse.Namespace(since="1d")
            cmd_summary(args)
            output = capsys.readouterr().out
            assert "No operations found" in output

    def test_summary_output(self, tmp_path, capsys):
        """Generate summary output."""
        log_file = tmp_path / "api-operations.jsonl"
        now = datetime.now(UTC)
        entries = [
            {
                "type": "gh",
                "operation": "pr_view",
                "success": True,
                "duration_ms": 1000,
                "session_id": "session1",
                "timestamp": now.isoformat(),
            },
            {
                "type": "gh",
                "operation": "pr_create",
                "success": True,
                "duration_ms": 2000,
                "session_id": "session1",
                "timestamp": now.isoformat(),
            },
            {
                "type": "git",
                "operation": "push",
                "success": False,
                "duration_ms": 500,
                "session_id": "session2",
                "timestamp": now.isoformat(),
            },
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries))

        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            args = argparse.Namespace(since="1d")
            cmd_summary(args)
            output = capsys.readouterr().out

            assert "Total operations: 3" in output
            assert "Success rate: 2/3" in output
            assert "By Type" in output
            assert "gh: 2" in output
            assert "git: 1" in output
            assert "Unique sessions: 2" in output


class TestCmdErrors:
    """Tests for cmd_errors command."""

    def test_no_errors(self, tmp_path, capsys):
        """Handle no errors."""
        log_file = tmp_path / "api-operations.jsonl"
        now = datetime.now(UTC)
        entry = {
            "type": "gh",
            "operation": "pr_view",
            "success": True,
            "timestamp": now.isoformat(),
        }
        log_file.write_text(json.dumps(entry))

        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            args = argparse.Namespace(since="7d")
            cmd_errors(args)
            output = capsys.readouterr().out
            assert "No failed operations found" in output

    def test_error_output(self, tmp_path, capsys):
        """Show error summary."""
        log_file = tmp_path / "api-operations.jsonl"
        now = datetime.now(UTC)
        entries = [
            {
                "type": "gh",
                "operation": "pr_merge",
                "success": False,
                "exit_code": 1,
                "timestamp": now.isoformat(),
            },
            {
                "type": "git",
                "operation": "push",
                "success": False,
                "exit_code": 128,
                "timestamp": now.isoformat(),
            },
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries))

        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            args = argparse.Namespace(since="7d")
            cmd_errors(args)
            output = capsys.readouterr().out

            assert "Failed Operations" in output
            assert "pr_merge" in output
            assert "push" in output
            assert "Total: 2 failed operations" in output


class TestCmdDurationStats:
    """Tests for cmd_duration_stats command."""

    def test_no_duration_data(self, tmp_path, capsys):
        """Handle no duration data."""
        log_file = tmp_path / "api-operations.jsonl"
        now = datetime.now(UTC)
        entry = {"type": "gh", "operation": "pr_view", "timestamp": now.isoformat()}
        log_file.write_text(json.dumps(entry))

        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            args = argparse.Namespace(since="7d")
            cmd_duration_stats(args)
            output = capsys.readouterr().out
            assert "No duration data available" in output

    def test_duration_stats_output(self, tmp_path, capsys):
        """Show duration statistics."""
        log_file = tmp_path / "api-operations.jsonl"
        now = datetime.now(UTC)
        entries = [
            {
                "type": "gh",
                "operation": "pr_view",
                "duration_ms": 1000,
                "timestamp": now.isoformat(),
            },
            {
                "type": "gh",
                "operation": "pr_view",
                "duration_ms": 2000,
                "timestamp": now.isoformat(),
            },
            {
                "type": "gh",
                "operation": "pr_view",
                "duration_ms": 3000,
                "timestamp": now.isoformat(),
            },
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries))

        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            args = argparse.Namespace(since="7d")
            cmd_duration_stats(args)
            output = capsys.readouterr().out

            assert "Duration Statistics" in output
            assert "gh:pr_view" in output
            assert "3" in output  # Count
            assert "2.0s" in output  # Average (2000ms)


class TestEdgeCases:
    """Tests for edge cases."""

    def test_malformed_timestamp(self, tmp_path):
        """Handle malformed timestamps gracefully."""
        log_file = tmp_path / "api-operations.jsonl"
        log_file.write_text('{"type": "gh", "operation": "test", "timestamp": "invalid"}\n')

        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            # Should not raise, just skip invalid entries when filtering
            result = load_operations(since=timedelta(days=1))
            assert result == []

    def test_missing_fields(self, tmp_path, capsys):
        """Handle entries with missing fields."""
        log_file = tmp_path / "api-operations.jsonl"
        now = datetime.now(UTC)
        entries = [
            {"timestamp": now.isoformat()},  # Missing type and operation
            {"type": "gh", "timestamp": now.isoformat()},  # Missing operation
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries))

        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            args = argparse.Namespace(since="1d")
            # Should not raise
            cmd_summary(args)
            output = capsys.readouterr().out
            assert "Total operations: 2" in output

    def test_very_large_duration(self, tmp_path, capsys):
        """Handle very large durations."""
        log_file = tmp_path / "api-operations.jsonl"
        now = datetime.now(UTC)
        entry = {
            "type": "gh",
            "operation": "slow_op",
            "duration_ms": 3600000,  # 1 hour
            "timestamp": now.isoformat(),
        }
        log_file.write_text(json.dumps(entry))

        with patch.object(analyze_api_operations, "API_OPERATIONS_LOG", log_file):
            args = argparse.Namespace(since="7d")
            cmd_duration_stats(args)
            output = capsys.readouterr().out
            assert "60.0m" in output
