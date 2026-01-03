#!/usr/bin/env python3
"""Integration tests (E2E) for ci-wait-check.py hook.

These tests run the hook as a subprocess, testing the full input-to-output flow.
For unit tests of individual functions, see test_ci_wait_check_unit.py.

Test categories:
1. Integration tests: Full hook execution via subprocess
2. Edge cases: Empty commands, special characters, long commands
3. False positive prevention: Ensure similar but valid commands are not blocked
"""

import json
import subprocess
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "ci-wait-check.py"


def run_hook(command: str) -> dict:
    """Run the hook with given command and return the result.

    Args:
        command: The bash command to test

    Returns:
        Parsed JSON result from the hook
    """
    input_data = {"tool_input": {"command": command}}
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestGhPrChecksWatch:
    """Integration tests for gh pr checks --watch blocking."""

    def test_blocks_pr_checks_watch_basic(self):
        """Should block basic gh pr checks --watch command."""
        result = run_hook("gh pr checks 123 --watch")
        assert result["decision"] == "block"
        assert "ci-monitor.py" in result["reason"]

    def test_blocks_pr_checks_watch_flags_first(self):
        """Should block gh pr checks --watch 123 (flags before PR number)."""
        result = run_hook("gh pr checks --watch 456")
        assert result["decision"] == "block"
        assert "ci-monitor.py" in result["reason"]

    def test_blocks_pr_checks_watch_with_interval(self):
        """Should block gh pr checks --watch with additional flags."""
        result = run_hook("gh pr checks 789 --watch --interval 30")
        assert result["decision"] == "block"

    def test_approves_pr_checks_without_watch(self):
        """Should approve gh pr checks without --watch flag."""
        result = run_hook("gh pr checks 123")
        assert result["decision"] == "approve"


class TestGhRunWatch:
    """Integration tests for gh run watch blocking."""

    def test_blocks_run_watch_basic(self):
        """Should block basic gh run watch command."""
        result = run_hook("gh run watch 12345678")
        assert result["decision"] == "block"
        assert "ci-monitor.py" in result["reason"]
        assert "冗長" in result["reason"]

    def test_blocks_run_watch_with_exit_status(self):
        """Should block gh run watch with --exit-status flag."""
        result = run_hook("gh run watch 12345678 --exit-status")
        assert result["decision"] == "block"
        assert "ci-monitor.py" in result["reason"]

    def test_blocks_run_watch_with_interval(self):
        """Should block gh run watch with --interval flag."""
        result = run_hook("gh run watch 12345678 --interval 10")
        assert result["decision"] == "block"

    def test_blocks_run_watch_without_run_id(self):
        """Should block gh run watch even without run ID."""
        result = run_hook("gh run watch")
        assert result["decision"] == "block"
        assert "ci-monitor.py" in result["reason"]

    def test_approves_run_list(self):
        """Should approve gh run list (not watch)."""
        result = run_hook("gh run list")
        assert result["decision"] == "approve"

    def test_approves_run_view(self):
        """Should approve gh run view (not watch)."""
        result = run_hook("gh run view 12345678")
        assert result["decision"] == "approve"

    def test_approves_quoted_run_watch_mention(self):
        """Should approve commands that only mention gh run watch in quotes."""
        # This is a false positive prevention test per Issue #1610
        result = run_hook('gh pr comment -b "please avoid gh run watch"')
        assert result["decision"] == "approve"


class TestManualPrCheck:
    """Integration tests for manual PR state check blocking."""

    def test_blocks_merge_state_status_check(self):
        """Should block gh pr view with --json mergeStateStatus."""
        result = run_hook("gh pr view 123 --json mergeStateStatus")
        assert result["decision"] == "block"
        assert "ci-monitor.py" in result["reason"]

    def test_blocks_api_pulls_direct(self):
        """Should block direct gh api /repos/.../pulls/{PR} access."""
        result = run_hook("gh api /repos/owner/repo/pulls/456")
        assert result["decision"] == "block"

    def test_approves_api_pulls_comments(self):
        """Should allow gh api /repos/.../pulls/{PR}/comments (Issue #192)."""
        result = run_hook("gh api /repos/owner/repo/pulls/456/comments")
        assert result["decision"] == "approve"

    def test_approves_api_pulls_reviews(self):
        """Should allow gh api /repos/.../pulls/{PR}/reviews (Issue #192)."""
        result = run_hook("gh api /repos/owner/repo/pulls/789/reviews")
        assert result["decision"] == "approve"


