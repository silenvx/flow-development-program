#!/usr/bin/env python3
"""Tests for check-hook-test-coverage.py"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load module from file path (handles hyphenated filenames)
script_path = Path(__file__).parent.parent / "check_hook_test_coverage.py"
spec = importlib.util.spec_from_file_location("check_hook_test_coverage", script_path)
check_hook_test_coverage = importlib.util.module_from_spec(spec)
sys.modules["check_hook_test_coverage"] = check_hook_test_coverage
spec.loader.exec_module(check_hook_test_coverage)


class TestGetChangedFiles:
    """Tests for get_changed_files function."""

    @patch("subprocess.run")
    def test_returns_changed_files_on_success(self, mock_run):
        """Should return list of changed files when git diff succeeds."""
        mock_run.return_value = MagicMock(
            stdout="file1.py\nfile2.py\n",
            returncode=0,
        )
        result = check_hook_test_coverage.get_changed_files()
        assert result == ["file1.py", "file2.py"]

    @patch("subprocess.run")
    def test_returns_empty_list_on_no_changes(self, mock_run):
        """Should return empty list when no files changed."""
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = check_hook_test_coverage.get_changed_files()
        assert result == []

    @patch("subprocess.run")
    def test_returns_none_on_git_failure(self, mock_run):
        """Should return None when git diff fails."""
        import subprocess

        mock_run.side_effect = subprocess.CalledProcessError(1, "git")
        result = check_hook_test_coverage.get_changed_files()
        assert result is None


class TestGetTestFileForHook:
    """Tests for get_test_file_for_hook function."""

    def test_converts_hyphens_to_underscores(self):
        """Should convert hyphens in hook name to underscores for test file."""
        hook = Path(".claude/hooks/my-test-hook.py")
        result = check_hook_test_coverage.get_test_file_for_hook(hook)
        assert result == Path(".claude/hooks/tests/test_my_test_hook.py")

    def test_handles_no_hyphens(self):
        """Should handle hook names without hyphens."""
        hook = Path(".claude/hooks/simple.py")
        result = check_hook_test_coverage.get_test_file_for_hook(hook)
        assert result == Path(".claude/hooks/tests/test_simple.py")


class TestGetHookFiles:
    """Tests for get_hook_files function."""

    @patch("pathlib.Path.glob")
    def test_excludes_common_and_init(self, mock_glob):
        """Should exclude common.py and __init__.py from results."""
        mock_glob.return_value = [
            Path(".claude/hooks/common.py"),
            Path(".claude/hooks/__init__.py"),
            Path(".claude/hooks/my-hook.py"),
            Path(".claude/hooks/test_something.py"),
        ]
        result = check_hook_test_coverage.get_hook_files()
        assert result == [Path(".claude/hooks/my-hook.py")]
