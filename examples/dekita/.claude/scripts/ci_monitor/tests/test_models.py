"""Tests for ci_monitor.models module."""

import json

from ci_monitor.models import (
    CheckStatus,
    ClassifiedComments,
    CodexReviewRequest,
    EventType,
    IntervalDirection,
    MergeState,
    MonitorEvent,
    MonitorResult,
    MultiPREvent,
    PRState,
    RateLimitEventType,
    RebaseResult,
    RetryWaitStatus,
    has_unresolved_threads,
)


class TestEventType:
    """Tests for EventType enum."""

    def test_all_event_types_exist(self):
        """Verify all expected event types are defined."""
        expected = [
            "BEHIND_DETECTED",
            "DIRTY_DETECTED",
            "REVIEW_COMPLETED",
            "REVIEW_ERROR",
            "CI_FAILED",
            "CI_PASSED",
            "TIMEOUT",
            "ERROR",
        ]
        actual = [e.name for e in EventType]
        assert actual == expected

    def test_event_type_values(self):
        """Verify event type values match names."""
        for event_type in EventType:
            assert event_type.value == event_type.name


class TestCheckStatus:
    """Tests for CheckStatus enum."""

    def test_all_check_statuses_exist(self):
        """Verify all expected check statuses are defined."""
        expected = ["PENDING", "SUCCESS", "FAILURE", "CANCELLED"]
        actual = [s.name for s in CheckStatus]
        assert actual == expected


class TestMergeState:
    """Tests for MergeState enum."""

    def test_all_merge_states_exist(self):
        """Verify all expected merge states are defined."""
        expected = ["CLEAN", "BEHIND", "DIRTY", "BLOCKED", "UNKNOWN"]
        actual = [s.name for s in MergeState]
        assert actual == expected


class TestRetryWaitStatus:
    """Tests for RetryWaitStatus enum."""

    def test_all_statuses_exist(self):
        """Verify all expected statuses are defined."""
        expected = ["CONTINUE", "TIMEOUT"]
        actual = [s.name for s in RetryWaitStatus]
        assert actual == expected

    def test_status_values(self):
        """Verify status values match names."""
        for status in RetryWaitStatus:
            assert status.value == status.name


class TestRateLimitEventType:
    """Tests for RateLimitEventType enum."""

    def test_str_mixin(self):
        """Verify str mixin allows direct string comparison."""
        # str mixin allows equality comparison with strings
        assert RateLimitEventType.WARNING == "warning"
        # Value access works
        assert RateLimitEventType.WARNING.value == "warning"

    def test_all_event_types_exist(self):
        """Verify all expected event types are defined."""
        expected = [
            "WARNING",
            "LIMIT_REACHED",
            "ADJUSTED_INTERVAL",
            "RECOVERED",
            "REST_PRIORITY_ENTERED",
            "REST_PRIORITY_EXITED",
        ]
        actual = [e.name for e in RateLimitEventType]
        assert actual == expected


class TestIntervalDirection:
    """Tests for IntervalDirection enum."""

    def test_all_directions_exist(self):
        """Verify all expected directions are defined."""
        expected = ["INCREASE", "DECREASE"]
        actual = [d.name for d in IntervalDirection]
        assert actual == expected

    def test_str_mixin(self):
        """Verify str mixin allows direct string comparison."""
        assert IntervalDirection.INCREASE == "increase"
        assert IntervalDirection.DECREASE == "decrease"


class TestPRState:
    """Tests for PRState dataclass."""

    def test_pr_state_creation(self):
        """Test creating a PRState instance."""
        state = PRState(
            merge_state=MergeState.CLEAN,
            pending_reviewers=["user1", "user2"],
            check_status=CheckStatus.SUCCESS,
        )
        assert state.merge_state == MergeState.CLEAN
        assert state.pending_reviewers == ["user1", "user2"]
        assert state.check_status == CheckStatus.SUCCESS
        assert state.check_details == []
        assert state.review_comments == []
        assert state.unresolved_threads == []


class TestMonitorEvent:
    """Tests for MonitorEvent dataclass."""

    def test_monitor_event_to_dict(self):
        """Test converting MonitorEvent to dict."""
        event = MonitorEvent(
            event_type=EventType.CI_PASSED,
            pr_number="123",
            timestamp="2025-01-01T00:00:00",
            message="CI passed",
        )
        result = event.to_dict()
        assert result["event"] == "CI_PASSED"
        assert result["pr_number"] == "123"
        assert result["message"] == "CI passed"

    def test_monitor_event_to_json(self):
        """Test converting MonitorEvent to JSON."""
        event = MonitorEvent(
            event_type=EventType.CI_PASSED,
            pr_number="123",
            timestamp="2025-01-01T00:00:00",
            message="CI passed",
        )
        result = json.loads(event.to_json())
        assert result["event"] == "CI_PASSED"


