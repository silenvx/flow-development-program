#!/usr/bin/env python3
"""Tests for pr-overlap-check.py hook."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

HOOK_PATH = Path(__file__).parent.parent / "pr-overlap-check.py"

# Add hooks directory to path for 'common' module import
_hooks_dir = str(HOOK_PATH.parent)
if _hooks_dir not in sys.path:
    sys.path.insert(0, _hooks_dir)


def load_module():
    """Load the hook module for testing."""
    spec = importlib.util.spec_from_file_location("pr_overlap_check", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pr_overlap_check"] = module
    spec.loader.exec_module(module)
    return module


class TestIsPushOrPrCreate:
    """Tests for is_push_or_pr_create function."""

    def setup_method(self):
        self.module = load_module()

    def test_detect_git_push(self):
        """Should detect git push commands."""
        assert self.module.is_push_or_pr_create("git push")
        assert self.module.is_push_or_pr_create("git push origin main")
        assert self.module.is_push_or_pr_create("git push -u origin feat/test")

    def test_detect_gh_pr_create(self):
        """Should detect gh pr create commands."""
        assert self.module.is_push_or_pr_create("gh pr create")
        assert self.module.is_push_or_pr_create('gh pr create --title "test"')
        assert self.module.is_push_or_pr_create("gh pr create --draft")

    def test_exclude_quoted_strings(self):
        """Should not detect commands inside quoted strings."""
        assert not self.module.is_push_or_pr_create("echo 'git push'")
        assert not self.module.is_push_or_pr_create('echo "gh pr create"')

    def test_empty_command(self):
        """Should return False for empty commands."""
        assert not self.module.is_push_or_pr_create("")
        assert not self.module.is_push_or_pr_create("   ")

    def test_non_matching_commands(self):
        """Should not detect other commands."""
        assert not self.module.is_push_or_pr_create("git status")
        assert not self.module.is_push_or_pr_create("gh pr view 123")
        assert not self.module.is_push_or_pr_create("ls -la")


class TestFindOverlappingFiles:
    """Tests for find_overlapping_files function."""

    def setup_method(self):
        self.module = load_module()

    def test_find_single_overlap(self):
        """Should find single overlapping file."""
        current_files = {"file1.py", "file2.py"}
        pr_files = {"#123": ["file1.py", "other.py"]}

        overlaps = self.module.find_overlapping_files(current_files, pr_files)

        assert overlaps == {"#123": ["file1.py"]}

    def test_find_multiple_overlaps_same_pr(self):
        """Should find multiple overlapping files in same PR."""
        current_files = {"file1.py", "file2.py", "file3.py"}
        pr_files = {"#123": ["file1.py", "file2.py", "other.py"]}

        overlaps = self.module.find_overlapping_files(current_files, pr_files)

        assert overlaps == {"#123": ["file1.py", "file2.py"]}

    def test_find_overlaps_multiple_prs(self):
        """Should find overlaps across multiple PRs."""
        current_files = {"common.py", "main.py"}
        pr_files = {
            "#123": ["common.py", "other.py"],
            "#456": ["main.py", "different.py"],
        }

        overlaps = self.module.find_overlapping_files(current_files, pr_files)

        assert overlaps == {"#123": ["common.py"], "#456": ["main.py"]}

    def test_no_overlaps(self):
        """Should return empty dict when no overlaps."""
        current_files = {"file1.py", "file2.py"}
        pr_files = {"#123": ["other1.py", "other2.py"]}

        overlaps = self.module.find_overlapping_files(current_files, pr_files)

        assert overlaps == {}

    def test_empty_current_files(self):
        """Should return empty dict when current files is empty."""
        current_files = set()
        pr_files = {"#123": ["file1.py"]}

        overlaps = self.module.find_overlapping_files(current_files, pr_files)

        assert overlaps == {}

    def test_empty_pr_files(self):
        """Should return empty dict when no other PRs."""
        current_files = {"file1.py"}
        pr_files = {}

        overlaps = self.module.find_overlapping_files(current_files, pr_files)

        assert overlaps == {}


class TestFormatWarning:
    """Tests for format_warning function."""

    def setup_method(self):
        self.module = load_module()

    def test_format_single_pr_single_file(self):
        """Should format warning for single PR with single file."""
        overlaps = {"#123": ["common.py"]}

        warning = self.module.format_warning(overlaps)

        assert "File overlap detected" in warning
        assert "#123" in warning
        assert "common.py" in warning
        assert "merge conflicts" in warning

    def test_format_multiple_files(self):
        """Should format warning with multiple files."""
        overlaps = {"#123": ["file1.py", "file2.py", "file3.py"]}

        warning = self.module.format_warning(overlaps)

        assert "file1.py" in warning
        assert "file2.py" in warning
        assert "file3.py" in warning

    def test_format_multiple_prs(self):
        """Should format warning with multiple PRs."""
        overlaps = {"#123": ["file1.py"], "#456": ["file2.py"]}

        warning = self.module.format_warning(overlaps)

        assert "#123" in warning
        assert "#456" in warning
        assert "file1.py" in warning
        assert "file2.py" in warning

    def test_truncate_many_files(self):
        """Should truncate when more than 5 files per PR."""
        overlaps = {"#123": [f"file{i}.py" for i in range(10)]}

        warning = self.module.format_warning(overlaps)

        # Should show first 5 files
        assert "file0.py" in warning
        assert "file4.py" in warning
        # Should show truncation message
        assert "5 more files" in warning
        # Should not show file6+
        assert "file6.py" not in warning


class TestGetCurrentBranchFiles:
    """Tests for get_current_branch_files function."""

    def setup_method(self):
        self.module = load_module()
        # Clear the lru_cache for each test
        self.module.get_current_branch_files.cache_clear()

    @patch("subprocess.run")
    def test_returns_changed_files(self, mock_run):
        """Should return set of changed files."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "file1.py\nfile2.py\nfile3.py"

        files = self.module.get_current_branch_files()

        assert files == {"file1.py", "file2.py", "file3.py"}

    @patch("subprocess.run")
    def test_returns_empty_on_error(self, mock_run):
        """Should return empty set on git error."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""

        files = self.module.get_current_branch_files()

        assert files == set()

    @patch("subprocess.run")
    def test_returns_empty_on_exception(self, mock_run):
        """Should return empty set on exception."""
        mock_run.side_effect = Exception("Git not found")

        files = self.module.get_current_branch_files()

        assert files == set()

    @patch("subprocess.run")
    def test_handles_empty_output(self, mock_run):
        """Should return empty set when no changed files."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""

        files = self.module.get_current_branch_files()

        assert files == set()


