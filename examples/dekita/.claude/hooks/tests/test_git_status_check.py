#!/usr/bin/env python3
"""Tests for git-status-check.py hook."""

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HOOK_PATH = Path(__file__).parent.parent / "git-status-check.py"


def run_hook(input_data: dict, cwd: str = None) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return json.loads(result.stdout)


def load_hook_module():
    """Load the hook module for testing helper functions."""
    spec = importlib.util.spec_from_file_location("git_status_check", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestGitStatusCheck:
    """Tests for git-status-check hook."""

    def test_approve_when_stop_hook_active(self):
        """Should approve immediately when stop_hook_active is True."""
        result = run_hook({"transcript_path": "/some/path", "stop_hook_active": True})

        assert result["decision"] == "approve"
        assert "stop_hook_active" in result["reason"]

    def test_approve_in_git_repo(self):
        """Should approve when running in a git repository."""
        # Run in the current repo's worktree directory
        result = run_hook(
            {"transcript_path": "/some/path"},
            cwd=str(Path(__file__).parent.parent.parent.parent),
        )

        assert result["decision"] == "approve"
        # Should have a branch name or clean status
        assert (
            "branch" in result["reason"].lower()
            or "clean" in result["reason"].lower()
            or "uncommitted" in result["reason"].lower()
        )


class TestGitStatusCheckHelpers:
    """Tests for helper functions in git-status-check."""

    def test_get_git_status_returns_tuple(self):
        """Test that get_git_status returns a tuple."""
        module = load_hook_module()

        result = module.get_git_status()

        assert isinstance(result, tuple)
        assert len(result) == 3
        is_clean, branch, status_output = result
        assert isinstance(is_clean, bool)
        assert isinstance(branch, str)
        assert isinstance(status_output, str)


def run_main_with_mocked_git(module, input_data: dict, git_status: tuple) -> dict:
    """Run the main function with mocked git status and capture output."""
    stdin_mock = io.StringIO(json.dumps(input_data))
    stdout_mock = io.StringIO()

    with (
        patch.object(module, "get_git_status", return_value=git_status),
        patch.object(sys, "stdin", stdin_mock),
        patch.object(sys, "stdout", stdout_mock),
    ):
        try:
            module.main()
        except SystemExit:
            pass  # main() calls sys.exit(0) at the end

    stdout_mock.seek(0)
    return json.loads(stdout_mock.read())


class TestGitStatusCheckWithMockedGit:
    """Tests with mocked git commands."""

    def test_clean_status_on_main(self):
        """Should approve with success message when git status is clean on main."""
        module = load_hook_module()

        result = run_main_with_mocked_git(
            module,
            {"transcript_path": "/some/path"},
            (True, "main", ""),
        )

        assert result["decision"] == "approve"
        assert "clean" in result["reason"].lower()
        assert "✅" in result["systemMessage"]
        assert "main" in result["systemMessage"]

    def test_dirty_status_on_main_warns(self):
        """Should approve with warning when uncommitted changes on main."""
        module = load_hook_module()

        result = run_main_with_mocked_git(
            module,
            {"transcript_path": "/some/path"},
            (False, "main", "M test.txt"),
        )

        assert result["decision"] == "approve"
        assert "uncommitted" in result["reason"].lower()
        assert "⚠️" in result["systemMessage"]
        assert "mainブランチに未コミット変更" in result["systemMessage"]

    def test_dirty_status_on_feature_branch_ok(self):
        """Should approve with info message when uncommitted changes on feature branch."""
        module = load_hook_module()

        result = run_main_with_mocked_git(
            module,
            {"transcript_path": "/some/path"},
            (False, "feature/test", "M test.txt"),
        )

        assert result["decision"] == "approve"
        assert "not main" in result["reason"].lower()
        assert "ℹ️" in result["systemMessage"]
        assert "feature/test" in result["systemMessage"]


class TestGitStatusCheckIntegration:
    """Integration tests that run in a real git environment."""

    def test_runs_in_current_repo(self):
        """Should run successfully in the current repository."""
        # The hook should work in the current repo
        result = run_hook(
            {"transcript_path": "/some/path"},
            cwd=str(Path(__file__).parent.parent.parent.parent),
        )

        assert result["decision"] == "approve"
        assert "systemMessage" in result

    def test_handles_non_git_directory(self):
        """Should handle running in a non-git directory gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_hook({"transcript_path": "/some/path"}, cwd=tmpdir)

            # Should still approve (not block) even if git commands fail
            assert result["decision"] == "approve"
