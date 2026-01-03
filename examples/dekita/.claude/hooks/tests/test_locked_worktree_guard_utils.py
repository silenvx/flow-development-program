#!/usr/bin/env python3
"""Tests for locked-worktree-guard.py - utils module."""

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


class TestExtractUnlockTargets:
    """Tests for extract_unlock_targets_from_command function.

    Issue #700: Detect unlock in chained commands.
    """

    def test_simple_unlock_command(self):
        """Test extracting path from simple unlock command."""
        result = locked_worktree_guard.extract_unlock_targets_from_command(
            "git worktree unlock /path/to/worktree"
        )
        assert len(result) == 1
        assert result[0] == Path("/path/to/worktree")

    def test_unlock_and_remove_chain(self):
        """Test extracting path from unlock && remove chain."""
        result = locked_worktree_guard.extract_unlock_targets_from_command(
            "git worktree unlock /path/to/worktree && git worktree remove /path/to/worktree"
        )
        assert len(result) == 1
        assert result[0] == Path("/path/to/worktree")

    def test_unlock_with_c_flag(self):
        """Test extracting path with -C flag."""
        result = locked_worktree_guard.extract_unlock_targets_from_command(
            "git -C /repo worktree unlock .worktrees/issue-123"
        )
        assert len(result) == 1
        # Path should be resolved relative to -C argument
        assert str(result[0]).endswith("issue-123")

    def test_no_unlock_command(self):
        """Test returns empty list when no unlock command."""
        result = locked_worktree_guard.extract_unlock_targets_from_command(
            "git worktree remove /path/to/worktree"
        )
        assert len(result) == 0

    def test_unlock_with_hook_cwd(self):
        """Test path resolution with hook_cwd."""
        result = locked_worktree_guard.extract_unlock_targets_from_command(
            "git worktree unlock .worktrees/issue-123",
            hook_cwd="/home/user/repo",
        )
        assert len(result) == 1
        # Use str comparison with endswith to handle macOS path resolution
        # (e.g., /home -> /System/Volumes/Data/home)
        assert str(result[0]).endswith("/home/user/repo/.worktrees/issue-123")

    def test_multiple_unlocks(self):
        """Test extracting multiple unlock targets."""
        result = locked_worktree_guard.extract_unlock_targets_from_command(
            "git worktree unlock /path1 && git worktree unlock /path2"
        )
        assert len(result) == 2
        assert Path("/path1") in result
        assert Path("/path2") in result


