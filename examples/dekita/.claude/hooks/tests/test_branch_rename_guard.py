#!/usr/bin/env python3
"""Tests for branch_rename_guard.py hook."""

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestBranchRenameGuard:
    """Tests for branch_rename_guard hook."""

    def test_check_branch_rename_simple(self):
        """Should detect simple git branch -m command."""
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        is_rename, target = branch_rename_guard.check_branch_rename(
            "git branch -m old-name new-name"
        )
        assert is_rename is True
        assert target == "old-name"

    def test_check_branch_rename_force(self):
        """Should detect git branch -M (force rename)."""
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        is_rename, target = branch_rename_guard.check_branch_rename("git branch -M main master")
        assert is_rename is True
        assert target == "main"

    def test_check_branch_rename_with_move_flag(self):
        """Should detect --move flag."""
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        is_rename, target = branch_rename_guard.check_branch_rename("git branch --move old new")
        assert is_rename is True

    def test_check_branch_rename_not_rename(self):
        """Should not flag non-rename commands."""
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        # Regular branch creation
        is_rename, _ = branch_rename_guard.check_branch_rename("git branch feature/new")
        assert is_rename is False

        # Branch deletion
        is_rename, _ = branch_rename_guard.check_branch_rename("git branch -d old-branch")
        assert is_rename is False

        # List branches
        is_rename, _ = branch_rename_guard.check_branch_rename("git branch -a")
        assert is_rename is False

    def test_check_branch_rename_in_quoted_string(self):
        """Should not detect rename inside quoted strings."""
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        # Inside echo
        is_rename, _ = branch_rename_guard.check_branch_rename("echo 'git branch -m test'")
        assert is_rename is False

    def test_check_branch_rename_not_git_commit(self):
        """Should not flag git commit -am as branch rename."""
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        # -am includes -m but is for commit, not branch rename
        is_rename, _ = branch_rename_guard.check_branch_rename("git commit -am 'message'")
        assert is_rename is False

        # Other git commands with -m should not be flagged
        is_rename, _ = branch_rename_guard.check_branch_rename("git merge -m 'message'")
        assert is_rename is False

    def test_check_branch_rename_with_global_options(self):
        """Should detect branch rename with git global options."""
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        # git -C <path> branch -m
        is_rename, target = branch_rename_guard.check_branch_rename(
            "git -C /some/path branch -m old new"
        )
        assert is_rename is True
        assert target == "old"

        # git --git-dir=<path> branch -M
        is_rename, target = branch_rename_guard.check_branch_rename(
            "git --git-dir=.git branch -M main master"
        )
        assert is_rename is True
        assert target == "main"

        # git -c key=value branch --move
        is_rename, _ = branch_rename_guard.check_branch_rename(
            "git -c user.name=test branch --move old new"
        )
        assert is_rename is True

    def test_check_branch_rename_with_branch_options(self):
        """Should detect branch rename with branch options before -m."""
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        # git branch --color -m
        is_rename, target = branch_rename_guard.check_branch_rename("git branch --color -m old new")
        assert is_rename is True
        assert target == "old"

        # git branch -v -m
        is_rename, target = branch_rename_guard.check_branch_rename("git branch -v -m main master")
        assert is_rename is True
        assert target == "main"

        # git branch --no-color --move
        is_rename, _ = branch_rename_guard.check_branch_rename(
            "git branch --no-color --move old new"
        )
        assert is_rename is True

    def test_check_branch_rename_with_force_flag(self):
        """Should detect branch rename with -f/--force flag (Issue #996 Codex review)."""
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        # git branch -f -m
        is_rename, target = branch_rename_guard.check_branch_rename("git branch -f -m old new")
        assert is_rename is True
        assert target == "old"

        # git branch -f -M
        is_rename, target = branch_rename_guard.check_branch_rename("git branch -f -M main master")
        assert is_rename is True
        assert target == "main"

        # git branch --force -m
        is_rename, target = branch_rename_guard.check_branch_rename("git branch --force -m old new")
        assert is_rename is True
        assert target == "old"

        # git branch --force --move
        is_rename, _ = branch_rename_guard.check_branch_rename("git branch --force --move old new")
        assert is_rename is True

    def test_check_branch_rename_with_combined_flags(self):
        """Should detect combined flags like -fm (Issue #996 Copilot review).

        Note: Even invalid combinations like -dm are blocked. This is intentional
        because blocking an invalid command is harmless - git itself would reject it.
        """
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        # git branch -fm (combined -f and -m)
        is_rename, target = branch_rename_guard.check_branch_rename("git branch -fm old new")
        assert is_rename is True
        assert target == "old"

        # git branch -afm (with additional flags)
        is_rename, target = branch_rename_guard.check_branch_rename("git branch -afm old new")
        assert is_rename is True
        assert target == "old"

        # git branch -dm (invalid: -d and -m are mutually exclusive)
        # Blocked intentionally - blocking invalid commands is harmless
        is_rename, _ = branch_rename_guard.check_branch_rename("git branch -dm old")
        assert is_rename is True

    def test_check_branch_rename_with_value_options(self):
        """Should detect rename with options that have values (Issue #996 Codex P1).

        This prevents bypass attempts like 'git branch --color=always -m old new'.
        """
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        # git branch --color=always -m
        is_rename, target = branch_rename_guard.check_branch_rename(
            "git branch --color=always -m old new"
        )
        assert is_rename is True
        assert target == "old"

        # git branch --sort=-committerdate -M
        is_rename, target = branch_rename_guard.check_branch_rename(
            "git branch --sort=-committerdate -M main master"
        )
        assert is_rename is True
        assert target == "main"

        # Multiple options with values
        is_rename, _ = branch_rename_guard.check_branch_rename(
            "git branch --color=always --sort=-date -m old new"
        )
        assert is_rename is True


