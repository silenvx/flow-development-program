#!/usr/bin/env python3
"""Tests for locked-worktree-guard.py - command_detection module."""

import importlib.util
import sys
import types
from pathlib import Path

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


class TestIsModifyingCommand:
    """Tests for is_modifying_command function."""

    def test_merge_is_modifying(self):
        """gh pr merge should be detected as modifying."""
        result = locked_worktree_guard.is_modifying_command("gh pr merge 123")
        assert result

    def test_checkout_is_modifying(self):
        """gh pr checkout should be detected as modifying."""
        result = locked_worktree_guard.is_modifying_command("gh pr checkout 123")
        assert result

    def test_close_is_modifying(self):
        """gh pr close should be detected as modifying."""
        result = locked_worktree_guard.is_modifying_command("gh pr close 123")
        assert result

    def test_comment_is_modifying(self):
        """gh pr comment should be detected as modifying."""
        result = locked_worktree_guard.is_modifying_command("gh pr comment 123 --body 'test'")
        assert result

    def test_merge_with_repo_flag_is_modifying(self):
        """gh --repo owner/repo pr merge should be detected as modifying."""
        result = locked_worktree_guard.is_modifying_command("gh --repo owner/repo pr merge 123")
        assert result

    def test_merge_with_short_repo_flag_is_modifying(self):
        """gh -R owner/repo pr merge should be detected as modifying."""
        result = locked_worktree_guard.is_modifying_command("gh -R owner/repo pr merge 123")
        assert result

    def test_view_is_not_modifying(self):
        """gh pr view should not be detected as modifying."""
        result = locked_worktree_guard.is_modifying_command("gh pr view 123")
        assert not result

    def test_list_is_not_modifying(self):
        """gh pr list should not be detected as modifying."""
        result = locked_worktree_guard.is_modifying_command("gh pr list")
        assert not result

    def test_checks_is_not_modifying(self):
        """gh pr checks should not be detected as modifying."""
        result = locked_worktree_guard.is_modifying_command("gh pr checks 123")
        assert not result

    def test_diff_is_not_modifying(self):
        """gh pr diff should not be detected as modifying."""
        result = locked_worktree_guard.is_modifying_command("gh pr diff 123")
        assert not result


class TestIsCiMonitorCommand:
    """Tests for is_ci_monitor_command function (Issue #608)."""

    def test_basic_ci_monitor_command(self):
        """Should detect basic ci-monitor.py invocation."""
        is_match, pr_numbers = locked_worktree_guard.is_ci_monitor_command(
            "python3 .claude/scripts/ci-monitor.py 602"
        )
        assert is_match
        assert pr_numbers == ["602"]

    def test_multiple_pr_numbers(self):
        """Should detect multi-PR mode ci-monitor.py invocation."""
        is_match, pr_numbers = locked_worktree_guard.is_ci_monitor_command(
            "python3 .claude/scripts/ci-monitor.py 101 202 303"
        )
        assert is_match
        assert pr_numbers == ["101", "202", "303"]

    def test_with_flags_before_pr(self):
        """Should skip flags and detect PR number."""
        is_match, pr_numbers = locked_worktree_guard.is_ci_monitor_command(
            "python3 .claude/scripts/ci-monitor.py --verbose 602"
        )
        assert is_match
        assert pr_numbers == ["602"]

    def test_full_path(self):
        """Should detect full path invocation."""
        is_match, pr_numbers = locked_worktree_guard.is_ci_monitor_command(
            "/Users/user/repo/.claude/scripts/ci-monitor.py 789"
        )
        assert is_match
        assert pr_numbers == ["789"]

    def test_direct_execution(self):
        """Should detect direct execution."""
        is_match, pr_numbers = locked_worktree_guard.is_ci_monitor_command("./ci-monitor.py 602")
        assert is_match
        assert pr_numbers == ["602"]

    def test_no_pr_number(self):
        """Should return empty list when no PR number."""
        is_match, pr_numbers = locked_worktree_guard.is_ci_monitor_command(
            "python3 .claude/scripts/ci-monitor.py"
        )
        assert is_match
        assert pr_numbers == []

    def test_non_ci_monitor_command(self):
        """Should not detect non-ci-monitor commands."""
        is_match, pr_numbers = locked_worktree_guard.is_ci_monitor_command("gh pr view 602")
        assert not is_match
        assert pr_numbers == []

    def test_other_python_script(self):
        """Should not detect other Python scripts."""
        is_match, pr_numbers = locked_worktree_guard.is_ci_monitor_command(
            "python3 other-script.py 602"
        )
        assert not is_match
        assert pr_numbers == []

    def test_empty_command(self):
        """Should handle empty command."""
        is_match, pr_numbers = locked_worktree_guard.is_ci_monitor_command("")
        assert not is_match
        assert pr_numbers == []


