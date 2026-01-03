#!/usr/bin/env python3
"""Unit tests for orphan-worktree-check.py"""

import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

import common

# Import with dynamic loading due to hyphens in filename
HOOK_PATH = Path(__file__).parent.parent / "orphan-worktree-check.py"
_spec = importlib.util.spec_from_file_location("orphan_worktree_check", HOOK_PATH)
orphan_worktree_check = importlib.util.module_from_spec(_spec)
sys.modules["orphan_worktree_check"] = orphan_worktree_check
_spec.loader.exec_module(orphan_worktree_check)


class TestGetRepoRoot:
    """Tests for get_repo_root function."""

    def test_main_repo_with_git_dir(self):
        """Should return project_dir when .git is a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()

            result = orphan_worktree_check.get_repo_root(project_dir)
            assert result == project_dir

    def test_no_git_returns_none(self):
        """Should return None when no .git exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            result = orphan_worktree_check.get_repo_root(project_dir)
            assert result is None

    def test_worktree_with_gitdir_file(self):
        """Should resolve to main repo root when in a worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create main repo structure
            main_repo = Path(tmpdir) / "main-repo"
            main_repo.mkdir()
            git_dir = main_repo / ".git"
            git_dir.mkdir()
            git_worktrees = git_dir / "worktrees" / "my-worktree"
            git_worktrees.mkdir(parents=True)

            # Create worktree directory with .git file
            worktree_dir = Path(tmpdir) / "worktree"
            worktree_dir.mkdir()
            git_file = worktree_dir / ".git"
            git_file.write_text(f"gitdir: {git_worktrees}")

            result = orphan_worktree_check.get_repo_root(worktree_dir)
            assert result == main_repo


class TestFindOrphanWorktrees:
    """Tests for find_orphan_worktrees function."""

    def test_no_worktrees_dir(self):
        """Should return empty list when .worktrees/ doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()

            result = orphan_worktree_check.find_orphan_worktrees(project_dir)
            assert result == []

    def test_no_orphans(self):
        """Should return empty list when all worktrees are registered."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()

            # Create .worktrees/foo directory
            worktrees_dir = project_dir / ".worktrees"
            (worktrees_dir / "foo").mkdir(parents=True)

            # Create corresponding .git/worktrees/foo entry
            git_worktrees = git_dir / "worktrees" / "foo"
            git_worktrees.mkdir(parents=True)

            result = orphan_worktree_check.find_orphan_worktrees(project_dir)
            assert result == []

    def test_find_orphan(self):
        """Should detect orphan worktree directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()

            # Create .worktrees/orphan directory (no git entry)
            worktrees_dir = project_dir / ".worktrees"
            (worktrees_dir / "orphan").mkdir(parents=True)

            # Create .git/worktrees/ but not for "orphan"
            git_worktrees = git_dir / "worktrees"
            git_worktrees.mkdir()

            result = orphan_worktree_check.find_orphan_worktrees(project_dir)
            assert result == [(".worktrees", "orphan")]

    def test_multiple_orphans(self):
        """Should detect multiple orphan worktree directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()

            # Create worktrees directory with multiple entries
            worktrees_dir = project_dir / ".worktrees"
            (worktrees_dir / "orphan1").mkdir(parents=True)
            (worktrees_dir / "orphan2").mkdir(parents=True)
            (worktrees_dir / "valid").mkdir(parents=True)

            # Only register "valid" in git
            git_worktrees = git_dir / "worktrees"
            (git_worktrees / "valid").mkdir(parents=True)

            result = orphan_worktree_check.find_orphan_worktrees(project_dir)
            # Sort by name for consistent ordering
            assert sorted(result, key=lambda x: x[1]) == [
                (".worktrees", "orphan1"),
                (".worktrees", "orphan2"),
            ]

    def test_no_git_worktrees_dir(self):
        """Should treat all .worktrees/ as orphans when .git/worktrees/ doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()

            # Create .worktrees/ without .git/worktrees/
            worktrees_dir = project_dir / ".worktrees"
            (worktrees_dir / "foo").mkdir(parents=True)

            result = orphan_worktree_check.find_orphan_worktrees(project_dir)
            assert result == [(".worktrees", "foo")]


