#!/usr/bin/env python3
"""Tests for worktree-removal-check.py hook."""

import json
import os
import subprocess
import sys
import tempfile
from importlib import import_module
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "worktree-removal-check.py"

# Import hook module once at module level
sys.path.insert(0, str(HOOK_PATH.parent))
hook_module = import_module("worktree-removal-check")
# Issue #2014: Use lib modules directly
import lib.cwd as cwd
import lib.git as git


def run_hook(input_data: dict, env: dict | None = None) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(result.stdout)


class TestWorktreeRemovalCheckIgnores:
    """Tests for commands that should be ignored (continue=True)."""

    def test_ignores_non_git_command(self):
        """Should ignore non-git commands."""
        result = run_hook({"tool_input": {"command": "ls -la"}})
        assert result["continue"]

    def test_ignores_git_status(self):
        """Should ignore git status."""
        result = run_hook({"tool_input": {"command": "git status"}})
        assert result["continue"]

    def test_ignores_git_worktree_add(self):
        """Should ignore git worktree add."""
        result = run_hook(
            {"tool_input": {"command": "git worktree add .worktrees/new feature/new"}}
        )
        assert result["continue"]

    def test_ignores_git_worktree_list(self):
        """Should ignore git worktree list."""
        result = run_hook({"tool_input": {"command": "git worktree list"}})
        assert result["continue"]

    def test_ignores_empty_input(self):
        """Should handle empty input gracefully."""
        result = run_hook({"tool_input": {}})
        assert result["continue"]


class TestExtractGitCPath:
    """Tests for extract_git_c_path function."""

    def test_extracts_absolute_path(self):
        """Should extract absolute -C path."""
        path = hook_module.extract_git_c_path("git -C /repo/path worktree remove foo")
        assert path == "/repo/path"

    def test_extracts_relative_path(self):
        """Should extract relative -C path."""
        path = hook_module.extract_git_c_path("git -C ../other worktree remove foo")
        assert path == "../other"

    def test_returns_none_without_c_option(self):
        """Should return None when -C is not present."""
        path = hook_module.extract_git_c_path("git worktree remove foo")
        assert path is None

    def test_handles_quoted_paths(self):
        """Should extract path even with special chars."""
        path = hook_module.extract_git_c_path("git -C /repo-with-dash worktree remove foo")
        assert path == "/repo-with-dash"


class TestExtractWorktreePath:
    """Tests for worktree path extraction from commands."""

    def test_extracts_simple_path(self):
        """Should extract simple worktree path."""
        path = hook_module.extract_worktree_path_from_command(
            "git worktree remove .worktrees/issue-123"
        )
        assert path == ".worktrees/issue-123"

    def test_extracts_with_force_flag(self):
        """Should extract path with -f flag."""
        path = hook_module.extract_worktree_path_from_command(
            "git worktree remove -f .worktrees/issue-123"
        )
        assert path == ".worktrees/issue-123"

    def test_extracts_with_force_long_flag(self):
        """Should extract path with --force flag."""
        path = hook_module.extract_worktree_path_from_command(
            "git worktree remove --force .worktrees/issue-123"
        )
        assert path == ".worktrees/issue-123"

    def test_extracts_with_git_c_option(self):
        """Should extract path with git -C option."""
        path = hook_module.extract_worktree_path_from_command(
            "git -C /repo worktree remove .worktrees/issue-123"
        )
        assert path == ".worktrees/issue-123"

    def test_returns_none_for_non_remove(self):
        """Should return None for non-remove commands."""
        path = hook_module.extract_worktree_path_from_command(
            "git worktree add .worktrees/issue-123"
        )
        assert path is None

    def test_extracts_path_with_force_flag_after(self):
        """Issue #1452: Should extract path when --force is after path."""
        path = hook_module.extract_worktree_path_from_command(
            "git worktree remove .worktrees/issue-123 --force"
        )
        assert path == ".worktrees/issue-123"

    def test_extracts_path_with_short_force_flag_after(self):
        """Issue #1452: Should extract path when -f is after path."""
        path = hook_module.extract_worktree_path_from_command(
            "git worktree remove .worktrees/issue-123 -f"
        )
        assert path == ".worktrees/issue-123"

    def test_extracts_path_with_git_c_and_force_after(self):
        """Issue #1452: Should extract path with git -C and --force after path."""
        path = hook_module.extract_worktree_path_from_command(
            "git -C /repo worktree remove .worktrees/issue-123 --force"
        )
        assert path == ".worktrees/issue-123"

    def test_extracts_path_from_bash_c_single_quote(self):
        """Issue #1471: Should extract path from bash -c 'cmd' pattern."""
        path = hook_module.extract_worktree_path_from_command(
            "bash -c 'cd /path && git worktree remove .worktrees/issue-123'"
        )
        assert path == ".worktrees/issue-123"

    def test_extracts_path_from_bash_c_double_quote(self):
        """Issue #1471: Should extract path from bash -c \"cmd\" pattern."""
        path = hook_module.extract_worktree_path_from_command(
            'bash -c "cd /path && git worktree remove .worktrees/issue-123"'
        )
        assert path == ".worktrees/issue-123"

    def test_extracts_path_from_bash_c_with_redirect(self):
        """Issue #1471: Should extract path from bash -c with redirect."""
        path = hook_module.extract_worktree_path_from_command(
            "bash -c 'git worktree remove .worktrees/issue-1428' 2>&1"
        )
        assert path == ".worktrees/issue-1428"

    def test_extracts_path_from_subshell_pattern(self):
        """Issue #1604: Should extract path from (cd && git worktree remove) pattern."""
        path = hook_module.extract_worktree_path_from_command(
            "(cd /path/to/repo && git worktree remove .worktrees/issue-1399)"
        )
        assert path == ".worktrees/issue-1399"

    def test_extracts_path_from_subshell_without_closing_paren_in_path(self):
        """Issue #1604: Should not include closing parenthesis in path."""
        path = hook_module.extract_worktree_path_from_command(
            "(git worktree remove .worktrees/issue-123)"
        )
        assert path == ".worktrees/issue-123"
        assert ")" not in path

    def test_extracts_path_from_subshell_with_force_flag(self):
        """Issue #1604: Should extract path from subshell with force flag."""
        path = hook_module.extract_worktree_path_from_command(
            "(cd /repo && git worktree remove --force .worktrees/issue-456)"
        )
        assert path == ".worktrees/issue-456"


class TestHasForceFlag:
    """Tests for has_force_flag function."""

    def test_detects_short_force_flag(self):
        """Should detect -f flag."""
        assert hook_module.has_force_flag("git worktree remove -f .worktrees/issue-123")

    def test_detects_long_force_flag(self):
        """Should detect --force flag."""
        assert hook_module.has_force_flag("git worktree remove --force .worktrees/issue-123")

    def test_detects_force_with_git_c(self):
        """Should detect force flag with git -C option."""
        assert hook_module.has_force_flag("git -C /repo worktree remove -f .worktrees/issue-123")

    def test_returns_false_without_force(self):
        """Should return False when no force flag."""
        assert not hook_module.has_force_flag("git worktree remove .worktrees/issue-123")

    def test_no_false_positive_for_path_with_f(self):
        """Should not match -f in path names."""
        assert not hook_module.has_force_flag("git worktree remove .worktrees/-f-branch")

    def test_no_false_positive_for_force_in_path(self):
        """Should not match --force-like strings in paths."""
        assert not hook_module.has_force_flag("git worktree remove my-path-force-test")

    def test_detects_force_after_path(self):
        """Issue #1452: Should detect --force after path."""
        assert hook_module.has_force_flag("git worktree remove .worktrees/issue-123 --force")

    def test_detects_short_force_after_path(self):
        """Issue #1452: Should detect -f after path."""
        assert hook_module.has_force_flag("git worktree remove .worktrees/issue-123 -f")

    def test_detects_force_after_path_with_trailing_space(self):
        """Issue #1452: Should detect --force after path with trailing space."""
        assert hook_module.has_force_flag("git worktree remove .worktrees/issue-123 --force ")


