#!/usr/bin/env python3
"""Tests for locked-worktree-guard.py - cd_path module."""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

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


class TestExtractGitBaseDirectory:
    """Tests for extract_git_base_directory function.

    This tests Issue #313: 引用符付きパスのエッジケース対応
    """

    def test_simple_c_flag(self):
        """Should extract base directory from -C flag."""
        result = locked_worktree_guard.extract_git_base_directory(
            "git -C /repo worktree remove foo"
        )
        assert result == "/repo"

    def test_quoted_c_flag_with_spaces(self):
        """Should extract base directory from -C flag with spaces."""
        result = locked_worktree_guard.extract_git_base_directory(
            'git -C "/repo with spaces" worktree remove foo'
        )
        assert result == "/repo with spaces"

    def test_work_tree_flag(self):
        """Should extract base directory from --work-tree flag."""
        result = locked_worktree_guard.extract_git_base_directory(
            "git --work-tree=/repo worktree remove foo"
        )
        assert result == "/repo"

    def test_work_tree_equals_format(self):
        """Should extract base directory from --work-tree=value format."""
        result = locked_worktree_guard.extract_git_base_directory(
            "git --work-tree=/repo/path worktree remove foo"
        )
        assert result == "/repo/path"

    def test_quoted_work_tree_with_spaces(self):
        """Should extract base directory from --work-tree flag with spaces."""
        result = locked_worktree_guard.extract_git_base_directory(
            'git --work-tree="/path with spaces" worktree remove foo'
        )
        assert result == "/path with spaces"

    def test_git_dir_flag_with_git_suffix(self):
        """Should extract parent directory from --git-dir flag ending with .git."""
        result = locked_worktree_guard.extract_git_base_directory(
            "git --git-dir=/repo/.git worktree remove foo"
        )
        assert result == "/repo"

    def test_git_dir_flag_without_git_suffix(self):
        """Should return as-is for --git-dir flag not ending with .git."""
        result = locked_worktree_guard.extract_git_base_directory(
            "git --git-dir=/custom/gitdir worktree remove foo"
        )
        assert result == "/custom/gitdir"

    def test_quoted_git_dir_with_spaces(self):
        """Should extract parent directory from --git-dir flag with spaces."""
        result = locked_worktree_guard.extract_git_base_directory(
            'git --git-dir="/path with spaces/.git" worktree remove foo'
        )
        assert result == "/path with spaces"

    def test_git_dir_with_git_in_middle_not_at_end(self):
        """Should return path as-is when .git appears in middle but not at end.

        This tests Issue #313: edge case where path contains .git but doesn't end with it
        e.g., /path/to/my.git.backup or /path/.github/something
        """
        result = locked_worktree_guard.extract_git_base_directory(
            "git --git-dir=/path/to/repo.git.backup worktree remove foo"
        )
        # Should return the full path since it doesn't end with .git
        assert result == "/path/to/repo.git.backup"

    def test_git_dir_github_folder(self):
        """Should return path as-is when folder contains .git pattern but isn't git dir.

        This tests Issue #313: edge case like .github folder
        """
        result = locked_worktree_guard.extract_git_base_directory(
            "git --git-dir=/path/.github/workflows worktree remove foo"
        )
        # Should return the full path since it doesn't end with .git
        assert result == "/path/.github/workflows"

    def test_no_flags(self):
        """Should return None when no flags are present."""
        result = locked_worktree_guard.extract_git_base_directory("git worktree remove foo")
        assert result is None

    def test_non_git_command(self):
        """Should return None for non-git commands."""
        result = locked_worktree_guard.extract_git_base_directory("ls -la")
        assert result is None


class TestExtractCdTargetBeforeGit:
    """Tests for _extract_cd_target_before_git (Issue #665)."""

    def test_cd_with_and_operator(self):
        """Test cd /path && git worktree remove ... pattern."""
        result = locked_worktree_guard._extract_cd_target_before_git(
            "cd /path/to/repo && git worktree remove .worktrees/issue-123"
        )
        assert result == "/path/to/repo"

    def test_cd_with_semicolon(self):
        """Test cd /path ; git worktree remove ... pattern."""
        result = locked_worktree_guard._extract_cd_target_before_git(
            "cd /path/to/repo ; git worktree remove .worktrees/issue-123"
        )
        assert result == "/path/to/repo"

    def test_cd_with_quoted_path(self):
        """Test cd "/path with spaces" && git worktree remove ... pattern."""
        result = locked_worktree_guard._extract_cd_target_before_git(
            'cd "/path with spaces" && git worktree remove .worktrees/issue-123'
        )
        assert result == "/path with spaces"

    def test_cd_with_flags_skipped(self):
        """Test that cd flags like -P, -L are skipped."""
        result = locked_worktree_guard._extract_cd_target_before_git(
            "cd -P /path/to/repo && git worktree remove .worktrees/issue-123"
        )
        assert result == "/path/to/repo"

    def test_cd_dash_is_valid_target(self):
        """Test that 'cd -' (previous directory) is a valid target."""
        result = locked_worktree_guard._extract_cd_target_before_git(
            "cd - && git worktree remove .worktrees/issue-123"
        )
        assert result == "-"

    def test_cd_before_pipe_is_ignored(self):
        """Test that cd before pipe is ignored (cd runs in subshell)."""
        result = locked_worktree_guard._extract_cd_target_before_git(
            "cd /path | git worktree remove .worktrees/issue-123"
        )
        # cd in pipeline runs in subshell, doesn't affect git
        assert result is None

    def test_cd_after_pipe_is_ignored(self):
        """Test that cd after pipe is ignored (cd runs in subshell)."""
        result = locked_worktree_guard._extract_cd_target_before_git(
            "echo foo | cd /tmp && git worktree remove .worktrees/issue-123"
        )
        # cd in pipeline runs in subshell, git runs in parent shell
        assert result is None

    def test_cd_before_pipeline_affects_git(self):
        """Test that cd before && then pipeline affects git."""
        result = locked_worktree_guard._extract_cd_target_before_git(
            "cd /path && echo foo | git worktree remove .worktrees/issue-123"
        )
        # cd runs in parent shell, pipeline inherits cwd
        assert result == "/path"

    def test_no_cd_returns_none(self):
        """Test that no cd returns None."""
        result = locked_worktree_guard._extract_cd_target_before_git(
            "git worktree remove .worktrees/issue-123"
        )
        assert result is None

    def test_cd_before_non_worktree_remove_returns_none(self):
        """Test that cd before non-worktree-remove returns None."""
        result = locked_worktree_guard._extract_cd_target_before_git(
            "cd /path && git worktree list"
        )
        assert result is None

    def test_cd_with_unlock_and_remove_chain(self):
        """Test cd before unlock && remove chain."""
        result = locked_worktree_guard._extract_cd_target_before_git(
            "cd /path/to/repo && git worktree unlock .worktrees/issue-123 && git worktree remove .worktrees/issue-123"
        )
        assert result == "/path/to/repo"


