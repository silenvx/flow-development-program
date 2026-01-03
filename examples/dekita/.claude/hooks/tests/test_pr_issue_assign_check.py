#!/usr/bin/env python3
"""Unit tests for pr-issue-assign-check.py"""

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# pr-issue-assign-check.py has hyphens, so we need dynamic import
HOOK_PATH = Path(__file__).parent.parent / "pr-issue-assign-check.py"
_spec = importlib.util.spec_from_file_location("pr_issue_assign_check", HOOK_PATH)
pr_issue_assign_check = importlib.util.module_from_spec(_spec)
sys.modules["pr_issue_assign_check"] = pr_issue_assign_check
_spec.loader.exec_module(pr_issue_assign_check)

is_gh_pr_create_command = pr_issue_assign_check.is_gh_pr_create_command
extract_pr_body = pr_issue_assign_check.extract_pr_body
extract_closes_issues = pr_issue_assign_check.extract_closes_issues
get_issue_assignees = pr_issue_assign_check.get_issue_assignees
get_current_user = pr_issue_assign_check.get_current_user
assign_issue = pr_issue_assign_check.assign_issue
main = pr_issue_assign_check.main


class TestIsGhPrCreateCommand:
    """Tests for is_gh_pr_create_command function."""

    def test_simple_pr_create(self):
        """Should detect simple gh pr create command."""
        assert is_gh_pr_create_command("gh pr create")

    def test_pr_create_with_options(self):
        """Should detect gh pr create with options."""
        assert is_gh_pr_create_command("gh pr create --title 'Test'")
        assert is_gh_pr_create_command("gh pr create -t 'Test' -b 'Body'")

    def test_not_pr_create(self):
        """Should not match other gh commands."""
        assert not is_gh_pr_create_command("gh pr list")
        assert not is_gh_pr_create_command("gh pr view")
        assert not is_gh_pr_create_command("gh issue create")

    def test_empty_command(self):
        """Should handle empty command."""
        assert not is_gh_pr_create_command("")
        assert not is_gh_pr_create_command("   ")

    def test_quoted_command_ignored(self):
        """Should ignore quoted gh pr create (e.g., in echo)."""
        assert not is_gh_pr_create_command("echo 'gh pr create'")
        assert not is_gh_pr_create_command('echo "gh pr create"')


class TestExtractPrBody:
    """Tests for extract_pr_body function."""

    def test_body_with_double_quotes(self):
        """Should extract body from --body option."""
        cmd = 'gh pr create --title "Test" --body "This is the body"'
        assert extract_pr_body(cmd) == "This is the body"

    def test_body_with_equals(self):
        """Should extract body from --body= format."""
        cmd = 'gh pr create --body="Body content"'
        assert extract_pr_body(cmd) == "Body content"

    def test_short_option(self):
        """Should extract body from -b option."""
        cmd = "gh pr create -t 'Title' -b 'Short body'"
        assert extract_pr_body(cmd) == "Short body"

    def test_heredoc_pattern(self):
        """Should extract body from HEREDOC pattern."""
        # HEREDOC pattern as typically passed to the hook (escaped newlines)
        cmd = (
            "gh pr create --body \"$(cat <<'EOF'\nThis is a\nmultiline body\nCloses #123\nEOF\n)\""
        )
        body = extract_pr_body(cmd)
        # Note: HEREDOC parsing is complex; body extraction may be partial
        # The important thing is it doesn't crash and returns something
        assert body is not None

    def test_no_body_option(self):
        """Should return None when no body specified."""
        cmd = "gh pr create --title 'Test'"
        assert extract_pr_body(cmd) is None

    def test_body_with_escaped_double_quotes(self):
        """Should handle escaped double quotes in body (Issue #218)."""
        cmd = r'gh pr create --body "Fix \"foo\" bug. Closes #123"'
        body = extract_pr_body(cmd)
        assert body is not None
        assert "Closes #123" in body
        assert r"\"foo\"" in body

    def test_body_with_escaped_single_quotes(self):
        """Should handle escaped single quotes in body."""
        cmd = r"gh pr create --body 'Fix \'bar\' bug. Closes #456'"
        body = extract_pr_body(cmd)
        assert body is not None
        assert "Closes #456" in body
        assert r"\'bar\'" in body

    def test_body_with_escaped_backslash(self):
        """Should handle escaped backslash in body."""
        cmd = r'gh pr create --body "Path: C:\\Users. Closes #789"'
        body = extract_pr_body(cmd)
        assert body is not None
        assert "Closes #789" in body

    def test_body_with_multiple_escapes(self):
        """Should handle multiple escape sequences in body."""
        cmd = r'gh pr create --body "Fix \"foo\" and \"bar\" bugs. Closes #100"'
        body = extract_pr_body(cmd)
        assert body is not None
        assert "Closes #100" in body

    def test_body_equals_with_escaped_quotes(self):
        """Should handle escaped quotes with --body= format."""
        cmd = r'gh pr create --body="JSON: {\"key\": \"value\"}. Closes #200"'
        body = extract_pr_body(cmd)
        assert body is not None
        assert "Closes #200" in body


