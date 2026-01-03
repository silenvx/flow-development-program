#!/usr/bin/env python3
"""Tests for fork-session-collaboration-advisor.py hook."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

# Import after path setup
from importlib import import_module

# Import the module (has hyphens in name)
advisor = import_module("fork-session-collaboration-advisor")

# Create module reference for patching
ADVISOR_MODULE = "fork-session-collaboration-advisor"


class TestFormatWorktreeInfo:
    """Tests for format_worktree_info function."""

    def test_format_with_issue_number(self):
        """Should format worktree with issue number."""
        info = MagicMock()
        info.issue_number = 123
        info.path = Path("/path/to/issue-123")
        info.changed_files = set()

        result = advisor.format_worktree_info(info)

        assert "Issue #123" in result

    def test_format_without_issue_number(self):
        """Should format worktree with path name when no issue number."""
        info = MagicMock()
        info.issue_number = None
        info.path = Path("/path/to/feature-branch")
        info.changed_files = set()

        result = advisor.format_worktree_info(info)

        assert "feature-branch" in result

    def test_format_with_files(self):
        """Should show changed files."""
        info = MagicMock()
        info.issue_number = 123
        info.path = Path("/path/to/issue-123")
        info.changed_files = {"file1.py", "file2.py"}

        result = advisor.format_worktree_info(info)

        assert "Files:" in result
        assert "file1.py" in result or "file2.py" in result

    def test_format_truncates_many_files(self):
        """Should truncate file list when more than 3 files."""
        info = MagicMock()
        info.issue_number = 123
        info.path = Path("/path/to/issue-123")
        info.changed_files = {"a.py", "b.py", "c.py", "d.py", "e.py"}

        result = advisor.format_worktree_info(info)

        assert "(+2 more)" in result


class TestFormatIssueSuggestion:
    """Tests for format_issue_suggestion function."""

    def test_format_basic_issue(self):
        """Should format issue with number and title."""
        issue = {"number": 100, "title": "Test Issue", "labels": []}

        result = advisor.format_issue_suggestion(issue, 1)

        assert "1." in result
        assert "#100" in result
        assert "Test Issue" in result

    def test_format_with_priority_label(self):
        """Should include priority label."""
        issue = {
            "number": 100,
            "title": "Test Issue",
            "labels": [{"name": "P1"}, {"name": "bug"}],
        }

        result = advisor.format_issue_suggestion(issue, 2)

        assert "[P1]" in result

    def test_format_without_priority_label(self):
        """Should not include priority marker when no P label."""
        issue = {
            "number": 100,
            "title": "Test Issue",
            "labels": [{"name": "enhancement"}],
        }

        result = advisor.format_issue_suggestion(issue, 1)

        assert "[P" not in result


class TestMain:
    """Tests for main function."""

    def test_skip_non_fork_session(self, capsys):
        """Should exit silently for non-fork sessions."""
        with (
            patch.object(advisor, "parse_hook_input") as mock_parse,
            patch.object(advisor, "is_fork_session") as mock_is_fork,
        ):
            mock_parse.return_value = {
                "session_id": "abc-123",
                "source": "new",
                "transcript_path": "/path/to/transcript",
            }
            mock_is_fork.return_value = False

            advisor.main()

            captured = capsys.readouterr()
            assert captured.out == ""

    def test_skip_when_no_active_worktrees(self, capsys):
        """Should exit silently when no active worktrees."""
        with (
            patch.object(advisor, "parse_hook_input") as mock_parse,
            patch.object(advisor, "is_fork_session") as mock_is_fork,
            patch.object(advisor, "get_active_worktree_sessions") as mock_get_sessions,
        ):
            mock_parse.return_value = {
                "session_id": "abc-123",
                "source": "resume",
                "transcript_path": "/path/to/transcript",
            }
            mock_is_fork.return_value = True
            mock_get_sessions.return_value = {"ancestor": [], "sibling": []}

            advisor.main()

            captured = capsys.readouterr()
            assert captured.out == ""

    def test_output_with_ancestor_worktrees(self, capsys):
        """Should output ancestor worktree info."""
        with (
            patch.object(advisor, "parse_hook_input") as mock_parse,
            patch.object(advisor, "is_fork_session") as mock_is_fork,
            patch.object(advisor, "get_active_worktree_sessions") as mock_get_sessions,
            patch.object(advisor, "suggest_independent_issues") as mock_suggest,
        ):
            mock_parse.return_value = {
                "session_id": "abc-123",
                "source": "resume",
                "transcript_path": "/path/to/transcript",
            }
            mock_is_fork.return_value = True

            # Create mock worktree info
            ancestor_info = MagicMock()
            ancestor_info.issue_number = 456
            ancestor_info.path = Path("/path/to/issue-456")
            ancestor_info.changed_files = {"file.py"}

            mock_get_sessions.return_value = {
                "ancestor": [ancestor_info],
                "sibling": [],
            }
            mock_suggest.return_value = []

            advisor.main()

            captured = capsys.readouterr()
            output = json.loads(captured.out)

            assert "hookSpecificOutput" in output
            message = output["hookSpecificOutput"]["systemMessage"]
            assert "親セッションの作業中Issue" in message
            assert "Issue #456" in message

    def test_output_with_sibling_worktrees(self, capsys):
        """Should output sibling worktree info with conflict warning."""
        with (
            patch.object(advisor, "parse_hook_input") as mock_parse,
            patch.object(advisor, "is_fork_session") as mock_is_fork,
            patch.object(advisor, "get_active_worktree_sessions") as mock_get_sessions,
            patch.object(advisor, "suggest_independent_issues") as mock_suggest,
        ):
            mock_parse.return_value = {
                "session_id": "abc-123",
                "source": "resume",
                "transcript_path": "/path/to/transcript",
            }
            mock_is_fork.return_value = True

            sibling_info = MagicMock()
            sibling_info.issue_number = 789
            sibling_info.path = Path("/path/to/issue-789")
            sibling_info.changed_files = {"other.py"}

            mock_get_sessions.return_value = {
                "ancestor": [],
                "sibling": [sibling_info],
            }
            mock_suggest.return_value = []

            advisor.main()

            captured = capsys.readouterr()
            output = json.loads(captured.out)

            message = output["hookSpecificOutput"]["systemMessage"]
            assert "sibling forkセッション" in message
            assert "競合注意" in message

    def test_output_with_suggested_issues(self, capsys):
        """Should output suggested independent issues."""
        with (
            patch.object(advisor, "parse_hook_input") as mock_parse,
            patch.object(advisor, "is_fork_session") as mock_is_fork,
            patch.object(advisor, "get_active_worktree_sessions") as mock_get_sessions,
            patch.object(advisor, "suggest_independent_issues") as mock_suggest,
        ):
            mock_parse.return_value = {
                "session_id": "abc-123",
                "source": "resume",
                "transcript_path": "/path/to/transcript",
            }
            mock_is_fork.return_value = True

            ancestor_info = MagicMock()
            ancestor_info.issue_number = 100
            ancestor_info.path = Path("/path/to/issue-100")
            ancestor_info.changed_files = set()

            mock_get_sessions.return_value = {
                "ancestor": [ancestor_info],
                "sibling": [],
            }
            mock_suggest.return_value = [
                {
                    "number": 200,
                    "title": "Independent Issue",
                    "labels": [{"name": "P2"}],
                },
            ]

            advisor.main()

            captured = capsys.readouterr()
            output = json.loads(captured.out)

            message = output["hookSpecificOutput"]["systemMessage"]
            assert "独立したIssue候補" in message
            assert "#200" in message
            assert "Independent Issue" in message

    def test_handles_session_error_gracefully(self, capsys):
        """Should handle errors gracefully without crashing."""
        with (
            patch.object(advisor, "parse_hook_input") as mock_parse,
            patch.object(advisor, "is_fork_session") as mock_is_fork,
            patch.object(advisor, "get_active_worktree_sessions") as mock_get_sessions,
        ):
            mock_parse.return_value = {
                "session_id": "abc-123",
                "source": "resume",
                "transcript_path": "/path/to/transcript",
            }
            mock_is_fork.return_value = True
            mock_get_sessions.side_effect = Exception("Test error")

            # Should not raise
            advisor.main()

            captured = capsys.readouterr()
            assert captured.out == ""
