#!/usr/bin/env python3
"""Tests for analyze-false-positives.py"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

# Load module directly from file (handles hyphen in filename)
_script_path = Path(__file__).parents[1] / "analyze-false-positives.py"
_spec = importlib.util.spec_from_file_location("analyze_false_positives", _script_path)
afp = importlib.util.module_from_spec(_spec)
sys.modules["analyze_false_positives"] = afp
_spec.loader.exec_module(afp)


def test_extract_command_pattern_normalizes_pr_numbers():
    """Should normalize PR numbers."""
    cmd = "gh pr view 123 --json reviews"
    pattern = afp.extract_command_pattern(cmd)
    assert "<NUM>" in pattern
    assert "123" not in pattern


def test_extract_command_pattern_normalizes_branches():
    """Should normalize branch names."""
    cmd = "git checkout feat/issue-123-description"
    pattern = afp.extract_command_pattern(cmd)
    assert "<BRANCH>" in pattern


def test_extract_file_pattern_extracts_extension():
    """Should extract file extension."""
    path = "/Users/test/project/src/hooks/test.py"
    pattern = afp.extract_file_pattern(path)
    assert ".py" in pattern


def test_extract_file_pattern_handles_empty_path():
    """Should handle empty file path gracefully."""
    pattern = afp.extract_file_pattern("")
    assert pattern == "root/*.unknown"


def test_extract_file_pattern_handles_trailing_slash():
    """Should handle paths with trailing slashes."""
    pattern = afp.extract_file_pattern("/path/to/dir/")
    assert pattern == "root/*.unknown"  # No extension in dir name


def test_extract_file_pattern_handles_whitespace_only():
    """Should handle whitespace-only paths."""
    pattern = afp.extract_file_pattern("   ")
    assert pattern == "root/*.unknown"


def test_extract_command_pattern_normalizes_issue_refs():
    """Should normalize issue references like #123."""
    cmd = "gh issue view #456"
    pattern = afp.extract_command_pattern(cmd)
    assert "#<NUM>" in pattern
    assert "456" not in pattern


def test_extract_command_pattern_avoids_path_prefix_hashes():
    """Should not normalize # immediately after path separators."""
    # #123 after / should be preserved
    cmd = "cat /path/#123/file.txt"
    pattern = afp.extract_command_pattern(cmd)
    assert "#123" in pattern  # Preserved because preceded by /


def test_extract_file_pattern_extracts_key_dirs():
    """Should extract key directories."""
    path = "/project/.claude/hooks/test.py"
    pattern = afp.extract_file_pattern(path)
    assert "hooks" in pattern or ".claude" in pattern


def test_analyze_false_positives_empty():
    """Should return empty dict for no false positives."""
    evaluations = [
        {"evaluation": "valid", "hook": "test"},
    ]
    result = afp.analyze_false_positives(evaluations)
    assert result == {}


def test_analyze_false_positives_filters_by_hook():
    """Should filter by hook when specified."""
    evaluations = [
        {"evaluation": "false_positive", "hook": "hook-a", "original_block": {}},
        {"evaluation": "false_positive", "hook": "hook-b", "original_block": {}},
    ]
    result = afp.analyze_false_positives(evaluations, target_hook="hook-a")
    assert "hook-a" in result
    assert "hook-b" not in result


def test_analyze_false_positives_groups_by_hook():
    """Should group false positives by hook."""
    evaluations = [
        {"evaluation": "false_positive", "hook": "hook-a", "original_block": {}},
        {"evaluation": "false_positive", "hook": "hook-a", "original_block": {}},
        {"evaluation": "false_positive", "hook": "hook-b", "original_block": {}},
    ]
    result = afp.analyze_false_positives(evaluations)
    assert result["hook-a"]["total_fps"] == 2
    assert result["hook-b"]["total_fps"] == 1


def test_analyze_false_positives_extracts_command_patterns():
    """Should extract patterns from command details."""
    evaluations = [
        {
            "evaluation": "false_positive",
            "hook": "ci-wait-check",
            "original_block": {"details": {"command": "gh pr view 123 --json reviews"}},
        },
    ]
    result = afp.analyze_false_positives(evaluations)
    patterns = result["ci-wait-check"]["patterns"]
    # Should have at least one command pattern
    assert any(k.startswith("command:") for k in patterns)


def test_analyze_false_positives_collects_improvement_suggestions():
    """Should collect user improvement suggestions."""
    evaluations = [
        {
            "evaluation": "false_positive",
            "hook": "test-hook",
            "improvement_suggestion": "Add exception for X",
            "original_block": {},
        },
        {
            "evaluation": "false_positive",
            "hook": "test-hook",
            "improvement_suggestion": "",  # Empty should be filtered
            "original_block": {},
        },
    ]
    result = afp.analyze_false_positives(evaluations)
    suggestions = result["test-hook"]["improvement_suggestions"]
    assert "Add exception for X" in suggestions
    assert "" not in suggestions


def test_generate_recommendations_high_volume():
    """Should recommend refactoring for high FP count."""
    data = {
        "total_fps": 10,
        "patterns": {},
        "improvement_suggestions": [],
    }
    with patch.object(afp, "HOOKS_DIR", Path("/nonexistent")):
        recs = afp.generate_recommendations("test-hook", data)
    assert any("refactoring" in r.lower() for r in recs)


def test_generate_report_format():
    """Should generate readable report."""
    analysis = {
        "test-hook": {
            "total_fps": 3,
            "patterns": {"command:gh pr view": [{}]},
            "improvement_suggestions": ["Test suggestion"],
        }
    }
    report = afp.generate_improvement_report(analysis)
    assert "test-hook" in report
    assert "False Positives: 3" in report
    assert "Test suggestion" in report


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