# Note: TestCheckAndUpdateSessionMarker class has been removed as it tested
# hook-specific session marker functions that are now consolidated in common.py.
# See test_common.py TestCheckAndUpdateSessionMarker for session marker tests.


class TestMain:
    """Integration tests for main() function."""

    def setup_method(self):
        """Create temporary directory for session markers."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        # Patch SESSION_DIR in common module (where check_and_update_session_marker uses it)
        self.session_dir_patcher = patch.object(common, "SESSION_DIR", self.temp_path)
        self.session_dir_patcher.start()

    def teardown_method(self):
        """Clean up temporary directory."""
        self.session_dir_patcher.stop()
        self.temp_dir.cleanup()

    def _run_main(self, project_dir: str = "") -> dict:
        """Helper to run main() and capture output."""
        captured_output = io.StringIO()

        with (
            patch("sys.stdout", captured_output),
            patch("orphan_worktree_check.log_hook_execution"),
            patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": project_dir}),
            pytest.raises(SystemExit) as ctx,
        ):
            orphan_worktree_check.main()

        assert ctx.value.code == 0
        return json.loads(captured_output.getvalue())

    def test_returns_approve_decision(self):
        """Should return approve decision."""
        result = self._run_main()
        assert result["decision"] == "approve"

    def test_outputs_valid_json(self):
        """Should output valid JSON."""
        captured_output = io.StringIO()

        with (
            patch("sys.stdout", captured_output),
            patch("orphan_worktree_check.log_hook_execution"),
            patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": ""}),
            pytest.raises(SystemExit),
        ):
            orphan_worktree_check.main()

        output = captured_output.getvalue()
        # Should be valid JSON
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_includes_system_message_when_orphans_exist(self):
        """Should include systemMessage when there are orphan worktrees."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()

            # Create orphan worktree
            worktrees_dir = project_dir / ".worktrees"
            (worktrees_dir / "orphan").mkdir(parents=True)
            (git_dir / "worktrees").mkdir()

            captured_output = io.StringIO()
            with (
                patch("sys.stdout", captured_output),
                patch("orphan_worktree_check.log_hook_execution"),
                patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(project_dir)}),
                pytest.raises(SystemExit),
            ):
                orphan_worktree_check.main()

            result = json.loads(captured_output.getvalue())
            assert "systemMessage" in result
            assert "orphan" in result["systemMessage"]

    def test_no_system_message_when_no_orphans(self):
        """Should not include systemMessage when there are no orphans."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()

            # No .worktrees directory = no orphans
            result = self._run_main(str(project_dir))
            assert "systemMessage" not in result

    def test_no_system_message_on_same_session(self):
        """Should not include systemMessage within same session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()

            # Create orphan worktree
            worktrees_dir = project_dir / ".worktrees"
            (worktrees_dir / "orphan").mkdir(parents=True)
            (git_dir / "worktrees").mkdir()

            # First call - new session
            captured_output1 = io.StringIO()
            with (
                patch("sys.stdout", captured_output1),
                patch("orphan_worktree_check.log_hook_execution"),
                patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(project_dir)}),
                pytest.raises(SystemExit),
            ):
                orphan_worktree_check.main()
            result1 = json.loads(captured_output1.getvalue())
            assert "systemMessage" in result1

            # Second call - same session
            captured_output2 = io.StringIO()
            with (
                patch("sys.stdout", captured_output2),
                patch("orphan_worktree_check.log_hook_execution"),
                patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(project_dir)}),
                pytest.raises(SystemExit),
            ):
                orphan_worktree_check.main()
            result2 = json.loads(captured_output2.getvalue())
            assert "systemMessage" not in result2

    def test_no_system_message_when_no_project_dir(self):
        """Should not include systemMessage when CLAUDE_PROJECT_DIR is not set."""
        result = self._run_main("")
        assert "systemMessage" not in result

    def test_handles_exceptions_gracefully(self):
        """Should not block on errors."""
        captured_output = io.StringIO()

        with (
            patch("sys.stdout", captured_output),
            patch("orphan_worktree_check.log_hook_execution"),
            patch.object(
                orphan_worktree_check,
                "check_and_update_session_marker",
                side_effect=Exception("Test error"),
            ),
            pytest.raises(SystemExit),
        ):
            orphan_worktree_check.main()

        result = json.loads(captured_output.getvalue())
        # Should still return approve
        assert result["decision"] == "approve"
