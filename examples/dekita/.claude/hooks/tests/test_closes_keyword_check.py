#!/usr/bin/env python3
"""Tests for closes-keyword-check.py hook."""

import json
import subprocess

from conftest import HOOKS_DIR, load_hook_module

HOOK_PATH = HOOKS_DIR / "closes-keyword-check.py"


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestExtractIssueFromBranch:
    """Tests for extract_issue_from_branch function."""

    def setup_method(self):
        self.module = load_hook_module("closes-keyword-check")

    def test_issue_number_pattern(self):
        """Test various issue-N patterns."""
        test_cases = [
            ("issue-123", "#123"),
            ("issue/123", "#123"),
            ("fix/issue-456-description", "#456"),
            ("feature/issue-789", "#789"),
        ]
        for branch, expected in test_cases:
            with self.subTest(branch=branch):
                result = self.module.extract_issue_from_branch(branch)
                assert result == expected

    def test_prefix_number_pattern(self):
        """Test prefix-N patterns like fix-123, feat/456."""
        test_cases = [
            ("fix-123", "#123"),
            ("fix/123-description", "#123"),
            ("feat-456-new-feature", "#456"),
            ("feature/789-something", "#789"),
            ("bug-100-bugfix", "#100"),
            ("hotfix-200", "#200"),
            ("chore/300-cleanup", "#300"),
            ("refactor-400-restructure", "#400"),
        ]
        for branch, expected in test_cases:
            with self.subTest(branch=branch):
                result = self.module.extract_issue_from_branch(branch)
                assert result == expected

    def test_no_issue_number(self):
        """Test branches without Issue numbers."""
        test_cases = [
            "main",
            "develop",
            "feature/description-only",
            "fix/no-number",
        ]
        for branch in test_cases:
            with self.subTest(branch=branch):
                result = self.module.extract_issue_from_branch(branch)
                assert result is None

    def test_empty_or_none(self):
        """Test empty or None branch names."""
        assert self.module.extract_issue_from_branch("") is None
        assert self.module.extract_issue_from_branch(None) is None


class TestExtractPrBody:
    """Tests for extract_pr_body function."""

    def setup_method(self):
        self.module = load_hook_module("closes-keyword-check")

    def test_body_double_quotes(self):
        """Test extracting body with double quotes."""
        command = 'gh pr create --title "test" --body "Closes #123"'
        result = self.module.extract_pr_body(command)
        assert result == "Closes #123"

    def test_body_single_quotes(self):
        """Test extracting body with single quotes."""
        command = "gh pr create --title 'test' --body 'Closes #123'"
        result = self.module.extract_pr_body(command)
        assert result == "Closes #123"

    def test_body_short_flag(self):
        """Test extracting body with -b flag."""
        command = 'gh pr create --title "test" -b "Closes #123"'
        result = self.module.extract_pr_body(command)
        assert result == "Closes #123"

    def test_body_equals_sign(self):
        """Test extracting body with --body= format."""
        command = 'gh pr create --title "test" --body="Closes #123"'
        result = self.module.extract_pr_body(command)
        assert result == "Closes #123"

    def test_no_body(self):
        """Test when no body is specified."""
        command = 'gh pr create --title "test"'
        result = self.module.extract_pr_body(command)
        assert result is None

    def test_heredoc_body(self):
        """Test extracting body from HEREDOC pattern."""
        command = '''gh pr create --title "test" --body "$(cat <<'EOF'
## Summary
Some description

Closes #123
EOF
)"'''
        result = self.module.extract_pr_body(command)
        assert result is not None
        assert "Closes #123" in result

    def test_heredoc_body_without_quotes(self):
        """Test HEREDOC with unquoted delimiter."""
        command = '''gh pr create --title "test" --body "$(cat <<EOF
Closes #456
EOF
)"'''
        result = self.module.extract_pr_body(command)
        assert result is not None
        assert "Closes #456" in result

    def test_body_with_escaped_double_quotes(self):
        """Test extracting body with escaped double quotes (Issue #218)."""
        command = r'gh pr create --body "Fix \"foo\" bug. Closes #123"'
        result = self.module.extract_pr_body(command)
        assert result is not None
        assert "Closes #123" in result
        assert r"\"foo\"" in result

    def test_body_with_escaped_single_quotes(self):
        """Test extracting body with escaped single quotes."""
        command = r"gh pr create --body 'Fix \'bar\' bug. Closes #456'"
        result = self.module.extract_pr_body(command)
        assert result is not None
        assert "Closes #456" in result
        assert r"\'bar\'" in result

    def test_body_with_escaped_backslash(self):
        """Test extracting body with escaped backslash."""
        command = r'gh pr create --body "Path: C:\\Users. Closes #789"'
        result = self.module.extract_pr_body(command)
        assert result is not None
        assert "Closes #789" in result

    def test_body_with_json_content(self):
        """Test extracting body with JSON-like content containing escaped quotes."""
        command = r'gh pr create --body="JSON: {\"key\": \"value\"}. Closes #200"'
        result = self.module.extract_pr_body(command)
        assert result is not None
        assert "Closes #200" in result

    def test_body_with_multiple_escapes(self):
        """Test extracting body with multiple escape sequences."""
        command = r'gh pr create --body "Fix \"foo\" and \"bar\" bugs. Closes #100"'
        result = self.module.extract_pr_body(command)
        assert result is not None
        assert "Closes #100" in result


