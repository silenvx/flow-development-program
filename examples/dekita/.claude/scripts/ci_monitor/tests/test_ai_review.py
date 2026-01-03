"""Tests for ci_monitor.ai_review module."""

from unittest.mock import MagicMock, patch

from ci_monitor.ai_review import (
    COPILOT_ERROR_PATTERNS,
    COPILOT_REVIEWER_LOGIN,
    check_and_report_contradictions,
    get_codex_review_requests,
    get_codex_reviews,
    get_copilot_reviews,
    has_copilot_or_codex_reviewer,
    is_ai_reviewer,
    is_copilot_review_error,
    request_copilot_review,
)


class TestIsAiReviewer:
    """Tests for is_ai_reviewer function."""

    def test_copilot_reviewer(self):
        """Test detection of Copilot reviewer."""
        assert is_ai_reviewer("copilot-pull-request-reviewer[bot]") is True

    def test_codex_reviewer(self):
        """Test detection of Codex reviewer."""
        assert is_ai_reviewer("chatgpt-codex-connector") is True

    def test_copilot_case_insensitive(self):
        """Test case insensitive detection."""
        assert is_ai_reviewer("COPILOT-pull-request-reviewer") is True
        assert is_ai_reviewer("ChatGPT-CODEX-Connector") is True

    def test_human_reviewer(self):
        """Test that human reviewers return False."""
        assert is_ai_reviewer("john-doe") is False
        assert is_ai_reviewer("user123") is False

    def test_empty_author(self):
        """Test empty author returns False."""
        assert is_ai_reviewer("") is False
        assert is_ai_reviewer(None) is False


class TestHasCopilotOrCodexReviewer:
    """Tests for has_copilot_or_codex_reviewer function."""

    def test_has_copilot(self):
        """Test detection of Copilot in reviewers list."""
        reviewers = ["john-doe", "copilot-pull-request-reviewer[bot]"]
        assert has_copilot_or_codex_reviewer(reviewers) is True

    def test_has_codex(self):
        """Test detection of Codex in reviewers list."""
        reviewers = ["jane-doe", "chatgpt-codex-connector"]
        assert has_copilot_or_codex_reviewer(reviewers) is True

    def test_no_ai_reviewers(self):
        """Test when no AI reviewers present."""
        reviewers = ["john-doe", "jane-doe"]
        assert has_copilot_or_codex_reviewer(reviewers) is False

    def test_empty_list(self):
        """Test empty reviewers list."""
        assert has_copilot_or_codex_reviewer([]) is False


class TestGetCodexReviewRequests:
    """Tests for get_codex_review_requests function."""

    @patch("ci_monitor.ai_review.run_gh_command")
    @patch("ci_monitor.ai_review._check_eyes_reaction")
    def test_finds_codex_review_request(self, mock_eyes, mock_run):
        """Test finding @codex review comment."""
        mock_run.return_value = (
            True,
            '[{"id": 123, "created_at": "2024-01-01T00:00:00Z", "body": "@codex review"}]',
        )
        mock_eyes.return_value = True

        requests = get_codex_review_requests("42")
        assert len(requests) == 1
        assert requests[0].comment_id == 123
        assert requests[0].has_eyes_reaction is True

    @patch("ci_monitor.ai_review.run_gh_command")
    def test_no_requests(self, mock_run):
        """Test when no @codex review comments exist."""
        mock_run.return_value = (True, "[]")
        requests = get_codex_review_requests("42")
        assert len(requests) == 0

    @patch("ci_monitor.ai_review.run_gh_command")
    def test_command_failure(self, mock_run):
        """Test when gh command fails."""
        mock_run.return_value = (False, "error")
        requests = get_codex_review_requests("42")
        assert len(requests) == 0


class TestGetCodexReviews:
    """Tests for get_codex_reviews function."""

    @patch("ci_monitor.ai_review.run_gh_command")
    def test_finds_codex_reviews(self, mock_run):
        """Test finding Codex reviews."""
        mock_run.return_value = (
            True,
            '[{"id": 1, "user": "chatgpt-codex-connector", "state": "COMMENTED"}]',
        )
        reviews = get_codex_reviews("42")
        assert len(reviews) == 1
        assert reviews[0]["user"] == "chatgpt-codex-connector"

    @patch("ci_monitor.ai_review.run_gh_command")
    def test_no_codex_reviews(self, mock_run):
        """Test when no Codex reviews exist."""
        mock_run.return_value = (True, "[]")
        reviews = get_codex_reviews("42")
        assert len(reviews) == 0


class TestGetCopilotReviews:
    """Tests for get_copilot_reviews function."""

    @patch("ci_monitor.ai_review.run_gh_command")
    def test_finds_copilot_reviews(self, mock_run):
        """Test finding Copilot reviews."""
        mock_run.return_value = (
            True,
            '[{"id": 1, "user": "copilot-pull-request-reviewer[bot]", "state": "COMMENTED"}]',
        )
        reviews = get_copilot_reviews("42")
        assert len(reviews) == 1
        assert "copilot" in reviews[0]["user"].lower()

    @patch("ci_monitor.ai_review.run_gh_command")
    def test_no_copilot_reviews(self, mock_run):
        """Test when no Copilot reviews exist."""
        mock_run.return_value = (True, "[]")
        reviews = get_copilot_reviews("42")
        assert len(reviews) == 0


