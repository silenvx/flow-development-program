#!/usr/bin/env python3
"""Tests for immediate-action-check.py hook.

Issue #2219: Ensures reflection is executed after PR merges.
Issue #2269: Changed to detect PR merges directly in transcript.
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

spec = importlib.util.spec_from_file_location(
    "immediate_action_check",
    Path(__file__).parent.parent / "immediate-action-check.py",
)
immediate_action_check = importlib.util.module_from_spec(spec)  # type: ignore
spec.loader.exec_module(immediate_action_check)  # type: ignore


def make_bash_tool_use(tool_id: str, command: str) -> dict:
    """Helper to create a Bash tool_use block."""
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": "Bash",
        "input": {"command": command},
    }


def make_tool_result(tool_id: str, stdout: str, exit_code: int = 0) -> dict:
    """Helper to create a tool_result block."""
    return {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "content": [{"type": "text", "text": stdout}],
        "exit_code": exit_code,
    }


def make_skill_call(skill: str) -> dict:
    """Helper to create a Skill tool_use block."""
    return {
        "type": "tool_use",
        "name": "Skill",
        "input": {"skill": skill},
    }


class TestFindPrMerges:
    """Tests for find_pr_merges function."""

    def test_empty_transcript(self) -> None:
        """Empty transcript returns empty list."""
        assert immediate_action_check.find_pr_merges([]) == []

    def test_no_merges(self) -> None:
        """Transcript without merges returns empty list."""
        transcript = [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Hi there!"}]},
        ]
        assert immediate_action_check.find_pr_merges(transcript) == []

    def test_finds_successful_merge(self) -> None:
        """Finds successful PR merge in transcript."""
        transcript = [
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-1", "gh pr merge 123")],
            },
            {
                "role": "user",
                "content": [
                    make_tool_result(
                        "tool-1",
                        "✓ Merged pull request #123",
                        exit_code=0,
                    )
                ],
            },
        ]
        result = immediate_action_check.find_pr_merges(transcript)
        assert result == [(0, "123")]

    def test_ignores_failed_merge(self) -> None:
        """Ignores failed merge commands."""
        transcript = [
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-1", "gh pr merge 123")],
            },
            {
                "role": "user",
                "content": [
                    make_tool_result(
                        "tool-1",
                        "error: pull request is not mergeable",
                        exit_code=1,
                    )
                ],
            },
        ]
        result = immediate_action_check.find_pr_merges(transcript)
        assert result == []

    def test_finds_multiple_merges(self) -> None:
        """Finds multiple successful merges."""
        transcript = [
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-1", "gh pr merge 100")],
            },
            {
                "role": "user",
                "content": [make_tool_result("tool-1", "✓ Merged pull request #100")],
            },
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-2", "gh pr merge 200")],
            },
            {
                "role": "user",
                "content": [make_tool_result("tool-2", "✓ Merged pull request #200")],
            },
        ]
        result = immediate_action_check.find_pr_merges(transcript)
        assert result == [(0, "100"), (2, "200")]

    def test_extracts_pr_number_from_stdout(self) -> None:
        """Extracts PR number from stdout when not in command (gh pr merge --squash)."""
        transcript = [
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-1", "gh pr merge --squash")],
            },
            {
                "role": "user",
                "content": [make_tool_result("tool-1", "✓ Merged pull request #456")],
            },
        ]
        result = immediate_action_check.find_pr_merges(transcript)
        assert result == [(0, "456")]


class TestFindSkillCallsAfter:
    """Tests for find_skill_calls_after function."""

    def test_no_skill_calls(self) -> None:
        """Returns False when no Skill calls exist."""
        transcript = [
            {"role": "assistant", "content": [{"type": "text", "text": "Done"}]},
        ]
        assert not immediate_action_check.find_skill_calls_after(transcript, 0, "reflect")

    def test_finds_skill_call_after_index(self) -> None:
        """Finds Skill call after the specified index."""
        transcript = [
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-1", "gh pr merge 123")],
            },
            {
                "role": "assistant",
                "content": [make_skill_call("reflect")],
            },
        ]
        assert immediate_action_check.find_skill_calls_after(transcript, 0, "reflect")

    def test_ignores_skill_call_before_index(self) -> None:
        """Ignores Skill call before the specified index."""
        transcript = [
            {
                "role": "assistant",
                "content": [make_skill_call("reflect")],
            },
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-1", "gh pr merge 123")],
            },
        ]
        # Skill call is at index 0, merge is at index 1
        # Looking after index 1, no Skill call exists
        assert not immediate_action_check.find_skill_calls_after(transcript, 1, "reflect")

    def test_ignores_different_skill(self) -> None:
        """Ignores Skill calls with different skill name."""
        transcript = [
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-1", "gh pr merge 123")],
            },
            {
                "role": "assistant",
                "content": [make_skill_call("commit")],
            },
        ]
        assert not immediate_action_check.find_skill_calls_after(transcript, 0, "reflect")


class TestCheckPostMergeReflection:
    """Tests for check_post_merge_reflection function."""

    def test_no_merges_returns_empty(self) -> None:
        """Returns empty list when no merges exist."""
        transcript = [
            {"role": "assistant", "content": [{"type": "text", "text": "Done"}]},
        ]
        assert immediate_action_check.check_post_merge_reflection(transcript) == []

    def test_reflected_merge_returns_empty(self) -> None:
        """Returns empty list when merge has reflection."""
        transcript = [
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-1", "gh pr merge 123")],
            },
            {
                "role": "user",
                "content": [make_tool_result("tool-1", "✓ Merged pull request #123")],
            },
            {
                "role": "assistant",
                "content": [make_skill_call("reflect")],
            },
        ]
        assert immediate_action_check.check_post_merge_reflection(transcript) == []

    def test_unreflected_merge_returns_pr_number(self) -> None:
        """Returns PR number when merge lacks reflection."""
        transcript = [
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-1", "gh pr merge 123")],
            },
            {
                "role": "user",
                "content": [make_tool_result("tool-1", "✓ Merged pull request #123")],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Done!"}],
            },
        ]
        assert immediate_action_check.check_post_merge_reflection(transcript) == ["123"]

    def test_multiple_unreflected_merges(self) -> None:
        """Returns all PR numbers lacking reflection."""
        transcript = [
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-1", "gh pr merge 100")],
            },
            {
                "role": "user",
                "content": [make_tool_result("tool-1", "✓ Merged pull request #100")],
            },
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-2", "gh pr merge 200")],
            },
            {
                "role": "user",
                "content": [make_tool_result("tool-2", "✓ Merged pull request #200")],
            },
        ]
        result = immediate_action_check.check_post_merge_reflection(transcript)
        assert set(result) == {"100", "200"}

    def test_partial_reflection(self) -> None:
        """One reflection covers only the preceding merge, not subsequent merges."""
        transcript = [
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-1", "gh pr merge 100")],
            },
            {
                "role": "user",
                "content": [make_tool_result("tool-1", "✓ Merged pull request #100")],
            },
            {
                "role": "assistant",
                "content": [make_skill_call("reflect")],
            },
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-2", "gh pr merge 200")],
            },
            {
                "role": "user",
                "content": [make_tool_result("tool-2", "✓ Merged pull request #200")],
            },
        ]
        # First merge (100) has reflect after it
        # Second merge (200) does not have reflect after it
        result = immediate_action_check.check_post_merge_reflection(transcript)
        assert result == ["200"]


class TestMain:
    """Tests for main function."""

    def test_approves_when_no_transcript_path(self) -> None:
        """Approves when transcript_path is not provided (fail-open, Issue #2269)."""
        input_data = {"session_id": "test-123"}

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch.object(immediate_action_check, "print") as mock_print:
                immediate_action_check.main()

            # Check what was printed - should approve (fail-open)
            calls = [str(c) for c in mock_print.call_args_list]
            assert any('"decision": "approve"' in c for c in calls)

    def test_approves_when_transcript_load_fails(self) -> None:
        """Approves when transcript file cannot be loaded (fail-open, Issue #2269/2274).

        Changed from fail-closed to fail-open because invalid transcript paths
        are a Claude Code issue, not a hook issue. Blocking is too strict.
        """
        input_data = {
            "session_id": "test-123",
            "transcript_path": "/nonexistent/path/transcript.json",
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch.object(immediate_action_check, "print") as mock_print:
                immediate_action_check.main()

            # Check what was printed - should approve (fail-open, Issue #2269/2274)
            calls = [str(c) for c in mock_print.call_args_list]
            assert any('"decision": "approve"' in c for c in calls)

    def test_approves_when_stop_hook_active(self) -> None:
        """Approves when stop_hook_active is True (prevents infinite loops)."""
        input_data = {
            "session_id": "test-123",
            "stop_hook_active": True,
            "transcript_path": "/tmp/transcript.json",
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch.object(immediate_action_check, "print") as mock_print:
                immediate_action_check.main()

                # Should approve without checking transcript
                calls = [str(c) for c in mock_print.call_args_list]
                assert any('"decision": "approve"' in c for c in calls)

    def test_blocks_when_merge_not_reflected(self) -> None:
        """Blocks when PR merge lacks reflection."""
        transcript = [
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-1", "gh pr merge 123")],
            },
            {
                "role": "user",
                "content": [make_tool_result("tool-1", "✓ Merged pull request #123")],
            },
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(transcript, f)
            temp_path = f.name

        try:
            input_data = {
                "session_id": "test-123",
                "transcript_path": temp_path,
            }

            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                with patch.object(immediate_action_check, "print") as mock_print:
                    immediate_action_check.main()

                    # Should block
                    calls = [str(c) for c in mock_print.call_args_list]
                    assert any('"decision": "block"' in c for c in calls)
                    assert any("#123" in c for c in calls)
        finally:
            Path(temp_path).unlink()

    def test_approves_when_merge_reflected(self) -> None:
        """Approves when PR merge has reflection."""
        transcript = [
            {
                "role": "assistant",
                "content": [make_bash_tool_use("tool-1", "gh pr merge 123")],
            },
            {
                "role": "user",
                "content": [make_tool_result("tool-1", "✓ Merged pull request #123")],
            },
            {
                "role": "assistant",
                "content": [make_skill_call("reflect")],
            },
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(transcript, f)
            temp_path = f.name

        try:
            input_data = {
                "session_id": "test-123",
                "transcript_path": temp_path,
            }

            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                with patch.object(immediate_action_check, "print") as mock_print:
                    immediate_action_check.main()

                    # Should approve
                    calls = [str(c) for c in mock_print.call_args_list]
                    assert any('"decision": "approve"' in c for c in calls)
        finally:
            Path(temp_path).unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
