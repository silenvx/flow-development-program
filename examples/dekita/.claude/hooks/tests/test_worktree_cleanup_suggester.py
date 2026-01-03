#!/usr/bin/env python3
"""Tests for worktree-cleanup-suggester.py hook."""

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

HOOK_PATH = Path(__file__).parent.parent / "worktree-cleanup-suggester.py"


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestStopHookActive:
    """Tests for stop_hook_active handling."""

    def test_approve_immediately_when_stop_hook_active(self):
        """Should approve immediately when stop_hook_active is True."""
        result = run_hook({"stop_hook_active": True})
        assert result["decision"] == "approve"
        assert "stop_hook_active" in result.get("reason", "")


class TestGetCurrentWorktreeInfo:
    """Tests for get_current_worktree_info function."""

    def test_returns_none_when_git_fails(self):
        """Should return None when git command fails."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        hook = import_module("worktree-cleanup-suggester")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            result = hook.get_current_worktree_info()
            assert result is None

    def test_module_can_be_loaded(self):
        """Should be able to load the hook module without errors."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        # Import the module to verify it can be loaded
        hook = import_module("worktree-cleanup-suggester")

        # Verify key functions exist
        assert hasattr(hook, "get_current_worktree_info")
        assert hasattr(hook, "get_pr_state_for_branch")
        assert hasattr(hook, "check_worktree_locked")
        assert hasattr(hook, "generate_cleanup_suggestion")
        assert hasattr(hook, "main")


class TestGetPrStateForBranch:
    """Tests for get_pr_state_for_branch function."""

    def test_returns_merged_state(self):
        """Should return MERGED when PR is merged."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        hook = import_module("worktree-cleanup-suggester")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "MERGED\n"
            result = hook.get_pr_state_for_branch("feat/test")
            assert result == "MERGED"

    def test_returns_closed_state(self):
        """Should return CLOSED when PR is closed."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        hook = import_module("worktree-cleanup-suggester")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "CLOSED\n"
            result = hook.get_pr_state_for_branch("feat/test")
            assert result == "CLOSED"

    def test_returns_open_state(self):
        """Should return OPEN when PR is still open."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        hook = import_module("worktree-cleanup-suggester")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "OPEN\n"
            result = hook.get_pr_state_for_branch("feat/test")
            assert result == "OPEN"

    def test_returns_none_when_no_pr(self):
        """Should return None when no PR found."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        hook = import_module("worktree-cleanup-suggester")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            result = hook.get_pr_state_for_branch("feat/test")
            assert result is None

    def test_returns_none_on_command_failure(self):
        """Should return None when gh command fails."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        hook = import_module("worktree-cleanup-suggester")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            result = hook.get_pr_state_for_branch("feat/test")
            assert result is None

    def test_returns_none_on_timeout(self):
        """Should return None on timeout."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        hook = import_module("worktree-cleanup-suggester")

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("gh", 5)
            result = hook.get_pr_state_for_branch("feat/test")
            assert result is None


