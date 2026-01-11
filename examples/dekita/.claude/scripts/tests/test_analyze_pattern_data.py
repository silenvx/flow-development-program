#!/usr/bin/env python3
"""Tests for analyze-pattern-data.py script."""

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPT_PATH = Path(__file__).parent.parent / "analyze_pattern_data.py"


@pytest.fixture
def module():
    """Load the analyze-pattern-data module."""
    spec = importlib.util.spec_from_file_location("analyze_pattern_data", str(SCRIPT_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPatternMatch:
    """Tests for PatternMatch dataclass."""

    def test_pattern_match_creation(self, module):
        """Should create PatternMatch with all fields."""
        match = module.PatternMatch(
            source="pr_comment",
            source_id="PR #123",
            matched_text="後で",
            context="これは後での対応です",
            url="https://github.com/test/repo/pull/123",
        )
        assert match.source == "pr_comment"
        assert match.source_id == "PR #123"
        assert match.matched_text == "後で"
        assert match.context == "これは後での対応です"
        assert match.url == "https://github.com/test/repo/pull/123"

    def test_pattern_match_default_url(self, module):
        """Should allow None URL."""
        match = module.PatternMatch(
            source="session_log",
            source_id="session-123",
            matched_text="将来",
            context="将来対応予定",
        )
        assert match.url is None


class TestRunGhCommand:
    """Tests for run_gh_command function."""

    def test_run_gh_command_success(self, module):
        """Should return stdout on success."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"test": "data"}',
                stderr="",
            )
            result = module.run_gh_command(["test", "command"])
            assert result == '{"test": "data"}'
            mock_run.assert_called_once()

    def test_run_gh_command_failure(self, module):
        """Should return None on command failure."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="Error message",
            )
            result = module.run_gh_command(["test", "command"])
            assert result is None

    def test_run_gh_command_timeout(self, module):
        """Should return None on timeout."""
        import subprocess

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("gh", 60)
            result = module.run_gh_command(["test", "command"])
            assert result is None

    def test_run_gh_command_not_found(self, module):
        """Should return None when gh is not found."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            result = module.run_gh_command(["test", "command"])
            assert result is None


class TestGetRepoBaseUrl:
    """Tests for get_repo_base_url function."""

    def test_get_repo_base_url_success(self, module):
        """Should return repo URL from gh command."""
        # Reset cached value
        module._REPO_BASE_URL = None

        with patch.object(
            module, "run_gh_command", return_value='{"url": "https://github.com/test/repo"}'
        ):
            result = module.get_repo_base_url()
            assert result == "https://github.com/test/repo"

    def test_get_repo_base_url_cached(self, module):
        """Should return cached URL without calling gh."""
        module._REPO_BASE_URL = "https://github.com/cached/repo"
        result = module.get_repo_base_url()
        assert result == "https://github.com/cached/repo"
        # Reset for other tests
        module._REPO_BASE_URL = None

    def test_get_repo_base_url_fallback(self, module):
        """Should return fallback URL on failure."""
        module._REPO_BASE_URL = None

        with patch.object(module, "run_gh_command", return_value=None):
            result = module.get_repo_base_url()
            assert result == "https://github.com/unknown/unknown"

        # Reset for other tests
        module._REPO_BASE_URL = None


class TestSearchPrComments:
    """Tests for search_pr_comments function."""

    def test_search_pr_comments_empty_prs(self, module):
        """Should return empty list when no PRs found."""
        module._REPO_BASE_URL = "https://github.com/test/repo"

        with patch.object(module, "run_gh_command", return_value=None):
            result = module.search_pr_comments("test", days=7, limit=10)
            assert result == []

        module._REPO_BASE_URL = None

    def test_search_pr_comments_no_comments(self, module):
        """Should handle PRs without comments."""
        module._REPO_BASE_URL = "https://github.com/test/repo"

        pr_list = json.dumps([{"number": 1, "title": "Test PR", "createdAt": "2025-01-01"}])
        comments = json.dumps([])

        def mock_gh_command(args):
            if "pr" in args and "list" in args:
                return pr_list
            elif "api" in args and "comments" in str(args):
                return comments
            return None

        with patch.object(module, "run_gh_command", side_effect=mock_gh_command):
            result = module.search_pr_comments("test", days=7, limit=10)
            assert result == []

        module._REPO_BASE_URL = None

    def test_search_pr_comments_with_match(self, module):
        """Should find pattern in PR comments."""
        module._REPO_BASE_URL = "https://github.com/test/repo"

        pr_list = json.dumps([{"number": 1, "title": "Test PR", "createdAt": "2025-01-01"}])
        comments = json.dumps(["This is a test comment with 後で keyword"])

        def mock_gh_command(args):
            if "pr" in args and "list" in args:
                return pr_list
            elif "api" in args:
                return comments
            return None

        with patch.object(module, "run_gh_command", side_effect=mock_gh_command):
            result = module.search_pr_comments("後で", days=7, limit=10)
            assert len(result) == 1
            assert result[0].source == "pr_comment"
            assert result[0].source_id == "PR #1"
            assert result[0].matched_text == "後で"
            assert "https://github.com/test/repo/pull/1" == result[0].url

        module._REPO_BASE_URL = None


class TestSearchIssueComments:
    """Tests for search_issue_comments function."""

    def test_search_issue_comments_empty_issues(self, module):
        """Should return empty list when no issues found."""
        module._REPO_BASE_URL = "https://github.com/test/repo"

        with patch.object(module, "run_gh_command", return_value=None):
            result = module.search_issue_comments("test", days=7, limit=10)
            assert result == []

        module._REPO_BASE_URL = None

    def test_search_issue_comments_body_match(self, module):
        """Should find pattern in issue body."""
        module._REPO_BASE_URL = "https://github.com/test/repo"

        issue_list = json.dumps(
            [{"number": 1, "title": "Test Issue", "body": "This contains 後で keyword"}]
        )

        def mock_gh_command(args):
            if "issue" in args and "list" in args:
                return issue_list
            elif "api" in args:
                return json.dumps([])
            return None

        with patch.object(module, "run_gh_command", side_effect=mock_gh_command):
            result = module.search_issue_comments("後で", days=7, limit=10)
            assert len(result) == 1
            assert result[0].source == "issue_body"
            assert result[0].source_id == "Issue #1"
            assert result[0].matched_text == "後で"

        module._REPO_BASE_URL = None


class TestSearchSessionLogs:
    """Tests for search_session_logs function."""

    def test_search_session_logs_no_logs_dir(self, module, tmp_path):
        """Should return empty list when logs dir doesn't exist."""
        with patch.object(os.path, "expanduser", return_value=str(tmp_path / "nonexistent")):
            result = module.search_session_logs("test", days=7)
            assert result == []

    def test_search_session_logs_with_match(self, module, tmp_path):
        """Should find pattern in session logs."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        # Create a test log file
        log_file = logs_dir / "test.jsonl"
        log_entry = {"message": {"content": "This has 後で keyword"}}
        log_file.write_text(json.dumps(log_entry) + "\n")

        with patch.object(os.path, "expanduser", return_value=str(logs_dir)):
            result = module.search_session_logs("後で", days=7)
            assert len(result) == 1
            assert result[0].source == "session_log"
            assert result[0].matched_text == "後で"


class TestCommandLineInterface:
    """Tests for command line interface."""

    def test_main_search_command_help(self, module):
        """Should show help for search command."""
        with patch.object(sys, "argv", ["script", "search", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                module.main()
            assert exc_info.value.code == 0

    def test_main_analyze_command_help(self, module):
        """Should show help for analyze command."""
        with patch.object(sys, "argv", ["script", "analyze", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                module.main()
            assert exc_info.value.code == 0

    def test_main_validate_command_help(self, module):
        """Should show help for validate command."""
        with patch.object(sys, "argv", ["script", "validate", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                module.main()
            assert exc_info.value.code == 0
