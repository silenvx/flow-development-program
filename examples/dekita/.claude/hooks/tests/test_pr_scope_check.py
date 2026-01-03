#!/usr/bin/env python3
"""Tests for pr-scope-check.py hook."""

import json
import subprocess
import sys
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "pr-scope-check.py"

# Add hooks directory to path for 'common' module import
_hooks_dir = str(HOOK_PATH.parent)
if _hooks_dir not in sys.path:
    sys.path.insert(0, _hooks_dir)


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestPrScopeCheckBasic:
    """Basic tests for pr-scope-check hook."""

    def test_approve_non_pr_create_commands(self):
        """Should approve commands that are not gh pr create."""
        test_cases = [
            "ls -la",
            "git status",
            "gh pr view 123",
            "gh pr merge 123 --squash",
            "echo 'gh pr create'",
        ]

        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "approve", f"Should approve: {command}"

    def test_approve_empty_command(self):
        """Should approve when command is empty."""
        result = run_hook({"tool_input": {"command": ""}})
        assert result["decision"] == "approve"

    def test_approve_no_tool_input(self):
        """Should approve when tool_input is missing."""
        result = run_hook({})
        assert result["decision"] == "approve"


class TestPrScopeCheckHelpers:
    """Tests for helper functions in pr-scope-check."""

    def setup_method(self):
        """Load the module once for all tests."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("pr_scope_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_extract_pr_title_double_quotes(self):
        """Test extracting title with double quotes."""
        title = self.module.extract_pr_title('gh pr create --title "fix: something (#123)"')
        assert title == "fix: something (#123)"

    def test_extract_pr_title_single_quotes(self):
        """Test extracting title with single quotes."""
        title = self.module.extract_pr_title("gh pr create --title 'fix: something (#123)'")
        assert title == "fix: something (#123)"

    def test_extract_pr_title_short_flag(self):
        """Test extracting title with -t flag."""
        title = self.module.extract_pr_title('gh pr create -t "fix: something"')
        assert title == "fix: something"

    def test_extract_pr_title_equals_sign(self):
        """Test extracting title with --title= format."""
        title = self.module.extract_pr_title('gh pr create --title="fix: something (#123)"')
        assert title == "fix: something (#123)"

    def test_extract_pr_title_short_flag_equals(self):
        """Test extracting title with -t= format."""
        title = self.module.extract_pr_title("gh pr create -t='fix: something'")
        assert title == "fix: something"

    def test_extract_pr_title_no_title(self):
        """Test when no title is specified."""
        title = self.module.extract_pr_title("gh pr create --body 'some body'")
        assert title is None

    def test_count_issue_references_single(self):
        """Test counting single Issue reference."""
        issues = self.module.count_issue_references("fix: something (#123)")
        assert issues == ["#123"]

    def test_count_issue_references_multiple(self):
        """Test counting multiple Issue references."""
        issues = self.module.count_issue_references("fix: issues #123, #456, #789")
        assert issues == ["#123", "#456", "#789"]

    def test_count_issue_references_none(self):
        """Test when no Issue references."""
        issues = self.module.count_issue_references("fix: something")
        assert issues == []


class TestPrScopeCheckBlocking:
    """Tests for blocking behavior."""

    def test_block_multiple_issues_in_title(self):
        """Should block when PR title contains multiple Issue references."""
        result = run_hook(
            {"tool_input": {"command": 'gh pr create --title "fix: issues #123 and #456"'}}
        )
        assert result["decision"] == "block"
        assert "#123" in result["reason"]
        assert "#456" in result["reason"]
        assert "1 Issue = 1 PR" in result["reason"]

    def test_block_multiple_issues_with_equals_syntax(self):
        """Should block when using --title= format with multiple Issues."""
        result = run_hook(
            {"tool_input": {"command": 'gh pr create --title="fix: issues #123 and #456"'}}
        )
        assert result["decision"] == "block"
        assert "#123" in result["reason"]
        assert "#456" in result["reason"]

    def test_approve_single_issue_in_title(self):
        """Should approve when PR title contains single Issue reference."""
        result = run_hook(
            {"tool_input": {"command": 'gh pr create --title "fix: something (#123)"'}}
        )
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "#123" in result["systemMessage"]

    def test_approve_no_issue_in_title(self):
        """Should approve when PR title contains no Issue references."""
        result = run_hook({"tool_input": {"command": 'gh pr create --title "fix: something"'}})
        assert result["decision"] == "approve"

    def test_approve_no_title_specified(self):
        """Should approve when no title is specified (GitHub will prompt)."""
        result = run_hook({"tool_input": {"command": "gh pr create --body 'some body'"}})
        assert result["decision"] == "approve"


class TestIsGhPrCreateCommand:
    """Tests for is_gh_pr_create_command function."""

    def setup_method(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("pr_scope_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_basic_gh_pr_create(self):
        """Should detect basic gh pr create commands."""
        assert self.module.is_gh_pr_create_command("gh pr create")
        assert self.module.is_gh_pr_create_command("gh pr create --title 'test'")
        assert self.module.is_gh_pr_create_command("gh  pr  create")

    def test_exclude_quoted_strings(self):
        """Should not detect gh pr create inside quoted strings."""
        assert not self.module.is_gh_pr_create_command("echo 'gh pr create'")
        assert not self.module.is_gh_pr_create_command('echo "gh pr create"')

    def test_empty_command(self):
        """Should return False for empty commands."""
        assert not self.module.is_gh_pr_create_command("")
        assert not self.module.is_gh_pr_create_command("   ")

    def test_non_pr_create_commands(self):
        """Should not detect other gh pr commands."""
        assert not self.module.is_gh_pr_create_command("gh pr view 123")
        assert not self.module.is_gh_pr_create_command("gh pr merge 123")


class TestHasBodyFileOption:
    """Tests for has_body_file_option function."""

    def setup_method(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("pr_scope_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_detect_body_file_long_option(self):
        """Should detect --body-file option."""
        assert self.module.has_body_file_option("gh pr create --body-file pr.md")
        assert self.module.has_body_file_option("gh pr create --body-file=pr.md")

    def test_detect_body_file_short_option(self):
        """Should detect -F option."""
        assert self.module.has_body_file_option("gh pr create -F pr.md")
        assert self.module.has_body_file_option("gh pr create -F=pr.md")

    def test_detect_body_file_attached_value(self):
        """Should detect -Ffilename (attached value without space)."""
        assert self.module.has_body_file_option("gh pr create -Fpr.md")
        assert self.module.has_body_file_option("gh pr create -F-")  # stdin

    def test_detect_body_file_with_stdin(self):
        """Should detect -F - (stdin) option."""
        assert self.module.has_body_file_option("gh pr create -F -")

    def test_no_body_file_option(self):
        """Should return False when no body-file option."""
        assert not self.module.has_body_file_option("gh pr create --title 'test'")
        assert not self.module.has_body_file_option("gh pr create --body 'inline body'")

    def test_body_file_with_title(self):
        """Should detect -F even when --title is present."""
        assert self.module.has_body_file_option("gh pr create --title 'test' -F pr.md")

    def test_ignore_body_file_in_quoted_string(self):
        """Should not detect -F inside quoted strings."""
        assert not self.module.has_body_file_option("echo '-F pr.md'")
        assert not self.module.has_body_file_option('echo "--body-file pr.md"')

    def test_body_file_at_end_of_command(self):
        """Should detect -F at end of command (though unusual)."""
        # Note: -F at end without value is unusual but we detect the pattern
        # The actual behavior is that gh pr create would error, but we warn anyway
        assert not self.module.has_body_file_option("gh pr create -F")  # No value after -F


class TestBodyFileWarning:
    """Tests for body-file warning behavior."""

    def test_warn_body_file_without_title(self):
        """Should emit warning when -F is used without --title."""
        result = run_hook({"tool_input": {"command": "gh pr create -F pr.md"}})
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "-F" in result["systemMessage"]
        assert "--body-file" in result["systemMessage"]

    def test_warn_body_file_long_option_without_title(self):
        """Should emit warning when --body-file is used without --title."""
        result = run_hook({"tool_input": {"command": "gh pr create --body-file pr.md"}})
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "-F" in result["systemMessage"]

    def test_no_warn_body_file_with_title(self):
        """Should not emit body-file warning when --title is present."""
        result = run_hook(
            {"tool_input": {"command": 'gh pr create -F pr.md --title "fix: test (#123)"'}}
        )
        assert result["decision"] == "approve"
        # Should have single issue message, not body-file warning
        assert "systemMessage" in result
        assert "#123" in result["systemMessage"]
        assert "-F" not in result["systemMessage"]

    def test_no_warn_without_body_file(self):
        """Should not emit warning when -F is not used."""
        result = run_hook({"tool_input": {"command": "gh pr create"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result
