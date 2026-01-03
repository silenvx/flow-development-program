#!/usr/bin/env python3
"""
Unit tests for session-worktree-status.py

Tests cover:
- get_worktrees_info function (combined worktree list and lock status)
- read_session_marker function
- get_recent_commit_time function
- has_uncommitted_changes function
- main hook logic
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest import load_hook_module

# Import the hook using conftest helper
hook_module = load_hook_module("session-worktree-status")

# Import symbols
get_worktrees_info = hook_module.get_worktrees_info
get_cwd_worktree_info = hook_module.get_cwd_worktree_info
read_session_marker = hook_module.read_session_marker
get_recent_commit_time = hook_module.get_recent_commit_time
has_uncommitted_changes = hook_module.has_uncommitted_changes


class TestGetWorktreesInfo:
    """Tests for get_worktrees_info function."""

    @patch("session_worktree_status.subprocess.run")
    def test_no_worktrees(self, mock_run):
        """Test when no worktrees exist in .worktrees directory."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="worktree /path/to/main\nHEAD abc123\nbranch refs/heads/main\n",
        )
        result = get_worktrees_info()
        assert result == []

    @patch("session_worktree_status.subprocess.run")
    def test_with_worktrees(self, mock_run):
        """Test with worktrees in .worktrees directory."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "worktree /path/to/main\n"
                "HEAD abc123\n"
                "branch refs/heads/main\n"
                "\n"
                "worktree /path/to/main/.worktrees/issue-123\n"
                "HEAD def456\n"
                "branch refs/heads/feat/issue-123\n"
                "\n"
                "worktree /path/to/main/.worktrees/issue-456\n"
                "HEAD ghi789\n"
                "branch refs/heads/fix/issue-456\n"
            ),
        )
        result = get_worktrees_info()
        assert len(result) == 2
        assert result[0]["path"] == Path("/path/to/main/.worktrees/issue-123")
        assert result[0]["locked"] is False
        assert result[1]["path"] == Path("/path/to/main/.worktrees/issue-456")
        assert result[1]["locked"] is False

    @patch("session_worktree_status.subprocess.run")
    def test_worktree_locked(self, mock_run):
        """Test with locked worktree."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "worktree /path/to/main\n"
                "HEAD abc123\n"
                "branch refs/heads/main\n"
                "\n"
                "worktree /path/to/main/.worktrees/issue-123\n"
                "HEAD def456\n"
                "branch refs/heads/feat\n"
                "locked\n"
            ),
        )
        result = get_worktrees_info()
        assert len(result) == 1
        assert result[0]["path"] == Path("/path/to/main/.worktrees/issue-123")
        assert result[0]["locked"] is True

    @patch("session_worktree_status.subprocess.run")
    def test_worktree_locked_with_reason(self, mock_run):
        """Test with worktree locked with a reason."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "worktree /path/to/main/.worktrees/issue-123\n"
                "HEAD def456\n"
                "branch refs/heads/feat\n"
                "locked work in progress\n"
            ),
        )
        result = get_worktrees_info()
        assert len(result) == 1
        assert result[0]["locked"] is True

    @patch("session_worktree_status.subprocess.run")
    def test_git_command_fails(self, mock_run):
        """Test when git command fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = get_worktrees_info()
        assert result == []

    @patch("session_worktree_status.subprocess.run")
    def test_timeout(self, mock_run):
        """Test when git command times out."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=10)
        result = get_worktrees_info()
        assert result == []


class TestGetCwdWorktreeInfo:
    """Tests for get_cwd_worktree_info function."""

    @patch("session_worktree_status.Path.cwd")
    def test_cwd_in_worktree(self, mock_cwd):
        """Test when CWD is inside a worktree."""
        mock_cwd.return_value = Path("/Users/test/project/.worktrees/issue-123/src")
        result = get_cwd_worktree_info()
        assert result is not None
        worktree_name, main_repo_path = result
        assert worktree_name == "issue-123"
        assert main_repo_path == Path("/Users/test/project")

    @patch("session_worktree_status.Path.cwd")
    def test_cwd_in_worktree_root(self, mock_cwd):
        """Test when CWD is at worktree root."""
        mock_cwd.return_value = Path("/Users/test/project/.worktrees/issue-456")
        result = get_cwd_worktree_info()
        assert result is not None
        worktree_name, main_repo_path = result
        assert worktree_name == "issue-456"
        assert main_repo_path == Path("/Users/test/project")

    @patch("session_worktree_status.Path.cwd")
    def test_cwd_not_in_worktree(self, mock_cwd):
        """Test when CWD is not inside a worktree."""
        mock_cwd.return_value = Path("/Users/test/project/src")
        result = get_cwd_worktree_info()
        assert result is None

    @patch("session_worktree_status.Path.cwd")
    def test_cwd_in_main_repo(self, mock_cwd):
        """Test when CWD is in main repo."""
        mock_cwd.return_value = Path("/Users/test/project")
        result = get_cwd_worktree_info()
        assert result is None

    @patch("session_worktree_status.Path.cwd")
    def test_cwd_access_error(self, mock_cwd):
        """Test when CWD cannot be accessed (e.g., deleted directory)."""
        mock_cwd.side_effect = OSError("No such directory")
        result = get_cwd_worktree_info()
        assert result is None

    @patch("session_worktree_status.Path.cwd")
    def test_cwd_with_special_chars(self, mock_cwd):
        """Test when path contains special characters that need escaping."""
        # Path with single quote
        mock_cwd.return_value = Path("/Users/test/my'project/.worktrees/issue-123")
        result = get_cwd_worktree_info()
        assert result is not None
        worktree_name, main_repo_path = result
        assert worktree_name == "issue-123"
        assert main_repo_path == Path("/Users/test/my'project")


class TestReadSessionMarker:
    """Tests for read_session_marker function."""

    def test_marker_exists(self, tmp_path):
        """Test reading existing marker file in JSON format."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        marker_path = worktree_path / ".claude-session"
        marker_data = {
            "session_id": "session-abc123",
            "created_at": "2025-12-30T09:30:00+00:00",
        }
        marker_path.write_text(json.dumps(marker_data))

        result = read_session_marker(worktree_path)
        assert result is not None
        assert result.get("session_id") == "session-abc123"

    def test_marker_not_exists(self, tmp_path):
        """Test when marker file doesn't exist."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()

        result = read_session_marker(worktree_path)
        assert result is None

    def test_marker_invalid_json(self, tmp_path):
        """Test that invalid JSON returns None."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        marker_path = worktree_path / ".claude-session"
        marker_path.write_text("not-valid-json")

        result = read_session_marker(worktree_path)
        assert result is None

    def test_marker_json_format(self, tmp_path):
        """Test reading marker file in new JSON format."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        marker_path = worktree_path / ".claude-session"
        marker_data = {
            "session_id": "session-xyz789",
            "created_at": "2025-12-30T09:30:00+00:00",
        }
        marker_path.write_text(json.dumps(marker_data))

        result = read_session_marker(worktree_path)
        assert result is not None
        assert result.get("session_id") == "session-xyz789"
        assert result.get("created_at") == "2025-12-30T09:30:00+00:00"

    def test_marker_empty_json(self, tmp_path):
        """Test reading marker file with empty JSON object."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        marker_path = worktree_path / ".claude-session"
        marker_path.write_text("{}")

        result = read_session_marker(worktree_path)
        assert result is not None
        # Empty JSON should return empty session_id
        assert result.get("session_id") == ""
        assert result.get("created_at") == ""

    def test_marker_json_missing_fields(self, tmp_path):
        """Test reading marker file with JSON missing some fields."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        marker_path = worktree_path / ".claude-session"
        # Only session_id, no created_at
        marker_path.write_text('{"session_id": "session-partial"}')

        result = read_session_marker(worktree_path)
        assert result is not None
        assert result.get("session_id") == "session-partial"
        assert result.get("created_at") == ""


class TestGetRecentCommitTime:
    """Tests for get_recent_commit_time function."""

    @patch("session_worktree_status.subprocess.run")
    @patch("session_worktree_status.time.time")
    def test_recent_commit(self, mock_time, mock_run):
        """Test with recent commit."""
        mock_time.return_value = 1704067200  # Current time
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="1704067200",  # 0 seconds ago
        )
        result = get_recent_commit_time(Path("/fake/worktree"))
        assert result == 0

    @patch("session_worktree_status.subprocess.run")
    @patch("session_worktree_status.time.time")
    def test_old_commit(self, mock_time, mock_run):
        """Test with old commit."""
        mock_time.return_value = 1704067200
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="1704060000",  # 7200 seconds ago (2 hours)
        )
        result = get_recent_commit_time(Path("/fake/worktree"))
        assert result == 7200

    @patch("session_worktree_status.subprocess.run")
    def test_git_fails(self, mock_run):
        """Test when git command fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = get_recent_commit_time(Path("/fake/worktree"))
        assert result is None