class TestHasClosesKeyword:
    """Tests for has_closes_keyword function."""

    def setup_method(self):
        self.module = load_hook_module("closes-keyword-check")

    def test_closes_keyword(self):
        """Test various Closes keywords."""
        test_cases = [
            ("Closes #123", "#123", True),
            ("closes #123", "#123", True),
            ("CLOSES #123", "#123", True),
            ("Close #123", "#123", True),
            ("Closed #123", "#123", True),
            # Colon format (GitHub also accepts this)
            ("Closes: #123", "#123", True),
            ("closes: #123", "#123", True),
        ]
        for body, issue, expected in test_cases:
            with self.subTest(body=body):
                result = self.module.has_closes_keyword(body, issue)
                assert result == expected

    def test_fixes_keyword(self):
        """Test various Fixes keywords."""
        test_cases = [
            ("Fixes #123", "#123", True),
            ("fixes #123", "#123", True),
            ("Fix #123", "#123", True),
            ("Fixed #123", "#123", True),
        ]
        for body, issue, expected in test_cases:
            with self.subTest(body=body):
                result = self.module.has_closes_keyword(body, issue)
                assert result == expected

    def test_resolves_keyword(self):
        """Test various Resolves keywords."""
        test_cases = [
            ("Resolves #123", "#123", True),
            ("resolves #123", "#123", True),
            ("Resolve #123", "#123", True),
            ("Resolved #123", "#123", True),
        ]
        for body, issue, expected in test_cases:
            with self.subTest(body=body):
                result = self.module.has_closes_keyword(body, issue)
                assert result == expected

    def test_wrong_issue_number(self):
        """Test when body has wrong Issue number."""
        result = self.module.has_closes_keyword("Closes #456", "#123")
        assert not result

    def test_no_keyword(self):
        """Test when body has no closing keyword."""
        result = self.module.has_closes_keyword("Related to #123", "#123")
        assert not result

    def test_empty_body(self):
        """Test empty body."""
        assert not self.module.has_closes_keyword("", "#123")
        assert not self.module.has_closes_keyword(None, "#123")

    def test_keyword_in_multiline_body(self):
        """Test keyword in multiline body."""
        body = """## Summary
Some description

Closes #123
"""
        result = self.module.has_closes_keyword(body, "#123")
        assert result


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
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "approve"

    def test_approve_empty_command(self):
        """Should approve when command is empty."""
        result = run_hook({"tool_input": {"command": ""}})
        assert result["decision"] == "approve"


class TestIsGhPrCreateCommand:
    """Tests for is_gh_pr_create_command function."""

    def setup_method(self):
        self.module = load_hook_module("closes-keyword-check")

    def test_basic_gh_pr_create(self):
        """Should detect basic gh pr create commands."""
        assert self.module.is_gh_pr_create_command("gh pr create")
        assert self.module.is_gh_pr_create_command("gh pr create --title 'test'")

    def test_exclude_quoted_strings(self):
        """Should not detect gh pr create inside quoted strings."""
        assert not self.module.is_gh_pr_create_command("echo 'gh pr create'")
        assert not self.module.is_gh_pr_create_command('echo "gh pr create"')

    def test_empty_command(self):
        """Should return False for empty commands."""
        assert not self.module.is_gh_pr_create_command("")


class TestHasBodyFileOption:
    """Tests for has_body_file_option function."""

    def setup_method(self):
        self.module = load_hook_module("closes-keyword-check")

    def test_body_file_long_option(self):
        """Should detect --body-file option."""
        command = 'gh pr create --title "test" --body-file PR_TEMPLATE.md'
        assert self.module.has_body_file_option(command)

    def test_body_file_short_option(self):
        """Should detect -F option."""
        command = 'gh pr create --title "test" -F PR_TEMPLATE.md'
        assert self.module.has_body_file_option(command)

    def test_no_body_file_option(self):
        """Should return False when no body-file option."""
        command = 'gh pr create --title "test" --body "Closes #123"'
        assert not self.module.has_body_file_option(command)

    def test_body_file_in_quoted_string(self):
        """Should not detect body-file inside quoted strings."""
        command = "echo '--body-file test.md'"
        assert not self.module.has_body_file_option(command)
