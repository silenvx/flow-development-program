#!/usr/bin/env python3
"""Tests for force-push-guard.py hook."""

import json
import subprocess
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "force-push-guard.py"


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestForcePushGuardBlocks:
    """Tests for commands that should be blocked."""

    def test_blocks_force(self):
        """Should block git push --force."""
        result = run_hook({"tool_input": {"command": "git push --force"}})
        assert result["decision"] == "block"
        assert "force-push-guard" in result["reason"]
        assert "--force-with-lease" in result["reason"]

    def test_blocks_force_with_remote(self):
        """Should block git push origin --force."""
        result = run_hook({"tool_input": {"command": "git push origin --force"}})
        assert result["decision"] == "block"

    def test_blocks_force_with_branch(self):
        """Should block git push origin branch --force."""
        result = run_hook({"tool_input": {"command": "git push origin main --force"}})
        assert result["decision"] == "block"

    def test_blocks_force_before_remote(self):
        """Should block git push --force origin branch."""
        result = run_hook({"tool_input": {"command": "git push --force origin main"}})
        assert result["decision"] == "block"

    def test_blocks_short_form(self):
        """Should block git push -f."""
        result = run_hook({"tool_input": {"command": "git push -f"}})
        assert result["decision"] == "block"

    def test_blocks_short_form_with_remote(self):
        """Should block git push -f origin branch."""
        result = run_hook({"tool_input": {"command": "git push -f origin main"}})
        assert result["decision"] == "block"

    def test_blocks_combined_short_flags(self):
        """Should block combined short flags containing -f."""
        test_cases = [
            "git push -uf origin main",  # -u and -f combined
            "git push -fu origin main",  # -f and -u combined
            "git push -auf origin main",  # multiple flags combined
        ]
        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "block", f"Should block: {command}"

    def test_blocks_mixed_force_flags(self):
        """Should block git push --force-with-lease --force (--force takes precedence)."""
        test_cases = [
            "git push --force-with-lease --force",
            "git push --force --force-with-lease",
            "git push --force-with-lease origin main --force",
        ]
        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "block", f"Should block: {command}"


class TestForcePushGuardAllows:
    """Tests for commands that should be allowed."""

    def test_allows_force_with_lease(self):
        """Should allow git push --force-with-lease."""
        result = run_hook({"tool_input": {"command": "git push --force-with-lease"}})
        assert result["decision"] == "approve"

    def test_allows_force_with_lease_with_remote(self):
        """Should allow git push --force-with-lease origin branch."""
        result = run_hook({"tool_input": {"command": "git push --force-with-lease origin main"}})
        assert result["decision"] == "approve"

    def test_allows_normal_push(self):
        """Should allow normal git push."""
        result = run_hook({"tool_input": {"command": "git push"}})
        assert result["decision"] == "approve"

    def test_allows_push_with_remote(self):
        """Should allow git push origin branch."""
        result = run_hook({"tool_input": {"command": "git push origin main"}})
        assert result["decision"] == "approve"

    def test_allows_push_with_upstream(self):
        """Should allow git push -u origin branch."""
        result = run_hook({"tool_input": {"command": "git push -u origin main"}})
        assert result["decision"] == "approve"

    def test_allows_quoted_commands(self):
        """Should ignore commands inside quotes."""
        test_cases = [
            "echo 'git push --force'",
            'echo "git push --force"',
        ]
        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "approve"

    def test_allows_long_options_with_f(self):
        """Should allow long options containing 'f' like --follow-tags."""
        test_cases = [
            "git push --follow-tags",
            "git push --filter=blob:none",
            "git push origin main --follow-tags",
        ]
        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "approve", f"Should allow: {command}"


class TestForcePushGuardEdgeCases:
    """Tests for edge cases."""

    def test_empty_command(self):
        """Should approve empty command."""
        result = run_hook({"tool_input": {"command": ""}})
        assert result["decision"] == "approve"

    def test_whitespace_command(self):
        """Should approve whitespace-only command."""
        result = run_hook({"tool_input": {"command": "   "}})
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
        """Should ignore non-push git commands."""
        test_cases = ["git status", "git log", "git pull", "git fetch"]
        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "approve"
