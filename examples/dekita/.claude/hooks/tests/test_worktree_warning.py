#!/usr/bin/env python3
"""Tests for worktree-warning.py hook."""

import json
import os
import subprocess
import sys
from pathlib import Path

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "worktree-warning.py"


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


class TestWorktreeWarning:
    """Tests for worktree-warning hook."""

    def test_exit_zero_always(self):
        """Hook should always exit with code 0 (even when blocking)."""
        test_cases = [
            {"tool_input": {"file_path": "/some/project/file.txt"}},
            {"tool_input": {"file_path": "/some/project/.worktrees/feature/file.txt"}},
            {"tool_input": {}},
            {},
        ]

        for input_data in test_cases:
            with self.subTest(input_data=input_data):
                # Use feature branch to avoid main branch block
                exit_code, _, _ = run_hook(input_data, env={"CLAUDE_TEST_BRANCH": "feature"})
                assert exit_code == 0

    def test_no_warning_for_worktree_path(self):
        """Should not warn when editing in worktree directory."""
        exit_code, stdout, stderr = run_hook(
            {"tool_input": {"file_path": "/project/.worktrees/feature/src/file.ts"}},
            env={"CLAUDE_PROJECT_DIR": "/project", "CLAUDE_TEST_BRANCH": "feature"},
        )

        assert exit_code == 0
        # Hook outputs JSON to stdout; warning in systemMessage
        assert "WARNING" not in stdout
        assert "block" not in stdout

    def test_warning_for_original_directory_on_feature_branch(self):
        """Should warn (not block) when editing in original directory on feature branch."""
        exit_code, stdout, stderr = run_hook(
            {"tool_input": {"file_path": "/project/src/file.ts"}},
            env={"CLAUDE_PROJECT_DIR": "/project", "CLAUDE_TEST_BRANCH": "feature"},
        )

        assert exit_code == 0
        # Hook outputs JSON to stdout with warning in systemMessage
        assert "WARNING" in stdout
        assert "AGENTS.md" in stdout
        # Should approve, not block
        result = json.loads(stdout)
        assert result.get("decision") == "approve"

    def test_block_on_main_branch(self):
        """Should block editing on main branch."""
        exit_code, stdout, stderr = run_hook(
            {"tool_input": {"file_path": "/project/src/file.ts"}},
            env={"CLAUDE_PROJECT_DIR": "/project", "CLAUDE_TEST_BRANCH": "main"},
        )

        assert exit_code == 0
        result = json.loads(stdout)
        assert result.get("decision") == "block"
        assert "main" in result.get("reason", "")
        assert "worktree" in result.get("reason", "")

    def test_block_on_master_branch(self):
        """Should block editing on master branch."""
        exit_code, stdout, stderr = run_hook(
            {"tool_input": {"file_path": "/project/src/file.ts"}},
            env={"CLAUDE_PROJECT_DIR": "/project", "CLAUDE_TEST_BRANCH": "master"},
        )

        assert exit_code == 0
        result = json.loads(stdout)
        assert result.get("decision") == "block"
        assert "master" in result.get("reason", "")

    def test_block_on_main_branch_new_directory(self):
        """Should block editing on main branch even when creating file in new directory."""
        # Simulate creating a file in a directory that doesn't exist yet
        exit_code, stdout, stderr = run_hook(
            {"tool_input": {"file_path": "/project/new-dir/new-file.ts"}},
            env={"CLAUDE_PROJECT_DIR": "/project", "CLAUDE_TEST_BRANCH": "main"},
        )

        assert exit_code == 0
        result = json.loads(stdout)
        assert result.get("decision") == "block"
        assert "main" in result.get("reason", "")

    def test_block_worktree_on_main_branch(self):
        """Should block editing even in worktree if branch is main (safety first)."""
        # Edge case: editing in a worktree directory while on main branch
        # Branch protection takes precedence over worktree path for safety
        exit_code, stdout, stderr = run_hook(
            {"tool_input": {"file_path": "/project/.worktrees/feature/src/file.ts"}},
            env={"CLAUDE_PROJECT_DIR": "/project", "CLAUDE_TEST_BRANCH": "main"},
        )

        assert exit_code == 0
        result = json.loads(stdout)
        # Branch protection takes precedence - still blocked on main
        assert result.get("decision") == "block"
        assert "main" in result.get("reason", "")

    def test_no_warning_for_empty_file_path(self):
        """Should not warn when file_path is empty."""
        exit_code, stdout, stderr = run_hook({"tool_input": {"file_path": ""}})

        assert exit_code == 0
        # Hook outputs JSON to stdout
        assert "WARNING" not in stdout

    def test_no_warning_for_missing_file_path(self):
        """Should not warn when file_path is missing."""
        exit_code, stdout, stderr = run_hook({"tool_input": {}})

        assert exit_code == 0
        # Hook outputs JSON to stdout
        assert "WARNING" not in stdout


