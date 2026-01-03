#!/usr/bin/env python3
"""Unit tests for ci_monitor.monitor module."""

import json
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
    EventType,
    MergeState,
    MultiPREvent,
    PRState,
    check_once,
    create_event,
    get_pr_state,
    has_copilot_or_codex_reviewer,
    monitor_multiple_prs,
)


class TestGetPrStateErrorHandling:
    """Tests for get_pr_state error handling (Issue #882)."""

    @patch("ci_monitor.github_api.run_gh_command")
    @patch("ci_monitor.github_api.run_gh_command_with_error")
    def test_returns_error_on_merge_state_failure(self, mock_with_error, mock_run):
        """Test that get_pr_state returns error message when merge state fetch fails."""
        mock_with_error.return_value = (False, "", "API rate limit exceeded")
        # run_gh_command won't be called if first call fails

        state, error = get_pr_state("123")

        assert state is None
        assert error is not None
        assert "rate limit" in error.lower()

    @patch("ci_monitor.github_api.run_gh_command")
    @patch("ci_monitor.github_api.run_gh_command_with_error")
    def test_returns_state_with_none_error_on_success(self, mock_with_error, mock_run):
        """Test that get_pr_state returns (state, None) on success."""
        mock_with_error.return_value = (True, "CLEAN", "")
        mock_run.side_effect = [
            (True, "[]"),
            (True, json.dumps([{"name": "CI", "state": "SUCCESS"}])),
        ]

        state, error = get_pr_state("123")

        assert state is not None
        assert error is None
        assert state.merge_state == MergeState.CLEAN
        assert state.check_status == CheckStatus.SUCCESS


class TestCheckOnce:
    """Tests for check_once function."""

    @patch("ci_monitor.main_loop.get_pr_state")
    def test_check_once_error_when_state_is_none(self, mock_get_pr_state):
        """Test that ERROR event is returned when PR state cannot be fetched."""
        mock_get_pr_state.return_value = (None, "mock error")

        event = check_once("123", [])

        assert event is not None
        assert event.event_type == EventType.ERROR
        assert "Failed to fetch PR state" in event.message

    @patch("ci_monitor.main_loop.get_pr_state")
    def test_check_once_behind_detected(self, mock_get_pr_state):
        """Test BEHIND_DETECTED event when branch is behind."""
        mock_get_pr_state.return_value = (
            PRState(
                merge_state=MergeState.BEHIND,
                pending_reviewers=[],
                check_status=CheckStatus.PENDING,
            ),
            None,
        )

        event = check_once("123", [])

        assert event is not None
        assert event.event_type == EventType.BEHIND_DETECTED
        assert "behind" in event.message.lower()
        assert "gh pr update-branch 123 --rebase" in event.suggested_action

    @patch("ci_monitor.main_loop.get_pr_state")
    def test_check_once_dirty_detected(self, mock_get_pr_state):
        """Test DIRTY_DETECTED event when conflict exists."""
        mock_get_pr_state.return_value = (
            PRState(
                merge_state=MergeState.DIRTY,
                pending_reviewers=[],
                check_status=CheckStatus.PENDING,
            ),
            None,
        )

        event = check_once("123", [])

        assert event is not None
        assert event.event_type == EventType.DIRTY_DETECTED
        assert "conflict" in event.message.lower()

    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_check_once_review_completed(self, mock_get_pr_state, mock_get_comments):
        """Test REVIEW_COMPLETED event when AI reviewer finishes."""
        mock_get_pr_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=[],  # AI reviewer no longer pending
                check_status=CheckStatus.PENDING,
            ),
            None,
        )
        mock_get_comments.return_value = [
            {"path": "src/main.py", "line": 10, "body": "Consider refactoring"}
        ]

        # Previous state had Copilot as reviewer
        event = check_once("123", ["Copilot"])

        assert event is not None
        assert event.event_type == EventType.REVIEW_COMPLETED
        assert "1 comments" in event.message
        assert event.details["comment_count"] == 1

    @patch("ci_monitor.main_loop.get_pr_state")
    def test_check_once_ci_passed(self, mock_get_pr_state):
        """Test CI_PASSED event when all checks succeed."""
        mock_get_pr_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=[],
                check_status=CheckStatus.SUCCESS,
                check_details=[{"name": "build", "state": "SUCCESS"}],
            ),
            None,
        )

        event = check_once("123", [])

        assert event is not None
        assert event.event_type == EventType.CI_PASSED
        assert "passed" in event.message.lower()

    @patch("ci_monitor.main_loop.get_pr_state")
    def test_check_once_ci_failed(self, mock_get_pr_state):
        """Test CI_FAILED event when checks fail."""
        mock_get_pr_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=[],
                check_status=CheckStatus.FAILURE,
                check_details=[
                    {"name": "build", "state": "SUCCESS"},
                    {"name": "test", "state": "FAILURE"},
                ],
            ),
            None,
        )

        event = check_once("123", [])

        assert event is not None
        assert event.event_type == EventType.CI_FAILED
        assert "test" in event.message
        assert "test" in event.details["failed_checks"]

    @patch("ci_monitor.main_loop.get_pr_state")
    def test_check_once_ci_cancelled(self, mock_get_pr_state):
        """Test CI_FAILED event when CI is cancelled."""
        mock_get_pr_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=[],
                check_status=CheckStatus.CANCELLED,
                check_details=[{"name": "build", "state": "CANCELLED"}],
            ),
            None,
        )

        event = check_once("123", [])

        assert event is not None
        assert event.event_type == EventType.CI_FAILED
        assert "cancelled" in event.message.lower()

    @patch("ci_monitor.main_loop.get_pr_state")
    def test_check_once_no_event_when_pending(self, mock_get_pr_state):
        """Test that None is returned when CI is still pending."""
        mock_get_pr_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=["Copilot"],  # Still has AI reviewer
                check_status=CheckStatus.PENDING,
                check_details=[{"name": "build", "state": "IN_PROGRESS"}],
            ),
            None,
        )

        # Copilot still pending (previous state also had Copilot)
        event = check_once("123", ["Copilot"])

        assert event is None