class TestUnlockAndRemoveBypass:
    """Tests for Issue #700: unlock && remove should bypass lock check.

    When a command contains both 'git worktree unlock <path>' and
    'git worktree remove <path>' for the same path, the lock check
    should be skipped since unlock will run first.
    """

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(worktree_manager, "is_cwd_inside_worktree", return_value=False)
    def test_unlock_and_remove_same_path_bypasses_lock_check(self, mock_is_cwd, mock_get_locked):
        """Test that unlock && remove for same path bypasses lock check."""
        # Mock that the worktree is locked
        mock_get_locked.return_value = [Path("/repo/.worktrees/issue-123")]

        # Command includes both unlock and remove for same path
        result = locked_worktree_guard.check_worktree_remove(
            "git worktree unlock /repo/.worktrees/issue-123 && git worktree remove /repo/.worktrees/issue-123",
            hook_cwd="/repo",
        )

        # Should approve (not block) because unlock is in the same command
        assert result is None

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(worktree_manager, "is_cwd_inside_worktree", return_value=False)
    def test_remove_without_unlock_still_blocks(self, mock_is_cwd, mock_get_locked):
        """Test that remove without unlock still blocks locked worktree."""
        # Mock that the worktree is locked
        mock_get_locked.return_value = [Path("/repo/.worktrees/issue-123")]

        # Command only has remove, no unlock
        result = locked_worktree_guard.check_worktree_remove(
            "git worktree remove /repo/.worktrees/issue-123",
            hook_cwd="/repo",
        )

        # Should block because worktree is locked and no unlock in command
        assert result is not None
        assert result["decision"] == "block"

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(worktree_manager, "is_cwd_inside_worktree", return_value=False)
    def test_unlock_different_path_still_blocks(self, mock_is_cwd, mock_get_locked):
        """Test that unlock for different path still blocks."""
        # Mock that issue-123 is locked
        mock_get_locked.return_value = [Path("/repo/.worktrees/issue-123")]

        # Command unlocks different worktree
        result = locked_worktree_guard.check_worktree_remove(
            "git worktree unlock /repo/.worktrees/other && git worktree remove /repo/.worktrees/issue-123",
            hook_cwd="/repo",
        )

        # Should block because issue-123 is locked but unlock is for 'other'
        assert result is not None
        assert result["decision"] == "block"

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(worktree_manager, "is_cwd_inside_worktree", return_value=False)
    def test_cd_before_unlock_and_remove(self, mock_is_cwd, mock_get_locked):
        """Test cd before unlock && remove pattern."""
        # Mock that the worktree is locked
        mock_get_locked.return_value = [Path("/repo/.worktrees/issue-123")]

        # Command with cd before unlock && remove
        result = locked_worktree_guard.check_worktree_remove(
            "cd /repo && git worktree unlock .worktrees/issue-123 && git worktree remove .worktrees/issue-123",
            hook_cwd="/home/user",
        )

        # Should approve because unlock is in the same command
        assert result is None

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(worktree_manager, "is_cwd_inside_worktree", return_value=False)
    def test_p2_unlock_after_remove_still_blocks(self, mock_is_cwd, mock_get_locked):
        """P2: unlock AFTER remove should still block.

        Codex review P2: Order matters - unlock must come before remove.
        """
        # Mock that the worktree is locked
        mock_get_locked.return_value = [Path("/repo/.worktrees/issue-123")]

        # Command has unlock AFTER remove - should block
        result = locked_worktree_guard.check_worktree_remove(
            "git worktree remove /repo/.worktrees/issue-123 && git worktree unlock /repo/.worktrees/issue-123",
            hook_cwd="/repo",
        )

        # Should block because unlock runs after remove
        assert result is not None
        assert result["decision"] == "block"

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(worktree_manager, "is_cwd_inside_worktree", return_value=False)
    def test_p3_cd_scoping_different_paths(self, mock_is_cwd, mock_get_locked):
        """P3: cd scoping - unlock and remove resolve to different paths.

        Codex review P3: When multiple cd commands exist, each git command
        should use the cd target that applies to its segment.

        In: cd /repo && git worktree unlock .wt/foo && cd /tmp && git worktree remove .wt/foo
        - unlock targets /repo/.wt/foo
        - remove targets /tmp/.wt/foo
        These are different paths, so unlock should NOT bypass the lock check.
        """
        # Mock that /tmp/.wt/foo is locked (the remove target)
        mock_get_locked.return_value = [Path("/tmp/.wt/foo")]

        result = locked_worktree_guard.check_worktree_remove(
            "cd /repo && git worktree unlock .wt/foo && cd /tmp && git worktree remove .wt/foo",
            hook_cwd="/home/user",
        )

        # Should block because unlock is for /repo/.wt/foo, not /tmp/.wt/foo
        assert result is not None
        assert result["decision"] == "block"

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(worktree_manager, "is_cwd_inside_worktree", return_value=False)
    def test_semicolon_cd_scoping_preserved(self, mock_is_cwd, mock_get_locked):
        """Codex review: cd with semicolon should work.

        In shell, 'cd /repo; git worktree unlock ...' executes unlock in /repo.
        The cd effect carries over across semicolons.
        """
        # Mock that the worktree is locked
        mock_get_locked.return_value = [Path("/repo/.worktrees/issue-123")]

        # Command uses semicolon - cd effect should carry over
        result = locked_worktree_guard.check_worktree_remove(
            "cd /repo; git worktree unlock .worktrees/issue-123 && git worktree remove .worktrees/issue-123",
            hook_cwd="/home/user",
        )

        # Should approve because unlock is in the same command and cd carries over
        assert result is None

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(worktree_manager, "is_cwd_inside_worktree", return_value=False)
    def test_relative_c_flag_with_cd(self, mock_is_cwd, mock_get_locked):
        """Codex review: relative -C flag should resolve against cd_target.

        In: cd /repo && git -C sub worktree unlock foo && git -C sub worktree remove foo
        - cd changes to /repo
        - -C sub is relative, should resolve to /repo/sub
        - unlock and remove both target /repo/sub/foo
        """
        # Mock that /repo/sub/foo is locked
        mock_get_locked.return_value = [Path("/repo/sub/foo")]

        result = locked_worktree_guard.check_worktree_remove(
            "cd /repo && git -C sub worktree unlock foo && git -C sub worktree remove foo",
            hook_cwd="/home/user",
        )

        # Should approve because unlock is for same path as remove
        assert result is None

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(worktree_manager, "is_cwd_inside_worktree", return_value=False)
    def test_p1_unlock_with_or_connector_still_blocks(self, mock_is_cwd, mock_get_locked):
        """P1: unlock || remove should still block.

        Codex review P1: When connected by ||, remove runs when unlock FAILS,
        so the lock is still present. Should NOT bypass lock check.
        """
        # Mock that the worktree is locked
        mock_get_locked.return_value = [Path("/repo/.worktrees/issue-123")]

        # Command uses || - remove runs when unlock fails
        result = locked_worktree_guard.check_worktree_remove(
            "git worktree unlock /repo/.worktrees/issue-123 || git worktree remove /repo/.worktrees/issue-123",
            hook_cwd="/repo",
        )

        # Should block because || means remove runs when unlock fails
        assert result is not None
        assert result["decision"] == "block"

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(worktree_manager, "is_cwd_inside_worktree", return_value=False)
    def test_unlock_with_and_connector_bypasses(self, mock_is_cwd, mock_get_locked):
        """unlock && remove should bypass lock check.

        When connected by &&, remove runs when unlock SUCCEEDS,
        so the lock is no longer present. Should bypass lock check.
        """
        # Mock that the worktree is locked
        mock_get_locked.return_value = [Path("/repo/.worktrees/issue-123")]

        # Command uses && - remove runs when unlock succeeds
        result = locked_worktree_guard.check_worktree_remove(
            "git worktree unlock /repo/.worktrees/issue-123 && git worktree remove /repo/.worktrees/issue-123",
            hook_cwd="/repo",
        )

        # Should approve because && means remove runs when unlock succeeds
        assert result is None

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(worktree_manager, "is_cwd_inside_worktree", return_value=False)
    def test_p1_unlock_with_semicolon_still_blocks(self, mock_is_cwd, mock_get_locked):
        """P1: unlock ; remove should still block.

        Codex review P1: When connected by ;, remove runs regardless of
        unlock outcome, so lock may still be present. Should NOT bypass.
        """
        # Mock that the worktree is locked
        mock_get_locked.return_value = [Path("/repo/.worktrees/issue-123")]

        # Command uses ; - remove runs regardless of unlock outcome
        result = locked_worktree_guard.check_worktree_remove(
            "git worktree unlock /repo/.worktrees/issue-123 ; git worktree remove /repo/.worktrees/issue-123",
            hook_cwd="/repo",
        )

        # Should block because ; means remove runs even if unlock fails
        assert result is not None
        assert result["decision"] == "block"


