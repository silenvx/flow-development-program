#!/usr/bin/env python3
"""Tests for issue-review-response-check hook."""

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add hooks directory to path for common module
hooks_dir = Path(__file__).parent.parent
sys.path.insert(0, str(hooks_dir))

# Load hook module with hyphenated name
hook_path = hooks_dir / "issue-review-response-check.py"
spec = importlib.util.spec_from_file_location("issue_review_response_check", hook_path)
issue_review_response_check = importlib.util.module_from_spec(spec)
sys.modules["issue_review_response_check"] = issue_review_response_check
spec.loader.exec_module(issue_review_response_check)


class TestExtractIssueNumber:
    """Tests for extract_issue_number function."""

    def test_simple_issue_close(self):
        """Should extract issue number from simple command."""
        result = issue_review_response_check.extract_issue_number("gh issue close 123")
        assert result == "123"

    def test_issue_close_with_hash(self):
        """Should extract issue number with # prefix."""
        result = issue_review_response_check.extract_issue_number("gh issue close #456")
        assert result == "456"

    def test_issue_close_with_options(self):
        """Should extract issue number when options follow."""
        result = issue_review_response_check.extract_issue_number(
            "gh issue close 789 --comment 'Done'"
        )
        assert result == "789"

    def test_no_issue_close(self):
        """Should return None for non-issue-close commands."""
        result = issue_review_response_check.extract_issue_number("gh issue view 123")
        assert result is None

    def test_issue_list_command(self):
        """Should return None for issue list command."""
        result = issue_review_response_check.extract_issue_number("gh issue list")
        assert result is None

    def test_quoted_command(self):
        """Should ignore issue numbers in quoted strings."""
        result = issue_review_response_check.extract_issue_number('echo "gh issue close 123"')
        assert result is None

    def test_issue_close_with_flag_before_number(self):
        """Should extract issue number when flags come before number."""
        result = issue_review_response_check.extract_issue_number(
            "gh issue close --reason completed 123"
        )
        assert result == "123"