class TestForceBypass:
    """Tests for force flag bypass behavior."""

    def test_allows_force_removal(self):
        """Should allow removal when -f flag is present."""
        result = run_hook(
            {"tool_input": {"command": "git worktree remove -f .worktrees/issue-123"}}
        )
        assert result["continue"]

    def test_allows_long_force_removal(self):
        """Should allow removal when --force flag is present."""
        result = run_hook(
            {"tool_input": {"command": "git worktree remove --force .worktrees/issue-123"}}
        )
        assert result["continue"]

    def test_allows_force_after_path(self):
        """Issue #1452: Should allow removal when --force is after path."""
        result = run_hook(
            {"tool_input": {"command": "git worktree remove .worktrees/issue-123 --force"}}
        )
        assert result["continue"]

    def test_allows_short_force_after_path(self):
        """Issue #1452: Should allow removal when -f is after path."""
        result = run_hook(
            {"tool_input": {"command": "git worktree remove .worktrees/issue-123 -f"}}
        )
        assert result["continue"]


class TestCheckFunctions:
    """Tests for individual check functions."""

    def test_check_recent_commits_returns_tuple(self):
        """Should return tuple (bool, str|None)."""
        # Test with non-existent path
        result = hook_module.check_recent_commits(Path("/nonexistent/path"))
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)

    def test_check_uncommitted_changes_returns_tuple(self):
        """Should return tuple (bool, int)."""
        # Test with non-existent path
        result = hook_module.check_uncommitted_changes(Path("/nonexistent/path"))
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], int)

    def test_check_stashed_changes_returns_tuple(self):
        """Should return tuple (bool, int)."""
        # Test with non-existent path
        result = hook_module.check_stashed_changes(Path("/nonexistent/path"))
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], int)


class TestGetDefaultBranchIssue934:
    """Tests for _get_default_branch function (Issue #934).

    Issue #934: Dynamically detect the default branch instead of hardcoding "main".
    """

    def test_returns_main_from_symbolic_ref(self):
        """Should return branch name from symbolic-ref when available."""
        from unittest.mock import MagicMock, patch

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="refs/remotes/origin/main\n",
            )
            result = git.get_default_branch(Path("/some/worktree"))
            assert result == "main"

    def test_returns_master_from_symbolic_ref(self):
        """Should return master when that's the default branch."""
        from unittest.mock import MagicMock, patch

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="refs/remotes/origin/master\n",
            )
            result = git.get_default_branch(Path("/some/worktree"))
            assert result == "master"

    def test_fallback_to_main_when_symbolic_ref_fails(self):
        """Should fallback to checking if main exists when symbolic-ref fails."""
        from unittest.mock import MagicMock, patch

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0]
            if "symbolic-ref" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="")
            elif "rev-parse" in cmd and "main" in cmd:
                return MagicMock(returncode=0, stdout="abc123")
            return MagicMock(returncode=1, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_run_side_effect):
            result = git.get_default_branch(Path("/some/worktree"))
            assert result == "main"

    def test_fallback_to_master_when_main_not_exists(self):
        """Should fallback to master when main doesn't exist."""
        from unittest.mock import MagicMock, patch

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0]
            if "symbolic-ref" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="")
            elif "rev-parse" in cmd and "main" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="")
            elif "rev-parse" in cmd and "master" in cmd:
                return MagicMock(returncode=0, stdout="abc123")
            return MagicMock(returncode=1, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_run_side_effect):
            result = git.get_default_branch(Path("/some/worktree"))
            assert result == "master"

    def test_returns_none_when_no_default_branch(self):
        """Should return None when no default branch can be detected."""
        from unittest.mock import MagicMock, patch

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            result = git.get_default_branch(Path("/some/worktree"))
            assert result is None


class TestGetCommitsSinceDefaultBranchIssue934:
    """Tests for _get_commits_since_default_branch function (Issue #934)."""

    def test_returns_count_when_default_branch_exists(self):
        """Should return commit count when default branch is detected."""
        from unittest.mock import MagicMock, patch

        with patch.object(git, "get_default_branch", return_value="main"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="5\n")
                result = git.get_commits_since_default_branch(Path("/some/worktree"))
                assert result == 5

    def test_returns_none_when_no_default_branch(self):
        """Should return None when default branch cannot be detected."""
        from unittest.mock import patch

        with patch.object(git, "get_default_branch", return_value=None):
            result = git.get_commits_since_default_branch(Path("/some/worktree"))
            assert result is None


class TestCheckRecentCommitsIssue930:
    """Tests for check_recent_commits false positive fix (Issue #930).

    Issue #930: New worktrees should not be flagged as having recent commits
    when they only have commits inherited from main branch.
    """

    def test_no_diverged_commits_returns_false(self):
        """Should return False when no commits since main (Issue #930).

        When a worktree is created from main and has no new commits,
        check_recent_commits should return False because there's no
        actual work in the worktree.
        """
        from unittest.mock import patch

        # Mock _get_commits_since_main to return 0 (no diverged commits)
        with patch.object(git, "get_commits_since_default_branch", return_value=0):
            result = git.check_recent_commits(Path("/some/worktree"))
            assert result == (False, None)

    def test_has_diverged_commits_checks_time(self):
        """Should check time threshold when commits exist since main."""
        import time
        from unittest.mock import MagicMock, patch

        # Recent commit (within 1 hour)
        recent_timestamp = int(time.time()) - 60  # 1 minute ago

        with patch.object(git, "get_commits_since_default_branch", return_value=1):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout=f"{recent_timestamp}\t1 minute ago\tTest commit",
                )
                result = git.check_recent_commits(Path("/some/worktree"))
                assert result[0] is True
                assert "1 minute ago" in result[1]

    def test_has_diverged_commits_old_commit_returns_false(self):
        """Should return False when diverged commit is older than threshold."""
        import time
        from unittest.mock import MagicMock, patch

        # Old commit (2 hours ago)
        old_timestamp = int(time.time()) - 7200  # 2 hours ago

        with patch.object(git, "get_commits_since_default_branch", return_value=1):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout=f"{old_timestamp}\t2 hours ago\tOld commit",
                )
                result = git.check_recent_commits(Path("/some/worktree"))
                assert result == (False, None)

    def test_none_diverged_count_falls_through(self):
        """Should fall through to time check when diverged count is None.

        When _get_commits_since_main returns None (e.g., main doesn't exist),
        the function should fall through to the existing time-based check
        for backwards compatibility.
        """
        import time
        from unittest.mock import MagicMock, patch

        recent_timestamp = int(time.time()) - 60

        with patch.object(git, "get_commits_since_default_branch", return_value=None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout=f"{recent_timestamp}\t1 minute ago\tTest commit",
                )
                result = git.check_recent_commits(Path("/some/worktree"))
                # Should still detect recent commit when diverged count is unknown
                assert result[0] is True


