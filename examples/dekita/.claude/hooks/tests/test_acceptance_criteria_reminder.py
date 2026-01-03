#!/usr/bin/env python3
"""Tests for acceptance_criteria_reminder hook (Issue #1288)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

import acceptance_criteria_reminder


class TestIsPrCreateCommand:
    """Tests for is_pr_create_command function."""

    def test_detects_gh_pr_create(self):
        """Test detection of gh pr create command."""
        assert acceptance_criteria_reminder.is_pr_create_command("gh pr create") is True
        assert (
            acceptance_criteria_reminder.is_pr_create_command("gh pr create --title test") is True
        )
        assert (
            acceptance_criteria_reminder.is_pr_create_command("gh pr create --body 'test'") is True
        )

    def test_ignores_other_commands(self):
        """Test that other commands are ignored."""
        assert acceptance_criteria_reminder.is_pr_create_command("gh pr view") is False
        assert acceptance_criteria_reminder.is_pr_create_command("gh pr list") is False
        assert acceptance_criteria_reminder.is_pr_create_command("git push") is False

    def test_handles_whitespace(self):
        """Test handling of extra whitespace."""
        assert acceptance_criteria_reminder.is_pr_create_command("gh  pr  create") is True
        assert acceptance_criteria_reminder.is_pr_create_command("  gh pr create  ") is True

    def test_avoids_false_positives(self):
        """Test that substring matches in non-gh commands are rejected."""
        # Commands that contain 'gh pr create' as a substring but aren't PR creation
        assert (
            acceptance_criteria_reminder.is_pr_create_command('rg "gh pr create" README.md')
            is False
        )
        assert acceptance_criteria_reminder.is_pr_create_command('echo "gh pr create"') is False
        assert (
            acceptance_criteria_reminder.is_pr_create_command("grep 'gh pr create' file.txt")
            is False
        )


class TestStripCodeBlocks:
    """Tests for strip_code_blocks function."""

    def test_strips_fenced_code_blocks(self):
        """Test removal of fenced code blocks."""
        text = """## Criteria
- [ ] Item 1
```python
- [ ] This should be removed
```
- [ ] Item 2
"""
        result = acceptance_criteria_reminder.strip_code_blocks(text)
        assert "- [ ] Item 1" in result
        assert "- [ ] Item 2" in result
        assert "This should be removed" not in result

    def test_strips_inline_code(self):
        """Test removal of inline code."""
        text = "- [ ] Check `- [ ] inline code` here"
        result = acceptance_criteria_reminder.strip_code_blocks(text)
        assert "- [ ] Check" in result
        assert "inline code" not in result

    def test_preserves_normal_checkboxes(self):
        """Test that normal checkboxes outside code are preserved."""
        text = """- [x] Completed
- [ ] Pending"""
        result = acceptance_criteria_reminder.strip_code_blocks(text)
        assert "- [x] Completed" in result
        assert "- [ ] Pending" in result


class TestExtractIssueNumberFromBranch:
    """Tests for extract_issue_number_from_branch function."""

    def test_extracts_from_standard_format(self):
        """Test extraction from standard branch formats."""
        assert (
            acceptance_criteria_reminder.extract_issue_number_from_branch("feat/issue-123-desc")
            == "123"
        )
        assert (
            acceptance_criteria_reminder.extract_issue_number_from_branch("fix/issue-456") == "456"
        )
        assert acceptance_criteria_reminder.extract_issue_number_from_branch("issue-789") == "789"

    def test_extracts_from_alternative_formats(self):
        """Test extraction from alternative formats."""
        assert acceptance_criteria_reminder.extract_issue_number_from_branch("123-feature") == "123"
        assert acceptance_criteria_reminder.extract_issue_number_from_branch("feature-123") == "123"

    def test_returns_none_for_no_issue(self):
        """Test returns None when no issue number found."""
        assert acceptance_criteria_reminder.extract_issue_number_from_branch("main") is None
        assert (
            acceptance_criteria_reminder.extract_issue_number_from_branch("feature-branch") is None
        )
        assert acceptance_criteria_reminder.extract_issue_number_from_branch("") is None
        assert acceptance_criteria_reminder.extract_issue_number_from_branch(None) is None


class TestFetchIssueAcceptanceCriteria:
    """Tests for fetch_issue_acceptance_criteria function."""

    def test_extracts_checkbox_items(self):
        """Test extraction of checkbox items from issue body."""
        issue_body = """## Description
