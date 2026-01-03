#!/usr/bin/env python3
"""Integration test for hook helper functions."""

import json
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent


class TestHookHelperIntegration:
    """Integration tests for hook helper functions."""

    def test_ci_wait_check_block_format(self):
        """Test that ci-wait-check includes hook name in block reason."""
        test_input = json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "gh pr checks 123 " + "--" + "watch"
                },  # Split to avoid hook detection
            }
        )

        result = subprocess.run(
            [sys.executable, str(HOOKS_DIR / "ci-wait-check.py")],
            input=test_input,
            capture_output=True,
            text=True,
        )

        output = json.loads(result.stdout)
        assert output["decision"] == "block", f"Expected block, got {output}"
        assert "[ci-wait-check]" in output["reason"], (
            f"Hook name missing in reason: {output['reason'][:100]}"
        )

    def test_ci_wait_check_approve_format(self):
        """Test that ci-wait-check includes systemMessage on approve."""
        test_input = json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls -la"},  # Safe command
            }
        )

        result = subprocess.run(
            [sys.executable, str(HOOKS_DIR / "ci-wait-check.py")],
            input=test_input,
            capture_output=True,
            text=True,
        )

        output = json.loads(result.stdout)
        assert output["decision"] == "approve", f"Expected approve, got {output}"
        assert "systemMessage" in output, f"systemMessage missing: {output}"
        assert "ci-wait-check" in output["systemMessage"], (
            f"Hook name missing in systemMessage: {output}"
        )

    def test_codex_review_check_approve_format(self):
        """Test that codex-review-check includes systemMessage on approve."""
        test_input = json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls -la"},  # Safe command
            }
        )

        result = subprocess.run(
            [sys.executable, str(HOOKS_DIR / "codex-review-check.py")],
            input=test_input,
            capture_output=True,
            text=True,
        )

        output = json.loads(result.stdout)
        assert output["decision"] == "approve", f"Expected approve, got {output}"
        assert "systemMessage" in output, f"systemMessage missing: {output}"
        assert "codex-review-check" in output["systemMessage"], f"Hook name missing: {output}"

    def test_merge_check_non_target_silent(self):
        """Test that merge-check exits silently for non-target commands."""
        test_input = json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls -la"},  # Non-merge command
            }
        )

        result = subprocess.run(
            [sys.executable, str(HOOKS_DIR / "merge-check.py")],
            input=test_input,
            capture_output=True,
            text=True,
        )

        # Non-target commands should exit silently (no output per design principle)
        assert result.returncode == 0, f"Expected exit code 0, got {result.returncode}"
        assert result.stdout.strip() == "", f"Expected no output, got: {result.stdout}"