class TestIsBareRedirectOperator:
    """Tests for _is_bare_redirect_operator function (Issue #1106)."""

    def test_detects_bare_output_redirect(self):
        """Should detect bare > and >>."""
        assert locked_worktree_guard._is_bare_redirect_operator(">")
        assert locked_worktree_guard._is_bare_redirect_operator(">>")

    def test_detects_bare_fd_redirect(self):
        """Should detect bare 2> and 2>>."""
        assert locked_worktree_guard._is_bare_redirect_operator("2>")
        assert locked_worktree_guard._is_bare_redirect_operator("2>>")

    def test_detects_bare_input_redirect(self):
        """Should detect bare <."""
        assert locked_worktree_guard._is_bare_redirect_operator("<")

    def test_rejects_redirect_with_target(self):
        """Should not detect redirects that include the target."""
        assert not locked_worktree_guard._is_bare_redirect_operator(">file")
        assert not locked_worktree_guard._is_bare_redirect_operator(">>file")
        assert not locked_worktree_guard._is_bare_redirect_operator("2>&1")
        assert not locked_worktree_guard._is_bare_redirect_operator("<file")

    def test_rejects_non_redirect(self):
        """Should not detect non-redirect tokens."""
        assert not locked_worktree_guard._is_bare_redirect_operator("123")
        assert not locked_worktree_guard._is_bare_redirect_operator("--squash")


