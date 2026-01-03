#!/usr/bin/env python3
"""Unit tests for reflection-progress-tracker.py"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Dynamic import for hyphenated module name
HOOK_PATH = Path(__file__).parent.parent / "reflection-progress-tracker.py"
_spec = importlib.util.spec_from_file_location("reflection_progress_tracker", HOOK_PATH)
reflection_progress_tracker = importlib.util.module_from_spec(_spec)
sys.modules["reflection_progress_tracker"] = reflection_progress_tracker
_spec.loader.exec_module(reflection_progress_tracker)

is_issue_create_command = reflection_progress_tracker.is_issue_create_command
extract_issue_number = reflection_progress_tracker.extract_issue_number
load_reflection_state = reflection_progress_tracker.load_reflection_state
save_reflection_state = reflection_progress_tracker.save_reflection_state


class TestIsIssueCreateCommand:
    """Tests for is_issue_create_command function."""

    def test_detects_simple_issue_create(self):
        """Should detect simple gh issue create command."""
        assert is_issue_create_command("gh issue create")

    def test_detects_issue_create_with_options(self):
        """Should detect issue create with options."""
        assert is_issue_create_command("gh issue create --title 'Bug' --body 'desc'")
        assert is_issue_create_command("gh issue create -t 'Title'")
        assert is_issue_create_command("gh issue create --label bug")

    def test_ignores_other_commands(self):
        """Should not flag non-issue-create commands."""
        assert not is_issue_create_command("gh issue view 123")
        assert not is_issue_create_command("gh issue list")
        assert not is_issue_create_command("gh pr create")
        assert not is_issue_create_command("git issue create")

    def test_handles_empty_string(self):
        """Should handle empty string."""
        assert not is_issue_create_command("")


class TestExtractIssueNumber:
    """Tests for extract_issue_number function."""

    def test_extracts_from_url(self):
        """Should extract issue number from GitHub URL."""
        assert extract_issue_number("https://github.com/owner/repo/issues/123") == "123"
        assert extract_issue_number("https://github.com/foo/bar/issues/456") == "456"

    def test_extracts_from_multiline_output(self):
        """Should extract issue number from multiline output."""
        output = """
Creating issue...
https://github.com/owner/repo/issues/789
Done!
"""
        assert extract_issue_number(output) == "789"

    def test_returns_none_for_no_match(self):
        """Should return None when no issue number found."""
        assert extract_issue_number("No issue URL here") is None
        assert extract_issue_number("") is None
        assert extract_issue_number("https://github.com/owner/repo/pull/123") is None


class TestReflectionStatePersistence:
    """Tests for reflection state load/save functions."""

    def test_load_returns_default_for_missing_file(self):
        """Should return default state when file doesn't exist."""
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(reflection_progress_tracker, "SESSION_DIR", Path(tmpdir) / "missing"):
                mock_ctx = MagicMock()
                mock_ctx.get_session_id.return_value = "test-session"
                with patch.object(reflection_progress_tracker, "_ctx", mock_ctx):
                    state = load_reflection_state()
                    assert state["reflection_required"] is False
                    assert state["merged_prs"] == []
                    assert state["reflection_done"] is False
                    assert state["issues_created"] == []

    def test_save_and_load_roundtrip(self):
        """Should save and load state correctly."""
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "claude-hooks"
            with patch.object(reflection_progress_tracker, "SESSION_DIR", session_dir):
                mock_ctx = MagicMock()
                mock_ctx.get_session_id.return_value = "test-session"
                with patch.object(reflection_progress_tracker, "_ctx", mock_ctx):
                    test_state = {
                        "reflection_required": True,
                        "merged_prs": ["100"],
                        "reflection_done": False,
                        "issues_created": ["200", "201"],
                    }
                    save_reflection_state(test_state)
                    loaded = load_reflection_state()
                    assert loaded["reflection_required"] is True
                    assert loaded["issues_created"] == ["200", "201"]


class TestMainIntegration:
    """Integration tests for main function."""

    def test_non_bash_tool_passthrough(self, capsys):
        """Should pass through for non-Bash tools."""
        input_data = {"tool_name": "Read", "tool_input": {}, "tool_result": {}}
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            reflection_progress_tracker.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert result["continue"] is True

    def test_non_issue_create_passthrough(self, capsys):
        """Should pass through for non-issue-create commands."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue view 123"},
            "tool_result": {"stdout": "Issue details", "exit_code": 0},
        }
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            reflection_progress_tracker.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert result["continue"] is True

    def test_successful_issue_create_tracks_issue(self, capsys):
        """Should track issue number on successful issue create."""
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "claude-hooks"
            input_data = {
                "tool_name": "Bash",
                "tool_input": {"command": "gh issue create --title 'Bug'"},
                "tool_result": {
                    "stdout": "https://github.com/owner/repo/issues/999",
                    "exit_code": 0,
                },
                "session_id": "test-session",
            }
            with patch.object(reflection_progress_tracker, "SESSION_DIR", session_dir):
                with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                    reflection_progress_tracker.main()
                    captured = capsys.readouterr()
                    result = json.loads(captured.out)
                    assert result["continue"] is True

                    # Verify issue was tracked - need to mock _ctx for load_reflection_state
                    mock_ctx = MagicMock()
                    mock_ctx.get_session_id.return_value = "test-session"
                    with patch.object(reflection_progress_tracker, "_ctx", mock_ctx):
                        state = load_reflection_state()
                        assert "999" in state.get("issues_created", [])

    def test_failed_issue_create_no_tracking(self, capsys):
        """Should not track issue on failed create."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create"},
            "tool_result": {"stdout": "Error", "exit_code": 1},
        }
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            reflection_progress_tracker.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert result["continue"] is True

    def test_does_not_duplicate_issue_numbers(self, capsys):
        """Should not add duplicate issue numbers."""
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "claude-hooks"
            with patch.object(reflection_progress_tracker, "SESSION_DIR", session_dir):
                # First issue create
                input_data = {
                    "tool_name": "Bash",
                    "tool_input": {"command": "gh issue create"},
                    "tool_result": {
                        "stdout": "https://github.com/owner/repo/issues/100",
                        "exit_code": 0,
                    },
                    "session_id": "test-session",
                }
                with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                    reflection_progress_tracker.main()

                # Same issue again
                with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                    reflection_progress_tracker.main()
                    capsys.readouterr()

                # Verify state - need to mock _ctx for load_reflection_state
                mock_ctx = MagicMock()
                mock_ctx.get_session_id.return_value = "test-session"
                with patch.object(reflection_progress_tracker, "_ctx", mock_ctx):
                    state = load_reflection_state()
                    # Should only have one "100"
                    assert state.get("issues_created", []).count("100") == 1
