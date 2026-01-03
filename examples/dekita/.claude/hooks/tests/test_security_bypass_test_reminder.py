#!/usr/bin/env python3
"""Tests for security_bypass_test_reminder.py hook."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import security_bypass_test_reminder as hook


class TestIsSecurityGuardFile:
    """Tests for is_security_guard_file function."""

    def test_guard_file(self):
        """Should detect *_guard.py files."""
        assert hook.is_security_guard_file("branch_rename_guard.py")
        assert hook.is_security_guard_file("/path/to/checkout_guard.py")
        assert hook.is_security_guard_file(".claude/hooks/resolve_thread_guard.py")

    def test_block_file(self):
        """Should detect *_block.py files."""
        assert hook.is_security_guard_file("checkout_block.py")
        assert hook.is_security_guard_file("/path/to/worktree_block.py")

    def test_check_file(self):
        """Should detect *_check.py files."""
        assert hook.is_security_guard_file("ci_wait_check.py")
        assert hook.is_security_guard_file("/path/to/main_sync_check.py")

    def test_hyphenated_names(self):
        """Should detect hyphenated names."""
        assert hook.is_security_guard_file("branch-rename-guard.py")
        assert hook.is_security_guard_file("checkout-block.py")
        assert hook.is_security_guard_file("ci-wait-check.py")

    def test_non_guard_file(self):
        """Should not detect regular Python files."""
        assert not hook.is_security_guard_file("main.py")
        assert not hook.is_security_guard_file("common.py")
        assert not hook.is_security_guard_file("utils.py")

    def test_test_file(self):
        """Test files should be detected as guard files (but filtered later)."""
        # test_ files contain _guard so they match the pattern
        assert hook.is_security_guard_file("test_branch_rename_guard.py")


class TestGetTestFilePath:
    """Tests for get_test_file_path function."""

    def test_hooks_directory(self):
        """Should find test file in hooks/tests directory."""
        with patch.object(Path, "exists", return_value=True):
            result = hook.get_test_file_path(".claude/hooks/branch_rename_guard.py")
            assert result is not None
            assert "test_branch_rename_guard.py" in str(result)

    def test_no_test_file(self):
        """Should return None when no test file exists."""
        with patch.object(Path, "exists", return_value=False):
            result = hook.get_test_file_path(".claude/hooks/new_guard.py")
            assert result is None

    def test_hyphenated_guard_finds_underscore_test(self):
        """Should find test file with underscores for hyphenated guard file."""

        # checkout-block.py should find test_checkout_block.py
        def mock_exists(self):
            # Return True when we find the underscore version
            return "test_checkout_block.py" in str(self)

        with patch.object(Path, "exists", mock_exists):
            result = hook.get_test_file_path(".claude/hooks/checkout-block.py")
            assert result is not None
            assert "test_checkout_block.py" in str(result)


class TestHasBypassTest:
    """Tests for has_bypass_test function."""

    def test_with_bypass_keyword(self):
        """Should detect bypass keyword in test file."""
        test_content = """
def test_guard_bypass_with_options(self):
    # Test bypass scenarios
    pass
"""
        with patch.object(Path, "read_text", return_value=test_content):
            assert hook.has_bypass_test(Path("test_file.py"))

    def test_with_japanese_bypass(self):
        """Should detect Japanese bypass keyword."""
        test_content = """
def test_guard_バイパス_prevention(self):
    # バイパス防止テスト
    pass
"""
        with patch.object(Path, "read_text", return_value=test_content):
            assert hook.has_bypass_test(Path("test_file.py"))

    def test_with_circumvent(self):
        """Should detect circumvent keyword."""
        test_content = """
def test_cannot_circumvent_guard(self):
    pass
"""
        with patch.object(Path, "read_text", return_value=test_content):
            assert hook.has_bypass_test(Path("test_file.py"))

    def test_without_bypass(self):
        """Should return False when no bypass keywords."""
        test_content = """
def test_basic_functionality(self):
    pass

def test_edge_cases(self):
    pass
"""
        with patch.object(Path, "read_text", return_value=test_content):
            assert not hook.has_bypass_test(Path("test_file.py"))

    def test_file_read_error(self):
        """Should return False (fail-open) on file read error."""
        with patch.object(Path, "read_text", side_effect=OSError("File not found")):
            assert not hook.has_bypass_test(Path("nonexistent.py"))
