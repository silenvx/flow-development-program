#!/usr/bin/env python3
"""Tests for issue-comments-check hook."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add hooks directory to path for common module
hooks_dir = Path(__file__).parent.parent
sys.path.insert(0, str(hooks_dir))

# Load hook module with hyphenated name
hook_path = hooks_dir / "issue-comments-check.py"
spec = importlib.util.spec_from_file_location("issue_comments_check", hook_path)
issue_comments_check = importlib.util.module_from_spec(spec)
sys.modules["issue_comments_check"] = issue_comments_check
spec.loader.exec_module(issue_comments_check)


class TestExtractIssueNumber:
    """Tests for extract_issue_number function."""

    def test_simple_issue_view(self):
        """Should extract issue number from simple command."""
        result = issue_comments_check.extract_issue_number("gh issue view 123")
        assert result == "123"

    def test_issue_view_with_hash(self):
        """Should extract issue number with # prefix."""
        result = issue_comments_check.extract_issue_number("gh issue view #456")
        assert result == "456"

    def test_issue_view_with_options(self):
        """Should extract issue number when options follow."""
        result = issue_comments_check.extract_issue_number("gh issue view 789 --web")
        assert result == "789"

    def test_no_issue_view(self):
        """Should return None for non-issue-view commands."""
        result = issue_comments_check.extract_issue_number("gh pr view 123")
        assert result is None

    def test_issue_list_command(self):
        """Should return None for issue list command."""
        result = issue_comments_check.extract_issue_number("gh issue list")
        assert result is None

    def test_quoted_command(self):
        """Should ignore issue numbers in quoted strings."""
        result = issue_comments_check.extract_issue_number('echo "gh issue view 123"')
        assert result is None

    def test_issue_view_with_flag_before_number(self):
        """Should extract issue number when flags come before number."""
        result = issue_comments_check.extract_issue_number("gh issue view --web 123")
        assert result == "123"

    def test_issue_view_with_multiple_flags(self):
        """Should extract issue number with multiple flags."""
        result = issue_comments_check.extract_issue_number("gh issue view --web --comments 456")
        assert result == "456"


class TestHasCommentsFlag:
    """Tests for has_comments_flag function."""

    def test_with_comments_flag(self):
        """Should detect --comments flag."""
        result = issue_comments_check.has_comments_flag("gh issue view 123 --comments")
        assert result

    def test_without_comments_flag(self):
        """Should return False when no --comments flag."""
        result = issue_comments_check.has_comments_flag("gh issue view 123")
        assert not result

    def test_with_other_flags(self):
        """Should return False with other flags but not --comments."""
        result = issue_comments_check.has_comments_flag("gh issue view 123 --web")
        assert not result

    def test_quoted_comments_flag(self):
        """Should ignore --comments inside quoted strings."""
        result = issue_comments_check.has_comments_flag('echo "gh issue view 123 --comments"')
        assert not result


class TestFetchIssueComments:
    """Tests for fetch_issue_comments function."""

    @patch("subprocess.run")
    def test_fetch_comments_success(self, mock_run):
        """Should return (True, comments) on success."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="---\n**user1** (2025-01-01):\nComment 1\n",
        )

        success, comments = issue_comments_check.fetch_issue_comments("123")
        assert success
        assert "user1" in comments
        assert "Comment 1" in comments

    @patch("subprocess.run")
    def test_fetch_comments_no_comments(self, mock_run):
        """Should return (True, '') when no comments."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
        )

        success, comments = issue_comments_check.fetch_issue_comments("123")
        assert success
        assert comments == ""

    @patch("subprocess.run")
    def test_fetch_comments_error(self, mock_run):
        """Should return (False, '') on error."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
        )

        success, comments = issue_comments_check.fetch_issue_comments("123")
        assert not success
        assert comments == ""


class TestMainIntegration:
    """Integration tests for main function."""

    @patch("issue_comments_check.fetch_issue_comments")
    @patch("issue_comments_check.log_hook_execution")
    def test_displays_comments_for_issue_view(self, mock_log, mock_fetch):
        """Should display comments when viewing issue without --comments."""
        mock_fetch.return_value = (True, "**user** (2025-01-01):\nTest comment")

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue view 538"},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                issue_comments_check.main()

                # Verify output contains systemMessage with comments
                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                assert output["decision"] == "approve"
                assert "systemMessage" in output
                assert "Test comment" in output["systemMessage"]

    @patch("issue_comments_check.fetch_issue_comments")
    @patch("issue_comments_check.log_hook_execution")
    def test_no_message_on_fetch_error(self, mock_log, mock_fetch):
        """Should not show misleading message when fetch fails."""
        mock_fetch.return_value = (False, "")

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue view 538"},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                issue_comments_check.main()

                # Should not have systemMessage (to avoid misleading user)
                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                assert output["decision"] == "approve"
                assert "systemMessage" not in output

    @patch("issue_comments_check.fetch_issue_comments")
    @patch("issue_comments_check.log_hook_execution")
    def test_skips_when_comments_flag_present(self, mock_log, mock_fetch):
        """Should not fetch comments when --comments already present."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue view 538 --comments"},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                issue_comments_check.main()

                # Should not call fetch_issue_comments
                mock_fetch.assert_not_called()

                # Output should be simple approve
                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                assert output["decision"] == "approve"

    @patch("issue_comments_check.log_hook_execution")
    def test_ignores_non_issue_commands(self, mock_log):
        """Should pass through non-issue commands."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr view 123"},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                issue_comments_check.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                # Issue #1607: Now uses print_continue_and_log_skip which returns {"continue": True}
                assert output.get("continue") is True or output.get("decision") == "approve"
                assert "systemMessage" not in output
