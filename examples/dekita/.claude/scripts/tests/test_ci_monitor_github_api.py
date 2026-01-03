#!/usr/bin/env python3
"""Unit tests for ci_monitor.github_api module."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add scripts directory to path so we can import ci_monitor package
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the ci_monitor package for attribute access (Issue #2624)
import ci_monitor

# Import directly from ci_monitor package (Issue #2624)
from ci_monitor import (
    run_gh_command_with_error,
)


class TestRunGhCommandWithError:
    """Tests for run_gh_command_with_error function."""

    @patch("subprocess.run")
    def test_success_returns_stdout_and_empty_stderr(self, mock_run):
        """Test successful command returns (True, stdout, stderr)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="output text",
            stderr="",
        )

        success, stdout, stderr = run_gh_command_with_error(["pr", "view", "123"])

        assert success is True
        assert stdout == "output text"
        assert stderr == ""

    @patch("subprocess.run")
    def test_failure_returns_stderr(self, mock_run):
        """Test failed command returns stderr for diagnosis."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error: Not Found",
        )

        success, stdout, stderr = run_gh_command_with_error(["pr", "view", "999"])

        assert success is False
        assert stdout == ""
        assert stderr == "error: Not Found"

    @patch("subprocess.run")
    def test_timeout_returns_error_message(self, mock_run):
        """Test timeout returns appropriate error message."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)

        success, stdout, stderr = run_gh_command_with_error(["pr", "view", "123"])

        assert success is False
        assert stdout == ""
        assert stderr == "Command timed out"

    @patch("subprocess.run")
    def test_exception_returns_error_string(self, mock_run):
        """Test other exceptions return error as string."""
        mock_run.side_effect = OSError("Network is unreachable")

        success, stdout, stderr = run_gh_command_with_error(["pr", "view", "123"])

        assert success is False
        assert stdout == ""
        assert "Network is unreachable" in stderr

    @patch("subprocess.run")
    def test_rate_limit_error_captured(self, mock_run):
        """Test that rate limit errors are captured in stderr."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="API rate limit exceeded for user",
        )

        success, stdout, stderr = run_gh_command_with_error(["pr", "view", "123"])

        assert success is False
        assert "rate limit" in stderr.lower()


class TestGetPrChangedFiles:
    """Test the get_pr_changed_files function."""

    @patch("ci_monitor.github_api.run_gh_command")
    def test_returns_set_of_changed_files(self, mock_run_gh):
        """Test that function returns a set of changed file paths."""
        mock_run_gh.return_value = (True, "file1.ts\nfile2.py\nfile3.md")

        result = ci_monitor.get_pr_changed_files("123")

        assert result == {"file1.ts", "file2.py", "file3.md"}

    @patch("ci_monitor.github_api.run_gh_command")
    def test_returns_none_on_failure(self, mock_run_gh):
        """Test that function returns None when gh command fails."""
        mock_run_gh.return_value = (False, "error message")

        result = ci_monitor.get_pr_changed_files("123")

        assert result is None

    @patch("ci_monitor.github_api.run_gh_command")
    def test_returns_empty_set_for_empty_pr(self, mock_run_gh):
        """Test that function returns empty set for PR with no file changes."""
        mock_run_gh.return_value = (True, "")

        result = ci_monitor.get_pr_changed_files("123")

        assert result == set()

    @patch("ci_monitor.github_api.run_gh_command")
    def test_returns_none_when_100_files_returned(self, mock_run_gh):
        """Test that function returns None when API pagination limit is reached.

        GitHub API returns at most 100 files. When we get exactly 100 files,
        assume there might be more and return None (safe fallback).
        See: https://github.com/silenvx/dekita/issues/324
        """
        # Generate exactly 100 file paths
        files = "\n".join([f"file{i}.ts" for i in range(100)])
        mock_run_gh.return_value = (True, files)

        result = ci_monitor.get_pr_changed_files("123")

        assert result is None

    @patch("ci_monitor.github_api.run_gh_command")
    def test_returns_files_when_under_100(self, mock_run_gh):
        """Test that function returns file set when under pagination limit."""
        # Generate 99 file paths (under the limit)
        files = "\n".join([f"file{i}.ts" for i in range(99)])
        mock_run_gh.return_value = (True, files)

        result = ci_monitor.get_pr_changed_files("123")

        assert len(result) == 99
        assert "file0.ts" in result
        assert "file98.ts" in result


class TestGetPrClosesIssues:
    """Tests for get_pr_closes_issues function."""

    def test_single_closes(self):
        """Test single Closes #123 pattern."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, "Closes #123")
            result = ci_monitor.get_pr_closes_issues("1")
            assert result == ["123"]

    def test_multiple_issues_comma(self):
        """Test comma-separated issues: Closes #123, #456."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, "Closes #123, #456")
            result = ci_monitor.get_pr_closes_issues("1")
            assert sorted(result) == ["123", "456"]

    def test_fix_keyword(self):
        """Test Fix/Fixes/Fixed keywords."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, "Fixes #100\nFixed #200\nFix #300")
            result = ci_monitor.get_pr_closes_issues("1")
            assert sorted(result) == ["100", "200", "300"]

    def test_resolve_keyword(self):
        """Test Resolve/Resolves/Resolved keywords."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, "Resolves #111\nResolved #222\nResolve #333")
            result = ci_monitor.get_pr_closes_issues("1")
            assert sorted(result) == ["111", "222", "333"]

    def test_case_insensitive(self):
        """Test case-insensitive matching."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, "CLOSES #1\ncloses #2\nCloses #3")
            result = ci_monitor.get_pr_closes_issues("1")
            assert sorted(result) == ["1", "2", "3"]

    def test_with_colon(self):
        """Test pattern with colon: Closes: #123."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, "Closes: #123")
            result = ci_monitor.get_pr_closes_issues("1")
            assert result == ["123"]

    def test_empty_body(self):
        """Test PR with empty body."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, "")
            result = ci_monitor.get_pr_closes_issues("1")
            assert result == []

    def test_no_closes_keyword(self):
        """Test PR body without closing keywords."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, "Just a description without closes")
            result = ci_monitor.get_pr_closes_issues("1")
            assert result == []

    def test_gh_command_failure(self):
        """Test when gh command fails."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (False, "")
            result = ci_monitor.get_pr_closes_issues("1")
            assert result == []

    def test_duplicate_issues_deduplicated(self):
        """Test that duplicate issue numbers are deduplicated."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, "Closes #123\nFixes #123")
            result = ci_monitor.get_pr_closes_issues("1")
            assert result == ["123"]


class TestGetIssueIncompleteCriteria:
    """Tests for get_issue_incomplete_criteria function."""

    def test_all_completed(self):
        """Test issue with all checkboxes completed."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (
                True,
                json.dumps({"body": "- [x] Done\n- [X] Also done", "state": "OPEN"}),
            )
            result = ci_monitor.get_issue_incomplete_criteria("1")
            assert result == []

    def test_some_incomplete(self):
        """Test issue with some unchecked checkboxes."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (
                True,
                json.dumps(
                    {"body": "- [x] Done\n- [ ] Not done\n- [ ] Also not done", "state": "OPEN"}
                ),
            )
            result = ci_monitor.get_issue_incomplete_criteria("1")
            assert len(result) == 2
            assert "「Not done」" in result
            assert "「Also not done」" in result

    def test_strikethrough_treated_as_complete(self):
        """Test that strikethrough checkboxes are treated as complete."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (
                True,
                json.dumps({"body": "- [ ] ~~Cancelled item~~\n- [ ] Pending", "state": "OPEN"}),
            )
            result = ci_monitor.get_issue_incomplete_criteria("1")
            assert len(result) == 1
            assert "「Pending」" in result
            assert "Cancelled" not in str(result)

    def test_code_block_checkbox_ignored(self):
        """Test that checkboxes inside code blocks are ignored."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            body = """- [ ] Real task
