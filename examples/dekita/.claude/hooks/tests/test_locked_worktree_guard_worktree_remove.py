#!/usr/bin/env python3
"""Tests for locked-worktree-guard.py - worktree_remove module."""

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


class TestIsWorktreeRemoveCommand:
    """Tests for is_worktree_remove_command function."""

    def test_simple_remove(self):
        """git worktree remove should be detected."""
        result = locked_worktree_guard.is_worktree_remove_command(
            "git worktree remove .worktrees/foo"
        )
        assert result

    def test_remove_with_force(self):
        """git worktree remove --force should be detected."""
        result = locked_worktree_guard.is_worktree_remove_command(
            "git worktree remove --force .worktrees/foo"
        )
        assert result

    def test_remove_with_short_force(self):
        """git worktree remove -f should be detected."""
        result = locked_worktree_guard.is_worktree_remove_command(
            "git worktree remove -f .worktrees/foo"
        )
        assert result

    def test_worktree_list_not_detected(self):
        """git worktree list should not be detected as remove."""
        result = locked_worktree_guard.is_worktree_remove_command("git worktree list")
        assert not result

    def test_worktree_add_not_detected(self):
        """git worktree add should not be detected as remove."""
        result = locked_worktree_guard.is_worktree_remove_command(
            "git worktree add .worktrees/foo -b feature"
        )
        assert not result

    def test_non_git_command(self):
        """Non-git commands should not be detected."""
        result = locked_worktree_guard.is_worktree_remove_command("ls -la")
        assert not result

    def test_remove_with_c_flag(self):
        """git -C /path worktree remove should be detected."""
        result = locked_worktree_guard.is_worktree_remove_command(
            "git -C /repo/path worktree remove .worktrees/foo"
        )
        assert result

    def test_remove_with_work_tree_flag(self):
        """git --work-tree=/path worktree remove should be detected."""
        result = locked_worktree_guard.is_worktree_remove_command(
            "git --work-tree=/repo worktree remove .worktrees/foo"
        )
        assert result

    # Issue #313: Tests for quoted paths with spaces
    def test_remove_with_quoted_c_flag_path(self):
        """git -C "/path with spaces" worktree remove should be detected.

        This tests Issue #313: 引用符付きパスのエッジケース対応
        """
        result = locked_worktree_guard.is_worktree_remove_command(
            'git -C "/repo with spaces" worktree remove .worktrees/foo'
        )
        assert result

    def test_remove_with_quoted_work_tree_flag(self):
        """git --work-tree="/path with spaces" worktree remove should be detected.

        This tests Issue #313: 引用符付きパスのエッジケース対応
        """
        result = locked_worktree_guard.is_worktree_remove_command(
            'git --work-tree="/path with spaces" worktree remove .worktrees/foo'
        )
        assert result

    def test_remove_with_work_tree_equals_format(self):
        """git --work-tree=/path worktree remove (equals format) should be detected.

        This tests Issue #313: --work-tree=value format
        """
        result = locked_worktree_guard.is_worktree_remove_command(
            "git --work-tree=/repo/path worktree remove .worktrees/foo"
        )
        assert result

    def test_remove_with_git_config_flag(self):
        """git -c option worktree remove should be detected.

        This tests Issue #313: -c flag handling
        """
        result = locked_worktree_guard.is_worktree_remove_command(
            "git -c core.autocrlf=false worktree remove .worktrees/foo"
        )
        assert result

    # Issue #313: Tests for shell operators
    def test_remove_with_shell_and_operator(self):
        """git worktree remove with && operator should be detected.

        This tests Issue #313: シェル演算子を含むコマンドのテスト
        """
        result = locked_worktree_guard.is_worktree_remove_command(
            "git worktree remove .worktrees/foo && echo done"
        )
        assert result

    def test_remove_with_shell_semicolon(self):
        """git worktree remove with ; operator should be detected.

        This tests Issue #313: シェル演算子を含むコマンドのテスト
        """
        result = locked_worktree_guard.is_worktree_remove_command(
            "git worktree remove .worktrees/foo ; echo done"
        )
        assert result

    def test_remove_with_pipe_operator(self):
        """git worktree remove with | operator should be detected.

        This tests Issue #313: シェル演算子を含むコマンドのテスト
        """
        result = locked_worktree_guard.is_worktree_remove_command(
            "git worktree remove .worktrees/foo | cat"
        )
        assert result


