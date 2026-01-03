#!/usr/bin/env python3
"""Tests for hook-effectiveness-evaluator.py."""

import importlib.util
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# hook-effectiveness-evaluator.py has hyphens, so we need dynamic import
HOOK_PATH = Path(__file__).parent.parent / "hook-effectiveness-evaluator.py"
_spec = importlib.util.spec_from_file_location("hook_effectiveness_evaluator", HOOK_PATH)
hook_module = importlib.util.module_from_spec(_spec)
sys.modules["hook_effectiveness_evaluator"] = hook_module
_spec.loader.exec_module(hook_module)


class TestLoadSessionLogs:
    """Tests for load_session_logs function."""

    def test_empty_log_entries(self):
        """Test with no log entries."""
        with patch.object(hook_module, "read_all_session_log_entries", return_value=[]):
            logs = hook_module.load_session_logs()
            assert logs == []

    def test_loads_recent_logs(self):
        """Test loading recent logs within session window."""
        now = datetime.now(UTC)
        recent_ts = (now - timedelta(minutes=30)).isoformat()
        old_ts = (now - timedelta(minutes=120)).isoformat()

        mock_entries = [
            {"timestamp": recent_ts, "hook": "test-hook", "decision": "approve"},
            {"timestamp": old_ts, "hook": "old-hook", "decision": "block"},
        ]

        with patch.object(hook_module, "read_all_session_log_entries", return_value=mock_entries):
            logs = hook_module.load_session_logs(session_window_minutes=60)
            assert len(logs) == 1
            assert logs[0]["hook"] == "test-hook"

    def test_handles_invalid_timestamps(self):
        """Test handling of entries with invalid timestamps."""
        now = datetime.now(UTC)
        recent_ts = now.isoformat()

        mock_entries = [
            {"timestamp": recent_ts, "hook": "valid-hook", "decision": "approve"},
            {"timestamp": "invalid", "hook": "bad-timestamp", "decision": "block"},
            {"timestamp": recent_ts, "hook": "another-hook", "decision": "block"},
        ]

        with patch.object(hook_module, "read_all_session_log_entries", return_value=mock_entries):
            logs = hook_module.load_session_logs(session_window_minutes=60)
            assert len(logs) == 2


class TestAnalyzeHookFrequency:
    """Tests for analyze_hook_frequency function."""

    def test_counts_decisions(self):
        """Test that decisions are counted correctly."""
        logs = [
            {"hook": "hook-a", "decision": "approve"},
            {"hook": "hook-a", "decision": "approve"},
            {"hook": "hook-a", "decision": "block", "reason": "blocked"},
            {"hook": "hook-b", "decision": "block", "reason": "also blocked"},
        ]

        stats = hook_module.analyze_hook_frequency(logs)

        assert stats["hook-a"]["total"] == 3
        assert stats["hook-a"]["approve"] == 2
        assert stats["hook-a"]["block"] == 1
        assert stats["hook-b"]["total"] == 1
        assert stats["hook-b"]["block"] == 1

    def test_collects_input_context(self):
        """Test that tool_names and input_previews are collected from details."""
        logs = [
            {
                "hook": "hook-a",
                "decision": "approve",
                "details": {"tool_name": "Bash", "input_preview": "gh pr list"},
            },
            {
                "hook": "hook-a",
                "decision": "approve",
                "details": {"tool_name": "Bash", "input_preview": "gh pr create"},
            },
            {
                "hook": "hook-a",
                "decision": "approve",
                "details": {"tool_name": "Edit", "input_preview": "/path/to/file.py"},
            },
        ]

        stats = hook_module.analyze_hook_frequency(logs)

        assert len(stats["hook-a"]["tool_names"]) == 3
        assert "Bash" in stats["hook-a"]["tool_names"]
        assert "Edit" in stats["hook-a"]["tool_names"]
        assert len(stats["hook-a"]["input_previews"]) == 3


class TestDetectOveractiveHooks:
    """Tests for detect_overactive_hooks function."""

    def test_detects_overactive(self):
        """Test detection of hooks with high approve ratio."""
        stats = {
            "overactive-hook": {"total": 15, "approve": 15, "block": 0, "reasons": []},
            "normal-hook": {"total": 5, "approve": 3, "block": 2, "reasons": []},
        }

        issues = hook_module.detect_overactive_hooks(stats)

        assert len(issues) == 1
        assert issues[0]["hook"] == "overactive-hook"
        assert issues[0]["type"] == "overactive"

    def test_ignores_low_frequency(self):
        """Test that low-frequency hooks are not flagged."""
        stats = {
            "low-freq-hook": {"total": 5, "approve": 5, "block": 0, "reasons": []},
        }

        issues = hook_module.detect_overactive_hooks(stats)
        assert len(issues) == 0

    def test_ignores_self_hook(self):
        """Test that this hook doesn't flag itself."""
        stats = {
            "hook-effectiveness-evaluator": {
                "total": 20,
                "approve": 20,
                "block": 0,
                "reasons": [],
            },
        }

        issues = hook_module.detect_overactive_hooks(stats)
        assert len(issues) == 0

    def test_suggestion_includes_tool_and_input_patterns(self):
        """Test that suggestion includes tool-specific insights when available."""
        stats = {
            "overactive-hook": {
                "total": 15,
                "approve": 15,
                "block": 0,
                "reasons": [],
                "tool_names": ["Bash", "Bash", "Bash", "Edit", "Edit"],
                "input_previews": ["gh pr list", "gh pr list", "gh pr create", "file.py"],
            },
        }

        issues = hook_module.detect_overactive_hooks(stats)

        assert len(issues) == 1
        suggestion = issues[0]["suggestion"]
        # Should mention the most common tool
        assert "Bash" in suggestion
        assert "3回" in suggestion
        # Should mention common input patterns
        assert "gh pr list" in suggestion


