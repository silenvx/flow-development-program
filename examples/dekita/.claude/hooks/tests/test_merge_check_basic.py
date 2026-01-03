#!/usr/bin/env python3
"""Tests for merge-check.py - basic module."""

import json
import subprocess
import sys
from pathlib import Path

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "merge-check.py"

# Import submodules for correct mock targets after modularization (Issue #1756)
# These imports enable tests to mock functions at their actual definition locations


def run_hook(input_data: dict) -> dict | None:
    """Run the hook with given input and return the result.

    Returns None if no output (silent approval per design principle).
    """
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        return None  # Silent approval
    return json.loads(result.stdout)


class TestMergeCheckAutoMerge:
    """Tests for auto-merge blocking."""

    def test_block_auto_merge(self):
        """Should block gh pr merge --auto commands."""
        result = run_hook({"tool_input": {"command": "gh pr merge 123 --auto --squash"}})

        assert result["decision"] == "block"
        assert "auto-merge" in result["reason"]

    def test_block_auto_merge_different_order(self):
        """Should block --auto regardless of argument order."""
        result = run_hook({"tool_input": {"command": "gh pr merge --auto 123"}})

        assert result["decision"] == "block"

    def test_approve_manual_merge(self):
        """Should approve manual merge without --auto (silent approval)."""
        result = run_hook({"tool_input": {"command": "gh pr merge 123 --squash"}})

        # Without AI reviewer check, this should approve silently (no output)
        assert result is None


class TestMergeCheckAdminMerge:
    """Tests for admin-merge blocking."""

    def test_block_admin_merge(self):
        """Should block gh pr merge --admin commands."""
        result = run_hook({"tool_input": {"command": "gh pr merge 123 --admin"}})

        assert result["decision"] == "block"
        assert "--admin" in result["reason"]
        assert "ブランチ保護ルール" in result["reason"]

    def test_block_admin_merge_different_order(self):
        """Should block --admin regardless of argument order."""
        result = run_hook({"tool_input": {"command": "gh pr merge --admin 123"}})

        assert result["decision"] == "block"

    def test_block_admin_merge_with_other_flags(self):
        """Should block --admin even with other flags."""
        result = run_hook({"tool_input": {"command": "gh pr merge 123 --squash --admin"}})

        assert result["decision"] == "block"
        assert "--admin" in result["reason"]

    def test_block_admin_merge_whitespace(self):
        """Should handle extra whitespace in --admin commands."""
        result = run_hook({"tool_input": {"command": "gh   pr   merge   --admin   123"}})

        assert result["decision"] == "block"

    def test_approve_merge_with_admin_in_body_issue_2384(self):
        """Should NOT block when --admin appears only in --body text (Issue #2384).

        This is a regression test for the false positive where gh pr merge
        with '--admin' in the --body text was incorrectly blocked.
        """
        # The --body contains "--admin" as part of the explanation text
        command = 'gh pr merge 123 --squash --body "This PR improves --admin option handling"'
        result = run_hook({"tool_input": {"command": command}})
        assert result is None  # Silent approval

    def test_approve_merge_with_auto_in_body_issue_2384(self):
        """Should NOT block when --auto appears only in --body text (Issue #2384)."""
        command = 'gh pr merge 123 --squash --body "Discussing --auto merge option"'
        result = run_hook({"tool_input": {"command": command}})
        assert result is None  # Silent approval

    def test_approve_merge_with_nested_quotes_in_body_copilot_review(self):
        """Should NOT block when '--admin' appears quoted inside --body (Copilot review).

        This tests the case where the option name is single-quoted inside a double-quoted
        body text, like: --body "The '--admin' option"
        """
        command = """gh pr merge 123 --squash --body "The '--admin' option should be avoided" """
        result = run_hook({"tool_input": {"command": command}})
        assert result is None  # Silent approval

    def test_approve_merge_with_nested_auto_quotes_in_body(self):
        """Should NOT block when '--auto' appears quoted inside --body."""
        command = """gh pr merge 123 --squash --body "The '--auto' option is disabled" """
        result = run_hook({"tool_input": {"command": command}})
        assert result is None  # Silent approval

    def test_approve_merge_with_body_equals_syntax_copilot_review(self):
        """Should NOT block when --admin appears in --body= value (Copilot review)."""
        command = 'gh pr merge 123 --squash --body="The --admin option is blocked"'
        result = run_hook({"tool_input": {"command": command}})
        assert result is None  # Silent approval

    def test_approve_merge_with_b_flag_copilot_review(self):
        """Should NOT block when --admin appears in -b value (Copilot review)."""
        command = 'gh pr merge 123 --squash -b "Discussing --admin behavior"'
        result = run_hook({"tool_input": {"command": command}})
        assert result is None  # Silent approval

    def test_block_real_admin_option_with_body(self):
        """Should block real --admin option even when --body is present."""
        command = 'gh pr merge 123 --admin --body "Some description"'
        result = run_hook({"tool_input": {"command": command}})
        assert result["decision"] == "block"
        assert "--admin" in result["reason"]

    def test_block_quoted_admin_option_codex_review(self):
        """Should block "--admin" even when quoted (Codex review feedback)."""
        command = 'gh pr merge 123 "--admin"'
        result = run_hook({"tool_input": {"command": command}})
        assert result["decision"] == "block"
        assert "--admin" in result["reason"]

    def test_block_single_quoted_admin_option(self):
        """Should block '--admin' with single quotes."""
        command = "gh pr merge 123 '--admin'"
        result = run_hook({"tool_input": {"command": command}})
        assert result["decision"] == "block"
        assert "--admin" in result["reason"]

    def test_block_single_quoted_auto_option(self):
        """Should block '--auto' with single quotes."""
        command = "gh pr merge 123 '--auto'"
        result = run_hook({"tool_input": {"command": command}})
        assert result["decision"] == "block"
        assert "auto-merge" in result["reason"]

    def test_block_quoted_auto_option_codex_review(self):
        """Should block "--auto" even when quoted (Codex review feedback)."""
        command = 'gh pr merge 123 "--auto"'
        result = run_hook({"tool_input": {"command": command}})
        assert result["decision"] == "block"
        assert "auto-merge" in result["reason"]


