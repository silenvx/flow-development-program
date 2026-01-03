#!/usr/bin/env python3
"""Tests for hook-dev-warning.py hook."""

# Import the module functions for unit testing
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

hooks_dir = Path(__file__).parent.parent
hook_file = hooks_dir / "hook-dev-warning.py"
spec = importlib.util.spec_from_file_location("hook_dev_warning", hook_file)
hook_dev_warning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook_dev_warning)

is_in_worktree = hook_dev_warning.is_in_worktree
get_worktree_root = hook_dev_warning.get_worktree_root
get_modified_hook_files = hook_dev_warning.get_modified_hook_files


class TestIsInWorktree:
    """Tests for is_in_worktree function."""

    def test_detects_worktree_path(self):
        """Should detect path containing /.worktrees/."""
        with patch("os.getcwd", return_value="/repo/.worktrees/issue-123/src"):
            assert is_in_worktree() is True

    def test_detects_worktree_root(self):
        """Should detect worktree root path."""
        with patch("os.getcwd", return_value="/repo/.worktrees/feature-x"):
            assert is_in_worktree() is True

    def test_rejects_main_repo(self):
        """Should reject main repository path."""
        with patch("os.getcwd", return_value="/repo/src"):
            assert is_in_worktree() is False

    def test_rejects_similar_path(self):
        """Should reject path with worktrees but not /.worktrees/."""
        with patch("os.getcwd", return_value="/home/user/worktrees/project"):
            assert is_in_worktree() is False

    def test_rejects_worktrees_parent_directory(self):
        """Should reject /.worktrees directory itself (parent of worktrees)."""
        with patch("os.getcwd", return_value="/repo/.worktrees"):
            assert is_in_worktree() is False


class TestGetWorktreeRoot:
    """Tests for get_worktree_root function."""

    def test_extracts_worktree_root_from_subdir(self):
        """Should extract worktree root from subdirectory."""
        with patch("os.getcwd", return_value="/repo/.worktrees/issue-456/src/components"):
            result = get_worktree_root()
            assert result == "/repo/.worktrees/issue-456"

    def test_extracts_worktree_root_at_root(self):
        """Should return worktree root when at root."""
        with patch("os.getcwd", return_value="/repo/.worktrees/feature-abc"):
            result = get_worktree_root()
            assert result == "/repo/.worktrees/feature-abc"

    def test_returns_none_for_main_repo(self):
        """Should return None for main repository."""
        with patch("os.getcwd", return_value="/repo/src"):
            result = get_worktree_root()
            assert result is None


class TestGetModifiedHookFiles:
    """Tests for get_modified_hook_files function."""

    def test_returns_empty_for_no_changes(self):
        """Should return empty list when no hook files are modified."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            result = get_modified_hook_files("/some/worktree")
            assert result == []

    def test_extracts_modified_files(self):
        """Should extract modified file paths from git status."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            # git status --porcelain format: XY filename (3 chars prefix)
            mock_run.return_value.stdout = (
                " M .claude/hooks/test-hook.py\n?? .claude/hooks/new-hook.py\n"
            )
            result = get_modified_hook_files("/some/worktree")
            # First 3 chars are stripped, so " M " becomes file path starting at position 3
            assert any("test-hook.py" in f for f in result)
            assert any("new-hook.py" in f for f in result)

    def test_handles_git_error(self):
        """Should return empty list on git error."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            result = get_modified_hook_files("/some/worktree")
            assert result == []


class TestHookIntegration:
    """Integration tests for the hook."""

    def test_outputs_valid_json_in_main_repo(self):
        """Should output valid JSON with continue=True in main repo."""
        # Test the logic directly with mocked functions
        with patch.object(hook_dev_warning, "is_in_worktree", return_value=False):
            import io
            import sys

            captured_output = io.StringIO()
            sys.stdout = captured_output
            try:
                hook_dev_warning.main()
            finally:
                sys.stdout = sys.__stdout__

            output = json.loads(captured_output.getvalue())
            assert output["continue"] is True
            assert "message" not in output

    def test_outputs_warning_in_worktree_with_changes(self):
        """Should output warning message when in worktree with hook changes."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/main/repo"}):
            with patch("os.getcwd", return_value="/main/repo/.worktrees/test-issue"):
                with patch.object(
                    hook_dev_warning,
                    "get_modified_hook_files",
                    return_value=[".claude/hooks/test.py"],
                ):
                    # Re-run main to get the output
                    import io
                    import sys

                    captured_output = io.StringIO()
                    sys.stdout = captured_output
                    try:
                        hook_dev_warning.main()
                    finally:
                        sys.stdout = sys.__stdout__

                    output = json.loads(captured_output.getvalue())
                    assert output["continue"] is True
                    assert "message" in output
                    assert "Worktree内でフック" in output["message"]
                    assert "対処法" in output["message"]