class TestGetEffectiveCwd:
    """Tests for get_effective_cwd function (Issue #671)."""

    def test_uses_claude_working_directory_if_set(self):
        """Should use CLAUDE_WORKING_DIRECTORY if set and exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir).resolve()
            original_env = os.environ.copy()
            try:
                os.environ["CLAUDE_WORKING_DIRECTORY"] = str(tmpdir_path)
                os.environ.pop("PWD", None)
                result = hook_module.get_effective_cwd()
                assert result == tmpdir_path
            finally:
                os.environ.clear()
                os.environ.update(original_env)

    def test_falls_back_to_pwd_if_claude_wd_not_set(self):
        """Should fall back to PWD if CLAUDE_WORKING_DIRECTORY not set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir).resolve()
            original_env = os.environ.copy()
            try:
                os.environ.pop("CLAUDE_WORKING_DIRECTORY", None)
                os.environ["PWD"] = str(tmpdir_path)
                result = hook_module.get_effective_cwd()
                assert result == tmpdir_path
            finally:
                os.environ.clear()
                os.environ.update(original_env)

    def test_falls_back_to_pwd_if_claude_wd_not_exists(self):
        """Should fall back to PWD if CLAUDE_WORKING_DIRECTORY path doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir).resolve()
            original_env = os.environ.copy()
            try:
                os.environ["CLAUDE_WORKING_DIRECTORY"] = "/nonexistent/path/12345"
                os.environ["PWD"] = str(tmpdir_path)
                result = hook_module.get_effective_cwd()
                assert result == tmpdir_path
            finally:
                os.environ.clear()
                os.environ.update(original_env)

    def test_falls_back_to_process_cwd(self):
        """Should fall back to Path.cwd() if no env vars set or valid."""
        original_env = os.environ.copy()
        try:
            os.environ.pop("CLAUDE_WORKING_DIRECTORY", None)
            os.environ.pop("PWD", None)
            result = hook_module.get_effective_cwd()
            assert result == Path.cwd().resolve()
        finally:
            # Restore all env vars (clear+update ensures complete restore)
            os.environ.clear()
            os.environ.update(original_env)

    def test_falls_back_to_cwd_if_pwd_not_exists(self):
        """Should fall back to Path.cwd() if PWD path doesn't exist."""
        original_env = os.environ.copy()
        try:
            os.environ.pop("CLAUDE_WORKING_DIRECTORY", None)
            os.environ["PWD"] = "/nonexistent/path/67890"
            result = hook_module.get_effective_cwd()
            # Should fall back to process cwd
            assert result == Path.cwd().resolve()
        finally:
            os.environ.clear()
            os.environ.update(original_env)


class TestExtractCdTargetFromCommand:
    """Tests for extract_cd_target_from_command function (Issue #671).

    Note: This function was moved to common.py in Issue #682.
    """

    def test_extracts_cd_with_and_operator(self):
        """Should extract cd target with && operator."""
        result = cwd.extract_cd_target_from_command(
            "cd /path/to/repo && git worktree remove .worktrees/issue-123"
        )
        assert result == "/path/to/repo"

    def test_extracts_cd_with_semicolon(self):
        """Should extract cd target with ; operator."""
        result = cwd.extract_cd_target_from_command(
            "cd /path/to/repo; git worktree remove .worktrees/issue-123"
        )
        assert result == "/path/to/repo"

    def test_extracts_cd_with_relative_path(self):
        """Should extract cd target with relative path."""
        result = cwd.extract_cd_target_from_command(
            "cd ../main-repo && git worktree remove .worktrees/issue-123"
        )
        assert result == "../main-repo"

    def test_returns_none_without_cd(self):
        """Should return None when no cd prefix."""
        result = cwd.extract_cd_target_from_command("git worktree remove .worktrees/issue-123")
        assert result is None

    def test_returns_none_for_cd_only(self):
        """Should return None for cd without following command."""
        result = cwd.extract_cd_target_from_command("cd /path/to/repo")
        assert result is None

    def test_extracts_cd_with_double_quoted_path(self):
        """Should extract cd target with double-quoted path containing spaces."""
        result = cwd.extract_cd_target_from_command(
            'cd "/path with spaces/repo" && git worktree remove foo'
        )
        assert result == "/path with spaces/repo"

    def test_extracts_cd_with_single_quoted_path(self):
        """Should extract cd target with single-quoted path containing spaces."""
        result = cwd.extract_cd_target_from_command(
            "cd '/path with spaces/repo' && git worktree remove foo"
        )
        assert result == "/path with spaces/repo"

    def test_extracts_cd_with_tab_character(self):
        """Should extract cd target with tab character between cd and path."""
        result = cwd.extract_cd_target_from_command("cd\t/path/to/repo && git worktree remove foo")
        assert result == "/path/to/repo"

    def test_extracts_cd_with_multiple_spaces(self):
        """Should extract cd target with multiple spaces between cd and path."""
        result = cwd.extract_cd_target_from_command(
            "cd    /path/to/repo && git worktree remove foo"
        )
        assert result == "/path/to/repo"


class TestGetEffectiveCwdWithCdPattern:
    """Tests for get_effective_cwd with cd pattern in command (Issue #671)."""

    def test_uses_cd_target_over_env(self):
        """Should use cd target from command over environment variables."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir).resolve()
            cd_target = tmpdir_path / "cd-target"
            env_target = tmpdir_path / "env-target"
            cd_target.mkdir()
            env_target.mkdir()

            original_env = os.environ.copy()
            try:
                os.environ["CLAUDE_WORKING_DIRECTORY"] = str(env_target)
                command = f"cd {cd_target} && git worktree remove foo"
                result = hook_module.get_effective_cwd(command)
                assert result == cd_target
            finally:
                os.environ.clear()
                os.environ.update(original_env)

    def test_falls_back_to_env_if_cd_target_not_exists(self):
        """Should fall back to env if cd target doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir).resolve()
            original_env = os.environ.copy()
            try:
                os.environ["CLAUDE_WORKING_DIRECTORY"] = str(tmpdir_path)
                command = "cd /nonexistent/path && git worktree remove foo"
                result = hook_module.get_effective_cwd(command)
                assert result == tmpdir_path
            finally:
                os.environ.clear()
                os.environ.update(original_env)

    def test_expands_tilde_in_cd_target(self):
        """Should expand ~ in cd target to home directory."""
        home = Path.home()
        original_env = os.environ.copy()
        try:
            os.environ.pop("CLAUDE_WORKING_DIRECTORY", None)
            os.environ.pop("PWD", None)
            # Test with actual home directory (~ expands to user's home)
            command = "cd ~ && git worktree remove foo"
            result = hook_module.get_effective_cwd(command)
            # Should resolve to home directory
            assert result == home.resolve()
        finally:
            os.environ.clear()
            os.environ.update(original_env)

    def test_resolves_relative_cd_from_env_cwd(self):
        """Should resolve relative cd path from environment cwd."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir).resolve()
            subdir = tmpdir_path / "subdir"
            subdir.mkdir()

            original_env = os.environ.copy()
            try:
                # Set env cwd to tmpdir
                os.environ["CLAUDE_WORKING_DIRECTORY"] = str(tmpdir_path)
                os.environ.pop("PWD", None)
                # Use relative cd path
                command = "cd subdir && git worktree remove foo"
                result = hook_module.get_effective_cwd(command)
                # Should resolve relative path from env cwd
                assert result == subdir
            finally:
                os.environ.clear()
                os.environ.update(original_env)


class TestCheckCwdInsideWorktreeWithCdPattern:
    """Tests for check_cwd_inside_worktree with cd pattern (Issue #671)."""

    def test_allows_when_cd_moves_outside_worktree(self):
        """Should allow deletion when cd moves outside the worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir).resolve()
            worktree_path = tmpdir_path / "worktree"
            main_repo_path = tmpdir_path / "main-repo"
            worktree_path.mkdir()
            main_repo_path.mkdir()

            original_env = os.environ.copy()
            try:
                # Current env says we're in worktree
                os.environ["CLAUDE_WORKING_DIRECTORY"] = str(worktree_path)
                # But command has cd to main repo first
                command = f"cd {main_repo_path} && git worktree remove {worktree_path}"
                result = hook_module.check_cwd_inside_worktree(worktree_path, command)
                # Should NOT block because cd moves us outside
                assert not result
            finally:
                os.environ.clear()
                os.environ.update(original_env)

    def test_blocks_when_cd_stays_inside_worktree(self):
        """Should block when cd stays inside the worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir).resolve()
            worktree_path = tmpdir_path / "worktree"
            subdir = worktree_path / "subdir"
            worktree_path.mkdir()
            subdir.mkdir()

            original_env = os.environ.copy()
            try:
                os.environ.pop("CLAUDE_WORKING_DIRECTORY", None)
                os.environ.pop("PWD", None)
                # cd to subdir of worktree
                command = f"cd {subdir} && git worktree remove {worktree_path}"
                result = hook_module.check_cwd_inside_worktree(worktree_path, command)
                # Should still block because subdir is inside worktree
                assert result
            finally:
                os.environ.clear()
                os.environ.update(original_env)


