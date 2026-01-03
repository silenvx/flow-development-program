#!/usr/bin/env python3
"""
Unit tests for worktree-commit-integrity-check.py

Tests cover:
- is_in_worktree function
- get_commits_since_main function
- extract_issue_numbers function
- check_merged_commits function
- main hook logic
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest import load_hook_module

# Import the hook using conftest helper
hook_module = load_hook_module("worktree-commit-integrity-check")

# Import symbols
is_in_worktree = hook_module.is_in_worktree
get_commits_since_main = hook_module.get_commits_since_main
extract_issue_numbers = hook_module.extract_issue_numbers
check_merged_commits = hook_module.check_merged_commits
get_git_status = hook_module.get_git_status


class TestIsInWorktree:
    """Tests for is_in_worktree function."""

    @patch("worktree_commit_integrity_check.Path.cwd")
    def test_in_worktree(self, mock_cwd):
        """Test when CWD is inside a worktree."""
        mock_cwd.return_value = Path("/path/to/repo/.worktrees/issue-123/src")
        result, name = is_in_worktree()
        assert result is True
        assert name == "issue-123"

    @patch("worktree_commit_integrity_check.Path.cwd")
    def test_not_in_worktree(self, mock_cwd):
        """Test when CWD is not inside a worktree."""
        mock_cwd.return_value = Path("/path/to/repo/src")
        result, name = is_in_worktree()
        assert result is False
        assert name is None

    @patch("worktree_commit_integrity_check.Path.cwd")
    def test_in_worktree_root(self, mock_cwd):
        """Test when CWD is at worktree root."""
        mock_cwd.return_value = Path("/path/to/repo/.worktrees/issue-456")
        result, name = is_in_worktree()
        assert result is True
        assert name == "issue-456"

    @patch("worktree_commit_integrity_check.Path.cwd")
    def test_cwd_error(self, mock_cwd):
        """Test when CWD access fails."""
        mock_cwd.side_effect = OSError("Directory not found")
        result, name = is_in_worktree()
        assert result is False
        assert name is None


class TestGetCommitsSinceMain:
    """Tests for get_commits_since_main function."""

    @patch("worktree_commit_integrity_check.subprocess.run")
    def test_no_commits(self, mock_run):
        """Test when there are no commits since main."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        commits, error = get_commits_since_main()
        assert commits == []
        assert error is None

    @patch("worktree_commit_integrity_check.subprocess.run")
    def test_with_commits(self, mock_run):
        """Test with commits since main."""
        # First call for git log --oneline
        # Second call for commit body
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="abc1234 feat: add feature (#123)\ndef5678 fix: bug fix (#456)\n",
            ),
            MagicMock(returncode=0, stdout="Closes #123\n"),
            MagicMock(returncode=0, stdout="Fixes #456\n"),
        ]
        commits, error = get_commits_since_main()
        assert len(commits) == 2
        assert commits[0]["hash"] == "abc1234"
        assert commits[0]["subject"] == "feat: add feature (#123)"
        assert commits[1]["hash"] == "def5678"
        assert error is None

    @patch("worktree_commit_integrity_check.subprocess.run")
    def test_git_failure(self, mock_run):
        """Test when git command fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error: main not found")
        commits, error = get_commits_since_main()
        assert commits == []
        assert error is not None
        assert "main not found" in error

    @patch("worktree_commit_integrity_check.subprocess.run")
    def test_timeout(self, mock_run):
        """Test when git command times out."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired("git", 5)
        commits, error = get_commits_since_main()
        assert commits == []
        assert error is not None
        assert "timed out" in error


