#!/usr/bin/env python3
"""Unit tests for ci_monitor.validation module."""

import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add scripts directory to path so we can import ci_monitor package
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the ci_monitor package for attribute access (Issue #2624)
import ci_monitor

# Import directly from ci_monitor package (Issue #2624)
from ci_monitor import (
    validate_pr_number,
    validate_pr_numbers,
)


class TestValidatePrNumber:
    """Tests for validate_pr_number function."""

    def test_valid_pr_number(self):
        """Test valid PR number."""
        is_valid, error = validate_pr_number("123")
        assert is_valid
        assert error == ""

    def test_valid_pr_number_large(self):
        """Test valid large PR number."""
        is_valid, error = validate_pr_number("999999")
        assert is_valid
        assert error == ""

    def test_invalid_pr_number_not_integer(self):
        """Test invalid PR number (not an integer)."""
        is_valid, error = validate_pr_number("abc")
        assert not is_valid
        assert "must be a positive integer" in error

    def test_invalid_pr_number_negative(self):
        """Test invalid PR number (negative)."""
        is_valid, error = validate_pr_number("-1")
        assert not is_valid
        assert "must be a positive integer" in error

    def test_invalid_pr_number_zero(self):
        """Test invalid PR number (zero)."""
        is_valid, error = validate_pr_number("0")
        assert not is_valid
        assert "must be a positive integer" in error

    def test_invalid_pr_number_too_large(self):
        """Test invalid PR number (too large)."""
        is_valid, error = validate_pr_number("1000000")
        assert not is_valid
        assert "value too large" in error

    def test_invalid_pr_number_float(self):
        """Test invalid PR number (float)."""
        is_valid, error = validate_pr_number("12.34")
        assert not is_valid
        assert "must be a positive integer" in error

    def test_invalid_pr_number_empty(self):
        """Test invalid PR number (empty string)."""
        is_valid, error = validate_pr_number("")
        assert not is_valid
        assert "must be a positive integer" in error


class TestValidatePrNumbers:
    """Tests for validate_pr_numbers function."""

    def test_valid_single_pr(self):
        """Test validation of single valid PR number."""
        result = validate_pr_numbers(["123"])
        assert result == ["123"]

    def test_valid_multiple_prs(self):
        """Test validation of multiple valid PR numbers."""
        result = validate_pr_numbers(["123", "456", "789"])
        assert result == ["123", "456", "789"]

    def test_invalid_pr_exits(self):
        """Test that invalid PR number causes sys.exit with error message."""
        captured_stderr = io.StringIO()
        with patch("sys.stderr", captured_stderr):
            with pytest.raises(SystemExit) as cm:
                validate_pr_numbers(["abc"])
        assert cm.value.code == 1
        assert "Invalid PR number" in captured_stderr.getvalue()

    def test_mixed_valid_invalid_exits(self):
        """Test that mix of valid and invalid PR numbers causes sys.exit."""
        captured_stderr = io.StringIO()
        with patch("sys.stderr", captured_stderr):
            with pytest.raises(SystemExit) as cm:
                validate_pr_numbers(["123", "abc", "456"])
        assert cm.value.code == 1
        assert "Invalid PR number" in captured_stderr.getvalue()

    def test_empty_list(self):
        """Test that empty list returns empty list without error."""
        result = validate_pr_numbers([])
        assert result == []

    # Issue #2454: test_status_display_with_rate_limit removed - --status mode was removed


class TestSanitizeForLog:
    """Tests for _sanitize_for_log function (Issue #1411)."""

    def test_sanitizes_string_control_characters(self):
        """Test that control characters are removed from strings."""
        # Null, newline, carriage return, escape
        result = ci_monitor._sanitize_for_log("test\x00\n\r\x1bvalue")
        assert result == "testvalue"

    def test_preserves_tab_character(self):
        """Test that tab character is preserved."""
        result = ci_monitor._sanitize_for_log("test\tvalue")
        assert result == "test\tvalue"

    def test_preserves_normal_string(self):
        """Test that normal strings are unchanged."""
        result = ci_monitor._sanitize_for_log("normal string 123")
        assert result == "normal string 123"

    def test_sanitizes_list_elements(self):
        """Test that list elements are sanitized recursively."""
        result = ci_monitor._sanitize_for_log(["test\x00", "normal", "value\n"])
        assert result == ["test", "normal", "value"]

    def test_sanitizes_dict_values(self):
        """Test that dict values are sanitized recursively."""
        result = ci_monitor._sanitize_for_log({"key": "value\x00", "nested": {"inner": "test\n"}})
        assert result == {"key": "value", "nested": {"inner": "test"}}

    def test_preserves_non_string_types(self):
        """Test that non-string types are returned as-is."""
        assert ci_monitor._sanitize_for_log(123) == 123
        assert ci_monitor._sanitize_for_log(3.14) == 3.14
        assert ci_monitor._sanitize_for_log(True) is True
        assert ci_monitor._sanitize_for_log(None) is None
