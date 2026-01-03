#!/usr/bin/env python3
"""Tests for locked-worktree-guard.py - worktree_basic module."""

import importlib.util
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add hooks directory to path for common module
hooks_dir = Path(__file__).parent.parent
sys.path.insert(0, str(hooks_dir))

# Import from new modular structure
import command_parser
import common  # Module reference needed for locked_worktree_guard.common assignment
import guard_rules
import worktree_manager
from lib.constants import SESSION_MARKER_FILE
from lib.cwd import get_effective_cwd
from lib.git import check_recent_commits, check_uncommitted_changes
from lib.github import extract_pr_number, parse_gh_pr_command

# Import main from the main hook file
spec = importlib.util.spec_from_file_location(
    "locked_worktree_guard_main", hooks_dir / "locked-worktree-guard.py"
)
locked_worktree_guard_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(locked_worktree_guard_main)

# Create a unified module namespace for backward compatibility with tests
locked_worktree_guard = types.ModuleType("locked_worktree_guard")
sys.modules["locked_worktree_guard"] = locked_worktree_guard

# Expose submodules for patching
locked_worktree_guard.worktree_manager = worktree_manager
locked_worktree_guard.guard_rules = guard_rules
locked_worktree_guard.command_parser = command_parser
locked_worktree_guard.common = common

# main function and helper functions from hook
locked_worktree_guard.main = locked_worktree_guard_main.main
locked_worktree_guard.has_force_rm_orphan_env = locked_worktree_guard_main.has_force_rm_orphan_env
locked_worktree_guard.FORCE_RM_ORPHAN_ENV = locked_worktree_guard_main.FORCE_RM_ORPHAN_ENV

# command_parser functions
locked_worktree_guard.normalize_shell_operators = command_parser.normalize_shell_operators
locked_worktree_guard.extract_cd_target_before_git = command_parser.extract_cd_target_before_git
locked_worktree_guard.is_modifying_command = command_parser.is_modifying_command
locked_worktree_guard.has_delete_branch_flag = command_parser.has_delete_branch_flag
locked_worktree_guard.get_merge_positional_arg = command_parser.get_merge_positional_arg
locked_worktree_guard.has_merge_positional_arg = command_parser.has_merge_positional_arg
locked_worktree_guard.is_shell_redirect = command_parser.is_shell_redirect
locked_worktree_guard.is_bare_redirect_operator = command_parser.is_bare_redirect_operator
locked_worktree_guard.extract_first_merge_command = command_parser.extract_first_merge_command
locked_worktree_guard.is_gh_pr_command = command_parser.is_gh_pr_command
locked_worktree_guard.is_ci_monitor_command = command_parser.is_ci_monitor_command
locked_worktree_guard.check_single_git_worktree_remove = (
    command_parser.check_single_git_worktree_remove
)
locked_worktree_guard.is_worktree_remove_command = command_parser.is_worktree_remove_command
locked_worktree_guard.extract_git_base_directory = command_parser.extract_git_base_directory
locked_worktree_guard.extract_base_dir_from_git_segment = (
    command_parser.extract_base_dir_from_git_segment
)
locked_worktree_guard.extract_worktree_path_from_git_command = (
    command_parser.extract_worktree_path_from_git_command
)
locked_worktree_guard.extract_unlock_path_from_git_command = (
    command_parser.extract_unlock_path_from_git_command
)
locked_worktree_guard.extract_unlock_targets_from_command = (
    command_parser.extract_unlock_targets_from_command
)
locked_worktree_guard.find_git_worktree_remove_position = (
    command_parser.find_git_worktree_remove_position
)
locked_worktree_guard.extract_worktree_path_from_command = (
    command_parser.extract_worktree_path_from_command
)
locked_worktree_guard.extract_rm_paths = command_parser.extract_rm_paths

# worktree_manager functions
locked_worktree_guard.is_self_session_worktree = worktree_manager.is_self_session_worktree
locked_worktree_guard.get_worktree_for_branch = worktree_manager.get_worktree_for_branch
locked_worktree_guard.get_branch_for_pr = worktree_manager.get_branch_for_pr
locked_worktree_guard.check_active_work_signs = worktree_manager.check_active_work_signs
locked_worktree_guard.get_locked_worktrees = worktree_manager.get_locked_worktrees
locked_worktree_guard.get_pr_for_branch = worktree_manager.get_pr_for_branch
locked_worktree_guard.get_current_worktree = worktree_manager.get_current_worktree
locked_worktree_guard.get_current_branch_name = worktree_manager.get_current_branch_name
locked_worktree_guard.get_all_locked_worktree_paths = worktree_manager.get_all_locked_worktree_paths
locked_worktree_guard.is_cwd_inside_worktree = worktree_manager.is_cwd_inside_worktree
locked_worktree_guard.get_main_repo_dir = worktree_manager.get_main_repo_dir
locked_worktree_guard.get_all_worktree_paths = worktree_manager.get_all_worktree_paths
locked_worktree_guard.get_orphan_worktree_directories = (
    worktree_manager.get_orphan_worktree_directories
)
locked_worktree_guard.get_rm_target_orphan_worktrees = (
    worktree_manager.get_rm_target_orphan_worktrees
)
locked_worktree_guard.get_rm_target_worktrees = worktree_manager.get_rm_target_worktrees
locked_worktree_guard.is_rm_worktree_command = worktree_manager.is_rm_worktree_command

