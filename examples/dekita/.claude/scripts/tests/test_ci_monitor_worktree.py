#!/usr/bin/env python3
"""Unit tests for ci_monitor.worktree module."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add scripts directory to path so we can import ci_monitor package
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the ci_monitor package for attribute access (Issue #2624)
import ci_monitor

# Import directly from ci_monitor package (Issue #2624)


class TestHasLocalChanges:
    """Tests for has_local_changes function (Issue #865)."""

    @patch("ci_monitor.pr_operations.subprocess.run")
    def test_no_changes(self, mock_run):
        """Test that no changes are detected when git commands return empty."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        has_changes, description = ci_monitor.has_local_changes()
        assert not has_changes
        assert description == ""

    @patch("ci_monitor.pr_operations.subprocess.run")
    def test_uncommitted_changes(self, mock_run):
        """Test detection of uncommitted changes."""

        def run_side_effect(cmd, *args, **kwargs):
            if "status" in cmd:
                return MagicMock(returncode=0, stdout=" M file.txt\n")
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = run_side_effect
        has_changes, description = ci_monitor.has_local_changes()
        assert has_changes
        assert "uncommitted changes" in description

    @patch("ci_monitor.pr_operations.subprocess.run")
    def test_unpushed_commits(self, mock_run):
        """Test detection of unpushed commits."""

        def run_side_effect(cmd, *args, **kwargs):
            if "status" in cmd:
                return MagicMock(returncode=0, stdout="")
            if "log" in cmd:
                return MagicMock(
                    returncode=0, stdout="abc1234 commit message\ndef5678 another commit\n"
                )
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = run_side_effect
        has_changes, description = ci_monitor.has_local_changes()
        assert has_changes
        assert "2 unpushed commit(s)" in description

    @patch("ci_monitor.pr_operations.subprocess.run")
    def test_both_uncommitted_and_unpushed(self, mock_run):
        """Test detection of both uncommitted and unpushed changes."""

        def run_side_effect(cmd, *args, **kwargs):
            if "status" in cmd:
                return MagicMock(returncode=0, stdout=" M file.txt\n")
            if "log" in cmd:
                return MagicMock(returncode=0, stdout="abc1234 commit message\n")
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = run_side_effect
        has_changes, description = ci_monitor.has_local_changes()
        assert has_changes
        assert "uncommitted changes" in description
        assert "1 unpushed commit(s)" in description

    @patch("ci_monitor.pr_operations.subprocess.run")
    def test_git_command_failure(self, mock_run):
        """Test graceful handling of git command failures."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=10)
        has_changes, description = ci_monitor.has_local_changes()
        # Should not raise, and should report no changes (fail-safe)
        assert not has_changes


class TestCleanupWorktreeAfterMerge:
    """Tests for cleanup_worktree_after_merge function (Issue #1457)."""

    def test_successful_cleanup(self):
        """Test successful worktree cleanup."""
        with (
            patch("ci_monitor.os.getcwd", return_value="/main/repo"),
            patch("ci_monitor.os.path.realpath", side_effect=lambda x: x),
            patch("ci_monitor.subprocess.run") as mock_run,
            patch("ci_monitor.log") as mock_log,
        ):
            # Mock unlock (ignore return value)
            # Mock worktree remove success
            # Mock branch list (empty)
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(returncode=0, stdout="", stderr=""),  # worktree remove
                MagicMock(returncode=0, stdout="", stderr=""),  # branch --list
            ]

            result = ci_monitor.cleanup_worktree_after_merge("/path/to/worktree", "/main/repo")

            assert result is True
            assert mock_log.call_count >= 1
            # Verify success message was logged
            success_calls = [
                call for call in mock_log.call_args_list if "削除しました" in str(call)
            ]
            assert len(success_calls) >= 1

    def test_moves_cwd_when_inside_worktree(self):
        """Test that cwd is moved when inside worktree."""
        with (
            patch("ci_monitor.os.getcwd", return_value="/path/to/worktree/subdir"),
            patch(
                "ci_monitor.os.path.realpath",
                side_effect=lambda x: x.rstrip("/"),
            ),
            patch("ci_monitor.os.chdir") as mock_chdir,
            patch("ci_monitor.subprocess.run") as mock_run,
            patch("ci_monitor.log") as mock_log,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(returncode=0, stdout="", stderr=""),  # worktree remove
                MagicMock(returncode=0, stdout="", stderr=""),  # branch --list
            ]

            result = ci_monitor.cleanup_worktree_after_merge("/path/to/worktree", "/main/repo")

            assert result is True
            mock_chdir.assert_called_once_with("/main/repo")
            # Verify move message was logged
            move_calls = [
                call for call in mock_log.call_args_list if "Moved to main repo" in str(call)
            ]
            assert len(move_calls) == 1

    def test_force_removal_fallback(self):
        """Test fallback to force removal when normal removal fails."""
        with (
            patch("ci_monitor.os.getcwd", return_value="/main/repo"),
            patch("ci_monitor.os.path.realpath", side_effect=lambda x: x),
            patch("ci_monitor.subprocess.run") as mock_run,
            patch("ci_monitor.log") as mock_log,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(
                    returncode=1, stdout="", stderr="contains modifications"
                ),  # normal remove fails
                MagicMock(returncode=0, stdout="", stderr=""),  # force remove succeeds
                MagicMock(returncode=0, stdout="", stderr=""),  # branch --list
            ]

            result = ci_monitor.cleanup_worktree_after_merge("/path/to/worktree", "/main/repo")

            assert result is True
            # Verify force removal was attempted
            force_calls = [
                call
                for call in mock_run.call_args_list
                if "-f" in str(call) and "worktree" in str(call)
            ]
            assert len(force_calls) == 1
            # Verify warning about fallback
            warning_calls = [
                call for call in mock_log.call_args_list if "強制削除を試行" in str(call)
            ]
            assert len(warning_calls) == 1

    def test_force_removal_also_fails(self):
        """Test when both normal and force removal fail."""
        with (
            patch("ci_monitor.os.getcwd", return_value="/main/repo"),
            patch("ci_monitor.os.path.realpath", side_effect=lambda x: x),
            patch("ci_monitor.subprocess.run") as mock_run,
            patch("ci_monitor.log") as mock_log,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(
                    returncode=1, stdout="", stderr="contains modifications"
                ),  # normal remove fails
                MagicMock(
                    returncode=1, stdout="", stderr="still in use"
                ),  # force remove also fails
            ]

            result = ci_monitor.cleanup_worktree_after_merge("/path/to/worktree", "/main/repo")

            assert result is False
            # Verify failure messages were logged
            # 1. "通常削除失敗、強制削除を試行" when normal removal fails
            # 2. "Worktree削除失敗" when force removal also fails
            fallback_calls = [
                call for call in mock_log.call_args_list if "強制削除を試行" in str(call)
            ]
            final_failure_calls = [
                call for call in mock_log.call_args_list if "❌ Worktree削除失敗" in str(call)
            ]
            assert len(fallback_calls) == 1
            assert len(final_failure_calls) == 1

    def test_timeout_handling(self):
        """Test timeout during worktree removal."""
        with (
            patch("ci_monitor.os.getcwd", return_value="/main/repo"),
            patch("ci_monitor.os.path.realpath", side_effect=lambda x: x),
            patch("ci_monitor.subprocess.run") as mock_run,
            patch("ci_monitor.log") as mock_log,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                subprocess.TimeoutExpired(cmd="git", timeout=30),  # timeout
            ]

            result = ci_monitor.cleanup_worktree_after_merge("/path/to/worktree", "/main/repo")

            assert result is False
            # Verify timeout message was logged
            timeout_calls = [
                call for call in mock_log.call_args_list if "タイムアウト" in str(call)
            ]
            assert len(timeout_calls) == 1

    def test_general_exception_handling(self):
        """Test handling of general exceptions."""
        with (
            patch("ci_monitor.os.getcwd", side_effect=OSError("Permission denied")),
            patch("ci_monitor.log") as mock_log,
        ):
            result = ci_monitor.cleanup_worktree_after_merge("/path/to/worktree", "/main/repo")

            assert result is False
            # Verify error message was logged
            error_calls = [call for call in mock_log.call_args_list if "エラー" in str(call)]
            assert len(error_calls) == 1

    def test_branch_cleanup_success(self):
        """Test successful branch cleanup after worktree removal."""
        with (
            patch("ci_monitor.os.getcwd", return_value="/main/repo"),
            patch("ci_monitor.os.path.realpath", side_effect=lambda x: x),
            patch("ci_monitor.subprocess.run") as mock_run,
            patch("ci_monitor.log") as mock_log,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(returncode=0, stdout="", stderr=""),  # worktree remove
                MagicMock(
                    returncode=0,
                    stdout="  feat/issue-1366-cleanup\n* main\n  other-branch",
                    stderr="",
                ),  # branch --list
                MagicMock(returncode=0, stdout="", stderr=""),  # branch -d
            ]

            result = ci_monitor.cleanup_worktree_after_merge(
                "/path/to/.worktrees/issue-1366", "/main/repo"
            )

            assert result is True
            # Verify branch deletion was logged
            branch_calls = [
                call
                for call in mock_log.call_args_list
                if "ブランチ" in str(call) and "削除" in str(call)
            ]
            assert len(branch_calls) == 1

    def test_branch_cleanup_no_matching_branch(self):
        """Test branch cleanup when no matching branch exists."""
        with (
            patch("ci_monitor.os.getcwd", return_value="/main/repo"),
            patch("ci_monitor.os.path.realpath", side_effect=lambda x: x),
            patch("ci_monitor.subprocess.run") as mock_run,
            patch("ci_monitor.log") as mock_log,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(returncode=0, stdout="", stderr=""),  # worktree remove
                MagicMock(
                    returncode=0, stdout="* main\n  other-branch", stderr=""
                ),  # branch --list - no matching branch
            ]

            result = ci_monitor.cleanup_worktree_after_merge(
                "/path/to/.worktrees/issue-1366", "/main/repo"
            )

            assert result is True
            # Verify no branch deletion message (only worktree success)
            branch_delete_calls = [
                call
                for call in mock_log.call_args_list
                if "ブランチ" in str(call) and "削除しました" in str(call)
            ]
            assert len(branch_delete_calls) == 0

    def test_json_mode_output(self):
        """Test json_mode=True is passed to log() function."""
        with (
            patch("ci_monitor.os.getcwd", return_value="/main/repo"),
            patch("ci_monitor.os.path.realpath", side_effect=lambda x: x),
            patch("ci_monitor.subprocess.run") as mock_run,
            patch("ci_monitor.log") as mock_log,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(returncode=0, stdout="", stderr=""),  # worktree remove
                MagicMock(returncode=0, stdout="", stderr=""),  # branch --list
            ]

            result = ci_monitor.cleanup_worktree_after_merge(
                "/path/to/worktree", "/main/repo", json_mode=True
            )

            assert result is True
            # Verify log() was called with json_mode=True
            json_mode_calls = [
                call for call in mock_log.call_args_list if call.kwargs.get("json_mode") is True
            ]
            assert len(json_mode_calls) >= 1, "log() should be called with json_mode=True"

    def test_exact_worktree_match_prevents_partial_match(self):
        """Test that branch matching is exact (issue-1366 doesn't match issue-13669)."""
        with (
            patch("ci_monitor.os.getcwd", return_value="/main/repo"),
            patch("ci_monitor.os.path.realpath", side_effect=lambda x: x),
            patch("ci_monitor.subprocess.run") as mock_run,
            patch("ci_monitor.log") as mock_log,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # unlock
                MagicMock(returncode=0, stdout="", stderr=""),  # worktree remove
                MagicMock(
                    returncode=0,
                    stdout="  feat/issue-13669-other\n* main",  # Similar but NOT matching
                    stderr="",
                ),  # branch --list
                # No branch -d call expected
            ]

            result = ci_monitor.cleanup_worktree_after_merge(
                "/path/to/.worktrees/issue-1366", "/main/repo"
            )

            assert result is True
            # Verify NO branch deletion (issue-13669 should NOT match issue-1366)
            branch_delete_calls = [
                call
                for call in mock_log.call_args_list
                if "ブランチ" in str(call) and "削除しました" in str(call)
            ]
            assert len(branch_delete_calls) == 0