class TestExtractIssueNumbers:
    """Tests for extract_issue_numbers function."""

    def test_single_issue(self):
        """Test extracting a single issue number."""
        commits = [{"hash": "abc123", "subject": "fix: issue #123", "body": ""}]
        result = extract_issue_numbers(commits)
        assert 123 in result
        assert "abc123" in result[123]

    def test_multiple_issues_same_commit(self):
        """Test extracting multiple issues from the same commit."""
        commits = [
            {
                "hash": "abc123",
                "subject": "fix: issues #123 and #456",
                "body": "Also related to #789",
            }
        ]
        result = extract_issue_numbers(commits)
        assert 123 in result
        assert 456 in result
        assert 789 in result
        assert "abc123" in result[123]
        assert "abc123" in result[456]
        assert "abc123" in result[789]

    def test_multiple_commits_same_issue(self):
        """Test multiple commits referencing the same issue."""
        commits = [
            {"hash": "abc123", "subject": "feat: start #100", "body": ""},
            {"hash": "def456", "subject": "fix: continue #100", "body": ""},
        ]
        result = extract_issue_numbers(commits)
        assert 100 in result
        assert len(result[100]) == 2
        assert "abc123" in result[100]
        assert "def456" in result[100]

    def test_no_issues(self):
        """Test when no issues are referenced."""
        commits = [{"hash": "abc123", "subject": "chore: cleanup", "body": "General cleanup"}]
        result = extract_issue_numbers(commits)
        assert result == {}

    def test_issue_in_body_only(self):
        """Test extracting issue from commit body."""
        commits = [{"hash": "abc123", "subject": "feat: new feature", "body": "Closes #999"}]
        result = extract_issue_numbers(commits)
        assert 999 in result


class TestCheckMergedCommits:
    """Tests for check_merged_commits function."""

    @patch("worktree_commit_integrity_check.subprocess.run")
    def test_no_merged_commits(self, mock_run):
        """Test when no commits are merged to main."""
        mock_run.return_value = MagicMock(returncode=0, stdout="  origin/feat/issue-123\n")
        commits = [{"hash": "abc123", "subject": "feat", "body": ""}]
        result = check_merged_commits(commits)
        assert result == []

    @patch("worktree_commit_integrity_check.subprocess.run")
    def test_merged_commit(self, mock_run):
        """Test when a commit is already merged to main."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="  origin/main\n  origin/feat/issue-123\n"
        )
        commits = [{"hash": "abc123", "subject": "feat", "body": ""}]
        result = check_merged_commits(commits)
        assert len(result) == 1
        assert result[0]["hash"] == "abc123"

    @patch("worktree_commit_integrity_check.subprocess.run")
    def test_git_failure(self, mock_run):
        """Test when git command fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        commits = [{"hash": "abc123", "subject": "feat", "body": ""}]
        result = check_merged_commits(commits)
        assert result == []