# guard_rules functions
locked_worktree_guard.check_pr_merged = guard_rules.check_pr_merged
locked_worktree_guard.improve_gh_error_message = guard_rules.improve_gh_error_message
locked_worktree_guard.execute_safe_merge = guard_rules.execute_safe_merge
locked_worktree_guard.try_auto_cleanup_worktree = guard_rules.try_auto_cleanup_worktree
locked_worktree_guard.check_self_branch_deletion = guard_rules.check_self_branch_deletion
locked_worktree_guard.check_rm_orphan_worktree = guard_rules.check_rm_orphan_worktree
locked_worktree_guard.check_rm_worktree = guard_rules.check_rm_worktree
locked_worktree_guard.check_worktree_remove = guard_rules.check_worktree_remove

# common module functions
locked_worktree_guard.extract_pr_number = extract_pr_number
locked_worktree_guard.parse_gh_pr_command = parse_gh_pr_command
locked_worktree_guard.check_recent_commits = check_recent_commits
locked_worktree_guard.check_uncommitted_changes = check_uncommitted_changes
locked_worktree_guard.SESSION_MARKER_FILE = SESSION_MARKER_FILE
locked_worktree_guard.get_effective_cwd = get_effective_cwd

# Private function aliases (with underscore prefix)
locked_worktree_guard._normalize_shell_operators = command_parser.normalize_shell_operators
locked_worktree_guard._extract_cd_target_before_git = command_parser.extract_cd_target_before_git
locked_worktree_guard._get_merge_positional_arg = command_parser.get_merge_positional_arg
locked_worktree_guard._has_merge_positional_arg = command_parser.has_merge_positional_arg
locked_worktree_guard._is_shell_redirect = command_parser.is_shell_redirect
locked_worktree_guard._is_bare_redirect_operator = command_parser.is_bare_redirect_operator
locked_worktree_guard._extract_first_merge_command = command_parser.extract_first_merge_command
locked_worktree_guard._check_single_git_worktree_remove = (
    command_parser.check_single_git_worktree_remove
)
locked_worktree_guard._extract_base_dir_from_git_segment = (
    command_parser.extract_base_dir_from_git_segment
)
locked_worktree_guard._extract_worktree_path_from_git_command = (
    command_parser.extract_worktree_path_from_git_command
)
locked_worktree_guard._extract_unlock_path_from_git_command = (
    command_parser.extract_unlock_path_from_git_command
)
locked_worktree_guard._find_git_worktree_remove_position = (
    command_parser.find_git_worktree_remove_position
)
locked_worktree_guard._extract_rm_paths = command_parser.extract_rm_paths
locked_worktree_guard._check_pr_merged = guard_rules.check_pr_merged
locked_worktree_guard._improve_gh_error_message = guard_rules.improve_gh_error_message
locked_worktree_guard._execute_safe_merge = guard_rules.execute_safe_merge
locked_worktree_guard._try_auto_cleanup_worktree = guard_rules.try_auto_cleanup_worktree


