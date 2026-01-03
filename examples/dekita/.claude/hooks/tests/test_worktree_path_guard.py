#!/usr/bin/env python3
"""Tests for worktree-path-guard.py hook."""

import json
import subprocess
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "worktree-path-guard.py"


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestWorktreePathGuardBlocks:
    """Tests for commands that should be blocked."""

    def test_blocks_absolute_path(self):
        """Should block git worktree add with absolute path."""
        result = run_hook(
            {"tool_input": {"command": "git worktree add /tmp/my-worktree feature/branch"}}
        )
        assert result["decision"] == "block"
        assert ".worktrees/" in result["reason"]

    def test_blocks_relative_path_outside(self):
        """Should block git worktree add with relative path outside .worktrees/."""
        result = run_hook(
            {"tool_input": {"command": "git worktree add ../dekita-123 feature/branch"}}
        )
        assert result["decision"] == "block"

    def test_blocks_sibling_directory(self):
        """Should block git worktree add to sibling directory."""
        result = run_hook(
            {"tool_input": {"command": "git worktree add my-worktree feature/branch"}}
        )
        assert result["decision"] == "block"

    def test_blocks_with_branch_option(self):
        """Should block even with -b option."""
        result = run_hook({"tool_input": {"command": "git worktree add -b new-branch /tmp/foo"}})
        assert result["decision"] == "block"

    def test_blocks_with_lock_option(self):
        """Should block even with --lock option."""
        result = run_hook(
            {"tool_input": {"command": "git worktree add --lock ../foo feature/branch"}}
        )
        assert result["decision"] == "block"

    def test_blocks_home_directory(self):
        """Should block git worktree add to home directory."""
        result = run_hook(
            {"tool_input": {"command": "git worktree add ~/worktrees/foo feature/branch"}}
        )
        assert result["decision"] == "block"

    def test_blocks_path_traversal(self):
        """Should block path traversal attacks like .worktrees/../foo."""
        test_cases = [
            "git worktree add .worktrees/../foo feature/branch",
            "git worktree add .worktrees/../../../tmp/evil feature/branch",
            "git worktree add .worktrees/issue-123/../../../tmp feature/branch",
        ]
        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "block", f"Should block: {command}"

    def test_blocks_worktrees_only(self):
        """Should block .worktrees without subdirectory."""
        result = run_hook({"tool_input": {"command": "git worktree add .worktrees feature/branch"}})
        assert result["decision"] == "block"


class TestWorktreePathGuardAllows:
    """Tests for commands that should be allowed."""

    def test_allows_worktrees_directory(self):
        """Should allow git worktree add .worktrees/foo."""
        result = run_hook(
            {"tool_input": {"command": "git worktree add .worktrees/issue-123 feature/branch"}}
        )
        assert result["decision"] == "approve"

    def test_allows_worktrees_with_branch(self):
        """Should allow git worktree add -b branch .worktrees/foo."""
        result = run_hook(
            {"tool_input": {"command": "git worktree add -b feature/new .worktrees/issue-456"}}
        )
        assert result["decision"] == "approve"

    def test_allows_worktrees_with_lock(self):
        """Should allow git worktree add --lock .worktrees/foo."""
        result = run_hook(
            {
                "tool_input": {
                    "command": "git worktree add --lock .worktrees/issue-789 feature/branch"
                }
            }
        )
        assert result["decision"] == "approve"

    def test_allows_worktrees_combined_options(self):
        """Should allow with combined options."""
        result = run_hook(
            {
                "tool_input": {
                    "command": "git worktree add --lock -b feature/new .worktrees/issue-100"
                }
            }
        )
        assert result["decision"] == "approve"

    def test_allows_worktrees_with_orphan(self):
        """Should allow git worktree add --orphan .worktrees/foo."""
        result = run_hook(
            {
                "tool_input": {
                    "command": "git worktree add --orphan new-branch .worktrees/issue-orphan"
                }
            }
        )
        assert result["decision"] == "approve"

    def test_allows_worktree_list(self):
        """Should allow git worktree list."""
        result = run_hook({"tool_input": {"command": "git worktree list"}})
        assert result["decision"] == "approve"

    def test_allows_worktree_remove(self):
        """Should allow git worktree remove (different command)."""
        result = run_hook({"tool_input": {"command": "git worktree remove .worktrees/issue-123"}})
        assert result["decision"] == "approve"

    def test_allows_worktree_prune(self):
        """Should allow git worktree prune."""
        result = run_hook({"tool_input": {"command": "git worktree prune"}})
        assert result["decision"] == "approve"


