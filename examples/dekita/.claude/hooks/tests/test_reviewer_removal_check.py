#!/usr/bin/env python3
"""Tests for reviewer-removal-check.py hook."""

import json
import subprocess
import sys
from pathlib import Path

# Get the hooks directory
HOOKS_DIR = Path(__file__).parent.parent
HOOK_SCRIPT = HOOKS_DIR / "reviewer-removal-check.py"


def run_hook(tool_name: str, command: str) -> dict:
    """Run the hook with the given input and return the result."""
    hook_input = {"tool_name": tool_name, "tool_input": {"command": command}}

    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
    )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": result.stderr, "stdout": result.stdout}


class TestReviewerRemovalCheck:
    """Tests for reviewer removal blocking."""

    def test_blocks_copilot_removal_with_json_input(self):
        """Test that removing Copilot via JSON input is blocked."""
        command = 'gh api repos/owner/repo/pulls/123/requested_reviewers -X DELETE --input - <<< \'{"reviewers":["Copilot"]}\''
        result = run_hook("Bash", command)
        assert result["decision"] == "block"
        assert "Copilot" in result["reason"]

    def test_blocks_codex_removal_with_json_input(self):
        """Test that removing Codex via JSON input is blocked."""
        command = 'gh api repos/owner/repo/pulls/123/requested_reviewers -X DELETE --input - <<< \'{"reviewers":["codex-bot"]}\''
        result = run_hook("Bash", command)
        assert result["decision"] == "block"
        assert "codex" in result["reason"].lower()

    def test_blocks_copilot_removal_with_f_flag(self):
        """Test that removing Copilot via -f flag is blocked."""
        command = "gh api repos/owner/repo/pulls/123/requested_reviewers -X DELETE -f reviewers='[\"Copilot\"]'"
        result = run_hook("Bash", command)
        assert result["decision"] == "block"

    def test_allows_human_reviewer_removal(self):
        """Test that removing human reviewers is approved."""
        command = 'gh api repos/owner/repo/pulls/123/requested_reviewers -X DELETE --input - <<< \'{"reviewers":["human-user"]}\''
        result = run_hook("Bash", command)
        assert result["decision"] == "approve"

    def test_allows_unrelated_api_calls(self):
        """Test that unrelated API calls are approved."""
        command = "gh api repos/owner/repo/pulls/123"
        result = run_hook("Bash", command)
        assert result["decision"] == "approve"

    def test_allows_non_delete_reviewer_operations(self):
        """Test that GET requests to reviewers endpoint are approved."""
        command = "gh api repos/owner/repo/pulls/123/requested_reviewers"
        result = run_hook("Bash", command)
        assert result["decision"] == "approve"

    def test_allows_non_bash_tools(self):
        """Test that non-Bash tools are approved."""
        command = "gh api repos/owner/repo/pulls/123/requested_reviewers -X DELETE"
        result = run_hook("Read", command)  # Using Read instead of Bash
        assert result["decision"] == "approve"

    def test_blocks_method_delete_variation(self):
        """Test that --method DELETE variation is also blocked."""
        command = 'gh api repos/owner/repo/pulls/123/requested_reviewers --method DELETE --input - <<< \'{"reviewers":["Copilot"]}\''
        result = run_hook("Bash", command)
        assert result["decision"] == "block"

    def test_error_message_includes_guidance(self):
        """Test that blocked message includes helpful guidance."""
        command = 'gh api repos/owner/repo/pulls/123/requested_reviewers -X DELETE --input - <<< \'{"reviewers":["Copilot"]}\''
        result = run_hook("Bash", command)
        assert result["decision"] == "block"
        # Check that guidance is included
        assert "タイムアウト" in result["reason"]
        assert "--timeout" in result["reason"]

    def test_allows_human_removal_from_copilot_named_repo(self):
        """Test that removing human from repo named 'copilot-tools' is approved.

        Regression test for false positive bug where repo names containing
        'copilot' or 'codex' would trigger blocking even for human reviewers.
        """
        command = 'gh api repos/org/copilot-tools/pulls/1/requested_reviewers -X DELETE --input - <<< \'{"reviewers":["alice"]}\''
        result = run_hook("Bash", command)
        assert result["decision"] == "approve"

    def test_allows_human_removal_from_codex_named_repo(self):
        """Test that removing human from repo named 'codex-app' is approved."""
        command = "gh api repos/org/codex-app/pulls/1/requested_reviewers -X DELETE -f reviewers='[\"bob\"]'"
        result = run_hook("Bash", command)
        assert result["decision"] == "approve"

    def test_blocks_copilot_removal_with_heredoc(self):
        """Test that removing Copilot via heredoc is blocked."""
        command = """gh api repos/owner/repo/pulls/123/requested_reviewers -X DELETE --input - << EOF
{"reviewers":["Copilot"]}
EOF"""
        result = run_hook("Bash", command)
        assert result["decision"] == "block"
        assert "Copilot" in result["reason"]

    def test_allows_human_removal_with_heredoc(self):
        """Test that removing human reviewer via heredoc is approved."""
        command = """gh api repos/owner/repo/pulls/123/requested_reviewers -X DELETE --input - << EOF
{"reviewers":["alice"]}
EOF"""
        result = run_hook("Bash", command)
        assert result["decision"] == "approve"
