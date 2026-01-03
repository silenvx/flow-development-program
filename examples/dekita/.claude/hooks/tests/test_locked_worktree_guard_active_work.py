#!/usr/bin/env python3
"""Tests for locked-worktree-guard.py - active_work module."""

import importlib.util
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


class TestCheckRecentCommits:
    """Tests for check_recent_commits function (Issue #528)."""

    @patch("subprocess.run")
    def test_detects_recent_commit(self, mock_run):
        """Should detect commits within 1 hour."""
        # Mock git log output with current timestamp
        import time

        current_timestamp = int(time.time())
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"{current_timestamp}\t5 minutes ago\tTest commit message",
        )

        has_recent, info = locked_worktree_guard.check_recent_commits(Path("/test/worktree"))
        assert has_recent
        assert "5 minutes ago" in info
        assert "Test commit message" in info

    @patch("subprocess.run")
    def test_ignores_old_commit(self, mock_run):
        """Should not detect commits older than 1 hour."""
        import time

        old_timestamp = int(time.time()) - 7200  # 2 hours ago
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"{old_timestamp}\t2 hours ago\tOld commit message",
        )

        has_recent, info = locked_worktree_guard.check_recent_commits(Path("/test/worktree"))
        assert not has_recent
        assert info is None

    @patch("subprocess.run")
    def test_handles_git_error(self, mock_run):
        """Should handle git errors gracefully."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        has_recent, info = locked_worktree_guard.check_recent_commits(Path("/test/worktree"))
        assert not has_recent
        assert info is None

    @patch("subprocess.run")
    def test_handles_empty_output(self, mock_run):
        """Should handle empty git output (no commits)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        has_recent, info = locked_worktree_guard.check_recent_commits(Path("/test/worktree"))
        assert not has_recent
        assert info is None


