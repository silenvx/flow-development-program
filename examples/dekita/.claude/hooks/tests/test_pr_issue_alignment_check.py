#!/usr/bin/env python3
"""Tests for pr-issue-alignment-check hook."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add hooks directory to path for common module
hooks_dir = Path(__file__).parent.parent
sys.path.insert(0, str(hooks_dir))

# Load hook module with hyphenated name
hook_path = hooks_dir / "pr-issue-alignment-check.py"
spec = importlib.util.spec_from_file_location("pr_issue_alignment_check", hook_path)
pr_issue_alignment_check = importlib.util.module_from_spec(spec)
sys.modules["pr_issue_alignment_check"] = pr_issue_alignment_check
spec.loader.exec_module(pr_issue_alignment_check)


class TestIsPrCreateCommand:
    """Tests for is_pr_create_command function."""

    def test_simple_pr_create(self):
        """Should detect simple gh pr create."""
        result = pr_issue_alignment_check.is_pr_create_command("gh pr create")
        assert result

    def test_pr_create_with_options(self):
        """Should detect gh pr create with options."""
        result = pr_issue_alignment_check.is_pr_create_command(
            'gh pr create --title "Test" --body "..."'
        )
        assert result

    def test_not_pr_create(self):
        """Should return False for other commands."""
        result = pr_issue_alignment_check.is_pr_create_command("gh pr view 123")
        assert not result

    def test_quoted_command(self):
        """Should ignore gh pr create in quoted strings."""
        result = pr_issue_alignment_check.is_pr_create_command('echo "gh pr create"')
        assert not result


class TestExtractIssueNumbersFromBody:
    """Tests for extract_issue_numbers_from_body function."""

    def test_closes_keyword(self):
        """Should extract issue number from Closes keyword."""
        result = pr_issue_alignment_check.extract_issue_numbers_from_body(
            'gh pr create --body "Closes #123"'
        )
        assert result == ["123"]

    def test_fixes_keyword(self):
        """Should extract issue number from Fixes keyword."""
        result = pr_issue_alignment_check.extract_issue_numbers_from_body(
            'gh pr create --body "Fixes #456"'
        )
        assert result == ["456"]

    def test_resolves_keyword(self):
        """Should extract issue number from Resolves keyword."""
        result = pr_issue_alignment_check.extract_issue_numbers_from_body(
            'gh pr create --body "Resolves #789"'
        )
        assert result == ["789"]

    def test_multiple_issues(self):
        """Should extract multiple issue numbers."""
        result = pr_issue_alignment_check.extract_issue_numbers_from_body(
            'gh pr create --body "Closes #123, Fixes #456"'
        )
        assert "123" in result
        assert "456" in result

    def test_case_insensitive(self):
        """Should be case insensitive."""
        result = pr_issue_alignment_check.extract_issue_numbers_from_body(
            'gh pr create --body "CLOSES #123"'
        )
        assert result == ["123"]

    def test_closes_with_colon(self):
        """Should extract issue number with colon format (Closes: #123)."""
        result = pr_issue_alignment_check.extract_issue_numbers_from_body(
            'gh pr create --body "Closes: #321"'
        )
        assert result == ["321"]

    def test_no_body(self):
        """Should return empty list when no body."""
        result = pr_issue_alignment_check.extract_issue_numbers_from_body(
            "gh pr create --title 'Test'"
        )
        assert result == []

    def test_no_closes_keyword(self):
        """Should return empty list when no Closes/Fixes keyword."""
        result = pr_issue_alignment_check.extract_issue_numbers_from_body(
            'gh pr create --body "Just a description"'
        )
        assert result == []

    def test_heredoc_pattern(self):
        """Should extract issue number from HEREDOC body."""
        command = '''gh pr create --title "Test" --body "$(cat <<'EOF'
## Summary
This PR fixes the issue.

Closes #543
EOF
)"'''
        result = pr_issue_alignment_check.extract_issue_numbers_from_body(command)
        assert result == ["543"]

    def test_body_with_quotes(self):
        """Should extract issue number from body containing quotes."""
        result = pr_issue_alignment_check.extract_issue_numbers_from_body(
            r'gh pr create --body "This is a \"quoted\" description. Closes #123"'
        )
        assert result == ["123"]

    def test_single_quoted_body(self):
        """Should extract issue number from single-quoted body."""
        result = pr_issue_alignment_check.extract_issue_numbers_from_body(
            "gh pr create --body 'Closes #789'"
        )
        assert result == ["789"]


class TestExtractAcceptanceCriteria:
    """Tests for extract_acceptance_criteria function."""

    def test_checkbox_unchecked(self):
        """Should detect unchecked checkbox items."""
        body = """## Acceptance Criteria
- [ ] First requirement
- [ ] Second requirement
"""
        result = pr_issue_alignment_check.extract_acceptance_criteria(body)
        assert len(result) == 2
        assert result[0] == (False, "First requirement")
        assert result[1] == (False, "Second requirement")

    def test_checkbox_checked(self):
        """Should detect checked checkbox items."""
        body = """## Acceptance Criteria
- [x] First requirement
- [X] Second requirement
"""
        result = pr_issue_alignment_check.extract_acceptance_criteria(body)
        assert len(result) == 2
        assert result[0] == (True, "First requirement")
        assert result[1] == (True, "Second requirement")

    def test_mixed_checkboxes(self):
        """Should handle mixed checked and unchecked items."""
        body = """## Criteria
- [x] Completed task
- [ ] Pending task
- [X] Another done
"""
        result = pr_issue_alignment_check.extract_acceptance_criteria(body)
        assert len(result) == 3
        assert result[0] == (True, "Completed task")
        assert result[1] == (False, "Pending task")
        assert result[2] == (True, "Another done")

    def test_asterisk_format(self):
        """Should handle asterisk format (* [ ])."""
        body = """* [ ] Task with asterisk
* [x] Done task
"""
        result = pr_issue_alignment_check.extract_acceptance_criteria(body)
        assert len(result) == 2
        assert result[0] == (False, "Task with asterisk")
        assert result[1] == (True, "Done task")

    def test_indented_checkboxes(self):
        """Should handle indented checkbox items."""
        body = """Nested list:
  - [ ] Indented task
    - [ ] More indented
"""
        result = pr_issue_alignment_check.extract_acceptance_criteria(body)
        assert len(result) == 2
        assert result[0] == (False, "Indented task")
        assert result[1] == (False, "More indented")

    def test_no_checkboxes(self):
        """Should return empty list when no checkboxes."""
        body = """## Description
This is a simple issue without checkboxes.
- Regular list item
- Another item
"""
        result = pr_issue_alignment_check.extract_acceptance_criteria(body)
        assert result == []


class TestFormatAcceptanceCriteriaMessage:
    """Tests for format_acceptance_criteria_message function."""

    def test_with_incomplete_items(self):
        """Should format warning with incomplete items."""
        criteria = [(False, "Task 1"), (True, "Task 2"), (False, "Task 3")]
        result = pr_issue_alignment_check.format_acceptance_criteria_message(
            "123", "Test Issue", criteria
        )
        assert "Issue #123: Test Issue" in result
        assert "未完了の受け入れ条件: 2件" in result
        assert "Task 1" in result
        assert "Task 3" in result

    def test_all_complete(self):
        """Should show only completed items when all done."""
        criteria = [(True, "Done 1"), (True, "Done 2")]
        result = pr_issue_alignment_check.format_acceptance_criteria_message(
            "456", "Complete Issue", criteria
        )
        assert "完了済み: 2件" in result
        assert "未完了" not in result

    def test_closed_issue_marker(self):
        """Should show (CLOSED) marker for closed issues."""
        criteria = [(False, "Incomplete task")]
        result = pr_issue_alignment_check.format_acceptance_criteria_message(
            "789", "Old Issue", criteria, is_closed=True
        )
        assert "Issue #789: Old Issue (CLOSED)" in result
        assert "クローズ済み" in result

    def test_open_issue_no_marker(self):
        """Should not show (CLOSED) marker for open issues."""
        criteria = [(False, "Incomplete task")]
        result = pr_issue_alignment_check.format_acceptance_criteria_message(
            "789", "Active Issue", criteria, is_closed=False
        )
        assert "(CLOSED)" not in result
        assert "クローズ済み" not in result


class TestFetchIssueContent:
    """Tests for fetch_issue_content function."""

    @patch("subprocess.run")
    def test_fetch_success(self, mock_run):
        """Should return (True, title, body, state) on success."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"title": "Issue Title", "body": "Issue body content", "state": "OPEN"}',
        )

        success, title, body, state = pr_issue_alignment_check.fetch_issue_content("123")
        assert success
        assert title == "Issue Title"
        assert body == "Issue body content"
        assert state == "OPEN"

    @patch("subprocess.run")
    def test_fetch_closed_issue(self, mock_run):
        """Should return CLOSED state for closed issues."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"title": "Closed Issue", "body": "Already done", "state": "CLOSED"}',
        )

        success, title, body, state = pr_issue_alignment_check.fetch_issue_content("456")
        assert success
        assert state == "CLOSED"

    @patch("subprocess.run")
    def test_fetch_error(self, mock_run):
        """Should return (False, '', '', '') on error."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
        )

        success, title, body, state = pr_issue_alignment_check.fetch_issue_content("123")
        assert not success
        assert title == ""
        assert body == ""
        assert state == ""

    @patch("subprocess.run")
    def test_fetch_handles_json_with_newlines(self, mock_run):
        """Should correctly parse JSON with newlines in body."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"title": "Test Issue", "body": "Line 1\\nLine 2\\nLine 3", "state": "OPEN"}',
        )

        success, title, body, state = pr_issue_alignment_check.fetch_issue_content("123")
        assert success
        assert title == "Test Issue"
        assert body == "Line 1\nLine 2\nLine 3"

    @patch("subprocess.run")
    def test_fetch_handles_null_body(self, mock_run):
        """Should handle null body (issues with no description)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"title": "Issue with no description", "body": null, "state": "OPEN"}',
        )

        success, title, body, state = pr_issue_alignment_check.fetch_issue_content("123")
        assert success
        assert title == "Issue with no description"
        assert body == ""


class TestMainIntegration:
    """Integration tests for main function."""

    @patch("pr_issue_alignment_check.fetch_issue_content")
    @patch("pr_issue_alignment_check.log_hook_execution")
    def test_displays_issue_content(self, mock_log, mock_fetch):
        """Should display issue content when creating PR with Closes."""
        mock_fetch.return_value = (True, "Test Issue", "Issue description", "OPEN")

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": 'gh pr create --body "Closes #543"'},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                pr_issue_alignment_check.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                assert output["decision"] == "approve"
                assert "systemMessage" in output
                assert "Test Issue" in output["systemMessage"]

    @patch("pr_issue_alignment_check.log_hook_execution")
    def test_ignores_non_pr_create(self, mock_log):
        """Should pass through non-pr-create commands."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr view 123"},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                pr_issue_alignment_check.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                # Issue #1607: Now uses print_continue_and_log_skip which returns {"continue": True}
                assert output.get("continue") is True or output.get("decision") == "approve"
                assert "systemMessage" not in output

    @patch("pr_issue_alignment_check.fetch_issue_content")
    @patch("pr_issue_alignment_check.log_hook_execution")
    def test_no_message_when_fetch_fails(self, mock_log, mock_fetch):
        """Should not display systemMessage when fetch fails."""
        mock_fetch.return_value = (False, "", "", "")

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": 'gh pr create --body "Closes #999"'},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                pr_issue_alignment_check.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                assert output["decision"] == "approve"
                assert "systemMessage" not in output

    @patch("pr_issue_alignment_check.fetch_issue_content")
    @patch("pr_issue_alignment_check.log_hook_execution")
    def test_warns_on_incomplete_criteria(self, mock_log, mock_fetch):
        """Should show strong warning when acceptance criteria incomplete."""
        mock_fetch.return_value = (
            True,
            "Feature Request",
            """## Description
Add new feature.

## Acceptance Criteria
- [ ] Implement feature
- [x] Add tests
- [ ] Update docs
""",
            "OPEN",
        )

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": 'gh pr create --body "Closes #100"'},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                pr_issue_alignment_check.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                assert output["decision"] == "approve"
                assert "systemMessage" in output
                assert "警告" in output["systemMessage"]
                assert "未完了" in output["systemMessage"]
                assert "2" in output["systemMessage"]  # 2 incomplete items

    @patch("pr_issue_alignment_check.fetch_issue_content")
    @patch("pr_issue_alignment_check.log_hook_execution")
    def test_success_message_when_all_criteria_complete(self, mock_log, mock_fetch):
        """Should show success message when all criteria complete."""
        mock_fetch.return_value = (
            True,
            "Bug Fix",
            """## Acceptance Criteria
- [x] Fix the bug
- [x] Add test
""",
            "OPEN",
        )

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": 'gh pr create --body "Closes #200"'},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                pr_issue_alignment_check.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                assert output["decision"] == "approve"
                assert "systemMessage" in output
                assert "✅" in output["systemMessage"]
                assert "警告" not in output["systemMessage"]

    @patch("pr_issue_alignment_check.fetch_issue_content")
    @patch("pr_issue_alignment_check.log_hook_execution")
    def test_closed_issue_with_incomplete_criteria(self, mock_log, mock_fetch):
        """Should show info message for closed issues with incomplete criteria."""
        mock_fetch.return_value = (
            True,
            "Old Feature",
            """## Acceptance Criteria
- [ ] Some incomplete task
- [x] Completed task
""",
            "CLOSED",
        )

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": 'gh pr create --body "Closes #592"'},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                pr_issue_alignment_check.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                assert output["decision"] == "approve"
                assert "systemMessage" in output
                # Should NOT show strong warning for closed issues
                assert "🚨" not in output["systemMessage"]
                # Should indicate it's closed
                assert "CLOSED" in output["systemMessage"]
                assert "クローズ済み" in output["systemMessage"]

    @patch("pr_issue_alignment_check.fetch_issue_content")
    @patch("pr_issue_alignment_check.log_hook_execution")
    def test_mixed_open_and_closed_issues(self, mock_log, mock_fetch):
        """Should warn for open issues even if closed issues also have incomplete criteria."""

        def mock_fetch_side_effect(issue_num):
            if issue_num == "100":
                # Open issue with incomplete criteria
                return (
                    True,
                    "Open Issue",
                    "- [ ] Incomplete\n",
                    "OPEN",
                )
            else:
                # Closed issue with incomplete criteria
                return (
                    True,
                    "Closed Issue",
                    "- [ ] Also incomplete\n",
                    "CLOSED",
                )

        mock_fetch.side_effect = mock_fetch_side_effect

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": 'gh pr create --body "Closes #100, Closes #200"'},
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch("builtins.print") as mock_print:
                pr_issue_alignment_check.main()

                call_args = mock_print.call_args[0][0]
                output = json.loads(call_args)
                assert output["decision"] == "approve"
                assert "systemMessage" in output
                # Should show strong warning because of open issue
                assert "警告" in output["systemMessage"]