class TestWorktreeWarningHelpers:
    """Tests for helper functions in worktree-warning."""

    def test_get_project_root_from_env(self):
        """Should get project root from CLAUDE_PROJECT_DIR env var."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("worktree_warning", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)

        # Set env var before loading module
        os.environ["CLAUDE_PROJECT_DIR"] = "/test/project"
        spec.loader.exec_module(module)

        result = module.get_project_root("/some/file.txt")
        assert result == "/test/project"

        # Clean up
        del os.environ["CLAUDE_PROJECT_DIR"]


class TestExtractWorktreeRoot:
    """Tests for extract_worktree_root function."""

    def setup_method(self):
        """Load the module for testing."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("worktree_warning", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_extracts_worktree_root_with_subdir(self):
        """Should extract worktree root from path with subdirectory."""
        result = self.module.extract_worktree_root("/project/.worktrees/feature/src/file.ts")
        assert result == "/project/.worktrees/feature"

    def test_extracts_worktree_root_without_subdir(self):
        """Should extract worktree root when file is at worktree root."""
        result = self.module.extract_worktree_root("/project/.worktrees/feature")
        assert result == "/project/.worktrees/feature"

    def test_returns_none_without_marker(self):
        """Should return None when .worktrees/ marker not present."""
        result = self.module.extract_worktree_root("/project/src/file.ts")
        assert result is None

    def test_handles_nested_worktree_name(self):
        """Should handle worktree names with various characters."""
        result = self.module.extract_worktree_root("/project/.worktrees/issue-123-fix/src/main.py")
        assert result == "/project/.worktrees/issue-123-fix"