class TestHasUncommittedChanges:
    """Tests for has_uncommitted_changes function."""

    @patch("session_worktree_status.subprocess.run")
    def test_has_changes(self, mock_run):
        """Test when there are uncommitted changes."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=" M src/file.py\n?? new_file.txt",
        )
        result = has_uncommitted_changes(Path("/fake/worktree"))
        assert result is True

    @patch("session_worktree_status.subprocess.run")
    def test_no_changes(self, mock_run):
        """Test when working tree is clean."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
        )
        result = has_uncommitted_changes(Path("/fake/worktree"))
        assert result is False

    @patch("session_worktree_status.subprocess.run")
    def test_git_fails(self, mock_run):
        """Test when git command fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="error")
        result = has_uncommitted_changes(Path("/fake/worktree"))
        assert result is False


class TestMain:
    """Tests for main function."""

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.parse_hook_input")
    def test_cwd_in_worktree(self, mock_parse, mock_get_info, mock_cwd_info, capsys):
        """Test when CWD is inside a worktree."""
        mock_parse.return_value = {"session_id": "test-session"}
        mock_cwd_info.return_value = ("issue-123", Path("/Users/test/project"))
        mock_get_info.return_value = []  # No other worktrees

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        assert "CWDがworktree内です: issue-123" in output["systemMessage"]
        # Path is quoted by shlex.quote (no quotes needed for simple paths)
        assert "cd /Users/test/project" in output["systemMessage"]

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.parse_hook_input")
    def test_cwd_in_worktree_with_special_chars(
        self, mock_parse, mock_get_info, mock_cwd_info, capsys
    ):
        """Test when CWD is inside a worktree with special characters in path."""
        mock_parse.return_value = {"session_id": "test-session"}
        # Path with single quote that needs escaping
        mock_cwd_info.return_value = ("issue-123", Path("/Users/test/my'project"))
        mock_get_info.return_value = []

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        # shlex.quote should escape the single quote
        assert "cd '/Users/test/my'\"'\"'project'" in output["systemMessage"]

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.parse_hook_input")
    def test_cwd_in_worktree_with_other_warnings(
        self,
        mock_parse,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        tmp_path,
        capsys,
    ):
        """Test when CWD is inside a worktree AND other worktrees have issues."""
        worktree_path = tmp_path / "issue-456"
        worktree_path.mkdir()

        mock_parse.return_value = {"session_id": "current-session"}
        mock_cwd_info.return_value = ("issue-123", Path("/Users/test/project"))
        mock_get_info.return_value = [{"path": worktree_path, "locked": True}]
        mock_marker.return_value = None
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 7200

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        # Both CWD warning and worktree warning should be present
        assert "CWDがworktree内です: issue-123" in output["systemMessage"]
        assert "issue-456" in output["systemMessage"]
        assert "ロック中" in output["systemMessage"]

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.parse_hook_input")
    def test_no_worktrees(self, mock_parse, mock_get_info, mock_cwd_info, capsys):
        """Test when no worktrees exist."""
        mock_parse.return_value = {"session_id": "test-session"}
        mock_cwd_info.return_value = None  # CWD not in worktree
        mock_get_info.return_value = []

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" not in output

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.parse_hook_input")
    def test_clean_worktrees(
        self,
        mock_parse,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        tmp_path,
        capsys,
    ):
        """Test when all worktrees are clean."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()

        mock_parse.return_value = {"session_id": "current-session"}
        mock_cwd_info.return_value = None  # CWD not in worktree
        mock_get_info.return_value = [{"path": worktree_path, "locked": False}]
        mock_marker.return_value = {"session_id": "current-session"}  # Same session
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 7200  # 2 hours ago

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" not in output

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.parse_hook_input")
    def test_worktree_with_different_session(
        self,
        mock_parse,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        tmp_path,
        capsys,
    ):
        """Test when worktree has different session marker."""
        worktree_path = tmp_path / "issue-123"
        worktree_path.mkdir()

        mock_parse.return_value = {"session_id": "current-session"}

        mock_cwd_info.return_value = None  # CWD not in worktree
        mock_get_info.return_value = [{"path": worktree_path, "locked": False}]
        mock_marker.return_value = {"session_id": "other-session"}  # Different session
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 7200

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        assert "issue-123" in output["systemMessage"]
        assert "別セッション" in output["systemMessage"]

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.parse_hook_input")
    def test_worktree_locked(
        self,
        mock_parse,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        tmp_path,
        capsys,
    ):
        """Test when worktree is locked."""
        worktree_path = tmp_path / "issue-456"
        worktree_path.mkdir()

        mock_parse.return_value = {"session_id": "current-session"}

        mock_cwd_info.return_value = None  # CWD not in worktree
        mock_get_info.return_value = [{"path": worktree_path, "locked": True}]
        mock_marker.return_value = None
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 7200

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        assert "issue-456" in output["systemMessage"]
        assert "ロック中" in output["systemMessage"]

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.parse_hook_input")
    def test_worktree_with_uncommitted_changes(
        self,
        mock_parse,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        tmp_path,
        capsys,
    ):
        """Test when worktree has uncommitted changes."""
        worktree_path = tmp_path / "issue-789"
        worktree_path.mkdir()

        mock_parse.return_value = {"session_id": "current-session"}

        mock_cwd_info.return_value = None  # CWD not in worktree
        mock_get_info.return_value = [{"path": worktree_path, "locked": False}]
        mock_marker.return_value = None
        mock_uncommitted.return_value = True
        mock_commit_time.return_value = 7200

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        assert "未コミット変更あり" in output["systemMessage"]

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.parse_hook_input")
    def test_worktree_with_recent_commit(
        self,
        mock_parse,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        tmp_path,
        capsys,
    ):
        """Test when worktree has recent commit."""
        worktree_path = tmp_path / "issue-999"
        worktree_path.mkdir()

        mock_parse.return_value = {"session_id": "current-session"}

        mock_cwd_info.return_value = None  # CWD not in worktree
        mock_get_info.return_value = [{"path": worktree_path, "locked": False}]
        mock_marker.return_value = None
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 1800  # 30 minutes ago

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        assert "直近" in output["systemMessage"]
        assert "分前にコミット" in output["systemMessage"]

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.parse_hook_input")
    def test_worktree_with_very_recent_commit(
        self,
        mock_parse,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        tmp_path,
        capsys,
    ):
        """Test when worktree has commit less than 1 minute ago."""
        worktree_path = tmp_path / "issue-888"
        worktree_path.mkdir()

        mock_parse.return_value = {"session_id": "current-session"}

        mock_cwd_info.return_value = None  # CWD not in worktree
        mock_get_info.return_value = [{"path": worktree_path, "locked": False}]
        mock_marker.return_value = None
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 30  # 30 seconds ago

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        assert "直近1分未満にコミット" in output["systemMessage"]

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.parse_hook_input")
    def test_worktree_with_long_session_id(
        self,
        mock_parse,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        tmp_path,
        capsys,
    ):
        """Test when worktree has session ID longer than 16 chars."""
        worktree_path = tmp_path / "issue-777"
        worktree_path.mkdir()

        mock_parse.return_value = {"session_id": "current-session"}

        mock_cwd_info.return_value = None  # CWD not in worktree
        mock_get_info.return_value = [{"path": worktree_path, "locked": False}]
        # Session ID longer than 16 characters
        mock_marker.return_value = {"session_id": "very-long-session-id-12345678"}
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 7200

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        assert "別セッション: very-long-sessio..." in output["systemMessage"]

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.parse_hook_input")
    def test_worktree_with_short_session_id(
        self,
        mock_parse,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        tmp_path,
        capsys,
    ):
        """Test when worktree has session ID shorter than 16 chars (no ellipsis)."""
        worktree_path = tmp_path / "issue-666"
        worktree_path.mkdir()

        mock_parse.return_value = {"session_id": "current-session"}

        mock_cwd_info.return_value = None  # CWD not in worktree
        mock_get_info.return_value = [{"path": worktree_path, "locked": False}]
        # Session ID exactly 16 characters or shorter - no ellipsis should appear
        mock_marker.return_value = {"session_id": "short-session"}
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 7200

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        assert "別セッション: short-session" in output["systemMessage"]
        assert "..." not in output["systemMessage"]

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.parse_hook_input")
    def test_worktree_with_stale_marker(
        self,
        mock_parse,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        tmp_path,
        capsys,
    ):
        """Test when worktree has same session ID but stale timestamp (context continuation case)."""
        worktree_path = tmp_path / "issue-stale"
        worktree_path.mkdir()

        mock_parse.return_value = {"session_id": "current-session"}

        mock_cwd_info.return_value = None  # CWD not in worktree
        mock_get_info.return_value = [{"path": worktree_path, "locked": False}]
        # Same session ID but with old timestamp (2 hours = 7200 seconds ago)
        from datetime import UTC, datetime, timedelta

        old_time = datetime.now(UTC) - timedelta(hours=2)
        mock_marker.return_value = {
            "session_id": "current-session",
            "created_at": old_time.isoformat(),
        }
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 7200

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        assert "issue-stale" in output["systemMessage"]
        assert "古いセッションマーカー" in output["systemMessage"]


