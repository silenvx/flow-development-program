"""Tests for issue-investigation-tracker.py"""

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load the module
hook_path = Path(__file__).parent.parent / "issue-investigation-tracker.py"
spec = importlib.util.spec_from_file_location("issue_investigation_tracker", hook_path)
issue_investigation_tracker = importlib.util.module_from_spec(spec)
sys.modules["issue_investigation_tracker"] = issue_investigation_tracker
spec.loader.exec_module(issue_investigation_tracker)


class TestGhIssueViewPattern:
    """Test cases for gh issue view command pattern matching"""

    def test_matches_basic_command(self):
        """Test basic gh issue view command"""
        match = issue_investigation_tracker.GH_ISSUE_VIEW_PATTERN.search("gh issue view 123")
        assert match is not None
        assert match.group(1) == "123"

    def test_matches_with_hash(self):
        """Test gh issue view with # prefix"""
        match = issue_investigation_tracker.GH_ISSUE_VIEW_PATTERN.search("gh issue view #456")
        assert match is not None
        assert match.group(1) == "456"

    def test_matches_with_extra_args(self):
        """Test gh issue view with additional arguments"""
        match = issue_investigation_tracker.GH_ISSUE_VIEW_PATTERN.search(
            "gh issue view 789 --json title"
        )
        assert match is not None
        assert match.group(1) == "789"

    def test_no_match_for_other_commands(self):
        """Test that other gh commands don't match"""
        match = issue_investigation_tracker.GH_ISSUE_VIEW_PATTERN.search("gh issue create")
        assert match is None

    def test_no_match_for_pr_view(self):
        """Test that gh pr view doesn't match"""
        match = issue_investigation_tracker.GH_ISSUE_VIEW_PATTERN.search("gh pr view 123")
        assert match is None


class TestInvestigationPattern:
    """Test cases for investigation comment pattern matching"""

    def test_matches_investigation_comment(self):
        """Test matching investigation start comment"""
        match = issue_investigation_tracker.INVESTIGATION_PATTERN.search(
            "🔍 調査開始 (session: abc12345)"
        )
        assert match is not None
        assert match.group(1) == "abc12345"

    def test_matches_with_hyphens(self):
        """Test matching session ID with hyphens"""
        match = issue_investigation_tracker.INVESTIGATION_PATTERN.search(
            "🔍 調査開始 (session: a1b2-c3d4-e5f6)"
        )
        assert match is not None
        assert match.group(1) == "a1b2-c3d4-e5f6"

    def test_no_match_for_other_text(self):
        """Test that other text doesn't match"""
        match = issue_investigation_tracker.INVESTIGATION_PATTERN.search(
            "This is a regular comment"
        )
        assert match is None


class TestFindActiveInvestigation:
    """Test cases for find_active_investigation function"""

    def test_finds_recent_investigation(self):
        """Test finding a recent investigation from another session"""
        now = datetime.now(UTC)
        recent_time = (now - timedelta(minutes=30)).isoformat()

        comments = [
            {
                "body": "🔍 調査開始 (session: other-session)",
                "createdAt": recent_time,
                "author": {"login": "testuser"},
            }
        ]

        result = issue_investigation_tracker.find_active_investigation(comments, "my-session")
        assert result is not None
        assert result["session_id"] == "other-session"

    def test_ignores_own_session(self):
        """Test that own session is ignored"""
        now = datetime.now(UTC)
        recent_time = (now - timedelta(minutes=30)).isoformat()

        comments = [
            {
                "body": "🔍 調査開始 (session: my-session)",
                "createdAt": recent_time,
                "author": {"login": "testuser"},
            }
        ]

        result = issue_investigation_tracker.find_active_investigation(comments, "my-session")
        assert result is None

    def test_ignores_old_investigation(self):
        """Test that old investigations are ignored"""
        now = datetime.now(UTC)
        old_time = (now - timedelta(hours=2)).isoformat()

        comments = [
            {
                "body": "🔍 調査開始 (session: other-session)",
                "createdAt": old_time,
                "author": {"login": "testuser"},
            }
        ]

        result = issue_investigation_tracker.find_active_investigation(comments, "my-session")
        assert result is None

    def test_returns_most_recent(self):
        """Test that the most recent investigation is returned"""
        now = datetime.now(UTC)
        older_time = (now - timedelta(minutes=45)).isoformat()
        newer_time = (now - timedelta(minutes=15)).isoformat()

        comments = [
            {
                "body": "🔍 調査開始 (session: session-old)",
                "createdAt": older_time,
                "author": {"login": "user1"},
            },
            {
                "body": "🔍 調査開始 (session: session-new)",
                "createdAt": newer_time,
                "author": {"login": "user2"},
            },
        ]

        result = issue_investigation_tracker.find_active_investigation(comments, "my-session")
        assert result is not None
        assert result["session_id"] == "session-new"

    def test_empty_comments(self):
        """Test with empty comments list"""
        result = issue_investigation_tracker.find_active_investigation([], "my-session")
        assert result is None