class TestGetPrState:
    """Tests for get_pr_state function, especially SKIPPED handling."""

    @patch("ci_monitor.github_api.run_gh_command")
    @patch("ci_monitor.github_api.run_gh_command_with_error")
    def test_skipped_checks_treated_as_success(self, mock_run_gh_with_error, mock_run_gh):
        """Test that SKIPPED checks are treated as success."""
        # First call uses run_gh_command_with_error: get merge state
        mock_run_gh_with_error.return_value = (True, "CLEAN", "")
        # Remaining calls use run_gh_command
        mock_run_gh.side_effect = [
            # Second call: get requested reviewers via gh api
            (True, "[]"),
            # Third call: get CI checks (mix of SUCCESS and SKIPPED)
            (
                True,
                json.dumps(
                    [
                        {"name": "CI / ci", "state": "SUCCESS"},
                        {"name": "E2E", "state": "SKIPPED"},
                        {"name": "Deploy", "state": "SKIPPED"},
                    ]
                ),
            ),
        ]

        state, error = get_pr_state("123")

        assert state is not None
        assert error is None
        assert state.check_status == CheckStatus.SUCCESS

    @patch("ci_monitor.github_api.run_gh_command")
    @patch("ci_monitor.github_api.run_gh_command_with_error")
    def test_all_skipped_checks_treated_as_success(self, mock_run_gh_with_error, mock_run_gh):
        """Test that all SKIPPED checks are treated as success."""
        mock_run_gh_with_error.return_value = (True, "CLEAN", "")
        mock_run_gh.side_effect = [
            (True, "[]"),
            (
                True,
                json.dumps(
                    [
                        {"name": "E2E", "state": "SKIPPED"},
                        {"name": "Deploy", "state": "SKIPPED"},
                    ]
                ),
            ),
        ]

        state, error = get_pr_state("123")

        assert state is not None
        assert error is None
        assert state.check_status == CheckStatus.SUCCESS

    @patch("ci_monitor.github_api.run_gh_command")
    @patch("ci_monitor.github_api.run_gh_command_with_error")
    def test_pending_with_skipped_stays_pending(self, mock_run_gh_with_error, mock_run_gh):
        """Test that PENDING checks with SKIPPED stays PENDING."""
        mock_run_gh_with_error.return_value = (True, "CLEAN", "")
        mock_run_gh.side_effect = [
            (True, "[]"),
            (
                True,
                json.dumps(
                    [
                        {"name": "CI / ci", "state": "IN_PROGRESS"},
                        {"name": "E2E", "state": "SKIPPED"},
                    ]
                ),
            ),
        ]

        state, error = get_pr_state("123")

        assert state is not None
        assert error is None
        assert state.check_status == CheckStatus.PENDING

    @patch("ci_monitor.github_api.run_gh_command")
    @patch("ci_monitor.github_api.run_gh_command_with_error")
    def test_failure_with_skipped_is_failure(self, mock_run_gh_with_error, mock_run_gh):
        """Test that FAILURE takes precedence over SKIPPED."""
        mock_run_gh_with_error.return_value = (True, "CLEAN", "")
        mock_run_gh.side_effect = [
            (True, "[]"),
            (
                True,
                json.dumps(
                    [
                        {"name": "CI / ci", "state": "FAILURE"},
                        {"name": "E2E", "state": "SKIPPED"},
                    ]
                ),
            ),
        ]

        state, error = get_pr_state("123")

        assert state is not None
        assert error is None
        assert state.check_status == CheckStatus.FAILURE

    @patch("ci_monitor.github_api.run_gh_command")
    @patch("ci_monitor.github_api.run_gh_command_with_error")
    def test_copilot_detected_in_requested_reviewers(self, mock_run_gh_with_error, mock_run_gh):
        """Test that Copilot is detected in requested_reviewers from gh api."""
        mock_run_gh_with_error.return_value = (True, "CLEAN", "")
        mock_run_gh.side_effect = [
            (True, '["Copilot"]'),  # Copilot in requested_reviewers
            (True, json.dumps([{"name": "CI", "state": "SUCCESS"}])),
        ]

        state, error = get_pr_state("123")

        assert state is not None
        assert error is None
        assert state.pending_reviewers == ["Copilot"]
        assert has_copilot_or_codex_reviewer(state.pending_reviewers)