class TestGetMarkerAgeSeconds:
    """Tests for get_marker_age_seconds function."""

    def test_valid_timestamp(self):
        """Test with valid ISO format timestamp."""
        from datetime import UTC, datetime, timedelta

        get_marker_age_seconds = hook_module.get_marker_age_seconds

        # Create marker with timestamp 1 hour ago
        old_time = datetime.now(UTC) - timedelta(hours=1)
        marker = {"session_id": "test", "created_at": old_time.isoformat()}

        result = get_marker_age_seconds(marker)
        assert result is not None
        # Allow 5 seconds of tolerance for test execution time
        assert 3595 <= result <= 3605

    def test_no_created_at(self):
        """Test when created_at is missing."""
        get_marker_age_seconds = hook_module.get_marker_age_seconds

        marker = {"session_id": "test"}
        result = get_marker_age_seconds(marker)
        assert result is None

    def test_empty_created_at(self):
        """Test when created_at is empty string."""
        get_marker_age_seconds = hook_module.get_marker_age_seconds

        marker = {"session_id": "test", "created_at": ""}
        result = get_marker_age_seconds(marker)
        assert result is None

    def test_invalid_timestamp(self):
        """Test with invalid timestamp format."""
        get_marker_age_seconds = hook_module.get_marker_age_seconds

        marker = {"session_id": "test", "created_at": "not-a-date"}
        result = get_marker_age_seconds(marker)
        assert result is None

    def test_naive_timestamp(self):
        """Test with naive (timezone-unaware) timestamp.

        The function assumes naive timestamps are UTC.
        """
        from datetime import UTC, datetime, timedelta

        get_marker_age_seconds = hook_module.get_marker_age_seconds

        # Create naive timestamp in UTC (without timezone info)
        # The function treats naive timestamps as UTC
        old_time = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None)
        marker = {"session_id": "test", "created_at": old_time.isoformat()}

        result = get_marker_age_seconds(marker)
        assert result is not None
        # Should calculate age correctly (assuming UTC)
        assert 3595 <= result <= 3605


