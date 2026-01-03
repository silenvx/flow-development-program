#!/usr/bin/env python3
"""Tests for branch_check.py hook."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path for module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


class TestGetCurrentBranch:
    """Tests for get_current_branch function."""

    def setup_method(self):
        """Import module for each test."""
        # Re-import to get fresh module
        import importlib

        import branch_check

        importlib.reload(branch_check)
        self.module = branch_check

    @patch("branch_check.subprocess.run")
    def test_returns_branch_name(self, mock_run: MagicMock):
        """Should return current branch name."""
        mock_run.return_value = MagicMock(returncode=0, stdout="main\n")
        result = self.module.get_current_branch()
        assert result == "main"

    @patch("branch_check.subprocess.run")
    def test_returns_feature_branch(self, mock_run: MagicMock):
        """Should return feature branch name."""
        mock_run.return_value = MagicMock(returncode=0, stdout="feat/issue-123-new-feature\n")
        result = self.module.get_current_branch()
        assert result == "feat/issue-123-new-feature"

    @patch("branch_check.subprocess.run")
    def test_returns_none_on_error(self, mock_run: MagicMock):
        """Should return None when git command fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = self.module.get_current_branch()
        assert result is None

    @patch("branch_check.subprocess.run")
    def test_returns_none_on_timeout(self, mock_run: MagicMock):
        """Should return None when git command times out."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=5)
        result = self.module.get_current_branch()
        assert result is None


class TestIsInWorktree:
    """Tests for is_in_worktree function."""

    def setup_method(self):
        """Import module for each test."""
        import importlib

        import branch_check

        importlib.reload(branch_check)
        self.module = branch_check

    @patch("branch_check.os.getcwd")
    def test_returns_true_in_worktree(self, mock_getcwd: MagicMock):
        """Should return True when in .worktrees directory."""
        mock_getcwd.return_value = "/path/to/repo/.worktrees/issue-123"
        result = self.module.is_in_worktree()
        assert result

    @patch("branch_check.os.getcwd")
    def test_returns_true_in_nested_worktree(self, mock_getcwd: MagicMock):
        """Should return True when in nested .worktrees directory."""
        mock_getcwd.return_value = "/path/to/repo/.worktrees/issue-123/src"
        result = self.module.is_in_worktree()
        assert result

    @patch("branch_check.os.getcwd")
    def test_returns_false_in_main_repo(self, mock_getcwd: MagicMock):
        """Should return False when in main repository."""
        mock_getcwd.return_value = "/path/to/repo"
        result = self.module.is_in_worktree()
        assert not result

    @patch("branch_check.os.getcwd")
    def test_returns_false_in_main_repo_src(self, mock_getcwd: MagicMock):
        """Should return False when in main repository subdirectory."""
        mock_getcwd.return_value = "/path/to/repo/src"
        result = self.module.is_in_worktree()
        assert not result


class TestIsMainRepository:
    """Tests for is_main_repository function."""

    def setup_method(self):
        """Import module for each test."""
        import importlib

        import branch_check

        importlib.reload(branch_check)
        self.module = branch_check

    @patch("branch_check.os.getcwd")
    @patch("branch_check.os.path.realpath")
    @patch("branch_check.subprocess.run")
    def test_returns_true_for_main_repo(
        self, mock_run: MagicMock, mock_realpath: MagicMock, mock_getcwd: MagicMock
    ):
        """Should return True when cwd is main repository."""
        mock_getcwd.return_value = "/path/to/repo"
        mock_realpath.return_value = "/path/to/repo"
        mock_run.return_value = MagicMock(
            returncode=0, stdout="worktree /path/to/repo\nHEAD abc123\n"
        )
        result = self.module.is_main_repository()
        assert result

    @patch("branch_check.os.getcwd")
    @patch("branch_check.os.path.realpath")
    @patch("branch_check.subprocess.run")
    def test_returns_true_for_worktree_subdir(
        self, mock_run: MagicMock, mock_realpath: MagicMock, mock_getcwd: MagicMock
    ):
        """Should return True for worktree (subdir of main repo).

        Note: Worktrees are subdirectories of main repo, so is_main_repository
        returns True. The is_in_worktree() check in main() handles this case.
        """
        mock_getcwd.return_value = "/path/to/repo/.worktrees/issue-123"
        mock_realpath.side_effect = lambda x: x  # Return same path
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="worktree /path/to/repo\nHEAD abc123\n\nworktree /path/to/repo/.worktrees/issue-123\nHEAD def456\n",
        )
        result = self.module.is_main_repository()
        assert result  # Worktree is a subdirectory of main repo

    @patch("branch_check.subprocess.run")
    def test_returns_false_on_git_error(self, mock_run: MagicMock):
        """Should return False when git command fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = self.module.is_main_repository()
        assert not result