class TestDetectRepeatedBlocks:
    """Tests for detect_repeated_blocks function."""

    def test_detects_repeated_blocks(self):
        """Test detection of repeated blocks with same reason."""
        logs = [
            {"hook": "blocker", "decision": "block", "reason": "same reason"},
            {"hook": "blocker", "decision": "block", "reason": "same reason"},
            {"hook": "blocker", "decision": "block", "reason": "same reason"},
            {"hook": "other", "decision": "block", "reason": "different"},
        ]

        issues = hook_module.detect_repeated_blocks(logs)

        assert len(issues) == 1
        assert issues[0]["hook"] == "blocker"
        assert issues[0]["type"] == "repeated_block"
        assert issues[0]["count"] == 3


class TestDetectIgnoredWarnings:
    """Tests for detect_ignored_warnings function."""

    def test_detects_ignored_warnings(self):
        """Test detection of repeated warnings."""
        logs = [
            {"hook": "warner", "decision": "approve", "reason": "⚠️ warning message"},
            {"hook": "warner", "decision": "approve", "reason": "⚠️ warning message"},
            {"hook": "warner", "decision": "approve", "reason": "⚠️ warning message"},
            {"hook": "other", "decision": "approve", "reason": "just info"},
        ]

        issues = hook_module.detect_ignored_warnings(logs)

        assert len(issues) == 1
        assert issues[0]["hook"] == "warner"
        assert issues[0]["type"] == "ignored_warning"
        assert issues[0]["count"] == 3


class TestGenerateImprovementSuggestions:
    """Tests for generate_improvement_suggestions function."""

    def test_combines_all_issues(self):
        """Test that all issue types are combined."""
        logs = [
            # Overactive
            *[{"hook": "overactive", "decision": "approve"} for _ in range(12)],
            # Repeated blocks
            {"hook": "blocker", "decision": "block", "reason": "same"},
            {"hook": "blocker", "decision": "block", "reason": "same"},
            {"hook": "blocker", "decision": "block", "reason": "same"},
            # Ignored warnings
            {"hook": "warner", "decision": "approve", "reason": "⚠️ warn"},
            {"hook": "warner", "decision": "approve", "reason": "⚠️ warn"},
            {"hook": "warner", "decision": "approve", "reason": "⚠️ warn"},
        ]

        stats = hook_module.analyze_hook_frequency(logs)
        suggestions = hook_module.generate_improvement_suggestions(logs, stats)

        # Should have suggestions for all three issue types
        assert len(suggestions) >= 3
        assert any("[過剰発動]" in s for s in suggestions)
        assert any("[繰り返しブロック]" in s for s in suggestions)
        assert any("[無視された警告]" in s for s in suggestions)


class TestMain:
    """Integration tests for main function."""

    def test_no_logs_outputs_approve(self):
        """Test that main outputs approve decision when no logs exist."""
        captured_output = io.StringIO()

        with (
            patch.object(hook_module, "read_all_session_log_entries", return_value=[]),
            patch.object(hook_module, "log_hook_execution"),
            patch("sys.stdin", io.StringIO("{}")),
            patch("sys.stdout", captured_output),
        ):
            hook_module.main()

        output = captured_output.getvalue()
        result = json.loads(output)
        assert result["decision"] == "approve"

    def test_with_issues_outputs_suggestions(self):
        """Test that main outputs suggestions when issues are found."""
        now = datetime.now(UTC)
        recent_ts = now.isoformat()

        # Create overactive hook entries
        mock_entries = [
            {"timestamp": recent_ts, "hook": "overactive", "decision": "approve"} for _ in range(15)
        ]

        captured_output = io.StringIO()

        with (
            patch.object(hook_module, "read_all_session_log_entries", return_value=mock_entries),
            patch.object(hook_module, "log_hook_execution"),
            patch("sys.stdin", io.StringIO("{}")),
            patch("sys.stdout", captured_output),
        ):
            hook_module.main()

        output = captured_output.getvalue()
        result = json.loads(output)
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "フック有効性レビュー" in result["systemMessage"]

    def test_stop_hook_active_returns_immediately(self):
        """Test that stop_hook_active flag causes immediate approval."""
        captured_output = io.StringIO()

        with (
            patch("sys.stdin", io.StringIO('{"stop_hook_active": true}')),
            patch("sys.stdout", captured_output),
        ):
            hook_module.main()

        output = captured_output.getvalue()
        result = json.loads(output)
        # Issue #1607: Now uses print_continue_and_log_skip which returns {"continue": True}
        assert result.get("continue") is True or result.get("decision") == "approve"
