#!/usr/bin/env python3
"""Tests for locked-worktree-guard.py - pr_commands module."""

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


class TestExtractPrNumber:
    """Tests for extract_pr_number function."""

    def test_extracts_pr_number_from_merge(self):
        """Should extract PR number from gh pr merge command."""
        result = locked_worktree_guard.extract_pr_number("gh pr merge 123")
        assert result == "123"

    def test_extracts_pr_number_with_flags(self):
        """Should extract PR number when flags are present."""
        result = locked_worktree_guard.extract_pr_number("gh pr merge --squash 123")
        assert result == "123"

    def test_extracts_pr_number_from_checkout(self):
        """Should extract PR number from gh pr checkout command."""
        result = locked_worktree_guard.extract_pr_number("gh pr checkout 456")
        assert result == "456"

    def test_extracts_pr_number_from_close(self):
        """Should extract PR number from gh pr close command."""
        result = locked_worktree_guard.extract_pr_number('gh pr close 789 --comment "reason"')
        assert result == "789"

    def test_returns_none_for_no_number(self):
        """Should return None when no PR number is present."""
        result = locked_worktree_guard.extract_pr_number("gh pr list")
        assert result is None

    def test_returns_none_for_non_gh_command(self):
        """Should return None for non-gh commands."""
        result = locked_worktree_guard.extract_pr_number("git status")
        assert result is None

    def test_extracts_pr_number_with_repo_flag(self):
        """Should extract PR number when --repo flag is present."""
        result = locked_worktree_guard.extract_pr_number("gh --repo owner/repo pr merge 123")
        assert result == "123"

    def test_extracts_pr_number_with_short_repo_flag(self):
        """Should extract PR number when -R flag is present."""
        result = locked_worktree_guard.extract_pr_number("gh -R owner/repo pr close 456")
        assert result == "456"


class TestIsGhPrCommand:
    """Tests for is_gh_pr_command function."""

    def test_simple_gh_pr_command(self):
        """gh pr merge should be detected."""
        result = locked_worktree_guard.is_gh_pr_command("gh pr merge 123")
        assert result

    def test_gh_pr_with_repo_flag(self):
        """gh --repo owner/repo pr merge should be detected."""
        result = locked_worktree_guard.is_gh_pr_command("gh --repo owner/repo pr merge 123")
        assert result

    def test_gh_pr_with_short_repo_flag(self):
        """gh -R owner/repo pr merge should be detected."""
        result = locked_worktree_guard.is_gh_pr_command("gh -R owner/repo pr merge 123")
        assert result

    def test_non_gh_command(self):
        """git status should not be detected as gh pr command."""
        result = locked_worktree_guard.is_gh_pr_command("git status")
        assert not result

    def test_gh_non_pr_command(self):
        """gh issue list should not be detected as gh pr command."""
        result = locked_worktree_guard.is_gh_pr_command("gh issue list")
        assert not result

    # Issue #318: Tests for avoiding false positives from quoted strings
    def test_gh_issue_create_with_pr_in_body_not_detected(self):
        """gh issue create with PR mention in body should NOT be detected as gh pr command.

        This tests Issue #318: heredoc/引数内のテキストを誤検出する問題
        """
        command = 'gh issue create --title "Bug" --body "See gh pr merge 307 for context"'
        result = locked_worktree_guard.is_gh_pr_command(command)
        assert not result

    def test_echo_with_gh_pr_not_detected(self):
        """echo with gh pr text should NOT be detected as gh pr command.

        This tests Issue #318: heredoc/引数内のテキストを誤検出する問題
        """
        command = 'echo "gh pr merge 123"'
        result = locked_worktree_guard.is_gh_pr_command(command)
        assert not result

    def test_heredoc_with_gh_pr_not_detected(self):
        """Commands with heredoc containing gh pr should NOT be detected.

        This tests Issue #318: heredoc/引数内のテキストを誤検出する問題
        """
        # Note: shlex.split handles heredoc content as quoted string
        command = 'gh issue create --body "Previous work: gh pr review 307 --approve"'
        result = locked_worktree_guard.is_gh_pr_command(command)
        assert not result


