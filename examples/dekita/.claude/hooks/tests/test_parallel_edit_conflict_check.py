#!/usr/bin/env python3
"""Tests for parallel-edit-conflict-check.py hook."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

HOOK_PATH = Path(__file__).parent.parent / "parallel-edit-conflict-check.py"

# Add hooks directory to path
_hooks_dir = str(HOOK_PATH.parent)
if _hooks_dir not in sys.path:
    sys.path.insert(0, _hooks_dir)


def load_module():
    """Load the hook module for testing."""
    spec = importlib.util.spec_from_file_location("parallel_edit_conflict_check", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["parallel_edit_conflict_check"] = module
    spec.loader.exec_module(module)
    return module


class TestGetTargetFile:
    """Tests for get_target_file function."""

    def setup_method(self):
        self.module = load_module()

    def test_returns_file_path(self):
        """Should return file_path from tool input."""
        tool_input = {"file_path": "/path/to/file.py"}

        result = self.module.get_target_file(tool_input)

        assert result == "/path/to/file.py"

    def test_returns_none_if_missing(self):
        """Should return None if file_path is missing."""
        tool_input = {}

        result = self.module.get_target_file(tool_input)

        assert result is None


class TestNormalizePath:
    """Tests for normalize_path function."""

    def setup_method(self):
        self.module = load_module()

    def test_normalize_worktree_absolute_path(self):
        """Should normalize absolute path in worktree to relative."""
        path = "/path/to/repo/.worktrees/issue-123/src/file.py"

        result = self.module.normalize_path(path)

        assert result == "src/file.py"

    def test_normalize_relative_path(self):
        """Should keep relative path as-is."""
        path = "src/file.py"

        result = self.module.normalize_path(path)

        assert result == "src/file.py"

    def test_normalize_dekita_project_path(self):
        """Should normalize dekita project absolute path."""
        path = "/Users/user/dekita/src/file.py"

        result = self.module.normalize_path(path)

        assert result == "src/file.py"


class TestFindConflictingWorktrees:
    """Tests for find_conflicting_worktrees function."""

    def setup_method(self):
        self.module = load_module()

    def test_find_conflict_in_sibling(self):
        """Should find conflict when sibling has same file."""
        from lib.session_graph import WorktreeInfo

        active_sessions = {
            "sibling": [
                WorktreeInfo(
                    path=Path("/path/.worktrees/issue-456"),
                    branch="feat/issue-456",
                    issue_number=456,
                    changed_files={"src/file.py", "other.py"},
                )
            ],
            "ancestor": [],
        }

        conflicts = self.module.find_conflicting_worktrees(
            "/path/.worktrees/issue-123/src/file.py",
            active_sessions,
        )

        assert len(conflicts) == 1
        assert conflicts[0]["issue_number"] == 456

    def test_no_conflict_when_different_files(self):
        """Should not find conflict when files are different."""
        from lib.session_graph import WorktreeInfo

        active_sessions = {
            "sibling": [
                WorktreeInfo(
                    path=Path("/path/.worktrees/issue-456"),
                    branch="feat/issue-456",
                    issue_number=456,
                    changed_files={"different.py"},
                )
            ],
            "ancestor": [],
        }

        conflicts = self.module.find_conflicting_worktrees(
            "/path/.worktrees/issue-123/src/file.py",
            active_sessions,
        )

        assert conflicts == []

    def test_no_conflict_when_no_siblings(self):
        """Should not find conflict when no siblings."""
        active_sessions = {
            "sibling": [],
            "ancestor": [],
        }

        conflicts = self.module.find_conflicting_worktrees(
            "/path/to/file.py",
            active_sessions,
        )

        assert conflicts == []


class TestFormatWarning:
    """Tests for format_warning function."""

    def setup_method(self):
        self.module = load_module()

    def test_format_single_conflict(self):
        """Should format warning for single conflict."""
        conflicts = [
            {
                "issue_number": 456,
                "path": "/path/.worktrees/issue-456",
                "changed_files": ["src/file.py", "other.py"],
                "total_files": 2,
            }
        ]

        warning = self.module.format_warning("src/file.py", conflicts)

        assert "並行編集の競合可能性" in warning
        assert "Issue #456" in warning
        assert "src/file.py" in warning

    def test_format_no_issue_number(self):
        """Should format warning when no issue number."""
        conflicts = [
            {
                "issue_number": None,
                "path": "/path/.worktrees/feature-branch",
                "changed_files": ["file.py"],
                "total_files": 1,
            }
        ]

        warning = self.module.format_warning("file.py", conflicts)

        # Should use worktree name instead
        assert "feature-branch" in warning

    def test_includes_tip(self):
        """Should include tip about avoiding conflicts."""
        conflicts = [
            {
                "issue_number": 456,
                "path": "/path/.worktrees/issue-456",
                "changed_files": ["file.py"],
                "total_files": 1,
            }
        ]

        warning = self.module.format_warning("file.py", conflicts)

        assert "競合を避けるため" in warning
        assert "独立したIssue" in warning


class TestMainFunction:
    """Tests for main function (integration tests)."""

    def setup_method(self):
        self.module = load_module()

    @patch("parallel_edit_conflict_check.parse_hook_input")
    @patch("parallel_edit_conflict_check.is_fork_session")
    @patch("parallel_edit_conflict_check.log_hook_execution")
    def test_skip_non_fork_session(self, mock_log, mock_is_fork, mock_parse):
        """Should skip processing for non-fork sessions."""
        mock_parse.return_value = {
            "session_id": "abc",
            "source": "new",
            "tool_input": {"file_path": "/path/to/file.py"},
        }
        mock_is_fork.return_value = False

        # Capture stdout
        import io
        import json
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            self.module.main()

        output = json.loads(f.getvalue())
        assert output["decision"] == "approve"
        mock_log.assert_called()

    @patch("parallel_edit_conflict_check.parse_hook_input")
    @patch("parallel_edit_conflict_check.is_fork_session")
    @patch("parallel_edit_conflict_check.log_hook_execution")
    def test_skip_no_target_file(self, mock_log, mock_is_fork, mock_parse):
        """Should skip processing when no target file."""
        mock_parse.return_value = {
            "session_id": "abc",
            "source": "resume",
            "tool_input": {},
        }
        mock_is_fork.return_value = True

        import io
        import json
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            self.module.main()

        output = json.loads(f.getvalue())
        assert output["decision"] == "approve"

    @patch("parallel_edit_conflict_check.parse_hook_input")
    @patch("parallel_edit_conflict_check.is_fork_session")
    @patch("parallel_edit_conflict_check.get_active_worktree_sessions")
    @patch("parallel_edit_conflict_check.log_hook_execution")
    def test_warns_on_conflict(self, mock_log, mock_sessions, mock_is_fork, mock_parse):
        """Should output warning when conflict detected."""
        from lib.session_graph import WorktreeInfo

        mock_parse.return_value = {
            "session_id": "abc",
            "source": "resume",
            "transcript_path": "/path/to/transcript",
            "tool_input": {"file_path": "/path/.worktrees/issue-123/src/file.py"},
        }
        mock_is_fork.return_value = True
        mock_sessions.return_value = {
            "sibling": [
                WorktreeInfo(
                    path=Path("/path/.worktrees/issue-456"),
                    branch="feat/issue-456",
                    issue_number=456,
                    changed_files={"src/file.py"},
                )
            ],
            "ancestor": [],
            "unknown": [],
        }

        import io
        import json
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            self.module.main()

        output = json.loads(f.getvalue())
        assert output["decision"] == "approve"  # Warning only, not blocking
        assert "systemMessage" in output
        assert "並行編集" in output["systemMessage"]