Some description.

## Acceptance Criteria
- [x] First item completed
- [ ] Second item pending
- [X] Third item completed
"""
        with patch.object(acceptance_criteria_reminder, "subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(
                    {
                        "title": "Test Issue",
                        "body": issue_body,
                        "state": "OPEN",
                    }
                ),
            )

            success, title, criteria = acceptance_criteria_reminder.fetch_issue_acceptance_criteria(
                "123"
            )

            assert success is True
            assert title == "Test Issue"
            assert len(criteria) == 3
            assert criteria[0] == (True, "First item completed")
            assert criteria[1] == (False, "Second item pending")
            assert criteria[2] == (True, "Third item completed")

    def test_treats_strikethrough_as_completed(self):
        """Test that strikethrough items are treated as completed."""
        issue_body = """## Acceptance Criteria
- [ ] ~~This item is excluded~~
- [ ] Regular pending item
"""
        with patch.object(acceptance_criteria_reminder, "subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(
                    {
                        "title": "Test Issue",
                        "body": issue_body,
                        "state": "OPEN",
                    }
                ),
            )

            success, title, criteria = acceptance_criteria_reminder.fetch_issue_acceptance_criteria(
                "123"
            )

            assert success is True
            assert len(criteria) == 2
            # Strikethrough is treated as completed
            assert criteria[0] == (True, "~~This item is excluded~~")
            assert criteria[1] == (False, "Regular pending item")

    def test_skips_closed_issues(self):
        """Test that closed issues are skipped."""
        with patch.object(acceptance_criteria_reminder, "subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(
                    {
                        "title": "Test Issue",
                        "body": "- [ ] Pending",
                        "state": "CLOSED",
                    }
                ),
            )

            success, title, criteria = acceptance_criteria_reminder.fetch_issue_acceptance_criteria(
                "123"
            )

            assert success is False

    def test_handles_gh_command_failure(self):
        """Test handling of gh command failure."""
        with patch.object(acceptance_criteria_reminder, "subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=1, stdout="")

            success, title, criteria = acceptance_criteria_reminder.fetch_issue_acceptance_criteria(
                "123"
            )

            assert success is False


class TestCheckAcceptanceCriteria:
    """Tests for check_acceptance_criteria function."""

    def test_returns_info_for_incomplete_criteria(self):
        """Test returns info when there are incomplete criteria."""
        issue_body = """## Acceptance Criteria
- [x] Completed item
- [ ] Pending item 1
- [ ] Pending item 2
"""
        with patch.object(acceptance_criteria_reminder, "subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(
                    {
                        "title": "Test Issue",
                        "body": issue_body,
                        "state": "OPEN",
                    }
                ),
            )

            result = acceptance_criteria_reminder.check_acceptance_criteria("123")

            assert result is not None
            assert result["issue_number"] == "123"
            assert result["title"] == "Test Issue"
            assert result["total_count"] == 3
            assert result["completed_count"] == 1
            assert len(result["incomplete_items"]) == 2

    def test_returns_none_for_complete_criteria(self):
        """Test returns None when all criteria are complete."""
        issue_body = """## Acceptance Criteria
