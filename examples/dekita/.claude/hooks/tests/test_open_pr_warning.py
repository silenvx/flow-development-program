#!/usr/bin/env python3
"""Tests for open-pr-warning.py hook."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


class TestGetOpenPrs:
    """Tests for get_open_prs function."""

    def setup_method(self):
        """Set up test fixtures."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "open_pr_warning",
            Path(__file__).parent.parent / "open-pr-warning.py",
        )
        self.module = importlib.util.module_from_spec(spec)

        # Mock common module
        mock_common = MagicMock()
        mock_common.log_hook_execution = MagicMock()

        with patch.dict("sys.modules", {"common": mock_common}):
            spec.loader.exec_module(self.module)

    @patch("subprocess.run")
    def test_returns_prs_on_success(self, mock_run):
        """Should return PRs when gh command succeeds."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"number": 123, "title": "Test PR", "headRefName": "feat/test"}]',
        )

        prs, error = self.module.get_open_prs()

        assert len(prs) == 1
        assert prs[0]["number"] == 123
        assert error is None

    @patch("subprocess.run")
    def test_returns_error_on_failure(self, mock_run):
        """Should return error message when gh command fails."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="auth required",
        )

        prs, error = self.module.get_open_prs()

        assert prs == []
        assert "gh pr list failed" in error

    @patch("subprocess.run")
    def test_returns_error_on_timeout(self, mock_run):
        """Should return error message on timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired("gh", 10)

        prs, error = self.module.get_open_prs()

        assert prs == []
        assert "timed out" in error

    @patch("subprocess.run")
    def test_returns_error_on_json_decode_error(self, mock_run):
        """Should return error message on JSON decode error."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="not valid json",
        )

        prs, error = self.module.get_open_prs()

        assert prs == []
        assert "Failed to parse" in error


class TestExtractIssueNumber:
    """Tests for extract_issue_number function."""

    def setup_method(self):
        """Set up test fixtures."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "open_pr_warning",
            Path(__file__).parent.parent / "open-pr-warning.py",
        )
        self.module = importlib.util.module_from_spec(spec)

        mock_common = MagicMock()
        mock_common.log_hook_execution = MagicMock()

        with patch.dict("sys.modules", {"common": mock_common}):
            spec.loader.exec_module(self.module)

    def test_extracts_from_issue_branch(self):
        """Should extract issue number from issue-123 format."""
        result = self.module.extract_issue_number("issue-123")
        assert result == 123

    def test_extracts_from_feat_branch(self):
        """Should extract issue number from feat/issue-456-xxx format."""
        result = self.module.extract_issue_number("feat/issue-456-feature-name")
        assert result == 456

    def test_extracts_from_fix_branch(self):
        """Should extract issue number from fix/issue-789-xxx format."""
        result = self.module.extract_issue_number("fix/issue-789-bug-fix")
        assert result == 789

    def test_returns_none_for_no_match(self):
        """Should return None when no issue number found."""
        result = self.module.extract_issue_number("feature-branch")
        assert result is None

    def test_case_insensitive(self):
        """Should be case insensitive."""
        result = self.module.extract_issue_number("ISSUE-100")
        assert result == 100


class TestMatchPrToWorktree:
    """Tests for match_pr_to_worktree function."""

    def setup_method(self):
        """Set up test fixtures."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "open_pr_warning",
            Path(__file__).parent.parent / "open-pr-warning.py",
        )
        self.module = importlib.util.module_from_spec(spec)

        mock_common = MagicMock()
        mock_common.log_hook_execution = MagicMock()

        with patch.dict("sys.modules", {"common": mock_common}):
            spec.loader.exec_module(self.module)

    def test_matches_by_branch_name(self):
        """Should match PR to worktree by branch name."""
        prs = [{"number": 1, "headRefName": "feat/issue-123-test"}]
        worktrees = [{"path": "/path/to/wt", "branch": "refs/heads/feat/issue-123-test"}]

        result = self.module.match_pr_to_worktree(prs, worktrees)

        assert len(result) == 1
        assert result[0]["worktree"] is not None
        assert result[0]["worktree"]["path"] == "/path/to/wt"

    def test_matches_by_issue_number_in_path(self):
        """Should match PR to worktree by issue number in path."""
        prs = [{"number": 1, "headRefName": "feat/issue-456-test"}]
        worktrees = [{"path": "/worktrees/issue-456", "branch": "refs/heads/main"}]

        result = self.module.match_pr_to_worktree(prs, worktrees)

        assert len(result) == 1
        assert result[0]["worktree"] is not None

    def test_no_match(self):
        """Should return None for worktree when no match found."""
        prs = [{"number": 1, "headRefName": "feat/unrelated"}]
        worktrees = [{"path": "/path/to/wt", "branch": "refs/heads/main"}]

        result = self.module.match_pr_to_worktree(prs, worktrees)

        assert len(result) == 1
        assert result[0]["worktree"] is None