class TestIsShellRedirect:
    """Tests for _is_shell_redirect function (Issue #1106)."""

    def test_detects_stderr_to_stdout(self):
        """Should detect 2>&1."""
        assert locked_worktree_guard._is_shell_redirect("2>&1")

    def test_detects_stdout_to_stderr(self):
        """Should detect >&2."""
        assert locked_worktree_guard._is_shell_redirect(">&2")

    def test_detects_output_redirect(self):
        """Should detect >file."""
        assert locked_worktree_guard._is_shell_redirect(">file")
        assert locked_worktree_guard._is_shell_redirect(">>file")

    def test_detects_fd_output_redirect(self):
        """Should detect 2>file."""
        assert locked_worktree_guard._is_shell_redirect("2>file")
        assert locked_worktree_guard._is_shell_redirect("2>>file")

    def test_detects_input_redirect(self):
        """Should detect <file."""
        assert locked_worktree_guard._is_shell_redirect("<file")

    def test_rejects_non_redirect(self):
        """Should not detect non-redirect tokens."""
        assert not locked_worktree_guard._is_shell_redirect("123")
        assert not locked_worktree_guard._is_shell_redirect("--squash")
        assert not locked_worktree_guard._is_shell_redirect("gh")
        assert not locked_worktree_guard._is_shell_redirect("merge")


class TestImproveGhErrorMessage:
    """Tests for _improve_gh_error_message function (Issue #1027)."""

    def test_argument_count_error(self):
        """Should translate argument count errors."""
        error = "accepts at most 1 arg(s), received 2"
        result = locked_worktree_guard._improve_gh_error_message(error, "gh pr merge 123 456")
        assert "コマンド引数エラー" in result
        assert "gh pr merge 123 456" in result

    def test_pr_not_found_error(self):
        """Should translate PR not found errors."""
        error = "no pull requests found"
        result = locked_worktree_guard._improve_gh_error_message(error, "gh pr merge 999")
        assert "PR/ブランチが見つかりません" in result

    def test_could_not_resolve_error(self):
        """Should translate could not resolve errors."""
        error = "could not resolve to a pull request"
        result = locked_worktree_guard._improve_gh_error_message(error, "gh pr merge abc")
        assert "PR/ブランチが見つかりません" in result

    def test_not_mergeable_error(self):
        """Should translate not mergeable errors."""
        error = "not mergeable"
        result = locked_worktree_guard._improve_gh_error_message(error, "gh pr merge 123")
        assert "マージ不可" in result

    def test_cannot_be_merged_error(self):
        """Should translate cannot be merged errors."""
        error = "Pull request cannot be merged"
        result = locked_worktree_guard._improve_gh_error_message(error, "gh pr merge 123")
        assert "マージ不可" in result

    def test_unauthorized_error(self):
        """Should translate unauthorized errors."""
        error = "401 Unauthorized"
        result = locked_worktree_guard._improve_gh_error_message(error, "gh pr merge 123")
        assert "認証/権限エラー" in result
        assert "gh auth status" in result

    def test_permission_error(self):
        """Should translate permission errors."""
        error = "You don't have permission to merge"
        result = locked_worktree_guard._improve_gh_error_message(error, "gh pr merge 123")
        assert "認証/権限エラー" in result

    def test_forbidden_error(self):
        """Should translate 403 Forbidden errors."""
        error = "403 Forbidden"
        result = locked_worktree_guard._improve_gh_error_message(error, "gh pr merge 123")
        assert "認証/権限エラー" in result

    def test_unknown_error_returns_original_with_command(self):
        """Should return original error with command context for unknown errors."""
        error = "Some unknown error occurred"
        result = locked_worktree_guard._improve_gh_error_message(error, "gh pr merge 123")
        assert "Some unknown error occurred" in result
        assert "gh pr merge 123" in result

    def test_case_insensitive_matching(self):
        """Should match error patterns case-insensitively."""
        error = "NO PULL REQUESTS FOUND"
        result = locked_worktree_guard._improve_gh_error_message(error, "gh pr merge 999")
        assert "PR/ブランチが見つかりません" in result

    def test_permission_denied_error(self):
        """Should translate permission denied errors."""
        error = "permission denied"
        result = locked_worktree_guard._improve_gh_error_message(error, "gh pr merge 123")
        assert "認証/権限エラー" in result
        assert "gh auth status" in result

    def test_empty_error_message(self):
        """Should handle empty error message gracefully."""
        error = ""
        result = locked_worktree_guard._improve_gh_error_message(error, "gh pr merge 123")
        # Empty error should return with command context (default case)
        assert "gh pr merge 123" in result


