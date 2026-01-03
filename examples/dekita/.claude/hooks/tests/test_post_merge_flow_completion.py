#!/usr/bin/env python3
# Design reviewed: 2025-12-22
"""Tests for post-merge-flow-completion.py."""

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add hooks directory to path for common module
hooks_dir = Path(__file__).parent.parent
if str(hooks_dir) not in sys.path:
    sys.path.insert(0, str(hooks_dir))


# Load modules with hyphenated filenames
def load_module(name: str, filename: str):
    """Load a Python module from a hyphenated filename."""
    spec = importlib.util.spec_from_file_location(
        name,
        Path(__file__).parent.parent / filename,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


post_merge = load_module("post_merge_flow_completion", "post-merge-flow-completion.py")


class TestIsPrMergeCommand:
    """Tests for is_pr_merge_command function."""

    def test_detects_merge_command(self):
        """Test that gh pr merge is detected."""
        assert post_merge.is_pr_merge_command("gh pr merge 123")
        assert post_merge.is_pr_merge_command("gh pr merge")

    def test_rejects_non_merge_commands(self):
        """Test that non-merge commands are rejected."""
        assert not post_merge.is_pr_merge_command("gh pr view 123")
        assert not post_merge.is_pr_merge_command("git merge main")


class TestIsMergeSuccess:
    """Tests for is_merge_success function (common.py version).

    Note: is_merge_success was moved to common.py with signature:
    is_merge_success(exit_code, stdout, command, *, stderr="")
    The order of arguments changed from (stdout, exit_code, command) to (exit_code, stdout, command).
    """

    def test_failure_exit_code(self):
        """Test that non-zero exit code returns False."""
        from lib.repo import is_merge_success

        assert not is_merge_success(1, "", "gh pr merge 123")

    def test_auto_merge_is_not_success(self):
        """Test that --auto flag is not treated as immediate success."""
        from lib.repo import is_merge_success

        assert not is_merge_success(0, "", "gh pr merge --auto 123")

    def test_explicit_success_patterns(self):
        """Test that explicit success patterns are detected."""
        from lib.repo import is_merge_success

        assert is_merge_success(0, "Merged pull request", "gh pr merge 123")
        assert is_merge_success(0, "Pull request #123 merged", "gh pr merge")
        assert is_merge_success(0, "was already merged", "gh pr merge 123")

    def test_empty_output_is_success(self):
        """Test that empty output with exit code 0 is success (squash merge)."""
        from lib.repo import is_merge_success

        assert is_merge_success(0, "", "gh pr merge 123")

    def test_unknown_output_is_not_success(self):
        """Test that unknown output is not treated as success."""
        from lib.repo import is_merge_success

        assert not is_merge_success(0, "Some unknown output", "gh pr merge 123")


class TestExtractPrNumber:
    """Tests for extract_pr_number function."""

    @patch("post_merge_flow_completion.common_extract_pr_number")
    def test_extracts_from_command_via_common(self, mock_common):
        """Test that PR number is extracted using common.extract_pr_number."""
        mock_common.return_value = "123"
        result = post_merge.extract_pr_number("gh pr merge 123")
        assert result == 123
        mock_common.assert_called_once_with("gh pr merge 123")

    @patch("post_merge_flow_completion.common_extract_pr_number")
    @patch("post_merge_flow_completion.subprocess.run")
    def test_fallback_to_current_branch(self, mock_run, mock_common):
        """Test fallback to current branch PR when no number in command."""
        mock_common.return_value = None  # No PR number in command
        mock_run.return_value = MagicMock(returncode=0, stdout='{"number": 789}')
        result = post_merge.extract_pr_number("gh pr merge --squash")
        assert result == 789

    @patch("post_merge_flow_completion.common_extract_pr_number")
    @patch("post_merge_flow_completion.subprocess.run")
    def test_returns_none_when_all_fail(self, mock_run, mock_common):
        """Test returns None when command has no number and gh pr view fails."""
        mock_common.return_value = None
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = post_merge.extract_pr_number("gh pr merge --squash")
        assert result is None


class TestGetIssueFromPr:
    """Tests for get_issue_from_pr function."""

    @patch("post_merge_flow_completion.subprocess.run")
    def test_extracts_from_closes_keyword(self, mock_run):
        """Test that issue is extracted from Closes keyword."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"body": "Closes #123", "title": "Some PR", "headRefName": "main"}',
        )
        assert post_merge.get_issue_from_pr(1) == 123

    @patch("post_merge_flow_completion.subprocess.run")
    def test_extracts_from_fixes_keyword(self, mock_run):
        """Test that issue is extracted from Fixes keyword."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"body": "Fixes #456", "title": "Some PR", "headRefName": "main"}',
        )
        assert post_merge.get_issue_from_pr(1) == 456

    @patch("post_merge_flow_completion.subprocess.run")
    def test_extracts_from_branch_name(self, mock_run):
        """Test that issue is extracted from branch name (single API call)."""
        # Now uses single API call with all fields
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"body": "", "title": "Some PR", "headRefName": "feat/issue-789-feature"}',
        )
        assert post_merge.get_issue_from_pr(1) == 789

    @patch("post_merge_flow_completion.subprocess.run")
    def test_returns_none_when_no_issue_found(self, mock_run):
        """Test that None is returned when no issue is found."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"body": "", "title": "Some PR", "headRefName": "main"}'
        )
        assert post_merge.get_issue_from_pr(1) is None

    @patch("post_merge_flow_completion.subprocess.run")
    def test_handles_api_failure(self, mock_run):
        """Test that None is returned when API call fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert post_merge.get_issue_from_pr(1) is None


