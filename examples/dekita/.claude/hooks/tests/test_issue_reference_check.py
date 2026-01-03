#!/usr/bin/env python3
"""Tests for issue-reference-check.py hook.

Issue #2059: Validates Issue references in comments before posting.
"""

import importlib.util
import sys
from pathlib import Path
from unittest import mock

HOOK_PATH = Path(__file__).parent.parent / "issue-reference-check.py"


def load_module():
    """Load the hook module for testing."""
    parent_dir = str(Path(__file__).parent.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    spec = importlib.util.spec_from_file_location("issue_reference_check", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestIsCommentCommand:
    """Tests for is_comment_command function."""

    def test_gh_pr_comment(self):
        """Should detect gh pr comment."""
        module = load_module()
        assert module.is_comment_command('gh pr comment 123 -b "test"') is True

    def test_gh_issue_comment(self):
        """Should detect gh issue comment."""
        module = load_module()
        assert module.is_comment_command('gh issue comment 456 --body "test"') is True

    def test_gh_api_replies(self):
        """Should detect gh api .../replies."""
        module = load_module()
        cmd = 'gh api /repos/owner/repo/pulls/comments/123/replies -f body="test"'
        assert module.is_comment_command(cmd) is True

    def test_graphql_reply(self):
        """Should detect GraphQL addPullRequestReviewThreadReply."""
        module = load_module()
        cmd = 'gh api graphql -f query="mutation { addPullRequestReviewThreadReply ..."'
        assert module.is_comment_command(cmd) is True

    def test_non_comment_command(self):
        """Should not match non-comment commands."""
        module = load_module()
        assert module.is_comment_command("gh pr list") is False
        assert module.is_comment_command("gh issue view 123") is False
        assert module.is_comment_command("git commit -m 'test'") is False


class TestExtractCommentBody:
    """Tests for extract_comment_body function."""

    def test_double_quoted_body(self):
        """Should extract body from -b 'message'."""
        module = load_module()
        body = module.extract_comment_body('gh pr comment 123 -b "Issue #999 created"')
        assert body == "Issue #999 created"

    def test_single_quoted_body(self):
        """Should extract body from -b 'message'."""
        module = load_module()
        body = module.extract_comment_body("gh pr comment 123 -b 'Issue #888 created'")
        assert body == "Issue #888 created"

    def test_long_body_flag(self):
        """Should extract body from --body 'message'."""
        module = load_module()
        body = module.extract_comment_body('gh issue comment 456 --body "See #777"')
        assert body == "See #777"

    def test_heredoc(self):
        """Should extract body from HEREDOC."""
        module = load_module()
        cmd = """gh pr comment 123 -b "$(cat <<'EOF'
Issue #666 is relevant.
More text here.
EOF
)"
"""
        body = module.extract_comment_body(cmd)
        assert body is not None
        assert "#666" in body

    def test_graphql_body(self):
        """Should extract body from GraphQL mutation."""
        module = load_module()
        cmd = "gh api graphql -f query='mutation { addPullRequestReviewThreadReply(input: {body: \"See #555\"}) }'"
        body = module.extract_comment_body(cmd)
        assert body == "See #555"

    def test_no_body(self):
        """Should return None when no body found."""
        module = load_module()
        body = module.extract_comment_body("gh pr comment 123")
        assert body is None

    def test_body_file(self, tmp_path):
        """Should extract body from --body-file."""
        module = load_module()
        body_file = tmp_path / "body.txt"
        body_file.write_text("Issue #444 referenced from file")
        cmd = f"gh pr comment 123 --body-file {body_file}"
        body = module.extract_comment_body(cmd)
        assert body == "Issue #444 referenced from file"

    def test_body_file_not_found(self):
        """Should return None when body file not found."""
        module = load_module()
        cmd = "gh pr comment 123 --body-file /nonexistent/path.txt"
        body = module.extract_comment_body(cmd)
        assert body is None

    def test_gh_api_body_at_file(self, tmp_path):
        """Should extract body from -F body=@file (gh api format)."""
        module = load_module()
        body_file = tmp_path / "msg.txt"
        body_file.write_text("See Issue #333")
        cmd = f"gh api /repos/owner/repo/pulls/comments/123/replies -F body=@{body_file}"
        body = module.extract_comment_body(cmd)
        assert body == "See Issue #333"

    def test_gh_api_inline_body(self):
        """Should extract body from -f body='message' (gh api inline format)."""
        module = load_module()
        cmd = 'gh api /repos/owner/repo/pulls/comments/123/replies -f body="Issue #222 created"'
        body = module.extract_comment_body(cmd)
        assert body == "Issue #222 created"


class TestReadBodyFromFile:
    """Tests for read_body_from_file function."""

    def test_read_file(self, tmp_path):
        """Should read file content."""
        module = load_module()
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello #123")
        result = module.read_body_from_file(str(test_file))
        assert result == "Hello #123"

    def test_file_not_found(self):
        """Should return None when file not found."""
        module = load_module()
        result = module.read_body_from_file("/nonexistent/file.txt")
        assert result is None


class TestExtractIssueReferences:
    """Tests for extract_issue_references function."""

    def test_simple_reference(self):
        """Should extract simple Issue reference."""
        module = load_module()
        refs = module.extract_issue_references("Created Issue #1234")
        assert refs == [1234]

    def test_multiple_references(self):
        """Should extract multiple Issue references."""
        module = load_module()
        refs = module.extract_issue_references("See #100 and #200 for details")
        assert sorted(refs) == [100, 200]

    def test_closes_excluded(self):
        """Should exclude Closes #xxx patterns."""
        module = load_module()
        refs = module.extract_issue_references("Closes #123\nSee also #456")
        assert refs == [456]

    def test_fixes_excluded(self):
        """Should exclude Fixes #xxx patterns."""
        module = load_module()
        refs = module.extract_issue_references("Fixes #789. Related: #111")
        assert refs == [111]

    def test_resolves_excluded(self):
        """Should exclude Resolves #xxx patterns."""
        module = load_module()
        refs = module.extract_issue_references("Resolves #222. See #333")
        assert refs == [333]

    def test_no_references(self):
        """Should return empty list when no references."""
        module = load_module()
        refs = module.extract_issue_references("No issue mentioned here")
        assert refs == []

    def test_only_closes(self):
        """Should return empty list when only Closes patterns."""
        module = load_module()
        refs = module.extract_issue_references("Closes #123")
        assert refs == []

    def test_pr_excluded(self):
        """Should exclude PR #xxx patterns."""
        module = load_module()
        refs = module.extract_issue_references("See PR #123 and Issue #456")
        assert refs == [456]

    def test_only_pr(self):
        """Should return empty list when only PR patterns."""
        module = load_module()
        refs = module.extract_issue_references("See PR #123")
        assert refs == []


class TestCheckIssueExists:
    """Tests for check_issue_exists function."""

    def test_exists(self):
        """Should return True when Issue exists."""
        module = load_module()
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout='{"number":123}')
            assert module.check_issue_exists(123) is True

    def test_not_exists(self):
        """Should return False when Issue doesn't exist."""
        module = load_module()
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1, stdout="", stderr="not found")
            assert module.check_issue_exists(99999) is False

    def test_timeout(self):
        """Should return True on timeout (fail-open)."""
        module = load_module()
        import subprocess

        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("gh", 10)
            assert module.check_issue_exists(123) is True

    def test_network_error_fail_open(self):
        """Should return True on network/auth errors (fail-open)."""
        module = load_module()
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1, stdout="", stderr="connection refused")
            assert module.check_issue_exists(123) is True