class TestHasDeleteBranchFlag:
    """Tests for has_delete_branch_flag function (Issue #649)."""

    def test_detects_delete_branch_flag(self):
        """Should detect --delete-branch flag."""
        result = locked_worktree_guard.has_delete_branch_flag("gh pr merge 123 --delete-branch")
        assert result

    def test_detects_short_d_flag(self):
        """Should detect -d flag."""
        result = locked_worktree_guard.has_delete_branch_flag("gh pr merge 123 -d")
        assert result

    def test_detects_flag_before_pr_number(self):
        """Should detect --delete-branch before PR number."""
        result = locked_worktree_guard.has_delete_branch_flag("gh pr merge --delete-branch 123")
        assert result

    def test_detects_flag_with_squash(self):
        """Should detect --delete-branch with other flags."""
        result = locked_worktree_guard.has_delete_branch_flag(
            "gh pr merge 123 --squash --delete-branch"
        )
        assert result

    def test_no_delete_branch_flag(self):
        """Should return False when no --delete-branch flag."""
        result = locked_worktree_guard.has_delete_branch_flag("gh pr merge 123 --squash")
        assert not result

    def test_no_flags(self):
        """Should return False when no flags present."""
        result = locked_worktree_guard.has_delete_branch_flag("gh pr merge 123")
        assert not result

    def test_non_gh_command(self):
        """Should return False for non-gh commands."""
        result = locked_worktree_guard.has_delete_branch_flag("git branch -d feature")
        assert not result

    def test_stops_at_shell_operators(self):
        """Should not check past shell operators."""
        # -d after && should not be detected
        result = locked_worktree_guard.has_delete_branch_flag(
            "gh pr merge 123 --squash && git branch -d feature"
        )
        assert not result

    def test_detects_flag_glued_to_operator(self):
        """Should detect --delete-branch even when glued to shell operators (no spaces)."""
        # Edge case: operator directly attached to flag
        result = locked_worktree_guard.has_delete_branch_flag(
            "gh pr merge --delete-branch&&echo ok"
        )
        assert result

    def test_detects_flag_glued_to_semicolon(self):
        """Should detect --delete-branch when glued to semicolon."""
        result = locked_worktree_guard.has_delete_branch_flag(
            "gh pr merge 123 --delete-branch;echo ok"
        )
        assert result

    def test_detects_flag_glued_to_pipe(self):
        """Should detect -d when glued to pipe."""
        result = locked_worktree_guard.has_delete_branch_flag("gh pr merge 123 -d|cat")
        assert result