class TestMonitorResult:
    """Tests for MonitorResult dataclass."""

    def test_monitor_result_defaults(self):
        """Test MonitorResult default values."""
        result = MonitorResult(success=True, message="Success")
        assert result.rebase_count == 0
        assert result.final_state is None
        assert result.review_completed is False
        assert result.ci_passed is False


class TestHasUnresolvedThreads:
    """Tests for has_unresolved_threads function."""

    def test_no_final_state(self):
        """Test with no final state."""
        result = MonitorResult(success=True, message="Success")
        assert has_unresolved_threads(result) is False

    def test_empty_unresolved_threads(self):
        """Test with empty unresolved threads."""
        state = PRState(
            merge_state=MergeState.CLEAN,
            pending_reviewers=[],
            check_status=CheckStatus.SUCCESS,
            unresolved_threads=[],
        )
        result = MonitorResult(success=True, message="Success", final_state=state)
        assert has_unresolved_threads(result) is False

    def test_with_unresolved_threads(self):
        """Test with unresolved threads."""
        state = PRState(
            merge_state=MergeState.CLEAN,
            pending_reviewers=[],
            check_status=CheckStatus.SUCCESS,
            unresolved_threads=[{"id": "1", "body": "Fix this"}],
        )
        result = MonitorResult(success=True, message="Success", final_state=state)
        assert has_unresolved_threads(result) is True


class TestRebaseResult:
    """Tests for RebaseResult dataclass."""

    def test_successful_rebase(self):
        """Test successful rebase result."""
        result = RebaseResult(success=True)
        assert result.success is True
        assert result.conflict is False
        assert result.error_message is None

    def test_conflict_rebase(self):
        """Test rebase with conflict."""
        result = RebaseResult(success=False, conflict=True, error_message="Merge conflict")
        assert result.success is False
        assert result.conflict is True
        assert result.error_message == "Merge conflict"


class TestClassifiedComments:
    """Tests for ClassifiedComments dataclass."""

    def test_creation(self):
        """Test creating a ClassifiedComments instance."""
        in_scope = [{"body": "Fix this bug"}]
        out_of_scope = [{"body": "Consider refactoring"}]
        classified = ClassifiedComments(in_scope=in_scope, out_of_scope=out_of_scope)
        assert classified.in_scope == in_scope
        assert classified.out_of_scope == out_of_scope

    def test_empty_lists(self):
        """Test with empty lists."""
        classified = ClassifiedComments(in_scope=[], out_of_scope=[])
        assert classified.in_scope == []
        assert classified.out_of_scope == []


class TestCodexReviewRequest:
    """Tests for CodexReviewRequest dataclass."""

    def test_creation(self):
        """Test creating a CodexReviewRequest instance."""
        request = CodexReviewRequest(
            comment_id=12345,
            created_at="2025-01-01T00:00:00Z",
            has_eyes_reaction=True,
        )
        assert request.comment_id == 12345
        assert request.created_at == "2025-01-01T00:00:00Z"
        assert request.has_eyes_reaction is True

    def test_without_eyes_reaction(self):
        """Test request without eyes reaction."""
        request = CodexReviewRequest(
            comment_id=67890,
            created_at="2025-01-02T00:00:00Z",
            has_eyes_reaction=False,
        )
        assert request.has_eyes_reaction is False


class TestMultiPREvent:
    """Tests for MultiPREvent dataclass."""

    def test_creation_with_event(self):
        """Test creating a MultiPREvent with event."""
        event = MonitorEvent(
            event_type=EventType.CI_PASSED,
            pr_number="123",
            timestamp="2025-01-01T00:00:00",
            message="CI passed",
        )
        state = PRState(
            merge_state=MergeState.CLEAN,
            pending_reviewers=[],
            check_status=CheckStatus.SUCCESS,
        )
        multi_event = MultiPREvent(pr_number="123", event=event, state=state)
        assert multi_event.pr_number == "123"
        assert multi_event.event == event
        assert multi_event.state == state

    def test_creation_with_none(self):
        """Test creating a MultiPREvent with None values."""
        multi_event = MultiPREvent(pr_number="456", event=None, state=None)
        assert multi_event.pr_number == "456"
        assert multi_event.event is None
        assert multi_event.state is None