class TestGetUnmatchedLockedWorktrees:
    """Tests for get_unmatched_locked_worktrees function (Issue #1095)."""

    def setup_method(self):
        """Set up test fixtures."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "open_pr_warning",
            Path(__file__).parent.parent / "open-pr-warning.py",
        )
        self.module = importlib.util.module_from_spec(spec)

        mock_common = MagicMock()
        mock_common.log_hook_execution = MagicMock()

        with patch.dict("sys.modules", {"common": mock_common}):
            spec.loader.exec_module(self.module)

    def test_returns_locked_worktrees_without_pr(self):
        """Should return locked worktrees that are not matched to any PR."""
        worktrees = [
            {
                "path": "/repo/.worktrees/issue-123",
                "branch": "refs/heads/feat/test",
                "locked": "true",
            },
            {
                "path": "/repo/.worktrees/issue-456",
                "branch": "refs/heads/feat/other",
                "locked": "true",
            },
        ]
        pr_worktree_map = [
            {"number": 1, "worktree": {"path": "/repo/.worktrees/issue-123"}},
        ]

        result = self.module.get_unmatched_locked_worktrees(worktrees, pr_worktree_map)

        assert len(result) == 1
        assert result[0]["path"] == "/repo/.worktrees/issue-456"

    def test_excludes_unlocked_worktrees(self):
        """Should not include unlocked worktrees."""
        worktrees = [
            {"path": "/repo/.worktrees/issue-123", "branch": "refs/heads/feat/test"},
        ]
        pr_worktree_map = []

        result = self.module.get_unmatched_locked_worktrees(worktrees, pr_worktree_map)

        assert len(result) == 0

    def test_excludes_main_repo(self):
        """Should not include main repo (no /.worktrees/ in path)."""
        worktrees = [
            {"path": "/repo", "branch": "refs/heads/main", "locked": "true"},
        ]
        pr_worktree_map = []

        result = self.module.get_unmatched_locked_worktrees(worktrees, pr_worktree_map)

        assert len(result) == 0

    def test_excludes_worktrees_backup_dir(self):
        """Should not include paths with .worktrees_backup (edge case)."""
        worktrees = [
            {
                "path": "/repo/.worktrees_backup/issue-123",
                "branch": "refs/heads/feat/test",
                "locked": "true",
            },
        ]
        pr_worktree_map = []

        result = self.module.get_unmatched_locked_worktrees(worktrees, pr_worktree_map)

        # Should NOT include because /.worktrees/ is not in the path
        assert len(result) == 0

    def test_returns_empty_when_all_matched(self):
        """Should return empty list when all locked worktrees are matched."""
        worktrees = [
            {
                "path": "/repo/.worktrees/issue-123",
                "branch": "refs/heads/feat/test",
                "locked": "true",
            },
        ]
        pr_worktree_map = [
            {"number": 1, "worktree": {"path": "/repo/.worktrees/issue-123"}},
        ]

        result = self.module.get_unmatched_locked_worktrees(worktrees, pr_worktree_map)

        assert len(result) == 0


class TestFormatWarningMessage:
    """Tests for format_warning_message function."""

    def setup_method(self):
        """Set up test fixtures."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "open_pr_warning",
            Path(__file__).parent.parent / "open-pr-warning.py",
        )
        self.module = importlib.util.module_from_spec(spec)

        mock_common = MagicMock()
        mock_common.log_hook_execution = MagicMock()

        with patch.dict("sys.modules", {"common": mock_common}):
            spec.loader.exec_module(self.module)

    def test_returns_empty_for_no_prs_and_no_locked(self):
        """Should return empty string when no PRs and no locked worktrees."""
        result = self.module.format_warning_message([], [])
        assert result == ""

    def test_includes_warning_header(self):
        """Should include warning header."""
        pr_map = [{"number": 1, "title": "Test", "branch": "test", "author": "user"}]

        result = self.module.format_warning_message(pr_map)

        assert "オープンPRが存在します" in result
        assert "介入禁止" in result

    def test_includes_pr_details(self):
        """Should include PR number, title, branch, author."""
        pr_map = [
            {
                "number": 123,
                "title": "Test PR",
                "branch": "feat/test",
                "author": "testuser",
            }
        ]

        result = self.module.format_warning_message(pr_map)

        assert "PR #123" in result
        assert "Test PR" in result
        assert "feat/test" in result
        assert "testuser" in result

    def test_includes_worktree_info(self):
        """Should include worktree path when matched."""
        pr_map = [
            {
                "number": 1,
                "title": "Test",
                "branch": "test",
                "author": "user",
                "worktree": {"path": "/path/to/wt", "locked": "true"},
            }
        ]

        result = self.module.format_warning_message(pr_map)

        assert "/path/to/wt" in result
        assert "🔒" in result

    def test_includes_unmatched_locked_worktrees(self):
        """Should include unmatched locked worktrees section (Issue #1095)."""
        pr_map = []
        unmatched_locked = [
            {"path": "/repo/.worktrees/issue-456", "branch": "refs/heads/feat/other"},
        ]

        result = self.module.format_warning_message(pr_map, unmatched_locked)

        assert "ロックされたworktree" in result
        assert "PRなし" in result
        assert "/repo/.worktrees/issue-456" in result

    def test_strips_refs_heads_from_branch_name(self):
        """Should strip refs/heads/ prefix from branch name in locked worktrees."""
        pr_map = []
        unmatched_locked = [
            {"path": "/repo/.worktrees/issue-456", "branch": "refs/heads/feat/other"},
        ]

        result = self.module.format_warning_message(pr_map, unmatched_locked)

        # refs/heads/ should be stripped, showing only feat/other
        assert "feat/other" in result
        assert "refs/heads/feat/other" not in result

    def test_includes_both_prs_and_locked_worktrees(self):
        """Should include both PRs and locked worktrees when both exist."""
        pr_map = [{"number": 1, "title": "Test", "branch": "test", "author": "user"}]
        unmatched_locked = [
            {"path": "/repo/.worktrees/issue-456", "branch": "refs/heads/feat/other"},
        ]

        result = self.module.format_warning_message(pr_map, unmatched_locked)

        assert "オープンPRが存在します" in result
        assert "ロックされたworktree" in result
        assert "PR #1" in result
        assert "/repo/.worktrees/issue-456" in result