class TestExtractWorktreePathFromCommand:
    """Tests for extract_worktree_path_from_command function."""

    def test_simple_path(self):
        """Should extract path from simple command."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "git worktree remove .worktrees/foo"
        )
        assert path == ".worktrees/foo"
        assert base_dir is None

    def test_path_with_force_before(self):
        """Should extract path when --force is before path."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "git worktree remove --force .worktrees/foo"
        )
        assert path == ".worktrees/foo"
        assert base_dir is None

    def test_path_with_short_force_before(self):
        """Should extract path when -f is before path."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "git worktree remove -f .worktrees/foo"
        )
        assert path == ".worktrees/foo"
        assert base_dir is None

    def test_absolute_path(self):
        """Should extract absolute path."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "git worktree remove /absolute/path/to/worktree"
        )
        assert path == "/absolute/path/to/worktree"
        assert base_dir is None

    def test_returns_none_for_no_path(self):
        """Should return None when no path is present."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "git worktree remove"
        )
        assert path is None
        assert base_dir is None

    def test_returns_none_for_only_flags(self):
        """Should return None when only flags are present."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "git worktree remove --force"
        )
        assert path is None
        assert base_dir is None

    def test_quoted_path_with_spaces(self):
        """Should extract path with spaces when quoted."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            'git worktree remove "/tmp/wt with space"'
        )
        assert path == "/tmp/wt with space"
        assert base_dir is None

    def test_quoted_path_with_force(self):
        """Should extract quoted path when --force is present."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            'git worktree remove --force "/path/with spaces/worktree"'
        )
        assert path == "/path/with spaces/worktree"
        assert base_dir is None

    def test_path_with_c_flag(self):
        """Should extract path and base_dir when -C flag is present."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "git -C /repo/path worktree remove .worktrees/foo"
        )
        assert path == ".worktrees/foo"
        assert base_dir == "/repo/path"

    def test_path_with_work_tree_flag(self):
        """Should extract path and base_dir when --work-tree flag is present."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "git --work-tree=/repo worktree remove .worktrees/bar"
        )
        assert path == ".worktrees/bar"
        assert base_dir == "/repo"

    def test_path_with_git_dir_flag(self):
        """Should extract path and base_dir when --git-dir flag is present."""
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "git --git-dir=/repo/.git worktree remove .worktrees/baz"
        )
        assert path == ".worktrees/baz"
        assert base_dir == "/repo"

    # Issue #313: Tests for quoted paths with spaces in global flags
    def test_quoted_c_flag_path_with_spaces(self):
        """Should handle -C flag with quoted path containing spaces.

        This tests Issue #313: 引用符付きパスのエッジケース対応
        """
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            'git -C "/repo with spaces/path" worktree remove .worktrees/foo'
        )
        assert path == ".worktrees/foo"
        assert base_dir == "/repo with spaces/path"

    def test_quoted_work_tree_flag_with_spaces(self):
        """Should handle --work-tree flag with quoted path containing spaces.

        This tests Issue #313: 引用符付きパスのエッジケース対応
        """
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            'git --work-tree="/path with spaces" worktree remove .worktrees/bar'
        )
        assert path == ".worktrees/bar"
        assert base_dir == "/path with spaces"

    def test_quoted_git_dir_flag_with_spaces(self):
        """Should handle --git-dir flag with quoted path containing spaces.

        This tests Issue #313: 引用符付きパスのエッジケース対応
        """
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            'git --git-dir="/path with spaces/.git" worktree remove .worktrees/baz'
        )
        assert path == ".worktrees/baz"
        assert base_dir == "/path with spaces"

    def test_work_tree_entire_flag_quoted_with_spaces(self):
        """Should handle entire flag+value quoted as single shell token.

        This tests Issue #313: 引用符付きパスのエッジケース対応

        This is an unusual shell syntax where the entire "--work-tree=/path with spaces"
        is quoted as a single token, rather than the more common --work-tree="/path with spaces".
        shlex.split() treats the quoted portion as a single token containing the equals sign.
        """
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            'git "--work-tree=/path with spaces" worktree remove .worktrees/foo'
        )
        assert path == ".worktrees/foo"
        assert base_dir == "/path with spaces"

    # Issue #313: Tests for shell operators in path extraction
    def test_path_extraction_with_shell_and_operator(self):
        """Should extract path correctly when && operator is present.

        This tests Issue #313: シェル演算子を含むコマンドのテスト
        """
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "git worktree remove .worktrees/foo && echo done"
        )
        assert path == ".worktrees/foo"
        assert base_dir is None

    def test_path_extraction_with_shell_semicolon(self):
        """Should extract path correctly when ; operator is present.

        This tests Issue #313: シェル演算子を含むコマンドのテスト
        """
        path, base_dir = locked_worktree_guard.extract_worktree_path_from_command(
            "git worktree remove .worktrees/foo ; echo done"
        )
        assert path == ".worktrees/foo"
        assert base_dir is None


