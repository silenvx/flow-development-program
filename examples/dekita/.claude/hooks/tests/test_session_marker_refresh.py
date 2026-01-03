"""Tests for session-marker-refresh hook."""

from __future__ import annotations

import json
import os
import sys
import time
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

smr = load_hook_module("session-marker-refresh")


class TestGetWorktreeRoot:
    """Tests for get_worktree_root function."""

    def test_inside_worktree(self):
        """Should return worktree root when inside a worktree."""
        with mock.patch.object(
            smr, "get_effective_cwd", return_value=Path("/path/to/repo/.worktrees/issue-123/subdir")
        ):
            result = smr.get_worktree_root()
            assert result == Path("/path/to/repo/.worktrees/issue-123")

    def test_inside_worktree_at_root(self):
        """Should return worktree root when at worktree root."""
        with mock.patch.object(
            smr, "get_effective_cwd", return_value=Path("/path/to/repo/.worktrees/feature-abc")
        ):
            result = smr.get_worktree_root()
            assert result == Path("/path/to/repo/.worktrees/feature-abc")

    def test_not_in_worktree(self):
        """Should return None when not in a worktree."""
        with mock.patch.object(smr, "get_effective_cwd", return_value=Path("/path/to/repo")):
            result = smr.get_worktree_root()
            assert result is None

    def test_main_repo(self):
        """Should return None when in main repo."""
        with mock.patch.object(
            smr, "get_effective_cwd", return_value=Path("/path/to/repo/src/components")
        ):
            result = smr.get_worktree_root()
            assert result is None

    def test_path_with_spaces(self):
        """Should handle paths containing spaces."""
        with mock.patch.object(
            smr,
            "get_effective_cwd",
            return_value=Path("/Users/John Doe/projects/.worktrees/issue-123/subdir"),
        ):
            result = smr.get_worktree_root()
            assert result == Path("/Users/John Doe/projects/.worktrees/issue-123")


class TestNeedsRefresh:
    """Tests for needs_refresh function."""

    def test_marker_not_exists(self, tmp_path):
        """Should return False when marker file does not exist."""
        marker_path = tmp_path / ".claude-session"
        assert smr.needs_refresh(marker_path) is False

    def test_marker_fresh(self, tmp_path):
        """Should return False when marker is fresh (within interval)."""
        marker_path = tmp_path / ".claude-session"
        marker_path.write_text("session-123")
        # Just created, so it's fresh
        assert smr.needs_refresh(marker_path) is False

    def test_marker_stale(self, tmp_path):
        """Should return True when marker is stale (older than interval)."""
        marker_path = tmp_path / ".claude-session"
        marker_path.write_text("session-123")
        # Set mtime to 15 minutes ago (older than 10 min interval)
        old_time = time.time() - 900
        os.utime(marker_path, (old_time, old_time))
        assert smr.needs_refresh(marker_path) is True

    def test_marker_just_at_boundary(self, tmp_path):
        """Should return False when marker is exactly at refresh interval."""
        marker_path = tmp_path / ".claude-session"
        marker_path.write_text("session-123")

        # Use mock to control time.time() for consistent test
        fixed_time = 1000000.0
        boundary_time = fixed_time - smr.REFRESH_INTERVAL
        os.utime(marker_path, (boundary_time, boundary_time))

        with mock.patch.object(smr.time, "time", return_value=fixed_time):
            # At boundary, age equals REFRESH_INTERVAL, not greater than
            assert smr.needs_refresh(marker_path) is False


class TestRefreshMarker:
    """Tests for refresh_marker function."""

    def test_refresh_success(self, tmp_path):
        """Should return True and update mtime when successful."""
        marker_path = tmp_path / ".claude-session"
        marker_path.write_text("session-123")

        # Set old mtime (30 minutes ago)
        old_time = time.time() - 1800
        os.utime(marker_path, (old_time, old_time))
        old_mtime = marker_path.stat().st_mtime

        # Refresh
        result = smr.refresh_marker(marker_path)
        new_mtime = marker_path.stat().st_mtime

        assert result is True
        assert new_mtime > old_mtime
        # Content should be unchanged
        assert marker_path.read_text() == "session-123"

    def test_refresh_nonexistent(self, tmp_path):
        """Should return False when file does not exist."""
        marker_path = tmp_path / "nonexistent" / ".claude-session"
        result = smr.refresh_marker(marker_path)
        assert result is False