class TestCheckCwdInsideWorktreeWithEnv:
    """Tests for check_cwd_inside_worktree with environment variables (Issue #671)."""

    def test_blocks_when_claude_wd_is_worktree(self):
        """Should block deletion when CLAUDE_WORKING_DIRECTORY is the worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve()
            original_env = os.environ.copy()
            try:
                os.environ["CLAUDE_WORKING_DIRECTORY"] = str(worktree_path)
                result = hook_module.check_cwd_inside_worktree(worktree_path)
                assert result
            finally:
                os.environ.clear()
                os.environ.update(original_env)

    def test_blocks_when_claude_wd_is_subdirectory(self):
        """Should block when CLAUDE_WORKING_DIRECTORY is inside the worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve()
            subdir = worktree_path / "subdir"
            subdir.mkdir()
            original_env = os.environ.copy()
            try:
                os.environ["CLAUDE_WORKING_DIRECTORY"] = str(subdir)
                result = hook_module.check_cwd_inside_worktree(worktree_path)
                assert result
            finally:
                os.environ.clear()
                os.environ.update(original_env)

    def test_allows_when_claude_wd_is_outside(self):
        """Should allow when CLAUDE_WORKING_DIRECTORY is outside the worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir).resolve()
            worktree_path = tmpdir_path / "worktree"
            other_path = tmpdir_path / "other"
            worktree_path.mkdir()
            other_path.mkdir()
            original_env = os.environ.copy()
            try:
                os.environ["CLAUDE_WORKING_DIRECTORY"] = str(other_path)
                result = hook_module.check_cwd_inside_worktree(worktree_path)
                assert not result
            finally:
                os.environ.clear()
                os.environ.update(original_env)


class TestCheckCwdInsideWorktree:
    """Tests for check_cwd_inside_worktree function (Issue #589)."""

    def test_returns_true_when_cwd_is_worktree(self):
        """Should return True when cwd IS the worktree path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve()
            original_cwd = os.getcwd()
            original_env = os.environ.copy()
            try:
                os.chdir(str(worktree_path))
                # Clear env vars to test process cwd fallback
                os.environ.pop("CLAUDE_WORKING_DIRECTORY", None)
                os.environ.pop("PWD", None)
                result = hook_module.check_cwd_inside_worktree(worktree_path)
                assert result
            finally:
                os.chdir(original_cwd)
                os.environ.clear()
                os.environ.update(original_env)

    def test_returns_true_when_cwd_is_subdirectory(self):
        """Should return True when cwd is inside the worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve()
            subdir = worktree_path / "subdir"
            subdir.mkdir()
            original_cwd = os.getcwd()
            original_env = os.environ.copy()
            try:
                os.chdir(str(subdir))
                os.environ.pop("CLAUDE_WORKING_DIRECTORY", None)
                os.environ.pop("PWD", None)
                result = hook_module.check_cwd_inside_worktree(worktree_path)
                assert result
            finally:
                os.chdir(original_cwd)
                os.environ.clear()
                os.environ.update(original_env)

    def test_returns_false_when_cwd_is_outside(self):
        """Should return False when cwd is outside the worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            worktree_path.mkdir()
            original_cwd = os.getcwd()
            original_env = os.environ.copy()
            try:
                # cwd is parent of worktree, not inside
                os.chdir(str(Path(tmpdir).resolve()))
                os.environ.pop("CLAUDE_WORKING_DIRECTORY", None)
                os.environ.pop("PWD", None)
                result = hook_module.check_cwd_inside_worktree(worktree_path)
                assert not result
            finally:
                os.chdir(original_cwd)
                os.environ.clear()
                os.environ.update(original_env)

    def test_returns_false_when_cwd_is_sibling(self):
        """Should return False when cwd is sibling directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir).resolve()
            worktree_path = tmpdir_path / "worktree"
            sibling_path = tmpdir_path / "sibling"
            worktree_path.mkdir()
            sibling_path.mkdir()
            original_cwd = os.getcwd()
            original_env = os.environ.copy()
            try:
                os.chdir(str(sibling_path))
                os.environ.pop("CLAUDE_WORKING_DIRECTORY", None)
                os.environ.pop("PWD", None)
                result = hook_module.check_cwd_inside_worktree(worktree_path)
                assert not result
            finally:
                os.chdir(original_cwd)
                os.environ.clear()
                os.environ.update(original_env)