class TestExtractWorktreePathFromCommandWithCd:
    """Tests for extract_worktree_path_from_command with cd support (Issue #665)."""

    def test_cd_and_relative_path(self):
        """Test cd /path && git worktree remove .relative pattern."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "cd /path/to/repo && git worktree remove .worktrees/issue-123"
        )
        assert path == ".worktrees/issue-123"
        assert base_dir == "/path/to/repo"

    def test_cd_with_quoted_path_and_relative(self):
        """Test cd with quoted path containing spaces."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            'cd "/path with spaces" && git worktree remove .worktrees/issue-123'
        )
        assert path == ".worktrees/issue-123"
        assert base_dir == "/path with spaces"

    def test_c_flag_overrides_cd(self):
        """Test that -C flag takes precedence over cd."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "cd /cd/path && git -C /flag/path worktree remove .worktrees/issue-123"
        )
        assert path == ".worktrees/issue-123"
        assert base_dir == "/flag/path"

    def test_unlock_and_remove_with_cd(self):
        """Test cd before unlock && remove chain."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "cd /path/to/repo && git worktree unlock .worktrees/issue-123 && git worktree remove .worktrees/issue-123"
        )
        assert path == ".worktrees/issue-123"
        assert base_dir == "/path/to/repo"

    def test_no_cd_without_c_flag(self):
        """Test that base_dir is None when neither cd nor -C is present."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "git worktree remove .worktrees/issue-123"
        )
        assert path == ".worktrees/issue-123"
        assert base_dir is None

    def test_cd_relative_parent(self):
        """Test cd .. && git worktree remove ... pattern."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "cd .. && git worktree remove .worktrees/issue-123"
        )
        assert path == ".worktrees/issue-123"
        assert base_dir == ".."


class TestCheckWorktreeRemoveWithRelativeCd:
    """Tests for check_worktree_remove with relative cd paths (Codex review fix)."""

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(guard_rules, "is_cwd_inside_worktree")
    def test_relative_cd_resolved_against_hook_cwd(self, mock_is_cwd_inside, mock_get_locked):
        """Test that relative cd paths are resolved against hook_cwd."""
        mock_get_locked.return_value = []
        mock_is_cwd_inside.return_value = True

        # Simulate: user is in /repo/.worktrees/issue-123
        # Command: cd .. && git worktree remove .worktrees/issue-123
        # Expected: base_dir ".." + hook_cwd "/repo/.worktrees/issue-123"
        #           = /repo/.worktrees + .worktrees/issue-123
        #           which should resolve correctly
        result = locked_worktree_guard.check_worktree_remove(
            "cd .. && git worktree remove .worktrees/issue-123",
            hook_cwd="/repo/.worktrees/issue-123",
        )

        # Should block because is_cwd_inside_worktree returns True
        assert result is not None
        assert result["decision"] == "block"

    @patch("pathlib.Path.cwd")
    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(guard_rules, "is_cwd_inside_worktree")
    def test_relative_cd_without_hook_cwd_uses_cwd_fallback(
        self, mock_is_cwd_inside, mock_get_locked, mock_cwd
    ):
        """Test that relative cd without hook_cwd falls back to Path.cwd()."""
        mock_cwd.return_value = Path("/fallback/cwd")
        mock_get_locked.return_value = []
        mock_is_cwd_inside.return_value = False

        # Without hook_cwd, should use Path.cwd() to resolve relative base_dir
        result = locked_worktree_guard.check_worktree_remove(
            "cd .. && git worktree remove .worktrees/issue-123",
            hook_cwd=None,
        )

        # Should approve (no blocking conditions met with mocks)
        assert result is None
        # Verify get_all_locked_worktree_paths was called with resolved path
        mock_get_locked.assert_called_once()
        call_arg = mock_get_locked.call_args[0][0]
        # base_dir ".." resolved against "/fallback/cwd" = "/fallback"
        assert call_arg == "/fallback"
