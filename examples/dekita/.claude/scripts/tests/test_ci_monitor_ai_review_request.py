#!/usr/bin/env python3
"""Unit tests for Copilot review error detection and request handling in ci_monitor.

Covers:
- Copilot review error detection
- request_copilot_review function
- Copilot review error integration tests
"""

import json
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
    EventType,
    MergeState,
    PRState,
    check_once,
)


class TestCopilotReviewError:
    """Tests for Copilot review error detection feature.

    When Copilot fails to review a PR, it posts an error message like:
    "Copilot encountered an error and was unable to review this pull request."

    These tests verify:
    1. Error detection via is_copilot_review_error()
    2. REVIEW_ERROR event creation in check_once()
    3. MonitorResult failure when error is detected in monitor_pr()
    """

    @patch("ci_monitor.github_api.run_gh_command")
    def test_get_copilot_reviews_finds_reviews(self, mock_run_gh):
        """Test that Copilot reviews are detected."""
        mock_run_gh.return_value = (
            True,
            json.dumps(
                [
                    {
                        "id": 123,
                        "user": "copilot-pull-request-reviewer[bot]",
                        "submitted_at": "2025-01-01T00:00:00Z",
                        "state": "COMMENTED",
                        "body": "Code review summary...",
                    }
                ]
            ),
        )

        reviews = ci_monitor.get_copilot_reviews("42")

        assert len(reviews) == 1
        assert reviews[0]["id"] == 123

    @patch("ci_monitor.github_api.run_gh_command")
    def test_get_copilot_reviews_empty_when_no_reviews(self, mock_run_gh):
        """Test that empty list is returned when no Copilot reviews."""
        mock_run_gh.return_value = (True, "[]")

        reviews = ci_monitor.get_copilot_reviews("42")

        assert len(reviews) == 0

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_is_copilot_review_error_detects_error(self, mock_get_reviews):
        """Test that error is detected when Copilot posts error message."""
        mock_get_reviews.return_value = [
            {
                "id": 123,
                "user": "copilot-pull-request-reviewer[bot]",
                "body": "Copilot encountered an error and was unable to review this pull request. You can try again by re-requesting a review.",
            }
        ]

        is_error, error_message = ci_monitor.is_copilot_review_error("42")

        assert is_error
        assert "encountered an error" in error_message

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_is_copilot_review_error_false_for_normal_review(self, mock_get_reviews):
        """Test that no error is detected for normal review."""
        mock_get_reviews.return_value = [
            {
                "id": 123,
                "user": "copilot-pull-request-reviewer[bot]",
                "body": "This code looks good. Consider adding a test.",
            }
        ]

        is_error, error_message = ci_monitor.is_copilot_review_error("42")

        assert not is_error
        assert error_message is None

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_is_copilot_review_error_false_when_no_reviews(self, mock_get_reviews):
        """Test that no error is detected when no reviews."""
        mock_get_reviews.return_value = []

        is_error, error_message = ci_monitor.is_copilot_review_error("42")

        assert not is_error
        assert error_message is None

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_is_copilot_review_error_detects_various_patterns(self, mock_get_reviews):
        """Test that various error patterns are detected."""
        error_messages = [
            "Copilot encountered an error and was unable to review",
            "Unable to review this pull request",
            "Could not complete the review",
            "Failed to review the changes",
            "An error occurred during review",
        ]

        for msg in error_messages:
            mock_get_reviews.return_value = [
                {"id": 1, "body": msg, "submitted_at": "2025-01-01T00:00:00Z"}
            ]
            is_error, _ = ci_monitor.is_copilot_review_error("42")
            assert is_error, f"Expected error for: {msg}"

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_is_copilot_review_error_ignores_old_error_if_newer_success(self, mock_get_reviews):
        """Test that old error is ignored if a newer successful review exists."""
        mock_get_reviews.return_value = [
            {
                "id": 1,
                "body": "Copilot encountered an error and was unable to review",
                "submitted_at": "2025-01-01T00:00:00Z",
            },
            {
                "id": 2,
                "body": "This code looks good. No issues found.",
                "submitted_at": "2025-01-02T00:00:00Z",  # Newer
            },
        ]

        is_error, error_message = ci_monitor.is_copilot_review_error("42")

        assert not is_error
        assert error_message is None

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_is_copilot_review_error_detects_latest_error(self, mock_get_reviews):
        """Test that error is detected when the latest review is an error."""
        mock_get_reviews.return_value = [
            {
                "id": 1,
                "body": "This code looks good.",
                "submitted_at": "2025-01-01T00:00:00Z",
            },
            {
                "id": 2,
                "body": "Copilot encountered an error and was unable to review",
                "submitted_at": "2025-01-02T00:00:00Z",  # Newer and is error
            },
        ]

        is_error, error_message = ci_monitor.is_copilot_review_error("42")

        assert is_error
        assert "encountered an error" in error_message


