"""Tests for false-positive-detector.py hook."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

# Load the hook module directly
HOOK_PATH = Path(__file__).parent.parent / "false-positive-detector.py"
spec = importlib.util.spec_from_file_location("false_positive_detector", HOOK_PATH)
hook_module = importlib.util.module_from_spec(spec)
sys.modules["false_positive_detector"] = hook_module
spec.loader.exec_module(hook_module)

load_session_blocks = hook_module.load_session_blocks
parse_timestamp = hook_module.parse_timestamp
detect_consecutive_blocks = hook_module.detect_consecutive_blocks
format_warning_message = hook_module.format_warning_message


class TestParseTimestamp:
    """Tests for parse_timestamp function."""

    def test_iso_format_with_positive_timezone(self):
        """Parse ISO format with positive timezone offset."""
        ts = "2026-01-02T15:29:15.383537+09:00"
        result = parse_timestamp(ts)
        assert result is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 2
        assert result.hour == 15
        assert result.minute == 29
        assert result.second == 15

    def test_iso_format_with_negative_timezone(self):
        """Parse ISO format with negative timezone offset."""
        ts = "2026-01-02T06:29:15.383537-05:00"
        result = parse_timestamp(ts)
        assert result is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 2
        assert result.hour == 6
        assert result.minute == 29
        assert result.second == 15

    def test_iso_format_with_z(self):
        """Parse ISO format with Z suffix."""
        ts = "2026-01-02T06:29:15Z"
        result = parse_timestamp(ts)
        assert result is not None
        assert result.year == 2026

    def test_iso_format_without_timezone(self):
        """Parse ISO format without timezone."""
        ts = "2026-01-02T15:29:15.383537"
        result = parse_timestamp(ts)
        assert result is not None
        assert result.year == 2026

    def test_empty_string(self):
        """Empty string returns None."""
        assert parse_timestamp("") is None

    def test_none_input(self):
        """None input returns None."""
        assert parse_timestamp(None) is None

    def test_invalid_format(self):
        """Invalid format returns None."""
        assert parse_timestamp("not a timestamp") is None


class TestDetectConsecutiveBlocks:
    """Tests for detect_consecutive_blocks function."""

    def test_no_blocks(self):
        """Empty blocks list returns empty patterns."""
        result = detect_consecutive_blocks([])
        assert result == {}

    def test_single_block(self):
        """Single block cannot form a consecutive pattern."""
        blocks = [
            {"hook": "test-hook", "timestamp": "2026-01-02T15:29:15+09:00"},
        ]
        result = detect_consecutive_blocks(blocks)
        assert result == {}

    def test_consecutive_blocks_same_hook(self):
        """Two blocks within 30 seconds should be detected."""
        ts1 = "2026-01-02T15:29:15+09:00"
        ts2 = "2026-01-02T15:29:29+09:00"  # 14 seconds later
        blocks = [
            {"hook": "test-hook", "timestamp": ts1},
            {"hook": "test-hook", "timestamp": ts2},
        ]
        result = detect_consecutive_blocks(blocks)
        assert "test-hook" in result
        assert len(result["test-hook"]) == 1
        assert result["test-hook"][0] == (ts1, ts2)

    def test_blocks_too_far_apart(self):
        """Two blocks more than 30 seconds apart should not be detected."""
        ts1 = "2026-01-02T15:29:00+09:00"
        ts2 = "2026-01-02T15:29:45+09:00"  # 45 seconds later
        blocks = [
            {"hook": "test-hook", "timestamp": ts1},
            {"hook": "test-hook", "timestamp": ts2},
        ]
        result = detect_consecutive_blocks(blocks)
        assert result == {}

    def test_different_hooks(self):
        """Blocks from different hooks should not form patterns."""
        ts1 = "2026-01-02T15:29:15+09:00"
        ts2 = "2026-01-02T15:29:20+09:00"
        blocks = [
            {"hook": "hook-a", "timestamp": ts1},
            {"hook": "hook-b", "timestamp": ts2},
        ]
        result = detect_consecutive_blocks(blocks)
        assert result == {}

    def test_multiple_consecutive_pairs(self):
        """Multiple consecutive pairs should all be detected."""
        ts1 = "2026-01-02T15:29:00+09:00"
        ts2 = "2026-01-02T15:29:10+09:00"
        ts3 = "2026-01-02T15:29:20+09:00"
        blocks = [
            {"hook": "test-hook", "timestamp": ts1},
            {"hook": "test-hook", "timestamp": ts2},
            {"hook": "test-hook", "timestamp": ts3},
        ]
        result = detect_consecutive_blocks(blocks)
        assert "test-hook" in result
        # ts1-ts2, ts2-ts3 are both within 30 seconds
        assert len(result["test-hook"]) == 2

    def test_blocks_are_sorted(self):
        """Blocks should be sorted by timestamp before detection."""
        ts1 = "2026-01-02T15:29:00+09:00"
        ts2 = "2026-01-02T15:29:10+09:00"
        # Intentionally out of order
        blocks = [
            {"hook": "test-hook", "timestamp": ts2},
            {"hook": "test-hook", "timestamp": ts1},
        ]
        result = detect_consecutive_blocks(blocks)
        assert "test-hook" in result
        assert result["test-hook"][0] == (ts1, ts2)

    def test_blocks_without_timestamp(self):
        """Blocks without timestamp should be handled gracefully."""
        blocks = [
            {"hook": "test-hook", "timestamp": "2026-01-02T15:29:00+09:00"},
            {"hook": "test-hook"},  # No timestamp
        ]
        result = detect_consecutive_blocks(blocks)
        # Should not crash, and no pattern detected due to missing timestamp
        assert result == {}

    def test_blocks_without_hook(self):
        """Blocks without hook should be skipped."""
        blocks = [
            {"timestamp": "2026-01-02T15:29:00+09:00"},
            {"timestamp": "2026-01-02T15:29:10+09:00"},
        ]
        result = detect_consecutive_blocks(blocks)
        assert result == {}

    def test_mixed_timezone_aware_and_naive(self):
        """Mixed timezone-aware and naive timestamps should be handled gracefully."""
        # One with timezone, one without - could cause TypeError if not handled
        ts1 = "2026-01-02T15:29:00+09:00"  # timezone-aware
        ts2 = "2026-01-02T15:29:10"  # naive (no timezone)
        blocks = [
            {"hook": "test-hook", "timestamp": ts1},
            {"hook": "test-hook", "timestamp": ts2},
        ]
        result = detect_consecutive_blocks(blocks)
        # Should not crash, gracefully handle the mismatch
        # Result may be empty (skipped) or contain the pair (if both parsed to same type)
        assert isinstance(result, dict)


class TestFormatWarningMessage:
    """Tests for format_warning_message function."""

    def test_single_hook_single_pair(self):
        """Format message for single hook with single pair."""
        patterns = {
            "test-hook": [
                ("2026-01-02T15:29:15+09:00", "2026-01-02T15:29:29+09:00"),
            ],
        }
        result = format_warning_message(patterns)
        assert "test-hook" in result
        assert "連続ブロック: 1回" in result
        assert "gh issue create" in result

    def test_multiple_hooks(self):
        """Format message for multiple hooks."""
        patterns = {
            "hook-a": [
                ("2026-01-02T15:29:15+09:00", "2026-01-02T15:29:20+09:00"),
            ],
            "hook-b": [
                ("2026-01-02T15:30:00+09:00", "2026-01-02T15:30:10+09:00"),
            ],
        }
        result = format_warning_message(patterns)
        assert "hook-a" in result
        assert "hook-b" in result

    def test_more_than_three_pairs(self):
        """More than 3 pairs should show '他 N件'."""
        patterns = {
            "test-hook": [
                ("2026-01-02T15:29:00+09:00", "2026-01-02T15:29:05+09:00"),
                ("2026-01-02T15:29:10+09:00", "2026-01-02T15:29:15+09:00"),
                ("2026-01-02T15:29:20+09:00", "2026-01-02T15:29:25+09:00"),
                ("2026-01-02T15:29:30+09:00", "2026-01-02T15:29:35+09:00"),
                ("2026-01-02T15:29:40+09:00", "2026-01-02T15:29:45+09:00"),
            ],
        }
        result = format_warning_message(patterns)
        assert "連続ブロック: 5回" in result
        assert "他 2件" in result


class TestLoadSessionBlocks:
    """Tests for load_session_blocks function."""

    def test_nonexistent_file(self):
        """Nonexistent log file returns empty list."""
        result = load_session_blocks("nonexistent-session-id")
        assert result == []

    def test_load_blocks_from_file(self):
        """Load blocks from a valid log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            session_id = "test-session-123"
            log_file = log_dir / f"hook-execution-{session_id}.jsonl"

            # Create test log entries
            entries = [
                {"hook": "hook-a", "decision": "block", "timestamp": "2026-01-02T15:29:15+09:00"},
                {"hook": "hook-a", "decision": "approve", "timestamp": "2026-01-02T15:29:20+09:00"},
                {"hook": "hook-b", "decision": "block", "timestamp": "2026-01-02T15:29:25+09:00"},
            ]
            with open(log_file, "w") as f:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")

            # Temporarily override LOG_DIR
            original_log_dir = hook_module.LOG_DIR
            hook_module.LOG_DIR = log_dir
            try:
                result = load_session_blocks(session_id)
            finally:
                hook_module.LOG_DIR = original_log_dir

            # Should only return blocks (not approves)
            assert len(result) == 2
            assert all(b["decision"] == "block" for b in result)

    def test_malformed_json_lines(self):
        """Malformed JSON lines should be skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            session_id = "test-session-456"
            log_file = log_dir / f"hook-execution-{session_id}.jsonl"

            with open(log_file, "w") as f:
                f.write('{"hook": "hook-a", "decision": "block"}\n')
                f.write("not valid json\n")
                f.write('{"hook": "hook-b", "decision": "block"}\n')

            original_log_dir = hook_module.LOG_DIR
            hook_module.LOG_DIR = log_dir
            try:
                result = load_session_blocks(session_id)
            finally:
                hook_module.LOG_DIR = original_log_dir

            # Should skip the malformed line and return 2 blocks
            assert len(result) == 2


class TestHookIntegration:
    """Integration tests for the hook execution."""

    def _run_hook(self, stop_hook_input: str = "") -> tuple[int, str]:
        """Run the hook and return exit code and output."""
        import os
        import subprocess

        # Inherit current environment but override specific variables
        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = "/nonexistent"  # Use nonexistent dir for testing

        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input=stop_hook_input,
            capture_output=True,
            text=True,
            env=env,
        )
        return result.returncode, result.stdout

    def test_hook_approves_when_no_blocks(self):
        """Hook should approve when no blocks are found."""
        # Use empty input (Stop hook input)
        hook_input = json.dumps({"stop_hook_active": True})
        exit_code, output = self._run_hook(hook_input)
        assert exit_code == 0
        result = json.loads(output)
        assert result["decision"] == "approve"

    def test_hook_handles_parse_error(self):
        """Hook should approve on parse error (fail open)."""
        exit_code, output = self._run_hook("not valid json")
        assert exit_code == 0
        result = json.loads(output)
        assert result["decision"] == "approve"
