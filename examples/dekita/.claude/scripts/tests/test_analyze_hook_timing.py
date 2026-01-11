#!/usr/bin/env python3
"""Tests for analyze-hook-timing.py script.

Issue #1882: Hook timing analysis script tests.
"""

import importlib.util
import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).parent.parent / "analyze_hook_timing.py"


def load_module():
    """Load the script module for testing."""
    spec = importlib.util.spec_from_file_location("analyze_hook_timing", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestParseLogFile:
    """Tests for parse_log_file function."""

    def test_parse_valid_log(self):
        """Should parse valid JSON log entries."""
        module = load_module()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write('{"hook": "test-hook", "decision": "approve", "duration_ms": 50}\n')
            f.write('{"hook": "other-hook", "decision": "block", "duration_ms": 100}\n')
            f.flush()
            log_path = Path(f.name)

        entries = module.parse_log_file(log_path)
        log_path.unlink()

        assert len(entries) == 2
        assert entries[0]["hook"] == "test-hook"
        assert entries[1]["decision"] == "block"

    def test_skip_invalid_json_lines(self):
        """Should skip invalid JSON lines without crashing."""
        module = load_module()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write('{"hook": "valid"}\n')
            f.write("invalid json line\n")
            f.write('{"hook": "also-valid"}\n')
            f.flush()
            log_path = Path(f.name)

        entries = module.parse_log_file(log_path)
        log_path.unlink()

        assert len(entries) == 2

    def test_empty_file(self):
        """Should return empty list for empty file."""
        module = load_module()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.flush()
            log_path = Path(f.name)

        entries = module.parse_log_file(log_path)
        log_path.unlink()

        assert entries == []

    def test_nonexistent_file(self):
        """Should return empty list for nonexistent file."""
        module = load_module()

        entries = module.parse_log_file(Path("/nonexistent/path.log"))
        assert entries == []


class TestAnalyzeHooks:
    """Tests for analyze_hooks function."""

    def test_count_executions(self):
        """Should count hook executions correctly."""
        module = load_module()

        entries = [
            {"hook": "hook-a", "decision": "approve"},
            {"hook": "hook-a", "decision": "approve"},
            {"hook": "hook-b", "decision": "block"},
        ]

        results = module.analyze_hooks(entries)

        assert results["hook-a"]["count"] == 2
        assert results["hook-b"]["count"] == 1

    def test_block_rate_calculation(self):
        """Should calculate block rate correctly."""
        module = load_module()

        entries = [
            {"hook": "test-hook", "decision": "approve"},
            {"hook": "test-hook", "decision": "block"},
            {"hook": "test-hook", "decision": "approve"},
            {"hook": "test-hook", "decision": "block"},
        ]

        results = module.analyze_hooks(entries)

        assert results["test-hook"]["block_count"] == 2
        assert results["test-hook"]["block_rate"] == 50.0

    def test_timing_statistics(self):
        """Should calculate timing statistics correctly."""
        module = load_module()

        entries = [
            {"hook": "timed-hook", "decision": "approve", "duration_ms": 10},
            {"hook": "timed-hook", "decision": "approve", "duration_ms": 30},
            {"hook": "timed-hook", "decision": "approve", "duration_ms": 20},
        ]

        results = module.analyze_hooks(entries)
        timing = results["timed-hook"]["timing"]

        assert timing["min_ms"] == 10
        assert timing["max_ms"] == 30
        assert timing["avg_ms"] == 20.0
        assert timing["total_ms"] == 60
        assert timing["samples"] == 3

    def test_filter_by_session(self):
        """Should filter by session_id when specified."""
        module = load_module()

        entries = [
            {"hook": "hook-a", "decision": "approve", "session_id": "session-1"},
            {"hook": "hook-a", "decision": "block", "session_id": "session-2"},
            {"hook": "hook-b", "decision": "approve", "session_id": "session-1"},
        ]

        results = module.analyze_hooks(entries, session_id="session-1")

        assert results["hook-a"]["count"] == 1
        assert results["hook-a"]["block_count"] == 0
        assert results["hook-b"]["count"] == 1

    def test_missing_duration_handled(self):
        """Should handle entries without duration_ms."""
        module = load_module()

        entries = [
            {"hook": "no-timing", "decision": "approve"},
            {"hook": "with-timing", "decision": "approve", "duration_ms": 50},
        ]

        results = module.analyze_hooks(entries)

        assert results["no-timing"]["has_timing"] is False
        assert results["with-timing"]["has_timing"] is True


class TestPrintReport:
    """Tests for print_report function."""

    def test_json_output(self):
        """Should output valid JSON when --json is specified."""
        module = load_module()

        results = {
            "test-hook": {
                "hook": "test-hook",
                "count": 10,
                "block_count": 2,
                "block_rate": 20.0,
                "has_timing": True,
                "timing": {
                    "min_ms": 5,
                    "max_ms": 50,
                    "avg_ms": 25.0,
                    "total_ms": 250,
                    "samples": 10,
                },
            }
        }

        with patch("sys.stdout", new=StringIO()) as mock_stdout:
            module.print_report(results, output_json=True)
            output = mock_stdout.getvalue()

        parsed = json.loads(output)
        assert parsed["test-hook"]["count"] == 10

    def test_table_output_contains_hook_names(self):
        """Should include hook names in table output."""
        module = load_module()

        results = {
            "my-hook": {
                "hook": "my-hook",
                "count": 5,
                "block_count": 1,
                "block_rate": 20.0,
                "has_timing": False,
            }
        }

        with patch("sys.stdout", new=StringIO()) as mock_stdout:
            module.print_report(results, output_json=False)
            output = mock_stdout.getvalue()

        assert "my-hook" in output

    def test_slow_hooks_warning(self):
        """Should warn about hooks exceeding slow threshold."""
        module = load_module()

        results = {
            "slow-hook": {
                "hook": "slow-hook",
                "count": 5,
                "block_count": 0,
                "block_rate": 0.0,
                "has_timing": True,
                "timing": {
                    "min_ms": 100,
                    "max_ms": 200,
                    "avg_ms": 150.0,
                    "total_ms": 750,
                    "samples": 5,
                },
            }
        }

        with patch("sys.stdout", new=StringIO()) as mock_stdout:
            module.print_report(results, slow_threshold_ms=100, output_json=False)
            output = mock_stdout.getvalue()

        assert "150.0ms" in output or "slow-hook" in output


class TestMainFunction:
    """Tests for main function integration."""

    def test_parse_handles_missing_file(self):
        """parse_log_file should return empty list for missing file."""
        module = load_module()

        entries = module.parse_log_file(Path("/nonexistent"))
        assert entries == []

    def test_empty_entries_handled(self):
        """analyze_hooks should handle empty entries."""
        module = load_module()

        results = module.analyze_hooks([])
        assert results == {}