class TestIsSelfSessionWorktree:
    """Tests for is_self_session_worktree function (Issue #1400).

    Issue #2496: Tests updated to pass session_id directly instead of patching.
    """

    def test_returns_true_when_session_id_matches(self, tmp_path: Path):
        """Should return True when marker file contains current session ID."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        marker = worktree / ".claude-session"
        marker.write_text("test-session-123")

        # Issue #2496: Pass session_id directly instead of patching
        result = locked_worktree_guard.is_self_session_worktree(
            worktree, session_id="test-session-123"
        )
        assert result is True

    def test_returns_false_when_session_id_differs(self, tmp_path: Path):
        """Should return False when marker file contains different session ID."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        marker = worktree / ".claude-session"
        marker.write_text("other-session-456")

        # Issue #2496: Pass session_id directly instead of patching
        result = locked_worktree_guard.is_self_session_worktree(
            worktree, session_id="my-session-123"
        )
        assert result is False

    def test_returns_false_when_no_marker_file(self, tmp_path: Path):
        """Should return False when marker file does not exist."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        # Issue #2496: Pass session_id directly instead of patching
        result = locked_worktree_guard.is_self_session_worktree(
            worktree, session_id="my-session-123"
        )
        assert result is False

    def test_returns_false_when_worktree_does_not_exist(self, tmp_path: Path):
        """Should return False when worktree directory does not exist."""
        worktree = tmp_path / "nonexistent"

        # Issue #2496: Pass session_id directly instead of patching
        result = locked_worktree_guard.is_self_session_worktree(
            worktree, session_id="my-session-123"
        )
        assert result is False

    def test_handles_marker_with_trailing_whitespace(self, tmp_path: Path):
        """Should strip whitespace from marker file content."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        marker = worktree / ".claude-session"
        marker.write_text("test-session-123\n  ")

        # Issue #2496: Pass session_id directly instead of patching
        result = locked_worktree_guard.is_self_session_worktree(
            worktree, session_id="test-session-123"
        )
        assert result is True

    def test_returns_false_when_session_id_is_none(self, tmp_path: Path):
        """Should return False when session_id is None (Issue #2496)."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        marker = worktree / ".claude-session"
        marker.write_text("test-session-123")

        # When session_id is None, ownership cannot be determined
        result = locked_worktree_guard.is_self_session_worktree(worktree, session_id=None)
        assert result is False


class TestGetCurrentWorktree:
    """Tests for get_current_worktree function.

    This tests Issue #317: hookがメインリポジトリで実行されるため、
    get_current_worktree()が正しいworktreeを検出できない問題
    """

    @patch("subprocess.run")
    def test_uses_cwd_parameter(self, mock_run):
        """Should use cwd parameter when running git command."""
        mock_run.return_value = MagicMock(returncode=0, stdout="/path/to/worktree\n")

        result = locked_worktree_guard.get_current_worktree("/some/worktree/path")

        # Verify git command was called with cwd parameter
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["cwd"] == "/some/worktree/path"
        assert result == Path("/path/to/worktree")

    @patch("subprocess.run")
    def test_works_without_cwd_parameter(self, mock_run):
        """Should work when cwd is None (uses current directory)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="/path/to/main\n")

        result = locked_worktree_guard.get_current_worktree(None)

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["cwd"] is None
        assert result == Path("/path/to/main")

    @patch("subprocess.run")
    def test_returns_none_on_git_failure(self, mock_run):
        """Should return None when git command fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = locked_worktree_guard.get_current_worktree("/some/path")

        assert result is None


class TestGetLockedWorktrees:
    """Tests for get_locked_worktrees function."""

    @patch("subprocess.run")
    def test_parses_locked_worktrees(self, mock_run):
        """Should parse locked worktrees from git worktree list output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "worktree /path/to/main\n"
                "branch refs/heads/main\n"
                "\n"
                "worktree /path/to/feature\n"
                "branch refs/heads/feature-branch\n"
                "locked\n"
                "\n"
                "worktree /path/to/another\n"
                "branch refs/heads/another-branch\n"
            ),
        )

        result = locked_worktree_guard.get_locked_worktrees()

        assert len(result) == 1
        assert result[0] == (Path("/path/to/feature"), "feature-branch")

    @patch("subprocess.run")
    def test_returns_empty_on_no_locked(self, mock_run):
        """Should return empty list when no worktrees are locked."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "worktree /path/to/main\n"
                "branch refs/heads/main\n"
                "\n"
                "worktree /path/to/feature\n"
                "branch refs/heads/feature-branch\n"
            ),
        )

        result = locked_worktree_guard.get_locked_worktrees()

        assert result == []

    @patch("subprocess.run")
    def test_parses_locked_with_reason(self, mock_run):
        """Should parse worktrees locked with a reason."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "worktree /path/to/main\n"
                "branch refs/heads/main\n"
                "\n"
                "worktree /path/to/feature\n"
                "branch refs/heads/feature-branch\n"
                "locked session-abc123\n"
            ),
        )

        result = locked_worktree_guard.get_locked_worktrees()

        assert len(result) == 1
        assert result[0] == (Path("/path/to/feature"), "feature-branch")

    @patch("subprocess.run")
    def test_returns_empty_on_git_failure(self, mock_run):
        """Should return empty list when git command fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = locked_worktree_guard.get_locked_worktrees()

        assert result == []


class TestGetAllLockedWorktreePaths:
    """Tests for get_all_locked_worktree_paths function."""

    @patch("subprocess.run")
    def test_returns_locked_paths(self, mock_run):
        """Should return paths of locked worktrees."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "worktree /path/to/main\n"
                "branch refs/heads/main\n"
                "\n"
                "worktree /path/to/feature\n"
                "branch refs/heads/feature-branch\n"
                "locked\n"
                "\n"
                "worktree /path/to/another\n"
                "branch refs/heads/another-branch\n"
                "locked session-123\n"
            ),
        )

        result = locked_worktree_guard.get_all_locked_worktree_paths()

        assert len(result) == 2
        assert Path("/path/to/feature") in result
        assert Path("/path/to/another") in result

    @patch("subprocess.run")
    def test_returns_empty_on_no_locked(self, mock_run):
        """Should return empty list when no worktrees are locked."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=("worktree /path/to/main\nbranch refs/heads/main\n"),
        )

        result = locked_worktree_guard.get_all_locked_worktree_paths()

        assert result == []


class TestGetMainRepoDir:
    """Tests for get_main_repo_dir function.

    Issue #360: 専用ユニットテスト追加
    """

    @patch("subprocess.run")
    def test_returns_parent_of_git_common_dir(self, mock_run):
        """Should return parent of git common dir."""
        mock_run.return_value = MagicMock(returncode=0, stdout="/Users/test/project/.git\n")

        result = locked_worktree_guard.get_main_repo_dir()

        assert result == Path("/Users/test/project")

    @patch("subprocess.run")
    def test_returns_none_on_git_failure(self, mock_run):
        """Should return None when git command fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = locked_worktree_guard.get_main_repo_dir()

        assert result is None

    @patch("subprocess.run")
    def test_returns_none_on_timeout(self, mock_run):
        """Should return None when git command times out.

        Issue #360: 例外ハンドリングの改善
        """
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=5)

        result = locked_worktree_guard.get_main_repo_dir()

        assert result is None

    @patch("subprocess.run")
    def test_returns_none_on_oserror(self, mock_run):
        """Should return None when OSError occurs (e.g., git not found).

        Issue #360: 例外ハンドリングの改善
        """
        mock_run.side_effect = OSError("git not found")

        result = locked_worktree_guard.get_main_repo_dir()

        assert result is None

    @patch("subprocess.run")
    def test_handles_worktree_git_common_dir(self, mock_run):
        """Should correctly handle git common dir from worktree."""
        # When run from a worktree, git rev-parse --git-common-dir returns
        # the path to the main repo's .git directory
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/Users/test/project/.git\n",  # Main repo's .git
        )

        result = locked_worktree_guard.get_main_repo_dir()

        assert result == Path("/Users/test/project")