class TestWorktreePathGuardEdgeCases:
    """Tests for edge cases."""

    def test_empty_command(self):
        """Should approve empty command."""
        result = run_hook({"tool_input": {"command": ""}})
        assert result["decision"] == "approve"

    def test_no_tool_input(self):
        """Should approve when tool_input is missing."""
        result = run_hook({})
        assert result["decision"] == "approve"

    def test_non_git_commands(self):
        """Should ignore non-git commands."""
        test_cases = ["ls -la", "npm run build", "python3 script.py"]
        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "approve"

    def test_other_git_commands(self):
        """Should ignore non-worktree git commands."""
        test_cases = ["git status", "git log", "git push", "git fetch"]
        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "approve"

    def test_quoted_commands(self):
        """Should ignore commands inside quotes."""
        test_cases = [
            "echo 'git worktree add /tmp/foo branch'",
            'echo "git worktree add ../bar branch"',
        ]
        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "approve"


class TestExtractWorktreeAddPath:
    """Tests for extract_worktree_add_path helper function."""

    def setup_method(self):
        """Load the module for testing helper functions."""
        import sys

        hooks_dir = Path(__file__).parent.parent
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        import importlib.util

        spec = importlib.util.spec_from_file_location("worktree_path_guard", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_extract_simple_path(self):
        """Should extract path from simple command."""
        path = self.module.extract_worktree_add_path("git worktree add .worktrees/foo branch")
        assert path == ".worktrees/foo"

    def test_extract_path_with_branch_option(self):
        """Should extract path when -b option is present."""
        path = self.module.extract_worktree_add_path(
            "git worktree add -b new-branch .worktrees/foo"
        )
        assert path == ".worktrees/foo"

    def test_extract_absolute_path(self):
        """Should extract absolute path."""
        path = self.module.extract_worktree_add_path("git worktree add /tmp/foo branch")
        assert path == "/tmp/foo"

    def test_returns_none_for_list(self):
        """Should return None for git worktree list."""
        path = self.module.extract_worktree_add_path("git worktree list")
        assert path is None

    def test_returns_none_for_remove(self):
        """Should return None for git worktree remove."""
        path = self.module.extract_worktree_add_path("git worktree remove .worktrees/foo")
        assert path is None


class TestIsValidWorktreePath:
    """Tests for is_valid_worktree_path helper function."""

    def setup_method(self):
        """Load the module for testing helper functions."""
        import sys

        hooks_dir = Path(__file__).parent.parent
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        import importlib.util

        spec = importlib.util.spec_from_file_location("worktree_path_guard", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_valid_worktrees_path(self):
        """Should return True for .worktrees/ paths."""
        assert self.module.is_valid_worktree_path(".worktrees/issue-123")
        assert self.module.is_valid_worktree_path(".worktrees/foo")
        assert self.module.is_valid_worktree_path(".worktrees/bar/baz")

    def test_invalid_absolute_path(self):
        """Should return False for absolute paths."""
        assert not self.module.is_valid_worktree_path("/tmp/foo")
        assert not self.module.is_valid_worktree_path("/home/user/worktrees/foo")

    def test_invalid_relative_path(self):
        """Should return False for relative paths outside .worktrees/."""
        assert not self.module.is_valid_worktree_path("../foo")
        assert not self.module.is_valid_worktree_path("foo")
        assert not self.module.is_valid_worktree_path("worktrees/foo")

    def test_invalid_path_traversal(self):
        """Should return False for path traversal attacks."""
        assert not self.module.is_valid_worktree_path(".worktrees/../foo")
        assert not self.module.is_valid_worktree_path(".worktrees/../../../tmp")
        assert not self.module.is_valid_worktree_path(".worktrees/issue-123/../../tmp")

    def test_invalid_worktrees_only(self):
        """Should return False for .worktrees without subdirectory."""
        assert not self.module.is_valid_worktree_path(".worktrees")
        assert not self.module.is_valid_worktree_path(".worktrees/")