class TestBranchRenameGuardIntegration:
    """Integration tests for branch_rename_guard hook."""

    @patch.dict("os.environ", {}, clear=True)
    def test_blocks_branch_rename(self):
        """Should block branch rename command."""
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        hook_input = json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git branch -m old-name new-name"},
                "session_id": "test-session",
            }
        )

        with patch("sys.stdin", StringIO(hook_input)):
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                branch_rename_guard.main()
                output = mock_stdout.getvalue()
                result = json.loads(output)

                assert result["decision"] == "block"
                assert "branch-rename-guard" in result["reason"]

    @patch.dict("os.environ", {}, clear=True)
    def test_approves_non_rename(self):
        """Should approve non-rename commands."""
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        hook_input = json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git branch -a"},
                "session_id": "test-session",
            }
        )

        with patch("sys.stdin", StringIO(hook_input)):
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                branch_rename_guard.main()
                output = mock_stdout.getvalue()
                result = json.loads(output)

                assert result["decision"] == "approve"

    @patch.dict("os.environ", {"SKIP_BRANCH_RENAME_GUARD": "1"}, clear=True)
    def test_skip_with_env_var(self):
        """Should skip when SKIP_BRANCH_RENAME_GUARD=1."""
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        hook_input = json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git branch -m main master"},
                "session_id": "test-session",
            }
        )

        with patch("sys.stdin", StringIO(hook_input)):
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                branch_rename_guard.main()
                output = mock_stdout.getvalue()
                result = json.loads(output)

                assert result["decision"] == "approve"

    @patch.dict("os.environ", {}, clear=True)
    def test_approves_non_bash_tool(self):
        """Should approve non-Bash tools."""
        import importlib

        import branch_rename_guard

        importlib.reload(branch_rename_guard)

        hook_input = json.dumps(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "/some/path"},
                "session_id": "test-session",
            }
        )

        with patch("sys.stdin", StringIO(hook_input)):
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                branch_rename_guard.main()
                output = mock_stdout.getvalue()
                result = json.loads(output)

                assert result["decision"] == "approve"