class TestAddCompletionComment:
    """Tests for add_completion_comment function."""

    @patch("post_merge_flow_completion.subprocess.run")
    def test_success(self, mock_run):
        """Test successful comment addition."""
        mock_run.return_value = MagicMock(returncode=0)
        result = post_merge.add_completion_comment(123, 456)
        assert result
        # Verify gh issue comment was called with correct args
        call_args = mock_run.call_args
        assert "gh" in call_args[0][0]
        assert "issue" in call_args[0][0]
        assert "comment" in call_args[0][0]
        assert "123" in call_args[0][0]

    @patch("post_merge_flow_completion.subprocess.run")
    def test_failure(self, mock_run):
        """Test failed comment addition."""
        mock_run.return_value = MagicMock(returncode=1)
        result = post_merge.add_completion_comment(123, 456)
        assert not result

    @patch("post_merge_flow_completion.subprocess.run")
    def test_timeout(self, mock_run):
        """Test timeout handling."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=10)
        result = post_merge.add_completion_comment(123, 456)
        assert not result


class TestMain:
    """Tests for main function (integration)."""

    @patch("post_merge_flow_completion.add_completion_comment")
    @patch("post_merge_flow_completion.get_issue_from_pr")
    @patch("post_merge_flow_completion.extract_pr_number")
    def test_successful_flow(self, mock_extract_pr, mock_get_issue, mock_add_comment):
        """Test successful end-to-end flow."""
        mock_extract_pr.return_value = 123
        mock_get_issue.return_value = 456
        mock_add_comment.return_value = True

        # Issue #2203: Use tool_result format for consistent exit_code handling
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 123"},
            "tool_output": "Merged pull request",
            "tool_result": {"exit_code": 0, "stdout": "Merged pull request", "stderr": ""},
        }

        with patch.object(sys, "stdin", io.StringIO(json.dumps(input_data))):
            with patch("builtins.print"):
                with patch("post_merge_flow_completion.log_hook_execution"):
                    post_merge.main()

        mock_add_comment.assert_called_once_with(456, 123)

    @patch("post_merge_flow_completion.add_completion_comment")
    @patch("post_merge_flow_completion.get_issue_from_pr")
    @patch("post_merge_flow_completion.extract_pr_number")
    def test_skips_non_bash_tool(self, mock_extract_pr, mock_get_issue, mock_add_comment):
        """Test that non-Bash tools are skipped."""
        input_data = {
            "tool_name": "Edit",
            "tool_input": {"command": "gh pr merge 123"},
        }

        with patch.object(sys, "stdin", io.StringIO(json.dumps(input_data))):
            post_merge.main()

        mock_extract_pr.assert_not_called()

    @patch("post_merge_flow_completion.add_completion_comment")
    @patch("post_merge_flow_completion.get_issue_from_pr")
    @patch("post_merge_flow_completion.extract_pr_number")
    def test_skips_non_merge_command(self, mock_extract_pr, mock_get_issue, mock_add_comment):
        """Test that non-merge commands are skipped."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr view 123"},
        }

        with patch.object(sys, "stdin", io.StringIO(json.dumps(input_data))):
            post_merge.main()

        mock_extract_pr.assert_not_called()

    @patch("post_merge_flow_completion.add_completion_comment")
    @patch("post_merge_flow_completion.get_issue_from_pr")
    @patch("post_merge_flow_completion.extract_pr_number")
    def test_skips_failed_merge(self, mock_extract_pr, mock_get_issue, mock_add_comment):
        """Test that failed merges are skipped."""
        # Issue #2203: Use tool_result format for consistent exit_code handling
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 123"},
            "tool_output": "",
            "tool_result": {"exit_code": 1, "stdout": "", "stderr": ""},  # Failed
        }

        with patch.object(sys, "stdin", io.StringIO(json.dumps(input_data))):
            post_merge.main()

        mock_extract_pr.assert_not_called()

    @patch("post_merge_flow_completion.log_hook_execution")
    @patch("post_merge_flow_completion.add_completion_comment")
    @patch("post_merge_flow_completion.get_issue_from_pr")
    @patch("post_merge_flow_completion.extract_pr_number")
    def test_skips_when_no_issue(self, mock_extract_pr, mock_get_issue, mock_add_comment, mock_log):
        """Test that processing is skipped when no issue is linked."""
        mock_extract_pr.return_value = 123
        mock_get_issue.return_value = None  # No linked issue

        # Issue #2203: Use tool_result format for consistent exit_code handling
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 123"},
            "tool_output": "Merged pull request",
            "tool_result": {"exit_code": 0, "stdout": "Merged pull request", "stderr": ""},
        }

        with patch.object(sys, "stdin", io.StringIO(json.dumps(input_data))):
            post_merge.main()

        mock_add_comment.assert_not_called()
        # Verify log was called indicating skip
        mock_log.assert_called()

    @patch("post_merge_flow_completion.log_hook_execution")
    @patch("post_merge_flow_completion.add_completion_comment")
    @patch("post_merge_flow_completion.get_issue_from_pr")
    @patch("post_merge_flow_completion.extract_pr_number")
    def test_skips_when_no_pr_number(
        self, mock_extract_pr, mock_get_issue, mock_add_comment, mock_log
    ):
        """Test that processing is skipped when PR number cannot be extracted."""
        mock_extract_pr.return_value = None  # Cannot extract PR number

        # Issue #2203: Use tool_result format for consistent exit_code handling
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge"},
            "tool_output": "Merged pull request",
            "tool_result": {"exit_code": 0, "stdout": "Merged pull request", "stderr": ""},
        }

        with patch.object(sys, "stdin", io.StringIO(json.dumps(input_data))):
            post_merge.main()

        mock_get_issue.assert_not_called()
        mock_add_comment.assert_not_called()
        # Verify log was called indicating skip
        mock_log.assert_called()

    @patch("post_merge_flow_completion.log_hook_execution")
    @patch("post_merge_flow_completion.add_completion_comment")
    @patch("post_merge_flow_completion.get_issue_from_pr")
    @patch("post_merge_flow_completion.extract_pr_number")
    def test_handles_comment_failure(
        self, mock_extract_pr, mock_get_issue, mock_add_comment, mock_log
    ):
        """Test that comment addition failure is handled gracefully."""
        mock_extract_pr.return_value = 123
        mock_get_issue.return_value = 456
        mock_add_comment.return_value = False  # Comment addition failed

        # Issue #2203: Use tool_result format for consistent exit_code handling
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 123"},
            "tool_output": "Merged pull request",
            "tool_result": {"exit_code": 0, "stdout": "Merged pull request", "stderr": ""},
        }

        with patch.object(sys, "stdin", io.StringIO(json.dumps(input_data))):
            with patch("builtins.print"):
                post_merge.main()

        mock_add_comment.assert_called_once_with(456, 123)
        # Verify log was called indicating failure
        mock_log.assert_called()