class TestGetMergePositionalArg:
    """Tests for _get_merge_positional_arg function (Issue #649)."""

    def test_returns_pr_number(self):
        """Should return PR number when present."""
        result = locked_worktree_guard._get_merge_positional_arg("gh pr merge 123 --delete-branch")
        assert result == "123"

    def test_returns_branch_name(self):
        """Should return branch name when present."""
        result = locked_worktree_guard._get_merge_positional_arg(
            "gh pr merge feature-branch --delete-branch"
        )
        assert result == "feature-branch"

    def test_returns_none_without_positional_arg(self):
        """Should return None when no positional arg."""
        result = locked_worktree_guard._get_merge_positional_arg("gh pr merge --delete-branch")
        assert result is None

    def test_returns_url(self):
        """Should return URL when used as selector."""
        result = locked_worktree_guard._get_merge_positional_arg(
            "gh pr merge https://github.com/owner/repo/pull/123 --delete-branch"
        )
        assert result == "https://github.com/owner/repo/pull/123"

    def test_returns_positional_after_flags(self):
        """Should return positional arg even after flags."""
        result = locked_worktree_guard._get_merge_positional_arg(
            "gh pr merge --squash 123 --delete-branch"
        )
        assert result == "123"

    def test_skips_flag_values(self):
        """Should not return flag values as positional arg."""
        result = locked_worktree_guard._get_merge_positional_arg(
            "gh pr merge --body 'message' --delete-branch"
        )
        assert result is None


class TestHasMergePositionalArg:
    """Tests for _has_merge_positional_arg function (Issue #649)."""

    def test_with_pr_number(self):
        """Should return True when PR number is present."""
        result = locked_worktree_guard._has_merge_positional_arg("gh pr merge 123 --delete-branch")
        assert result

    def test_with_branch_name(self):
        """Should return True when branch name is present."""
        result = locked_worktree_guard._has_merge_positional_arg(
            "gh pr merge feature-branch --delete-branch"
        )
        assert result

    def test_without_positional_arg(self):
        """Should return False when no positional arg."""
        result = locked_worktree_guard._has_merge_positional_arg("gh pr merge --delete-branch")
        assert not result

    def test_with_only_flags(self):
        """Should return False when only flags present."""
        result = locked_worktree_guard._has_merge_positional_arg(
            "gh pr merge --squash --delete-branch"
        )
        assert not result

    def test_with_body_flag(self):
        """Should skip --body flag and its value."""
        result = locked_worktree_guard._has_merge_positional_arg(
            "gh pr merge --body 'message' --delete-branch"
        )
        assert not result

    def test_with_subject_flag(self):
        """Should skip --subject flag and its value."""
        result = locked_worktree_guard._has_merge_positional_arg(
            "gh pr merge --subject 'commit message' --delete-branch"
        )
        assert not result

    def test_with_short_t_flag(self):
        """Should skip -t flag and its value."""
        result = locked_worktree_guard._has_merge_positional_arg(
            "gh pr merge -t 'msg' --delete-branch"
        )
        assert not result

    def test_with_author_email_flag(self):
        """Should skip --author-email flag and its value."""
        result = locked_worktree_guard._has_merge_positional_arg(
            "gh pr merge --author-email 'user@example.com' --delete-branch"
        )
        assert not result

    def test_with_repo_flag(self):
        """Should skip --repo flag and its value."""
        result = locked_worktree_guard._has_merge_positional_arg(
            "gh pr merge --repo owner/repo --delete-branch"
        )
        assert not result

    def test_pr_number_after_flags(self):
        """Should detect PR number even after flags."""
        result = locked_worktree_guard._has_merge_positional_arg(
            "gh pr merge --squash 123 --delete-branch"
        )
        assert result

    def test_glued_operators_without_positional(self):
        """Should return False when operators are glued to flags (Issue #649 P1 fix)."""
        # This test ensures that glued operators like "&&" don't cause false positives
        result = locked_worktree_guard._has_merge_positional_arg(
            "gh pr merge --delete-branch&&echo ok"
        )
        assert not result

    def test_glued_semicolon_without_positional(self):
        """Should return False when semicolon is glued to flags."""
        result = locked_worktree_guard._has_merge_positional_arg(
            "gh pr merge --delete-branch;echo ok"
        )
        assert not result

    def test_glued_pipe_without_positional(self):
        """Should return False when pipe is glued to flags."""
        result = locked_worktree_guard._has_merge_positional_arg("gh pr merge --delete-branch|cat")
        assert not result
