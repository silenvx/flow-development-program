#!/usr/bin/env python3
"""Tests for pr-related-issue-check hook."""

import json
import subprocess
from unittest.mock import patch

from conftest import HOOKS_DIR, load_hook_module

HOOK_PATH = HOOKS_DIR / "pr_related_issue_check.py"


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestIsGhPrCreateCommand:
    """Tests for is_gh_pr_create_command function."""

    def setup_method(self):
        self.module = load_hook_module("pr_related_issue_check")

    def test_basic_gh_pr_create(self):
        """Should detect basic gh pr create commands."""
        assert self.module.is_gh_pr_create_command("gh pr create")
        assert self.module.is_gh_pr_create_command("gh pr create --title 'test'")
        assert self.module.is_gh_pr_create_command('gh pr create --title "test" --body "body"')

    def test_exclude_quoted_strings(self):
        """Should not detect gh pr create inside quoted strings."""
        assert not self.module.is_gh_pr_create_command("echo 'gh pr create'")
        assert not self.module.is_gh_pr_create_command('echo "gh pr create"')

    def test_empty_command(self):
        """Should return False for empty commands."""
        assert not self.module.is_gh_pr_create_command("")
        assert not self.module.is_gh_pr_create_command("   ")

    def test_other_gh_commands(self):
        """Should not match other gh pr commands."""
        assert not self.module.is_gh_pr_create_command("gh pr view 123")
        assert not self.module.is_gh_pr_create_command("gh pr list")
        assert not self.module.is_gh_pr_create_command("gh pr merge 123")


class TestExtractPrTitle:
    """Tests for extract_pr_title function."""

    def setup_method(self):
        self.module = load_hook_module("pr_related_issue_check")

    def test_title_double_quotes(self):
        """Test extracting title with double quotes."""
        command = 'gh pr create --title "Add feature" --body "body"'
        result = self.module.extract_pr_title(command)
        assert result == "Add feature"

    def test_title_single_quotes(self):
        """Test extracting title with single quotes."""
        command = "gh pr create --title 'Add feature' --body 'body'"
        result = self.module.extract_pr_title(command)
        assert result == "Add feature"

    def test_title_short_flag(self):
        """Test extracting title with -t flag."""
        command = 'gh pr create -t "Add feature" -b "body"'
        result = self.module.extract_pr_title(command)
        assert result == "Add feature"

    def test_title_equals_sign(self):
        """Test extracting title with --title= format."""
        command = 'gh pr create --title="Add feature" --body="body"'
        result = self.module.extract_pr_title(command)
        assert result == "Add feature"

    def test_no_title(self):
        """Test when no title is specified."""
        command = "gh pr create"
        result = self.module.extract_pr_title(command)
        assert result is None

    def test_title_with_escaped_quotes(self):
        """Test extracting title with escaped quotes."""
        command = r'gh pr create --title "Add \"feature\" support"'
        result = self.module.extract_pr_title(command)
        assert result is not None
        assert "feature" in result


class TestExtractPrBody:
    """Tests for extract_pr_body function."""

    def setup_method(self):
        self.module = load_hook_module("pr_related_issue_check")

    def test_body_double_quotes(self):
        """Test extracting body with double quotes."""
        command = 'gh pr create --title "test" --body "Fix authentication bug"'
        result = self.module.extract_pr_body(command)
        assert result == "Fix authentication bug"

    def test_body_single_quotes(self):
        """Test extracting body with single quotes."""
        command = "gh pr create --title 'test' --body 'Fix authentication bug'"
        result = self.module.extract_pr_body(command)
        assert result == "Fix authentication bug"

    def test_body_short_flag(self):
        """Test extracting body with -b flag."""
        command = 'gh pr create --title "test" -b "Fix authentication bug"'
        result = self.module.extract_pr_body(command)
        assert result == "Fix authentication bug"

    def test_no_body(self):
        """Test when no body is specified."""
        command = 'gh pr create --title "test"'
        result = self.module.extract_pr_body(command)
        assert result is None

    def test_heredoc_body(self):
        """Test extracting body from HEREDOC pattern."""
        command = '''gh pr create --title "test" --body "$(cat <<'EOF'
## Summary
Fix authentication bug
EOF
)"'''
        result = self.module.extract_pr_body(command)
        assert result is not None
        assert "authentication" in result