class TestParseGhPrCommand:
    """Tests for parse_gh_pr_command function."""

    def test_simple_merge_command(self):
        """Should parse gh pr merge command."""
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command("gh pr merge 123")
        assert subcommand == "merge"
        assert pr_number == "123"

    def test_command_with_flags(self):
        """Should parse command with flags."""
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command(
            "gh pr merge --squash 123"
        )
        assert subcommand == "merge"
        assert pr_number == "123"

    def test_command_with_repo_flag(self):
        """Should parse command with --repo flag."""
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command(
            "gh --repo owner/repo pr merge 456"
        )
        assert subcommand == "merge"
        assert pr_number == "456"

    def test_non_gh_command_returns_none(self):
        """Should return None for non-gh commands."""
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command("git status")
        assert subcommand is None
        assert pr_number is None

    def test_gh_issue_command_returns_none(self):
        """Should return None for gh issue commands."""
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command("gh issue list")
        assert subcommand is None
        assert pr_number is None

    def test_quoted_body_with_pr_command_not_detected(self):
        """Should NOT detect gh pr inside quoted body.

        This tests Issue #318: heredoc/引数内のテキストを誤検出する問題
        """
        command = 'gh issue create --body "Related: gh pr merge 307"'
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command(command)
        # gh issue create is not a gh pr command
        assert subcommand is None
        assert pr_number is None

    def test_echo_with_pr_command_not_detected(self):
        """Should NOT detect gh pr inside echo.

        This tests Issue #318: heredoc/引数内のテキストを誤検出する問題
        """
        command = 'echo "gh pr merge 123"'
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command(command)
        assert subcommand is None
        assert pr_number is None

    def test_pr_view_command(self):
        """Should parse gh pr view command."""
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command("gh pr view 123")
        assert subcommand == "view"
        assert pr_number == "123"

    def test_pr_list_command(self):
        """Should parse gh pr list command (no PR number)."""
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command("gh pr list")
        assert subcommand == "list"
        assert pr_number is None

    # Review feedback: Test for --help flag (doesn't take arguments)
    def test_help_flag_does_not_consume_next_token(self):
        """--help flag should not consume the next token as its argument.

        Review feedback: Copilot pointed out that --help and -h don't take arguments.
        """
        # gh --help pr merge 123 should still detect 'pr merge 123'
        # Note: In practice, --help would show help and exit, but the parser
        # should still correctly identify 'pr' as the command
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command("gh --help pr merge 123")
        assert subcommand == "merge"
        assert pr_number == "123"

    # Review feedback: Test for --hostname flag (takes argument)
    def test_hostname_flag_with_argument(self):
        """--hostname flag should consume its argument.

        Review feedback: Codex pointed out that --hostname takes an argument.
        """
        command = "gh --hostname enterprise.github.com pr merge 123"
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command(command)
        assert subcommand == "merge"
        assert pr_number == "123"

    # Review feedback: Test PR number extraction with flag values
    def test_pr_number_extraction_with_title_flag(self):
        """PR number should be extracted correctly even with --title flag.

        Review feedback: Copilot pointed out that --title 123 456 should extract 456.
        """
        # gh pr edit --title "something" 456
        command = 'gh pr edit --title "New Title" 456'
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command(command)
        assert subcommand == "edit"
        assert pr_number == "456"

    def test_pr_number_extraction_skips_numeric_flag_values(self):
        """PR number extraction should skip numeric values of flags.

        Review feedback: Copilot pointed out that gh pr edit --title 123 456
        should extract 456, not 123.
        """
        # Note: --title normally takes a string, but if it had a numeric value
        command = "gh pr edit --limit 10 456"
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command(command)
        assert subcommand == "edit"
        assert pr_number == "456"

    # Review feedback: Test for equals sign format flags
    def test_hostname_flag_with_equals_format(self):
        """--hostname=value format should work correctly.

        Review feedback: Copilot suggested testing equals sign format.
        shlex.split() keeps --hostname=value as single token, which
        doesn't start with 'pr', so we skip to find 'pr'.
        """
        command = "gh --hostname=enterprise.github.com pr merge 123"
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command(command)
        assert subcommand == "merge"
        assert pr_number == "123"

    def test_repo_flag_with_equals_format(self):
        """--repo=value format should work correctly.

        Review feedback: Copilot suggested testing equals sign format.
        """
        command = "gh --repo=owner/repo pr merge 456"
        subcommand, pr_number = locked_worktree_guard.parse_gh_pr_command(command)
        assert subcommand == "merge"
        assert pr_number == "456"


class TestGetPrForBranch:
    """Tests for get_pr_for_branch function."""

    @patch("subprocess.run")
    def test_returns_pr_number(self, mock_run):
        """Should return PR number for branch with open PR."""
        mock_run.return_value = MagicMock(returncode=0, stdout="123\n")

        result = locked_worktree_guard.get_pr_for_branch("feature-branch")

        assert result == "123"

    @patch("subprocess.run")
    def test_returns_none_for_no_pr(self, mock_run):
        """Should return None when branch has no open PR."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        result = locked_worktree_guard.get_pr_for_branch("branch-without-pr")

        assert result is None

    @patch("subprocess.run")
    def test_returns_none_on_gh_failure(self, mock_run):
        """Should return None when gh command fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = locked_worktree_guard.get_pr_for_branch("any-branch")

        assert result is None


class TestGetBranchForPr:
    """Tests for get_branch_for_pr function (Issue #528)."""

    @patch("subprocess.run")
    def test_gets_branch_name_for_pr(self, mock_run):
        """Should get branch name for a PR number."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="feat/issue-123\n",
        )

        result = locked_worktree_guard.get_branch_for_pr("123")
        assert result == "feat/issue-123"

    @patch("subprocess.run")
    def test_returns_none_for_nonexistent_pr(self, mock_run):
        """Should return None when PR not found."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = locked_worktree_guard.get_branch_for_pr("99999")
        assert result is None
