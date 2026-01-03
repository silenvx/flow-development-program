#!/usr/bin/env python3
"""Tests for session-end-main-check.py hook."""

import json
import os
import subprocess
import sys
import tempfile
from importlib import import_module
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent))
session_end_main_check = import_module("session-end-main-check")

HOOK_PATH = Path(__file__).parent.parent / "session-end-main-check.py"


def run_hook(input_data: dict, env: dict | None = None) -> dict:
    """Run the hook with given input and return the result."""
    test_env = os.environ.copy()
    if env:
        test_env.update(env)

    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        env=test_env,
    )
    return json.loads(result.stdout)


class TestSessionEndMainCheck:
    """Tests for session-end-main-check hook."""

    def test_approve_with_empty_input(self):
        """Should approve with empty input (no CLAUDE_PROJECT_DIR)."""
        result = run_hook({}, env={"CLAUDE_PROJECT_DIR": ""})
        # Issue #1607: Now uses print_continue_and_log_skip which returns {"continue": True}
        assert result.get("continue") is True or result.get("decision") == "approve"

    def test_approve_when_stop_hook_active(self):
        """Should approve immediately when stop_hook_active is True."""
        result = run_hook({"stop_hook_active": True})
        # Issue #1607: Now uses print_continue_and_log_skip which returns {"continue": True}
        assert result.get("continue") is True or result.get("decision") == "approve"

    def test_approve_when_no_project_dir(self):
        """Should approve when CLAUDE_PROJECT_DIR is not set."""
        result = run_hook({}, env={"CLAUDE_PROJECT_DIR": ""})
        # Issue #1607: Now uses print_continue_and_log_skip which returns {"continue": True}
        assert result.get("continue") is True or result.get("decision") == "approve"


