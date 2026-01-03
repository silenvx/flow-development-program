#!/usr/bin/env python3
"""Tests for session-end-worktree-cleanup.py hook."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add tests and hooks directory to path for imports
TESTS_DIR = Path(__file__).parent
HOOKS_DIR = TESTS_DIR.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from conftest import load_hook_module

# Load module under test (handles hyphenated filename)
hook = load_hook_module("session-end-worktree-cleanup")

HOOK_PATH = HOOKS_DIR / "session-end-worktree-cleanup.py"


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
        assert result["ok"] is True


class TestGetWorktreesInfo:
    """Tests for get_worktrees_info function."""

    def test_module_can_be_loaded(self):
        """Should be able to load the hook module without errors."""
        # Verify key functions exist
        assert hasattr(hook, "get_worktrees_info")
        assert hasattr(hook, "get_pr_state")
        assert hasattr(hook, "cleanup_worktree")
        assert hasattr(hook, "main")

    def test_returns_empty_list_when_git_fails(self):
        """Should return empty list when git command fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            result = hook.get_worktrees_info()
            assert result == []

    def test_parses_worktree_list_correctly(self):
        """Should parse git worktree list output correctly."""
        mock_output = """worktree /path/to/main
branch refs/heads/main

worktree /path/to/.worktrees/issue-123
branch refs/heads/fix/issue-123
locked

worktree /path/to/.worktrees/issue-456
branch refs/heads/feat/issue-456
"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = mock_output

        with patch("subprocess.run", return_value=mock_result):
            worktrees = hook.get_worktrees_info()
            assert len(worktrees) == 3
            # First is main
            assert worktrees[0]["is_main"] is True
            assert worktrees[0]["branch"] == "main"
            # Second is locked
            assert worktrees[1]["locked"] is True
            assert worktrees[1]["branch"] == "fix/issue-123"
            # Third is not locked
            assert worktrees[2]["locked"] is False
            assert worktrees[2]["branch"] == "feat/issue-456"

    def test_handles_locked_with_reason(self):
        """Should handle 'locked <reason>' format."""
        # "locked Session active" format (with reason)
        mock_output = """worktree /path/to/main
branch refs/heads/main

worktree /path/to/.worktrees/issue-123
branch refs/heads/fix/issue-123
locked Session active
"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = mock_output

        with patch("subprocess.run", return_value=mock_result):
            worktrees = hook.get_worktrees_info()
            assert len(worktrees) == 2
            # Second worktree should be marked as locked
            assert worktrees[1]["locked"] is True


class TestGetPrState:
    """Tests for get_pr_state function."""

    def test_returns_merged_state(self):
        """Should return MERGED when PR is merged."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "MERGED\n"

        with patch("subprocess.run", return_value=mock_result):
            state = hook.get_pr_state("fix/issue-123")
            assert state == "MERGED"

    def test_returns_none_when_gh_fails(self):
        """Should return None when gh command fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            state = hook.get_pr_state("fix/issue-123")
            assert state is None


class TestCleanupWorktree:
    """Tests for cleanup_worktree function."""

    def test_returns_success_on_normal_remove(self):
        """Should return success when normal remove works."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            success, msg = hook.cleanup_worktree("/path/to/.worktrees/issue-123")
            assert success is True
            assert "削除完了" in msg

    def test_returns_failure_on_error(self):
        """Should return failure when remove fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error message"

        with patch("subprocess.run", return_value=mock_result):
            success, msg = hook.cleanup_worktree("/path/to/.worktrees/issue-123")
            assert success is False
            assert "削除失敗" in msg


class TestMainFunction:
    """Tests for main function integration."""

    def test_module_main_function_exists(self):
        """Should have a main function that can be called."""
        # Verify main function exists and is callable
        assert hasattr(hook, "main")
        assert callable(hook.main)

    def test_stop_hook_active_returns_early(self):
        """Should return immediately when stop_hook_active is True.

        This test is safe because the hook exits before any git commands.
        """
        result = run_hook({"stop_hook_active": True})
        assert result["decision"] == "approve"
        assert result["ok"] is True


