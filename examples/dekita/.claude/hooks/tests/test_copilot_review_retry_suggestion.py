#!/usr/bin/env python3
"""Tests for copilot-review-retry-suggestion hook."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path for common module
hooks_dir = Path(__file__).parent.parent
sys.path.insert(0, str(hooks_dir))

# Load hook module with hyphenated name
hook_path = hooks_dir / "copilot-review-retry-suggestion.py"
spec = importlib.util.spec_from_file_location("copilot_review_retry_suggestion", hook_path)
copilot_hook = importlib.util.module_from_spec(spec)
sys.modules["copilot_review_retry_suggestion"] = copilot_hook
spec.loader.exec_module(copilot_hook)


class TestIsCopilotReviewCheck:
    """Tests for is_copilot_review_check function."""

    def test_gh_pr_checks(self):
        """Should detect gh pr checks command."""
        result = copilot_hook.is_copilot_review_check("gh pr checks 123", "")
        assert result

    def test_gh_api_reviews(self):
        """Should detect gh api reviews command."""
        result = copilot_hook.is_copilot_review_check(
            "gh api /repos/owner/repo/pulls/123/reviews", ""
        )
        assert result

    def test_gh_api_requested_reviewers(self):
        """Should detect gh api requested_reviewers command."""
        result = copilot_hook.is_copilot_review_check(
            "gh api /repos/owner/repo/pulls/123/requested_reviewers", ""
        )
        assert result

    def test_ci_monitor_output_with_copilot_error(self):
        """Should detect Copilot error in ci-monitor output."""
        result = copilot_hook.is_copilot_review_check(
            "python3 ci-monitor.py", "Copilot review failed with error"
        )
        assert result

    def test_unrelated_command(self):
        """Should return False for unrelated commands."""
        result = copilot_hook.is_copilot_review_check("git status", "")
        assert not result


class TestHasCopilotReviewError:
    """Tests for has_copilot_review_error function."""

    def test_encountered_error(self):
        """Should detect 'Copilot encountered an error' message."""
        result = copilot_hook.has_copilot_review_error(
            "Copilot encountered an error and was unable to review", ""
        )
        assert result

    def test_unable_to_review(self):
        """Should detect 'unable to review' message."""
        result = copilot_hook.has_copilot_review_error(
            "Copilot was unable to review this pull request", ""
        )
        assert result

    def test_no_error(self):
        """Should return False when no error."""
        result = copilot_hook.has_copilot_review_error("Review completed successfully", "")
        assert not result

    def test_case_insensitive(self):
        """Should be case insensitive."""
        result = copilot_hook.has_copilot_review_error("COPILOT ENCOUNTERED AN ERROR", "")
        assert result


class TestExtractPrNumber:
    """Tests for extract_pr_number function."""

    def test_pr_number_in_path(self):
        """Should extract PR number from API path."""
        result = copilot_hook.extract_pr_number("gh api /repos/owner/repo/pulls/123")
        assert result == "123"

    def test_pr_checks_command(self):
        """Should extract PR number from pr checks command."""
        result = copilot_hook.extract_pr_number("gh pr checks 456")
        assert result == "456"

    def test_no_pr_number(self):
        """Should return None when no PR number."""
        result = copilot_hook.extract_pr_number("git status")
        assert result is None

    def test_spaceless_pull_pattern(self):
        """Should extract PR number from spaceless pattern like pull123."""
        result = copilot_hook.extract_pr_number("gh api /repos/owner/repo/pull123")
        assert result == "123"

    def test_pulls_with_slash(self):
        """Should extract PR number from pulls/123 pattern."""
        result = copilot_hook.extract_pr_number("gh api /repos/owner/repo/pulls/789")
        assert result == "789"


class TestIsCopilotReviewCheckDetailed:
    """Detailed tests for is_copilot_review_check function."""

    def test_echo_command_with_copilot_text(self):
        """Should return False for echo commands that mention Copilot."""
        result = copilot_hook.is_copilot_review_check('echo "Copilot"', "")
        assert not result

    def test_grep_command(self):
        """Should return False for grep commands."""
        result = copilot_hook.is_copilot_review_check("grep -r 'copilot'", "")
        assert not result

    def test_cat_command(self):
        """Should return False for cat commands."""
        result = copilot_hook.is_copilot_review_check("cat file.txt", "")
        assert not result


class TestMainIntegration:
    """Integration tests for main function."""

    @patch("copilot_review_retry_suggestion.save_error_count")
    @patch("copilot_review_retry_suggestion.load_error_count")
    @patch("copilot_review_retry_suggestion.log_hook_execution")
    def test_suggests_recreation_after_threshold(self, mock_log, mock_load, mock_save):
        """Should suggest PR recreation after error threshold."""
        mock_load.return_value = {"count": 2, "last_pr": "123"}

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr checks 123"},
            "tool_result": {
                "stdout": "Copilot encountered an error",
                "stderr": "",
            },
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                copilot_hook.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                assert "systemMessage" in output
                assert "作り直す" in output["systemMessage"]

    @patch("copilot_review_retry_suggestion.save_error_count")
    @patch("copilot_review_retry_suggestion.load_error_count")
    @patch("copilot_review_retry_suggestion.log_hook_execution")
    def test_no_suggestion_before_threshold(self, mock_log, mock_load, mock_save):
        """Should not suggest PR recreation before threshold."""
        mock_load.return_value = {"count": 0, "last_pr": None}

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr checks 123"},
            "tool_result": {
                "stdout": "Copilot encountered an error",
                "stderr": "",
            },
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                copilot_hook.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                assert "systemMessage" not in output

    @patch("copilot_review_retry_suggestion.log_hook_execution")
    def test_ignores_non_bash(self, mock_log):
        """Should ignore non-Bash tool calls."""
        input_data = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/test"},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                copilot_hook.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                # Issue #1607: Now returns {"continue": True} with skip logging
                assert output == {"continue": True}

    @patch("copilot_review_retry_suggestion.save_error_count")
    @patch("copilot_review_retry_suggestion.load_error_count")
    @patch("copilot_review_retry_suggestion.log_hook_execution")
    def test_resets_counter_on_success(self, mock_log, mock_load, mock_save):
        """Should reset error counter when Copilot review succeeds."""
        mock_load.return_value = {"count": 2, "last_pr": "123"}

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr checks 123"},
            "tool_result": {
                "stdout": "Copilot review completed",
                "stderr": "",
            },
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print"):
                copilot_hook.main()

                # Verify save was called with reset counter
                # Note: save_error_count(ctx, data) - data is second argument
                mock_save.assert_called_once()
                saved_data = mock_save.call_args[0][1]
                assert saved_data["count"] == 0
                assert saved_data["last_pr"] is None

    @patch("copilot_review_retry_suggestion.save_error_count")
    @patch("copilot_review_retry_suggestion.load_error_count")
    @patch("copilot_review_retry_suggestion.log_hook_execution")
    def test_resets_counter_on_pr_switch(self, mock_log, mock_load, mock_save):
        """Should reset error counter when switching to a different PR."""
        # Previous errors were on PR 123
        mock_load.return_value = {"count": 2, "last_pr": "123"}

        # Now checking PR 456
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr checks 456"},
            "tool_result": {
                "stdout": "Copilot encountered an error",
                "stderr": "",
            },
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print"):
                copilot_hook.main()

                # Verify save was called with count=1 (reset then incremented)
                # Note: save_error_count(ctx, data) - data is second argument
                mock_save.assert_called_once()
                saved_data = mock_save.call_args[0][1]
                assert saved_data["count"] == 1
                assert saved_data["last_pr"] == "456"
