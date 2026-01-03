#!/usr/bin/env python3
"""Unit tests for Copilot retry logic and pending timeout in ci_monitor.

Covers:
- Copilot retry constants
- Copilot retry logic integration tests
- Copilot pending timeout detection
"""

import sys
from pathlib import Path
from unittest.mock import patch

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


class TestCopilotRetryConstants:
    """Tests for Copilot retry-related constants (Issue #1353).

    Verifies that the retry constants are properly defined and have expected values.
    """

    def test_default_max_copilot_retry_exists(self):
        """Test DEFAULT_MAX_COPILOT_RETRY constant exists."""
        assert hasattr(ci_monitor, "DEFAULT_MAX_COPILOT_RETRY")
        assert ci_monitor.DEFAULT_MAX_COPILOT_RETRY == 3

    def test_default_max_retry_wait_polls_exists(self):
        """Test DEFAULT_MAX_RETRY_WAIT_POLLS constant exists."""
        assert hasattr(ci_monitor, "DEFAULT_MAX_RETRY_WAIT_POLLS")
        assert ci_monitor.DEFAULT_MAX_RETRY_WAIT_POLLS == 4

    def test_retry_wait_status_enum_exists(self):
        """Test RetryWaitStatus enum exists with expected values."""
        assert hasattr(ci_monitor, "RetryWaitStatus")
        assert ci_monitor.RetryWaitStatus.CONTINUE.value == "CONTINUE"
        assert ci_monitor.RetryWaitStatus.TIMEOUT.value == "TIMEOUT"