class TestUnlockCurrentWorktree:
    """Tests for unlock_current_worktree function (Issue #1315)."""

    def test_module_has_unlock_function(self):
        """Should have unlock_current_worktree function."""
        assert hasattr(hook, "unlock_current_worktree")
        assert callable(hook.unlock_current_worktree)

    def test_returns_none_when_cwd_is_none(self):
        """Should return None when cwd is None."""
        worktrees = [{"path": "/path/to/main", "is_main": True, "locked": False}]
        result = hook.unlock_current_worktree(None, worktrees)
        assert result is None

    def test_returns_none_when_cwd_in_main(self):
        """Should return None when cwd is in main repo."""
        cwd = Path("/path/to/main/subdir")
        worktrees = [{"path": "/path/to/main", "is_main": True, "locked": False}]
        result = hook.unlock_current_worktree(cwd, worktrees)
        assert result is None

    def test_returns_none_when_worktree_not_locked(self):
        """Should return None when worktree is not locked."""
        cwd = Path("/path/to/.worktrees/issue-123/subdir")
        worktrees = [
            {"path": "/path/to/main", "is_main": True, "locked": False},
            {"path": "/path/to/.worktrees/issue-123", "is_main": False, "locked": False},
        ]
        result = hook.unlock_current_worktree(cwd, worktrees)
        assert result is None

    def test_unlocks_when_worktree_is_locked(self):
        """Should unlock worktree when it is locked."""
        cwd = Path("/path/to/.worktrees/issue-123/subdir")
        worktrees = [
            {"path": "/path/to/main", "is_main": True, "locked": False},
            {"path": "/path/to/.worktrees/issue-123", "is_main": False, "locked": True},
        ]

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = hook.unlock_current_worktree(cwd, worktrees)
            assert result is not None
            assert "ロックを解除" in result
            assert "issue-123" in result

    def test_returns_error_message_on_nonzero_returncode(self):
        """Should return error message when git command fails with non-zero returncode."""
        cwd = Path("/path/to/.worktrees/issue-123/subdir")
        worktrees = [
            {"path": "/path/to/main", "is_main": True, "locked": False},
            {"path": "/path/to/.worktrees/issue-123", "is_main": False, "locked": True},
        ]

        mock_result = MagicMock()
        mock_result.returncode = 1  # Non-zero indicates failure

        with patch("subprocess.run", return_value=mock_result):
            result = hook.unlock_current_worktree(cwd, worktrees)
            assert result is not None
            assert "失敗" in result
            assert "issue-123" in result

    def test_returns_error_message_on_timeout(self):
        """Should return error message when unlock times out."""
        cwd = Path("/path/to/.worktrees/issue-123/subdir")
        worktrees = [
            {"path": "/path/to/main", "is_main": True, "locked": False},
            {"path": "/path/to/.worktrees/issue-123", "is_main": False, "locked": True},
        ]

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5)):
            result = hook.unlock_current_worktree(cwd, worktrees)
            assert result is not None
            assert "失敗" in result
            assert "issue-123" in result

    def test_returns_error_message_on_oserror(self):
        """Should return error message on OSError."""
        cwd = Path("/path/to/.worktrees/issue-123/subdir")
        worktrees = [
            {"path": "/path/to/main", "is_main": True, "locked": False},
            {"path": "/path/to/.worktrees/issue-123", "is_main": False, "locked": True},
        ]

        with patch("subprocess.run", side_effect=OSError("git not found")):
            result = hook.unlock_current_worktree(cwd, worktrees)
            assert result is not None
            assert "失敗" in result

    def test_returns_none_when_cwd_not_in_any_worktree(self):
        """Should return None when cwd is not inside any worktree."""
        cwd = Path("/some/other/path")
        worktrees = [
            {"path": "/path/to/main", "is_main": True, "locked": False},
            {"path": "/path/to/.worktrees/issue-123", "is_main": False, "locked": True},
        ]
        result = hook.unlock_current_worktree(cwd, worktrees)
        assert result is None
