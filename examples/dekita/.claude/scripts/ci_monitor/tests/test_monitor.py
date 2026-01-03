"""Tests for ci_monitor.monitor module.

This module tests the core monitoring functions extracted from ci-monitor.py
as part of Issue #1765 refactoring (Phase 7).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ci_monitor.models import (
    CheckStatus,
    EventType,
    MergeState,
    MonitorEvent,
    PRState,
)
from ci_monitor.monitor import (
    _sanitize_for_log,
    check_once,
    check_self_reference,
    get_issue_incomplete_criteria,
    get_pr_closes_issues,
    get_wait_time_suggestions,
    log_ci_monitor_event,
    monitor_notify_only,
    show_wait_time_hint,
)


class TestSanitizeForLog:
    """Tests for _sanitize_for_log function."""

    def test_sanitizes_string_control_chars(self) -> None:
        result = _sanitize_for_log("hello\x00world\x1f")
        assert result == "helloworld"

    def test_preserves_tab(self) -> None:
        result = _sanitize_for_log("hello\tworld")
        assert result == "hello\tworld"

    def test_handles_list(self) -> None:
        result = _sanitize_for_log(["hello\x00", "world\x1f"])
        assert result == ["hello", "world"]

    def test_handles_dict(self) -> None:
        # Note: only values are sanitized, not keys
        result = _sanitize_for_log({"key": "value\x1f"})
        assert result == {"key": "value"}

    def test_handles_non_string(self) -> None:
        assert _sanitize_for_log(123) == 123
        assert _sanitize_for_log(None) is None


class TestLogCiMonitorEvent:
    """Tests for log_ci_monitor_event function."""

    @patch("ci_monitor.monitor.log_hook_execution")
    def test_logs_event_with_details(self, mock_log: MagicMock) -> None:
        log_ci_monitor_event(
            pr_number="123",
            action="monitor_start",
            result="started",
            details={"interval": 30},
        )

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[1]["hook_name"] == "ci-monitor"
        assert call_args[1]["decision"] == "monitor_start"
        assert "PR #123" in call_args[1]["reason"]

    @patch("ci_monitor.monitor.log_hook_execution")
    def test_logs_event_without_details(self, mock_log: MagicMock) -> None:
        log_ci_monitor_event(
            pr_number="456",
            action="rebase",
            result="success",
        )

        mock_log.assert_called_once()


class TestGetPrClosesIssues:
    """Tests for get_pr_closes_issues function."""

    @patch("ci_monitor.monitor.run_gh_command")
    def test_finds_closes_keyword(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (True, "This PR Closes #123")
        result = get_pr_closes_issues("1")
        assert "123" in result

    @patch("ci_monitor.monitor.run_gh_command")
    def test_finds_multiple_issues(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (True, "Fixes #123, #456")
        result = get_pr_closes_issues("1")
        assert "123" in result
        assert "456" in result

    @patch("ci_monitor.monitor.run_gh_command")
    def test_finds_resolves_keyword(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (True, "Resolves #789")
        result = get_pr_closes_issues("1")
        assert "789" in result

    @patch("ci_monitor.monitor.run_gh_command")
    def test_returns_empty_on_failure(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (False, "")
        result = get_pr_closes_issues("1")
        assert result == []

    @patch("ci_monitor.monitor.run_gh_command")
    def test_returns_empty_when_no_closing_keywords(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (True, "Some PR body without closing keywords")
        result = get_pr_closes_issues("1")
        assert result == []


class TestGetIssueIncompleteCriteria:
    """Tests for get_issue_incomplete_criteria function."""

    @patch("ci_monitor.monitor.run_gh_command")
    def test_finds_incomplete_checkboxes(self, mock_cmd: MagicMock) -> None:
        body = "- [ ] First task\n- [x] Second task\n- [ ] Third task"
        mock_cmd.return_value = (True, json.dumps({"body": body, "state": "OPEN"}))
        result = get_issue_incomplete_criteria("123")
        assert len(result) == 2
        assert "First task" in result[0]

    @patch("ci_monitor.monitor.run_gh_command")
    def test_skips_closed_issues(self, mock_cmd: MagicMock) -> None:
        body = "- [ ] Incomplete task"
        mock_cmd.return_value = (True, json.dumps({"body": body, "state": "CLOSED"}))
        result = get_issue_incomplete_criteria("123")
        assert result == []

    @patch("ci_monitor.monitor.run_gh_command")
    def test_returns_empty_on_failure(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (False, "")
        result = get_issue_incomplete_criteria("123")
        assert result == []

    @patch("ci_monitor.monitor.run_gh_command")
    def test_truncates_long_criteria(self, mock_cmd: MagicMock) -> None:
        body = "- [ ] This is a very long task description that should be truncated"
        mock_cmd.return_value = (True, json.dumps({"body": body, "state": "OPEN"}))
        result = get_issue_incomplete_criteria("123")
        assert len(result) == 1
        assert result[0].endswith("...」")


class TestGetWaitTimeSuggestions:
    """Tests for get_wait_time_suggestions function."""

    @patch("ci_monitor.monitor.get_pr_closes_issues")
    @patch("ci_monitor.monitor.get_review_comments")
    @patch("ci_monitor.monitor.get_unresolved_threads")
    def test_returns_empty_when_no_issues(
        self,
        mock_threads: MagicMock,
        mock_comments: MagicMock,
        mock_closes: MagicMock,
    ) -> None:
        mock_threads.return_value = []
        mock_comments.return_value = []
        mock_closes.return_value = []
        result = get_wait_time_suggestions("123")
        assert result == []

    @patch("ci_monitor.monitor.get_pr_closes_issues")
    @patch("ci_monitor.monitor.get_review_comments")
    @patch("ci_monitor.monitor.get_unresolved_threads")
    def test_includes_unresolved_threads(
        self,
        mock_threads: MagicMock,
        mock_comments: MagicMock,
        mock_closes: MagicMock,
    ) -> None:
        mock_threads.return_value = [{"id": 1}, {"id": 2}]
        mock_comments.return_value = []
        mock_closes.return_value = []
        result = get_wait_time_suggestions("123")
        assert len(result) == 1
        assert "未解決スレッド 2件" in result[0]


class TestShowWaitTimeHint:
    """Tests for show_wait_time_hint function."""

    @patch("ci_monitor.monitor.get_wait_time_suggestions")
    def test_skips_on_iteration_zero(self, mock_suggestions: MagicMock) -> None:
        show_wait_time_hint("123", iteration=0)
        mock_suggestions.assert_not_called()

    @patch("ci_monitor.monitor.get_wait_time_suggestions")
    def test_skips_when_not_interval(self, mock_suggestions: MagicMock) -> None:
        show_wait_time_hint("123", iteration=1, hint_interval=3)
        mock_suggestions.assert_not_called()

    @patch("ci_monitor.monitor.get_wait_time_suggestions")
    def test_calls_on_interval(self, mock_suggestions: MagicMock) -> None:
        mock_suggestions.return_value = []
        show_wait_time_hint("123", iteration=3, hint_interval=3)
        mock_suggestions.assert_called_once_with("123")


class TestCheckOnce:
    """Tests for check_once function."""

    @patch("ci_monitor.monitor.get_pr_state")
    def test_returns_error_on_state_failure(self, mock_state: MagicMock) -> None:
        mock_state.return_value = (None, "API error")
        result = check_once("123", [])
        assert result is not None
        assert result.event_type == EventType.ERROR
        assert "API error" in result.message

    @patch("ci_monitor.monitor.get_pr_state")
    def test_detects_behind_state(self, mock_state: MagicMock) -> None:
        state = PRState(
            merge_state=MergeState.BEHIND,
            pending_reviewers=[],
            check_status=CheckStatus.PENDING,
            check_details=[],
        )
        mock_state.return_value = (state, None)
        result = check_once("123", [])
        assert result is not None
        assert result.event_type == EventType.BEHIND_DETECTED

    @patch("ci_monitor.monitor.get_pr_state")
    def test_detects_dirty_state(self, mock_state: MagicMock) -> None:
        state = PRState(
            merge_state=MergeState.DIRTY,
            pending_reviewers=[],
            check_status=CheckStatus.PENDING,
            check_details=[],
        )
        mock_state.return_value = (state, None)
        result = check_once("123", [])
        assert result is not None
        assert result.event_type == EventType.DIRTY_DETECTED

    @patch("ci_monitor.monitor.get_pr_state")
    def test_detects_ci_passed(self, mock_state: MagicMock) -> None:
        state = PRState(
            merge_state=MergeState.CLEAN,
            pending_reviewers=[],
            check_status=CheckStatus.SUCCESS,
            check_details=[{"name": "CI", "state": "SUCCESS"}],
        )
        mock_state.return_value = (state, None)
        result = check_once("123", [])
        assert result is not None
        assert result.event_type == EventType.CI_PASSED

    @patch("ci_monitor.monitor.get_pr_state")
    def test_detects_ci_failed(self, mock_state: MagicMock) -> None:
        state = PRState(
            merge_state=MergeState.CLEAN,
            pending_reviewers=[],
            check_status=CheckStatus.FAILURE,
            check_details=[{"name": "CI", "state": "FAILURE"}],
        )
        mock_state.return_value = (state, None)
        result = check_once("123", [])
        assert result is not None
        assert result.event_type == EventType.CI_FAILED

    @patch("ci_monitor.monitor.get_pr_state")
    def test_returns_none_when_pending(self, mock_state: MagicMock) -> None:
        state = PRState(
            merge_state=MergeState.CLEAN,
            pending_reviewers=[],
            check_status=CheckStatus.PENDING,
            check_details=[],
        )
        mock_state.return_value = (state, None)
        result = check_once("123", [])
        assert result is None

    @patch("ci_monitor.monitor.log_review_comments_to_quality_log")
    @patch("ci_monitor.monitor.get_review_comments")
    @patch("ci_monitor.monitor.is_copilot_review_error")
    @patch("ci_monitor.monitor.has_copilot_or_codex_reviewer")
    @patch("ci_monitor.monitor.get_pr_state")
    def test_detects_review_completed(
        self,
        mock_state: MagicMock,
        mock_has_ai: MagicMock,
        mock_error: MagicMock,
        mock_comments: MagicMock,
        mock_log: MagicMock,
    ) -> None:
        state = PRState(
            merge_state=MergeState.CLEAN,
            pending_reviewers=[],
            check_status=CheckStatus.PENDING,
            check_details=[],
        )
        mock_state.return_value = (state, None)
        mock_has_ai.side_effect = [True, False]  # had reviewer, now doesn't
        mock_error.return_value = (False, None)
        mock_comments.return_value = [{"body": "LGTM"}]
        result = check_once("123", ["copilot[bot]"])
        assert result is not None
        assert result.event_type == EventType.REVIEW_COMPLETED


class TestMonitorNotifyOnly:
    """Tests for monitor_notify_only function."""

    @patch("ci_monitor.monitor.emit_event")
    @patch("ci_monitor.monitor.get_pr_state")
    def test_emits_error_on_state_failure(
        self, mock_state: MagicMock, mock_emit: MagicMock
    ) -> None:
        mock_state.return_value = (None, "Error")
        result = monitor_notify_only("123")
        assert result == 0
        mock_emit.assert_called_once()

    @patch("ci_monitor.monitor.check_once")
    @patch("ci_monitor.monitor.get_pr_state")
    def test_emits_event_when_detected(self, mock_state: MagicMock, mock_check: MagicMock) -> None:
        state = PRState(
            merge_state=MergeState.CLEAN,
            pending_reviewers=[],
            check_status=CheckStatus.SUCCESS,
            check_details=[],
        )
        mock_state.return_value = (state, None)
        mock_check.return_value = MonitorEvent(
            event_type=EventType.CI_PASSED,
            pr_number="123",
            timestamp="2025-01-01T00:00:00",
            message="CI passed",
        )

        with patch("ci_monitor.monitor.emit_event") as mock_emit:
            result = monitor_notify_only("123")
            assert result == 0
            mock_emit.assert_called_once()

    @patch("ci_monitor.monitor.check_once")
    @patch("ci_monitor.monitor.get_pr_state")
    @patch("builtins.print")
    def test_outputs_status_when_no_event(
        self, mock_print: MagicMock, mock_state: MagicMock, mock_check: MagicMock
    ) -> None:
        state = PRState(
            merge_state=MergeState.CLEAN,
            pending_reviewers=[],
            check_status=CheckStatus.PENDING,
            check_details=[],
        )
        mock_state.return_value = (state, None)
        mock_check.return_value = None

        result = monitor_notify_only("123")
        assert result == 1
        mock_print.assert_called_once()
        # Verify JSON output
        printed_output = mock_print.call_args[0][0]
        data = json.loads(printed_output)
        assert data["type"] == "status"
        assert data["pr_number"] == "123"


class TestCheckSelfReference:
    """Tests for check_self_reference function."""

    @patch("ci_monitor.monitor.get_pr_changed_files")
    def test_returns_true_when_ci_monitor_changed(self, mock_files: MagicMock) -> None:
        mock_files.return_value = {"path/to/ci-monitor.py", "other.py"}
        result = check_self_reference("123")
        assert result is True

    @patch("ci_monitor.monitor.get_pr_changed_files")
    def test_returns_false_when_ci_monitor_not_changed(self, mock_files: MagicMock) -> None:
        mock_files.return_value = {"other.py", "another.py"}
        result = check_self_reference("123")
        assert result is False

    @patch("ci_monitor.monitor.get_pr_changed_files")
    def test_returns_false_on_api_failure(self, mock_files: MagicMock) -> None:
        mock_files.return_value = None
        result = check_self_reference("123")
        assert result is False

    @patch("ci_monitor.monitor.get_pr_changed_files")
    def test_does_not_match_similar_filename(self, mock_files: MagicMock) -> None:
        mock_files.return_value = {"my-ci-monitor.py"}  # Ends with ci-monitor.py, matches
        result = check_self_reference("123")
        assert result is True