class TestMain:
    """Tests for main function."""

    def test_not_in_worktree(self, capsys):
        """Should exit successfully without refreshing when not in worktree."""
        with (
            mock.patch.object(smr, "get_effective_cwd", return_value=Path("/path/to/main/repo")),
            mock.patch.object(smr, "parse_hook_input", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                smr.main()
            assert exc_info.value.code == 0

            captured = capsys.readouterr()
            output = json.loads(captured.out.strip())
            assert output == {"continue": True}

    def test_no_marker_file(self, tmp_path, capsys):
        """Should exit successfully when marker file does not exist."""
        worktree_path = tmp_path / ".worktrees" / "issue-123"
        worktree_path.mkdir(parents=True)

        with (
            mock.patch.object(smr, "get_effective_cwd", return_value=worktree_path),
            mock.patch.object(smr, "parse_hook_input", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                smr.main()
            assert exc_info.value.code == 0

            captured = capsys.readouterr()
            output = json.loads(captured.out.strip())
            assert output == {"continue": True}

    def test_marker_fresh_no_refresh(self, tmp_path, capsys):
        """Should not refresh when marker is fresh."""
        worktree_path = tmp_path / ".worktrees" / "issue-123"
        worktree_path.mkdir(parents=True)
        marker_path = worktree_path / smr.SESSION_MARKER_FILE
        marker_path.write_text("session-123")

        with (
            mock.patch.object(smr, "get_effective_cwd", return_value=worktree_path),
            mock.patch.object(smr, "parse_hook_input", return_value={}),
            mock.patch.object(smr, "log_hook_execution") as mock_log,
        ):
            with pytest.raises(SystemExit) as exc_info:
                smr.main()
            assert exc_info.value.code == 0

            # Should not log refresh
            mock_log.assert_not_called()

            captured = capsys.readouterr()
            output = json.loads(captured.out.strip())
            assert output == {"continue": True}

    def test_marker_stale_refresh(self, tmp_path, capsys):
        """Should refresh when marker is stale."""
        worktree_path = tmp_path / ".worktrees" / "issue-123"
        worktree_path.mkdir(parents=True)
        marker_path = worktree_path / smr.SESSION_MARKER_FILE
        marker_path.write_text("session-123")

        # Set mtime to 15 minutes ago (older than 10 min interval)
        old_time = time.time() - 900
        os.utime(marker_path, (old_time, old_time))
        old_mtime = marker_path.stat().st_mtime

        with (
            mock.patch.object(smr, "get_effective_cwd", return_value=worktree_path),
            mock.patch.object(smr, "parse_hook_input", return_value={}),
            mock.patch.object(smr, "log_hook_execution") as mock_log,
        ):
            with pytest.raises(SystemExit) as exc_info:
                smr.main()
            assert exc_info.value.code == 0

            # Should log refresh
            mock_log.assert_called_once()
            assert "Refreshed marker" in mock_log.call_args[0][2]

            # mtime should be updated
            new_mtime = marker_path.stat().st_mtime
            assert new_mtime > old_mtime

            captured = capsys.readouterr()
            output = json.loads(captured.out.strip())
            assert output == {"continue": True}

    def test_json_output_always(self, capsys):
        """Should always output valid JSON even on error."""
        with (
            mock.patch.object(smr, "parse_hook_input", return_value={}),
            mock.patch.object(smr, "get_effective_cwd", side_effect=Exception("Test error")),
            mock.patch.object(smr, "log_hook_execution"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                smr.main()
            assert exc_info.value.code == 0

            captured = capsys.readouterr()
            output = json.loads(captured.out.strip())
            assert output == {"continue": True}