class TestManualPolling:
    """Integration tests for manual polling pattern blocking."""

    def test_blocks_sleep_and_gh(self):
        """Should block sleep && gh polling pattern."""
        result = run_hook("sleep 30 && gh api repos/owner/repo/pulls")
        assert result["decision"] == "block"
        assert "手動ポーリング" in result["reason"]

    def test_blocks_sleep_semicolon_gh(self):
        """Should block sleep ; gh polling pattern."""
        result = run_hook("sleep 30; gh pr list")
        assert result["decision"] == "block"

    def test_blocks_while_loop_polling(self):
        """Should block while loop with sleep and gh."""
        result = run_hook("while true; do sleep 10; gh api repos/...; done")
        assert result["decision"] == "block"


class TestApprovedCommands:
    """Integration tests for commands that should be approved."""

    def test_approves_normal_gh_commands(self):
        """Should approve normal gh commands."""
        commands = [
            "gh pr list",
            "gh pr view 123",
            "gh pr merge 456",
            "gh issue list",
            "gh repo clone owner/repo",
        ]
        for cmd in commands:
            with self.subTest(command=cmd):
                result = run_hook(cmd)
                assert result["decision"] == "approve"

    def test_approves_unrelated_commands(self):
        """Should approve unrelated bash commands."""
        commands = [
            "npm run build",
            "git status",
            "python3 script.py",
            "ls -la",
        ]
        for cmd in commands:
            with self.subTest(command=cmd):
                result = run_hook(cmd)
                assert result["decision"] == "approve"


class TestEdgeCases:
    """Edge case tests to ensure robustness."""

    def test_empty_command(self):
        """Should approve empty command (no false positive)."""
        result = run_hook("")
        assert result["decision"] == "approve"

    def test_whitespace_only_command(self):
        """Should approve whitespace-only command."""
        result = run_hook("   \n\t  ")
        assert result["decision"] == "approve"

    def test_command_with_special_characters(self):
        """Should handle commands with special characters."""
        result = run_hook("echo 'hello world' | grep 'hello'")
        assert result["decision"] == "approve"

    def test_long_command(self):
        """Should handle very long commands."""
        long_cmd = "echo " + "a" * 10000
        result = run_hook(long_cmd)
        assert result["decision"] == "approve"

    def test_command_with_unicode(self):
        """Should handle commands with unicode characters."""
        result = run_hook("echo '日本語テスト 🎉'")
        assert result["decision"] == "approve"


class TestFalsePositivePrevention:
    """Tests to prevent false positives (blocking valid commands)."""

    def test_no_false_positive_on_watch_without_pr_checks(self):
        """Should not block 'watch' in other contexts."""
        result = run_hook("watch -n 1 date")
        assert result["decision"] == "approve"

    def test_no_false_positive_on_sleep_without_gh(self):
        """Should not block sleep without gh command."""
        result = run_hook("sleep 30 && echo 'done'")
        assert result["decision"] == "approve"

    def test_no_false_positive_on_gh_without_sleep(self):
        """Should not block gh without sleep pattern."""
        result = run_hook("gh pr list && echo 'done'")
        assert result["decision"] == "approve"

    def test_no_false_positive_on_while_without_both(self):
        """Should not block while loop without both sleep and gh."""
        result = run_hook("while true; do echo 'hello'; done")
        assert result["decision"] == "approve"

    def test_no_false_positive_on_partial_match(self):
        """Should not block commands that partially match patterns."""
        # 'sleeping' is not the same as 'sleep N'
        result = run_hook("echo 'sleeping beauty' && gh pr list")
        assert result["decision"] == "approve"

    def test_allows_gh_pr_checks_list_format(self):
        """Should allow gh pr checks with different output formats."""
        result = run_hook("gh pr checks 123 --json name,status")
        assert result["decision"] == "approve"


class TestMalformedInput:
    """Tests for malformed or edge-case inputs."""

    def test_missing_tool_input(self):
        """Should handle missing tool_input gracefully."""
        input_data = {"other_field": "value"}
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
        )
        parsed = json.loads(result.stdout)
        # Should approve (fail-open) when input is malformed
        assert parsed["decision"] == "approve"

    def test_missing_command_field(self):
        """Should handle missing command field gracefully."""
        input_data = {"tool_input": {}}
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
        )
        parsed = json.loads(result.stdout)
        assert parsed["decision"] == "approve"

    def test_null_command(self):
        """Should handle null command gracefully.

        Issue #1508: When command is None, it's converted to empty string
        and processed normally (approves as no blocked patterns match).
        """
        input_data = {"tool_input": {"command": None}}
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
        )
        parsed = json.loads(result.stdout)
        # command=None is converted to "", which approves normally
        assert parsed["decision"] == "approve"

    def test_null_tool_input(self):
        """Should handle null tool_input gracefully.

        Issue #1508: When tool_input is None (not missing, but explicitly null),
        it's converted to empty dict and command is extracted as empty string.
        """
        input_data = {"tool_input": None}
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
        )
        parsed = json.loads(result.stdout)
        # tool_input=None is converted to {}, command becomes "", approves normally
        assert parsed["decision"] == "approve"