class TestMain:
    """Tests for main function."""

    def setup_method(self):
        """Import module for each test."""
        import importlib

        import branch_check

        importlib.reload(branch_check)
        self.module = branch_check

    @patch("branch_check.parse_hook_input")
    @patch("branch_check.is_in_worktree")
    @patch("builtins.print")
    def test_no_output_in_worktree(
        self, mock_print: MagicMock, mock_is_in_worktree: MagicMock, mock_parse: MagicMock
    ):
        """Should not print anything when in worktree."""
        mock_parse.return_value = {}
        mock_is_in_worktree.return_value = True
        self.module.main()
        mock_print.assert_not_called()

    @patch("branch_check.parse_hook_input")
    @patch("branch_check.is_in_worktree")
    @patch("branch_check.is_main_repository")
    @patch("builtins.print")
    def test_no_output_not_main_repo(
        self,
        mock_print: MagicMock,
        mock_is_main_repo: MagicMock,
        mock_is_in_worktree: MagicMock,
        mock_parse: MagicMock,
    ):
        """Should not print anything when not in main repository."""
        mock_parse.return_value = {}
        mock_is_in_worktree.return_value = False
        mock_is_main_repo.return_value = False
        self.module.main()
        mock_print.assert_not_called()

    @patch("branch_check.parse_hook_input")
    @patch("branch_check.is_in_worktree")
    @patch("branch_check.is_main_repository")
    @patch("branch_check.get_current_branch")
    @patch("builtins.print")
    def test_no_output_on_main_branch(
        self,
        mock_print: MagicMock,
        mock_get_branch: MagicMock,
        mock_is_main_repo: MagicMock,
        mock_is_in_worktree: MagicMock,
        mock_parse: MagicMock,
    ):
        """Should not print anything when on main branch."""
        mock_parse.return_value = {}
        mock_is_in_worktree.return_value = False
        mock_is_main_repo.return_value = True
        mock_get_branch.return_value = "main"
        self.module.main()
        mock_print.assert_not_called()

    @patch("branch_check.parse_hook_input")
    @patch("branch_check.log_hook_execution")
    @patch("branch_check.is_in_worktree")
    @patch("branch_check.is_main_repository")
    @patch("branch_check.get_current_branch")
    @patch("builtins.print")
    def test_blocks_on_non_main_branch(
        self,
        mock_print: MagicMock,
        mock_get_branch: MagicMock,
        mock_is_main_repo: MagicMock,
        mock_is_in_worktree: MagicMock,
        mock_log: MagicMock,
        mock_parse: MagicMock,
    ):
        """Should block (exit 2) when on non-main branch in main repo."""
        mock_parse.return_value = {}
        mock_is_in_worktree.return_value = False
        mock_is_main_repo.return_value = True
        mock_get_branch.return_value = "feat/issue-123"

        with pytest.raises(SystemExit) as exc_info:
            self.module.main()

        assert exc_info.value.code == 2  # exit 2 = blocking error
        mock_print.assert_called_once()
        call_args = mock_print.call_args[0][0]
        assert "feat/issue-123" in call_args
        assert "🚫" in call_args
