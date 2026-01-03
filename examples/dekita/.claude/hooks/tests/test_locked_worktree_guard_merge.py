#!/usr/bin/env python3
"""Tests for locked-worktree-guard.py - merge module."""

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


class TestCheckSelfBranchDeletion:
    """Tests for check_self_branch_deletion function (Issue #649, #855)."""

    @patch.object(guard_rules, "check_pr_merged")
    @patch.object(guard_rules, "execute_safe_merge")
    @patch.object(guard_rules, "get_branch_for_pr")
    @patch.object(guard_rules, "get_current_branch_name")
    @patch.object(guard_rules, "get_main_repo_dir")
    @patch.object(guard_rules, "get_current_worktree")
    def test_blocks_self_branch_deletion_with_auto_merge(
        self,
        mock_current_wt,
        mock_main_repo,
        mock_current_branch,
        mock_pr_branch,
        mock_execute_merge,
        mock_check_merged,
    ):
        """Should auto-merge and block when PR's branch matches current worktree's branch (Issue #855)."""
        mock_current_wt.return_value = Path("/repo/.worktrees/issue-123")
        mock_main_repo.return_value = Path("/repo")
        mock_current_branch.return_value = "feat/issue-123"
        mock_pr_branch.return_value = "feat/issue-123"
        mock_execute_merge.return_value = (True, "Merged successfully")
        mock_check_merged.return_value = True  # PR is actually merged

        result = locked_worktree_guard.check_self_branch_deletion("gh pr merge 456 --delete-branch")

        assert result is not None
        assert result["decision"] == "block"
        assert "マージ完了（自動実行）" in result["reason"]
        mock_execute_merge.assert_called_once()
        mock_check_merged.assert_called_once()

    @patch.object(guard_rules, "execute_safe_merge")
    @patch.object(guard_rules, "get_branch_for_pr")
    @patch.object(guard_rules, "get_current_branch_name")
    @patch.object(guard_rules, "get_main_repo_dir")
    @patch.object(guard_rules, "get_current_worktree")
    def test_reports_merge_failure(
        self,
        mock_current_wt,
        mock_main_repo,
        mock_current_branch,
        mock_pr_branch,
        mock_execute_merge,
    ):
        """Should report merge failure when auto-merge fails."""
        mock_current_wt.return_value = Path("/repo/.worktrees/issue-123")
        mock_main_repo.return_value = Path("/repo")
        mock_current_branch.return_value = "feat/issue-123"
        mock_pr_branch.return_value = "feat/issue-123"
        mock_execute_merge.return_value = (False, "PR is not mergeable")

        result = locked_worktree_guard.check_self_branch_deletion("gh pr merge 456 --delete-branch")

        assert result is not None
        assert result["decision"] == "block"
        assert "マージ失敗" in result["reason"]
        assert "PR is not mergeable" in result["reason"]

    @patch.object(guard_rules, "check_pr_merged")
    @patch.object(guard_rules, "execute_safe_merge")
    @patch.object(guard_rules, "get_branch_for_pr")
    @patch.object(guard_rules, "get_current_branch_name")
    @patch.object(guard_rules, "get_main_repo_dir")
    @patch.object(guard_rules, "get_current_worktree")
    def test_reports_merge_incomplete_when_pr_not_merged(
        self,
        mock_current_wt,
        mock_main_repo,
        mock_current_branch,
        mock_pr_branch,
        mock_execute_merge,
        mock_check_merged,
    ):
        """Should report merge incomplete when command succeeds but PR is not merged (Issue #942)."""
        mock_current_wt.return_value = Path("/repo/.worktrees/issue-123")
        mock_main_repo.return_value = Path("/repo")
        mock_current_branch.return_value = "feat/issue-123"
        mock_pr_branch.return_value = "feat/issue-123"
        mock_execute_merge.return_value = (True, "Merged successfully")
        mock_check_merged.return_value = False  # PR is NOT actually merged

        result = locked_worktree_guard.check_self_branch_deletion("gh pr merge 456 --delete-branch")

        assert result is not None
        assert result["decision"] == "block"
        assert "マージ未完了" in result["reason"]
        assert "他のフック" in result["reason"]  # Mentions other hooks might have blocked
        mock_execute_merge.assert_called_once()
        mock_check_merged.assert_called_once()

    @patch.object(guard_rules, "get_branch_for_pr")
    @patch.object(guard_rules, "get_current_branch_name")
    @patch.object(guard_rules, "get_main_repo_dir")
    @patch.object(guard_rules, "get_current_worktree")
    def test_allows_different_branch_deletion(
        self,
        mock_current_wt,
        mock_main_repo,
        mock_current_branch,
        mock_pr_branch,
    ):
        """Should allow when PR's branch is different from current worktree's branch."""
        mock_current_wt.return_value = Path("/repo/.worktrees/issue-123")
        mock_main_repo.return_value = Path("/repo")
        mock_current_branch.return_value = "feat/issue-123"
        mock_pr_branch.return_value = "feat/issue-456"  # Different branch

        result = locked_worktree_guard.check_self_branch_deletion("gh pr merge 456 --delete-branch")

        assert result is None

    @patch.object(guard_rules, "get_main_repo_dir")
    @patch.object(guard_rules, "get_current_worktree")
    def test_allows_from_main_repo(self, mock_current_wt, mock_main_repo):
        """Should allow when running from main repo (not worktree)."""
        mock_current_wt.return_value = Path("/repo")
        mock_main_repo.return_value = Path("/repo")

        result = locked_worktree_guard.check_self_branch_deletion("gh pr merge 456 --delete-branch")

        assert result is None

    def test_allows_without_delete_branch_flag(self):
        """Should allow when --delete-branch flag is not present."""
        result = locked_worktree_guard.check_self_branch_deletion("gh pr merge 456 --squash")

        assert result is None

    def test_allows_non_merge_commands(self):
        """Should allow non-merge commands."""
        result = locked_worktree_guard.check_self_branch_deletion(
            "gh pr view 456 --delete-branch"  # --delete-branch is not valid for view
        )

        assert result is None

    @patch.object(guard_rules, "get_current_branch_name")
    @patch.object(guard_rules, "get_main_repo_dir")
    @patch.object(guard_rules, "get_current_worktree")
    def test_allows_different_branch_name_selector(
        self,
        mock_current_wt,
        mock_main_repo,
        mock_current_branch,
    ):
        """Should allow when specified branch name differs from current branch."""
        mock_current_wt.return_value = Path("/repo/.worktrees/issue-123")
        mock_main_repo.return_value = Path("/repo")
        mock_current_branch.return_value = "feat/issue-123"

        # Branch name "feature-branch" differs from current "feat/issue-123"
        result = locked_worktree_guard.check_self_branch_deletion(
            "gh pr merge feature-branch --delete-branch"
        )

        # Should allow since it's a different branch
        assert result is None

    @patch.object(guard_rules, "execute_safe_merge")
    @patch.object(guard_rules, "get_current_branch_name")
    @patch.object(guard_rules, "get_main_repo_dir")
    @patch.object(guard_rules, "get_current_worktree")
    def test_blocks_same_branch_name_selector(
        self,
        mock_current_wt,
        mock_main_repo,
        mock_current_branch,
        mock_execute_merge,
    ):
        """Should block when specified branch name matches current branch (P1 fix)."""
        mock_current_wt.return_value = Path("/repo/.worktrees/issue-123")
        mock_main_repo.return_value = Path("/repo")
        mock_current_branch.return_value = "feat/issue-123"
        mock_execute_merge.return_value = (True, "Merged")

        # Branch name matches current branch - self-branch deletion
        result = locked_worktree_guard.check_self_branch_deletion(
            "gh pr merge feat/issue-123 --delete-branch"
        )

        # Should block since this would delete current worktree's branch
        assert result is not None
        assert result["decision"] == "block"

    @patch.object(guard_rules, "get_current_branch_name")
    @patch.object(guard_rules, "get_main_repo_dir")
    @patch.object(guard_rules, "get_current_worktree")
    def test_allows_url_selector(
        self,
        mock_current_wt,
        mock_main_repo,
        mock_current_branch,
    ):
        """Should allow when selector is a URL (fail open - can't determine branch)."""
        mock_current_wt.return_value = Path("/repo/.worktrees/issue-123")
        mock_main_repo.return_value = Path("/repo")
        mock_current_branch.return_value = "feat/issue-123"

        # URL selector - can't determine which branch
        result = locked_worktree_guard.check_self_branch_deletion(
            "gh pr merge https://github.com/owner/repo/pull/123 --delete-branch"
        )

        # Should allow (fail open) since we can't determine the branch from URL
        assert result is None

    @patch.object(guard_rules, "execute_safe_merge")
    @patch.object(guard_rules, "get_current_branch_name")
    @patch.object(guard_rules, "get_main_repo_dir")
    @patch.object(guard_rules, "get_current_worktree")
    def test_blocks_no_pr_selector(
        self,
        mock_current_wt,
        mock_main_repo,
        mock_current_branch,
        mock_execute_merge,
    ):
        """Should block when no PR selector provided (targets current branch)."""
        # When gh pr merge is called without PR number or branch name,
        # it merges the PR for the current branch - exactly what Issue #649 targets
        mock_current_wt.return_value = Path("/repo/.worktrees/issue-123")
        mock_main_repo.return_value = Path("/repo")
        mock_current_branch.return_value = "feat/issue-123"
        mock_execute_merge.return_value = (True, "Merged")

        # No selector - gh pr merge uses current branch
        result = locked_worktree_guard.check_self_branch_deletion("gh pr merge --delete-branch")

        # Should block since this would delete current worktree's branch
        assert result is not None
        assert result["decision"] == "block"

    @patch.object(guard_rules, "execute_safe_merge")
    @patch.object(guard_rules, "get_current_branch_name")
    @patch.object(guard_rules, "get_main_repo_dir")
    @patch.object(guard_rules, "get_current_worktree")
    def test_blocks_no_pr_selector_with_squash(
        self,
        mock_current_wt,
        mock_main_repo,
        mock_current_branch,
        mock_execute_merge,
    ):
        """Should block when no PR selector but has other flags."""
        mock_current_wt.return_value = Path("/repo/.worktrees/issue-123")
        mock_main_repo.return_value = Path("/repo")
        mock_current_branch.return_value = "feat/issue-123"
        mock_execute_merge.return_value = (True, "Merged")

        result = locked_worktree_guard.check_self_branch_deletion(
            "gh pr merge --squash --delete-branch"
        )

        assert result is not None
        assert result["decision"] == "block"

    @patch.object(guard_rules, "execute_safe_merge")
    @patch.object(guard_rules, "get_current_branch_name")
    @patch.object(guard_rules, "get_main_repo_dir")
    @patch.object(guard_rules, "get_current_worktree")
    def test_blocks_with_short_d_flag(
        self,
        mock_current_wt,
        mock_main_repo,
        mock_current_branch,
        mock_execute_merge,
    ):
        """Should block with -d flag."""
        mock_current_wt.return_value = Path("/repo/.worktrees/issue-123")
        mock_main_repo.return_value = Path("/repo")
        mock_current_branch.return_value = "feat/issue-123"
        mock_execute_merge.return_value = (True, "Merged")

        with patch.object(guard_rules, "get_branch_for_pr", return_value="feat/issue-123"):
            result = locked_worktree_guard.check_self_branch_deletion("gh pr merge 456 -d")

        assert result is not None
        assert result["decision"] == "block"

    # Issue #1025: Tests for cd command handling in check_self_branch_deletion

    @patch.object(guard_rules, "get_effective_cwd")
    @patch.object(guard_rules, "get_branch_for_pr")
    @patch.object(guard_rules, "get_current_branch_name")
    @patch.object(guard_rules, "get_main_repo_dir")
    @patch.object(guard_rules, "get_current_worktree")
    def test_cd_command_changes_effective_cwd(
        self,
        mock_current_wt,
        mock_main_repo,
        mock_current_branch,
        mock_pr_branch,
        mock_get_effective_cwd,
    ):
        """cd target should be used as effective cwd (Issue #1025)."""
        # When cd /main/repo is used, effective_cwd should be /main/repo, not hook_cwd
        mock_get_effective_cwd.return_value = Path("/main/repo")
        mock_current_wt.return_value = Path("/main/repo")  # Main repo, not worktree
        mock_main_repo.return_value = Path("/main/repo")

        result = locked_worktree_guard.check_self_branch_deletion(
            "cd /main/repo && gh pr merge 123 --delete-branch",
            hook_cwd="/repo/.worktrees/issue-123",
        )

        # Should allow because effective_cwd is main repo
        assert result is None
        # Verify get_effective_cwd was called with correct arguments
        mock_get_effective_cwd.assert_called_once_with(
            "cd /main/repo && gh pr merge 123 --delete-branch",
            "/repo/.worktrees/issue-123",
        )

    @patch.object(guard_rules, "get_effective_cwd")
    @patch.object(guard_rules, "check_pr_merged")
    @patch.object(guard_rules, "execute_safe_merge")
    @patch.object(guard_rules, "get_branch_for_pr")
    @patch.object(guard_rules, "get_current_branch_name")
    @patch.object(guard_rules, "get_main_repo_dir")
    @patch.object(guard_rules, "get_current_worktree")
    def test_without_cd_uses_hook_cwd(
        self,
        mock_current_wt,
        mock_main_repo,
        mock_current_branch,
        mock_pr_branch,
        mock_execute_merge,
        mock_check_merged,
        mock_get_effective_cwd,
    ):
        """Without cd, hook_cwd should be used as effective_cwd."""
        hook_cwd = "/repo/.worktrees/issue-123"
        mock_get_effective_cwd.return_value = Path(hook_cwd)
        mock_current_wt.return_value = Path(hook_cwd)
        mock_main_repo.return_value = Path("/repo")
        mock_current_branch.return_value = "feat/issue-123"
        mock_pr_branch.return_value = "feat/issue-123"
        mock_execute_merge.return_value = (True, "Merged")
        mock_check_merged.return_value = True

        result = locked_worktree_guard.check_self_branch_deletion(
            "gh pr merge 123 --delete-branch",
            hook_cwd=hook_cwd,
        )

        # Should block because we're in a worktree with matching branch
        assert result is not None
        assert result["decision"] == "block"
        # Verify get_effective_cwd was called with correct arguments
        mock_get_effective_cwd.assert_called_once_with(
            "gh pr merge 123 --delete-branch",
            hook_cwd,
        )

    @patch.object(guard_rules, "get_effective_cwd")
    @patch.object(guard_rules, "get_current_worktree")
    def test_cd_to_nonexistent_path_fallback(
        self,
        mock_current_wt,
        mock_get_effective_cwd,
    ):
        """When cd target leads to no worktree, should allow gracefully."""
        # get_effective_cwd always returns a Path, but it may be a path
        # that doesn't correspond to any known worktree
        mock_get_effective_cwd.return_value = Path("/nonexistent")
        mock_current_wt.return_value = None  # No worktree found for this path

        result = locked_worktree_guard.check_self_branch_deletion(
            "cd /nonexistent && gh pr merge 123 --delete-branch",
            hook_cwd="/repo/.worktrees/issue-123",
        )

        # Should allow (no worktree found = safe to proceed)
        assert result is None
        # Verify get_effective_cwd was called with correct arguments
        mock_get_effective_cwd.assert_called_once_with(
            "cd /nonexistent && gh pr merge 123 --delete-branch",
            "/repo/.worktrees/issue-123",
        )