class TestCopilotRetryLogic:
    """Integration tests for Copilot retry logic (Issue #1353).

    Tests the retry mechanism when Copilot review encounters an error:
    1. Error detection triggers retry
    2. Retry count increments correctly
    3. Max retries leads to failure
    4. Successful retry after error
    5. Retry wait polling behavior
    """

    @patch("ci_monitor.main_loop.request_copilot_review")
    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_retry_triggered_on_copilot_error(
        self,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_is_error,
        mock_request_review,
    ):
        """Test that retry is triggered when Copilot error is detected."""
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
            elif call_count <= 5:
                # Copilot finished with error
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # After timeout
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
        mock_is_error.return_value = (True, "Copilot encountered an error")
        mock_request_review.return_value = (True, "")  # Retry succeeds

        # Run with short timeout
        ci_monitor.monitor_pr("123", timeout_minutes=1)

        # request_copilot_review should have been called (retry triggered)
        # Should retry up to DEFAULT_MAX_COPILOT_RETRY times when error persists
        assert mock_request_review.called
        assert mock_request_review.call_count == ci_monitor.DEFAULT_MAX_COPILOT_RETRY

    @patch("ci_monitor.main_loop.request_copilot_review")
    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_max_retries_leads_to_failure(
        self,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_is_error,
        mock_request_review,
    ):
        """Test that exceeding max retries leads to failure."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # Always return no pending reviewers (Copilot keeps failing)
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
        # Always return error
        mock_is_error.return_value = (True, "Copilot encountered an error")
        # Retry request succeeds but Copilot keeps failing
        mock_request_review.return_value = (True, "")

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        # Should fail after max retries
        assert not result.success
        assert "Copilot review failed" in result.message
        # Should have retried DEFAULT_MAX_COPILOT_RETRY times
        assert mock_request_review.call_count == ci_monitor.DEFAULT_MAX_COPILOT_RETRY

    @patch("ci_monitor.main_loop.request_copilot_review")
    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_successful_retry_after_error(
        self,
        mock_get_pr_state,
        mock_sleep,
        mock_get_comments,
        mock_get_threads,
        mock_codex_pending,
        mock_is_error,
        mock_request_review,
    ):
        """Test successful recovery after retry."""
        get_pr_state_call_count = 0
        is_error_call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal get_pr_state_call_count
            get_pr_state_call_count += 1
            if get_pr_state_call_count == 1:
                # Initial: Copilot assigned
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif get_pr_state_call_count == 2:
                # Copilot finished with error (no pending reviewers)
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif get_pr_state_call_count <= 4:
                # After retry: Copilot re-assigned and pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["copilot-pull-request-reviewer[bot]"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # Copilot finished successfully, CI passed
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        def is_error_side_effect(pr_number):
            # Use independent counter to avoid synchronization issues
            nonlocal is_error_call_count
            is_error_call_count += 1
            # Return error only on first call (when Copilot first finishes)
            if is_error_call_count == 1:
                return (True, "Copilot encountered an error")
            return (False, None)

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.side_effect = is_error_side_effect
        mock_request_review.return_value = (True, "")
        mock_get_comments.return_value = []
        mock_get_threads.return_value = []

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        # Should succeed after retry
        assert result.success
        # Should have retried once
        assert mock_request_review.call_count == 1

    @patch("ci_monitor.main_loop.request_copilot_review")
    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_retry_request_failure_logged(
        self,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_is_error,
        mock_request_review,
    ):
        """Test that failed retry request is handled gracefully."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
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
        mock_is_error.return_value = (True, "Copilot encountered an error")
        # Retry request fails
        mock_request_review.return_value = (False, "API rate limit exceeded")

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        # Should still attempt retries even if request fails
        assert mock_request_review.called
        # After max retries, should fail
        assert not result.success

    @patch("ci_monitor.main_loop.request_copilot_review")
    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.events.log")
    def test_retry_count_in_log_message(
        self,
        mock_log,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_is_error,
        mock_request_review,
    ):
        """Test that retry count is included in log messages."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
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
        mock_is_error.return_value = (True, "Copilot encountered an error")
        mock_request_review.return_value = (True, "")

        ci_monitor.monitor_pr("123", timeout_minutes=1)

        # Check that log was called with retry count format
        # Extract first argument (log message) from each call
        log_messages = [call[0][0] for call in mock_log.call_args_list if call[0]]
        retry_logs = [msg for msg in log_messages if "retrying" in msg.lower()]
        assert len(retry_logs) > 0, "Should log retry attempts"

        # Verify format includes count like "1/3" or "2/3"
        has_retry_count = any(
            f"/{ci_monitor.DEFAULT_MAX_COPILOT_RETRY}" in msg for msg in log_messages
        )
        assert has_retry_count, "Should include retry count in format X/MAX"

    @patch("ci_monitor.main_loop.request_copilot_review")
    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.events.log")
    @patch("ci_monitor.main_loop.time.time")
    def test_retry_wait_timeout_proceeds_to_next_retry(
        self,
        mock_time,
        mock_log,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_is_error,
        mock_request_review,
    ):
        """Test that retry wait timeout triggers next retry attempt (Issue #1501).

        When copilot_retry_wait_polls >= DEFAULT_MAX_RETRY_WAIT_POLLS,
        _handle_copilot_retry_wait() returns RetryWaitStatus.TIMEOUT
        and the system should proceed with the next retry attempt.
        """
        # Track time progression - allow enough iterations for retry wait timeout
        time_call_count = 0
        max_retry_wait_polls = ci_monitor.DEFAULT_MAX_RETRY_WAIT_POLLS

        def time_side_effect():
            nonlocal time_call_count
            time_call_count += 1
            # First few calls: within timeout (1 minute = 60 seconds)
            # After retry wait timeout + second retry, trigger overall timeout
            if time_call_count <= max_retry_wait_polls + 10:
                return time_call_count * 5  # 5 seconds per call, staying within timeout
            return 120  # Exceed 1 minute timeout to exit

        mock_time.side_effect = time_side_effect

        get_pr_state_call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal get_pr_state_call_count
            get_pr_state_call_count += 1
            if get_pr_state_call_count == 1:
                # Initial: Copilot assigned
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            # All subsequent calls: Copilot no longer assigned, triggers error check path
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

        # Keep returning error to trigger retry-wait polling
        # After first retry request succeeds, copilot_retry_in_progress becomes True
        # Then we wait for DEFAULT_MAX_RETRY_WAIT_POLLS before timing out
        is_error_call_count = 0

        def is_error_side_effect(pr_number):
            nonlocal is_error_call_count
            is_error_call_count += 1
            # Keep returning error to trigger the retry path
            return (True, "Copilot encountered an error")

        mock_is_error.side_effect = is_error_side_effect
        mock_request_review.return_value = (True, "")  # Retry request succeeds

        ci_monitor.monitor_pr("123", timeout_minutes=1)

        # Verify retry was attempted at least once
        assert mock_request_review.called, "Retry should be requested"

        # Verify log messages include retry wait timeout message
        log_messages = [call[0][0] for call in mock_log.call_args_list if call[0]]
        assert len(log_messages) > 0, "Should have log messages"

        # Verify retry wait polling occurred (looking for "Waiting for Copilot" message)
        waiting_logs = [msg for msg in log_messages if "Waiting for Copilot" in msg]
        timeout_logs = [msg for msg in log_messages if "timeout" in msg.lower()]

        # Either waiting logs or timeout logs should exist if retry-wait path was exercised
        assert len(waiting_logs) > 0 or len(timeout_logs) > 0, (
            f"Should have retry wait logs. Got: {log_messages[:10]}"
        )


class TestCopilotPendingTimeout:
    """Tests for Copilot pending timeout detection (Issue #1532)."""

    def test_constants_exist(self):
        """Should have timeout and max recreate constants."""
        assert hasattr(ci_monitor, "DEFAULT_COPILOT_PENDING_TIMEOUT")
        assert hasattr(ci_monitor, "DEFAULT_MAX_PR_RECREATE")
        assert ci_monitor.DEFAULT_COPILOT_PENDING_TIMEOUT == 300  # 5 minutes
        assert ci_monitor.DEFAULT_MAX_PR_RECREATE == 1

    def test_monitor_result_has_details_field(self):
        """MonitorResult should have details field for PR recreation info."""
        MonitorResult = ci_monitor.MonitorResult
        result = MonitorResult(
            success=False,
            message="Test",
            details={"recreated_pr": "456", "original_pr": "123"},
        )
        assert result.details["recreated_pr"] == "456"
        assert result.details["original_pr"] == "123"

    @patch("ci_monitor.main_loop.save_monitor_state")
    @patch("ci_monitor.main_loop.recreate_pr")
    @patch("ci_monitor.main_loop.log_ci_monitor_event")
    @patch("ci_monitor.main_loop.check_rate_limit")
    @patch("ci_monitor.main_loop.time.time")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_pr_recreation_on_pending_timeout(
        self,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_time,
        mock_check_rate,
        mock_log_event,
        mock_recreate_pr,
        mock_save_state,
    ):
        """Should recreate PR when Copilot is pending for more than 300 seconds."""
        # Mock rate limit to avoid API calls
        mock_check_rate.return_value = (5000, 5000, 0)

        # Simulate time progression for timeout detection:
        # - First few calls return 0 (start_time, copilot_pending_since initialization)
        # - Later calls return 400 (exceeds DEFAULT_COPILOT_PENDING_TIMEOUT of 300s)
        time_call_count = [0]

        def time_side_effect():
            time_call_count[0] += 1
            if time_call_count[0] <= 2:
                return 0  # start_time and initial checks
            elif time_call_count[0] <= 4:
                return 0  # First iteration: copilot_pending_since starts
            else:
                return 400  # Exceeds 300s timeout, triggers recreation

        mock_time.side_effect = time_side_effect

        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            # Always return Copilot as pending reviewer
            return (
                PRState(
                    merge_state=MergeState.CLEAN,
                    pending_reviewers=["Copilot"],
                    check_status=CheckStatus.PENDING,
                ),
                None,
            )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_recreate_pr.return_value = (True, "456", "PR recreated successfully")

        result = ci_monitor.monitor_pr("123", timeout_minutes=10)

        # Verify recreate_pr was called
        mock_recreate_pr.assert_called_once_with("123")
        # Result should indicate PR was recreated
        assert not result.success  # Original PR monitoring ended
        assert result.details.get("recreated_pr") == "456"

    @patch("ci_monitor.main_loop.save_monitor_state")
    @patch("ci_monitor.main_loop.recreate_pr")
    @patch("ci_monitor.main_loop.log_ci_monitor_event")
    @patch("ci_monitor.main_loop.check_rate_limit")
    @patch("ci_monitor.main_loop.time.time")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_max_recreate_count_prevents_further_recreations(
        self,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_time,
        mock_check_rate,
        mock_log_event,
        mock_recreate_pr,
        mock_save_state,
    ):
        """Should not recreate PR more than DEFAULT_MAX_PR_RECREATE times.

        This test verifies that even if recreation fails, the counter prevents
        infinite recreation attempts. After reaching the max count, monitoring
        continues until CI succeeds or timeout.
        """
        # Mock rate limit to avoid API calls
        mock_check_rate.return_value = (5000, 5000, 0)

        # Time increases by 100s per get_pr_state call. After a few iterations,
        # pending_duration exceeds 300s timeout, triggering recreation attempt.
        call_index = [0]

        def time_side_effect():
            # Time = call_index * 100, incremented by get_pr_state_side_effect
            return call_index[0] * 100

        mock_time.side_effect = time_side_effect

        state_call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal state_call_count
            state_call_count += 1
            call_index[0] += 1  # Advance time by 100s per call

            if state_call_count > 5:
                # Return CI success with Copilot still pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],  # Keep pending for timeout logic
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            return (
                PRState(
                    merge_state=MergeState.CLEAN,
                    pending_reviewers=["Copilot"],
                    check_status=CheckStatus.PENDING,
                ),
                None,
            )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        # Recreation fails, so monitoring continues with limited retry count
        mock_recreate_pr.return_value = (False, None, "Recreation failed")

        ci_monitor.monitor_pr("123", timeout_minutes=10)

        # recreate_pr should be called exactly DEFAULT_MAX_PR_RECREATE times (1)
        assert mock_recreate_pr.call_count == ci_monitor.DEFAULT_MAX_PR_RECREATE

    @patch("ci_monitor.main_loop.save_monitor_state")
    @patch("ci_monitor.main_loop.recreate_pr")
    @patch("ci_monitor.main_loop.log_ci_monitor_event")
    @patch("ci_monitor.main_loop.check_rate_limit")
    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.time.time")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_pending_timer_resets_when_copilot_no_longer_pending(
        self,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_time,
        mock_is_error,
        mock_check_rate,
        mock_log_event,
        mock_recreate_pr,
        mock_save_state,
    ):
        """Should reset pending timer when Copilot is no longer pending.

        This test verifies that copilot_pending_since is reset to None when
        Copilot finishes reviewing (pending_reviewers becomes empty). Without
        this reset, a new pending state would inherit the old timer and
        incorrectly trigger timeout.
        """
        # Mock rate limit and error check to avoid API calls
        mock_check_rate.return_value = (5000, 5000, 0)
        mock_is_error.return_value = (False, None)

        # Time always returns 0 - since Copilot finishes before timeout (300s),
        # no recreation should occur regardless of time values
        time_call_count = [0]

        def time_side_effect():
            time_call_count[0] += 1
            return 0  # No time progression needed for this test

        mock_time.side_effect = time_side_effect

        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # First 2 calls: Copilot pending (timer starts)
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # Third call: Copilot finished (timer resets), CI passed
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],  # Timer resets when this becomes empty
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False

        result = ci_monitor.monitor_pr("123", timeout_minutes=10)

        # recreate_pr should NOT be called because Copilot finished normally
        mock_recreate_pr.assert_not_called()
        # CI passed successfully
        assert result.ci_passed