class TestForkSessionDetection:
    """Tests for fork-session detection functionality (Issue #2466)."""

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.create_hook_context")
    @patch("session_worktree_status.get_session_ancestry")
    @patch("session_worktree_status.is_fork_session")
    @patch("session_worktree_status.parse_hook_input")
    def test_fork_session_detects_ancestor_worktree(
        self,
        mock_parse,
        mock_is_fork,
        mock_ancestry,
        mock_session_id,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        tmp_path,
        capsys,
    ):
        """Test that fork-session detects and warns about ancestor session's worktree."""
        worktree_path = tmp_path / "issue-123"
        worktree_path.mkdir()

        mock_parse.return_value = {
            "session_id": "child-session",
            "source": "resume",
            "transcript_path": "/tmp/transcript.json",
        }
        mock_session_id.return_value.get_session_id.return_value = "child-session"
        mock_is_fork.return_value = True
        mock_ancestry.return_value = ["parent-session", "grandparent-session"]
        mock_cwd_info.return_value = None
        mock_get_info.return_value = [{"path": worktree_path, "locked": False}]
        # Worktree belongs to parent session (ancestor)
        mock_marker.return_value = {"session_id": "parent-session"}
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 7200

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        # Fork-session specific warning should appear
        assert "fork-session検出" in output["systemMessage"]
        assert "元セッション（fork元）のworktree" in output["systemMessage"]
        assert "介入は禁止" in output["systemMessage"]
        assert "issue-123" in output["systemMessage"]

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.create_hook_context")
    @patch("session_worktree_status.get_session_ancestry")
    @patch("session_worktree_status.is_fork_session")
    @patch("session_worktree_status.parse_hook_input")
    def test_fork_session_with_non_ancestor_worktree(
        self,
        mock_parse,
        mock_is_fork,
        mock_ancestry,
        mock_session_id,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        tmp_path,
        capsys,
    ):
        """Test that fork-session shows normal warning for non-ancestor session's worktree."""
        worktree_path = tmp_path / "issue-456"
        worktree_path.mkdir()

        mock_parse.return_value = {
            "session_id": "child-session",
            "source": "resume",
            "transcript_path": "/tmp/transcript.json",
        }
        mock_session_id.return_value.get_session_id.return_value = "child-session"
        mock_is_fork.return_value = True
        mock_ancestry.return_value = ["parent-session"]
        mock_cwd_info.return_value = None
        mock_get_info.return_value = [{"path": worktree_path, "locked": False}]
        # Worktree belongs to unrelated session (not an ancestor)
        mock_marker.return_value = {"session_id": "unrelated-session"}
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 7200

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        # Should show normal "different session" warning, not fork-specific warning
        assert "別セッション" in output["systemMessage"]
        assert "fork-session検出" not in output["systemMessage"]

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.create_hook_context")
    @patch("session_worktree_status.get_session_ancestry")
    @patch("session_worktree_status.is_fork_session")
    @patch("session_worktree_status.parse_hook_input")
    def test_non_fork_session_no_ancestor_check(
        self,
        mock_parse,
        mock_is_fork,
        mock_ancestry,
        mock_session_id,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        tmp_path,
        capsys,
    ):
        """Test that non-fork session doesn't do ancestor checking."""
        worktree_path = tmp_path / "issue-789"
        worktree_path.mkdir()

        mock_parse.return_value = {"session_id": "normal-session"}
        mock_session_id.return_value.get_session_id.return_value = "normal-session"
        mock_is_fork.return_value = False
        mock_cwd_info.return_value = None
        mock_get_info.return_value = [{"path": worktree_path, "locked": False}]
        mock_marker.return_value = {"session_id": "other-session"}
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 7200

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        # Should show normal warning, not fork-specific
        assert "別セッション" in output["systemMessage"]
        assert "fork-session検出" not in output["systemMessage"]
        # get_session_ancestry should not be called for non-fork sessions
        mock_ancestry.assert_not_called()

    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.create_hook_context")
    @patch("session_worktree_status.get_session_ancestry")
    @patch("session_worktree_status.is_fork_session")
    @patch("session_worktree_status.parse_hook_input")
    def test_fork_session_excludes_current_session_from_ancestors(
        self,
        mock_parse,
        mock_is_fork,
        mock_ancestry,
        mock_session_id,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        tmp_path,
        capsys,
    ):
        """Test that current session is excluded from ancestor list."""
        worktree_path = tmp_path / "issue-current"
        worktree_path.mkdir()

        mock_parse.return_value = {
            "session_id": "current-session",
            "source": "resume",
            "transcript_path": "/tmp/transcript.json",
        }
        mock_session_id.return_value.get_session_id.return_value = "current-session"
        mock_is_fork.return_value = True
        # Ancestry includes current session (should be filtered out)
        mock_ancestry.return_value = ["current-session", "parent-session"]
        mock_cwd_info.return_value = None
        mock_get_info.return_value = [{"path": worktree_path, "locked": False}]
        # Worktree belongs to current session (same session)
        mock_marker.return_value = {"session_id": "current-session"}
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 7200

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        # No warning for current session's own worktree
        assert "systemMessage" not in output or "fork-session検出" not in output.get(
            "systemMessage", ""
        )


