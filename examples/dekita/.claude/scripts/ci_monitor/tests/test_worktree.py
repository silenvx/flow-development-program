"""Tests for ci_monitor.worktree module."""

import subprocess
from unittest.mock import MagicMock, patch

from ci_monitor.worktree import (
    _is_exact_worktree_match,
    cleanup_worktree_after_merge,
    get_worktree_info,
)


class TestIsExactWorktreeMatch:
    """Tests for _is_exact_worktree_match helper function."""

    def test_exact_match_with_prefix(self):
        """Test matching branch with prefix."""
        assert _is_exact_worktree_match("fix/issue-1366-cleanup", "issue-1366") is True

    def test_exact_match_at_start(self):
        """Test matching at start of branch name."""
        assert _is_exact_worktree_match("issue-1366-cleanup", "issue-1366") is True

    def test_exact_match_at_end(self):
        """Test matching at end of branch name."""
        assert _is_exact_worktree_match("fix/issue-1366", "issue-1366") is True

    def test_no_match_partial_number(self):
        """Test no match when issue number is part of larger number."""
        assert _is_exact_worktree_match("fix/issue-13669", "issue-1366") is False

    def test_no_match_partial_prefix(self):
        """Test no match when wt_name is only partial match."""
        assert _is_exact_worktree_match("feature-1234", "123") is False

    def test_no_match_different_issue(self):
        """Test no match for completely different issue."""
        assert _is_exact_worktree_match("fix/issue-999", "issue-1366") is False

    def test_match_with_hyphen_separator(self):
        """Test match with hyphen as separator."""
        assert _is_exact_worktree_match("issue-1366-phase2", "issue-1366") is True

    def test_match_with_slash_separator(self):
        """Test match with slash as separator."""
        assert _is_exact_worktree_match("feature/issue-1366/impl", "issue-1366") is True

    def test_match_with_underscore_separator(self):
        """Test match with underscore as separator."""
        assert _is_exact_worktree_match("fix_issue-1366_cleanup", "issue-1366") is True


class TestGetWorktreeInfo:
    """Tests for get_worktree_info function."""

    def test_returns_none_when_not_in_worktree(self):
        """Test returns (None, None) when not inside a worktree."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="worktree /main/repo\nHEAD abc123\nbranch refs/heads/main\n\n",
            )
            with patch("os.getcwd", return_value="/main/repo"):
                with patch("os.path.realpath", side_effect=lambda x: x):
                    main, wt = get_worktree_info()
                    assert main is None
                    assert wt is None

    def test_returns_paths_when_in_worktree(self):
        """Test returns correct paths when inside a worktree."""
        worktree_output = (
            "worktree /main/repo\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n\n"
            "worktree /main/repo/.worktrees/issue-123\n"
            "HEAD def456\n"
            "branch refs/heads/fix/issue-123\n\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=worktree_output,
            )
            with patch("os.getcwd", return_value="/main/repo/.worktrees/issue-123"):
                with patch("os.path.realpath", side_effect=lambda x: x):
                    main, wt = get_worktree_info()
                    assert main == "/main/repo"
                    assert wt == "/main/repo/.worktrees/issue-123"

    def test_returns_none_on_git_error(self):
        """Test returns (None, None) when git command fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            main, wt = get_worktree_info()
            assert main is None
            assert wt is None

    def test_returns_none_on_exception(self):
        """Test returns (None, None) when exception occurs."""
        with patch("subprocess.run", side_effect=OSError("Command not found")):
            main, wt = get_worktree_info()
            assert main is None
            assert wt is None

    def test_handles_subdirectory_of_worktree(self):
        """Test detects when cwd is a subdirectory of worktree."""
        worktree_output = (
            "worktree /main/repo\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n\n"
            "worktree /main/repo/.worktrees/issue-123\n"
            "HEAD def456\n"
            "branch refs/heads/fix/issue-123\n\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=worktree_output,
            )
            # cwd is a subdirectory of the worktree
            with patch("os.getcwd", return_value="/main/repo/.worktrees/issue-123/src/components"):
                with patch("os.path.realpath", side_effect=lambda x: x):
                    main, wt = get_worktree_info()
                    assert main == "/main/repo"
                    assert wt == "/main/repo/.worktrees/issue-123"