class TestGetRepoRoot:
    """Tests for get_repo_root function."""

    def test_no_project_dir(self):
        """Should return None when CLAUDE_PROJECT_DIR is empty and no git repo detected."""
        # Mock subprocess.run to simulate no git repo
        mock_result = MagicMock()
        mock_result.returncode = 1  # git rev-parse fails
        mock_result.stdout = ""

        with (
            patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}, clear=False),
            patch("lib.repo.subprocess.run", return_value=mock_result),
            patch("lib.repo.Path.cwd", return_value=Path("/nonexistent/path")),
        ):
            result = session_end_main_check.get_repo_root()
            assert result is None

    def test_nonexistent_path(self):
        """Should return None when path does not exist."""
        with patch.dict(
            os.environ,
            {"CLAUDE_PROJECT_DIR": "/nonexistent/path"},
            clear=False,
        ):
            result = session_end_main_check.get_repo_root()
            assert result is None

    def test_regular_git_repo(self):
        """Should return project path for regular git repo."""
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = Path(tmpdir) / ".git"
            git_dir.mkdir()

            with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": tmpdir}, clear=False):
                result = session_end_main_check.get_repo_root()
                assert result == Path(tmpdir)

    def test_worktree_case(self):
        """Should return main repo path for worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create main repo structure
            main_repo = Path(tmpdir) / "main-repo"
            main_repo.mkdir()
            main_git = main_repo / ".git"
            main_git.mkdir()

            # Create worktrees structure
            worktrees_dir = main_git / "worktrees"
            worktrees_dir.mkdir()
            worktree_git = worktrees_dir / "my-worktree"
            worktree_git.mkdir()

            # Create worktree directory
            worktree_dir = Path(tmpdir) / "worktree"
            worktree_dir.mkdir()

            # Create .git file pointing to worktree git dir
            git_file = worktree_dir / ".git"
            git_file.write_text(f"gitdir: {worktree_git}")

            with patch.dict(
                os.environ,
                {"CLAUDE_PROJECT_DIR": str(worktree_dir)},
                clear=False,
            ):
                result = session_end_main_check.get_repo_root()
                assert result == main_repo


class TestIsMainBehind:
    """Tests for is_main_behind function."""

    @patch("subprocess.run")
    def test_main_is_behind(self, mock_run: MagicMock):
        """Should return True when main is behind origin/main."""
        # First call: git fetch succeeds
        fetch_result = MagicMock()
        fetch_result.returncode = 0

        # Second call: git rev-list returns 3 commits behind
        revlist_result = MagicMock()
        revlist_result.returncode = 0
        revlist_result.stdout = "3\n"

        mock_run.side_effect = [fetch_result, revlist_result]

        is_behind, count, error = session_end_main_check.is_main_behind(Path("/repo"))

        assert is_behind
        assert count == 3
        assert error is None

    @patch("subprocess.run")
    def test_main_is_up_to_date(self, mock_run: MagicMock):
        """Should return False when main is up-to-date."""
        fetch_result = MagicMock()
        fetch_result.returncode = 0

        revlist_result = MagicMock()
        revlist_result.returncode = 0
        revlist_result.stdout = "0\n"

        mock_run.side_effect = [fetch_result, revlist_result]

        is_behind, count, error = session_end_main_check.is_main_behind(Path("/repo"))

        assert not is_behind
        assert count == 0
        assert error is None

    @patch("subprocess.run")
    def test_git_fetch_fails(self, mock_run: MagicMock):
        """Should return error when git fetch fails."""
        fetch_result = MagicMock()
        fetch_result.returncode = 1
        fetch_result.stderr = "fatal: not a git repository"

        mock_run.return_value = fetch_result

        is_behind, count, error = session_end_main_check.is_main_behind(Path("/repo"))

        assert not is_behind
        assert count == 0
        assert "git fetch failed" in error

    @patch("subprocess.run")
    def test_git_revlist_fails(self, mock_run: MagicMock):
        """Should return error when git rev-list fails."""
        fetch_result = MagicMock()
        fetch_result.returncode = 0

        revlist_result = MagicMock()
        revlist_result.returncode = 1
        revlist_result.stderr = "fatal: ambiguous argument"

        mock_run.side_effect = [fetch_result, revlist_result]

        is_behind, count, error = session_end_main_check.is_main_behind(Path("/repo"))

        assert not is_behind
        assert count == 0
        assert "git rev-list failed" in error

    @patch("subprocess.run")
    def test_git_timeout(self, mock_run: MagicMock):
        """Should return error when git command times out."""
        mock_run.side_effect = subprocess.TimeoutExpired("git", 30)

        is_behind, count, error = session_end_main_check.is_main_behind(Path("/repo"))

        assert not is_behind
        assert count == 0
        assert error == "git command timed out"

    @patch("subprocess.run")
    def test_git_not_found(self, mock_run: MagicMock):
        """Should return error when git is not found."""
        mock_run.side_effect = FileNotFoundError()

        is_behind, count, error = session_end_main_check.is_main_behind(Path("/repo"))

        assert not is_behind
        assert count == 0
        assert error == "git command not found"

    @patch("subprocess.run")
    def test_parse_error(self, mock_run: MagicMock):
        """Should return error when count cannot be parsed."""
        fetch_result = MagicMock()
        fetch_result.returncode = 0

        revlist_result = MagicMock()
        revlist_result.returncode = 0
        revlist_result.stdout = "not a number\n"

        mock_run.side_effect = [fetch_result, revlist_result]

        is_behind, count, error = session_end_main_check.is_main_behind(Path("/repo"))

        assert not is_behind
        assert count == 0
        assert "parse error" in error


class TestGetCurrentBranch:
    """Tests for get_current_branch function."""

    @patch("subprocess.run")
    def test_returns_branch_name(self, mock_run: MagicMock):
        """Should return current branch name."""
        result = MagicMock()
        result.returncode = 0
        result.stdout = "feat/my-feature\n"
        mock_run.return_value = result

        branch = session_end_main_check.get_current_branch(Path("/repo"))
        assert branch == "feat/my-feature"

    @patch("subprocess.run")
    def test_returns_none_on_error(self, mock_run: MagicMock):
        """Should return None when git command fails."""
        result = MagicMock()
        result.returncode = 1
        mock_run.return_value = result

        branch = session_end_main_check.get_current_branch(Path("/repo"))
        assert branch is None

    @patch("subprocess.run")
    def test_returns_none_on_timeout(self, mock_run: MagicMock):
        """Should return None when git command times out."""
        mock_run.side_effect = subprocess.TimeoutExpired("git", 30)

        branch = session_end_main_check.get_current_branch(Path("/repo"))
        assert branch is None