class TestTryAutoCleanupWorktree:
    """Tests for _try_auto_cleanup_worktree function (Issue #1676)."""

    @patch("subprocess.run")
    def test_skips_cleanup_when_worktree_is_locked(self, mock_run, tmp_path):
        """Should not attempt cleanup if worktree is locked."""
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        with patch.object(
            guard_rules, "get_locked_worktrees", return_value=[(worktree, "feature")]
        ):
            success, msg = locked_worktree_guard._try_auto_cleanup_worktree(
                main_repo, worktree, "feature"
            )

        assert success is False
        assert "ロック中" in msg
        # Verify subprocess.run was not called (early return)
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_successful_cleanup(self, mock_run, tmp_path):
        """Should return success when worktree is deleted.

        Note: Remote branch is automatically deleted by GitHub's "delete_branch_on_merge" setting,
        so we no longer attempt to delete it manually.
        """
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(guard_rules, "get_locked_worktrees", return_value=[]):
            success, msg = locked_worktree_guard._try_auto_cleanup_worktree(
                main_repo, worktree, "feature"
            )

        assert success is True
        assert "worktree削除 成功" in msg
        # Verify only worktree removal was called (no branch deletion)
        assert mock_run.call_count == 1

    @patch("subprocess.run")
    def test_failed_worktree_cleanup(self, mock_run, tmp_path):
        """Should return failure when worktree removal fails."""
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="fatal: cannot remove worktree"
        )

        with patch.object(guard_rules, "get_locked_worktrees", return_value=[]):
            success, msg = locked_worktree_guard._try_auto_cleanup_worktree(
                main_repo, worktree, "feature"
            )

        assert success is False
        assert "削除失敗" in msg

    @patch("subprocess.run")
    def test_timeout_during_worktree_cleanup(self, mock_run, tmp_path):
        """Should handle timeout during worktree removal."""
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)

        with patch.object(guard_rules, "get_locked_worktrees", return_value=[]):
            success, msg = locked_worktree_guard._try_auto_cleanup_worktree(
                main_repo, worktree, "feature"
            )

        assert success is False
        assert "タイムアウト" in msg

    # Note: test_timeout_during_branch_deletion was removed because
    # remote branch deletion is now handled by GitHub's "delete_branch_on_merge" setting

    def test_oserror_during_path_resolution(self, tmp_path):
        """Should return failure when path resolution fails with OSError."""
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        worktree = tmp_path / "nonexistent"  # Does not exist

        with patch.object(guard_rules, "get_locked_worktrees", return_value=[]):
            with patch.object(Path, "resolve", side_effect=OSError("Permission denied")):
                success, msg = locked_worktree_guard._try_auto_cleanup_worktree(
                    main_repo, worktree, "feature"
                )

        assert success is False
        assert "パス解決エラー" in msg
