"""Tests for followup-issue-guard hook."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

# Load module with hyphen in name
hook_path = Path(__file__).parent.parent / "followup-issue-guard.py"
spec = importlib.util.spec_from_file_location("followup_issue_guard", hook_path)
followup_issue_guard = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(followup_issue_guard)  # type: ignore[union-attr]

# Import functions from dynamically loaded module
FOLLOWUP_KEYWORDS = followup_issue_guard.FOLLOWUP_KEYWORDS
HOOK_NAME = followup_issue_guard.HOOK_NAME
SKIP_ENV_VAR = followup_issue_guard.SKIP_ENV_VAR
contains_followup_keyword = followup_issue_guard.contains_followup_keyword
contains_issue_reference = followup_issue_guard.contains_issue_reference
extract_comment_body = followup_issue_guard.extract_comment_body
is_comment_command = followup_issue_guard.is_comment_command
main = followup_issue_guard.main


class TestIsCommentCommand:
    """Tests for is_comment_command function."""

    def test_gh_pr_comment(self) -> None:
        """gh pr comment should be detected."""
        assert is_comment_command("gh pr comment 123 --body 'test'") is True

    def test_gh_issue_comment(self) -> None:
        """gh issue comment should be detected."""
        assert is_comment_command("gh issue comment 456 --body 'test'") is True

    def test_gh_api_comments(self) -> None:
        """gh api with comments endpoint should be detected."""
        assert is_comment_command("gh api repos/owner/repo/pulls/1/comments") is True

    def test_non_comment_command(self) -> None:
        """Non-comment commands should not be detected."""
        assert is_comment_command("gh pr create --title 'test'") is False
        assert is_comment_command("git commit -m 'test'") is False
        assert is_comment_command("echo 'gh pr comment'") is False


class TestExtractCommentBody:
    """Tests for extract_comment_body function."""

    def test_double_quoted_body(self) -> None:
        """Extract body with double quotes."""
        command = 'gh pr comment 123 --body "This is a test"'
        assert extract_comment_body(command) == "This is a test"

    def test_single_quoted_body(self) -> None:
        """Extract body with single quotes."""
        command = "gh pr comment 123 --body 'This is a test'"
        assert extract_comment_body(command) == "This is a test"

    def test_short_flag(self) -> None:
        """Extract body with -b flag."""
        command = 'gh pr comment 123 -b "This is a test"'
        assert extract_comment_body(command) == "This is a test"

    def test_no_body(self) -> None:
        """Return None when no body is found."""
        command = "gh pr comment 123"
        assert extract_comment_body(command) is None

    def test_double_quoted_with_apostrophe(self) -> None:
        """Extract body with apostrophe inside double quotes."""
        command = """gh pr comment 123 --body "We'll handle this later\""""
        assert extract_comment_body(command) == "We'll handle this later"

    def test_single_quoted_with_double_quote(self) -> None:
        """Extract body with double quote inside single quotes."""
        command = """gh pr comment 123 --body 'He said "later"'"""
        assert extract_comment_body(command) == 'He said "later"'


class TestContainsFollowupKeyword:
    """Tests for contains_followup_keyword function."""

    def test_detects_atode(self) -> None:
        """Detect 後で keyword."""
        has_keyword, keyword = contains_followup_keyword("後で対応します")
        assert has_keyword is True
        assert keyword is not None

    def test_detects_shourai(self) -> None:
        """Detect 将来 keyword."""
        has_keyword, keyword = contains_followup_keyword("将来的に対応します")
        assert has_keyword is True

    def test_detects_followup(self) -> None:
        """Detect フォローアップ keyword."""
        has_keyword, keyword = contains_followup_keyword("フォローアップとして検討します")
        assert has_keyword is True

    def test_detects_betto(self) -> None:
        """Detect 別途 keyword."""
        has_keyword, keyword = contains_followup_keyword("別途対応が必要です")
        assert has_keyword is True

    def test_detects_kongo_taio(self) -> None:
        """Detect 今後.*対応 pattern."""
        has_keyword, keyword = contains_followup_keyword("今後のフォローアップとして対応します")
        assert has_keyword is True

    def test_detects_scope_gai(self) -> None:
        """Detect スコープ外 keyword."""
        has_keyword, keyword = contains_followup_keyword("スコープ外なので後で")
        assert has_keyword is True

    def test_detects_english_later(self) -> None:
        """Detect 'later' keyword."""
        has_keyword, keyword = contains_followup_keyword("We'll handle this later")
        assert has_keyword is True

    def test_detects_english_followup(self) -> None:
        """Detect 'follow-up' keyword."""
        has_keyword, keyword = contains_followup_keyword("This needs a follow-up")
        assert has_keyword is True

    def test_no_keyword(self) -> None:
        """Return False when no keyword is found."""
        has_keyword, keyword = contains_followup_keyword("修正しました")
        assert has_keyword is False
        assert keyword is None


