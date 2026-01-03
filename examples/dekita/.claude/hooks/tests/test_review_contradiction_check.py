"""Tests for review_contradiction_check module."""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from review_contradiction_check import (
    PROXIMITY_THRESHOLD,
    detect_potential_contradictions,
    format_contradiction_warnings,
)


class TestDetectPotentialContradictions:
    """Tests for detect_potential_contradictions function."""

    def test_detects_same_file_close_lines(self) -> None:
        """Should detect comments on same file within proximity threshold."""
        prev = [{"path": "src/app.py", "line": 100, "body": "Previous comment"}]
        new = [{"path": "src/app.py", "line": 105, "body": "New comment"}]

        warnings = detect_potential_contradictions(new, prev)

        assert len(warnings) == 1
        assert warnings[0]["file"] == "src/app.py"
        assert warnings[0]["prev_line"] == 100
        assert warnings[0]["new_line"] == 105

    def test_ignores_different_files(self) -> None:
        """Should NOT detect comments on different files."""
        prev = [{"path": "src/app.py", "line": 100, "body": "Previous comment"}]
        new = [{"path": "src/other.py", "line": 100, "body": "New comment"}]

        warnings = detect_potential_contradictions(new, prev)

        assert len(warnings) == 0

    def test_ignores_distant_lines(self) -> None:
        """Should NOT detect comments far apart (>= PROXIMITY_THRESHOLD)."""
        prev = [{"path": "src/app.py", "line": 100, "body": "Previous comment"}]
        new = [{"path": "src/app.py", "line": 100 + PROXIMITY_THRESHOLD, "body": "New comment"}]

        warnings = detect_potential_contradictions(new, prev)

        assert len(warnings) == 0

    def test_detects_at_threshold_boundary(self) -> None:
        """Should detect comments at threshold - 1 distance."""
        prev = [{"path": "src/app.py", "line": 100, "body": "Previous comment"}]
        new = [{"path": "src/app.py", "line": 100 + PROXIMITY_THRESHOLD - 1, "body": "New comment"}]

        warnings = detect_potential_contradictions(new, prev)

        assert len(warnings) == 1

    def test_handles_multiple_comments(self) -> None:
        """Should detect multiple potential contradictions."""
        prev = [
            {"path": "src/app.py", "line": 100, "body": "First previous"},
            {"path": "src/app.py", "line": 200, "body": "Second previous"},
        ]
        new = [
            {"path": "src/app.py", "line": 102, "body": "Close to first"},
            {"path": "src/app.py", "line": 205, "body": "Close to second"},
        ]

        warnings = detect_potential_contradictions(new, prev)

        assert len(warnings) == 2

    def test_truncates_long_body(self) -> None:
        """Should truncate comment body to 100 characters."""
        long_body = "x" * 200
        prev = [{"path": "src/app.py", "line": 100, "body": long_body}]
        new = [{"path": "src/app.py", "line": 105, "body": long_body}]

        warnings = detect_potential_contradictions(new, prev)

        assert len(warnings[0]["prev_body"]) == 100
        assert len(warnings[0]["new_body"]) == 100

    def test_handles_missing_path(self) -> None:
        """Should skip comments without path."""
        prev = [{"path": "src/app.py", "line": 100, "body": "Previous"}]
        new = [{"line": 100, "body": "No path"}]  # Missing path

        warnings = detect_potential_contradictions(new, prev)

        assert len(warnings) == 0

    def test_handles_missing_line(self) -> None:
        """Should skip comments without line number."""
        prev = [{"path": "src/app.py", "line": 100, "body": "Previous"}]
        new = [{"path": "src/app.py", "body": "No line"}]  # Missing line

        warnings = detect_potential_contradictions(new, prev)

        assert len(warnings) == 0

    def test_handles_empty_lists(self) -> None:
        """Should handle empty comment lists."""
        assert detect_potential_contradictions([], []) == []
        assert detect_potential_contradictions([{"path": "a.py", "line": 1, "body": "x"}], []) == []
        assert detect_potential_contradictions([], [{"path": "a.py", "line": 1, "body": "x"}]) == []

    def test_handles_missing_body(self) -> None:
        """Should handle comments without body field."""
        prev = [{"path": "src/app.py", "line": 100}]  # Missing body
        new = [{"path": "src/app.py", "line": 105}]  # Missing body

        warnings = detect_potential_contradictions(new, prev)

        assert len(warnings) == 1
        assert warnings[0]["prev_body"] == ""
        assert warnings[0]["new_body"] == ""

    def test_same_line_detected(self) -> None:
        """Should detect comments on exact same line."""
        prev = [{"path": "src/app.py", "line": 100, "body": "Previous"}]
        new = [{"path": "src/app.py", "line": 100, "body": "New on same line"}]

        warnings = detect_potential_contradictions(new, prev)

        assert len(warnings) == 1
        assert warnings[0]["prev_line"] == 100
        assert warnings[0]["new_line"] == 100


