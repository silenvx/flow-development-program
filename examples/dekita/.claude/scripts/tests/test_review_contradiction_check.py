"""Tests for review_contradiction_check.py."""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from review_contradiction_check import (
    PROXIMITY_THRESHOLD,
    _detect_within_batch,
    detect_potential_contradictions,
    format_contradiction_warnings,
)


class TestDetectPotentialContradictions:
    """Tests for detect_potential_contradictions function."""

    def test_detects_contradiction_between_batches(self):
        """Detects contradictions between new and previous comments."""
        new_comments = [
            {"path": "test.py", "line": 15, "body": "Use consistent naming"},
        ]
        previous_comments = [
            {"path": "test.py", "line": 10, "body": "Previous suggestion"},
        ]

        result = detect_potential_contradictions(new_comments, previous_comments)

        assert len(result) == 1
        assert result[0]["file"] == "test.py"
        assert result[0]["prev_line"] == 10
        assert result[0]["new_line"] == 15
        assert result[0]["same_batch"] is False

    def test_no_contradiction_when_different_files(self):
        """No contradiction when comments are on different files."""
        new_comments = [{"path": "a.py", "line": 10, "body": "Comment A"}]
        previous_comments = [{"path": "b.py", "line": 10, "body": "Comment B"}]

        result = detect_potential_contradictions(new_comments, previous_comments)

        assert len(result) == 0

    def test_no_contradiction_when_lines_far_apart(self):
        """No contradiction when lines are far apart."""
        new_comments = [{"path": "test.py", "line": 100, "body": "Comment A"}]
        previous_comments = [{"path": "test.py", "line": 10, "body": "Comment B"}]

        result = detect_potential_contradictions(new_comments, previous_comments)

        assert len(result) == 0

    def test_uses_proximity_threshold(self):
        """Uses PROXIMITY_THRESHOLD constant correctly."""
        new_comments = [{"path": "test.py", "line": PROXIMITY_THRESHOLD - 1, "body": "A"}]
        previous_comments = [{"path": "test.py", "line": 0, "body": "B"}]

        result = detect_potential_contradictions(new_comments, previous_comments)
        assert len(result) == 1

        # Exactly at threshold should not trigger
        new_comments = [{"path": "test.py", "line": PROXIMITY_THRESHOLD, "body": "A"}]
        result = detect_potential_contradictions(new_comments, previous_comments)
        assert len(result) == 0

    def test_truncates_long_bodies(self):
        """Truncates comment bodies longer than 100 chars."""
        long_body = "x" * 150
        new_comments = [{"path": "test.py", "line": 10, "body": long_body}]
        previous_comments = [{"path": "test.py", "line": 5, "body": "short"}]

        result = detect_potential_contradictions(new_comments, previous_comments)

        assert len(result[0]["new_body"]) == 100
        assert result[0]["new_truncated"] is True
        assert result[0]["prev_truncated"] is False


class TestDetectWithinBatch:
    """Tests for _detect_within_batch function (Issue #1596)."""

    def test_detects_proximity_within_batch(self):
        """Detects proximity when previous_comments is empty."""
        new_comments = [
            {"path": "test.py", "line": 10, "body": "First comment"},
            {"path": "test.py", "line": 15, "body": "Second comment"},
        ]

        result = detect_potential_contradictions(new_comments, [])

        assert len(result) == 1
        assert result[0]["file"] == "test.py"
        assert result[0]["same_batch"] is True

    def test_no_duplication_in_batch(self):
        """Each pair is only reported once."""
        comments = [
            {"path": "test.py", "line": 10, "body": "A"},
            {"path": "test.py", "line": 15, "body": "B"},
        ]

        result = _detect_within_batch(comments)

        # Only one warning, not two (A-B and B-A)
        assert len(result) == 1

    def test_multiple_pairs_in_batch(self):
        """Detects multiple pairs of close comments."""
        comments = [
            {"path": "test.py", "line": 10, "body": "A"},
            {"path": "test.py", "line": 15, "body": "B"},
            {"path": "test.py", "line": 18, "body": "C"},  # Close to B
        ]

        result = _detect_within_batch(comments)

        # A-B and B-C (and possibly A-C if within threshold)
        assert len(result) >= 2

    def test_ignores_different_files_in_batch(self):
        """Ignores comments on different files within batch."""
        comments = [
            {"path": "a.py", "line": 10, "body": "A"},
            {"path": "b.py", "line": 12, "body": "B"},
        ]

        result = _detect_within_batch(comments)

        assert len(result) == 0

    def test_handles_missing_line_numbers(self):
        """Handles comments without line numbers."""
        comments = [
            {"path": "test.py", "line": 10, "body": "A"},
            {"path": "test.py", "line": None, "body": "B"},
        ]

        result = _detect_within_batch(comments)

        assert len(result) == 0

    def test_detects_both_within_batch_and_cross_batch(self):
        """Detects within-batch even when previous_comments exists (Issue #1596)."""
        # New comments have internal proximity (same-batch detection)
        new_comments = [
            {"path": "test.py", "line": 10, "body": "First new"},
            {"path": "test.py", "line": 15, "body": "Second new"},
        ]
        # Previous comments exist (cross-batch detection also applies)
        previous_comments = [
            {"path": "test.py", "line": 12, "body": "Previous comment"},
        ]

        result = detect_potential_contradictions(new_comments, previous_comments)

        # Should have 3 warnings:
        # - same_batch=True: new(10) <-> new(15)
        # - same_batch=False: new(10) <-> prev(12)
        # - same_batch=False: new(15) <-> prev(12)
        same_batch_warnings = [w for w in result if w["same_batch"] is True]
        cross_batch_warnings = [w for w in result if w["same_batch"] is False]

        assert len(same_batch_warnings) == 1
        assert len(cross_batch_warnings) == 2
        assert len(result) == 3


class TestFormatContradictionWarnings:
    """Tests for format_contradiction_warnings function."""

    def test_empty_warnings(self):
        """Returns empty string for no warnings."""
        assert format_contradiction_warnings([]) == ""

    def test_formats_cross_batch_warning(self):
        """Formats cross-batch warnings correctly."""
        warnings = [
            {
                "file": "test.py",
                "prev_line": 10,
                "new_line": 15,
                "prev_body": "Previous",
                "new_body": "New",
                "prev_truncated": False,
                "new_truncated": False,
                "same_batch": False,
            }
        ]

        result = format_contradiction_warnings(warnings)

        assert "test.py" in result
        assert "前回指摘" in result
        assert "今回指摘" in result
        assert "矛盾の可能性あり" in result

    def test_formats_same_batch_warning(self):
        """Formats same-batch warnings correctly (Issue #1596)."""
        warnings = [
            {
                "file": "test.py",
                "prev_line": 10,
                "new_line": 15,
                "prev_body": "First",
                "new_body": "Second",
                "prev_truncated": False,
                "new_truncated": False,
                "same_batch": True,
            }
        ]

        result = format_contradiction_warnings(warnings)

        assert "test.py" in result
        assert "指摘1" in result
        assert "指摘2" in result
        assert "同一バッチ内" in result

    def test_adds_ellipsis_for_truncated(self):
        """Adds ellipsis for truncated bodies."""
        warnings = [
            {
                "file": "test.py",
                "prev_line": 10,
                "new_line": 15,
                "prev_body": "x" * 100,
                "new_body": "Short",
                "prev_truncated": True,
                "new_truncated": False,
                "same_batch": False,
            }
        ]

        result = format_contradiction_warnings(warnings)

        assert "..." in result