class TestMainFunction:
    """Tests for main function."""

    def test_approve_non_bash(self):
        """Should approve non-Bash tools."""
        module = load_module()
        with mock.patch.object(module, "parse_hook_input", return_value={"tool_name": "Edit"}):
            with mock.patch.object(module, "log_hook_execution"):
                with mock.patch("builtins.print") as mock_print:
                    module.main()
                    output = mock_print.call_args[0][0]
                    assert '"decision": "approve"' in output

    def test_approve_non_comment_command(self):
        """Should approve non-comment Bash commands."""
        module = load_module()
        with mock.patch.object(
            module,
            "parse_hook_input",
            return_value={
                "tool_name": "Bash",
                "tool_input": {"command": "git status"},
            },
        ):
            with mock.patch.object(module, "log_hook_execution"):
                with mock.patch("builtins.print") as mock_print:
                    module.main()
                    output = mock_print.call_args[0][0]
                    assert '"decision": "approve"' in output

    def test_block_non_existent_issue(self):
        """Should block when referencing non-existent Issue."""
        module = load_module()
        with mock.patch.object(
            module,
            "parse_hook_input",
            return_value={
                "tool_name": "Bash",
                "tool_input": {"command": 'gh pr comment 1 -b "Issue #99999 created"'},
            },
        ):
            with mock.patch.object(module, "check_issue_exists", return_value=False):
                with mock.patch.object(module, "log_hook_execution"):
                    with mock.patch("builtins.print") as mock_print:
                        module.main()
                        output = mock_print.call_args[0][0]
                        assert '"decision": "block"' in output
                        assert "99999" in output

    def test_approve_existent_issue(self):
        """Should approve when referencing existent Issue."""
        module = load_module()
        with mock.patch.object(
            module,
            "parse_hook_input",
            return_value={
                "tool_name": "Bash",
                "tool_input": {"command": 'gh pr comment 1 -b "See Issue #123"'},
            },
        ):
            with mock.patch.object(module, "check_issue_exists", return_value=True):
                with mock.patch.object(module, "log_hook_execution"):
                    with mock.patch("builtins.print") as mock_print:
                        module.main()
                        output = mock_print.call_args[0][0]
                        assert '"decision": "approve"' in output

    def test_approve_closes_only(self):
        """Should approve when only Closes #xxx is referenced."""
        module = load_module()
        with mock.patch.object(
            module,
            "parse_hook_input",
            return_value={
                "tool_name": "Bash",
                "tool_input": {"command": 'gh pr comment 1 -b "Closes #123"'},
            },
        ):
            with mock.patch.object(module, "log_hook_execution"):
                with mock.patch("builtins.print") as mock_print:
                    module.main()
                    output = mock_print.call_args[0][0]
                    assert '"decision": "approve"' in output

    def test_approve_cross_repo_command(self):
        """Should approve cross-repo commands without validation (fail-open)."""
        module = load_module()
        with mock.patch.object(
            module,
            "parse_hook_input",
            return_value={
                "tool_name": "Bash",
                "tool_input": {"command": 'gh pr comment 1 --repo other/repo -b "Issue #99999"'},
            },
        ):
            with mock.patch.object(module, "log_hook_execution"):
                with mock.patch("builtins.print") as mock_print:
                    module.main()
                    output = mock_print.call_args[0][0]
                    assert '"decision": "approve"' in output
