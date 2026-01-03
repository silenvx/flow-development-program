"""Tests for pr_operations module."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ci_monitor.models import CheckStatus, MergeState, RebaseResult
from ci_monitor.pr_operations import (
    format_rebase_summary,
    get_main_last_commit_time,
    get_pr_branch_name,
    get_pr_state,
    has_ai_review_pending,
    has_local_changes,
    is_codex_review_pending,
    merge_pr,
    rebase_pr,
    recreate_pr,
    reopen_pr_with_retry,
    sync_local_after_rebase,
    validate_pr_number,
    validate_pr_numbers,
    wait_for_main_stable,
)


class TestValidatePrNumber:
    """Tests for validate_pr_number."""

    def test_valid_pr_number(self) -> None:
        """Valid PR numbers should pass validation."""
        is_valid, error = validate_pr_number("123")
        assert is_valid is True
        assert error == ""

    def test_valid_large_pr_number(self) -> None:
        """Large but valid PR numbers should pass."""
        is_valid, error = validate_pr_number("999999")
        assert is_valid is True
        assert error == ""

    def test_invalid_non_integer(self) -> None:
        """Non-integer values should fail."""
        is_valid, error = validate_pr_number("abc")
        assert is_valid is False
        assert "must be a positive integer" in error

    def test_invalid_zero(self) -> None:
        """Zero should fail."""
        is_valid, error = validate_pr_number("0")
        assert is_valid is False
        assert "must be a positive integer" in error

    def test_invalid_negative(self) -> None:
        """Negative numbers should fail."""
        is_valid, error = validate_pr_number("-1")
        assert is_valid is False
        assert "must be a positive integer" in error

    def test_invalid_too_large(self) -> None:
        """Numbers above 999999 should fail."""
        is_valid, error = validate_pr_number("1000000")
        assert is_valid is False
        assert "value too large" in error


class TestValidatePrNumbers:
    """Tests for validate_pr_numbers."""

    def test_empty_list(self) -> None:
        """Empty list should return empty list."""
        result = validate_pr_numbers([])
        assert result == []

    def test_valid_list(self) -> None:
        """Valid PR numbers should return unchanged."""
        result = validate_pr_numbers(["1", "2", "3"])
        assert result == ["1", "2", "3"]

    def test_invalid_exits(self) -> None:
        """Invalid PR numbers should cause exit."""
        with pytest.raises(SystemExit) as exc_info:
            validate_pr_numbers(["1", "abc", "3"])
        assert exc_info.value.code == 1


class TestGetPrState:
    """Tests for get_pr_state."""

    @patch("ci_monitor.pr_operations.run_gh_command_with_error")
    @patch("ci_monitor.pr_operations.run_gh_command")
    def test_successful_state_fetch(
        self, mock_run: MagicMock, mock_run_with_error: MagicMock
    ) -> None:
        """Successful state fetch returns PRState."""
        mock_run_with_error.return_value = (True, "CLEAN", "")
        mock_run.side_effect = [
            (True, '["user1"]'),  # reviewers
            (True, '[{"name": "test", "state": "SUCCESS"}]'),  # checks
        ]

        state, error = get_pr_state("123")

        assert state is not None
        assert error is None
        assert state.merge_state == MergeState.CLEAN
        assert state.pending_reviewers == ["user1"]
        assert state.check_status == CheckStatus.SUCCESS

    @patch("ci_monitor.pr_operations.run_gh_command_with_error")
    def test_api_error(self, mock_run_with_error: MagicMock) -> None:
        """API error returns None state with error message."""
        mock_run_with_error.return_value = (False, "", "API error")

        state, error = get_pr_state("123")

        assert state is None
        assert error == "API error"

    @patch("ci_monitor.pr_operations.run_gh_command_with_error")
    @patch("ci_monitor.pr_operations.run_gh_command")
    def test_unknown_merge_state(self, mock_run: MagicMock, mock_run_with_error: MagicMock) -> None:
        """Unknown merge state should be handled."""
        mock_run_with_error.return_value = (True, "INVALID_STATE", "")
        mock_run.side_effect = [
            (True, "[]"),
            (True, "[]"),
        ]

        state, error = get_pr_state("123")

        assert state is not None
        assert state.merge_state == MergeState.UNKNOWN

    @patch("ci_monitor.pr_operations.run_gh_command_with_error")
    @patch("ci_monitor.pr_operations.run_gh_command")
    def test_ci_failure_detected(self, mock_run: MagicMock, mock_run_with_error: MagicMock) -> None:
        """CI failure should be detected."""
        mock_run_with_error.return_value = (True, "CLEAN", "")
        mock_run.side_effect = [
            (True, "[]"),
            (True, '[{"name": "test", "state": "FAILURE"}]'),
        ]

        state, error = get_pr_state("123")

        assert state is not None
        assert state.check_status == CheckStatus.FAILURE

    @patch("ci_monitor.pr_operations.run_gh_command_with_error")
    @patch("ci_monitor.pr_operations.run_gh_command")
    def test_ci_cancelled_detected(
        self, mock_run: MagicMock, mock_run_with_error: MagicMock
    ) -> None:
        """CI cancelled should be detected."""
        mock_run_with_error.return_value = (True, "CLEAN", "")
        mock_run.side_effect = [
            (True, "[]"),
            (True, '[{"name": "test", "state": "CANCELLED"}]'),
        ]

        state, error = get_pr_state("123")

        assert state is not None
        assert state.check_status == CheckStatus.CANCELLED


class TestHasLocalChanges:
    """Tests for has_local_changes."""

    @patch("subprocess.run")
    def test_no_changes(self, mock_run: MagicMock) -> None:
        """No local changes should return False."""
        mock_status = MagicMock()
        mock_status.returncode = 0
        mock_status.stdout = ""

        mock_log = MagicMock()
        mock_log.returncode = 0
        mock_log.stdout = ""

        mock_run.side_effect = [mock_status, mock_log]

        has_changes, description = has_local_changes()

        assert has_changes is False
        assert description == ""

    @patch("subprocess.run")
    def test_uncommitted_changes(self, mock_run: MagicMock) -> None:
        """Uncommitted changes should be detected."""
        mock_status = MagicMock()
        mock_status.returncode = 0
        mock_status.stdout = "M file.py"

        mock_log = MagicMock()
        mock_log.returncode = 0
        mock_log.stdout = ""

        mock_run.side_effect = [mock_status, mock_log]

        has_changes, description = has_local_changes()

        assert has_changes is True
        assert "uncommitted changes" in description

    @patch("subprocess.run")
    def test_unpushed_commits(self, mock_run: MagicMock) -> None:
        """Unpushed commits should be detected."""
        mock_status = MagicMock()
        mock_status.returncode = 0
        mock_status.stdout = ""

        mock_log = MagicMock()
        mock_log.returncode = 0
        mock_log.stdout = "abc123 Commit message"

        mock_run.side_effect = [mock_status, mock_log]

        has_changes, description = has_local_changes()

        assert has_changes is True
        assert "unpushed commit" in description

    @patch("subprocess.run")
    def test_timeout_handled(self, mock_run: MagicMock) -> None:
        """Timeout should be handled gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=10)

        has_changes, description = has_local_changes()

        assert has_changes is False
        assert description == ""

    @patch("subprocess.run")
    def test_untracked_files_only(self, mock_run: MagicMock) -> None:
        """Untracked files only should not be detected as uncommitted changes.

        Issue #1805: git rebase works fine with untracked files, so they
        should not block rebase operations.
        """
        mock_status = MagicMock()
        mock_status.returncode = 0
        mock_status.stdout = "?? .claude-session-marker\n?? untracked.txt"

        mock_log = MagicMock()
        mock_log.returncode = 0
        mock_log.stdout = ""

        mock_run.side_effect = [mock_status, mock_log]

        has_changes, description = has_local_changes()

        assert has_changes is False
        assert description == ""

    @patch("subprocess.run")
    def test_untracked_and_tracked_changes(self, mock_run: MagicMock) -> None:
        """Untracked files with tracked changes should detect uncommitted changes."""
        mock_status = MagicMock()
        mock_status.returncode = 0
        mock_status.stdout = "?? untracked.txt\n M modified.py"

        mock_log = MagicMock()
        mock_log.returncode = 0
        mock_log.stdout = ""

        mock_run.side_effect = [mock_status, mock_log]

        has_changes, description = has_local_changes()

        assert has_changes is True
        assert "uncommitted changes" in description