- [x] Completed item 1
- [x] Completed item 2
"""
        with patch.object(acceptance_criteria_reminder, "subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(
                    {
                        "title": "Test Issue",
                        "body": issue_body,
                        "state": "OPEN",
                    }
                ),
            )

            result = acceptance_criteria_reminder.check_acceptance_criteria("123")

            assert result is None


class TestFormatWarningMessage:
    """Tests for format_warning_message function."""

    def test_formats_warning_correctly(self):
        """Test warning message formatting."""
        issue_info = {
            "issue_number": "123",
            "title": "Test Issue",
            "total_count": 3,
            "completed_count": 1,
            "incomplete_items": ["Item 1", "Item 2"],
        }

        message = acceptance_criteria_reminder.format_warning_message(issue_info)

        assert "Issue #123" in message
        assert "Test Issue" in message
        assert "1/3 完了" in message
        assert "- [ ] Item 1" in message
        assert "- [ ] Item 2" in message

    def test_truncates_long_list(self):
        """Test that long lists are truncated."""
        issue_info = {
            "issue_number": "123",
            "title": "Test Issue",
            "total_count": 10,
            "completed_count": 3,
            "incomplete_items": [f"Item {i}" for i in range(7)],
        }

        message = acceptance_criteria_reminder.format_warning_message(issue_info)

        # Should show first 5 items and indicate there are more
        assert "...他2件" in message


class TestMain:
    """Tests for main function."""

    def test_continues_for_non_bash_tool(self):
        """Test continues for non-Bash tool calls."""
        hook_input = {"tool_name": "Read", "tool_input": {"file_path": "/some/file"}}

        with patch.object(
            acceptance_criteria_reminder, "parse_hook_input", return_value=hook_input
        ):
            with patch("builtins.print") as mock_print:
                acceptance_criteria_reminder.main()

                # Should output continue: true
                mock_print.assert_called()
                output = mock_print.call_args[0][0]
                assert json.loads(output)["continue"] is True

    def test_continues_for_non_pr_create_command(self):
        """Test continues for non-pr-create commands."""
        hook_input = {"tool_name": "Bash", "tool_input": {"command": "git status"}}

        with patch.object(
            acceptance_criteria_reminder, "parse_hook_input", return_value=hook_input
        ):
            with patch("builtins.print") as mock_print:
                acceptance_criteria_reminder.main()

                output = mock_print.call_args[0][0]
                assert json.loads(output)["continue"] is True

    def test_warns_on_incomplete_criteria(self):
        """Test warns when issue has incomplete criteria."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --title test"},
        }

        issue_info = {
            "issue_number": "123",
            "title": "Test Issue",
            "total_count": 2,
            "completed_count": 1,
            "incomplete_items": ["Pending item"],
        }

        with (
            patch.object(acceptance_criteria_reminder, "parse_hook_input", return_value=hook_input),
            patch.object(
                acceptance_criteria_reminder,
                "get_current_branch",
                return_value="feat/issue-123-test",
            ),
            patch.object(
                acceptance_criteria_reminder, "check_acceptance_criteria", return_value=issue_info
            ),
            patch.object(acceptance_criteria_reminder, "log_hook_execution"),
            patch("builtins.print") as mock_print,
        ):
            acceptance_criteria_reminder.main()

            # Should print warning to stderr and continue
            calls = mock_print.call_args_list
            # Last call should be the continue response
            assert json.loads(calls[-1][0][0])["continue"] is True
            # Should have printed a warning
            assert len(calls) > 1

    def test_continues_silently_when_criteria_complete(self):
        """Test continues silently when all criteria are complete."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --title test"},
        }

        with (
            patch.object(acceptance_criteria_reminder, "parse_hook_input", return_value=hook_input),
            patch.object(
                acceptance_criteria_reminder,
                "get_current_branch",
                return_value="feat/issue-123-test",
            ),
            patch.object(
                acceptance_criteria_reminder, "check_acceptance_criteria", return_value=None
            ),
            patch("builtins.print") as mock_print,
        ):
            acceptance_criteria_reminder.main()

            # Should only output continue response (no warning)
            assert mock_print.call_count == 1
            output = mock_print.call_args[0][0]
            assert json.loads(output)["continue"] is True