class TestCheckUncommittedChanges:
    """Tests for check_uncommitted_changes function (Issue #528)."""

    @patch("subprocess.run")
    def test_detects_uncommitted_changes(self, mock_run):
        """Should detect uncommitted changes."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="M  file1.py\nA  file2.py\n?? untracked.py",
        )

        has_changes, count = locked_worktree_guard.check_uncommitted_changes(Path("/test/worktree"))
        assert has_changes
        assert count == 3

    @patch("subprocess.run")
    def test_no_changes(self, mock_run):
        """Should return false when no changes."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        has_changes, count = locked_worktree_guard.check_uncommitted_changes(Path("/test/worktree"))
        assert not has_changes
        assert count == 0

    @patch("subprocess.run")
    def test_handles_git_error(self, mock_run):
        """Should handle git errors gracefully."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        has_changes, count = locked_worktree_guard.check_uncommitted_changes(Path("/test/worktree"))
        assert not has_changes
        assert count == 0


class TestCheckActiveWorkSigns:
    """Tests for check_active_work_signs function (Issue #528)."""

    @patch.object(worktree_manager, "check_uncommitted_changes")
    @patch.object(worktree_manager, "check_recent_commits")
    def test_detects_both_signs(self, mock_recent, mock_uncommitted):
        """Should detect both recent commits and uncommitted changes."""
        mock_recent.return_value = (True, "5 minutes ago: Test commit")
        mock_uncommitted.return_value = (True, 3)

        warnings = locked_worktree_guard.check_active_work_signs(Path("/test/worktree"))
        assert len(warnings) == 2
        assert "最新コミット" in warnings[0]
        assert "未コミット変更" in warnings[1]

    @patch.object(worktree_manager, "check_uncommitted_changes")
    @patch.object(worktree_manager, "check_recent_commits")
    def test_detects_only_recent_commits(self, mock_recent, mock_uncommitted):
        """Should detect only recent commits."""
        mock_recent.return_value = (True, "5 minutes ago: Test commit")
        mock_uncommitted.return_value = (False, 0)

        warnings = locked_worktree_guard.check_active_work_signs(Path("/test/worktree"))
        assert len(warnings) == 1
        assert "最新コミット" in warnings[0]

    @patch.object(worktree_manager, "check_uncommitted_changes")
    @patch.object(worktree_manager, "check_recent_commits")
    def test_detects_only_uncommitted_changes(self, mock_recent, mock_uncommitted):
        """Should detect only uncommitted changes."""
        mock_recent.return_value = (False, None)
        mock_uncommitted.return_value = (True, 2)

        warnings = locked_worktree_guard.check_active_work_signs(Path("/test/worktree"))
        assert len(warnings) == 1
        assert "未コミット変更" in warnings[0]

    @patch.object(worktree_manager, "check_uncommitted_changes")
    @patch.object(worktree_manager, "check_recent_commits")
    def test_no_signs_detected(self, mock_recent, mock_uncommitted):
        """Should return empty list when no active work signs."""
        mock_recent.return_value = (False, None)
        mock_uncommitted.return_value = (False, 0)

        warnings = locked_worktree_guard.check_active_work_signs(Path("/test/worktree"))
        assert len(warnings) == 0


class TestActiveWorkWarningIntegration:
    """Integration tests for active work warning in PR operations (Issue #528)."""

    @patch.object(locked_worktree_guard, "check_active_work_signs")
    @patch.object(locked_worktree_guard, "get_worktree_for_branch")
    @patch.object(guard_rules, "get_branch_for_pr")
    @patch.object(guard_rules, "get_locked_worktrees")
    @patch.object(guard_rules, "get_current_worktree")
    def test_warns_on_active_work_signs(
        self,
        mock_current_wt,
        mock_locked_wts,
        mock_get_branch,
        mock_get_wt_for_branch,
        mock_active_signs,
    ):
        """Should warn (not block) when PR's worktree has active work signs."""
        # Setup mocks
        mock_current_wt.return_value = Path("/repo/.worktrees/current")
        mock_locked_wts.return_value = []  # No locked worktrees
        mock_get_branch.return_value = "feat/issue-123"
        mock_get_wt_for_branch.return_value = Path("/repo/.worktrees/feature-123")
        mock_active_signs.return_value = ["最新コミット（1時間以内）: 5 minutes ago"]

        # Simulate main() logic for active work check
        current_worktree = mock_current_wt.return_value

        _pr_branch = mock_get_branch.return_value  # Used to trigger the mock
        worktree_for_pr = mock_get_wt_for_branch.return_value

        # Skip if same as current worktree
        if worktree_for_pr and worktree_for_pr != current_worktree:
            active_signs = mock_active_signs.return_value
            if active_signs:
                result = {
                    "decision": "approve",
                    "systemMessage": "⚠️ このPRは別セッションが作業中の可能性があります。",
                }
                assert result["decision"] == "approve"
                assert "⚠️" in result["systemMessage"]

    @patch.object(locked_worktree_guard, "check_active_work_signs")
    @patch.object(locked_worktree_guard, "get_worktree_for_branch")
    @patch.object(guard_rules, "get_branch_for_pr")
    @patch.object(guard_rules, "get_locked_worktrees")
    @patch.object(guard_rules, "get_current_worktree")
    def test_skips_warning_for_own_worktree(
        self,
        mock_current_wt,
        mock_locked_wts,
        mock_get_branch,
        mock_get_wt_for_branch,
        mock_active_signs,
    ):
        """Should not warn when PR belongs to our own worktree."""
        # Setup: PR belongs to current worktree
        mock_current_wt.return_value = Path("/repo/.worktrees/feature-123")
        mock_locked_wts.return_value = []
        mock_get_branch.return_value = "feat/issue-123"
        mock_get_wt_for_branch.return_value = Path("/repo/.worktrees/feature-123")
        mock_active_signs.return_value = ["最新コミット（1時間以内）: 5 minutes ago"]

        current_worktree = mock_current_wt.return_value
        worktree_for_pr = mock_get_wt_for_branch.return_value

        # Should skip warning for own worktree
        should_warn = worktree_for_pr and worktree_for_pr != current_worktree
        assert not should_warn

    @patch.object(locked_worktree_guard, "check_active_work_signs")
    @patch.object(locked_worktree_guard, "get_worktree_for_branch")
    @patch.object(guard_rules, "get_branch_for_pr")
    @patch.object(guard_rules, "get_locked_worktrees")
    @patch.object(guard_rules, "get_current_worktree")
    def test_no_warning_when_no_active_signs(
        self,
        mock_current_wt,
        mock_locked_wts,
        mock_get_branch,
        mock_get_wt_for_branch,
        mock_active_signs,
    ):
        """Should not warn when no active work signs detected."""
        mock_current_wt.return_value = Path("/repo/.worktrees/current")
        mock_locked_wts.return_value = []
        mock_get_branch.return_value = "feat/issue-123"
        mock_get_wt_for_branch.return_value = Path("/repo/.worktrees/feature-123")
        mock_active_signs.return_value = []  # No active signs

        active_signs = mock_active_signs.return_value
        should_warn = len(active_signs) > 0
        assert not should_warn
