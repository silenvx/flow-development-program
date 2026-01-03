#!/usr/bin/env python3
"""Tests for locked-worktree-guard.py - rm_commands module."""

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


class TestIsRmWorktreeCommand:
    """Tests for is_rm_worktree_command function (Issue #289)."""

    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_detects_rm_rf_worktree(self, mock_get_worktrees):
        """Should detect rm -rf targeting worktree directory."""
        mock_get_worktrees.return_value = [
            Path("/Users/test/project"),
            Path("/Users/test/project/.worktrees/feature-123"),
        ]

        result, path = locked_worktree_guard.is_rm_worktree_command(
            "rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert result
        assert path == Path("/Users/test/project/.worktrees/feature-123")

    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_detects_rm_worktree_with_flags_after(self, mock_get_worktrees):
        """Should detect rm worktree even when flags come after path."""
        mock_get_worktrees.return_value = [
            Path("/Users/test/project"),
            Path("/Users/test/project/.worktrees/fix-bug"),
        ]

        result, path = locked_worktree_guard.is_rm_worktree_command(
            "rm /Users/test/project/.worktrees/fix-bug -rf"
        )
        assert result
        assert path == Path("/Users/test/project/.worktrees/fix-bug")

    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_not_triggered_for_non_worktree(self, mock_get_worktrees):
        """Should not trigger for rm commands targeting non-worktree paths."""
        mock_get_worktrees.return_value = [
            Path("/Users/test/project"),
            Path("/Users/test/project/.worktrees/feature-123"),
        ]

        result, path = locked_worktree_guard.is_rm_worktree_command("rm -rf /tmp/some-directory")
        assert not result
        assert path is None

    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_triggered_when_rm_main_repo_contains_worktrees(self, mock_get_worktrees):
        """Should trigger when rm targets directory containing worktrees.

        When rm -rf targets a directory that contains worktrees,
        it should be detected because it would delete those worktrees too.
        """
        mock_get_worktrees.return_value = [
            Path("/Users/test/project"),
            Path("/Users/test/project/.worktrees/feature-123"),
        ]

        result, path = locked_worktree_guard.is_rm_worktree_command("rm -rf /Users/test/project")
        # Main repo contains worktrees, so it should trigger
        assert result
        assert path == Path("/Users/test/project/.worktrees/feature-123")

    def test_not_triggered_for_non_rm_commands(self):
        """Should not trigger for non-rm commands."""
        result, path = locked_worktree_guard.is_rm_worktree_command("ls -la")
        assert not result
        assert path is None

    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_no_worktrees_returns_false(self, mock_get_worktrees):
        """Should return False when no secondary worktrees exist."""
        mock_get_worktrees.return_value = [Path("/Users/test/project")]

        result, path = locked_worktree_guard.is_rm_worktree_command("rm -rf .worktrees/feature-123")
        assert not result
        assert path is None


class TestCheckRmWorktree:
    """Tests for check_rm_worktree function (Issue #289)."""

    @patch.object(guard_rules, "get_rm_target_worktrees")
    @patch.object(guard_rules, "is_cwd_inside_worktree")
    @patch.object(guard_rules, "get_main_repo_dir")
    def test_blocks_rm_when_cwd_inside_worktree(
        self, mock_main_repo, mock_is_inside, mock_get_targets
    ):
        """Should block rm when CWD is inside target worktree."""
        worktree_path = Path("/Users/test/project/.worktrees/feature-123")
        mock_get_targets.return_value = [(worktree_path, worktree_path)]
        mock_is_inside.return_value = True
        mock_main_repo.return_value = Path("/Users/test/project")

        result = locked_worktree_guard.check_rm_worktree(
            "rm -rf .worktrees/feature-123", hook_cwd="/Users/test/project/.worktrees/feature-123"
        )

        assert result is not None
        assert result["decision"] == "block"
        assert "rm コマンドでworktreeを削除" in result["reason"]
        assert "シェルセッションが破損" in result["reason"]

    @patch.object(guard_rules, "get_rm_target_worktrees")
    @patch.object(guard_rules, "is_cwd_inside_worktree")
    def test_allows_rm_when_cwd_outside_worktree(self, mock_is_inside, mock_get_targets):
        """Should allow rm when CWD is outside target worktree."""
        worktree_path = Path("/Users/test/project/.worktrees/feature-123")
        mock_get_targets.return_value = [(worktree_path, worktree_path)]
        mock_is_inside.return_value = False

        result = locked_worktree_guard.check_rm_worktree(
            "rm -rf .worktrees/feature-123", hook_cwd="/Users/test/project"
        )

        assert result is None

    @patch.object(guard_rules, "get_rm_target_worktrees")
    def test_allows_non_worktree_rm(self, mock_get_targets):
        """Should allow rm commands not targeting worktrees."""
        mock_get_targets.return_value = []

        result = locked_worktree_guard.check_rm_worktree(
            "rm -rf /tmp/some-directory", hook_cwd="/Users/test/project"
        )

        assert result is None

    @patch.object(guard_rules, "get_rm_target_worktrees")
    @patch.object(guard_rules, "is_cwd_inside_worktree")
    @patch.object(guard_rules, "get_main_repo_dir")
    def test_blocks_rm_with_multiple_targets_when_cwd_in_second(
        self, mock_main_repo, mock_is_inside, mock_get_targets
    ):
        """Should block when CWD is inside the second target worktree.

        This tests the fix for multi-target rm commands like:
        rm -rf .worktrees/old .worktrees/current

        If CWD is inside .worktrees/current, it should still be blocked
        even though .worktrees/old is checked first.
        """
        old_worktree = Path("/Users/test/project/.worktrees/old")
        current_worktree = Path("/Users/test/project/.worktrees/current")
        mock_get_targets.return_value = [
            (old_worktree, old_worktree),
            (current_worktree, current_worktree),
        ]
        # CWD is outside 'old' but inside 'current'
        mock_is_inside.side_effect = lambda wt, cwd, cmd=None: wt == current_worktree
        mock_main_repo.return_value = Path("/Users/test/project")

        result = locked_worktree_guard.check_rm_worktree(
            "rm -rf .worktrees/old .worktrees/current",
            hook_cwd="/Users/test/project/.worktrees/current",
        )

        assert result is not None
        assert result["decision"] == "block"
        assert "current" in result["reason"]


class TestGetRmTargetWorktrees:
    """Tests for get_rm_target_worktrees function."""

    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_returns_all_target_worktrees(self, mock_get_worktrees):
        """Should return all worktrees targeted by rm command."""
        mock_get_worktrees.return_value = [
            Path("/Users/test/project"),
            Path("/Users/test/project/.worktrees/old"),
            Path("/Users/test/project/.worktrees/current"),
        ]

        result = locked_worktree_guard.get_rm_target_worktrees(
            "rm -rf /Users/test/project/.worktrees/old /Users/test/project/.worktrees/current"
        )

        assert len(result) == 2
        worktree_paths = [wt for _, wt in result]
        assert Path("/Users/test/project/.worktrees/old") in worktree_paths
        assert Path("/Users/test/project/.worktrees/current") in worktree_paths

    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_does_not_detect_subdirectory_deletion(self, mock_get_worktrees):
        """Should NOT detect deletion of subdirectory within worktree.

        Deleting a subdirectory within a worktree (e.g., .worktrees/feature/subdir)
        is safe and should not be blocked - shell corruption only occurs when
        deleting the directory containing CWD, not arbitrary subdirectories.
        """
        mock_get_worktrees.return_value = [
            Path("/Users/test/project"),
            Path("/Users/test/project/.worktrees/feature-123"),
        ]

        result = locked_worktree_guard.get_rm_target_worktrees(
            "rm -rf /Users/test/project/.worktrees/feature-123/subdir"
        )

        # Should return empty - subdirectory deletion is not worktree deletion
        assert len(result) == 0

    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_detects_chained_rm_commands(self, mock_get_worktrees):
        """Should detect worktrees in chained rm commands.

        Commands like: rm -rf .worktrees/old && rm -rf .worktrees/current
        should detect BOTH worktrees, not just the first one.
        """
        mock_get_worktrees.return_value = [
            Path("/Users/test/project"),
            Path("/Users/test/project/.worktrees/old"),
            Path("/Users/test/project/.worktrees/current"),
        ]

        result = locked_worktree_guard.get_rm_target_worktrees(
            "rm -rf /Users/test/project/.worktrees/old && rm -rf /Users/test/project/.worktrees/current"
        )

        # Should return BOTH worktrees
        assert len(result) == 2
        worktree_paths = [wt for _, wt in result]
        assert Path("/Users/test/project/.worktrees/old") in worktree_paths
        assert Path("/Users/test/project/.worktrees/current") in worktree_paths

    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_detects_semicolon_chained_rm_commands(self, mock_get_worktrees):
        """Should detect worktrees in semicolon-chained rm commands."""
        mock_get_worktrees.return_value = [
            Path("/Users/test/project"),
            Path("/Users/test/project/.worktrees/a"),
            Path("/Users/test/project/.worktrees/b"),
        ]

        result = locked_worktree_guard.get_rm_target_worktrees(
            "rm -rf /Users/test/project/.worktrees/a ; rm -rf /Users/test/project/.worktrees/b"
        )

        assert len(result) == 2

    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_ignores_rm_as_argument_to_other_commands(self, mock_get_worktrees):
        """Should not treat 'rm' as rm command when it's an argument to another command."""
        mock_get_worktrees.return_value = [
            Path("/Users/test/project"),
            Path("/Users/test/project/.worktrees/feature-123"),
        ]

        # 'echo rm' should not be detected as rm command
        result = locked_worktree_guard.get_rm_target_worktrees(
            "echo rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 0

        # 'printf rm' should not be detected as rm command
        result = locked_worktree_guard.get_rm_target_worktrees(
            "printf '%s' rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 0

        # 'cat rm' (file named rm) should not be detected
        result = locked_worktree_guard.get_rm_target_worktrees(
            "cat rm /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 0

        # 'sudo ssh host rm' - rm runs remotely, should not block locally
        result = locked_worktree_guard.get_rm_target_worktrees(
            "sudo ssh host rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 0

        # 'sudo bash -c "rm ..."' - bash is the command, not rm
        result = locked_worktree_guard.get_rm_target_worktrees(
            "sudo bash -c 'rm -rf /Users/test/project/.worktrees/feature-123'"
        )
        assert len(result) == 0

    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_detects_rm_after_pipe_chain(self, mock_get_worktrees):
        """Should detect rm command after pipe as a new command segment."""
        mock_get_worktrees.return_value = [
            Path("/Users/test/project"),
            Path("/Users/test/project/.worktrees/feature-123"),
        ]

        # rm after echo | should be detected (though unusual)
        result = locked_worktree_guard.get_rm_target_worktrees(
            "echo foo | rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 1

    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_detects_sudo_rm_command(self, mock_get_worktrees):
        """Should detect rm command when used with sudo."""
        mock_get_worktrees.return_value = [
            Path("/Users/test/project"),
            Path("/Users/test/project/.worktrees/feature-123"),
        ]

        # sudo rm
        result = locked_worktree_guard.get_rm_target_worktrees(
            "sudo rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 1

        # sudo with flags before rm
        result = locked_worktree_guard.get_rm_target_worktrees(
            "sudo -n rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 1

        # sudo with option that takes argument (e.g., -u root)
        result = locked_worktree_guard.get_rm_target_worktrees(
            "sudo -u root rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 1

        # sudo with multiple options
        result = locked_worktree_guard.get_rm_target_worktrees(
            "sudo -n -u root -g wheel rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 1

    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_detects_full_path_rm_command(self, mock_get_worktrees):
        """Should detect rm command when invoked with full path."""
        mock_get_worktrees.return_value = [
            Path("/Users/test/project"),
            Path("/Users/test/project/.worktrees/feature-123"),
        ]

        # /bin/rm
        result = locked_worktree_guard.get_rm_target_worktrees(
            "/bin/rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 1

        # /usr/bin/rm
        result = locked_worktree_guard.get_rm_target_worktrees(
            "/usr/bin/rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 1

        # sudo /bin/rm
        result = locked_worktree_guard.get_rm_target_worktrees(
            "sudo /bin/rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 1

    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_detects_rm_with_env_var_prefix(self, mock_get_worktrees):
        """Should detect rm command when prefixed with environment variables."""
        mock_get_worktrees.return_value = [
            Path("/Users/test/project"),
            Path("/Users/test/project/.worktrees/feature-123"),
        ]

        # Single env var prefix
        result = locked_worktree_guard.get_rm_target_worktrees(
            "FOO=1 rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 1

        # Multiple env var prefixes
        result = locked_worktree_guard.get_rm_target_worktrees(
            "FOO=1 BAR=2 rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 1

        # Env var prefix with sudo
        result = locked_worktree_guard.get_rm_target_worktrees(
            "SUDO_ASKPASS=/bin/true sudo rm -rf /Users/test/project/.worktrees/feature-123"
        )
        assert len(result) == 1

    @patch.object(worktree_manager, "get_all_worktree_paths")
    @patch("pathlib.Path.expanduser")
    def test_expands_tilde_in_path(self, mock_expanduser, mock_get_worktrees):
        """Should expand ~ to home directory in rm targets."""
        # Mock expanduser to return an absolute path
        mock_expanduser.return_value = Path("/Users/test/project/.worktrees/feature-123")
        mock_get_worktrees.return_value = [
            Path("/Users/test/project"),
            Path("/Users/test/project/.worktrees/feature-123"),
        ]

        result = locked_worktree_guard.get_rm_target_worktrees(
            "rm -rf ~/project/.worktrees/feature-123"
        )
        assert len(result) == 1


class TestExtractRmPaths:
    """Tests for _extract_rm_paths helper function (Issue #800 refactoring)."""

    def test_simple_rm_command(self):
        """Should extract paths from simple rm command."""
        result = locked_worktree_guard._extract_rm_paths("rm -rf /path/to/dir")
        assert result == ["/path/to/dir"]

    def test_rm_with_multiple_paths(self):
        """Should extract multiple paths from rm command."""
        result = locked_worktree_guard._extract_rm_paths("rm -rf /path/a /path/b")
        assert result == ["/path/a", "/path/b"]

    def test_chained_rm_commands(self):
        """Should extract paths from chained rm commands."""
        result = locked_worktree_guard._extract_rm_paths("rm -rf /a && rm -rf /b")
        assert result == ["/a", "/b"]

    def test_rm_with_semicolon(self):
        """Should extract paths from rm commands separated by semicolon."""
        result = locked_worktree_guard._extract_rm_paths("rm /a; rm /b")
        assert result == ["/a", "/b"]

    def test_sudo_rm(self):
        """Should extract paths from sudo rm command."""
        result = locked_worktree_guard._extract_rm_paths("sudo rm -rf /protected")
        assert result == ["/protected"]

    def test_sudo_with_flags(self):
        """Should extract paths from sudo with flags."""
        result = locked_worktree_guard._extract_rm_paths("sudo -u root rm -rf /protected")
        assert result == ["/protected"]

    def test_env_var_prefix(self):
        """Should extract paths with environment variable prefix."""
        result = locked_worktree_guard._extract_rm_paths("FOO=1 rm -rf /path")
        assert result == ["/path"]

    def test_full_path_rm(self):
        """Should handle full path to rm binary."""
        result = locked_worktree_guard._extract_rm_paths("/bin/rm -rf /path")
        assert result == ["/path"]

    def test_non_rm_command_returns_empty(self):
        """Should return empty list for non-rm commands."""
        result = locked_worktree_guard._extract_rm_paths("echo hello")
        assert result == []

    def test_empty_command_returns_empty(self):
        """Should return empty list for empty command."""
        result = locked_worktree_guard._extract_rm_paths("")
        assert result == []

    def test_rm_after_echo_ignored(self):
        """Should not extract paths from rm in echo command."""
        result = locked_worktree_guard._extract_rm_paths("echo rm -rf /path")
        assert result == []


class TestGetOrphanWorktreeDirectories:
    """Tests for get_orphan_worktree_directories function (Issue #795)."""

    @patch.object(worktree_manager, "get_main_repo_dir")
    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_returns_empty_when_no_main_repo(self, mock_get_all, mock_get_main):
        """Should return empty list when main repo cannot be determined."""
        mock_get_main.return_value = None

        result = locked_worktree_guard.get_orphan_worktree_directories()

        assert result == []
        mock_get_all.assert_not_called()

    @patch.object(worktree_manager, "get_main_repo_dir")
    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_returns_empty_when_no_worktrees_dir(self, mock_get_all, mock_get_main):
        """Should return empty list when .worktrees directory doesn't exist."""
        mock_get_main.return_value = Path("/repo")
        # Path("/repo/.worktrees") doesn't exist, so is_dir() returns False

        with patch.object(Path, "is_dir", return_value=False):
            result = locked_worktree_guard.get_orphan_worktree_directories()

        assert result == []

    @patch.object(worktree_manager, "get_main_repo_dir")
    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_detects_orphan_directory(self, mock_get_all, mock_get_main, tmp_path):
        """Should detect directory in .worktrees/ not registered with git."""
        # Setup: create a .worktrees directory with an orphan subdirectory
        main_repo = tmp_path / "repo"
        main_repo.mkdir()
        worktrees_dir = main_repo / ".worktrees"
        worktrees_dir.mkdir()
        orphan_dir = worktrees_dir / "orphan-issue"
        orphan_dir.mkdir()
        registered_dir = worktrees_dir / "registered-issue"
        registered_dir.mkdir()

        mock_get_main.return_value = main_repo
        # Only registered_dir is in git worktree list
        mock_get_all.return_value = [main_repo, registered_dir]

        result = locked_worktree_guard.get_orphan_worktree_directories()

        assert len(result) == 1
        assert orphan_dir.resolve() in result

    @patch.object(worktree_manager, "get_main_repo_dir")
    @patch.object(worktree_manager, "get_all_worktree_paths")
    def test_returns_empty_when_all_registered(self, mock_get_all, mock_get_main, tmp_path):
        """Should return empty list when all directories are registered."""
        main_repo = tmp_path / "repo"
        main_repo.mkdir()
        worktrees_dir = main_repo / ".worktrees"
        worktrees_dir.mkdir()
        registered_dir = worktrees_dir / "issue-123"
        registered_dir.mkdir()

        mock_get_main.return_value = main_repo
        mock_get_all.return_value = [main_repo, registered_dir]

        result = locked_worktree_guard.get_orphan_worktree_directories()

        assert result == []


class TestGetRmTargetOrphanWorktrees:
    """Tests for get_rm_target_orphan_worktrees function (Issue #795)."""

    @patch.object(worktree_manager, "get_orphan_worktree_directories")
    def test_returns_empty_when_no_orphans(self, mock_get_orphans):
        """Should return empty list when no orphan directories exist."""
        mock_get_orphans.return_value = []

        result = locked_worktree_guard.get_rm_target_orphan_worktrees(
            "rm -rf /some/path", hook_cwd="/repo"
        )

        assert result == []

    @patch.object(worktree_manager, "get_orphan_worktree_directories")
    def test_detects_rm_targeting_orphan(self, mock_get_orphans, tmp_path):
        """Should detect rm command targeting an orphan worktree directory."""
        orphan_dir = tmp_path / ".worktrees" / "orphan-issue"
        orphan_dir.mkdir(parents=True)

        mock_get_orphans.return_value = [orphan_dir]

        result = locked_worktree_guard.get_rm_target_orphan_worktrees(
            f"rm -rf {orphan_dir}", hook_cwd=str(tmp_path)
        )

        assert len(result) == 1
        assert result[0][1] == orphan_dir

    @patch.object(worktree_manager, "get_orphan_worktree_directories")
    def test_detects_rm_with_relative_path(self, mock_get_orphans, tmp_path):
        """Should detect rm command with relative path targeting orphan."""
        orphan_dir = tmp_path / ".worktrees" / "orphan-issue"
        orphan_dir.mkdir(parents=True)

        mock_get_orphans.return_value = [orphan_dir]

        result = locked_worktree_guard.get_rm_target_orphan_worktrees(
            "rm -rf .worktrees/orphan-issue", hook_cwd=str(tmp_path)
        )

        assert len(result) == 1
        assert result[0][1] == orphan_dir

    @patch.object(worktree_manager, "get_orphan_worktree_directories")
    def test_ignores_unrelated_rm_command(self, mock_get_orphans, tmp_path):
        """Should ignore rm commands not targeting orphan directories."""
        orphan_dir = tmp_path / ".worktrees" / "orphan-issue"
        orphan_dir.mkdir(parents=True)

        mock_get_orphans.return_value = [orphan_dir]

        result = locked_worktree_guard.get_rm_target_orphan_worktrees(
            "rm -rf /some/other/path", hook_cwd=str(tmp_path)
        )

        assert result == []


class TestCheckRmOrphanWorktree:
    """Tests for check_rm_orphan_worktree function (Issue #795)."""

    @patch.object(guard_rules, "get_rm_target_orphan_worktrees")
    def test_approves_when_no_orphans_targeted(self, mock_get_targets):
        """Should approve when rm doesn't target orphan directories."""
        mock_get_targets.return_value = []

        result = locked_worktree_guard.check_rm_orphan_worktree(
            "rm -rf /some/path", hook_cwd="/repo"
        )

        assert result is None

    @patch.object(guard_rules, "get_rm_target_orphan_worktrees")
    @patch.object(guard_rules, "get_main_repo_dir")
    def test_blocks_when_orphan_targeted(self, mock_get_main, mock_get_targets, tmp_path):
        """Should block when rm targets an orphan worktree directory."""
        orphan_dir = tmp_path / ".worktrees" / "orphan-issue"
        mock_get_targets.return_value = [(orphan_dir, orphan_dir)]
        mock_get_main.return_value = tmp_path

        result = locked_worktree_guard.check_rm_orphan_worktree(
            f"rm -rf {orphan_dir}", hook_cwd=str(tmp_path)
        )

        assert result is not None
        assert result["decision"] == "block"
        assert "孤立worktree" in result["reason"]
        assert "FORCE_RM_ORPHAN=1" in result["reason"]

    @patch.object(guard_rules, "get_rm_target_orphan_worktrees")
    @patch.object(guard_rules, "get_main_repo_dir")
    def test_block_message_includes_repair_instructions(
        self, mock_get_main, mock_get_targets, tmp_path
    ):
        """Block message should include git worktree repair instructions."""
        orphan_dir = tmp_path / ".worktrees" / "orphan-issue"
        mock_get_targets.return_value = [(orphan_dir, orphan_dir)]
        mock_get_main.return_value = tmp_path

        result = locked_worktree_guard.check_rm_orphan_worktree(
            f"rm -rf {orphan_dir}", hook_cwd=str(tmp_path)
        )

        assert result is not None
        assert "git worktree repair" in result["reason"]
        assert "git worktree prune" in result["reason"]


class TestHasForceRmOrphanEnv:
    """Tests for has_force_rm_orphan_env helper function (Issue #2618)."""

    @patch.dict("os.environ", {"FORCE_RM_ORPHAN": "1"})
    def test_returns_true_for_exported_env_var(self):
        """Should return True when FORCE_RM_ORPHAN=1 is exported."""
        assert locked_worktree_guard.has_force_rm_orphan_env("rm -rf .worktrees/foo") is True

    @patch.dict("os.environ", {"FORCE_RM_ORPHAN": "true"})
    def test_returns_true_for_exported_env_var_true(self):
        """Should return True when FORCE_RM_ORPHAN=true is exported."""
        assert locked_worktree_guard.has_force_rm_orphan_env("rm -rf .worktrees/foo") is True

    @patch.dict("os.environ", {}, clear=True)
    def test_returns_true_for_inline_env_var(self):
        """Should return True when FORCE_RM_ORPHAN=1 is inline prefix."""
        assert (
            locked_worktree_guard.has_force_rm_orphan_env("FORCE_RM_ORPHAN=1 rm -rf .worktrees/foo")
            is True
        )

    @patch.dict("os.environ", {}, clear=True)
    def test_returns_true_for_inline_env_var_quoted(self):
        """Should return True when FORCE_RM_ORPHAN='1' is inline with quotes."""
        assert (
            locked_worktree_guard.has_force_rm_orphan_env(
                'FORCE_RM_ORPHAN="1" rm -rf .worktrees/foo'
            )
            is True
        )

    @patch.dict("os.environ", {}, clear=True)
    def test_returns_false_without_env_var(self):
        """Should return False when FORCE_RM_ORPHAN is not set."""
        assert locked_worktree_guard.has_force_rm_orphan_env("rm -rf .worktrees/foo") is False

    @patch.dict("os.environ", {"FORCE_RM_ORPHAN": "0"})
    def test_returns_false_for_zero_value(self):
        """Should return False when FORCE_RM_ORPHAN=0."""
        assert locked_worktree_guard.has_force_rm_orphan_env("rm -rf .worktrees/foo") is False

    @patch.dict("os.environ", {}, clear=True)
    def test_returns_false_for_env_inside_quotes(self):
        """Should return False when FORCE_RM_ORPHAN=1 is inside quoted string."""
        assert (
            locked_worktree_guard.has_force_rm_orphan_env(
                "echo 'FORCE_RM_ORPHAN=1' && rm -rf .worktrees/foo"
            )
            is False
        )


class TestForceRmOrphanBypass:
    """Tests for FORCE_RM_ORPHAN=1 environment variable bypass (Issue #795)."""

    @patch.object(guard_rules, "check_rm_orphan_worktree")
    @patch.object(locked_worktree_guard, "check_rm_worktree", return_value=None)
    @patch.object(locked_worktree_guard, "is_worktree_remove_command", return_value=False)
    @patch.object(locked_worktree_guard, "is_gh_pr_command", return_value=False)
    @patch.dict("os.environ", {"FORCE_RM_ORPHAN": "1"})
    def test_force_rm_orphan_bypasses_check(
        self,
        mock_is_gh_pr,
        mock_is_worktree_remove,
        mock_check_rm,
        mock_check_orphan,
    ):
        """FORCE_RM_ORPHAN=1 should skip orphan worktree check."""
        import io
        import json as json_module

        # Prepare stdin mock
        input_data = {
            "tool_input": {"command": "rm -rf .worktrees/orphan-issue"},
            "cwd": "/repo",
        }

        with patch("sys.stdin", io.StringIO(json_module.dumps(input_data))):
            with patch("sys.exit"):
                with patch("builtins.print"):
                    locked_worktree_guard.main()

        # check_rm_orphan_worktree should NOT be called when FORCE_RM_ORPHAN=1
        mock_check_orphan.assert_not_called()

    @patch.object(locked_worktree_guard_main, "check_rm_orphan_worktree")
    @patch.object(locked_worktree_guard_main, "check_rm_worktree", return_value=None)
    @patch.object(locked_worktree_guard_main, "is_worktree_remove_command", return_value=False)
    @patch.object(locked_worktree_guard_main, "is_gh_pr_command", return_value=False)
    @patch.dict("os.environ", {}, clear=True)
    def test_without_force_rm_orphan_calls_check(
        self,
        mock_is_gh_pr,
        mock_is_worktree_remove,
        mock_check_rm,
        mock_check_orphan,
    ):
        """Without FORCE_RM_ORPHAN, orphan worktree check should be called."""
        import io
        import json as json_module

        mock_check_orphan.return_value = None  # No block

        input_data = {
            "tool_input": {"command": "rm -rf .worktrees/orphan-issue"},
            "cwd": "/repo",
        }

        with patch("sys.stdin", io.StringIO(json_module.dumps(input_data))):
            with patch("sys.exit"):
                with patch("builtins.print"):
                    locked_worktree_guard.main()

        # check_rm_orphan_worktree SHOULD be called when FORCE_RM_ORPHAN is not set
        mock_check_orphan.assert_called_once()

    @patch.object(guard_rules, "check_rm_orphan_worktree")
    @patch.object(locked_worktree_guard, "check_rm_worktree", return_value=None)
    @patch.object(locked_worktree_guard, "is_worktree_remove_command", return_value=False)
    @patch.object(locked_worktree_guard, "is_gh_pr_command", return_value=False)
    @patch.dict("os.environ", {}, clear=True)
    def test_inline_force_rm_orphan_bypasses_check(
        self,
        mock_is_gh_pr,
        mock_is_worktree_remove,
        mock_check_rm,
        mock_check_orphan,
    ):
        """FORCE_RM_ORPHAN=1 in command prefix should skip orphan worktree check (Issue #2618)."""
        import io
        import json as json_module

        # Inline environment variable prefix in command
        input_data = {
            "tool_input": {"command": "FORCE_RM_ORPHAN=1 rm -rf .worktrees/orphan-issue"},
            "cwd": "/repo",
        }

        with patch("sys.stdin", io.StringIO(json_module.dumps(input_data))):
            with patch("sys.exit"):
                with patch("builtins.print"):
                    locked_worktree_guard.main()

        # check_rm_orphan_worktree should NOT be called when FORCE_RM_ORPHAN=1 is in command
        mock_check_orphan.assert_not_called()

    @patch.object(guard_rules, "check_rm_orphan_worktree")
    @patch.object(locked_worktree_guard, "check_rm_worktree", return_value=None)
    @patch.object(locked_worktree_guard, "is_worktree_remove_command", return_value=False)
    @patch.object(locked_worktree_guard, "is_gh_pr_command", return_value=False)
    @patch.dict("os.environ", {}, clear=True)
    def test_inline_force_rm_orphan_quoted_bypasses_check(
        self,
        mock_is_gh_pr,
        mock_is_worktree_remove,
        mock_check_rm,
        mock_check_orphan,
    ):
        """FORCE_RM_ORPHAN="1" with quotes in command should skip check (Issue #2618)."""
        import io
        import json as json_module

        # Quoted inline environment variable
        input_data = {
            "tool_input": {"command": 'FORCE_RM_ORPHAN="1" rm -rf .worktrees/orphan-issue'},
            "cwd": "/repo",
        }

        with patch("sys.stdin", io.StringIO(json_module.dumps(input_data))):
            with patch("sys.exit"):
                with patch("builtins.print"):
                    locked_worktree_guard.main()

        # check_rm_orphan_worktree should NOT be called
        mock_check_orphan.assert_not_called()