class TestIsCopilotReviewError:
    """Tests for is_copilot_review_error function."""

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_detects_error_review(self, mock_get):
        """Test detection of Copilot error review."""
        mock_get.return_value = [
            {
                "id": 1,
                "submitted_at": "2024-01-01T00:00:00Z",
                "body": "Copilot encountered an error and was unable to review",
            }
        ]
        is_error, message = is_copilot_review_error("42")
        assert is_error is True
        assert "error" in message.lower()

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_no_error_in_latest(self, mock_get):
        """Test when latest review is not an error."""
        mock_get.return_value = [
            {
                "id": 1,
                "submitted_at": "2024-01-01T00:00:00Z",
                "body": "Code looks good",
            }
        ]
        is_error, message = is_copilot_review_error("42")
        assert is_error is False
        assert message is None

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_no_reviews(self, mock_get):
        """Test when no reviews exist."""
        mock_get.return_value = []
        is_error, message = is_copilot_review_error("42")
        assert is_error is False
        assert message is None

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_latest_is_not_error(self, mock_get):
        """Test that only the latest review is checked."""
        mock_get.return_value = [
            {
                "id": 1,
                "submitted_at": "2024-01-01T00:00:00Z",
                "body": "encountered an error",
            },
            {
                "id": 2,
                "submitted_at": "2024-01-02T00:00:00Z",
                "body": "All good",
            },
        ]
        is_error, _ = is_copilot_review_error("42")
        # Latest review (2024-01-02) is not an error
        assert is_error is False


class TestRequestCopilotReview:
    """Tests for request_copilot_review function."""

    @patch("subprocess.run")
    def test_successful_request(self, mock_run):
        """Test successful review request."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="{}",
            stderr="",
        )
        success, message = request_copilot_review("42")
        assert success is True
        assert message == ""

    @patch("subprocess.run")
    def test_failed_request(self, mock_run):
        """Test failed review request."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: Not authorized",
        )
        success, message = request_copilot_review("42")
        assert success is False
        assert "Not authorized" in message


class TestCheckAndReportContradictions:
    """Tests for check_and_report_contradictions function."""

    def test_skips_in_json_mode(self):
        """Test that function skips in JSON mode."""
        comments = [{"user": "copilot", "body": "test"}]
        # Should not raise and not print
        check_and_report_contradictions(comments, None, json_mode=True)

    def test_skips_with_no_comments(self):
        """Test that function skips with no comments."""
        check_and_report_contradictions([], None, json_mode=False)

    def test_skips_with_no_ai_comments(self):
        """Test that function skips when no AI comments."""
        comments = [{"user": "john-doe", "body": "test"}]
        check_and_report_contradictions(comments, None, json_mode=False)

    def test_calls_detect_fn_with_ai_comments(self):
        """Test that detect_fn is called with AI comments."""
        comments = [{"user": "copilot-reviewer", "body": "test", "id": 1}]
        detect_fn = MagicMock(return_value=[])
        format_fn = MagicMock()

        check_and_report_contradictions(
            comments,
            None,
            json_mode=False,
            detect_fn=detect_fn,
            format_fn=format_fn,
        )
        detect_fn.assert_called_once()

    def test_filters_previously_seen_comments(self):
        """Test that previously seen comments are filtered out."""
        comments = [
            {"user": "copilot-reviewer", "body": "test1", "id": 1},
            {"user": "copilot-reviewer", "body": "test2", "id": 2},
        ]
        previous = [{"user": "copilot-reviewer", "body": "test1", "id": 1}]
        detect_fn = MagicMock(return_value=[])
        format_fn = MagicMock()

        check_and_report_contradictions(
            comments,
            previous,
            json_mode=False,
            detect_fn=detect_fn,
            format_fn=format_fn,
        )
        # Should only pass new comments (id=2)
        call_args = detect_fn.call_args[0]
        assert len(call_args[0]) == 1
        assert call_args[0][0]["id"] == 2


class TestCopilotConstants:
    """Tests for Copilot-related constants."""

    def test_copilot_reviewer_login(self):
        """Test COPILOT_REVIEWER_LOGIN constant."""
        assert "copilot" in COPILOT_REVIEWER_LOGIN.lower()
        assert "[bot]" in COPILOT_REVIEWER_LOGIN

    def test_copilot_error_patterns(self):
        """Test COPILOT_ERROR_PATTERNS contains expected patterns."""
        assert len(COPILOT_ERROR_PATTERNS) > 0
        assert "encountered an error" in COPILOT_ERROR_PATTERNS
        assert "unable to review" in COPILOT_ERROR_PATTERNS
