#!/usr/bin/env python3
"""Tests for cwd-check.py hook."""

import json
import subprocess
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "cwd-check.py"


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestCwdCheck:
    """Tests for cwd-check hook."""

    def test_approve_when_cwd_exists(self):
        """Should approve when current working directory exists."""
        result = run_hook({"transcript_path": "/some/path"})

        assert result["decision"] == "approve"
        assert "Current working directory exists" in result["reason"]

    def test_approve_when_stop_hook_active(self):
        """Should approve immediately when stop_hook_active is True."""
        result = run_hook({"transcript_path": "/some/path", "stop_hook_active": True})

        assert result["decision"] == "approve"
        assert "stop_hook_active" in result["reason"]

    def test_approve_with_empty_input(self):
        """Should approve with empty input (cwd still exists)."""
        result = run_hook({})

        assert result["decision"] == "approve"

    def test_reason_contains_cwd_path(self):
        """The reason should contain the current working directory path."""
        result = run_hook({"transcript_path": "/some/path"})

        assert result["decision"] == "approve"
        # The reason should contain some path (the actual cwd)
        assert "/" in result["reason"] or "\\" in result["reason"], "Reason should contain a path"


class TestCwdCheckHelpers:
    """Tests for helper functions in cwd-check."""

    def test_generate_handoff_message(self):
        """Test that handoff message is properly formatted."""
        # Import the module to test the helper function
        import importlib.util

        spec = importlib.util.spec_from_file_location("cwd_check", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        message = module.generate_handoff_message()

        assert "カレントディレクトリ消失" in message
        assert "Claude Code" in message
        assert "再起動" in message
