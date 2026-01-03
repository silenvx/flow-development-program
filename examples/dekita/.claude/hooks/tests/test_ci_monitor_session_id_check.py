"""Tests for ci-monitor-session-id-check hook.

Issue #2389: Warn when ci-monitor.py is called without --session-id.
"""

import json
import subprocess
import sys
from pathlib import Path

HOOK_PATH = (Path(__file__).parent.parent / "ci-monitor-session-id-check.py").resolve()


def run_hook(command: str) -> dict:
    """Run the hook with the given command and return the result."""
    hook_input = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": "test-session-id",
    }
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent.parent.parent,  # Project root
    )
    return json.loads(result.stdout)


class TestCiMonitorSessionIdCheck:
    """Tests for ci-monitor-session-id-check hook."""

    def test_non_ci_monitor_command_approved(self):
        """Non ci-monitor.py commands should be approved without warning."""
        result = run_hook("git status")
        assert result["decision"] == "approve"
        assert "message" not in result or result.get("message") == ""

    def test_ci_monitor_with_session_id_approved(self):
        """ci-monitor.py with --session-id should be approved."""
        result = run_hook(
            "python3 .claude/scripts/ci-monitor.py 123 "
            "--session-id 3f03a042-a9ef-44a2-839a-d17badc44b0a"
        )
        assert result["decision"] == "approve"
        # No warning message when --session-id is provided
        assert "警告" not in result.get("systemMessage", "")

    def test_ci_monitor_without_session_id_warns(self):
        """ci-monitor.py without --session-id should produce warning."""
        result = run_hook("python3 .claude/scripts/ci-monitor.py 123")
        assert result["decision"] == "approve"  # Not blocking, just warning
        assert "警告" in result.get("systemMessage", "")
        assert "--session-id" in result.get("systemMessage", "")

    def test_ci_monitor_with_other_flags_without_session_id_warns(self):
        """ci-monitor.py with other flags but without --session-id should warn."""
        result = run_hook("python3 .claude/scripts/ci-monitor.py 123 --early-exit --merge")
        assert result["decision"] == "approve"
        assert "警告" in result.get("systemMessage", "")
        assert "--session-id" in result.get("systemMessage", "")

    def test_ci_monitor_with_session_id_and_other_flags_approved(self):
        """ci-monitor.py with --session-id and other flags should be approved."""
        result = run_hook(
            "python3 .claude/scripts/ci-monitor.py 123 --session-id abc123 --early-exit"
        )
        assert result["decision"] == "approve"
        assert "警告" not in result.get("systemMessage", "")

    def test_ci_monitor_with_session_id_equals_format_approved(self):
        """ci-monitor.py with --session-id=value (equals format) should be approved."""
        result = run_hook("python3 .claude/scripts/ci-monitor.py 123 --session-id=abc123")
        assert result["decision"] == "approve"
        assert "警告" not in result.get("systemMessage", "")

    def test_ci_monitor_with_session_id_multiple_spaces_approved(self):
        """ci-monitor.py with multiple spaces after --session-id should be approved."""
        result = run_hook("python3 .claude/scripts/ci-monitor.py 123 --session-id    abc123")
        assert result["decision"] == "approve"
        assert "警告" not in result.get("systemMessage", "")

    def test_ci_monitor_with_session_id_equals_empty_warns(self):
        """ci-monitor.py with --session-id= (empty value) should warn."""
        result = run_hook("python3 .claude/scripts/ci-monitor.py 123 --session-id=")
        assert result["decision"] == "approve"  # Not blocking, just warning
        assert "警告" in result.get("systemMessage", "")

    def test_ci_monitor_with_session_id_equals_space_warns(self):
        """ci-monitor.py with --session-id= followed by space should warn."""
        result = run_hook("python3 .claude/scripts/ci-monitor.py 123 --session-id= --other-flag")
        assert result["decision"] == "approve"  # Not blocking, just warning
        assert "警告" in result.get("systemMessage", "")

    def test_ci_monitor_py_in_path_detected(self):
        """ci-monitor.py should be detected regardless of path prefix."""
        result = run_hook('python3 "$CLAUDE_PROJECT_DIR"/.claude/scripts/ci-monitor.py 123')
        assert result["decision"] == "approve"
        assert "警告" in result.get("systemMessage", "")

    def test_other_script_not_affected(self):
        """Other scripts should not be affected."""
        result = run_hook("python3 .claude/scripts/some-other-script.py 123")
        assert result["decision"] == "approve"
        assert "message" not in result or result.get("message") == ""