class TestGetOpenPrFiles:
    """Tests for get_open_pr_files function."""

    def setup_method(self):
        self.module = load_module()

    @patch("pr_overlap_check.get_current_branch")
    @patch("subprocess.run")
    def test_returns_pr_files(self, mock_run, mock_branch):
        """Should return dict of PR files."""
        mock_branch.return_value = "main"
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = """[
            {"number": 123, "headRefName": "feat/a", "files": [{"path": "file1.py"}]},
            {"number": 456, "headRefName": "feat/b", "files": [{"path": "file2.py"}]}
        ]"""

        pr_files = self.module.get_open_pr_files()

        assert pr_files == {"#123": ["file1.py"], "#456": ["file2.py"]}

    @patch("pr_overlap_check.get_current_branch")
    @patch("subprocess.run")
    def test_excludes_current_branch(self, mock_run, mock_branch):
        """Should exclude current branch's PR."""
        mock_branch.return_value = "feat/current"
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = """[
            {"number": 123, "headRefName": "feat/current", "files": [{"path": "file1.py"}]},
            {"number": 456, "headRefName": "feat/other", "files": [{"path": "file2.py"}]}
        ]"""

        pr_files = self.module.get_open_pr_files()

        # Should only include #456, not #123
        assert "#123" not in pr_files
        assert pr_files == {"#456": ["file2.py"]}

    @patch("pr_overlap_check.get_current_branch")
    @patch("subprocess.run")
    def test_returns_empty_on_error(self, mock_run, mock_branch):
        """Should return empty dict on gh error."""
        mock_branch.return_value = "main"
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""

        pr_files = self.module.get_open_pr_files()

        assert pr_files == {}

    @patch("pr_overlap_check.get_current_branch")
    @patch("subprocess.run")
    def test_returns_empty_on_exception(self, mock_run, mock_branch):
        """Should return empty dict on exception."""
        mock_branch.return_value = "main"
        mock_run.side_effect = Exception("gh not found")

        pr_files = self.module.get_open_pr_files()

        assert pr_files == {}