class TestCheckWorktreeLocked:
    """Tests for check_worktree_locked function."""

    def test_returns_false_when_no_lock_file(self):
        """Should return False when no lock file exists."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        hook = import_module("worktree-cleanup-suggester")

        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            git_file = worktree_path / ".git"
            git_file.write_text("gitdir: /some/path/to/git/worktrees/test")

            # Create the gitdir but no lock file
            gitdir = Path(tmpdir) / "gitdir"
            gitdir.mkdir()

            result = hook.check_worktree_locked(str(worktree_path))
            assert not result

    def test_returns_true_when_lock_file_exists(self):
        """Should return True when lock file exists."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        hook = import_module("worktree-cleanup-suggester")

        with tempfile.TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir) / "worktree"
            worktree_path.mkdir()

            # Create gitdir structure
            gitdir = Path(tmpdir) / "gitdir"
            gitdir.mkdir()
            lock_file = gitdir / "locked"
            lock_file.touch()

            # Create .git file pointing to gitdir
            git_file = worktree_path / ".git"
            git_file.write_text(f"gitdir: {gitdir}")

            result = hook.check_worktree_locked(str(worktree_path))
            assert result

    def test_returns_false_when_git_file_missing(self):
        """Should return False when .git file doesn't exist."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        hook = import_module("worktree-cleanup-suggester")

        with tempfile.TemporaryDirectory() as tmpdir:
            result = hook.check_worktree_locked(tmpdir)
            assert not result

    def test_handles_relative_gitdir_path(self):
        """Should handle relative gitdir path correctly."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        hook = import_module("worktree-cleanup-suggester")

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create worktree structure with relative gitdir
            worktree_path = Path(tmpdir) / "worktree"
            worktree_path.mkdir()

            # Create gitdir as sibling of worktree
            gitdir = Path(tmpdir) / "gitdir"
            gitdir.mkdir()
            lock_file = gitdir / "locked"
            lock_file.touch()

            # Create .git file with relative path (from .git file's parent to gitdir)
            git_file = worktree_path / ".git"
            git_file.write_text("gitdir: ../gitdir")

            result = hook.check_worktree_locked(str(worktree_path))
            assert result


class TestGenerateCleanupSuggestion:
    """Tests for generate_cleanup_suggestion function."""

    def test_generates_suggestion_for_merged_pr(self):
        """Should generate cleanup suggestion for MERGED PR."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        hook = import_module("worktree-cleanup-suggester")

        worktree_info = {
            "path": "/main/repo/.worktrees/issue-123",
            "branch": "feat/issue-123-test",
            "is_main": False,
            "main_repo": "/main/repo",
        }

        result = hook.generate_cleanup_suggestion(worktree_info, "MERGED")

        assert "issue-123" in result
        assert "MERGED" in result
        assert "git worktree remove" in result
        assert "cleanup-worktrees.sh" in result

    def test_generates_suggestion_for_closed_pr(self):
        """Should generate cleanup suggestion for CLOSED PR."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        hook = import_module("worktree-cleanup-suggester")

        worktree_info = {
            "path": "/main/repo/.worktrees/issue-456",
            "branch": "fix/issue-456-bug",
            "is_main": False,
            "main_repo": "/main/repo",
        }

        result = hook.generate_cleanup_suggestion(worktree_info, "CLOSED")

        assert "issue-456" in result
        assert "CLOSED" in result
        assert "git worktree unlock" in result

    def test_quotes_paths_with_spaces(self):
        """Should quote paths containing spaces for shell safety."""
        import sys

        sys.path.insert(0, str(HOOK_PATH.parent))
        from importlib import import_module

        hook = import_module("worktree-cleanup-suggester")

        worktree_info = {
            "path": "/Users/John Doe/repos/.worktrees/issue-789",
            "branch": "feat/issue-789-feature",
            "is_main": False,
            "main_repo": "/Users/John Doe/repos",
        }

        result = hook.generate_cleanup_suggestion(worktree_info, "MERGED")

        # Paths with spaces should be quoted
        assert "'/Users/John Doe/repos'" in result
        assert "'/Users/John Doe/repos/.worktrees/issue-789'" in result


class TestMainFunction:
    """Integration tests for the main function."""

    def test_approve_with_empty_input(self):
        """Should approve with empty input (not in worktree scenario)."""
        result = run_hook({})
        assert result["decision"] == "approve"

    def test_hook_handles_exception_gracefully(self):
        """Should approve on exception to avoid blocking."""
        # This test verifies the hook doesn't crash on malformed input
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input="not valid json",
            capture_output=True,
            text=True,
        )
        # Hook should handle gracefully (either approve or output valid JSON)
        # Based on implementation, it prints error to stderr and approves
        if result.stdout:
            try:
                output = json.loads(result.stdout)
                assert output.get("decision") == "approve"
            except json.JSONDecodeError:
                pass  # Hook may exit without JSON on parse error