class TestGetMainLastCommitTime:
    """Tests for get_main_last_commit_time."""

    @patch("subprocess.run")
    def test_successful_fetch(self, mock_run: MagicMock) -> None:
        """Successful fetch returns timestamp."""
        mock_fetch = MagicMock()
        mock_fetch.returncode = 0

        mock_log = MagicMock()
        mock_log.returncode = 0
        mock_log.stdout = "1704067200"

        mock_run.side_effect = [mock_fetch, mock_log]

        result = get_main_last_commit_time()

        assert result == 1704067200

    @patch("subprocess.run")
    def test_fetch_failure(self, mock_run: MagicMock) -> None:
        """Fetch failure returns None."""
        mock_fetch = MagicMock()
        mock_fetch.returncode = 1

        mock_run.return_value = mock_fetch

        result = get_main_last_commit_time()

        assert result is None

    @patch("subprocess.run")
    def test_timeout_returns_none(self, mock_run: MagicMock) -> None:
        """Timeout returns None."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)

        result = get_main_last_commit_time()

        assert result is None


class TestRebasePr:
    """Tests for rebase_pr."""

    @patch("ci_monitor.pr_operations.run_gh_command_with_error")
    def test_successful_rebase(self, mock_run: MagicMock) -> None:
        """Successful rebase returns success result."""
        mock_run.return_value = (True, "Updated branch", "")

        result = rebase_pr("123")

        assert isinstance(result, RebaseResult)
        assert result.success is True
        assert result.conflict is False

    @patch("ci_monitor.pr_operations.run_gh_command_with_error")
    def test_conflict_detected(self, mock_run: MagicMock) -> None:
        """Merge conflict should be detected."""
        mock_run.return_value = (False, "", "Merge conflict in file.py")

        result = rebase_pr("123")

        assert result.success is False
        assert result.conflict is True
        assert result.error_message is not None

    @patch("ci_monitor.pr_operations.run_gh_command_with_error")
    def test_non_conflict_error(self, mock_run: MagicMock) -> None:
        """Non-conflict errors should not set conflict flag."""
        mock_run.return_value = (False, "", "Network error")

        result = rebase_pr("123")

        assert result.success is False
        assert result.conflict is False
        assert result.error_message == "Network error"


class TestMergePr:
    """Tests for merge_pr."""

    @patch("ci_monitor.pr_operations.run_gh_command_with_error")
    def test_successful_merge(self, mock_run: MagicMock) -> None:
        """Successful merge returns success."""
        mock_run.return_value = (True, "Merged", "")

        success, message = merge_pr("123")

        assert success is True
        assert message == "Merge successful"

    @patch("ci_monitor.pr_operations.run_gh_command_with_error")
    def test_behind_error(self, mock_run: MagicMock) -> None:
        """Behind error should return BEHIND."""
        mock_run.return_value = (False, "", "Head branch is not up to date")

        success, message = merge_pr("123")

        assert success is False
        assert message == "BEHIND"

    @patch("ci_monitor.pr_operations.run_gh_command_with_error")
    def test_other_error(self, mock_run: MagicMock) -> None:
        """Other errors should be returned as-is."""
        mock_run.return_value = (False, "", "Some other error")

        success, message = merge_pr("123")

        assert success is False
        assert message == "Some other error"


class TestGetPrBranchName:
    """Tests for get_pr_branch_name."""

    @patch("ci_monitor.pr_operations.run_gh_command")
    def test_successful_fetch(self, mock_run: MagicMock) -> None:
        """Successful fetch returns branch name."""
        mock_run.return_value = (True, "feature/my-branch")

        result = get_pr_branch_name("123")

        assert result == "feature/my-branch"

    @patch("ci_monitor.pr_operations.run_gh_command")
    def test_failure_returns_none(self, mock_run: MagicMock) -> None:
        """Failure returns None."""
        mock_run.return_value = (False, "")

        result = get_pr_branch_name("123")

        assert result is None


class TestFormatRebaseSummary:
    """Tests for format_rebase_summary."""

    def test_single_rebase(self) -> None:
        """Single rebase should not suggest merge queue."""
        result = format_rebase_summary(1)
        assert "Rebases performed: 1" in result
        assert "merge queue" not in result

    def test_multiple_rebases(self) -> None:
        """Multiple rebases should suggest merge queue."""
        result = format_rebase_summary(2)
        assert "Rebases performed: 2" in result
        assert "merge queue" in result

    def test_zero_rebases(self) -> None:
        """Zero rebases should not suggest merge queue."""
        result = format_rebase_summary(0)
        assert "Rebases performed: 0" in result
        assert "merge queue" not in result


class TestSyncLocalAfterRebase:
    """Tests for sync_local_after_rebase."""

    @patch("subprocess.run")
    def test_not_in_git_repo(self, mock_run: MagicMock) -> None:
        """Not in git repo should return True (no sync needed)."""
        mock_result = MagicMock()
        mock_result.returncode = 128

        mock_run.return_value = mock_result

        result = sync_local_after_rebase("main", json_mode=True)

        assert result is True

    @patch("subprocess.run")
    def test_different_branch(self, mock_run: MagicMock) -> None:
        """Different branch should return True (no sync needed)."""
        mock_is_repo = MagicMock()
        mock_is_repo.returncode = 0

        mock_branch = MagicMock()
        mock_branch.returncode = 0
        mock_branch.stdout = "other-branch"

        mock_run.side_effect = [mock_is_repo, mock_branch]

        result = sync_local_after_rebase("main", json_mode=True)

        assert result is True

    @patch("subprocess.run")
    def test_uncommitted_changes(self, mock_run: MagicMock) -> None:
        """Uncommitted changes should return False."""
        mock_is_repo = MagicMock()
        mock_is_repo.returncode = 0

        mock_branch = MagicMock()
        mock_branch.returncode = 0
        mock_branch.stdout = "main"

        mock_status = MagicMock()
        mock_status.returncode = 0
        mock_status.stdout = "M file.py"

        mock_run.side_effect = [mock_is_repo, mock_branch, mock_status]

        result = sync_local_after_rebase("main", json_mode=True)

        assert result is False


class TestReopenPrWithRetry:
    """Tests for reopen_pr_with_retry."""

    @patch("ci_monitor.pr_operations.run_gh_command")
    def test_successful_first_attempt(self, mock_run: MagicMock) -> None:
        """Successful first attempt returns success."""
        mock_run.return_value = (True, "Reopened")

        success, error, attempts = reopen_pr_with_retry("123", "comment")

        assert success is True
        assert error == ""
        assert attempts == 1

    @patch("ci_monitor.pr_operations.run_gh_command")
    @patch("time.sleep")
    def test_retry_on_failure(self, mock_sleep: MagicMock, mock_run: MagicMock) -> None:
        """Retries on failure until success."""
        mock_run.side_effect = [
            (False, "Error 1"),
            (False, "Error 2"),
            (True, "Reopened"),
        ]

        success, error, attempts = reopen_pr_with_retry("123", "comment", max_retries=3)

        assert success is True
        assert error == ""
        assert attempts == 3

    @patch("ci_monitor.pr_operations.run_gh_command")
    @patch("time.sleep")
    def test_all_retries_fail(self, mock_sleep: MagicMock, mock_run: MagicMock) -> None:
        """All retries failing returns failure."""
        mock_run.return_value = (False, "Persistent error")

        success, error, attempts = reopen_pr_with_retry("123", "comment", max_retries=3)

        assert success is False
        assert error == "Persistent error"
        assert attempts == 3

    @patch("ci_monitor.pr_operations.run_gh_command")
    def test_min_retries(self, mock_run: MagicMock) -> None:
        """max_retries less than 1 should be treated as 1."""
        mock_run.return_value = (False, "Error")

        success, error, attempts = reopen_pr_with_retry("123", "comment", max_retries=0)

        assert attempts == 1


class TestRecreatePr:
    """Tests for recreate_pr."""

    @patch("ci_monitor.pr_operations.run_gh_command")
    def test_get_details_failure(self, mock_run: MagicMock) -> None:
        """Failure to get PR details should return error."""
        mock_run.return_value = (False, "API error")

        success, new_pr, message = recreate_pr("123")

        assert success is False
        assert new_pr is None
        assert "Failed to get PR details" in message

    @patch("ci_monitor.pr_operations.run_gh_command")
    def test_missing_title(self, mock_run: MagicMock) -> None:
        """Missing title should return error."""
        mock_run.return_value = (True, json.dumps({"title": "", "headRefName": "branch"}))

        success, new_pr, message = recreate_pr("123")

        assert success is False
        assert new_pr is None
        assert "タイトル" in message

    @patch("ci_monitor.pr_operations.run_gh_command")
    def test_successful_recreate(self, mock_run: MagicMock) -> None:
        """Successful recreation should return new PR number."""
        mock_run.side_effect = [
            (
                True,
                json.dumps(
                    {
                        "title": "Test PR",
                        "body": "Description",
                        "headRefName": "feature/test",
                        "baseRefName": "main",
                        "labels": [],
                        "assignees": [],
                        "isDraft": False,
                    }
                ),
            ),
            (True, "Closed"),  # close
            (True, "https://github.com/owner/repo/pull/456"),  # create
        ]

        success, new_pr, message = recreate_pr("123")

        assert success is True
        assert new_pr == "456"
        assert "456" in message


class TestIsCodexReviewPending:
    """Tests for is_codex_review_pending."""

    @patch("ci_monitor.pr_operations.get_codex_review_requests")
    def test_no_requests(self, mock_requests: MagicMock) -> None:
        """No requests returns False."""
        mock_requests.return_value = []

        result = is_codex_review_pending("123")

        assert result is False

    @patch("ci_monitor.pr_operations.get_codex_review_requests")
    @patch("ci_monitor.pr_operations.get_codex_reviews")
    def test_request_with_review(self, mock_reviews: MagicMock, mock_requests: MagicMock) -> None:
        """Request with completed review returns False."""
        from ci_monitor.models import CodexReviewRequest

        mock_requests.return_value = [
            CodexReviewRequest(
                comment_id=1, created_at="2024-01-01T00:00:00Z", has_eyes_reaction=True
            )
        ]
        mock_reviews.return_value = [{"submitted_at": "2024-01-01T01:00:00Z"}]

        result = is_codex_review_pending("123")

        assert result is False

    @patch("ci_monitor.pr_operations.get_codex_review_requests")
    @patch("ci_monitor.pr_operations.get_codex_reviews")
    def test_request_without_review(
        self, mock_reviews: MagicMock, mock_requests: MagicMock
    ) -> None:
        """Request without completed review returns True."""
        from ci_monitor.models import CodexReviewRequest

        mock_requests.return_value = [
            CodexReviewRequest(
                comment_id=1, created_at="2024-01-01T00:00:00Z", has_eyes_reaction=True
            )
        ]
        mock_reviews.return_value = []

        result = is_codex_review_pending("123")

        assert result is True


class TestHasAiReviewPending:
    """Tests for has_ai_review_pending."""

    @patch("ci_monitor.pr_operations.has_copilot_or_codex_reviewer")
    @patch("ci_monitor.pr_operations.is_codex_review_pending")
    def test_copilot_pending(self, mock_codex: MagicMock, mock_copilot: MagicMock) -> None:
        """Copilot in reviewers returns True."""
        mock_copilot.return_value = True
        mock_codex.return_value = False

        result = has_ai_review_pending("123", ["copilot"])

        assert result is True
        mock_codex.assert_not_called()

    @patch("ci_monitor.pr_operations.has_copilot_or_codex_reviewer")
    @patch("ci_monitor.pr_operations.is_codex_review_pending")
    def test_codex_pending(self, mock_codex: MagicMock, mock_copilot: MagicMock) -> None:
        """Codex review pending returns True."""
        mock_copilot.return_value = False
        mock_codex.return_value = True

        result = has_ai_review_pending("123", [])

        assert result is True

    @patch("ci_monitor.pr_operations.has_copilot_or_codex_reviewer")
    @patch("ci_monitor.pr_operations.is_codex_review_pending")
    def test_no_ai_pending(self, mock_codex: MagicMock, mock_copilot: MagicMock) -> None:
        """No AI review pending returns False."""
        mock_copilot.return_value = False
        mock_codex.return_value = False

        result = has_ai_review_pending("123", [])

        assert result is False


class TestWaitForMainStable:
    """Tests for wait_for_main_stable."""

    @patch("ci_monitor.pr_operations.get_main_last_commit_time")
    @patch("ci_monitor.pr_operations.log")
    @patch("time.time")
    @patch("time.sleep")
    def test_already_stable(
        self,
        mock_sleep: MagicMock,
        mock_time: MagicMock,
        mock_log: MagicMock,
        mock_get_time: MagicMock,
    ) -> None:
        """Already stable main returns True immediately."""
        current_time = 1704067200
        mock_time.return_value = current_time
        # Commit was 10 minutes ago (stable duration is 5 min by default)
        mock_get_time.return_value = current_time - 600

        result = wait_for_main_stable(json_mode=True)

        assert result is True

    @patch("ci_monitor.pr_operations.get_main_last_commit_time")
    @patch("ci_monitor.pr_operations.log")
    @patch("time.time")
    @patch("time.sleep")
    def test_timeout(
        self,
        mock_sleep: MagicMock,
        mock_time: MagicMock,
        mock_log: MagicMock,
        mock_get_time: MagicMock,
    ) -> None:
        """Timeout returns False."""
        # Simulate time progressing past timeout
        call_count = [0]

        def time_side_effect() -> float:
            call_count[0] += 1
            # After first call, return time past timeout
            if call_count[0] > 1:
                return 1704067200 + 2000  # Past timeout
            return 1704067200

        mock_time.side_effect = time_side_effect
        mock_get_time.return_value = 1704067200  # Always recent

        result = wait_for_main_stable(timeout_minutes=1, json_mode=True)

        assert result is False