class TestLoadSessionCreatedIssues:
    """Tests for load_session_created_issues function (Issue #2475)."""

    def test_no_file_returns_empty_list(self, tmp_path, monkeypatch):
        """Returns empty list when session issues file doesn't exist."""
        # Mock _get_session_log_dir to return temp directory
        monkeypatch.setattr(hook_module, "_get_session_log_dir", lambda: tmp_path)

        result = hook_module.load_session_created_issues("nonexistent-session")
        assert result == []

    def test_valid_file_returns_issues(self, tmp_path, monkeypatch):
        """Returns issue numbers from valid JSON file."""
        monkeypatch.setattr(hook_module, "_get_session_log_dir", lambda: tmp_path)

        # Create session issues file
        issues_file = tmp_path / "session-created-issues-test-session.json"
        issues_file.write_text(json.dumps({"issues": [123, 456, 789]}))

        result = hook_module.load_session_created_issues("test-session")
        assert result == [123, 456, 789]

    def test_empty_issues_list(self, tmp_path, monkeypatch):
        """Returns empty list when issues array is empty."""
        monkeypatch.setattr(hook_module, "_get_session_log_dir", lambda: tmp_path)

        issues_file = tmp_path / "session-created-issues-empty-session.json"
        issues_file.write_text(json.dumps({"issues": []}))

        result = hook_module.load_session_created_issues("empty-session")
        assert result == []

    def test_missing_issues_key(self, tmp_path, monkeypatch):
        """Returns empty list when 'issues' key is missing."""
        monkeypatch.setattr(hook_module, "_get_session_log_dir", lambda: tmp_path)

        issues_file = tmp_path / "session-created-issues-no-key.json"
        issues_file.write_text(json.dumps({"other_data": "value"}))

        result = hook_module.load_session_created_issues("no-key")
        assert result == []

    def test_invalid_json(self, tmp_path, monkeypatch):
        """Returns empty list when file contains invalid JSON."""
        monkeypatch.setattr(hook_module, "_get_session_log_dir", lambda: tmp_path)

        issues_file = tmp_path / "session-created-issues-invalid.json"
        issues_file.write_text("not valid json {{{")

        result = hook_module.load_session_created_issues("invalid")
        assert result == []

    def test_non_dict_json_array(self, tmp_path, monkeypatch):
        """Returns empty list when file contains JSON array instead of object."""
        monkeypatch.setattr(hook_module, "_get_session_log_dir", lambda: tmp_path)

        issues_file = tmp_path / "session-created-issues-array.json"
        issues_file.write_text(json.dumps([1, 2, 3]))

        result = hook_module.load_session_created_issues("array")
        assert result == []

    def test_non_dict_json_string(self, tmp_path, monkeypatch):
        """Returns empty list when file contains JSON string instead of object."""
        monkeypatch.setattr(hook_module, "_get_session_log_dir", lambda: tmp_path)

        issues_file = tmp_path / "session-created-issues-string.json"
        issues_file.write_text(json.dumps("just a string"))

        result = hook_module.load_session_created_issues("string")
        assert result == []

    def test_issues_not_list(self, tmp_path, monkeypatch):
        """Returns empty list when 'issues' value is not a list."""
        monkeypatch.setattr(hook_module, "_get_session_log_dir", lambda: tmp_path)

        issues_file = tmp_path / "session-created-issues-not-list.json"
        issues_file.write_text(json.dumps({"issues": "not a list"}))

        result = hook_module.load_session_created_issues("not-list")
        assert result == []

    def test_issues_with_non_int_elements(self, tmp_path, monkeypatch):
        """Returns empty list when 'issues' contains non-int elements."""
        monkeypatch.setattr(hook_module, "_get_session_log_dir", lambda: tmp_path)

        issues_file = tmp_path / "session-created-issues-mixed.json"
        issues_file.write_text(json.dumps({"issues": [1, "two", 3]}))

        result = hook_module.load_session_created_issues("mixed")
        assert result == []

    def test_issues_with_null_elements(self, tmp_path, monkeypatch):
        """Returns empty list when 'issues' contains null elements."""
        monkeypatch.setattr(hook_module, "_get_session_log_dir", lambda: tmp_path)

        issues_file = tmp_path / "session-created-issues-null.json"
        issues_file.write_text(json.dumps({"issues": [1, None, 3]}))

        result = hook_module.load_session_created_issues("null")
        assert result == []

    def test_path_traversal_sanitized(self, tmp_path, monkeypatch):
        """Path traversal attempts are sanitized."""
        monkeypatch.setattr(hook_module, "_get_session_log_dir", lambda: tmp_path)

        # Create file with sanitized name (just "passwd.json" part after Path.name)
        issues_file = tmp_path / "session-created-issues-passwd.json"
        issues_file.write_text(json.dumps({"issues": [999]}))

        # Attempt path traversal - should be sanitized to just "passwd"
        result = hook_module.load_session_created_issues("../../etc/passwd")
        assert result == [999]

    def test_file_read_error(self, tmp_path, monkeypatch):
        """Returns empty list on file read error."""
        monkeypatch.setattr(hook_module, "_get_session_log_dir", lambda: tmp_path)

        # Create a directory with same name as expected file (causes read error)
        issues_dir = tmp_path / "session-created-issues-dir-session.json"
        issues_dir.mkdir()

        result = hook_module.load_session_created_issues("dir-session")
        assert result == []