class TestGetGitStatus:
    """Tests for get_git_status function."""

    @patch("worktree_commit_integrity_check.subprocess.run")
    def test_clean_status(self, mock_run):
        """Test when there are no changes."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = get_git_status()
        assert result == ""

    @patch("worktree_commit_integrity_check.subprocess.run")
    def test_with_changes(self, mock_run):
        """Test when there are uncommitted changes."""
        mock_run.return_value = MagicMock(returncode=0, stdout=" M file1.py\n?? file2.py")
        result = get_git_status()
        assert "file1.py" in result
        assert "file2.py" in result


class TestMain:
    """Tests for main hook logic."""

    @patch("worktree_commit_integrity_check.log_hook_execution")
    @patch("worktree_commit_integrity_check.parse_hook_input")
    @patch("worktree_commit_integrity_check.is_in_worktree")
    def test_not_in_worktree_skip(self, mock_is_in, mock_parse, mock_log, capsys):
        """Test that hook skips when not in worktree."""
        mock_is_in.return_value = (False, None)
        hook_module.main()
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        mock_log.assert_called_once()
        assert "skip" in str(mock_log.call_args)

    @patch("worktree_commit_integrity_check.log_hook_execution")
    @patch("worktree_commit_integrity_check.log_worktree_state")
    @patch("worktree_commit_integrity_check.get_git_status")
    @patch("worktree_commit_integrity_check.check_merged_commits")
    @patch("worktree_commit_integrity_check.extract_issue_numbers")
    @patch("worktree_commit_integrity_check.get_commits_since_main")
    @patch("worktree_commit_integrity_check.is_in_worktree")
    @patch("worktree_commit_integrity_check.parse_hook_input")
    def test_multiple_issues_warning(
        self,
        mock_parse,
        mock_is_in,
        mock_commits,
        mock_extract,
        mock_merged,
        mock_status,
        mock_log_state,
        mock_log,
        capsys,
    ):
        """Test warning when multiple issues detected."""
        mock_is_in.return_value = (True, "issue-123")
        mock_commits.return_value = (
            [
                {"hash": "abc123", "subject": "feat #123", "body": ""},
                {"hash": "def456", "subject": "fix #456", "body": ""},
            ],
            None,
        )
        mock_extract.return_value = {123: ["abc123"], 456: ["def456"]}
        mock_merged.return_value = []
        mock_status.return_value = ""

        hook_module.main()
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["continue"] is True
        assert "systemMessage" in output
        assert "#123" in output["systemMessage"]
        assert "#456" in output["systemMessage"]
        assert "複数のIssue" in output["systemMessage"]

    @patch("worktree_commit_integrity_check.log_hook_execution")
    @patch("worktree_commit_integrity_check.log_worktree_state")
    @patch("worktree_commit_integrity_check.get_git_status")
    @patch("worktree_commit_integrity_check.check_merged_commits")
    @patch("worktree_commit_integrity_check.extract_issue_numbers")
    @patch("worktree_commit_integrity_check.get_commits_since_main")
    @patch("worktree_commit_integrity_check.is_in_worktree")
    @patch("worktree_commit_integrity_check.parse_hook_input")
    def test_merged_commits_warning(
        self,
        mock_parse,
        mock_is_in,
        mock_commits,
        mock_extract,
        mock_merged,
        mock_status,
        mock_log_state,
        mock_log,
        capsys,
    ):
        """Test warning when merged commits detected."""
        mock_is_in.return_value = (True, "issue-123")
        mock_commits.return_value = (
            [{"hash": "abc123", "subject": "feat #123", "body": ""}],
            None,
        )
        mock_extract.return_value = {123: ["abc123"]}
        mock_merged.return_value = [{"hash": "abc123", "subject": "feat #123", "body": ""}]
        mock_status.return_value = ""

        hook_module.main()
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["continue"] is True
        assert "systemMessage" in output
        assert "マージ済み" in output["systemMessage"]

    @patch("worktree_commit_integrity_check.log_hook_execution")
    @patch("worktree_commit_integrity_check.log_worktree_state")
    @patch("worktree_commit_integrity_check.get_git_status")
    @patch("worktree_commit_integrity_check.check_merged_commits")
    @patch("worktree_commit_integrity_check.extract_issue_numbers")
    @patch("worktree_commit_integrity_check.get_commits_since_main")
    @patch("worktree_commit_integrity_check.is_in_worktree")
    @patch("worktree_commit_integrity_check.parse_hook_input")
    def test_no_warnings(
        self,
        mock_parse,
        mock_is_in,
        mock_commits,
        mock_extract,
        mock_merged,
        mock_status,
        mock_log_state,
        mock_log,
        capsys,
    ):
        """Test when no warnings needed."""
        mock_is_in.return_value = (True, "issue-123")
        mock_commits.return_value = (
            [{"hash": "abc123", "subject": "feat #123", "body": ""}],
            None,
        )
        mock_extract.return_value = {123: ["abc123"]}
        mock_merged.return_value = []
        mock_status.return_value = ""

        hook_module.main()
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["continue"] is True
        assert "systemMessage" not in output

    @patch("worktree_commit_integrity_check.log_hook_execution")
    @patch("worktree_commit_integrity_check.get_commits_since_main")
    @patch("worktree_commit_integrity_check.is_in_worktree")
    @patch("worktree_commit_integrity_check.parse_hook_input")
    def test_git_error_warning(
        self,
        mock_parse,
        mock_is_in,
        mock_commits,
        mock_log,
        capsys,
    ):
        """Test warning when git command fails."""
        mock_is_in.return_value = (True, "issue-123")
        mock_commits.return_value = ([], "error: main not found")

        hook_module.main()
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["continue"] is True
        assert "systemMessage" in output
        assert "gitエラー" in output["systemMessage"]
        assert "main not found" in output["systemMessage"]