```
- [ ] In code block
```
- [x] Completed"""
            mock_gh.return_value = (True, json.dumps({"body": body, "state": "OPEN"}))
            result = ci_monitor.get_issue_incomplete_criteria("1")
            assert len(result) == 1
            assert "「Real task」" in result
            assert "code block" not in str(result)

    def test_closed_issue_skipped(self):
        """Test that closed issues return empty list."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (
                True,
                json.dumps({"body": "- [ ] Unchecked but closed", "state": "CLOSED"}),
            )
            result = ci_monitor.get_issue_incomplete_criteria("1")
            assert result == []

    def test_long_criteria_truncated(self):
        """Test that long criteria text is truncated."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            long_text = "A" * 50  # 50 characters
            mock_gh.return_value = (
                True,
                json.dumps({"body": f"- [ ] {long_text}", "state": "OPEN"}),
            )
            result = ci_monitor.get_issue_incomplete_criteria("1")
            assert len(result) == 1
            # truncate処理: 27文字 + "..."(3文字) = 30文字を本文に適用し、「」で囲む
            # 「」(2文字) + 27文字 + "..."(3文字) = 32文字
            assert len(result[0]) == 32
            assert result[0].startswith("「" + "A" * 27)
            assert result[0].endswith("...」")

    def test_empty_body(self):
        """Test issue with empty body."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, json.dumps({"body": "", "state": "OPEN"}))
            result = ci_monitor.get_issue_incomplete_criteria("1")
            assert result == []

    def test_gh_command_failure(self):
        """Test when gh command fails."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (False, "")
            result = ci_monitor.get_issue_incomplete_criteria("1")
            assert result == []

    def test_json_decode_error(self):
        """Test handling of invalid JSON response."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, "not json")
            result = ci_monitor.get_issue_incomplete_criteria("1")
            assert result == []

    def test_asterisk_list_marker(self):
        """Test checkbox with asterisk (*) list marker."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (
                True,
                json.dumps({"body": "* [ ] Asterisk item", "state": "OPEN"}),
            )
            result = ci_monitor.get_issue_incomplete_criteria("1")
            assert len(result) == 1
            assert "「Asterisk item」" in result