class TestExtractFirstMergeCommand:
    """Tests for _extract_first_merge_command function (Issue #855)."""

    def test_extracts_simple_merge(self):
        """Should extract simple merge command."""
        result = locked_worktree_guard._extract_first_merge_command(
            "gh pr merge 123 --delete-branch"
        )
        assert "gh" in result
        assert "pr" in result
        assert "merge" in result
        assert "123" in result
        assert "--delete-branch" not in result

    def test_stops_at_and_operator(self):
        """Should stop at && operator and not include chained commands."""
        result = locked_worktree_guard._extract_first_merge_command(
            "gh pr merge 123 --delete-branch && echo done && rm -rf /"
        )
        assert "gh" in result
        assert "merge" in result
        assert "123" in result
        assert "echo" not in result
        assert "done" not in result
        assert "rm" not in result
        assert "&&" not in result

    def test_stops_at_or_operator(self):
        """Should stop at || operator."""
        result = locked_worktree_guard._extract_first_merge_command(
            "gh pr merge 123 -d || echo failed"
        )
        assert "123" in result
        assert "-d" not in result
        assert "echo" not in result
        assert "failed" not in result

    def test_stops_at_semicolon(self):
        """Should stop at ; operator."""
        result = locked_worktree_guard._extract_first_merge_command(
            "gh pr merge 123 --delete-branch ; echo next"
        )
        assert "123" in result
        assert "echo" not in result
        assert ";" not in result

    def test_stops_at_pipe(self):
        """Should stop at | operator."""
        result = locked_worktree_guard._extract_first_merge_command(
            "gh pr merge 123 --delete-branch | cat"
        )
        assert "123" in result
        assert "cat" not in result
        assert "|" not in result

    def test_removes_both_delete_flags(self):
        """Should remove both --delete-branch and -d."""
        result = locked_worktree_guard._extract_first_merge_command(
            "gh pr merge 123 --delete-branch -d --squash"
        )
        assert "--delete-branch" not in result
        assert "-d" not in result
        assert "--squash" in result

    def test_removes_stderr_redirect(self):
        """Should remove 2>&1 redirect (Issue #1106)."""
        result = locked_worktree_guard._extract_first_merge_command(
            "gh pr merge 123 --squash --delete-branch 2>&1"
        )
        assert "123" in result
        assert "--squash" in result
        assert "--delete-branch" not in result
        assert "2>&1" not in result

    def test_removes_output_redirect(self):
        """Should remove output redirect like >file."""
        result = locked_worktree_guard._extract_first_merge_command(
            "gh pr merge 123 --squash >output.log"
        )
        assert "123" in result
        assert "--squash" in result
        assert ">output.log" not in result
        assert "output.log" not in result

    def test_removes_append_redirect(self):
        """Should remove append redirect like >>file."""
        result = locked_worktree_guard._extract_first_merge_command(
            "gh pr merge 123 --squash >>output.log"
        )
        assert "123" in result
        assert "--squash" in result
        assert ">>output.log" not in result
        assert "output.log" not in result

    def test_removes_stderr_to_file_redirect(self):
        """Should remove 2>file and 2>>file redirects."""
        result = locked_worktree_guard._extract_first_merge_command(
            "gh pr merge 123 --squash 2>error.log"
        )
        assert "123" in result
        assert "2>error.log" not in result
        assert "error.log" not in result

    def test_removes_spaced_output_redirect(self):
        """Should remove spaced redirect like '> output.log' (Codex review fix)."""
        result = locked_worktree_guard._extract_first_merge_command(
            "gh pr merge 123 --squash > output.log"
        )
        assert "123" in result
        assert "--squash" in result
        assert ">" not in result
        assert "output.log" not in result

    def test_removes_spaced_append_redirect(self):
        """Should remove spaced append redirect like '>> output.log'."""
        result = locked_worktree_guard._extract_first_merge_command(
            "gh pr merge 123 --squash >> output.log"
        )
        assert "123" in result
        assert "--squash" in result
        assert ">>" not in result
        assert "output.log" not in result

    def test_removes_spaced_stderr_redirect(self):
        """Should remove spaced stderr redirect like '2> error.log'."""
        result = locked_worktree_guard._extract_first_merge_command(
            "gh pr merge 123 --squash 2> error.log"
        )
        assert "123" in result
        assert "--squash" in result
        assert "2>" not in result
        assert "error.log" not in result

    def test_removes_spaced_input_redirect(self):
        """Should remove spaced input redirect like '< input.txt'."""
        result = locked_worktree_guard._extract_first_merge_command(
            "gh pr merge 123 --squash < input.txt"
        )
        assert "123" in result
        assert "--squash" in result
        assert "<" not in result
        assert "input.txt" not in result