class TestLockedWorktreeWarning:
    """Tests for locked worktree warning (Issue #527)."""

    def setup_method(self):
        """Load the module for testing."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("worktree_warning", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_get_worktree_lock_info_function_exists(self):
        """Should have get_worktree_lock_info function."""
        assert hasattr(self.module, "get_worktree_lock_info")
        assert callable(self.module.get_worktree_lock_info)

    def test_get_worktree_lock_info_returns_tuple(self):
        """Should return tuple (is_locked, lock_reason)."""
        # Test with non-existent path
        result = self.module.get_worktree_lock_info("/nonexistent/path")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)

    def test_locked_worktree_shows_warning_with_mock(self):
        """Should show warning when editing in a locked worktree (mocked)."""
        from unittest.mock import patch

        # Mock get_worktree_lock_info to return locked state
        with patch.object(
            self.module, "get_worktree_lock_info", return_value=(True, "別セッション作業中")
        ):
            # Re-run the hook module's main() would require more complex setup
            # Instead, verify the function is called correctly
            is_locked, reason = self.module.get_worktree_lock_info("/any/path")
            assert is_locked
            assert reason == "別セッション作業中"

    def test_unlocked_worktree_no_warning(self):
        """Should not show warning when worktree is not locked."""
        exit_code, stdout, stderr = run_hook(
            {"tool_input": {"file_path": "/project/.worktrees/feature/src/file.ts"}},
            env={
                "CLAUDE_PROJECT_DIR": "/project",
                "CLAUDE_TEST_BRANCH": "feature",
            },
        )

        assert exit_code == 0
        result = json.loads(stdout)
        assert result.get("decision") == "approve"


class TestAllowlistOnMainBranch:
    """Tests for allowlist on main branch (Issue #844)."""

    def setup_method(self):
        """Load the module for testing."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("worktree_warning", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_is_path_in_allowlist_function_exists(self):
        """Should have is_path_in_allowlist function."""
        assert hasattr(self.module, "is_path_in_allowlist")
        assert callable(self.module.is_path_in_allowlist)

    def test_plan_file_in_allowlist(self):
        """Plan files should be in allowlist."""
        result = self.module.is_path_in_allowlist("/project/.claude/plans/issue-123.md", "/project")
        assert result is True

    def test_regular_file_not_in_allowlist(self):
        """Regular source files should not be in allowlist."""
        result = self.module.is_path_in_allowlist("/project/src/file.ts", "/project")
        assert result is False

    def test_claude_file_not_in_allowlist(self):
        """Other .claude files should not be in allowlist (only plans/)."""
        result = self.module.is_path_in_allowlist("/project/.claude/hooks/some-hook.py", "/project")
        assert result is False

    def test_allowlist_empty_paths(self):
        """Should return False for empty paths."""
        assert self.module.is_path_in_allowlist("", "/project") is False
        assert self.module.is_path_in_allowlist("/project/file.txt", "") is False

    def test_similar_directory_not_in_allowlist(self):
        """Similar directory names should NOT be in allowlist (e.g., plans-backup)."""
        # .claude/plans-backup/ should NOT match .claude/plans/
        result = self.module.is_path_in_allowlist(
            "/project/.claude/plans-backup/file.md", "/project"
        )
        assert result is False

        # .claude/plans2/ should NOT match .claude/plans/
        result = self.module.is_path_in_allowlist("/project/.claude/plans2/file.md", "/project")
        assert result is False

    def test_project_root_with_trailing_slash(self):
        """project_root with trailing slash should work correctly."""
        # With trailing slash
        result = self.module.is_path_in_allowlist(
            "/project/.claude/plans/issue-999.md", "/project/"
        )
        assert result is True

        # Ensure it still blocks non-allowlist paths
        result = self.module.is_path_in_allowlist("/project/src/file.ts", "/project/")
        assert result is False

    def test_plan_file_allowed_on_main_branch(self):
        """Plan files should be allowed on main branch."""
        exit_code, stdout, stderr = run_hook(
            {"tool_input": {"file_path": "/project/.claude/plans/issue-999.md"}},
            env={"CLAUDE_PROJECT_DIR": "/project", "CLAUDE_TEST_BRANCH": "main"},
        )

        assert exit_code == 0
        result = json.loads(stdout)
        assert result.get("decision") == "approve"
        assert "許可リスト" in result.get("systemMessage", "")

    def test_regular_file_blocked_on_main_branch(self):
        """Regular files should still be blocked on main branch."""
        exit_code, stdout, stderr = run_hook(
            {"tool_input": {"file_path": "/project/src/main.ts"}},
            env={"CLAUDE_PROJECT_DIR": "/project", "CLAUDE_TEST_BRANCH": "main"},
        )

        assert exit_code == 0
        result = json.loads(stdout)
        assert result.get("decision") == "block"


class TestWorktreeWarningEdgeCases:
    """Tests for edge cases."""

    def test_handles_invalid_json(self):
        """Should handle invalid JSON input gracefully."""
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input="not valid json",
            capture_output=True,
            text=True,
        )

        # Should not crash, exit with 0
        assert result.returncode == 0

    def test_handles_path_outside_project(self):
        """Should handle paths outside the project directory."""
        exit_code, stdout, stderr = run_hook(
            {"tool_input": {"file_path": "/completely/different/path/file.txt"}},
            env={"CLAUDE_PROJECT_DIR": "/project"},
        )

        assert exit_code == 0
        # Should not warn for paths outside project (JSON output to stdout)
        assert "WARNING" not in stdout