class TestIsCwdInsideWorktree:
    """Tests for is_cwd_inside_worktree function."""

    def test_cwd_exactly_matches_worktree(self):
        """Should return True when cwd exactly matches worktree path."""
        worktree = Path("/Users/test/project/.worktrees/foo")
        cwd = Path("/Users/test/project/.worktrees/foo")
        result = locked_worktree_guard.is_cwd_inside_worktree(worktree, cwd)
        assert result

    def test_cwd_is_subdirectory_of_worktree(self):
        """Should return True when cwd is a subdirectory of worktree."""
        worktree = Path("/Users/test/project/.worktrees/foo")
        cwd = Path("/Users/test/project/.worktrees/foo/src/components")
        result = locked_worktree_guard.is_cwd_inside_worktree(worktree, cwd)
        assert result

    def test_cwd_is_outside_worktree(self):
        """Should return False when cwd is outside worktree."""
        worktree = Path("/Users/test/project/.worktrees/foo")
        cwd = Path("/Users/test/project")
        result = locked_worktree_guard.is_cwd_inside_worktree(worktree, cwd)
        assert not result

    def test_cwd_is_sibling_worktree(self):
        """Should return False when cwd is in a sibling worktree."""
        worktree = Path("/Users/test/project/.worktrees/foo")
        cwd = Path("/Users/test/project/.worktrees/bar")
        result = locked_worktree_guard.is_cwd_inside_worktree(worktree, cwd)
        assert not result

    def test_returns_true_on_oserror(self):
        """Should return True (fail-close) when Path.resolve() raises OSError.

        Security fix: When we can't determine if CWD is inside worktree,
        we assume it IS inside to prevent accidental deletion.
        """
        worktree = Path("/Users/test/project/.worktrees/foo")
        cwd = Path("/Users/test/project/.worktrees/foo")

        def mock_resolve(self, *args, **kwargs):
            # Simulate broken symlink or permission issue
            raise OSError("Permission denied or broken symlink")

        with patch.object(Path, "resolve", mock_resolve):
            result = locked_worktree_guard.is_cwd_inside_worktree(worktree, cwd)

        # Should return True (fail-close) on error to prevent accidental deletion
        assert result