class TestCleanupWorktreeAfterMerge:
    """Tests for cleanup_worktree_after_merge function."""

    def test_successful_cleanup(self):
        """Test successful worktree removal."""
        with patch("subprocess.run") as mock_run:
            # Mock unlock (success)
            # Mock remove (success)
            # Mock branch list (empty)
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(returncode=0, stdout="", stderr=""),  # remove
                MagicMock(returncode=0, stdout=""),  # branch list
            ]
            with patch("os.getcwd", return_value="/main/repo"):
                with patch("os.path.realpath", side_effect=lambda x: x):
                    result = cleanup_worktree_after_merge(
                        "/main/repo/.worktrees/issue-123",
                        "/main/repo",
                    )
                    assert result is True

    def test_moves_to_main_repo_when_inside_worktree(self):
        """Test moves to main repo when cwd is inside worktree."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(returncode=0, stdout="", stderr=""),  # remove
                MagicMock(returncode=0, stdout=""),  # branch list
            ]
            with patch("os.getcwd", return_value="/main/repo/.worktrees/issue-123"):
                with patch("os.path.realpath", side_effect=lambda x: x):
                    with patch("os.chdir") as mock_chdir:
                        result = cleanup_worktree_after_merge(
                            "/main/repo/.worktrees/issue-123",
                            "/main/repo",
                        )
                        assert result is True
                        mock_chdir.assert_called_once_with("/main/repo")

    def test_force_remove_on_initial_failure(self):
        """Test falls back to force remove when normal remove fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(
                    returncode=1, stdout="", stderr="contains modified changes"
                ),  # remove fails
                MagicMock(returncode=0, stdout="", stderr=""),  # force remove succeeds
                MagicMock(returncode=0, stdout=""),  # branch list
            ]
            with patch("os.getcwd", return_value="/main/repo"):
                with patch("os.path.realpath", side_effect=lambda x: x):
                    result = cleanup_worktree_after_merge(
                        "/main/repo/.worktrees/issue-123",
                        "/main/repo",
                    )
                    assert result is True

    def test_returns_false_when_all_remove_attempts_fail(self):
        """Test returns False when both normal and force remove fail."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(returncode=1, stdout="", stderr="error"),  # remove fails
                MagicMock(returncode=1, stdout="", stderr="still error"),  # force remove fails
            ]
            with patch("os.getcwd", return_value="/main/repo"):
                with patch("os.path.realpath", side_effect=lambda x: x):
                    result = cleanup_worktree_after_merge(
                        "/main/repo/.worktrees/issue-123",
                        "/main/repo",
                    )
                    assert result is False

    def test_cleanup_branch_after_worktree_removal(self):
        """Test branch cleanup is attempted after worktree removal."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(returncode=0, stdout="", stderr=""),  # remove
                MagicMock(returncode=0, stdout="  main\n* fix/issue-123-feature\n"),  # branch list
                MagicMock(returncode=0, stdout="", stderr=""),  # branch delete
            ]
            with patch("os.getcwd", return_value="/main/repo"):
                with patch("os.path.realpath", side_effect=lambda x: x):
                    result = cleanup_worktree_after_merge(
                        "/main/repo/.worktrees/issue-123",
                        "/main/repo",
                    )
                    assert result is True
                    # Verify branch delete was called with matching branch
                    calls = mock_run.call_args_list
                    assert len(calls) == 4
                    assert calls[3][0][0] == ["git", "branch", "-d", "fix/issue-123-feature"]

    def test_log_fn_is_called(self):
        """Test log function is called during cleanup."""
        mock_log = MagicMock()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(returncode=0, stdout="", stderr=""),  # remove
                MagicMock(returncode=0, stdout=""),  # branch list
            ]
            with patch("os.getcwd", return_value="/main/repo"):
                with patch("os.path.realpath", side_effect=lambda x: x):
                    cleanup_worktree_after_merge(
                        "/main/repo/.worktrees/issue-123",
                        "/main/repo",
                        log_fn=mock_log,
                    )
                    # Verify log was called
                    assert mock_log.call_count >= 1

    def test_returns_false_on_timeout(self):
        """Test returns False when subprocess times out."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                subprocess.TimeoutExpired(cmd="git", timeout=30),  # remove times out
            ]
            with patch("os.getcwd", return_value="/main/repo"):
                with patch("os.path.realpath", side_effect=lambda x: x):
                    result = cleanup_worktree_after_merge(
                        "/main/repo/.worktrees/issue-123",
                        "/main/repo",
                    )
                    assert result is False

    def test_returns_false_on_exception(self):
        """Test returns False on unexpected exception."""
        with patch("subprocess.run", side_effect=OSError("Command not found")):
            with patch("os.getcwd", return_value="/main/repo"):
                with patch("os.path.realpath", side_effect=lambda x: x):
                    result = cleanup_worktree_after_merge(
                        "/main/repo/.worktrees/issue-123",
                        "/main/repo",
                    )
                    assert result is False

    def test_branch_cleanup_error_does_not_fail_overall(self):
        """Test branch cleanup error doesn't cause overall failure."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(returncode=0, stdout="", stderr=""),  # remove succeeds
                OSError("Branch list failed"),  # branch list fails
            ]
            with patch("os.getcwd", return_value="/main/repo"):
                with patch("os.path.realpath", side_effect=lambda x: x):
                    # Should still return True because worktree removal succeeded
                    result = cleanup_worktree_after_merge(
                        "/main/repo/.worktrees/issue-123",
                        "/main/repo",
                    )
                    assert result is True
