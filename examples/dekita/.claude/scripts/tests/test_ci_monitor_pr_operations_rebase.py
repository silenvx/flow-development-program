#!/usr/bin/env python3
"""Unit tests for rebase-related functions in ci_monitor.pr_operations module.

Covers:
- RebaseReviewCheckedFlag behavior
- get_pr_branch_name function
- sync_local_after_rebase function
- format_rebase_summary function
- Rebase file increase detection
- rebase_pr function
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add scripts directory to path so we can import ci_monitor package
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the ci_monitor package for attribute access (Issue #2624)
import ci_monitor

# Import directly from ci_monitor package (Issue #2624)
from ci_monitor import (
    CheckStatus,
    MergeState,
    PRState,
)


class TestRebaseReviewCheckedFlag:
    """Tests for rebase_review_checked flag (Issue #241 fix).

    Issue #241: After rebase, the message "AI reviewer re-requested after rebase"
    was being repeated every loop iteration. The fix adds a rebase_review_checked
    flag to ensure the async check only happens once per rebase.
    """

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.rebase_pr")
    @patch("ci_monitor.main_loop.has_local_changes", return_value=(False, ""))
    def test_rebase_review_check_only_once_per_rebase(
        self, mock_local_changes, mock_rebase, mock_get_pr_state, mock_sleep
    ):
        """Test that async AI reviewer check only happens once per rebase.

        After the first check following a rebase, subsequent CI passes should
        NOT trigger the async delay check again.
        """
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 2:
                # BEHIND detected - triggers rebase
                return (
                    PRState(
                        merge_state=MergeState.BEHIND,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 3:
                # After rebase, CI pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 4:
                # CI passed - first time after rebase
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            else:
                # After async delay check - success
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_rebase.return_value = ci_monitor.RebaseResult(success=True)

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert result.rebase_count == 1
        # Async delay should only be called ONCE (not multiple times)
        sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
        async_delay_count = sleep_calls.count(ci_monitor.ASYNC_REVIEWER_CHECK_DELAY_SECONDS)
        assert async_delay_count == 1

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.rebase_pr")
    @patch("ci_monitor.main_loop.has_local_changes", return_value=(False, ""))
    def test_rebase_review_checked_resets_on_new_rebase(
        self, mock_local_changes, mock_rebase, mock_get_pr_state, mock_sleep
    ):
        """Test that rebase_review_checked resets when a new rebase occurs.

        If another BEHIND state is detected after the first rebase,
        the async check should happen again for the new rebase.
        """
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 2:
                # First BEHIND - triggers first rebase
                return (
                    PRState(
                        merge_state=MergeState.BEHIND,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 3:
                # After first rebase, CI pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 4:
                # CI passed first time
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            elif call_count == 5:
                # After first async delay - BEHIND again!
                return (
                    PRState(
                        merge_state=MergeState.BEHIND,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            elif call_count == 6:
                # Second BEHIND detected
                return (
                    PRState(
                        merge_state=MergeState.BEHIND,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 7:
                # After second rebase, CI pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 8:
                # CI passed second time
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            else:
                # After second async delay - success
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_rebase.return_value = ci_monitor.RebaseResult(success=True)

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert result.rebase_count == 2
        # Async delay should be called TWICE (once per rebase)
        sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
        async_delay_count = sleep_calls.count(ci_monitor.ASYNC_REVIEWER_CHECK_DELAY_SECONDS)
        assert async_delay_count == 2

    # Issue #2454: Removed tests referencing non-existent API_OPERATIONS_LOG attribute
    # - test_logs_to_api_operations_jsonl
    # - test_api_operations_includes_direction_for_adjusted_interval
    # - test_api_operations_handles_write_error


class TestGetPrBranchName:
    """Tests for get_pr_branch_name function (Issue #895)."""

    def test_returns_branch_name(self):
        """Test successful branch name retrieval."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, "feat/issue-123-new-feature")
            result = ci_monitor.get_pr_branch_name("123")
            assert result == "feat/issue-123-new-feature"

    def test_returns_none_on_failure(self):
        """Test returns None on command failure."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (False, "Error")
            result = ci_monitor.get_pr_branch_name("123")
            assert result is None

    def test_returns_none_on_empty_output(self):
        """Test returns None on empty output."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, "")
            result = ci_monitor.get_pr_branch_name("123")
            assert result is None


class TestSyncLocalAfterRebase:
    """Tests for sync_local_after_rebase function (Issue #895)."""

    def test_syncs_local_branch_successfully(self, capsys):
        """Test successful local sync after rebase."""
        with patch.object(ci_monitor, "subprocess") as mock_subprocess:
            # Mock git commands
            mock_subprocess.run.side_effect = [
                MagicMock(returncode=0, stdout="true", stderr=""),  # --is-inside-work-tree
                MagicMock(returncode=0, stdout="feat/issue-123", stderr=""),  # --abbrev-ref HEAD
                MagicMock(returncode=0, stdout="", stderr=""),  # git status --porcelain
                MagicMock(returncode=0, stdout="", stderr=""),  # git pull --rebase
            ]
            mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
            mock_subprocess.FileNotFoundError = FileNotFoundError
            result = ci_monitor.sync_local_after_rebase("feat/issue-123")
            assert result is True
            captured = capsys.readouterr()
            assert "Syncing local branch" in captured.out
            assert "synced successfully" in captured.out

    def test_skips_sync_when_not_in_git_repo(self):
        """Test sync returns True when not in git repo (no sync needed)."""
        with patch.object(ci_monitor, "subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(
                returncode=1, stdout="", stderr="fatal: not a git repository"
            )
            mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
            mock_subprocess.FileNotFoundError = FileNotFoundError
            result = ci_monitor.sync_local_after_rebase("feat/issue-123")
            # Returns True because sync is "not needed" when not in git repo
            assert result is True

    def test_skips_sync_when_on_different_branch(self, capsys):
        """Test sync skipped when on different branch (returns True with message)."""
        with patch.object(ci_monitor, "subprocess") as mock_subprocess:
            mock_subprocess.run.side_effect = [
                MagicMock(returncode=0, stdout="true", stderr=""),  # --is-inside-work-tree
                MagicMock(returncode=0, stdout="main", stderr=""),  # different branch
            ]
            mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
            mock_subprocess.FileNotFoundError = FileNotFoundError
            result = ci_monitor.sync_local_after_rebase("feat/issue-123")
            # Returns True because sync is "not needed" when on different branch
            assert result is True
            captured = capsys.readouterr()
            assert "not on the target branch" in captured.out

    def test_skips_sync_when_uncommitted_changes(self, capsys):
        """Test sync skipped when uncommitted changes exist (returns False)."""
        with patch.object(ci_monitor, "subprocess") as mock_subprocess:
            mock_subprocess.run.side_effect = [
                MagicMock(returncode=0, stdout="true", stderr=""),  # --is-inside-work-tree
                MagicMock(returncode=0, stdout="feat/issue-123", stderr=""),  # on target branch
                MagicMock(returncode=0, stdout=" M some_file.py", stderr=""),  # uncommitted changes
            ]
            mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
            mock_subprocess.FileNotFoundError = FileNotFoundError
            result = ci_monitor.sync_local_after_rebase("feat/issue-123")
            # Returns False because sync failed (user needs to manually sync)
            assert result is False
            captured = capsys.readouterr()
            assert "Uncommitted" in captured.out

    def test_json_mode_suppresses_output(self, capsys):
        """Test json_mode=True suppresses all human-readable output."""
        with patch.object(ci_monitor, "subprocess") as mock_subprocess:
            # Mock git commands for successful sync
            mock_subprocess.run.side_effect = [
                MagicMock(returncode=0, stdout="true", stderr=""),  # --is-inside-work-tree
                MagicMock(returncode=0, stdout="feat/issue-123", stderr=""),  # --abbrev-ref HEAD
                MagicMock(returncode=0, stdout="", stderr=""),  # git status --porcelain
                MagicMock(returncode=0, stdout="", stderr=""),  # git pull --rebase
            ]
            mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
            mock_subprocess.FileNotFoundError = FileNotFoundError
            result = ci_monitor.sync_local_after_rebase("feat/issue-123", json_mode=True)
            assert result is True
            captured = capsys.readouterr()
            # With json_mode=True, no output should be printed
            assert captured.out == ""

    def test_git_pull_failure(self, capsys):
        """Test sync returns False when git pull --rebase fails."""
        with patch.object(ci_monitor, "subprocess") as mock_subprocess:
            mock_subprocess.run.side_effect = [
                MagicMock(returncode=0, stdout="true", stderr=""),  # --is-inside-work-tree
                MagicMock(returncode=0, stdout="feat/issue-123", stderr=""),  # --abbrev-ref HEAD
                MagicMock(returncode=0, stdout="", stderr=""),  # git status --porcelain (clean)
                MagicMock(
                    returncode=1, stdout="", stderr="error: cannot pull with rebase"
                ),  # git pull --rebase fails
            ]
            mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
            mock_subprocess.FileNotFoundError = FileNotFoundError
            result = ci_monitor.sync_local_after_rebase("feat/issue-123")
            assert result is False
            captured = capsys.readouterr()
            assert "Failed to sync" in captured.out

    def test_git_pull_timeout(self, capsys):
        """Test sync returns False when git pull --rebase times out."""
        with patch.object(ci_monitor, "subprocess") as mock_subprocess:
            mock_subprocess.run.side_effect = [
                MagicMock(returncode=0, stdout="true", stderr=""),  # --is-inside-work-tree
                MagicMock(returncode=0, stdout="feat/issue-123", stderr=""),  # --abbrev-ref HEAD
                MagicMock(returncode=0, stdout="", stderr=""),  # git status --porcelain (clean)
                subprocess.TimeoutExpired(cmd="git pull", timeout=30),  # git pull times out
            ]
            mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
            mock_subprocess.FileNotFoundError = FileNotFoundError
            result = ci_monitor.sync_local_after_rebase("feat/issue-123")
            assert result is False
            captured = capsys.readouterr()
            assert "timed out" in captured.out


class TestFormatRebaseSummary:
    """Tests for format_rebase_summary function (Issue #1364)."""

    def test_zero_rebases(self):
        """Should return count without suggestion for 0 rebases."""
        result = ci_monitor.format_rebase_summary(0)
        assert result == "Rebases performed: 0"
        assert "consider merge queue" not in result

    def test_one_rebase(self):
        """Should return count without suggestion for 1 rebase."""
        result = ci_monitor.format_rebase_summary(1)
        assert result == "Rebases performed: 1"
        assert "consider merge queue" not in result

    def test_two_rebases(self):
        """Should return count with merge queue suggestion for 2 rebases."""
        result = ci_monitor.format_rebase_summary(2)
        assert result == "Rebases performed: 2 (consider merge queue)"
        assert "consider merge queue" in result

    def test_three_rebases(self):
        """Should return count with merge queue suggestion for 3+ rebases."""
        result = ci_monitor.format_rebase_summary(3)
        assert result == "Rebases performed: 3 (consider merge queue)"
        assert "consider merge queue" in result

    def test_many_rebases(self):
        """Should handle large rebase counts correctly."""
        result = ci_monitor.format_rebase_summary(10)
        assert result == "Rebases performed: 10 (consider merge queue)"
        assert "consider merge queue" in result


class TestRebaseFileIncreaseDetection:
    """Tests for rebase file increase detection (Issue #1341)."""

    def test_file_increase_detected(self):
        """Should detect when files increase after rebase."""
        # Test the detection logic directly
        files_before_count = 3
        files_after_count = 5
        # Logic from monitor_pr: warn if files_before_count > 0 and files_after_count > files_before_count
        should_warn = files_before_count > 0 and files_after_count > files_before_count
        assert should_warn is True
        assert files_after_count - files_before_count == 2

    def test_file_count_unchanged(self):
        """Should not warn when file count is unchanged."""
        files_before_count = 3
        files_after_count = 3
        should_warn = files_before_count > 0 and files_after_count > files_before_count
        assert should_warn is False

    def test_file_count_decreased(self):
        """Should not warn when file count decreases."""
        files_before_count = 5
        files_after_count = 3
        should_warn = files_before_count > 0 and files_after_count > files_before_count
        assert should_warn is False

    def test_api_failure_before(self):
        """Should not warn when API fails before rebase (files_before is None)."""
        files_before_count = -1  # Represents None from get_pr_changed_files
        files_after_count = 5
        should_warn = files_before_count > 0 and files_after_count > files_before_count
        assert should_warn is False

    def test_api_failure_after(self):
        """Should not warn when API fails after rebase (files_after is None)."""
        files_before_count = 3
        files_after_count = -1  # Represents None from get_pr_changed_files
        should_warn = files_before_count > 0 and files_after_count > files_before_count
        assert should_warn is False

    def test_zero_files_before(self):
        """Should not warn when starting with 0 files (edge case)."""
        files_before_count = 0
        # This is edge case: 0 files before means PR had no changes, which is unusual
        # We skip warning because files_before_count > 0 is not satisfied
        # Note: files_after_count check is short-circuited since files_before_count == 0
        should_warn = files_before_count > 0
        assert should_warn is False


class TestRebasePrFunction:
    """Tests for rebase_pr() function (Issue #1348).

    Tests cover:
    - Successful rebase returns RebaseResult(success=True)
    - Conflict detection with various indicators
    - Error message capture on failure
    """

    def test_rebase_success(self):
        """Successful rebase should return RebaseResult with success=True."""
        with patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh:
            mock_gh.return_value = (True, "", "")
            result = ci_monitor.rebase_pr("123")
            assert result.success is True
            assert result.conflict is False
            assert result.error_message is None

    def test_rebase_failure_with_conflict_keyword(self):
        """Failure with 'conflict' in error message should set conflict=True."""
        with patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh:
            mock_gh.return_value = (False, "", "Merge conflict detected")
            result = ci_monitor.rebase_pr("123")
            assert result.success is False
            assert result.conflict is True
            assert result.error_message == "Merge conflict detected"

    def test_rebase_failure_with_merge_conflict_keyword(self):
        """Failure with 'merge conflict' in error message should set conflict=True."""
        with patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh:
            mock_gh.return_value = (False, "", "MERGE CONFLICT in file.py")
            result = ci_monitor.rebase_pr("123")
            assert result.success is False
            assert result.conflict is True

    def test_rebase_failure_with_could_not_be_rebased(self):
        """Failure with 'could not be rebased' should set conflict=True."""
        with patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh:
            mock_gh.return_value = (False, "", "PR could not be rebased automatically")
            result = ci_monitor.rebase_pr("123")
            assert result.success is False
            assert result.conflict is True

    def test_rebase_failure_without_conflict(self):
        """Failure without conflict indicators should set conflict=False."""
        with patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh:
            mock_gh.return_value = (False, "", "Network timeout")
            result = ci_monitor.rebase_pr("123")
            assert result.success is False
            assert result.conflict is False
            assert result.error_message == "Network timeout"

    def test_rebase_failure_uses_stdout_if_stderr_empty(self):
        """Error message should use stdout if stderr is empty."""
        with patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh:
            mock_gh.return_value = (False, "Error in stdout", "")
            result = ci_monitor.rebase_pr("123")
            assert result.success is False
            assert result.error_message == "Error in stdout"

    def test_rebase_failure_prefers_stderr(self):
        """Error message should prefer stderr over stdout."""
        with patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh:
            mock_gh.return_value = (False, "stdout content", "stderr content")
            result = ci_monitor.rebase_pr("123")
            assert result.success is False
            assert result.error_message == "stderr content"

    def test_rebase_failure_with_empty_output(self):
        """Failure with empty output should have None error_message."""
        with patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh:
            mock_gh.return_value = (False, "", "")
            result = ci_monitor.rebase_pr("123")
            assert result.success is False
            assert result.conflict is False
            assert result.error_message is None

    def test_conflict_detection_case_insensitive(self):
        """Conflict detection should be case-insensitive."""
        with patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh:
            mock_gh.return_value = (False, "", "CONFLICT detected")
            result = ci_monitor.rebase_pr("123")
            assert result.success is False
            assert result.conflict is True

    def test_rebase_success_syncs_local_and_updates_marker(self):
        """Issue #1795: Successful rebase should sync local branch and update marker.

        gh pr update-branch --rebase executes rebase on GitHub (remote),
        so local post-rewrite hook is not triggered. We need to:
        1. Check current branch to verify we're on PR branch
        2. Fetch from origin
        3. Reset local to match remote (only if on PR branch)
        4. Run marker update script explicitly
        """
        with (
            patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh,
            patch.object(ci_monitor, "get_pr_branch_name") as mock_branch,
            patch("subprocess.run") as mock_run,
            patch("pathlib.Path.exists") as mock_exists,
        ):
            mock_gh.return_value = (True, "", "")
            mock_branch.return_value = "feat/test-branch"
            mock_exists.return_value = True
            # Mock subprocess.run to return current branch as PR branch
            mock_run.return_value = MagicMock(returncode=0, stdout="feat/test-branch\n")

            result = ci_monitor.rebase_pr("123")

            assert result.success is True

            # Verify get_pr_branch_name was called
            mock_branch.assert_called_once_with("123")

            # Verify subprocess.run calls (get current branch, fetch, reset, marker update)
            assert mock_run.call_count == 4

            calls = mock_run.call_args_list

            # First call: git rev-parse --abbrev-ref HEAD (get current branch)
            assert calls[0][0][0] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]

            # Second call: git fetch origin
            assert calls[1][0][0] == ["git", "fetch", "origin"]

            # Third call: git reset --hard origin/{branch}
            assert calls[2][0][0] == ["git", "reset", "--hard", "origin/feat/test-branch"]

            # Fourth call: marker update script
            assert "update_codex_marker_on_rebase.sh" in str(calls[3][0][0])

    def test_rebase_success_skips_sync_when_branch_not_found(self):
        """Issue #1795: Should skip sync if branch name cannot be determined."""
        with (
            patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh,
            patch.object(ci_monitor, "get_pr_branch_name") as mock_branch,
            patch("subprocess.run") as mock_run,
        ):
            mock_gh.return_value = (True, "", "")
            mock_branch.return_value = None  # Branch not found

            result = ci_monitor.rebase_pr("123")

            assert result.success is True
            mock_branch.assert_called_once_with("123")
            # subprocess.run should not be called when branch is None
            mock_run.assert_not_called()

    def test_rebase_success_skips_marker_update_when_script_not_exists(self):
        """Issue #1795: Should skip marker update if script doesn't exist."""
        with (
            patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh,
            patch.object(ci_monitor, "get_pr_branch_name") as mock_branch,
            patch("subprocess.run") as mock_run,
            patch("pathlib.Path.exists") as mock_exists,
        ):
            mock_gh.return_value = (True, "", "")
            mock_branch.return_value = "feat/test-branch"
            mock_exists.return_value = False  # Script doesn't exist
            # Mock subprocess.run to return current branch as PR branch
            mock_run.return_value = MagicMock(returncode=0, stdout="feat/test-branch\n")

            result = ci_monitor.rebase_pr("123")

            assert result.success is True

            # rev-parse, fetch, and reset should be called (3 calls, no marker update)
            assert mock_run.call_count == 3

            calls = mock_run.call_args_list

            # First call: git rev-parse --abbrev-ref HEAD
            assert calls[0][0][0] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]

            # Second call: git fetch origin
            assert calls[1][0][0] == ["git", "fetch", "origin"]

            # Third call: git reset --hard origin/{branch}
            assert calls[2][0][0] == ["git", "reset", "--hard", "origin/feat/test-branch"]

    def test_rebase_success_skips_reset_when_not_on_pr_branch(self):
        """Issue #1795: Should skip reset/marker update if not on PR branch.

        This is critical for safety - if we're on main and reset to origin/{pr-branch},
        we would corrupt the local main branch.
        """
        with (
            patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh,
            patch.object(ci_monitor, "get_pr_branch_name") as mock_branch,
            patch("subprocess.run") as mock_run,
        ):
            mock_gh.return_value = (True, "", "")
            mock_branch.return_value = "feat/test-branch"
            # Current branch is main, not the PR branch; fetch succeeds
            mock_run.return_value = MagicMock(returncode=0, stdout="main\n", stderr="")

            result = ci_monitor.rebase_pr("123")

            assert result.success is True

            # Only rev-parse and fetch should be called (2 calls)
            # Reset and marker update are skipped because we're not on PR branch
            assert mock_run.call_count == 2

            calls = mock_run.call_args_list

            # First call: git rev-parse --abbrev-ref HEAD
            assert calls[0][0][0] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]

            # Second call: git fetch origin (still fetches to update refs)
            assert calls[1][0][0] == ["git", "fetch", "origin"]

    def test_rebase_success_returns_success_on_subprocess_exception(self):
        """Issue #1795: Should return success=True even if subprocess raises exception.

        Remote rebase succeeded, so local sync/marker failures are not fatal.
        """
        with (
            patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh,
            patch.object(ci_monitor, "get_pr_branch_name") as mock_branch,
            patch("subprocess.run") as mock_run,
        ):
            mock_gh.return_value = (True, "", "")
            mock_branch.return_value = "feat/test-branch"
            # Simulate timeout exception
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=10)

            result = ci_monitor.rebase_pr("123")

            # Should still return success because remote rebase succeeded
            assert result.success is True

    def test_rebase_success_skips_reset_when_fetch_fails(self):
        """Issue #1795: Should skip reset/marker update if fetch fails."""
        with (
            patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh,
            patch.object(ci_monitor, "get_pr_branch_name") as mock_branch,
            patch("subprocess.run") as mock_run,
        ):
            mock_gh.return_value = (True, "", "")
            mock_branch.return_value = "feat/test-branch"

            # First call (rev-parse) succeeds, second call (fetch) fails
            def side_effect(*args, **kwargs):
                cmd = args[0]
                if cmd[0] == "git" and cmd[1] == "rev-parse":
                    return MagicMock(returncode=0, stdout="feat/test-branch\n", stderr="")
                elif cmd[0] == "git" and cmd[1] == "fetch":
                    return MagicMock(returncode=1, stdout="", stderr="Network error")
                return MagicMock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect

            result = ci_monitor.rebase_pr("123")

            assert result.success is True
            # Only rev-parse and fetch called (reset skipped due to fetch failure)
            assert mock_run.call_count == 2
