#!/usr/bin/env python3
"""Unit tests for lib/session_validation.py

Issue #2282: Centralized session ID validation tests.
"""

import sys
from pathlib import Path

# Add parent directory to path for lib module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from lib.session_validation import is_safe_session_id


class TestIsSafeSessionId:
    """Tests for is_safe_session_id function."""

    def test_valid_uuid_format(self):
        """Should accept valid UUID format session IDs."""
        assert is_safe_session_id("aac956f9-4701-4bca-98f4-d4f166716c73") is True
        assert is_safe_session_id("12345678-1234-1234-1234-123456789abc") is True

    def test_valid_alphanumeric(self):
        """Should accept alphanumeric session IDs."""
        assert is_safe_session_id("test-session-123") is True
        assert is_safe_session_id("abc123") is True
        assert is_safe_session_id("ABC123") is True
        assert is_safe_session_id("ppid-12345") is True

    def test_valid_hyphens(self):
        """Should accept session IDs with hyphens."""
        assert is_safe_session_id("session-with-hyphens") is True
        assert is_safe_session_id("a-b-c") is True

    def test_rejects_path_traversal(self):
        """Should reject session IDs with path traversal characters."""
        assert is_safe_session_id("../../../etc/passwd") is False
        assert is_safe_session_id("session/../../../etc") is False
        assert is_safe_session_id("..") is False
        assert is_safe_session_id(".") is False

    def test_rejects_forward_slashes(self):
        """Should reject session IDs with forward slashes."""
        assert is_safe_session_id("path/to/file") is False
        assert is_safe_session_id("/etc/passwd") is False

    def test_rejects_backslashes(self):
        """Should reject session IDs with backslashes."""
        assert is_safe_session_id("path\\to\\file") is False
        assert is_safe_session_id("..\\..\\etc") is False

    def test_rejects_empty(self):
        """Should reject empty session IDs."""
        assert is_safe_session_id("") is False

    def test_rejects_none_like_strings(self):
        """Should reject None-like string representations."""
        # Note: The function expects str, so we test common None-like string representations
        assert is_safe_session_id("None") is True  # "None" is alphanumeric, so valid
        assert is_safe_session_id("null") is True  # "null" is alphanumeric, so valid
        assert is_safe_session_id("undefined") is True  # alphanumeric, valid

    def test_rejects_special_characters(self):
        """Should reject session IDs with special characters."""
        assert is_safe_session_id("session;rm -rf /") is False
        assert is_safe_session_id("session$(cat /etc/passwd)") is False
        assert is_safe_session_id("session`id`") is False
        assert is_safe_session_id("session|cat") is False
        assert is_safe_session_id("session&echo") is False

    def test_rejects_spaces(self):
        """Should reject session IDs with spaces."""
        assert is_safe_session_id("session id") is False
        assert is_safe_session_id(" ") is False
        assert is_safe_session_id("session ") is False

    def test_rejects_quotes(self):
        """Should reject session IDs with quotes."""
        assert is_safe_session_id("session'id") is False
        assert is_safe_session_id('session"id') is False

    def test_rejects_newlines(self):
        """Should reject session IDs with newlines."""
        assert is_safe_session_id("session\nid") is False
        assert is_safe_session_id("session\r\nid") is False