class TestCheckWorktreeRemove:
    """Tests for check_worktree_remove function."""

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(guard_rules, "is_cwd_inside_worktree")
    def test_blocks_locked_worktree_removal(self, mock_is_cwd_inside, mock_get_locked):
        """Should block removal of locked worktree."""
        mock_is_cwd_inside.return_value = False
        mock_get_locked.return_value = [Path("/Users/test/project/.worktrees/foo")]

        with patch.object(Path, "cwd", return_value=Path("/Users/test/project")):
            result = locked_worktree_guard.check_worktree_remove(
                "git worktree remove .worktrees/foo"
            )

        assert result is not None
        assert result["decision"] == "block"
        assert "ロックされた" in result["reason"]

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(guard_rules, "is_cwd_inside_worktree")
    def test_allows_unlocked_worktree_removal(self, mock_is_cwd_inside, mock_get_locked):
        """Should allow removal of unlocked worktree."""
        mock_is_cwd_inside.return_value = False
        mock_get_locked.return_value = [Path("/Users/test/project/.worktrees/other")]

        with patch.object(Path, "cwd", return_value=Path("/Users/test/project")):
            result = locked_worktree_guard.check_worktree_remove(
                "git worktree remove .worktrees/foo"
            )

        assert result is None

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(guard_rules, "is_cwd_inside_worktree")
    def test_blocks_force_removal_of_locked(self, mock_is_cwd_inside, mock_get_locked):
        """Should block force removal of locked worktree."""
        mock_is_cwd_inside.return_value = False
        mock_get_locked.return_value = [Path("/Users/test/project/.worktrees/foo")]

        with patch.object(Path, "cwd", return_value=Path("/Users/test/project")):
            result = locked_worktree_guard.check_worktree_remove(
                "git worktree remove --force .worktrees/foo"
            )

        assert result is not None
        assert result["decision"] == "block"

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(guard_rules, "get_main_repo_dir")
    def test_blocks_removal_when_cwd_inside_worktree(self, mock_get_main, mock_get_locked):
        """Should block removal when CWD is inside target worktree.

        Also verifies that get_main_repo_dir is called to provide helpful error message.
        (Issue #360: モック呼び出し検証)
        """
        mock_get_locked.return_value = []  # Not locked
        mock_get_main.return_value = Path("/Users/test/project")

        # Use absolute path in command to avoid cwd-based resolution issues in tests
        with patch.object(Path, "cwd", return_value=Path("/Users/test/project/.worktrees/foo")):
            result = locked_worktree_guard.check_worktree_remove(
                "git worktree remove /Users/test/project/.worktrees/foo",
                hook_cwd="/Users/test/project/.worktrees/foo",
            )

        assert result is not None
        assert result["decision"] == "block"
        assert "現在のディレクトリがworktree内" in result["reason"]
        # Verify get_main_repo_dir was called to provide helpful cd command
        mock_get_main.assert_called_once()

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(guard_rules, "get_main_repo_dir")
    def test_blocks_removal_when_cwd_in_subdirectory_of_worktree(
        self, mock_get_main, mock_get_locked
    ):
        """Should block removal when CWD is in subdirectory of target worktree.

        Also verifies that get_main_repo_dir is called to provide helpful error message.
        (Issue #360: モック呼び出し検証)
        """
        mock_get_locked.return_value = []  # Not locked
        mock_get_main.return_value = Path("/Users/test/project")

        with patch.object(Path, "cwd", return_value=Path("/Users/test/project/.worktrees/foo/src")):
            result = locked_worktree_guard.check_worktree_remove(
                "git worktree remove /Users/test/project/.worktrees/foo",
                hook_cwd="/Users/test/project/.worktrees/foo/src",
            )

        assert result is not None
        assert result["decision"] == "block"
        assert "現在のディレクトリがworktree内" in result["reason"]
        # Verify get_main_repo_dir was called to provide helpful cd command
        mock_get_main.assert_called_once()

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    def test_allows_removal_from_main_repo(self, mock_get_locked):
        """Should allow removal when CWD is in main repo."""
        mock_get_locked.return_value = []  # Not locked

        with patch.object(Path, "cwd", return_value=Path("/Users/test/project")):
            result = locked_worktree_guard.check_worktree_remove(
                "git worktree remove /Users/test/project/.worktrees/foo",
                hook_cwd="/Users/test/project",
            )

        assert result is None

    # Issue #313: Test for resolve() exception handling
    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    def test_handles_resolve_oserror_gracefully(self, mock_get_locked):
        """Should handle OSError from resolve() gracefully.

        This tests Issue #313: resolve() exception handling
        """
        mock_get_locked.return_value = []

        # Test with a path that might cause resolve() issues
        # The function should not raise an exception
        with patch.object(Path, "cwd", return_value=Path("/Users/test/project")):
            # This should not raise even if resolve fails internally
            result = locked_worktree_guard.check_worktree_remove(
                "git worktree remove .worktrees/foo"
            )

        # Should return None (approve) when no locked worktrees match
        assert result is None

    @patch.object(guard_rules, "get_all_locked_worktree_paths")
    @patch.object(guard_rules, "is_cwd_inside_worktree")
    def test_handles_resolve_oserror_on_worktree_path(self, mock_is_cwd_inside, mock_get_locked):
        """Should handle OSError when resolving worktree path.

        This tests Issue #313: resolve() exception handling for broken symlinks
        """
        mock_is_cwd_inside.return_value = False  # Not inside worktree
        mock_get_locked.return_value = []

        # Mock Path.resolve to raise OSError
        original_resolve = Path.resolve

        def mock_resolve(self, *args, **kwargs):
            if ".worktrees/broken" in str(self):
                raise OSError("Broken symlink or permission denied")
            return original_resolve(self, *args, **kwargs)

        with patch.object(Path, "cwd", return_value=Path("/Users/test/project")):
            with patch.object(Path, "resolve", mock_resolve):
                # This should not raise even when resolve fails
                result = locked_worktree_guard.check_worktree_remove(
                    "git worktree remove .worktrees/broken"
                )

        # Should return None (approve) since path couldn't be resolved to match
        assert result is None


class TestIsCwdInsideWorktreeSameObject:
    """Additional tests for is_cwd_inside_worktree with same Path objects.

    Issue #360: worktree_path と cwd が同じ Path オブジェクトの場合のテスト
    """

    def test_same_path_object_instance(self):
        """Should return True when cwd and worktree are the same Path instance.

        This tests the edge case where the same Path object is used for both parameters.
        """
        path = Path("/Users/test/project/.worktrees/foo")
        result = locked_worktree_guard.is_cwd_inside_worktree(path, path)
        assert result