class TestRequestCopilotReview:
    """Tests for request_copilot_review function (Issue #1394).

    Verifies that the function returns a tuple of (success, error_message)
    for better error diagnostics.
    """

    @patch("ci_monitor.pr_operations.subprocess.run")
    def test_request_copilot_review_success(self, mock_run):
        """Test successful Copilot review request returns (True, '')."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="{}",
            stderr="",
        )

        success, error_msg = ci_monitor.request_copilot_review("123")

        assert success is True
        assert error_msg == ""

    @patch("ci_monitor.pr_operations.subprocess.run")
    def test_request_copilot_review_failure_returns_error(self, mock_run):
        """Test failed request returns (False, error_message)."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Not Found: Resource not found",
        )

        success, error_msg = ci_monitor.request_copilot_review("123")

        assert success is False
        assert "Not Found" in error_msg

    @patch("ci_monitor.pr_operations.subprocess.run")
    def test_request_copilot_review_failure_no_stderr(self, mock_run):
        """Test failed request with empty stderr."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="",
        )

        success, error_msg = ci_monitor.request_copilot_review("123")

        assert success is False
        assert error_msg == "No stderr output"

    @patch("ci_monitor.pr_operations.subprocess.run")
    def test_request_copilot_review_exception_returns_error(self, mock_run):
        """Test exception handling returns (False, error_message)."""
        mock_run.side_effect = RuntimeError("Connection timeout")

        success, error_msg = ci_monitor.request_copilot_review("123")

        assert success is False
        assert "Connection timeout" in error_msg


class TestCopilotReviewErrorIntegration:
    """Integration tests for Copilot review error handling.

    These tests verify the integration between check_once(), monitor_pr()
    and the error detection/retry mechanism.
    """

    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_check_once_returns_review_error_event(
        self, mock_get_pr_state, mock_get_comments, mock_is_error
    ):
        """Test that check_once returns REVIEW_ERROR event when Copilot fails."""
        mock_get_pr_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=[],  # Copilot removed after error
                check_status=CheckStatus.PENDING,
            ),
            None,
        )
        mock_is_error.return_value = (True, "Copilot encountered an error")

        # Previous state had Copilot as reviewer
        event = check_once("123", ["Copilot"])

        assert event is not None
        assert event.event_type == EventType.REVIEW_ERROR
        assert "error" in event.message.lower()
        assert event.suggested_action == "Re-request Copilot review"

    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_monitor_pr_fails_on_copilot_error(
        self, mock_get_pr_state, mock_sleep, mock_codex_pending, mock_is_error
    ):
        """Test that monitor_pr returns failure when Copilot review errors."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state: Copilot assigned
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # Copilot finished (with error)
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (
            True,
            "Copilot encountered an error and was unable to review",
        )

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert not result.success
        assert "Copilot review failed" in result.message
        assert not result.review_completed

    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_monitor_pr_succeeds_on_normal_review(
        self,
        mock_get_pr_state,
        mock_get_comments,
        mock_sleep,
        mock_get_threads,
        mock_codex_pending,
        mock_is_error,
    ):
        """Test that monitor_pr succeeds when Copilot review completes normally."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state: Copilot assigned
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 2:
                # Copilot finished (normal)
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            else:
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (False, None)  # No error
        mock_get_comments.return_value = []
        mock_get_threads.return_value = []

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert result.review_completed

    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.classify_review_comments")
    def test_monitor_pr_early_exit_on_review_comments(
        self,
        mock_classify,
        mock_get_pr_state,
        mock_get_comments,
        mock_sleep,
        mock_get_threads,
        mock_codex_pending,
        mock_is_error,
    ):
        """Test that monitor_pr exits early when early_exit=True and review comments are detected."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state: Copilot assigned
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=ci_monitor.CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # Copilot finished (normal) - still pending CI
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=ci_monitor.CheckStatus.PENDING,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (False, None)  # No error
        mock_get_comments.return_value = [
            {"body": "Consider adding a test", "line": 10, "path": "foo.py", "user": "Copilot"}
        ]
        mock_get_threads.return_value = []
        mock_classify.return_value = ci_monitor.ClassifiedComments(
            in_scope=[
                {"body": "Consider adding a test", "line": 10, "path": "foo.py", "user": "Copilot"}
            ],
            out_of_scope=[],
        )

        result = ci_monitor.monitor_pr("123", timeout_minutes=1, early_exit=True)

        # Early exit should return success with review_completed=True
        assert result.success
        assert result.review_completed
        assert "early exit" in result.message.lower()
        # CI is still pending, so ci_passed should be False
        assert not result.ci_passed

    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.classify_review_comments")
    def test_monitor_pr_early_exit_not_bypassing_ci_failure(
        self,
        mock_classify,
        mock_get_pr_state,
        mock_get_comments,
        mock_sleep,
        mock_get_threads,
        mock_codex_pending,
        mock_is_error,
    ):
        """Test that early_exit does not bypass CI failure handling."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state: Copilot assigned
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=ci_monitor.CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # Copilot finished AND CI failed
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=ci_monitor.CheckStatus.FAILURE,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (False, None)  # No Copilot error
        mock_get_comments.return_value = [
            {"body": "Consider adding a test", "line": 10, "path": "foo.py", "user": "Copilot"}
        ]
        mock_get_threads.return_value = []
        mock_classify.return_value = ci_monitor.ClassifiedComments(
            in_scope=[
                {"body": "Consider adding a test", "line": 10, "path": "foo.py", "user": "Copilot"}
            ],
            out_of_scope=[],
        )

        result = ci_monitor.monitor_pr("123", timeout_minutes=1, early_exit=True)

        # Even with early_exit=True, CI failure should take precedence
        assert not result.success
        assert "ci failed" in result.message.lower()
        assert not result.ci_passed

    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.classify_review_comments")
    def test_monitor_pr_early_exit_on_out_of_scope_comments(
        self,
        mock_classify,
        mock_get_pr_state,
        mock_get_comments,
        mock_sleep,
        mock_get_threads,
        mock_codex_pending,
        mock_is_error,
    ):
        """Test that early_exit triggers for out-of-scope comments too.

        This documents the intentional behavior: early exit is triggered for ANY
        review comments, including out-of-scope ones. This allows the agent to
        start creating follow-up Issues early, following the shift-left principle.
        """
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=ci_monitor.CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=ci_monitor.CheckStatus.PENDING,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (False, None)
        # Only out-of-scope comments (e.g., suggestions for other files)
        mock_get_comments.return_value = [
            {"body": "Consider updating docs", "line": 5, "path": "README.md", "user": "Copilot"}
        ]
        mock_get_threads.return_value = []
        mock_classify.return_value = ci_monitor.ClassifiedComments(
            in_scope=[],
            out_of_scope=[
                {
                    "body": "Consider updating docs",
                    "line": 5,
                    "path": "README.md",
                    "user": "Copilot",
                }
            ],
        )

        result = ci_monitor.monitor_pr("123", timeout_minutes=1, early_exit=True)

        # Early exit should trigger for out-of-scope comments too
        assert result.success
        assert result.review_completed
        assert "early exit" in result.message.lower()

    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.classify_review_comments")
    def test_monitor_pr_early_exit_not_bypassing_ci_cancelled(
        self,
        mock_classify,
        mock_get_pr_state,
        mock_get_comments,
        mock_sleep,
        mock_get_threads,
        mock_codex_pending,
        mock_is_error,
    ):
        """Test that early_exit does not bypass CI cancelled handling."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=ci_monitor.CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # Copilot finished AND CI was cancelled
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=ci_monitor.CheckStatus.CANCELLED,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (False, None)
        mock_get_comments.return_value = [
            {"body": "Consider adding a test", "line": 10, "path": "foo.py", "user": "Copilot"}
        ]
        mock_get_threads.return_value = []
        mock_classify.return_value = ci_monitor.ClassifiedComments(
            in_scope=[
                {"body": "Consider adding a test", "line": 10, "path": "foo.py", "user": "Copilot"}
            ],
            out_of_scope=[],
        )

        result = ci_monitor.monitor_pr("123", timeout_minutes=1, early_exit=True)

        # Even with early_exit=True, CI cancellation should take precedence
        assert not result.success
        assert "cancelled" in result.message.lower()
        assert not result.ci_passed