class TestExtractClosesIssues:
    """Tests for extract_closes_issues function."""

    def test_closes_keyword(self):
        """Should extract issue from Closes keyword."""
        assert extract_closes_issues("Closes #123") == [123]
        assert extract_closes_issues("Closes: #456") == [456]

    def test_fixes_keyword(self):
        """Should extract issue from Fixes keyword."""
        assert extract_closes_issues("Fixes #789") == [789]
        assert extract_closes_issues("Fixed #100") == [100]

    def test_resolves_keyword(self):
        """Should extract issue from Resolves keyword."""
        assert extract_closes_issues("Resolves #200") == [200]
        assert extract_closes_issues("Resolved #300") == [300]

    def test_case_insensitive(self):
        """Should be case insensitive."""
        assert extract_closes_issues("CLOSES #111") == [111]
        assert extract_closes_issues("closes #222") == [222]
        assert extract_closes_issues("ClOsEs #333") == [333]

    def test_multiple_issues(self):
        """Should extract multiple issues."""
        body = "Closes #123\nFixes #456\nResolves #789"
        issues = extract_closes_issues(body)
        assert issues == [123, 456, 789]

    def test_duplicate_issues_are_deduplicated(self):
        """Should remove duplicate issue numbers.

        When same issue is referenced multiple times (e.g., Closes #123 and Fixes #123),
        it should only appear once in the result.
        """
        body = "Closes #123\nFixes #123"
        issues = extract_closes_issues(body)
        assert issues == [123]

    def test_multiple_duplicates_mixed(self):
        """Should deduplicate mixed references to same issues."""
        body = "Closes #100\nFixes #200\nResolves #100\nCloses #300\nFixes #200"
        issues = extract_closes_issues(body)
        assert issues == [100, 200, 300]

    def test_no_issues(self):
        """Should return empty list when no issues."""
        assert extract_closes_issues("Just a regular body") == []
        assert extract_closes_issues("") == []
        assert extract_closes_issues("Related to #123") == []  # Not a closing keyword

    def test_issue_in_code_block_still_extracted(self):
        """Should extract issues even in code blocks (limitation)."""
        # This is a known limitation - code blocks aren't filtered
        body = "```\nCloses #999\n```"
        assert extract_closes_issues(body) == [999]


class TestGetIssueAssignees:
    """Tests for get_issue_assignees function."""

    @patch("pr_issue_assign_check.subprocess.run")
    def test_returns_assignees(self, mock_run):
        """Should return list of assignee logins."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"assignees": [{"login": "user1"}, {"login": "user2"}]}',
        )
        result = get_issue_assignees(123)
        assert result == ["user1", "user2"]

    @patch("pr_issue_assign_check.subprocess.run")
    def test_returns_none_on_failure(self, mock_run):
        """Should return None on command failure to distinguish from empty assignees."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = get_issue_assignees(123)
        assert result is None

    @patch("pr_issue_assign_check.subprocess.run")
    def test_returns_empty_on_no_assignees(self, mock_run):
        """Should return empty list when no assignees."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"assignees": []}',
        )
        result = get_issue_assignees(123)
        assert result == []


class TestGetCurrentUser:
    """Tests for get_current_user function."""

    @patch("pr_issue_assign_check.subprocess.run")
    def test_returns_user_login(self, mock_run):
        """Should return user login on success."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="testuser\n",
        )
        result = get_current_user()
        assert result == "testuser"

    @patch("pr_issue_assign_check.subprocess.run")
    def test_returns_none_on_failure(self, mock_run):
        """Should return None on command failure."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = get_current_user()
        assert result is None

    @patch("pr_issue_assign_check.subprocess.run")
    def test_returns_none_on_timeout(self, mock_run):
        """Should return None on timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired("gh", 10)
        result = get_current_user()
        assert result is None


class TestAssignIssue:
    """Tests for assign_issue function."""

    @patch("pr_issue_assign_check.subprocess.run")
    def test_returns_true_on_success(self, mock_run):
        """Should return True when assignment succeeds."""
        mock_run.return_value = MagicMock(returncode=0)
        result = assign_issue(123)
        assert result

    @patch("pr_issue_assign_check.subprocess.run")
    def test_returns_false_on_failure(self, mock_run):
        """Should return False when assignment fails."""
        mock_run.return_value = MagicMock(returncode=1)
        result = assign_issue(123)
        assert not result