class TestMergeCheckHelpers:
    """Tests for helper functions in merge-check."""

    def test_extract_pr_number(self):
        """Test PR number extraction from various command formats.

        Note: extract_pr_number is now in common.py (Issue #557).
        It extracts PR numbers from any gh pr command, not just merge.
        """
        import sys

        hooks_dir = HOOK_PATH.parent
        sys.path.insert(0, str(hooks_dir))
        from lib.github import extract_pr_number

        # Standard format
        assert extract_pr_number("gh pr merge 123") == "123"

        # With options before number
        assert extract_pr_number("gh pr merge --squash 456") == "456"

        # With options after number
        assert extract_pr_number("gh pr merge 789 --squash") == "789"

        # No PR number
        assert extract_pr_number("gh pr merge") is None

        # View command - now extracts PR number (behavior change from original)
        assert extract_pr_number("gh pr view 123") == "123"


class TestMergeCheckNonMergeCommands:
    """Tests for non-merge commands."""

    def test_approve_non_merge_commands(self):
        """Should approve commands that are not gh pr merge (silent)."""
        test_cases = [
            "ls -la",
            "git status",
            "gh pr view 123",
            "gh pr create --title 'Test'",
            "echo 'gh pr merge'",  # String containing merge but not the command
        ]

        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                # Non-merge commands should exit silently (no output)
                assert result is None, f"Should approve silently: {command}"

    def test_approve_issue_create_with_auto_in_body(self):
        """Should approve gh issue create even if body contains '--auto' (silent).

        This is a regression test for the false positive where gh issue create
        with '--auto' in the body text was incorrectly blocked.
        """
        # Simulate creating an issue that discusses auto-merge in its body
        command = 'gh issue create --title "Test" --body "discussing gh pr merge --auto option"'
        result = run_hook({"tool_input": {"command": command}})
        assert result is None  # Silent approval

    def test_approve_issue_create_with_admin_in_body(self):
        """Should approve gh issue create even if body contains '--admin' (silent)."""
        command = 'gh issue create --title "Test" --body "gh pr merge --admin should be blocked"'
        result = run_hook({"tool_input": {"command": command}})
        assert result is None  # Silent approval

    def test_approve_heredoc_with_merge_keywords(self):
        """Should approve commands with merge keywords in heredoc body (silent)."""
        # Simulate gh issue create with heredoc body containing merge command text
        command = """gh issue create --title "Test" --body "$(cat <<'EOF'
## Problem
- gh pr merge --auto is dangerous
- gh pr merge --admin bypasses protection
EOF
)\""""
        result = run_hook({"tool_input": {"command": command}})
        assert result is None  # Silent approval

    def test_block_chained_auto_merge(self):
        """Should block gh pr merge --auto even when chained with other commands."""
        test_cases = [
            "cd repo && gh pr merge --auto 123",
            "git pull && gh pr merge 123 --auto --squash",
            "echo done; gh pr merge --auto 123",
            "VAR=1 && gh pr merge --auto 123",
        ]
        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "block", f"Should block: {command}"

    def test_block_chained_admin_merge(self):
        """Should block gh pr merge --admin even when chained with other commands."""
        command = "cd repo && gh pr merge --admin 123"
        result = run_hook({"tool_input": {"command": command}})
        assert result["decision"] == "block"

    def test_approve_quoted_merge_in_body_issue_1392(self):
        """Should NOT block when gh pr merge is inside quoted strings (Issue #1392)."""
        # --body contains merge command text - should not trigger merge detection
        result = run_hook(
            {"tool_input": {"command": 'gh pr comment --body "Use gh pr merge --squash"'}}
        )
        assert result is None  # Should silently approve

    def test_approve_quoted_operators_with_merge_issue_1392(self):
        """Should NOT block when shell operators are inside quotes with merge text (Issue #1392)."""
        test_cases = [
            'gh pr comment --body "cd repo && gh pr merge"',
            "gh pr comment --body 'git pull; gh pr merge 123'",
            'gh pr comment --body "Use || to fallback: gh pr merge || echo failed"',
        ]
        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result is None, f"Should approve: {command}"

    def test_approve_empty_command(self):
        """Should approve when command is empty (silent)."""
        result = run_hook({"tool_input": {"command": ""}})

        assert result is None  # Silent approval

    def test_approve_no_tool_input(self):
        """Should approve when tool_input is missing (silent)."""
        result = run_hook({})

        assert result is None  # Silent approval


class TestMergeCheckEdgeCases:
    """Tests for edge cases."""

    def test_case_sensitivity(self):
        """Commands should be case-sensitive (gh is lowercase, silent approval)."""
        # GH (uppercase) should not match
        result = run_hook({"tool_input": {"command": "GH pr merge --auto 123"}})

        # Should approve silently because it doesn't match the pattern
        assert result is None

    def test_whitespace_handling(self):
        """Should handle extra whitespace in commands."""
        result = run_hook({"tool_input": {"command": "gh   pr   merge   --auto   123"}})

        assert result["decision"] == "block"
