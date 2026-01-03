#!/usr/bin/env python3
"""Tests for lib/session_graph.py module."""

import sys
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from lib.session_graph import (
    WorktreeInfo,
    extract_issue_number_from_branch,
    get_worktree_changed_files,
    get_worktree_list,
    get_worktree_session_id,
)


class TestExtractIssueNumberFromBranch:
    """Tests for extract_issue_number_from_branch function."""

    def test_extract_simple_issue_branch(self):
        """Should extract issue number from simple branch name."""
        assert extract_issue_number_from_branch("issue-123") == 123

    def test_extract_feat_prefix(self):
        """Should extract issue number with feat/ prefix."""
        assert extract_issue_number_from_branch("feat/issue-456-description") == 456

    def test_extract_fix_prefix(self):
        """Should extract issue number with fix/ prefix."""
        assert extract_issue_number_from_branch("fix/issue-789") == 789

    def test_case_insensitive(self):
        """Should match case-insensitively."""
        assert extract_issue_number_from_branch("Issue-100") == 100
        assert extract_issue_number_from_branch("ISSUE-200") == 200

    def test_no_issue_number(self):
        """Should return None for branches without issue number."""
        assert extract_issue_number_from_branch("main") is None
        assert extract_issue_number_from_branch("feat/new-feature") is None
        assert extract_issue_number_from_branch("fix/bug") is None

    def test_partial_match(self):
        """Should not match partial issue patterns."""
        assert extract_issue_number_from_branch("tissue-123") is None


class TestGetWorktreeList:
    """Tests for get_worktree_list function."""

    @patch("subprocess.run")
    def test_parse_porcelain_output(self, mock_run):
        """Should parse git worktree list --porcelain output."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = """worktree /path/to/main
branch refs/heads/main

worktree /path/to/.worktrees/issue-123
branch refs/heads/feat/issue-123-test
locked
"""

        result = get_worktree_list()

        assert len(result) == 2
        assert result[0] == (Path("/path/to/main"), "main", False)
        assert result[1] == (Path("/path/to/.worktrees/issue-123"), "feat/issue-123-test", True)

    @patch("subprocess.run")
    def test_returns_empty_on_error(self, mock_run):
        """Should return empty list on git error."""
        mock_run.return_value.returncode = 1

        result = get_worktree_list()

        assert result == []

    @patch("subprocess.run")
    def test_returns_empty_on_timeout(self, mock_run):
        """Should return empty list on timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=5)

        result = get_worktree_list()

        assert result == []


class TestGetWorktreeSessionId:
    """Tests for get_worktree_session_id function."""

    def test_returns_session_id_from_marker(self, tmp_path):
        """Should return session ID from .claude-session marker."""
        marker = tmp_path / ".claude-session"
        marker.write_text('{"session_id": "abc-123"}')

        result = get_worktree_session_id(tmp_path)

        assert result == "abc-123"

    def test_returns_none_if_no_marker(self, tmp_path):
        """Should return None if no marker file exists."""
        result = get_worktree_session_id(tmp_path)

        assert result is None

    def test_returns_none_on_invalid_json(self, tmp_path):
        """Should return None on invalid JSON in marker."""
        marker = tmp_path / ".claude-session"
        marker.write_text("not json")

        result = get_worktree_session_id(tmp_path)

        assert result is None

    def test_returns_none_if_missing_session_id(self, tmp_path):
        """Should return None if session_id key is missing."""
        marker = tmp_path / ".claude-session"
        marker.write_text('{"other_key": "value"}')

        result = get_worktree_session_id(tmp_path)

        assert result is None


class TestGetWorktreeChangedFiles:
    """Tests for get_worktree_changed_files function."""

    def setup_method(self):
        """Clear cache before each test."""
        get_worktree_changed_files.cache_clear()

    @patch("subprocess.run")
    def test_returns_changed_files(self, mock_run):
        """Should return frozenset of changed files."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "file1.py\nfile2.py\nfile3.py"

        result = get_worktree_changed_files(Path("/path/to/worktree"))

        assert result == frozenset({"file1.py", "file2.py", "file3.py"})

    @patch("subprocess.run")
    def test_returns_empty_on_error(self, mock_run):
        """Should return empty frozenset on git error."""
        mock_run.return_value.returncode = 1

        result = get_worktree_changed_files(Path("/path/to/worktree"))

        assert result == frozenset()

    @patch("subprocess.run")
    def test_handles_empty_output(self, mock_run):
        """Should handle empty output gracefully."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""

        result = get_worktree_changed_files(Path("/path/to/worktree"))

        assert result == frozenset()

    @patch("subprocess.run")
    def test_results_are_cached(self, mock_run):
        """Should cache results for same worktree path."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "file.py"

        path = Path("/path/to/worktree")
        result1 = get_worktree_changed_files(path)
        result2 = get_worktree_changed_files(path)

        # Should only call subprocess once due to caching
        assert mock_run.call_count == 1
        assert result1 == result2


class TestWorktreeInfo:
    """Tests for WorktreeInfo dataclass."""

    def test_create_with_defaults(self):
        """Should create WorktreeInfo with default values."""
        info = WorktreeInfo(path=Path("/path"), branch="main")

        assert info.path == Path("/path")
        assert info.branch == "main"
        assert info.session_id is None
        assert info.issue_number is None
        assert info.changed_files == set()

    def test_create_with_all_fields(self):
        """Should create WorktreeInfo with all fields."""
        info = WorktreeInfo(
            path=Path("/path"),
            branch="feat/issue-123",
            session_id="abc-123",
            issue_number=123,
            changed_files={"file1.py", "file2.py"},
        )

        assert info.path == Path("/path")
        assert info.branch == "feat/issue-123"
        assert info.session_id == "abc-123"
        assert info.issue_number == 123
        assert info.changed_files == {"file1.py", "file2.py"}