class TestMainFunction:
    """Tests for main function."""

    def setup_method(self):
        """Set up test fixtures."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "open_pr_warning",
            Path(__file__).parent.parent / "open-pr-warning.py",
        )
        self.module = importlib.util.module_from_spec(spec)

        self.mock_common = MagicMock()
        self.mock_common.log_hook_execution = MagicMock()

        with patch.dict("sys.modules", {"common": self.mock_common}):
            spec.loader.exec_module(self.module)

    @patch("subprocess.run")
    def test_outputs_warning_on_error(self, mock_run):
        """Should output warning message when PR fetch fails."""
        mock_run.side_effect = OSError("command not found")

        from io import StringIO

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.module.main()
            output = json.loads(mock_stdout.getvalue())

        assert output["continue"]
        assert "オープンPRの確認に失敗しました" in output.get("message", "")

    @patch("subprocess.run")
    def test_outputs_pr_warning(self, mock_run):
        """Should output PR warning when PRs exist."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"number": 1, "title": "Test", "headRefName": "test", "author": {"login": "user"}}]',
        )

        from io import StringIO

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.module.main()
            output = json.loads(mock_stdout.getvalue())

        assert output["continue"]
        assert "message" in output
        assert "介入禁止" in output["message"]

    @patch("subprocess.run")
    def test_no_message_when_no_prs(self, mock_run):
        """Should not include message when no PRs exist."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="[]",
        )

        from io import StringIO

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.module.main()
            output = json.loads(mock_stdout.getvalue())

        assert output["continue"]
        assert "message" not in output