class TestFormatContradictionWarnings:
    """Tests for format_contradiction_warnings function."""

    def test_formats_single_warning(self) -> None:
        """Should format a single warning correctly."""
        warnings = [
            {
                "file": "src/app.py",
                "prev_line": 100,
                "new_line": 105,
                "prev_body": "Previous comment",
                "new_body": "New comment",
            }
        ]

        result = format_contradiction_warnings(warnings)

        assert "⚠️" in result
        assert "src/app.py" in result
        assert "line 100" in result
        assert "line 105" in result
        assert "Previous comment" in result
        assert "New comment" in result
        assert "矛盾の可能性あり" in result

    def test_returns_empty_for_no_warnings(self) -> None:
        """Should return empty string when no warnings."""
        result = format_contradiction_warnings([])

        assert result == ""

    def test_formats_multiple_warnings(self) -> None:
        """Should format multiple warnings."""
        warnings = [
            {
                "file": "src/app.py",
                "prev_line": 100,
                "new_line": 105,
                "prev_body": "First prev",
                "new_body": "First new",
            },
            {
                "file": "src/other.py",
                "prev_line": 200,
                "new_line": 205,
                "prev_body": "Second prev",
                "new_body": "Second new",
            },
        ]

        result = format_contradiction_warnings(warnings)

        assert "src/app.py" in result
        assert "src/other.py" in result
        assert result.count("矛盾の可能性あり") == 2

    def test_ellipsis_only_for_truncated_body(self) -> None:
        """Should only add ellipsis when body was actually truncated."""
        short_body = "Short comment"  # Less than 100 chars
        truncated_body = "x" * 100  # Truncated from > 100 chars
        exact_100_body = "y" * 100  # Exactly 100 chars, not truncated
        warnings = [
            {
                "file": "src/app.py",
                "prev_line": 100,
                "new_line": 105,
                "prev_body": short_body,
                "new_body": truncated_body,
                "prev_truncated": False,
                "new_truncated": True,  # Was truncated
            },
            {
                "file": "src/other.py",
                "prev_line": 200,
                "new_line": 205,
                "prev_body": exact_100_body,
                "new_body": short_body,
                "prev_truncated": False,  # Not truncated, just exactly 100 chars
                "new_truncated": False,
            },
        ]

        result = format_contradiction_warnings(warnings)

        # Short body should NOT have ellipsis
        assert f'"{short_body}"' in result
        # Truncated body should have ellipsis
        assert f'"{truncated_body}..."' in result
        # Exactly 100 chars but not truncated should NOT have ellipsis
        assert f'"{exact_100_body}"' in result
        assert f'"{exact_100_body}..."' not in result

    def test_detect_sets_truncation_flags(self) -> None:
        """Should set truncation flags when body exceeds 100 chars."""
        short_body = "Short"
        long_body = "x" * 200  # Will be truncated to 100
        prev = [{"path": "src/app.py", "line": 100, "body": short_body}]
        new = [{"path": "src/app.py", "line": 105, "body": long_body}]

        warnings = detect_potential_contradictions(new, prev)

        assert len(warnings) == 1
        assert warnings[0]["prev_truncated"] is False
        assert warnings[0]["new_truncated"] is True
        assert len(warnings[0]["new_body"]) == 100