class TestMainFunction:
    """Test cases for main function"""

    @patch("issue_investigation_tracker.parse_hook_input")
    @patch("issue_investigation_tracker.log_hook_execution")
    def test_approves_non_bash_tool(self, mock_log, mock_parse):
        """Test that non-Bash tools are approved"""
        mock_parse.return_value = {"tool_name": "Edit", "tool_input": {}}

        with patch("builtins.print") as mock_print:
            issue_investigation_tracker.main()
            output = mock_print.call_args[0][0]
            result = json.loads(output)
            assert result["decision"] == "approve"

    @patch("issue_investigation_tracker.parse_hook_input")
    @patch("issue_investigation_tracker.log_hook_execution")
    def test_approves_non_issue_view_command(self, mock_log, mock_parse):
        """Test that non gh issue view commands are approved"""
        mock_parse.return_value = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr view 123"},
        }

        with patch("builtins.print") as mock_print:
            issue_investigation_tracker.main()
            output = mock_print.call_args[0][0]
            result = json.loads(output)
            assert result["decision"] == "approve"

    @patch("issue_investigation_tracker.parse_hook_input")
    @patch("issue_investigation_tracker.get_issue_comments")
    @patch("issue_investigation_tracker.create_hook_context")
    @patch("issue_investigation_tracker.find_active_investigation")
    @patch("issue_investigation_tracker.add_investigation_comment")
    @patch("issue_investigation_tracker.log_hook_execution")
    def test_warns_on_active_investigation(
        self, mock_log, mock_add, mock_find, mock_session, mock_comments, mock_parse
    ):
        """Test warning when another session is investigating"""
        mock_parse.return_value = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue view 123"},
        }
        mock_comments.return_value = []
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "my-session"
        mock_session.return_value = mock_ctx
        mock_find.return_value = {
            "session_id": "other-session",
            "created_at": "2025-01-01T00:00:00Z",
            "author": "otheruser",
        }

        with patch("builtins.print") as mock_print:
            issue_investigation_tracker.main()
            output = mock_print.call_args[0][0]
            result = json.loads(output)
            assert result["decision"] == "approve"
            assert "systemMessage" in result
            assert "別セッションが調査中" in result["systemMessage"]

    @patch("issue_investigation_tracker.parse_hook_input")
    @patch("issue_investigation_tracker.get_issue_comments")
    @patch("issue_investigation_tracker.create_hook_context")
    @patch("issue_investigation_tracker.find_active_investigation")
    @patch("issue_investigation_tracker.has_recent_own_comment")
    @patch("issue_investigation_tracker.add_investigation_comment")
    @patch("issue_investigation_tracker.log_hook_execution")
    def test_adds_comment_when_no_active_investigation(
        self,
        mock_log,
        mock_add,
        mock_has_recent,
        mock_find,
        mock_session,
        mock_comments,
        mock_parse,
    ):
        """Test that investigation comment is added when no active investigation"""
        mock_parse.return_value = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue view 456"},
        }
        mock_comments.return_value = []
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "my-session"
        mock_session.return_value = mock_ctx
        mock_find.return_value = None
        mock_has_recent.return_value = False
        mock_add.return_value = True

        with patch("builtins.print") as mock_print:
            issue_investigation_tracker.main()
            output = mock_print.call_args[0][0]
            result = json.loads(output)
            assert result["decision"] == "approve"

        mock_add.assert_called_once_with(456, "my-session")

    @patch("issue_investigation_tracker.parse_hook_input")
    @patch("issue_investigation_tracker.get_issue_comments")
    @patch("issue_investigation_tracker.create_hook_context")
    @patch("issue_investigation_tracker.find_active_investigation")
    @patch("issue_investigation_tracker.has_recent_own_comment")
    @patch("issue_investigation_tracker.add_investigation_comment")
    @patch("issue_investigation_tracker.log_hook_execution")
    def test_skips_comment_when_already_commented(
        self,
        mock_log,
        mock_add,
        mock_has_recent,
        mock_find,
        mock_session,
        mock_comments,
        mock_parse,
    ):
        """Test that duplicate comment is not added"""
        mock_parse.return_value = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue view 456"},
        }
        mock_comments.return_value = []
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "my-session"
        mock_session.return_value = mock_ctx
        mock_find.return_value = None
        mock_has_recent.return_value = True  # Already has recent comment

        with patch("builtins.print") as mock_print:
            issue_investigation_tracker.main()
            output = mock_print.call_args[0][0]
            result = json.loads(output)
            assert result["decision"] == "approve"

        mock_add.assert_not_called()  # Should not add comment


class TestHasRecentOwnComment:
    """Test cases for has_recent_own_comment function"""

    def test_returns_true_for_recent_own_comment(self):
        """Test that recent own comment is detected"""
        now = datetime.now(UTC)
        recent_time = (now - timedelta(minutes=30)).isoformat()

        comments = [
            {
                "body": "🔍 調査開始 (session: my-session)",
                "createdAt": recent_time,
                "author": {"login": "testuser"},
            }
        ]

        result = issue_investigation_tracker.has_recent_own_comment(comments, "my-session")
        assert result is True

    def test_returns_false_for_old_own_comment(self):
        """Test that old own comment is not detected"""
        now = datetime.now(UTC)
        old_time = (now - timedelta(hours=2)).isoformat()

        comments = [
            {
                "body": "🔍 調査開始 (session: my-session)",
                "createdAt": old_time,
                "author": {"login": "testuser"},
            }
        ]

        result = issue_investigation_tracker.has_recent_own_comment(comments, "my-session")
        assert result is False

    def test_returns_false_for_other_session(self):
        """Test that other session's comment is not detected"""
        now = datetime.now(UTC)
        recent_time = (now - timedelta(minutes=30)).isoformat()

        comments = [
            {
                "body": "🔍 調査開始 (session: other-session)",
                "createdAt": recent_time,
                "author": {"login": "testuser"},
            }
        ]

        result = issue_investigation_tracker.has_recent_own_comment(comments, "my-session")
        assert result is False

    def test_returns_false_for_empty_comments(self):
        """Test with empty comments list"""
        result = issue_investigation_tracker.has_recent_own_comment([], "my-session")
        assert result is False