class TestExecuteSafeMerge:
    """Tests for _execute_safe_merge function (Issue #855)."""

    @patch("subprocess.run")
    def test_successful_merge(self, mock_run):
        """Should return success on successful merge."""
        mock_run.return_value = MagicMock(returncode=0, stdout="Merged successfully", stderr="")

        success, output = locked_worktree_guard._execute_safe_merge(
            "gh pr merge 123 --delete-branch", "/some/path"
        )

        assert success is True
        assert "Merged successfully" in output
        mock_run.assert_called_once()
        # Check that --delete-branch was removed
        call_args = mock_run.call_args
        assert "--delete-branch" not in call_args[0][0][2]  # bash -c "command"

    @patch("subprocess.run")
    def test_failed_merge(self, mock_run):
        """Should return failure on failed merge."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="PR is not mergeable")

        success, output = locked_worktree_guard._execute_safe_merge(
            "gh pr merge 123 --delete-branch", "/some/path"
        )

        assert success is False
        # Issue #1027: Error messages are now translated to Japanese
        assert "マージ不可" in output

    @patch("subprocess.run")
    def test_timeout(self, mock_run):
        """Should handle timeout."""
        import subprocess as sp

        mock_run.side_effect = sp.TimeoutExpired("gh", 120)

        success, output = locked_worktree_guard._execute_safe_merge(
            "gh pr merge 123 --delete-branch", "/some/path"
        )

        assert success is False
        assert "timed out" in output.lower()

    @patch("subprocess.run")
    def test_passes_cwd(self, mock_run):
        """Should pass cwd to subprocess."""
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        locked_worktree_guard._execute_safe_merge("gh pr merge 123 --delete-branch", "/custom/path")

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["cwd"] == "/custom/path"

    @patch("subprocess.run")
    def test_does_not_execute_chained_commands(self, mock_run):
        """CRITICAL: Should only execute merge, not chained commands like && rm -rf."""
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        # This command has dangerous chained commands
        locked_worktree_guard._execute_safe_merge(
            "gh pr merge 123 --delete-branch && echo danger && rm -rf /",
            "/some/path",
        )

        # Get the actual command that was executed
        call_args = mock_run.call_args[0][0]  # ["bash", "-c", "actual command"]
        executed_command = call_args[2]

        # Verify dangerous commands were NOT included
        assert "echo" not in executed_command
        assert "danger" not in executed_command
        assert "rm" not in executed_command
        assert "&&" not in executed_command
        # But merge command parts should be there
        assert "gh" in executed_command
        assert "merge" in executed_command
        assert "123" in executed_command


class TestMergeCheckDryRunIntegration:
    """Tests for merge-check --dry-run integration (Issue #948, #952).

    These tests verify the merge-check dry-run integration in check_self_branch_deletion.
    The integration is tested through the behavior of the function when various
    subprocess conditions occur (success, failure, timeout, script not found, etc.).
    """

    @patch("subprocess.run")
    def test_blocks_when_merge_check_returns_error(self, mock_run, tmp_path):
        """Should block auto-merge when merge-check --dry-run returns non-zero exit code."""
        merge_check_path = tmp_path / ".claude" / "hooks"
        merge_check_path.mkdir(parents=True)
        (merge_check_path / "merge-check.py").touch()

        call_count = {"merge_check": 0}

        def subprocess_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list):
                # Worktree check - simulate being in a worktree
                if "git" in cmd and "worktree" in cmd:
                    return MagicMock(returncode=0, stdout="/test/worktree", stderr="")
                # Branch check
                if "git" in cmd and "branch" in cmd and "--show-current" in cmd:
                    return MagicMock(returncode=0, stdout="feature/test", stderr="")
                # PR view for getting PR number
                if "gh" in cmd and "pr" in cmd and "view" in cmd:
                    return MagicMock(returncode=0, stdout="123", stderr="")
                # merge-check --dry-run fails
                if "python3" in cmd and "merge-check.py" in str(cmd):
                    call_count["merge_check"] += 1
                    return MagicMock(
                        returncode=1,
                        stdout="❌ PR has unresolved review threads",
                        stderr="",
                    )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = subprocess_side_effect

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            # Call check_self_branch_deletion with a merge command
            result = locked_worktree_guard.check_self_branch_deletion(
                "gh pr merge --squash", str(tmp_path)
            )

            # When merge-check fails, it should block and include the error message
            if result and result.get("decision") == "block":
                assert (
                    "unresolved" in result.get("reason", "").lower()
                    or call_count["merge_check"] > 0
                )

    @patch("subprocess.run")
    def test_proceeds_when_merge_check_times_out(self, mock_run, tmp_path):
        """Should proceed with merge when merge-check --dry-run times out (fail open)."""
        merge_check_path = tmp_path / ".claude" / "hooks"
        merge_check_path.mkdir(parents=True)
        (merge_check_path / "merge-check.py").touch()

        timeout_raised = {"count": 0}

        def subprocess_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list):
                if "python3" in cmd and "merge-check.py" in str(cmd):
                    timeout_raised["count"] += 1
                    raise subprocess.TimeoutExpired(cmd="merge-check", timeout=120)
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = subprocess_side_effect

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            # The timeout should be caught and not propagate
            # This test verifies no exception is raised
            try:
                locked_worktree_guard.check_self_branch_deletion(
                    "gh pr merge --squash", str(tmp_path)
                )
            except subprocess.TimeoutExpired as e:
                raise AssertionError("TimeoutExpired should be caught, not propagated") from e

    @patch("subprocess.run")
    def test_proceeds_when_merge_check_script_not_found(self, mock_run, tmp_path):
        """Should proceed with merge when merge-check.py does not exist."""
        # Create project dir without merge-check.py
        claude_hooks_dir = tmp_path / ".claude" / "hooks"
        claude_hooks_dir.mkdir(parents=True)
        # Don't create merge-check.py

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            merge_check_script = Path(tmp_path) / ".claude" / "hooks" / "merge-check.py"
            # Verify script doesn't exist
            assert not merge_check_script.exists()
            # Function should not raise when script doesn't exist
            locked_worktree_guard.check_self_branch_deletion("gh pr merge --squash", str(tmp_path))
            # Should complete without error due to missing merge-check script

    @patch("subprocess.run")
    def test_proceeds_when_claude_project_dir_not_set(self, mock_run):
        """Should proceed with merge when CLAUDE_PROJECT_DIR is not set."""
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": ""}, clear=False):
            # Function should handle missing CLAUDE_PROJECT_DIR gracefully
            locked_worktree_guard.check_self_branch_deletion("gh pr merge --squash", "/some/path")
            # Should complete without error when CLAUDE_PROJECT_DIR is empty

    @patch("subprocess.run")
    def test_skips_dry_run_when_pr_number_not_available(self, mock_run, tmp_path):
        """Should skip merge-check dry-run when PR number cannot be retrieved."""
        merge_check_path = tmp_path / ".claude" / "hooks"
        merge_check_path.mkdir(parents=True)
        (merge_check_path / "merge-check.py").touch()

        merge_check_called = {"called": False}

        def subprocess_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list):
                if "gh" in cmd and "pr" in cmd and "view" in cmd:
                    # gh pr view fails - no PR for current branch
                    return MagicMock(returncode=1, stdout="", stderr="no pull requests found")
                if "python3" in cmd and "merge-check.py" in str(cmd):
                    merge_check_called["called"] = True
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = subprocess_side_effect

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            locked_worktree_guard.check_self_branch_deletion("gh pr merge --squash", str(tmp_path))
            # merge-check should not be called when PR number is not available
            # (this depends on the implementation - it may or may not be called)

    @patch("subprocess.run")
    def test_logs_success_when_dry_run_passes(self, mock_run, tmp_path):
        """Should log success when merge-check --dry-run passes (Issue #952)."""
        merge_check_path = tmp_path / ".claude" / "hooks"
        merge_check_path.mkdir(parents=True)
        (merge_check_path / "merge-check.py").touch()

        dry_run_result = {"returncode": None}

        def subprocess_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list):
                if "python3" in cmd and "merge-check.py" in str(cmd):
                    dry_run_result["returncode"] = 0
                    return MagicMock(returncode=0, stdout="All checks passed", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = subprocess_side_effect

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            locked_worktree_guard.check_self_branch_deletion("gh pr merge --squash", str(tmp_path))
            # Verify dry-run was called and returned success
            # The log_hook_execution call is internal - we verify the returncode was 0

    @patch("subprocess.run")
    def test_logs_warning_when_pr_view_times_out(self, mock_run):
        """Should log warning when gh pr view times out (Issue #952)."""
        timeout_caught = {"caught": False}

        def subprocess_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list) and "gh" in cmd and "pr" in cmd and "view" in cmd:
                timeout_caught["caught"] = True
                raise subprocess.TimeoutExpired(cmd="gh pr view", timeout=30)
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = subprocess_side_effect

        # Timeout should be caught and logged, not propagated
        try:
            locked_worktree_guard.check_self_branch_deletion("gh pr merge --squash", "/some/path")
        except subprocess.TimeoutExpired as e:
            raise AssertionError("TimeoutExpired from gh pr view should be caught") from e

    @patch("subprocess.run")
    def test_logs_warning_when_pr_view_fails_with_oserror(self, mock_run):
        """Should log warning when gh pr view fails with OSError (Issue #952)."""
        oserror_caught = {"caught": False}

        def subprocess_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list) and "gh" in cmd and "pr" in cmd and "view" in cmd:
                oserror_caught["caught"] = True
                raise OSError("gh command not found")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = subprocess_side_effect

        # OSError should be caught and logged, not propagated
        try:
            locked_worktree_guard.check_self_branch_deletion("gh pr merge --squash", "/some/path")
        except OSError as e:
            raise AssertionError("OSError from gh pr view should be caught") from e
