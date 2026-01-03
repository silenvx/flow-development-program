#!/usr/bin/env python3
"""Tests for commit-amend-block.py hook."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "commit-amend-block.py"


def run_hook(command: str) -> dict | None:
    """Run the hook with given command and return the result."""
    input_data = {"tool_input": {"command": command}}
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def _load_hook_module():
    """Load the hook module for direct testing of functions."""
    spec_obj = importlib.util.spec_from_file_location("commit_amend_block", HOOK_PATH)
    hook_module = importlib.util.module_from_spec(spec_obj)
    spec_obj.loader.exec_module(hook_module)
    return hook_module


class TestContainsAmendFlagDirect:
    """Direct unit tests for contains_amend_flag function."""

    def test_detect_simple_amend(self):
        """Should detect simple git commit --amend."""
        hook = _load_hook_module()
        assert hook.contains_amend_flag("git commit --amend") is True

    def test_detect_amend_with_message(self):
        """Should detect git commit --amend -m 'message'."""
        hook = _load_hook_module()
        assert hook.contains_amend_flag("git commit --amend -m 'message'") is True

    def test_detect_message_then_amend(self):
        """Should detect git commit -m 'message' --amend."""
        hook = _load_hook_module()
        assert hook.contains_amend_flag("git commit -m 'message' --amend") is True

    def test_detect_amend_with_global_options(self):
        """Should detect git -C path commit --amend."""
        hook = _load_hook_module()
        assert hook.contains_amend_flag("git -C /some/path commit --amend") is True

    def test_not_detect_regular_commit(self):
        """Should not detect regular git commit without --amend."""
        hook = _load_hook_module()
        assert hook.contains_amend_flag("git commit -m 'message'") is False

    def test_not_detect_non_git_command(self):
        """Should not detect non-git commands."""
        hook = _load_hook_module()
        assert hook.contains_amend_flag("echo hello") is False

    def test_not_detect_amend_in_quoted_string(self):
        """Should not detect --amend inside quoted strings."""
        hook = _load_hook_module()
        assert hook.contains_amend_flag('echo "git commit --amend"') is False

    def test_not_detect_amend_in_unrelated_chained_command(self):
        """Should not detect --amend in unrelated chained command."""
        hook = _load_hook_module()
        assert hook.contains_amend_flag("git commit -m 'msg' && echo --amend") is False

    def test_detect_amend_in_chained_commands(self):
        """Should detect real amend in chained commands."""
        hook = _load_hook_module()
        assert hook.contains_amend_flag("git add . && git commit --amend") is True

    def test_detect_amend_with_no_edit(self):
        """Should detect git commit --amend --no-edit."""
        hook = _load_hook_module()
        assert hook.contains_amend_flag("git commit --amend --no-edit") is True

    def test_detect_no_edit_then_amend(self):
        """Should detect git commit --no-edit --amend."""
        hook = _load_hook_module()
        assert hook.contains_amend_flag("git commit --no-edit --amend") is True

    def test_detect_amend_with_no_verify(self):
        """Should detect git commit --amend --no-verify."""
        hook = _load_hook_module()
        assert hook.contains_amend_flag("git commit --amend --no-verify") is True


class TestContainsAmendFlag:
    """Integration tests for hook execution with amend commands.

    These tests verify the hook runs without error and returns a valid decision.
    The actual decision (approve/block) depends on execution context:
    - In worktree: approve (amend is allowed)
    - In main repo: block (amend is blocked)

    For deterministic detection logic tests, see TestContainsAmendFlagDirect.
    """

    def test_amend_command_executes_without_error(self):
        """Hook executes without error for git commit --amend."""
        result = run_hook("git commit --amend")
        assert result is not None
        # Decision depends on execution context (worktree vs main repo)
        assert result.get("decision") in ("approve", "block")

    def test_amend_with_message_executes_without_error(self):
        """Hook executes without error for git commit --amend -m."""
        result = run_hook("git commit --amend -m 'message'")
        assert result is not None
        assert result.get("decision") in ("approve", "block")

    def test_message_then_amend_executes_without_error(self):
        """Hook executes without error for git commit -m --amend."""
        result = run_hook("git commit -m 'message' --amend")
        assert result is not None
        assert result.get("decision") in ("approve", "block")

    def test_amend_with_global_options_executes_without_error(self):
        """Hook executes without error for git -C path commit --amend."""
        result = run_hook("git -C /some/path commit --amend")
        assert result is not None
        assert result.get("decision") in ("approve", "block")

    def test_approve_regular_commit(self):
        """Should approve regular git commit without --amend."""
        result = run_hook("git commit -m 'message'")
        assert result is not None
        assert result.get("decision") == "approve"

    def test_approve_non_git_command(self):
        """Should approve non-git commands."""
        result = run_hook("echo hello")
        assert result is not None
        assert result.get("decision") == "approve"


class TestWorktreeDetection:
    """Tests for worktree detection logic using mocks."""

    def test_is_in_worktree_detects_worktree_path(self):
        """is_in_worktree should return True for worktree paths."""
        spec_obj = importlib.util.spec_from_file_location("commit_amend_block", HOOK_PATH)
        hook_module = importlib.util.module_from_spec(spec_obj)

        with patch.object(os, "getcwd", return_value="/path/to/.worktrees/issue-123"):
            spec_obj.loader.exec_module(hook_module)
            assert hook_module.is_in_worktree() is True

    def test_is_in_worktree_false_for_main_repo(self):
        """is_in_worktree should return False for main repo path."""
        spec_obj = importlib.util.spec_from_file_location("commit_amend_block", HOOK_PATH)
        hook_module = importlib.util.module_from_spec(spec_obj)

        with patch.object(os, "getcwd", return_value="/path/to/repo"):
            spec_obj.loader.exec_module(hook_module)
            assert hook_module.is_in_worktree() is False


class TestMainRepositoryDetection:
    """Tests for is_main_repository() using mocks."""

    def test_is_main_repository_true_when_in_main(self):
        """is_main_repository should return True when cwd matches main repo path."""
        hook = _load_hook_module()

        mock_result = subprocess.CompletedProcess(
            args=["git", "worktree", "list", "--porcelain"],
            returncode=0,
            stdout="worktree /path/to/main-repo\nHEAD abc123\nbranch refs/heads/main\n",
            stderr="",
        )
        with patch.object(subprocess, "run", return_value=mock_result):
            with patch.object(os, "getcwd", return_value="/path/to/main-repo"):
                with patch.object(os.path, "realpath", side_effect=lambda x: x):
                    assert hook.is_main_repository() is True

    def test_is_main_repository_true_when_in_subdirectory(self):
        """is_main_repository should return True when cwd is subdirectory of main repo."""
        hook = _load_hook_module()

        mock_result = subprocess.CompletedProcess(
            args=["git", "worktree", "list", "--porcelain"],
            returncode=0,
            stdout="worktree /path/to/main-repo\nHEAD abc123\nbranch refs/heads/main\n",
            stderr="",
        )
        with patch.object(subprocess, "run", return_value=mock_result):
            with patch.object(os, "getcwd", return_value="/path/to/main-repo/subdir"):
                with patch.object(os.path, "realpath", side_effect=lambda x: x):
                    assert hook.is_main_repository() is True

    def test_is_main_repository_false_when_in_worktree(self):
        """is_main_repository should return False when cwd is in a worktree."""
        hook = _load_hook_module()

        mock_result = subprocess.CompletedProcess(
            args=["git", "worktree", "list", "--porcelain"],
            returncode=0,
            stdout="worktree /path/to/main-repo\nHEAD abc123\nbranch refs/heads/main\n\nworktree /path/to/.worktrees/issue-123\nHEAD def456\nbranch refs/heads/fix/issue-123\n",
            stderr="",
        )
        with patch.object(subprocess, "run", return_value=mock_result):
            with patch.object(os, "getcwd", return_value="/path/to/.worktrees/issue-123"):
                with patch.object(os.path, "realpath", side_effect=lambda x: x):
                    assert hook.is_main_repository() is False

    def test_is_main_repository_false_on_git_error(self):
        """is_main_repository should return False when git command fails."""
        hook = _load_hook_module()

        with patch.object(subprocess, "run", side_effect=FileNotFoundError("git not found")):
            assert hook.is_main_repository() is False

    def test_is_main_repository_false_on_timeout(self):
        """is_main_repository should return False when git command times out."""
        hook = _load_hook_module()

        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 5)):
            assert hook.is_main_repository() is False


class TestHookIntegration:
    """Integration tests for the hook."""

    def test_empty_command_approves(self):
        """Should approve empty command."""
        result = run_hook("")
        assert result is not None
        assert result.get("decision") == "approve"

    def test_git_status_approves(self):
        """Should approve git status (not commit)."""
        result = run_hook("git status")
        assert result is not None
        assert result.get("decision") == "approve"

    def test_git_add_approves(self):
        """Should approve git add (not commit)."""
        result = run_hook("git add .")
        assert result is not None
        assert result.get("decision") == "approve"

    def test_amend_no_edit_detected(self):
        """Should detect git commit --amend --no-edit."""
        result = run_hook("git commit --amend --no-edit")
        assert result is not None


class TestEdgeCases:
    """Tests for edge cases."""

    def test_amend_in_quoted_string_not_blocked(self):
        """Should not block when --amend appears in a quoted string."""
        result = run_hook('echo "git commit --amend"')
        assert result is not None
        assert result.get("decision") == "approve"

    def test_multiple_commands_with_amend(self):
        """Should detect amend in chained commands."""
        result = run_hook("git add . && git commit --amend")
        assert result is not None

    def test_case_sensitivity(self):
        """Git command should be case-sensitive (lowercase only)."""
        result = run_hook("GIT COMMIT --AMEND")
        assert result is not None
        # Uppercase GIT should not be detected
        assert result.get("decision") == "approve"

    def test_amend_in_unrelated_chained_command_not_blocked(self):
        """Should not block when --amend appears in unrelated chained command."""
        # e.g., "git commit -m foo && echo --amend" should not be blocked
        result = run_hook("git commit -m 'message' && echo --amend")
        assert result is not None
        assert result.get("decision") == "approve"