class TestContainsIssueReference:
    """Tests for contains_issue_reference function."""

    def test_hash_number(self) -> None:
        """Detect #1234 format."""
        assert contains_issue_reference("Issue #1234 を作成しました") is True

    def test_issue_hash_number(self) -> None:
        """Detect Issue #1234 format."""
        assert contains_issue_reference("Issue #1234") is True

    def test_issue_number(self) -> None:
        """Detect Issue 1234 format."""
        assert contains_issue_reference("Issue 1234 を作成しました") is True

    def test_issue_hyphen_number(self) -> None:
        """Detect issue-1234 format."""
        assert contains_issue_reference("issue-1234 で対応") is True

    def test_no_reference(self) -> None:
        """Return False when no reference is found."""
        assert contains_issue_reference("後で対応します") is False


class TestMain:
    """Integration tests for main function."""

    def test_approve_non_bash_tool(self) -> None:
        """Approve non-Bash tools."""
        hook_input = {"tool_name": "Read", "tool_input": {}}
        with patch.object(followup_issue_guard, "parse_hook_input", return_value=hook_input):
            with patch("builtins.print") as mock_print:
                main()
                result = json.loads(mock_print.call_args[0][0])
                assert result["decision"] == "approve"

    def test_approve_non_comment_command(self) -> None:
        """Approve non-comment commands."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
        }
        with patch.object(followup_issue_guard, "parse_hook_input", return_value=hook_input):
            with patch("builtins.print") as mock_print:
                main()
                result = json.loads(mock_print.call_args[0][0])
                assert result["decision"] == "approve"

    def test_approve_comment_without_followup(self) -> None:
        """Approve comment without follow-up keywords."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": 'gh pr comment 123 --body "修正しました"'},
        }
        with patch.object(followup_issue_guard, "parse_hook_input", return_value=hook_input):
            with patch("builtins.print") as mock_print:
                main()
                result = json.loads(mock_print.call_args[0][0])
                assert result["decision"] == "approve"

    def test_approve_followup_with_issue_reference(self) -> None:
        """Approve follow-up comment with Issue reference."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {
                "command": 'gh pr comment 123 --body "Issue #1488 を作成しました。今後のフォローアップとして対応します。"'
            },
        }
        with patch.object(followup_issue_guard, "parse_hook_input", return_value=hook_input):
            with patch("builtins.print") as mock_print:
                main()
                result = json.loads(mock_print.call_args[0][0])
                assert result["decision"] == "approve"

    def test_block_followup_without_issue_reference(self) -> None:
        """Block follow-up comment without Issue reference."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {
                "command": 'gh pr comment 123 --body "今後のフォローアップとして検討します"'
            },
        }
        with patch.object(followup_issue_guard, "parse_hook_input", return_value=hook_input):
            with patch("builtins.print") as mock_print:
                main()
                result = json.loads(mock_print.call_args[0][0])
                assert result["decision"] == "block"
                assert "followup-issue-guard" in result["reason"]

    def test_skip_with_env_var(self) -> None:
        """Skip check when env var is set."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {
                "command": 'gh pr comment 123 --body "今後のフォローアップとして検討します"'
            },
        }
        with patch.object(followup_issue_guard, "parse_hook_input", return_value=hook_input):
            with patch.object(followup_issue_guard, "check_skip_env", return_value=True):
                with patch("builtins.print") as mock_print:
                    main()
                    result = json.loads(mock_print.call_args[0][0])
                    assert result["decision"] == "approve"