class TestMonitorPrWaitReview:
    """Tests for monitor_pr with --wait-review option."""

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_continues_after_ci_passes_when_reviewer_pending(
        self, mock_get_pr_state, mock_get_comments, mock_sleep
    ):
        """Test that it continues after CI passes if AI reviewer still pending."""
        # First call: CI passed but Copilot still reviewing
        # Second call: CI passed, Copilot finished
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state - CI pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 2:
                # CI passed but Copilot still reviewing
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            elif call_count == 3:
                # CI passed, Copilot still reviewing (continuing to wait)
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            else:
                # Finally, Copilot finished
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_get_comments.return_value = [{"path": "test.py", "line": 1, "body": "comment"}]

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert result.ci_passed
        assert result.review_completed
        # Should have called get_pr_state multiple times (at least 4)
        assert mock_get_pr_state.call_count >= 4

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_exits_on_ci_pass_with_human_reviewer_only(
        self, mock_get_pr_state, mock_is_codex_pending, mock_sleep
    ):
        """Test that with human reviewer only, it exits when CI passes (no AI review to wait for)."""
        mock_get_pr_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=["human-reviewer"],  # Human reviewer (no auto-enable)
                check_status=CheckStatus.SUCCESS,  # CI passed
            ),
            None,
        )
        mock_is_codex_pending.return_value = False  # No Codex Cloud review pending

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert result.ci_passed
        # review_completed should be False since we didn't wait
        assert not result.review_completed
        # Should only call get_pr_state twice (initial + first loop)
        assert mock_get_pr_state.call_count == 2

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_proceeds_when_no_ai_reviewers(self, mock_get_pr_state, mock_sleep):
        """Test that it proceeds if no AI reviewers detected."""
        mock_get_pr_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=["human-user"],  # No AI reviewers
                check_status=CheckStatus.SUCCESS,
            ),
            None,
        )

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert result.ci_passed
        # review_completed should be False since there were no AI reviewers
        assert not result.review_completed

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_message_includes_review_completed(
        self, mock_get_pr_state, mock_get_comments, mock_sleep
    ):
        """Test that result message mentions review completion when review is completed."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.SUCCESS
                        if call_count == 2
                        else CheckStatus.PENDING,
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
        mock_get_comments.return_value = []

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert "review completed" in result.message


class TestMonitorMultiplePRs:
    """Tests for monitor_multiple_prs function."""

    @patch("ci_monitor.events.log")
    @patch("ci_monitor.monitor._monitor_single_pr_for_event")
    def test_returns_on_first_actionable_event(self, mock_monitor, mock_log):
        """Test that function returns immediately on first actionable event."""

        # Set up mock to return an event for the first PR
        def mock_monitor_side_effect(pr_number, timeout_minutes, stop_event):
            return MultiPREvent(
                pr_number=pr_number,
                event=create_event(EventType.CI_PASSED, pr_number, "CI passed"),
                state=PRState(
                    merge_state=MergeState.CLEAN,
                    pending_reviewers=[],
                    check_status=CheckStatus.SUCCESS,
                ),
            )

        mock_monitor.side_effect = mock_monitor_side_effect

        results = monitor_multiple_prs(["100", "101", "102"], timeout_minutes=1)

        # Should return with only 1 result (first event detected)
        assert len(results) == 1
        assert results[0].event is not None
        assert results[0].event.event_type == EventType.CI_PASSED

    @patch("ci_monitor.events.log")
    @patch("ci_monitor.monitor._monitor_single_pr_for_event")
    def test_handles_monitor_exception(self, mock_monitor, mock_log):
        """Test that exceptions from individual monitors are caught and returned immediately."""

        def mock_monitor_side_effect(pr_number, timeout_minutes, stop_event):
            if pr_number == "200":
                raise RuntimeError("API error")
            return MultiPREvent(
                pr_number=pr_number,
                event=create_event(EventType.CI_PASSED, pr_number, "CI passed"),
                state=None,
            )

        mock_monitor.side_effect = mock_monitor_side_effect

        results = monitor_multiple_prs(["200", "201"], timeout_minutes=1)

        # Should return immediately on error (1 result)
        assert len(results) == 1

        # The result should be an error
        error_result = results[0]
        assert error_result.event.event_type == EventType.ERROR
        assert "API error" in error_result.event.message

    @patch("ci_monitor.events.log")
    @patch("ci_monitor.monitor._monitor_single_pr_for_event")
    def test_empty_pr_list(self, mock_monitor, mock_log):
        """Test monitoring with empty PR list returns empty results."""
        results = monitor_multiple_prs([], timeout_minutes=1)

        assert len(results) == 0
        mock_monitor.assert_not_called()


class TestMonitorSinglePRForEvent:
    """Tests for _monitor_single_pr_for_event function."""

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.monitor.check_once")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_returns_on_first_event(self, mock_get_state, mock_check_once, mock_sleep):
        """Test that function returns immediately when an event is detected."""
        mock_get_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=[],
                check_status=CheckStatus.SUCCESS,
            ),
            None,
        )
        mock_check_once.return_value = create_event(EventType.CI_PASSED, "123", "CI passed")

        result = ci_monitor._monitor_single_pr_for_event("123", timeout_minutes=1)

        assert result.pr_number == "123"
        assert result.event.event_type == EventType.CI_PASSED
        # Should not sleep if event found on first check
        mock_sleep.assert_not_called()

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.monitor.check_once")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.time.time")
    def test_returns_timeout_when_no_event(
        self, mock_time, mock_get_state, mock_check_once, mock_sleep
    ):
        """Test that function returns TIMEOUT event when timeout expires."""
        # Simulate timeout by incrementing time
        call_count = [0]

        def time_side_effect():
            call_count[0] += 1
            # First call: start time
            # Second call: elapsed > timeout
            if call_count[0] == 1:
                return 0
            return 120  # 2 minutes elapsed, greater than 1 minute timeout

        mock_time.side_effect = time_side_effect
        mock_get_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=[],
                check_status=CheckStatus.PENDING,
            ),
            None,
        )
        mock_check_once.return_value = None  # No event

        result = ci_monitor._monitor_single_pr_for_event("456", timeout_minutes=1)

        assert result.pr_number == "456"
        assert result.event.event_type == EventType.TIMEOUT
        assert "Timeout" in result.event.message

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.monitor.check_once")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.time.time")
    def test_continues_polling_until_event(
        self, mock_time, mock_get_state, mock_check_once, mock_sleep
    ):
        """Test that function polls until an event is found."""
        # Simulate multiple polls before event
        poll_count = [0]

        def time_side_effect():
            # Always return time within timeout
            return poll_count[0] * 5  # 5 seconds per poll

        mock_time.side_effect = time_side_effect

        def check_once_side_effect(pr_number, previous_reviewers):
            poll_count[0] += 1
            if poll_count[0] >= 3:  # Event on third poll
                return create_event(EventType.CI_PASSED, pr_number, "CI passed")
            return None

        mock_get_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=[],
                check_status=CheckStatus.PENDING,
            ),
            None,
        )
        mock_check_once.side_effect = check_once_side_effect

        result = ci_monitor._monitor_single_pr_for_event("789", timeout_minutes=10)

        assert result.pr_number == "789"
        assert result.event.event_type == EventType.CI_PASSED
        # Should have polled 3 times, sleeping twice (not on first poll)
        assert mock_sleep.call_count == 2

    @patch("ci_monitor.monitor.check_once")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_stops_when_stop_event_is_set(self, mock_get_state, mock_check_once):
        """Test that function returns early when stop_event is set."""
        import threading

        mock_get_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=[],
                check_status=CheckStatus.PENDING,
            ),
            None,
        )
        mock_check_once.return_value = None  # No event

        # Create a stop event and set it immediately
        stop_event = threading.Event()
        stop_event.set()

        result = ci_monitor._monitor_single_pr_for_event(
            "999", timeout_minutes=10, stop_event=stop_event
        )

        # Should return with no event since stop was signaled
        assert result.pr_number == "999"
        assert result.event is None


class TestLogCiMonitorEvent:
    """Tests for log_ci_monitor_event function (Issue #1411)."""

    def test_logs_monitor_start_event(self):
        """Test logging monitor_start action."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_ci_monitor_event("123", "monitor_start", "started")
            mock_log.assert_called_once()
            call_kwargs = mock_log.call_args.kwargs
            assert call_kwargs["hook_name"] == "ci-monitor"
            assert call_kwargs["decision"] == "monitor_start"
            assert "PR #123: monitor_start - started" in call_kwargs["reason"]
            assert call_kwargs["details"]["pr_number"] == "123"
            assert call_kwargs["details"]["action"] == "monitor_start"
            assert call_kwargs["details"]["result"] == "started"

    def test_logs_rebase_success(self):
        """Test logging rebase action with success result."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_ci_monitor_event("456", "rebase", "success")
            call_kwargs = mock_log.call_args.kwargs
            assert call_kwargs["decision"] == "rebase"
            assert call_kwargs["details"]["result"] == "success"

    def test_logs_rebase_failure(self):
        """Test logging rebase action with failure result."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_ci_monitor_event("456", "rebase", "failure")
            call_kwargs = mock_log.call_args.kwargs
            assert call_kwargs["details"]["result"] == "failure"

    def test_logs_ci_state_change(self):
        """Test logging ci_state_change action with various results."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            results = ["success", "failure", "cancelled"]
            for result in results:
                ci_monitor.log_ci_monitor_event("789", "ci_state_change", result)

            # Verify each call using call_args_list for clarity
            assert mock_log.call_count == len(results)
            for i, result in enumerate(results):
                call_kwargs = mock_log.call_args_list[i].kwargs
                assert call_kwargs["decision"] == "ci_state_change"
                assert call_kwargs["details"]["result"] == result

    def test_logs_monitor_complete_success(self):
        """Test logging monitor_complete action with success result."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_ci_monitor_event("100", "monitor_complete", "success")
            call_kwargs = mock_log.call_args.kwargs
            assert call_kwargs["decision"] == "monitor_complete"
            assert call_kwargs["details"]["result"] == "success"

    def test_logs_monitor_complete_timeout(self):
        """Test logging monitor_complete action with timeout result."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_ci_monitor_event("100", "monitor_complete", "timeout")
            call_kwargs = mock_log.call_args.kwargs
            assert call_kwargs["details"]["result"] == "timeout"

    def test_merges_additional_details(self):
        """Test that additional details are merged into the log entry."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            extra_details = {"rebase_count": 3, "duration_seconds": 120}
            ci_monitor.log_ci_monitor_event("123", "monitor_complete", "success", extra_details)
            call_kwargs = mock_log.call_args.kwargs
            details = call_kwargs["details"]
            assert details["rebase_count"] == 3
            assert details["duration_seconds"] == 120
            assert details["pr_number"] == "123"

    def test_handles_none_details(self):
        """Test that None details are handled correctly."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_ci_monitor_event("123", "monitor_start", "started", None)
            mock_log.assert_called_once()
            # Should not raise and should log successfully

    def test_handles_empty_details(self):
        """Test that empty details dict is handled correctly."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_ci_monitor_event("123", "monitor_start", "started", {})
            mock_log.assert_called_once()
            # Verify only base fields are present (no extra fields from empty dict)
            details = mock_log.call_args.kwargs["details"]
            assert set(details.keys()) == {"pr_number", "action", "result"}

    def test_sanitizes_pr_number_with_control_chars(self):
        """Test that PR number is sanitized."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_ci_monitor_event("123\x00", "monitor_start", "started")
            call_kwargs = mock_log.call_args.kwargs
            assert call_kwargs["details"]["pr_number"] == "123"
            assert "PR #123:" in call_kwargs["reason"]

    def test_sanitizes_result_with_control_chars(self):
        """Test that result is sanitized."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_ci_monitor_event("123", "monitor_start", "started\n\r")
            call_kwargs = mock_log.call_args.kwargs
            assert call_kwargs["details"]["result"] == "started"

    def test_sanitizes_details_with_control_chars(self):
        """Test that details values are sanitized."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_ci_monitor_event(
                "123", "rebase", "success", {"message": "test\x00value"}
            )
            call_kwargs = mock_log.call_args.kwargs
            assert call_kwargs["details"]["message"] == "testvalue"
