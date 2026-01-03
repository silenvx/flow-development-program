#!/usr/bin/env python3
"""Tests for worktree-creation-marker.py hook."""

import json
import os
import subprocess
import sys
from pathlib import Path

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "worktree-creation-marker.py"


def run_hook(input_data: dict, env: dict = None) -> tuple[int, str, str]:
    """Run the hook with given input and return (exit_code, stdout, stderr)."""
    process_env = os.environ.copy()
    if env:
        process_env.update(env)

    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        env=process_env,
    )
    return result.returncode, result.stdout, result.stderr


class TestWorktreeCreationMarker:
    """Tests for worktree-creation-marker hook."""

    def test_exit_zero_always(self):
        """Hook should always exit with code 0."""
        test_cases = [
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git worktree add .worktrees/test -b test main"},
            },
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            {"tool_name": "Edit", "tool_input": {"file_path": "/some/file.txt"}},
            {},
        ]

        for input_data in test_cases:
            exit_code, _, _ = run_hook(input_data)
            assert exit_code == 0

    def test_skip_non_bash_tools(self):
        """Should skip non-Bash tools."""
        exit_code, _, _ = run_hook({"tool_name": "Edit", "tool_input": {"file_path": "/file.txt"}})
        assert exit_code == 0

    def test_skip_non_worktree_commands(self):
        """Should skip non-worktree add commands."""
        exit_code, _, _ = run_hook({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        assert exit_code == 0

    def test_skip_failed_worktree_add(self):
        """Should skip when worktree add failed."""
        exit_code, _, _ = run_hook(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git worktree add .worktrees/test main"},
                "tool_result": {"exit_code": 1},
            }
        )
        assert exit_code == 0

    def test_write_marker_on_success(self, tmp_path):
        """Should write session marker when worktree add succeeds."""
        # Create a fake worktree directory
        worktree = tmp_path / ".worktrees" / "issue-123"
        worktree.mkdir(parents=True)

        exit_code, stdout, _ = run_hook(
            {
                "tool_name": "Bash",
                "tool_input": {"command": f"git worktree add {worktree} main"},
                "tool_result": {"exit_code": 0},
                "session_id": "test-session-xyz",
            },
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert exit_code == 0
        # Check marker was created in JSON format
        marker = worktree / ".claude-session"
        assert marker.exists()
        marker_data = json.loads(marker.read_text())
        assert marker_data["session_id"] == "test-session-xyz"
        assert "created_at" in marker_data  # Timestamp should be present

    def test_write_marker_when_exit_code_missing(self, tmp_path):
        """Should write marker when exit_code is not provided (Issue #1461).

        When tool_result doesn't include exit_code, the hook should assume
        success (exit_code=0) and create the marker, not skip with exit_code=-1.
        """
        # Create a fake worktree directory
        worktree = tmp_path / ".worktrees" / "issue-1461"
        worktree.mkdir(parents=True)

        exit_code, stdout, _ = run_hook(
            {
                "tool_name": "Bash",
                "tool_input": {"command": f"git worktree add {worktree} main"},
                "tool_result": {},  # No exit_code - this was causing the bug
                "session_id": "test-session-missing-exitcode",
            },
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert exit_code == 0
        # Marker should be created in JSON format (this was failing before the fix)
        marker = worktree / ".claude-session"
        assert marker.exists(), "Marker should be created even when exit_code is missing"
        marker_data = json.loads(marker.read_text())
        assert marker_data["session_id"] == "test-session-missing-exitcode"
        assert "created_at" in marker_data  # Timestamp should be present


class TestExtractWorktreePath:
    """Tests for extract_worktree_path function."""

    def test_extract_simple_path(self):
        """Should extract worktree path from simple command."""
        from conftest import load_hook_module

        hook = load_hook_module("worktree-creation-marker")

        result = hook.extract_worktree_add_path("git worktree add .worktrees/issue-123 main")
        assert result == ".worktrees/issue-123"

    def test_extract_with_branch_flag(self):
        """Should extract worktree path with -b flag."""
        from conftest import load_hook_module

        hook = load_hook_module("worktree-creation-marker")

        result = hook.extract_worktree_add_path(
            "git worktree add .worktrees/issue-456 -b feat/test main"
        )
        assert result == ".worktrees/issue-456"

    def test_extract_with_env_prefix(self):
        """Should extract worktree path with SKIP_PLAN prefix."""
        from conftest import load_hook_module

        hook = load_hook_module("worktree-creation-marker")

        result = hook.extract_worktree_add_path(
            "SKIP_PLAN=1 git worktree add .worktrees/issue-789 main"
        )
        assert result == ".worktrees/issue-789"

    def test_no_match_non_worktree(self):
        """Should return None for non-worktree commands."""
        from conftest import load_hook_module

        hook = load_hook_module("worktree-creation-marker")

        result = hook.extract_worktree_add_path("git status")
        assert result is None


class TestWriteSessionMarker:
    """Tests for write_session_marker function."""

    TEST_SESSION_ID = "test-session-123"

    def test_writes_json_format(self, tmp_path):
        """Should write marker in JSON format with session_id and timestamp."""

        from conftest import load_hook_module
        from lib.session import create_hook_context

        hook = load_hook_module("worktree-creation-marker")

        worktree = tmp_path / "worktree"
        worktree.mkdir()

        ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
        result = hook.write_session_marker(ctx, worktree)

        assert result is True
        marker = worktree / ".claude-session"
        assert marker.exists()
        data = json.loads(marker.read_text())
        assert data["session_id"] == self.TEST_SESSION_ID
        assert "created_at" in data
        # Timestamp should be in ISO format
        assert "T" in data["created_at"]

    def test_atomic_write_uses_correct_temp_name(self, tmp_path):
        """Should use .claude-session.tmp as temp file (not .tmp)."""
        from unittest.mock import patch

        from conftest import load_hook_module
        from lib.session import create_hook_context

        hook = load_hook_module("worktree-creation-marker")

        worktree = tmp_path / "worktree"
        worktree.mkdir()

        # Track what files were created during write
        created_files = []
        original_write_text = Path.write_text

        def mock_write_text(self, content):
            created_files.append(str(self))
            return original_write_text(self, content)

        ctx = create_hook_context({"session_id": "test-session"})
        with patch.object(Path, "write_text", mock_write_text):
            hook.write_session_marker(ctx, worktree)

        # Temp file should be .claude-session.tmp, not just .tmp
        assert any(".claude-session.tmp" in f for f in created_files)

    def test_returns_false_on_error(self, tmp_path):
        """Should return False when write fails."""
        from conftest import load_hook_module
        from lib.session import create_hook_context

        hook = load_hook_module("worktree-creation-marker")

        # Non-existent directory should cause failure
        non_existent = tmp_path / "does-not-exist"
        ctx = create_hook_context({"session_id": "test-session"})
        result = hook.write_session_marker(ctx, non_existent)
        assert result is False