class TestExtractKeywords:
    """Tests for extract_keywords function."""

    def setup_method(self):
        self.module = load_hook_module("pr_related_issue_check")

    def test_extracts_keywords_from_title(self):
        """Should extract keywords from title."""
        result = self.module.extract_keywords("Add authentication feature", None)
        assert "authentication" in result

    def test_extracts_keywords_from_body(self):
        """Should extract keywords from body."""
        result = self.module.extract_keywords(None, "Implement logging system")
        assert "logging" in result
        assert "system" in result

    def test_extracts_keywords_from_both(self):
        """Should extract keywords from both title and body."""
        result = self.module.extract_keywords(
            "Authentication improvement", "Better security handling"
        )
        assert "Authentication" in result or "authentication" in [k.lower() for k in result]
        assert "security" in result or "security" in [k.lower() for k in result]

    def test_filters_stop_words(self):
        """Should filter out stop words."""
        result = self.module.extract_keywords("The quick fix for the bug", None)
        # 'the', 'for', 'fix', 'bug' are in STOP_WORDS, so only 'quick' remains
        assert "the" not in [k.lower() for k in result]
        assert "for" not in [k.lower() for k in result]
        assert "fix" not in [k.lower() for k in result]
        assert "bug" not in [k.lower() for k in result]
        assert "quick" in [k.lower() for k in result]

    def test_filters_short_words(self):
        """Should filter out words shorter than MIN_KEYWORD_LENGTH."""
        result = self.module.extract_keywords("A to do it", None)
        # All these words are too short
        assert len(result) == 0

    def test_limits_keyword_count(self):
        """Should limit to MAX_KEYWORDS."""
        result = self.module.extract_keywords(
            "authentication authorization validation sanitization serialization deserialization",
            None,
        )
        assert len(result) <= self.module.MAX_KEYWORDS

    def test_empty_input(self):
        """Should return empty list for empty input."""
        result = self.module.extract_keywords(None, None)
        assert result == []
        result = self.module.extract_keywords("", "")
        assert result == []

    def test_deduplicates_keywords(self):
        """Should not include duplicate keywords."""
        result = self.module.extract_keywords("authentication authentication", "authentication")
        auth_count = sum(1 for k in result if k.lower() == "authentication")
        assert auth_count <= 1

    def test_japanese_keywords(self):
        """Should extract Japanese keywords."""
        result = self.module.extract_keywords("認証機能の追加", None)
        # Should extract Japanese words
        assert len(result) > 0


class TestSearchRelatedIssues:
    """Tests for search_related_issues function."""

    def setup_method(self):
        self.module = load_hook_module("pr_related_issue_check")

    def test_empty_keywords_returns_empty(self):
        """Should return empty list for empty keywords."""
        result = self.module.search_related_issues([])
        assert result == []

    def test_handles_subprocess_error(self):
        """Should return empty list on subprocess error."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("Command not found")
            result = self.module.search_related_issues(["test"])
            assert result == []

    def test_handles_timeout(self):
        """Should return empty list on timeout."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("gh", 10)
            result = self.module.search_related_issues(["test"])
            assert result == []

    def test_handles_invalid_json(self):
        """Should return empty list on invalid JSON."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "not valid json"
            result = self.module.search_related_issues(["test"])
            assert result == []

    def test_handles_non_zero_return_code(self):
        """Should return empty list on non-zero return code."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = "[]"
            result = self.module.search_related_issues(["test"])
            assert result == []


class TestHookIntegration:
    """Integration tests for the hook."""

    def test_approve_non_pr_create_commands(self):
        """Should approve commands that are not gh pr create."""
        test_cases = [
            "ls -la",
            "git status",
            "gh pr view 123",
            "echo 'gh pr create'",
        ]
        for command in test_cases:
            result = run_hook({"tool_input": {"command": command}})
            assert result["decision"] == "approve"

    def test_approve_empty_command(self):
        """Should approve when command is empty."""
        result = run_hook({"tool_input": {"command": ""}})
        assert result["decision"] == "approve"

    def test_approve_pr_create_without_keywords(self):
        """Should approve when no keywords can be extracted."""
        result = run_hook({"tool_input": {"command": "gh pr create"}})
        assert result["decision"] == "approve"