class TestForkSessionSelfCreatedIssues:
    """Tests for self-created Issue display in fork-session warnings (Issue #2475)."""

    @patch("session_worktree_status.load_session_created_issues")
    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.create_hook_context")
    @patch("session_worktree_status.get_session_ancestry")
    @patch("session_worktree_status.is_fork_session")
    @patch("session_worktree_status.parse_hook_input")
    def test_fork_session_shows_self_created_issues(
        self,
        mock_parse,
        mock_is_fork,
        mock_ancestry,
        mock_session_id,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        mock_load_issues,
        tmp_path,
        capsys,
    ):
        """Fork-session warning shows self-created Issues list."""
        worktree_path = tmp_path / "issue-123"
        worktree_path.mkdir()

        mock_parse.return_value = {
            "session_id": "child-session",
            "source": "resume",
            "transcript_path": "/tmp/transcript.json",
        }
        mock_session_id.return_value.get_session_id.return_value = "child-session"
        mock_is_fork.return_value = True
        mock_ancestry.return_value = ["parent-session"]
        mock_cwd_info.return_value = None
        mock_get_info.return_value = [{"path": worktree_path, "locked": False}]
        mock_marker.return_value = {"session_id": "parent-session"}
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 7200
        # This session created Issues #100 and #200
        mock_load_issues.return_value = [100, 200]

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        # Should show self-created Issues
        assert "このセッションで作成したIssue" in output["systemMessage"]
        assert "#100" in output["systemMessage"]
        assert "#200" in output["systemMessage"]
        assert "警告なしで許可" in output["systemMessage"]

    @patch("session_worktree_status.load_session_created_issues")
    @patch("session_worktree_status.get_cwd_worktree_info")
    @patch("session_worktree_status.get_worktrees_info")
    @patch("session_worktree_status.read_session_marker")
    @patch("session_worktree_status.has_uncommitted_changes")
    @patch("session_worktree_status.get_recent_commit_time")
    @patch("session_worktree_status.create_hook_context")
    @patch("session_worktree_status.get_session_ancestry")
    @patch("session_worktree_status.is_fork_session")
    @patch("session_worktree_status.parse_hook_input")
    def test_fork_session_no_self_created_issues(
        self,
        mock_parse,
        mock_is_fork,
        mock_ancestry,
        mock_session_id,
        mock_commit_time,
        mock_uncommitted,
        mock_marker,
        mock_get_info,
        mock_cwd_info,
        mock_load_issues,
        tmp_path,
        capsys,
    ):
        """Fork-session warning shows generic exception message when no self-created Issues."""
        worktree_path = tmp_path / "issue-456"
        worktree_path.mkdir()

        mock_parse.return_value = {
            "session_id": "child-session",
            "source": "resume",
            "transcript_path": "/tmp/transcript.json",
        }
        mock_session_id.return_value.get_session_id.return_value = "child-session"
        mock_is_fork.return_value = True
        mock_ancestry.return_value = ["parent-session"]
        mock_cwd_info.return_value = None
        mock_get_info.return_value = [{"path": worktree_path, "locked": False}]
        mock_marker.return_value = {"session_id": "parent-session"}
        mock_uncommitted.return_value = False
        mock_commit_time.return_value = 7200
        # No self-created Issues
        mock_load_issues.return_value = []

        hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "systemMessage" in output
        # Should show unified message structure (no self-created Issues yet)
        assert "このセッションで作成したIssue" in output["systemMessage"]
        assert "まだありません" in output["systemMessage"]
        assert "警告なしで許可" in output["systemMessage"]
        # Should NOT show specific issue numbers
        assert "#100" not in output["systemMessage"]