class TestMainIntegration:
    """Integration tests for main() function."""

    def _run_main_with_input(self, hook_input: dict) -> dict:
        """Helper to run main() with given hook input and capture output."""
        stdin_data = json.dumps(hook_input)
        captured_output = io.StringIO()

        with (
            patch("sys.stdin", io.StringIO(stdin_data)),
            patch("sys.stdout", captured_output),
            patch("pr_issue_assign_check.log_hook_execution"),
            pytest.raises(SystemExit) as ctx,
        ):
            main()

        assert ctx.value.code == 0
        return json.loads(captured_output.getvalue())

    def test_non_gh_pr_create_command_approves(self):
        """Should approve non-gh pr create commands immediately."""
        result = self._run_main_with_input({"tool_input": {"command": "gh issue list"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_no_body_approves(self):
        """Should approve when no body is specified."""
        result = self._run_main_with_input(
            {"tool_input": {"command": "gh pr create --title 'Test'"}}
        )
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_no_closes_keywords_approves(self):
        """Should approve when body has no Closes keywords."""
        result = self._run_main_with_input(
            {"tool_input": {"command": "gh pr create --body 'Just a regular body'"}}
        )
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    @patch("pr_issue_assign_check.get_current_user")
    @patch("pr_issue_assign_check.get_issue_assignees")
    @patch("pr_issue_assign_check.assign_issue")
    def test_auto_assigns_unassigned_issue(self, mock_assign, mock_assignees, mock_user):
        """Should auto-assign unassigned issues."""
        mock_user.return_value = "testuser"
        mock_assignees.return_value = []  # No assignees
        mock_assign.return_value = True

        result = self._run_main_with_input(
            {"tool_input": {"command": "gh pr create --body 'Closes #123'"}}
        )

        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "自動アサイン" in result["systemMessage"]
        mock_assign.assert_called_once_with(123)

    @patch("pr_issue_assign_check.get_current_user")
    @patch("pr_issue_assign_check.get_issue_assignees")
    def test_warns_when_assigned_to_others(self, mock_assignees, mock_user):
        """Should warn when issue is assigned to others."""
        mock_user.return_value = "testuser"
        mock_assignees.return_value = ["other_user"]

        result = self._run_main_with_input(
            {"tool_input": {"command": "gh pr create --body 'Closes #123'"}}
        )

        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "他者にアサイン済み" in result["systemMessage"]
        assert "other_user" in result["systemMessage"]

    @patch("pr_issue_assign_check.get_current_user")
    @patch("pr_issue_assign_check.get_issue_assignees")
    def test_no_message_when_already_assigned_to_self(self, mock_assignees, mock_user):
        """Should not show message when issue is already assigned to current user."""
        mock_user.return_value = "testuser"
        mock_assignees.return_value = ["testuser"]

        result = self._run_main_with_input(
            {"tool_input": {"command": "gh pr create --body 'Closes #123'"}}
        )

        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    @patch("pr_issue_assign_check.get_current_user")
    @patch("pr_issue_assign_check.get_issue_assignees")
    def test_warns_when_assignee_lookup_fails(self, mock_assignees, mock_user):
        """Should warn when assignee lookup fails."""
        mock_user.return_value = "testuser"
        mock_assignees.return_value = None  # Lookup failed

        result = self._run_main_with_input(
            {"tool_input": {"command": "gh pr create --body 'Closes #123'"}}
        )

        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "確認に失敗" in result["systemMessage"]

    @patch("pr_issue_assign_check.get_current_user")
    @patch("pr_issue_assign_check.get_issue_assignees")
    @patch("pr_issue_assign_check.assign_issue")
    def test_warns_when_auto_assign_fails(self, mock_assign, mock_assignees, mock_user):
        """Should warn when auto-assign fails."""
        mock_user.return_value = "testuser"
        mock_assignees.return_value = []  # No assignees
        mock_assign.return_value = False  # Assignment failed

        result = self._run_main_with_input(
            {"tool_input": {"command": "gh pr create --body 'Closes #123'"}}
        )

        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "アサインに失敗" in result["systemMessage"]
        assert "gh issue edit 123 --add-assignee @me" in result["systemMessage"]

    @patch("pr_issue_assign_check.get_current_user")
    @patch("pr_issue_assign_check.get_issue_assignees")
    @patch("pr_issue_assign_check.assign_issue")
    def test_handles_multiple_issues(self, mock_assign, mock_assignees, mock_user):
        """Should process multiple issues in PR body."""
        mock_user.return_value = "testuser"
        # Issue 100: no assignees (auto-assign)
        # Issue 200: assigned to others
        # Issue 300: assigned to self
        mock_assignees.side_effect = [[], ["other_user"], ["testuser"]]
        mock_assign.return_value = True

        result = self._run_main_with_input(
            {
                "tool_input": {
                    "command": "gh pr create --body 'Closes #100\nFixes #200\nResolves #300'"
                }
            }
        )

        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "自動アサイン" in result["systemMessage"]
        assert "他者にアサイン済み" in result["systemMessage"]
        # Only issue 100 (unassigned) should trigger assign
        mock_assign.assert_called_once_with(100)

    @patch("pr_issue_assign_check.get_current_user")
    @patch("pr_issue_assign_check.get_issue_assignees")
    def test_no_warning_when_user_unknown_and_issue_assigned_to_others(
        self, mock_assignees, mock_user
    ):
        """Should not warn about others when current user is unknown.

        When get_current_user() returns None, we can't determine if the
        assignee is "someone else", so no warning should be shown.
        """
        mock_user.return_value = None  # User lookup failed
        mock_assignees.return_value = ["other_user"]

        result = self._run_main_with_input(
            {"tool_input": {"command": "gh pr create --body 'Closes #123'"}}
        )

        assert result["decision"] == "approve"
        # No systemMessage because we can't determine if it's "others"
        assert "systemMessage" not in result

    @patch("pr_issue_assign_check.get_current_user")
    @patch("pr_issue_assign_check.get_issue_assignees")
    @patch("pr_issue_assign_check.assign_issue")
    def test_auto_assigns_when_user_unknown(self, mock_assign, mock_assignees, mock_user):
        """Should auto-assign unassigned issues even when current user is unknown.

        Auto-assign uses @me which works regardless of knowing the current user.
        """
        mock_user.return_value = None  # User lookup failed
        mock_assignees.return_value = []  # No assignees
        mock_assign.return_value = True

        result = self._run_main_with_input(
            {"tool_input": {"command": "gh pr create --body 'Closes #123'"}}
        )

        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "自動アサイン" in result["systemMessage"]
        mock_assign.assert_called_once_with(123)

    def test_handles_exception_gracefully(self):
        """Should handle exceptions and still approve."""
        stdin_data = "invalid json"
        captured_output = io.StringIO()

        with (
            patch("sys.stdin", io.StringIO(stdin_data)),
            patch("sys.stdout", captured_output),
            patch("sys.stderr", io.StringIO()),  # Suppress error output
            patch("pr_issue_assign_check.log_hook_execution"),
            pytest.raises(SystemExit) as ctx,
        ):
            main()

        assert ctx.value.code == 0
        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"


class TestEdgeCasesWhenUserUnknown(TestMainIntegration):
    """Tests for edge cases when get_current_user() returns None.

    These tests verify behavior when the current user cannot be determined
    (e.g., gh CLI not authenticated, network issues).
    Inherits from TestMainIntegration to reuse _run_main_with_input helper.
    """

    @patch("pr_issue_assign_check.get_current_user")
    @patch("pr_issue_assign_check.get_issue_assignees")
    @patch("pr_issue_assign_check.assign_issue")
    def test_auto_assigns_when_user_unknown(self, mock_assign, mock_get_assignees, mock_get_user):
        """Should auto-assign when user is unknown and issue is unassigned."""
        mock_get_user.return_value = None
        mock_get_assignees.return_value = []  # Unassigned
        mock_assign.return_value = True

        result = self._run_main_with_input(
            {"tool_input": {"command": "gh pr create --body 'Closes #123'"}}
        )

        # Should have auto-assigned even with unknown user
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "自動アサイン" in result["systemMessage"]
        mock_assign.assert_called_once_with(123)

    @patch("pr_issue_assign_check.get_current_user")
    @patch("pr_issue_assign_check.get_issue_assignees")
    @patch("pr_issue_assign_check.assign_issue")
    def test_no_warning_when_user_unknown_and_issue_assigned(
        self, mock_assign, mock_get_assignees, mock_get_user
    ):
        """Should not warn when user is unknown and issue is assigned to others.

        When get_current_user() returns None, the condition
        `current_user and current_user not in assignees` evaluates to False,
        so no warning is generated. This is acceptable fail-open behavior.
        """
        mock_get_user.return_value = None
        mock_get_assignees.return_value = ["other-user"]  # Assigned to someone
        mock_assign.return_value = True

        result = self._run_main_with_input(
            {"tool_input": {"command": "gh pr create --body 'Closes #456'"}}
        )

        # No message because current_user is None (fail-open)
        assert result["decision"] == "approve"
        assert "systemMessage" not in result
        mock_assign.assert_not_called()