class TestGetWorktreeForBranch:
    """Tests for get_worktree_for_branch function (Issue #528)."""

    @patch("subprocess.run")
    def test_finds_worktree_for_branch(self, mock_run):
        """Should find worktree path for a given branch."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "worktree /repo/main\n"
                "branch refs/heads/main\n"
                "\n"
                "worktree /repo/.worktrees/feature-123\n"
                "branch refs/heads/feat/issue-123\n"
                "\n"
            ),
        )

        result = locked_worktree_guard.get_worktree_for_branch("feat/issue-123")
        assert result == Path("/repo/.worktrees/feature-123")

    @patch("subprocess.run")
    def test_returns_none_for_unknown_branch(self, mock_run):
        """Should return None when branch not found."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=("worktree /repo/main\nbranch refs/heads/main\n\n"),
        )

        result = locked_worktree_guard.get_worktree_for_branch("nonexistent-branch")
        assert result is None

    @patch("subprocess.run")
    def test_handles_git_error(self, mock_run):
        """Should handle git errors gracefully."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = locked_worktree_guard.get_worktree_for_branch("any-branch")
        assert result is None


class TestGetCurrentBranchName:
    """Tests for get_current_branch_name function (Issue #649)."""

    @patch("subprocess.run")
    def test_returns_branch_name(self, mock_run):
        """Should return branch name."""
        mock_run.return_value = MagicMock(returncode=0, stdout="feat/issue-123\n")

        result = locked_worktree_guard.get_current_branch_name()
        assert result == "feat/issue-123"

    @patch("subprocess.run")
    def test_returns_none_for_detached_head(self, mock_run):
        """Should return None for detached HEAD."""
        mock_run.return_value = MagicMock(returncode=0, stdout="HEAD\n")

        result = locked_worktree_guard.get_current_branch_name()
        assert result is None

    @patch("subprocess.run")
    def test_returns_none_on_error(self, mock_run):
        """Should return None on error."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = locked_worktree_guard.get_current_branch_name()
        assert result is None

    @patch("subprocess.run")
    def test_uses_cwd_parameter(self, mock_run):
        """Should use cwd parameter when provided."""
        mock_run.return_value = MagicMock(returncode=0, stdout="main\n")

        locked_worktree_guard.get_current_branch_name("/some/path")
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["cwd"] == "/some/path"
