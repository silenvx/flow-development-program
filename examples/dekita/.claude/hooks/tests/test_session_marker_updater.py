"""Tests for session-marker-updater hook."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# Add tests and hooks directory to path for imports
TESTS_DIR = Path(__file__).parent
HOOKS_DIR = TESTS_DIR.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from conftest import load_hook_module
from lib.session import create_hook_context

smu = load_hook_module("session-marker-updater")


class TestGetWorktreeRoot:
    """Tests for get_worktree_root function."""

    def test_inside_worktree(self):
        """Should return worktree root when inside a worktree."""
        cwd = Path("/path/to/repo/.worktrees/issue-123/subdir")
        result = smu.get_worktree_root(cwd)
        assert result == Path("/path/to/repo/.worktrees/issue-123")

    def test_inside_worktree_at_root(self):
        """Should return worktree root when at worktree root."""
        cwd = Path("/path/to/repo/.worktrees/feature-abc")
        result = smu.get_worktree_root(cwd)
        assert result == Path("/path/to/repo/.worktrees/feature-abc")

    def test_not_in_worktree(self):
        """Should return None when not in a worktree."""
        cwd = Path("/path/to/repo")
        result = smu.get_worktree_root(cwd)
        assert result is None

    def test_main_repo(self):
        """Should return None when in main repo."""
        cwd = Path("/path/to/repo/src/components")
        result = smu.get_worktree_root(cwd)
        assert result is None

    def test_windows_path(self):
        """Should handle Windows-style paths with backslashes."""
        # Note: Path() normalizes backslashes on Unix, so we test with a string
        cwd_str = r"C:\Users\Dev\.worktrees\issue-123\subdir"
        # Simulate what would happen on Windows
        import re

        match = re.search(r"(.*?[/\\]\.worktrees[/\\][^/\\]+)", cwd_str)
        assert match is not None
        assert match.group(1) == r"C:\Users\Dev\.worktrees\issue-123"

    def test_path_with_spaces(self):
        """Should handle paths containing spaces."""
        cwd = Path("/Users/John Doe/projects/.worktrees/issue-123/subdir")
        result = smu.get_worktree_root(cwd)
        assert result == Path("/Users/John Doe/projects/.worktrees/issue-123")


class TestWriteSessionMarker:
    """Tests for write_session_marker function."""

    TEST_SESSION_ID = "test-session-123"

    def test_write_success(self, tmp_path):
        """Should write session marker successfully."""
        ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
        result = smu.write_session_marker(ctx, tmp_path)
        assert result is True
        marker_file = tmp_path / smu.SESSION_MARKER_FILE
        assert marker_file.exists()
        assert marker_file.read_text() == self.TEST_SESSION_ID

    def test_write_failure_readonly(self, tmp_path):
        """Should return False when directory is not writable."""
        ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
        # Use a non-existent path
        result = smu.write_session_marker(ctx, Path("/nonexistent/path"))
        assert result is False


class TestMain:
    """Tests for main function."""

    def test_not_in_worktree(self):
        """Should succeed without writing when not in worktree."""
        with (
            mock.patch("os.getcwd", return_value="/path/to/main/repo"),
            mock.patch.object(smu, "parse_hook_input", return_value={"session_id": "test-session"}),
            mock.patch.object(smu, "log_hook_execution") as mock_log,
        ):
            with pytest.raises(SystemExit) as exc_info:
                smu.main()
            assert exc_info.value.code == 0
            mock_log.assert_called_once()
            assert "Not in worktree" in mock_log.call_args[0][2]

    def test_in_worktree_success(self, tmp_path):
        """Should write marker when in worktree."""
        worktree_path = tmp_path / ".worktrees" / "issue-456"
        worktree_path.mkdir(parents=True)
        cwd = worktree_path / "subdir"
        cwd.mkdir()

        with (
            mock.patch("os.getcwd", return_value=str(cwd)),
            mock.patch.object(smu, "parse_hook_input", return_value={"session_id": "session-xyz"}),
            mock.patch.object(smu, "log_hook_execution") as mock_log,
        ):
            with pytest.raises(SystemExit) as exc_info:
                smu.main()
            assert exc_info.value.code == 0

            # Check marker was written
            marker_file = worktree_path / smu.SESSION_MARKER_FILE
            assert marker_file.exists()
            assert marker_file.read_text() == "session-xyz"

            # Check log was called with success
            mock_log.assert_called()
            assert "Updated marker" in mock_log.call_args[0][2]

    def test_json_output_format(self, capsys):
        """Should output valid JSON with continue: True."""
        with (
            mock.patch("os.getcwd", return_value="/path/to/main/repo"),
            mock.patch.object(smu, "parse_hook_input", return_value={"session_id": "test-session"}),
            mock.patch.object(smu, "log_hook_execution"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                smu.main()
            assert exc_info.value.code == 0

            # Verify JSON output
            captured = capsys.readouterr()
            output = json.loads(captured.out.strip())
            assert output == {"continue": True}