class TestGetAiReviewCommentTime:
    """Tests for get_ai_review_comment_time function."""

    @patch("subprocess.run")
    def test_returns_datetime_when_ai_review_exists(self, mock_run):
        """Should return datetime when AI Review comment exists."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="2025-12-20T12:05:59Z\n",
        )

        result = issue_review_response_check.get_ai_review_comment_time("123")
        assert result is not None
        assert result.year == 2025
        assert result.month == 12
        assert result.day == 20

    @patch("subprocess.run")
    def test_returns_none_when_no_ai_review(self, mock_run):
        """Should return None when no AI Review comment."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
        )

        result = issue_review_response_check.get_ai_review_comment_time("123")
        assert result is None

    @patch("subprocess.run")
    def test_returns_none_on_error(self, mock_run):
        """Should return None on gh command error."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
        )

        result = issue_review_response_check.get_ai_review_comment_time("123")
        assert result is None

    @patch("subprocess.run")
    def test_returns_latest_when_multiple_ai_reviews(self, mock_run):
        """Should return the latest (newest) AI Review timestamp."""
        # Multiple timestamps, newest is last
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="2025-12-20T10:00:00Z\n2025-12-20T12:00:00Z\n2025-12-20T14:00:00Z\n",
        )

        result = issue_review_response_check.get_ai_review_comment_time("123")
        assert result is not None
        # Should be 14:00 (the latest)
        assert result.hour == 14


class TestGetAiReviewSuggestions:
    """Tests for get_ai_review_suggestions function."""

    @patch("subprocess.run")
    def test_extracts_bullet_points(self, mock_run):
        """Should extract bullet point suggestions from AI Review."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Some intro text\n- First suggestion here\n* Second suggestion here\nMore text",
        )

        result = issue_review_response_check.get_ai_review_suggestions("123")
        assert len(result) == 2
        assert "First suggestion" in result[0]
        assert "Second suggestion" in result[1]

    @patch("subprocess.run")
    def test_skips_short_suggestions(self, mock_run):
        """Should skip very short bullet points."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="- Short\n- This is a longer suggestion that should be included",
        )

        result = issue_review_response_check.get_ai_review_suggestions("123")
        assert len(result) == 1
        assert "longer suggestion" in result[0]

    @patch("subprocess.run")
    def test_returns_max_three_suggestions(self, mock_run):
        """Should return at most 3 suggestions."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="- Suggestion one here\n- Suggestion two here\n- Suggestion three\n- Suggestion four\n- Suggestion five",
        )

        result = issue_review_response_check.get_ai_review_suggestions("123")
        assert len(result) == 3

    @patch("subprocess.run")
    def test_returns_empty_on_error(self, mock_run):
        """Should return empty list on error."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
        )

        result = issue_review_response_check.get_ai_review_suggestions("123")
        assert result == []


class TestWasIssueEditedAfter:
    """Tests for was_issue_edited_after function.

    Uses issue's updated_at field instead of timeline events (Issue #658).
    """

    @patch("subprocess.run")
    def test_returns_true_when_updated_after(self, mock_run):
        """Should return True when issue updated_at is after AI Review."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="2025-12-20T14:00:00Z\n",
        )

        after_time = datetime(2025, 12, 20, 12, 0, 0, tzinfo=UTC)
        result = issue_review_response_check.was_issue_edited_after("123", after_time)
        assert result

    @patch("subprocess.run")
    def test_returns_true_when_updated_at_empty(self, mock_run):
        """Should return True (don't block) when updated_at is empty."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
        )

        after_time = datetime(2025, 12, 20, 12, 0, 0, tzinfo=UTC)
        result = issue_review_response_check.was_issue_edited_after("123", after_time)
        assert result

    @patch("subprocess.run")
    def test_returns_false_when_updated_before(self, mock_run):
        """Should return False when updated_at is before AI Review."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="2025-12-20T10:00:00Z\n",
        )

        after_time = datetime(2025, 12, 20, 12, 0, 0, tzinfo=UTC)
        result = issue_review_response_check.was_issue_edited_after("123", after_time)
        assert not result

    @patch("subprocess.run")
    def test_returns_true_on_api_error(self, mock_run):
        """Should return True on API error (don't block)."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
        )

        after_time = datetime(2025, 12, 20, 12, 0, 0, tzinfo=UTC)
        result = issue_review_response_check.was_issue_edited_after("123", after_time)
        assert result


class TestMainIntegration:
    """Integration tests for main function."""

    @patch("issue_review_response_check.get_ai_review_comment_time")
    @patch("issue_review_response_check.log_hook_execution")
    def test_approves_when_no_ai_review(self, mock_log, mock_get_time):
        """Should approve when no AI Review comment exists."""
        mock_get_time.return_value = None

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue close 123"},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                issue_review_response_check.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                assert output["decision"] == "approve"

    @patch("issue_review_response_check.was_issue_edited_after")
    @patch("issue_review_response_check.get_ai_review_comment_time")
    @patch("issue_review_response_check.log_hook_execution")
    def test_approves_when_edited_after_review(self, mock_log, mock_get_time, mock_was_edited):
        """Should approve when issue was edited after AI Review."""
        mock_get_time.return_value = datetime(2025, 12, 20, 12, 0, 0, tzinfo=UTC)
        mock_was_edited.return_value = True

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue close 123"},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                issue_review_response_check.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                assert output["decision"] == "approve"

    @patch("issue_review_response_check.get_ai_review_suggestions")
    @patch("issue_review_response_check.was_issue_edited_after")
    @patch("issue_review_response_check.get_ai_review_comment_time")
    @patch("issue_review_response_check.log_hook_execution")
    def test_blocks_when_not_edited_after_review(
        self, mock_log, mock_get_time, mock_was_edited, mock_suggestions
    ):
        """Should block when AI Review exists but issue was not edited."""
        mock_get_time.return_value = datetime(2025, 12, 20, 12, 0, 0, tzinfo=UTC)
        mock_was_edited.return_value = False
        mock_suggestions.return_value = ["- Add more details", "- Fix typo"]

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue close 123"},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                issue_review_response_check.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                assert output["decision"] == "block"
                assert "AIレビューコメント" in output["reason"]
                assert "gh issue edit" in output["reason"]

    @patch("issue_review_response_check.log_hook_execution")
    def test_ignores_non_close_commands(self, mock_log):
        """Should pass through non-issue-close commands."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue view 123"},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                issue_review_response_check.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                # Issue #1607: Now uses print_continue_and_log_skip which returns {"continue": True}
                assert output.get("continue") is True or output.get("decision") == "approve"

    @patch("issue_review_response_check.log_hook_execution")
    def test_ignores_non_bash_tools(self, mock_log):
        """Should pass through non-Bash tools."""
        input_data = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file"},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                issue_review_response_check.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                # Issue #1607: Now uses print_continue_and_log_skip which returns {"continue": True}
                assert output.get("continue") is True or output.get("decision") == "approve"