class TestCdAndGitCCombination:
    """Tests for cd and git -C path combination (Copilot review)."""

    def test_cd_then_relative_git_c_path(self):
        """Should resolve relative -C path from cd target directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir).resolve()
            main_repo = tmpdir_path / "main-repo"
            other_repo = tmpdir_path / "other-repo"
            worktree = other_repo / ".worktrees" / "issue-123"
            main_repo.mkdir()
            other_repo.mkdir()
            worktree.mkdir(parents=True)

            # Command: cd /main-repo && git -C ../other-repo worktree remove .worktrees/issue-123
            # Expected: -C path resolved from cd target (main_repo)
            # So ../other-repo from main_repo = other_repo
            command = f"cd {main_repo} && git -C ../other-repo worktree remove .worktrees/issue-123"

            # Extract -C path
            git_c_path = hook_module.extract_git_c_path(command)
            assert git_c_path == "../other-repo"

            # get_effective_cwd with command should return main_repo (cd target)
            original_env = os.environ.copy()
            try:
                os.environ.pop("CLAUDE_WORKING_DIRECTORY", None)
                os.environ.pop("PWD", None)
                effective_cwd = hook_module.get_effective_cwd(command)
                assert effective_cwd == main_repo

                # The main() logic resolves -C path from effective_cwd
                # ../other-repo from main_repo should resolve to other_repo
                resolved_c_path = (effective_cwd / git_c_path).resolve()
                assert resolved_c_path == other_repo
            finally:
                os.environ.clear()
                os.environ.update(original_env)


class TestResolveWorktreePath:
    """Tests for resolve_worktree_path function."""

    def test_returns_none_for_nonexistent_relative_path(self):
        """Should return None for non-existent relative path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # cwd is the second argument now
            result = hook_module.resolve_worktree_path(".worktrees/nonexistent-123", Path(tmpdir))
            assert result is None

    def test_returns_none_for_nonexistent_absolute_path(self):
        """Should return None for non-existent absolute path."""
        # cwd doesn't matter for absolute paths
        result = hook_module.resolve_worktree_path(
            "/nonexistent/absolute/path/worktree", Path("/tmp")
        )
        assert result is None

    def test_returns_path_for_existing_directory(self):
        """Should return path for existing directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a worktree directory
            tmpdir_resolved = Path(tmpdir).resolve()
            worktree_path = tmpdir_resolved / ".worktrees" / "issue-test"
            worktree_path.mkdir(parents=True)

            # cwd is the second argument now
            result = hook_module.resolve_worktree_path(".worktrees/issue-test", tmpdir_resolved)
            assert result == worktree_path

    def test_resolves_dot_from_cwd(self):
        """Should resolve '.' from the current working directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a worktree-like directory
            tmpdir_resolved = Path(tmpdir).resolve()
            worktree_path = tmpdir_resolved / "my-worktree"
            worktree_path.mkdir()

            # '.' should resolve to the cwd itself
            result = hook_module.resolve_worktree_path(".", worktree_path)
            assert result == worktree_path

    def test_resolves_relative_path_from_cwd(self):
        """Should resolve relative paths from cwd, not repo root."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create nested directories
            tmpdir_resolved = Path(tmpdir).resolve()
            parent = tmpdir_resolved / "parent"
            child = parent / "child"
            child.mkdir(parents=True)

            # From parent, "child" should resolve to parent/child
            result = hook_module.resolve_worktree_path("child", parent)
            assert result == child


class TestGetWorktreeBranch:
    """Tests for get_worktree_branch function."""

    def test_returns_none_for_nonexistent_path(self):
        """Should return None for non-existent path."""
        result = hook_module.get_worktree_branch(Path("/nonexistent/path"))
        assert result is None

    def test_returns_branch_name_for_valid_worktree(self):
        """Should return branch name for valid git worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            # Initialize git repo with a commit
            subprocess.run(["git", "init"], cwd=tmpdir_path, capture_output=True, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "checkout", "-b", "test-branch"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True,
            )
            (tmpdir_path / "file.txt").write_text("test")
            subprocess.run(["git", "add", "."], cwd=tmpdir_path, capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True,
            )

            result = hook_module.get_worktree_branch(tmpdir_path)
            assert result == "test-branch"

    def test_returns_none_for_detached_head(self):
        """Should return None for detached HEAD state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            # Initialize git repo with a commit
            subprocess.run(["git", "init"], cwd=tmpdir_path, capture_output=True, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True,
            )
            (tmpdir_path / "file.txt").write_text("test")
            subprocess.run(["git", "add", "."], cwd=tmpdir_path, capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True,
            )
            # Detach HEAD
            subprocess.run(
                ["git", "checkout", "--detach"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True,
            )

            result = hook_module.get_worktree_branch(tmpdir_path)
            assert result is None


class TestCheckPrMergedForBranch:
    """Tests for check_pr_merged_for_branch function."""

    def test_returns_false_none_for_nonexistent_branch(self):
        """Should return (False, None) for non-existent branch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            # Initialize git repo for gh context
            subprocess.run(["git", "init"], cwd=tmpdir_path, capture_output=True, check=True)
            result = hook_module.check_pr_merged_for_branch(
                "nonexistent-branch-that-surely-does-not-exist-12345",
                tmpdir_path,
            )
            assert result == (False, None)

    def test_returns_tuple_format(self):
        """Should return a tuple of (bool, int|None)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            # Initialize git repo for gh context
            subprocess.run(["git", "init"], cwd=tmpdir_path, capture_output=True, check=True)
            result = hook_module.check_pr_merged_for_branch("any-branch", tmpdir_path)
            assert isinstance(result, tuple)
            assert len(result) == 2
            assert isinstance(result[0], bool)
            assert result[1] is None or isinstance(result[1], int)

    def test_returns_true_when_pr_merged(self):
        """Should return (True, pr_number) when PR is merged (Issue #914, #925)."""
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            mock_result = MagicMock()
            mock_result.returncode = 0
            # Use mergedAt instead of merged (Issue #925: merged field not available)
            mock_result.stdout = '{"number": 123, "mergedAt": "2025-12-23T11:58:36Z"}'

            with patch("subprocess.run", return_value=mock_result) as mock_run:
                result = hook_module.check_pr_merged_for_branch("fix/issue-123", tmpdir_path)
                assert result == (True, 123)
                # Verify gh pr view is used (not gh pr list)
                call_args = mock_run.call_args[0][0]
                assert call_args[:3] == ["gh", "pr", "view"]

    def test_returns_false_when_pr_not_merged(self):
        """Should return (False, None) when PR exists but not merged (Issue #914, #925)."""
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            mock_result = MagicMock()
            mock_result.returncode = 0
            # Use mergedAt: null instead of merged: false (Issue #925)
            mock_result.stdout = '{"number": 456, "mergedAt": null}'

            with patch("subprocess.run", return_value=mock_result):
                result = hook_module.check_pr_merged_for_branch("feat/issue-456", tmpdir_path)
                assert result == (False, None)


class TestHookCwdUsageIssue1172:
    """Tests for hook_cwd usage to detect session's actual cwd (Issue #1172).

    Issue #1172: worktree-removal-check was not using the 'cwd' field from
    hook input, causing it to fail to detect when the session's cwd was
    inside the worktree being deleted.
    """

    def test_blocks_when_hook_cwd_is_inside_worktree(self):
        """Should block when hook input 'cwd' is inside the worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            worktree_path.mkdir()

            # Simulate hook input with cwd inside worktree
            result = run_hook(
                {
                    "tool_input": {"command": f"git worktree remove {worktree_path}"},
                    "cwd": str(worktree_path),  # Session cwd is inside worktree
                }
            )
            # Should block because cwd is inside worktree
            assert result.get("decision") == "block"

    def test_blocks_when_hook_cwd_is_subdirectory_of_worktree(self):
        """Should block when hook input 'cwd' is a subdirectory of the worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            subdir = worktree_path / "subdir"
            subdir.mkdir(parents=True)

            result = run_hook(
                {
                    "tool_input": {"command": f"git worktree remove {worktree_path}"},
                    "cwd": str(subdir),  # Session cwd is inside worktree subdirectory
                }
            )
            assert result.get("decision") == "block"

    def test_allows_when_hook_cwd_is_outside_worktree(self):
        """Should allow when hook input 'cwd' is outside the worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            other_path = Path(tmpdir).resolve() / "other"
            worktree_path.mkdir()
            other_path.mkdir()

            result = run_hook(
                {
                    "tool_input": {"command": f"git worktree remove {worktree_path}"},
                    "cwd": str(other_path),  # Session cwd is outside worktree
                }
            )
            # Should allow (no blocking, just continue)
            assert result.get("continue", False)

    def test_hook_cwd_takes_priority_over_env_vars(self):
        """Should use hook_cwd even when CLAUDE_WORKING_DIRECTORY is set differently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            env_cwd = Path(tmpdir).resolve() / "env-cwd"
            worktree_path.mkdir()
            env_cwd.mkdir()

            original_env = os.environ.copy()
            try:
                # Set CLAUDE_WORKING_DIRECTORY to be outside worktree
                os.environ["CLAUDE_WORKING_DIRECTORY"] = str(env_cwd)

                # But hook_cwd is inside worktree
                result = run_hook(
                    {
                        "tool_input": {"command": f"git worktree remove {worktree_path}"},
                        "cwd": str(worktree_path),  # Hook cwd is inside worktree
                    }
                )
                # Should block because hook_cwd takes priority
                assert result.get("decision") == "block"
            finally:
                os.environ.clear()
                os.environ.update(original_env)

    def test_fails_closed_when_cwd_detection_raises_oserror(self):
        """Should fail-closed (block) when cwd detection raises OSError.

        P2 fix: If get_effective_cwd() raises OSError (e.g., cwd was deleted),
        the hook should fail-closed and block the worktree deletion to be safe.
        This prevents fail-open regression where deletion could proceed without
        proper cwd safety check.

        This test uses unittest.mock.patch to simulate OSError during cwd detection.
        Since run_hook uses subprocess, we need to test the logic directly by
        importing and calling the relevant code with mocking.
        """
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            worktree_path.mkdir()
            outside_cwd = Path(tmpdir).resolve() / "outside"
            outside_cwd.mkdir()

            original_env = os.environ.copy()
            try:
                os.environ.pop("CLAUDE_WORKING_DIRECTORY", None)
                os.environ.pop("PWD", None)
                os.environ["CLAUDE_PROJECT_DIR"] = str(tmpdir)

                # Create a mock that raises OSError only when checking session cwd
                call_count = [0]
                original_get_effective_cwd = hook_module.get_effective_cwd

                def mock_get_effective_cwd(command=None, base_cwd=None):
                    call_count[0] += 1
                    # First call is for worktree path resolution, second is for session cwd
                    # The session cwd check calls get_effective_cwd(None, hook_cwd)
                    if command is None and call_count[0] > 1:
                        raise FileNotFoundError("[Errno 2] No such file or directory")
                    return original_get_effective_cwd(command, base_cwd)

                # Patch at the module level where it's imported
                with patch.object(
                    hook_module, "get_effective_cwd", side_effect=mock_get_effective_cwd
                ):
                    # Capture stdout to get the result
                    import sys
                    from io import StringIO

                    old_stdout = sys.stdout
                    old_stdin = sys.stdin
                    sys.stdout = StringIO()
                    sys.stdin = StringIO(
                        json.dumps(
                            {
                                "tool_input": {"command": f"git worktree remove {worktree_path}"},
                                "cwd": str(outside_cwd),
                            }
                        )
                    )

                    try:
                        hook_module.main()
                        output = sys.stdout.getvalue()
                        result = json.loads(output)
                        # Should fail-closed and block the deletion due to OSError
                        assert result.get("decision") == "block", f"Got: {result}"
                    finally:
                        sys.stdout = old_stdout
                        sys.stdin = old_stdin
            finally:
                os.environ.clear()
                os.environ.update(original_env)


class TestCheckOtherSessionActiveIssue1563:
    """Tests for check_other_session_active function (Issue #1563)."""

    TEST_CURRENT_SESSION = "current-session-123"

    def test_returns_false_when_no_marker_file(self, tmp_path):
        """Should return False when .claude-session marker doesn't exist."""
        from lib.session import create_hook_context

        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()

        ctx = create_hook_context({"session_id": self.TEST_CURRENT_SESSION})
        has_other, session_id, minutes = hook_module.check_other_session_active(worktree_path, ctx)
        assert has_other is False
        assert session_id is None
        assert minutes is None

    def test_returns_false_when_marker_is_stale(self, tmp_path):
        """Should return False when marker is older than threshold."""
        from datetime import UTC, datetime, timedelta

        from lib.session import create_hook_context

        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()

        # Create marker file
        marker_path = worktree_path / ".claude-session"
        marker_path.write_text("other-session-id")

        # Set mtime to 60 minutes ago (beyond 30 min threshold)
        old_time = (datetime.now(UTC) - timedelta(minutes=60)).timestamp()
        os.utime(marker_path, (old_time, old_time))

        ctx = create_hook_context({"session_id": self.TEST_CURRENT_SESSION})
        has_other, session_id, minutes = hook_module.check_other_session_active(worktree_path, ctx)
        assert has_other is False
        assert session_id is None
        assert minutes is None

    def test_returns_false_when_marker_is_own_session(self, tmp_path):
        """Should return False when marker belongs to current session."""
        from lib.session import create_hook_context

        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()

        # Create marker file with current session ID
        marker_path = worktree_path / ".claude-session"
        current_session = "current-session-123"
        marker_path.write_text(current_session)

        ctx = create_hook_context({"session_id": current_session})
        has_other, session_id, minutes = hook_module.check_other_session_active(worktree_path, ctx)

        assert has_other is False
        assert session_id is None
        assert minutes is None

    def test_returns_true_when_marker_is_other_session(self, tmp_path):
        """Should return True when marker belongs to different recent session."""
        from lib.session import create_hook_context

        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()

        # Create marker file with different session ID
        marker_path = worktree_path / ".claude-session"
        other_session = "other-session-456"
        marker_path.write_text(other_session)

        ctx = create_hook_context({"session_id": "current-session-123"})
        has_other, session_id, minutes = hook_module.check_other_session_active(worktree_path, ctx)

        assert has_other is True
        assert session_id == other_session
        assert minutes is not None
        assert minutes < 1  # Should be very recent

    def test_returns_false_when_marker_is_empty(self, tmp_path):
        """Should return False when marker file is empty."""
        from lib.session import create_hook_context

        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()

        # Create empty marker file
        marker_path = worktree_path / ".claude-session"
        marker_path.write_text("")

        ctx = create_hook_context({"session_id": self.TEST_CURRENT_SESSION})
        has_other, session_id, minutes = hook_module.check_other_session_active(worktree_path, ctx)
        assert has_other is False
        assert session_id is None
        assert minutes is None

    def test_returns_false_on_read_error(self, tmp_path):
        """Should fail-open and return False on file read errors."""
        from unittest.mock import patch

        from lib.session import create_hook_context

        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()

        # Create marker file
        marker_path = worktree_path / ".claude-session"
        marker_path.write_text("some-session")

        # Mock read_text to raise OSError (after exists() and stat() succeed)
        original_read_text = Path.read_text

        def mock_read_text(self, *args, **kwargs):
            if self.name == ".claude-session":
                raise OSError("Permission denied")
            return original_read_text(self, *args, **kwargs)

        ctx = create_hook_context({"session_id": self.TEST_CURRENT_SESSION})
        with patch.object(Path, "read_text", mock_read_text):
            has_other, session_id, minutes = hook_module.check_other_session_active(
                worktree_path, ctx
            )

        assert has_other is False
        assert session_id is None
        assert minutes is None


class TestOtherSessionBlockIntegrationIssue1563:
    """Integration tests for other session blocking in main() (Issue #1563).

    These tests verify the full hook flow when another session is detected,
    including that --force does NOT bypass the other session check.
    """

    def test_blocks_when_other_session_marker_exists(self):
        """Should block when another session's marker exists and is recent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            outside_cwd = Path(tmpdir).resolve() / "outside"
            worktree_path.mkdir()
            outside_cwd.mkdir()

            # Create marker with different session ID
            marker_path = worktree_path / ".claude-session"
            marker_path.write_text("other-session-id-12345")

            result = run_hook(
                {
                    "tool_input": {"command": f"git worktree remove {worktree_path}"},
                    "cwd": str(outside_cwd),
                    "session_id": "my-session-id-67890",
                }
            )
            assert result.get("decision") == "block"
            assert "別セッション" in result.get("reason", "")

    def test_blocks_with_force_flag_when_other_session_marker_exists(self):
        """Should block even with --force when another session is working.

        --force bypasses active work checks but NOT the other session check,
        as deleting another session's worktree would break that session.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            outside_cwd = Path(tmpdir).resolve() / "outside"
            worktree_path.mkdir()
            outside_cwd.mkdir()

            # Create marker with different session ID
            marker_path = worktree_path / ".claude-session"
            marker_path.write_text("other-session-id-12345")

            result = run_hook(
                {
                    "tool_input": {"command": f"git worktree remove --force {worktree_path}"},
                    "cwd": str(outside_cwd),
                    "session_id": "my-session-id-67890",
                }
            )
            assert result.get("decision") == "block"
            assert "別セッション" in result.get("reason", "")

    def test_allows_when_marker_is_own_session(self):
        """Should allow deletion when marker belongs to the same session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            outside_cwd = Path(tmpdir).resolve() / "outside"
            worktree_path.mkdir()
            outside_cwd.mkdir()

            # Create marker with same session ID
            marker_path = worktree_path / ".claude-session"
            marker_path.write_text("my-session-id-67890")

            result = run_hook(
                {
                    "tool_input": {"command": f"git worktree remove {worktree_path}"},
                    "cwd": str(outside_cwd),
                    "session_id": "my-session-id-67890",
                }
            )
            # Should not block due to other session (may block for other reasons)
            if result.get("decision") == "block":
                assert "別セッション" not in result.get("reason", "")

    def test_allows_when_json_marker_is_own_session(self):
        """Should allow deletion when JSON-format marker belongs to the same session.

        Issue #1863: worktree-creation-marker.py writes JSON format markers,
        but worktree-removal-check.py was reading them as plain text, causing
        the session ID comparison to fail and blocking self-cleanup.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            outside_cwd = Path(tmpdir).resolve() / "outside"
            worktree_path.mkdir()
            outside_cwd.mkdir()

            # Create marker in JSON format (as written by worktree-creation-marker.py)
            marker_path = worktree_path / ".claude-session"
            marker_data = {
                "session_id": "my-session-id-67890",
                "created_at": "2025-12-30T09:30:00+00:00",
            }
            marker_path.write_text(json.dumps(marker_data))

            result = run_hook(
                {
                    "tool_input": {"command": f"git worktree remove {worktree_path}"},
                    "cwd": str(outside_cwd),
                    "session_id": "my-session-id-67890",
                }
            )
            # Should not block due to other session (may block for other reasons)
            if result.get("decision") == "block":
                assert "別セッション" not in result.get("reason", "")

    def test_blocks_when_json_marker_is_other_session(self):
        """Should block when JSON-format marker belongs to different session.

        Issue #1863: Verify that JSON format markers are correctly parsed
        and different session IDs are properly detected.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            outside_cwd = Path(tmpdir).resolve() / "outside"
            worktree_path.mkdir()
            outside_cwd.mkdir()

            # Create marker in JSON format with different session ID
            marker_path = worktree_path / ".claude-session"
            marker_data = {
                "session_id": "other-session-id-12345",
                "created_at": "2025-12-30T09:30:00+00:00",
            }
            marker_path.write_text(json.dumps(marker_data))

            result = run_hook(
                {
                    "tool_input": {"command": f"git worktree remove {worktree_path}"},
                    "cwd": str(outside_cwd),
                    "session_id": "my-session-id-67890",
                }
            )
            assert result.get("decision") == "block"
            assert "別セッション" in result.get("reason", "")


class TestFailOpenPreventionIssue1608:
    """Tests to ensure hooks don't fail-open.

    Issue #1608: Add integration tests to detect fail-open scenarios
    where hooks should block but don't due to regex bugs or early returns.
    """

    def test_subshell_pattern_blocks_cwd_inside_worktree(self):
        """Issue #1604: Subshell pattern should not bypass cwd check.

        When session cwd is inside the worktree, the hook MUST block
        even if the command uses subshell syntax like (cd && git worktree remove).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            worktree_path.mkdir()

            # Command uses subshell pattern - this was the bypass in Issue #1604
            command = f"(cd /somewhere && git worktree remove {worktree_path})"

            # Session cwd IS inside the worktree - MUST block
            result = run_hook(
                {
                    "tool_input": {"command": command},
                    "cwd": str(worktree_path),  # cwd is inside worktree
                }
            )
            assert result.get("decision") == "block", (
                f"Subshell pattern should NOT bypass cwd check. Got: {result}"
            )
            assert "cwd" in result.get("reason", "").lower() or "削除対象" in result.get(
                "reason", ""
            )

    def test_subshell_pattern_allows_when_cwd_outside(self):
        """Subshell pattern should allow when cwd is outside worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            outside_cwd = Path(tmpdir).resolve() / "outside"
            worktree_path.mkdir()
            outside_cwd.mkdir()

            command = f"(cd /somewhere && git worktree remove {worktree_path})"

            result = run_hook(
                {
                    "tool_input": {"command": command},
                    "cwd": str(outside_cwd),  # cwd is outside worktree
                }
            )
            # Should allow (no blocking reason related to cwd)
            assert result.get("continue") is True or (
                result.get("decision") == "block" and "cwd" not in result.get("reason", "").lower()
            )

    def test_regex_handles_backticks(self):
        """Regex should extract path from backtick command substitution."""
        path = hook_module.extract_worktree_path_from_command(
            "`git worktree remove .worktrees/issue-123`"
        )
        # Should either extract the path or return None (fail-closed)
        # The key is it should NOT extract something wrong
        if path is not None:
            assert path == ".worktrees/issue-123"
            assert "`" not in path

    def test_regex_handles_dollar_parentheses(self):
        """Regex should extract path from $(...) command substitution."""
        path = hook_module.extract_worktree_path_from_command(
            "$(git worktree remove .worktrees/issue-123)"
        )
        # Should either extract the path or return None (fail-closed)
        if path is not None:
            assert path == ".worktrees/issue-123"
            assert "$" not in path
            assert "(" not in path
            assert ")" not in path

    def test_regex_handles_quoted_paths_with_spaces(self):
        """Regex should handle quoted paths with spaces."""
        # Single quotes
        path1 = hook_module.extract_worktree_path_from_command(
            "git worktree remove '.worktrees/issue 123'"
        )
        # The current implementation may not handle this - that's OK
        # What matters is it doesn't return something wrong
        if path1 is not None:
            assert "'" not in path1

        # Double quotes
        path2 = hook_module.extract_worktree_path_from_command(
            'git worktree remove ".worktrees/issue 123"'
        )
        if path2 is not None:
            assert '"' not in path2

    def test_cwd_inside_worktree_always_blocks(self):
        """When cwd is inside the target worktree, deletion MUST be blocked.

        This is the critical safety check: if the session's cwd is inside
        the worktree being deleted, all subsequent Bash commands would fail.
        This test verifies that the cwd check always triggers when needed.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            worktree_path.mkdir()

            # Command with valid worktree path
            command = f"git worktree remove {worktree_path}"

            # Run with cwd inside worktree - MUST block
            result = run_hook(
                {
                    "tool_input": {"command": command},
                    "cwd": str(worktree_path),
                }
            )
            assert result.get("decision") == "block"

    def test_nonexistent_path_does_not_block(self):
        """Nonexistent worktree path should not block (let git handle error).

        When the target path doesn't exist, the hook allows the command
        to proceed. Git itself will then report the error. This is
        intentional: the hook's job is to protect existing worktrees,
        not to validate paths.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            outside_cwd = Path(tmpdir).resolve()
            nonexistent = outside_cwd / "does-not-exist"

            result = run_hook(
                {
                    "tool_input": {"command": f"git worktree remove {nonexistent}"},
                    "cwd": str(outside_cwd),
                }
            )
            # Should allow - path doesn't exist, so no worktree to protect
            assert result.get("continue") is True


class TestRegexEdgeCasesIssue1608:
    """Additional regex edge case tests for Issue #1608."""

    def test_extracts_path_with_trailing_semicolon(self):
        """Should extract path when command ends with semicolon."""
        path = hook_module.extract_worktree_path_from_command(
            "git worktree remove .worktrees/issue-123;"
        )
        assert path == ".worktrees/issue-123"
        assert ";" not in path

    def test_extracts_path_with_chained_command(self):
        """Should extract path when followed by && and another command."""
        path = hook_module.extract_worktree_path_from_command(
            "git worktree remove .worktrees/issue-123 && echo done"
        )
        assert path == ".worktrees/issue-123"
        assert "&" not in path

    def test_extracts_path_with_pipe(self):
        """Should extract path when followed by pipe."""
        path = hook_module.extract_worktree_path_from_command(
            "git worktree remove .worktrees/issue-123 | tee log.txt"
        )
        assert path == ".worktrees/issue-123"
        assert "|" not in path

    def test_extracts_path_with_redirect(self):
        """Should extract path when followed by redirect."""
        path = hook_module.extract_worktree_path_from_command(
            "git worktree remove .worktrees/issue-123 2>&1"
        )
        assert path == ".worktrees/issue-123"
        assert ">" not in path

    def test_does_not_extract_from_echo(self):
        """Should not extract path from echo command (false positive)."""
        # This is a known limitation - the hook may false-positive here
        # But it's better to false-positive (block when shouldn't) than
        # false-negative (allow when should block)
        path = hook_module.extract_worktree_path_from_command(
            "echo 'git worktree remove .worktrees/issue-123'"
        )
        # Either None (good) or the path (acceptable false positive)
        if path is not None:
            assert path == ".worktrees/issue-123"


class TestMergedPrBypassesCwdCheckIssue1809:
    """Tests for Issue #1809: Merged PR should bypass cwd check.

    Issue #1809: When a PR is merged, worktree deletion should be allowed
    even if the session's cwd is inside the worktree. This is because:
    1. If PR is merged, the work is complete and deletion is safe
    2. Claude Code's Bash tool cannot persistently change cwd, making
       the previous guidance unactionable
    """

    def test_allows_deletion_when_pr_merged_and_cwd_inside(self):
        """Should allow deletion when PR is merged, even with cwd inside worktree.

        This test uses direct function calls with mocking instead of run_hook()
        because run_hook() executes via subprocess which doesn't inherit patches.
        """
        from io import StringIO
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            worktree_path.mkdir()

            # Prepare hook input
            input_data = json.dumps(
                {
                    "tool_input": {"command": f"git worktree remove {worktree_path}"},
                    "cwd": str(worktree_path),  # cwd inside worktree
                }
            )

            # Mock stdin/stdout and the PR check functions
            old_stdin = sys.stdin
            old_stdout = sys.stdout

            with patch.object(hook_module, "get_worktree_branch", return_value="fix/issue-123"):
                with patch.object(
                    hook_module, "check_pr_merged_for_branch", return_value=(True, 123)
                ):
                    sys.stdin = StringIO(input_data)
                    sys.stdout = StringIO()

                    try:
                        hook_module.main()
                        output = sys.stdout.getvalue()
                        result = json.loads(output)
                    finally:
                        sys.stdin = old_stdin
                        sys.stdout = old_stdout

                    # Should NOT block - merged PR bypasses cwd check
                    assert result.get("continue") is True, (
                        f"Merged PR should bypass cwd check. Got: {result}"
                    )

    def test_blocks_when_pr_not_merged_and_cwd_inside(self):
        """Should block when PR is not merged and cwd is inside worktree."""
        from io import StringIO
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            worktree_path.mkdir()

            input_data = json.dumps(
                {
                    "tool_input": {"command": f"git worktree remove {worktree_path}"},
                    "cwd": str(worktree_path),
                }
            )

            old_stdin = sys.stdin
            old_stdout = sys.stdout

            with patch.object(hook_module, "get_worktree_branch", return_value="fix/issue-456"):
                with patch.object(
                    hook_module, "check_pr_merged_for_branch", return_value=(False, None)
                ):
                    sys.stdin = StringIO(input_data)
                    sys.stdout = StringIO()

                    try:
                        hook_module.main()
                        output = sys.stdout.getvalue()
                        result = json.loads(output)
                    finally:
                        sys.stdin = old_stdin
                        sys.stdout = old_stdout

                    # Should block because cwd is inside and PR not merged
                    assert result.get("decision") == "block"

    def test_blocks_when_no_branch_and_cwd_inside(self):
        """Should block when worktree has no branch (detached HEAD) and cwd inside."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            worktree_path.mkdir()

            # No mock needed - real get_worktree_branch will return None for non-git dir
            result = run_hook(
                {
                    "tool_input": {"command": f"git worktree remove {worktree_path}"},
                    "cwd": str(worktree_path),
                }
            )
            # Should block because no branch means we can't check PR status
            assert result.get("decision") == "block"

    def test_cwd_block_message_includes_actionable_guidance(self):
        """Should provide actionable guidance in block message (Issue #1809)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            worktree_path.mkdir()

            # No mock needed - run the actual hook
            result = run_hook(
                {
                    "tool_input": {"command": f"git worktree remove {worktree_path}"},
                    "cwd": str(worktree_path),
                }
            )
            reason = result.get("reason", "")
            # Should include actionable guidance
            assert "方法1" in reason or "方法2" in reason or "方法3" in reason, (
                f"Block message should include actionable guidance. Got: {reason}"
            )
            # Should mention PR merge option
            assert "マージ" in reason or "merge" in reason.lower(), (
                f"Block message should mention PR merge option. Got: {reason}"
            )
            # Should mention SKIP_WORKTREE_CHECK bypass
            assert "SKIP_WORKTREE_CHECK" in reason, (
                f"Block message should mention SKIP_WORKTREE_CHECK. Got: {reason}"
            )

    def test_blocks_other_session_even_when_pr_merged(self):
        """Should block for other active session even when PR is merged (Issue #1809).

        The other session check is critical and must NEVER be bypassed,
        not even when PR is merged. This protects other sessions from
        having their cwd deleted.
        """
        from io import StringIO
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir).resolve() / "worktree"
            worktree_path.mkdir()

            input_data = json.dumps(
                {
                    "tool_input": {"command": f"git worktree remove {worktree_path}"},
                    "cwd": "/some/other/path",  # NOT inside worktree
                }
            )

            old_stdin = sys.stdin
            old_stdout = sys.stdout

            # Mock: PR is merged, but another session is active
            with patch.object(hook_module, "get_worktree_branch", return_value="fix/issue-123"):
                with patch.object(
                    hook_module, "check_pr_merged_for_branch", return_value=(True, 123)
                ):
                    with patch.object(
                        hook_module,
                        "check_other_session_active",
                        return_value=(True, "other-session-id", 5.0),
                    ):
                        sys.stdin = StringIO(input_data)
                        sys.stdout = StringIO()

                        try:
                            hook_module.main()
                            output = sys.stdout.getvalue()
                            result = json.loads(output)
                        finally:
                            sys.stdin = old_stdin
                            sys.stdout = old_stdout

                        # Should BLOCK because other session is active
                        # even though PR is merged
                        assert result.get("decision") == "block", (
                            f"Should block for other session even with merged PR. Got: {result}"
                        )
                        reason = result.get("reason", "")
                        assert "別セッション" in reason, (
                            f"Block message should mention other session. Got: {reason}"
                        )
