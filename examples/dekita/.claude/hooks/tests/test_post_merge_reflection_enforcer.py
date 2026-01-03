#!/usr/bin/env python3
"""Unit tests for post-merge-reflection-enforcer.py (stateless version).

Issue #2159: Simplified to stateless design. No state files are used.
Issue #2416: Added project directory validity check for worktree deletion.
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Dynamic import for hyphenated module name
HOOK_PATH = Path(__file__).parent.parent / "post-merge-reflection-enforcer.py"
_spec = importlib.util.spec_from_file_location("post_merge_reflection_enforcer", HOOK_PATH)
post_merge_reflection_enforcer = importlib.util.module_from_spec(_spec)
sys.modules["post_merge_reflection_enforcer"] = post_merge_reflection_enforcer
_spec.loader.exec_module(post_merge_reflection_enforcer)

is_pr_merge_command = post_merge_reflection_enforcer.is_pr_merge_command
extract_pr_number = post_merge_reflection_enforcer.extract_pr_number
_check_project_dir_valid = post_merge_reflection_enforcer._check_project_dir_valid
# is_merge_success is now in common.py with new signature
from lib.repo import is_merge_success


class TestCheckProjectDirValid:
    """Tests for _check_project_dir_valid function (Issue #2416)."""

    def test_returns_true_when_no_project_dir(self):
        """Should return True when CLAUDE_PROJECT_DIR is not set."""
        with patch.dict(os.environ, {}, clear=True):
            is_valid, original_repo = _check_project_dir_valid()
            assert is_valid is True
            assert original_repo is None

    def test_returns_true_when_project_dir_exists(self):
        """Should return True when CLAUDE_PROJECT_DIR exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": tmpdir}):
                with patch.object(
                    post_merge_reflection_enforcer,
                    "get_repo_root",
                    return_value=Path(tmpdir),
                ):
                    is_valid, original_repo = _check_project_dir_valid()
                    assert is_valid is True
                    # original_repo should be the repo root when project dir exists
                    assert original_repo == Path(tmpdir)

    def test_returns_false_when_worktree_deleted(self):
        """Should return False when worktree path doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_path = Path(tmpdir)
            worktree_path = original_path / ".worktrees" / "issue-123"
            # Don't create worktree_path, simulating deletion
            worktree_path_str = str(worktree_path)

            with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": worktree_path_str}):
                is_valid, original_repo = _check_project_dir_valid()
                assert is_valid is False
                assert original_repo == original_path

    def test_returns_none_when_original_not_found(self):
        """Should return None when original repo path can't be determined."""
        # Use a path without .worktrees pattern
        nonexistent_path = "/nonexistent/path/without/worktrees"
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": nonexistent_path}):
            is_valid, original_repo = _check_project_dir_valid()
            assert is_valid is False
            assert original_repo is None

    def test_returns_none_when_worktrees_at_path_root(self):
        """Should return None when .worktrees is at path root (worktrees_idx=0).

        Edge case: Relative path like ".worktrees/issue-123" has worktrees_idx=0.
        Path(*parts[:0]) = Path() which returns current directory.
        This case should return None to avoid unexpected behavior.
        """
        # .worktrees/issue-123 -> parts = ('.worktrees', 'issue-123')
        # worktrees_idx = 0, so Path(*parts[:0]) = Path() = current dir
        worktrees_at_root = ".worktrees/issue-123"
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": worktrees_at_root}):
            is_valid, original_repo = _check_project_dir_valid()
            assert is_valid is False
            # Should return None because worktrees_idx is 0 (no valid parent)
            assert original_repo is None


class TestIsPrMergeCommand:
    """Tests for is_pr_merge_command function."""

    def test_detects_simple_merge(self):
        """Should detect simple gh pr merge command."""
        assert is_pr_merge_command("gh pr merge 123")

    def test_detects_merge_with_options(self):
        """Should detect merge with options."""
        assert is_pr_merge_command("gh pr merge 123 --squash")
        assert is_pr_merge_command("gh pr merge --auto")
        assert is_pr_merge_command("gh pr merge 456 --merge --delete-branch")

    def test_ignores_other_commands(self):
        """Should not flag non-merge commands."""
        assert not is_pr_merge_command("gh pr view 123")
        assert not is_pr_merge_command("gh pr create")
        assert not is_pr_merge_command("gh issue create")
        assert not is_pr_merge_command("git merge main")

    def test_handles_empty_string(self):
        """Should handle empty string."""
        assert not is_pr_merge_command("")

    def test_ignores_quoted_strings(self):
        """Should not detect commands inside quoted strings (Issue #2553)."""
        assert not is_pr_merge_command("echo 'gh pr merge 123'")
        assert not is_pr_merge_command('echo "gh pr merge 456"')
        assert not is_pr_merge_command("printf 'Run gh pr merge to merge'")

    def test_ignores_heredoc_commands(self):
        """Should not detect commands in heredoc content (Issue #2553)."""
        # Heredoc with single-quoted marker
        assert not is_pr_merge_command("cat > file.py << 'EOF'\ngh pr merge 123\nEOF")
        # Heredoc with unquoted marker
        assert not is_pr_merge_command("cat << EOF\ngh pr merge 456\nEOF")
        # Heredoc with dash (strip leading tabs)
        assert not is_pr_merge_command("cat <<- 'MARKER'\ngh pr merge 789\nMARKER")
        # tee heredoc
        assert not is_pr_merge_command("tee file.txt << EOF\ngh pr merge 999\nEOF")
        # bash heredoc (shell execution)
        assert not is_pr_merge_command("bash << 'SCRIPT'\ngh pr merge 111\nSCRIPT")

    def test_detects_quoted_heredoc_marker_with_real_merge(self):
        """Should detect real merge when << is inside quotes (Issue #2553).

        When << is inside quotes, it's not a real heredoc.
        e.g., echo 'test <<' && gh pr merge 123 should be detected.
        """
        # << inside quotes is stripped, so real merge is detected
        assert is_pr_merge_command("echo 'test <<' && gh pr merge 123")
        assert is_pr_merge_command('echo "<<" && gh pr merge 456')
        # printf with << in quotes
        assert is_pr_merge_command("printf '<<EOF' && gh pr merge 789")

    def test_detects_real_merge_with_chain(self):
        """Should detect real merge in command chains."""
        assert is_pr_merge_command("cd /path && gh pr merge 123")
        assert is_pr_merge_command("git status; gh pr merge 456 --squash")

    def test_non_heredoc_commands_with_heredoc_syntax(self):
        """Commands without cat/tee/bash/sh/etc. followed by << should be detected.

        The heredoc pattern is only matched for known heredoc-starting commands.
        Other commands with << should still be detected if they contain gh pr merge.
        """
        # Here-string (<<<) should not be treated as heredoc, so merge is detected
        assert is_pr_merge_command("cat <<< 'test' && gh pr merge 123")
        # Command with << that's not a known heredoc command
        assert is_pr_merge_command("python script.py << gh pr merge 123")

    def test_detects_merge_before_heredoc(self):
        """Should detect real merge when it appears BEFORE heredoc.

        e.g., gh pr merge 123 && cat <<EOF should be detected because
        the merge command comes BEFORE the heredoc data.
        """
        # Real merge followed by heredoc
        assert is_pr_merge_command("gh pr merge 123 && cat <<EOF\ntest data\nEOF")
        assert is_pr_merge_command("gh pr merge 456 --squash; cat << 'END'\ndata\nEND")
        # Multiple commands with merge first
        assert is_pr_merge_command("gh pr merge 789 && tee log.txt <<EOF\nlog\nEOF")


class TestIsMergeSuccess:
    """Tests for is_merge_success function (common.py version).

    Note: is_merge_success was moved to common.py with signature:
    is_merge_success(exit_code, stdout, command, *, stderr="")
    """

    def test_detects_successful_merge(self):
        """Should detect successful merge output."""
        assert is_merge_success(0, "Merged pull request #123", "gh pr merge 123")
        assert is_merge_success(0, "✓ Merged pull request #456", "gh pr merge 456")

    def test_detects_rebase_merge(self):
        """Should detect rebase merge output."""
        assert is_merge_success(0, "Rebased and merged pull request #789", "gh pr merge 789")
        assert is_merge_success(0, "✓ Rebased and merged pull request #100", "gh pr merge 100")

    def test_detects_squash_merge(self):
        """Should detect squash merge output."""
        assert is_merge_success(0, "Squashed and merged pull request #200", "gh pr merge 200")
        assert is_merge_success(0, "✓ Squashed and merged pull request #300", "gh pr merge 300")

    def test_detects_locked_worktree_guard_output(self):
        """Should detect locked-worktree-guard output (Issue #2609)."""
        # locked-worktree-guard returns "Merge completed successfully." when it
        # intercepts and re-executes a merge command without --delete-branch
        output = (
            "[locked-worktree-guard] ✅ マージ完了（自動実行）: PR #2605\n\n"
            "worktree内からのマージを検出し、--delete-branch なしで自動実行しました。\n"
            "出力: Merge completed successfully."
        )
        assert is_merge_success(0, output, "gh pr merge 2605 --squash --delete-branch")

    def test_ignores_failed_merge(self):
        """Should not detect failed merge when no success pattern present."""
        assert not is_merge_success(1, "Error: merge failed", "gh pr merge 123")
        assert not is_merge_success(1, "Pull request could not be created", "gh pr merge 123")

    def test_returns_true_on_non_zero_exit_with_success_pattern(self):
        """Non-zero exit code with success pattern returns True (worktree edge case)."""
        # This handles the worktree --delete-branch edge case where merge succeeds
        # but branch deletion fails
        assert is_merge_success(1, "Merged pull request #123", "gh pr merge 123")

    def test_handles_empty_output(self):
        """Should handle empty output - returns True for empty output with exit 0 (squash merge)."""
        # Empty output with exit code 0 is success (squash merge case)
        assert is_merge_success(0, "", "gh pr merge 123")
        # Non-zero exit code is failure
        assert not is_merge_success(1, "", "gh pr merge 123")

    def test_detects_branch_delete_failure_with_merge_success_as_success(self):
        """Should detect merge success when both merge success and branch deletion failure.

        Issue #2099: When running gh pr merge --delete-branch in a worktree,
        the branch deletion may fail (because the branch is checked out),
        but the merge itself succeeded. The output contains both the merge
        success message and the branch deletion failure message.
        """
        # Realistic scenario: merge succeeded, then branch delete failed
        assert is_merge_success(
            1,
            "✓ Merged pull request #123\nfailed to delete branch 'fix/issue-123'",
            "gh pr merge 123 --squash --delete-branch",
        )
        assert is_merge_success(
            1,
            "Merged pull request #456\nerror: Cannot delete branch 'feat/new-feature'",
            "gh pr merge 456 --delete-branch",
        )
        assert is_merge_success(
            1,
            "Pull request #789 merged\ncannot delete the checked out branch 'fix/bug'",
            "gh pr merge 789 --squash --delete-branch",
        )

    def test_branch_delete_failure_in_stderr(self):
        """Should detect merge success when branch delete failure is in stderr."""
        # Branch delete failure in stderr, merge success in stdout
        assert is_merge_success(
            1,
            "✓ Merged pull request #123",
            "gh pr merge 123 --delete-branch",
            stderr="failed to delete branch 'fix/issue-123'",
        )

    def test_branch_delete_failure_only_is_success(self):
        """Branch deletion failure alone IS success when --delete-branch is used.

        Issue #2228: gh CLI only attempts branch deletion AFTER merge succeeds.
        So if we see a branch deletion failure, the merge was successful.
        The merge success message may not be included in the output.
        """
        # Branch delete failure alone with --delete-branch flag = merge success
        assert is_merge_success(
            1,
            "failed to delete branch 'fix/issue-123'",
            "gh pr merge 123 --squash --delete-branch",
        )
        assert is_merge_success(
            1,
            "error: Cannot delete branch 'feat/new-feature'",
            "gh pr merge 456 --delete-branch",
        )
        # Real-world example from Issue #2228
        assert is_merge_success(
            1,
            "failed to delete local branch fix/issue-2222-exit-code-fix: "
            "failed to run git: error: cannot delete branch 'fix/issue-2222-exit-code-fix' "
            "used by worktree at '/path/to/.worktrees/issue-2222'",
            "gh pr merge 2223 --squash --delete-branch",
        )

    def test_branch_delete_failure_requires_delete_branch_flag(self):
        """Branch deletion failure detection requires --delete-branch in command.

        Without --delete-branch flag, a branch deletion error message
        should not be treated as merge success (even with merge success pattern).
        This is already covered by success_patterns check, but tests the flag requirement.
        """
        # Without --delete-branch, branch delete failure alone should be false
        assert not is_merge_success(
            1,
            "failed to delete branch 'fix/issue-123'",
            "gh pr merge 123 --squash",
        )
        assert not is_merge_success(
            1,
            "error: Cannot delete branch 'feat/new-feature'",
            "gh pr merge 456",
        )


class TestExtractPrNumber:
    """Tests for extract_pr_number function."""

    def test_extracts_from_command(self):
        """Should extract PR number from gh pr merge command."""
        assert extract_pr_number("gh pr merge 100") == "100"
        assert extract_pr_number("gh pr merge 123 --squash") == "123"
        assert extract_pr_number("gh pr merge 456 --merge --delete-branch") == "456"

    def test_returns_none_for_no_match(self):
        """Should return None when no PR number in command."""
        # Only extracts from 'gh pr merge <number>' format
        assert extract_pr_number("gh pr merge") is None
        assert extract_pr_number("gh pr merge --auto") is None
        assert extract_pr_number("No PR number here") is None
        assert extract_pr_number("") is None


class TestMainIntegration:
    """Integration tests for main function (stateless version)."""

    def test_non_bash_tool_passthrough(self, capsys):
        """Should pass through for non-Bash tools."""
        input_data = {"tool_name": "Read", "tool_input": {}, "tool_result": {}}
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            post_merge_reflection_enforcer.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert result["continue"] is True

    def test_non_merge_command_passthrough(self, capsys):
        """Should pass through for non-merge commands."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr view 123"},
            "tool_result": {"stdout": "PR details", "exit_code": 0},
        }
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            post_merge_reflection_enforcer.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert result["continue"] is True
            assert "decision" not in result or result.get("decision") != "block"

    def test_successful_merge_returns_block_with_reflect(self, capsys):
        """Should return block + continue: true on successful merge.

        Issue #2089: Uses decision: block + continue: true pattern to force
        Claude Code to execute /reflect.
        Issue #2159: Stateless - no state files used.
        Issue #2364: Output to both reason AND systemMessage for transcript recording.
        """
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 123 --squash"},
            "tool_result": {
                "stdout": "✓ Merged pull request #123",
                "exit_code": 0,
            },
        }
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            post_merge_reflection_enforcer.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            # Issue #2089: Uses decision: block + continue: true pattern
            assert result["continue"] is True
            assert result["decision"] == "block"
            # Issue #2364: Both reason and systemMessage should contain the same message
            assert "reason" in result
            assert "systemMessage" in result
            assert "/reflect" in result["reason"]
            assert "/reflect" in result["systemMessage"]
            assert "PR #123" in result["reason"]
            assert "PR #123" in result["systemMessage"]
            # Verify [IMMEDIATE: /reflect] tag is present for detection
            assert "[IMMEDIATE: /reflect]" in result["reason"]
            assert "[IMMEDIATE: /reflect]" in result["systemMessage"]

    def test_failed_merge_no_action(self, capsys):
        """Should not block on failed merge."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 123"},
            "tool_result": {"stdout": "Error: merge failed", "exit_code": 1},
        }
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            post_merge_reflection_enforcer.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert result["continue"] is True
            assert "decision" not in result or result.get("decision") != "block"

    def test_every_merge_triggers_reflection(self, capsys):
        """Stateless: every successful merge triggers /reflect instruction.

        Issue #2159: Unlike the stateful version, every merge triggers
        the reflection instruction. There's no 'already triggered' state.
        """
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 123 --squash"},
            "tool_result": {
                "stdout": "✓ Merged pull request #123",
                "exit_code": 0,
            },
        }

        # First merge
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            post_merge_reflection_enforcer.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert result["decision"] == "block"
            assert "/reflect" in result["reason"]

        # Second merge of same PR - should ALSO block (stateless)
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            post_merge_reflection_enforcer.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            # Stateless: every merge triggers block
            assert result["decision"] == "block"
            assert "/reflect" in result["reason"]

    def test_merge_without_pr_number(self, capsys):
        """Should handle merge without explicit PR number in command."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge --squash"},
            "tool_result": {
                "stdout": "✓ Merged pull request #456",
                "exit_code": 0,
            },
        }
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            post_merge_reflection_enforcer.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert result["decision"] == "block"
            assert "/reflect" in result["reason"]
            # PR number extracted from command is None, so reason shows '?'
            assert "PR #?" in result["reason"]

    def test_worktree_deleted_shows_warning(self, capsys):
        """Should show worktree warning when project dir doesn't exist (Issue #2416)."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 789 --squash"},
            "tool_result": {
                "stdout": "✓ Merged pull request #789",
                "exit_code": 0,
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            original_path = Path(tmpdir)
            worktree_path = original_path / ".worktrees" / "issue-789"
            # Don't create worktree_path, simulating it was deleted
            worktree_path_str = str(worktree_path)

            with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": worktree_path_str}):
                with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                    with patch.object(post_merge_reflection_enforcer, "log_hook_execution"):
                        post_merge_reflection_enforcer.main()
                        captured = capsys.readouterr()
                        result = json.loads(captured.out)
                        # Should still block with reflect instruction
                        assert result["decision"] == "block"
                        assert "[IMMEDIATE: /reflect]" in result["reason"]
                        # Should include worktree warning
                        assert "worktreeが削除されています" in result["reason"]
                        # Should include the original path
                        assert tmpdir in result["reason"]
