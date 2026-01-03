#!/usr/bin/env python3
"""Tests for session-todo-check.py hook."""

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).parent.parent / "session-todo-check.py"


def run_hook(transcript_content: str) -> dict:
    """Run the hook with given transcript content."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(transcript_content)
        transcript_path = f.name

    try:
        hook_input = json.dumps({"transcript_path": transcript_path})
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input=hook_input,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return json.loads(result.stdout)
    finally:
        Path(transcript_path).unlink(missing_ok=True)


def create_transcript_with_todos(todos: list[dict]) -> str:
    """Create transcript content with TodoWrite tool call."""
    entry = {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "name": "TodoWrite",
                "input": {"todos": todos},
            }
        ],
    }
    return json.dumps(entry)


class TestSessionTodoCheck:
    """Tests for session-todo-check hook."""

    def test_no_transcript_path(self) -> None:
        """Test hook with no transcript path."""
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input=json.dumps({}),
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = json.loads(result.stdout)
        assert output["continue"] is True

    def test_no_todos(self) -> None:
        """Test hook with no todos in transcript."""
        transcript = json.dumps({"role": "assistant", "content": "Hello"})
        result = run_hook(transcript)
        assert result["continue"] is True

    def test_all_todos_completed(self) -> None:
        """Test hook with all todos completed."""
        todos = [
            {"content": "Task 1", "status": "completed", "activeForm": "Doing Task 1"},
            {"content": "Task 2 #123", "status": "completed", "activeForm": "Doing Task 2"},
        ]
        transcript = create_transcript_with_todos(todos)
        result = run_hook(transcript)
        assert result["continue"] is True
        assert "message" not in result

    def test_incomplete_todo_with_issue_ref(self) -> None:
        """Test hook with incomplete todo that has issue reference."""
        todos = [
            {"content": "Fix bug #1234", "status": "in_progress", "activeForm": "Fixing bug"},
            {
                "content": "Implement feature #5678",
                "status": "pending",
                "activeForm": "Implementing",
            },
        ]
        transcript = create_transcript_with_todos(todos)
        result = run_hook(transcript)
        assert result["continue"] is True
        assert "message" not in result

    def test_incomplete_todo_without_issue_ref(self) -> None:
        """Test hook with incomplete todo without issue reference."""
        todos = [
            {"content": "Fix something", "status": "in_progress", "activeForm": "Fixing"},
            {"content": "Add tests", "status": "pending", "activeForm": "Adding tests"},
        ]
        transcript = create_transcript_with_todos(todos)
        result = run_hook(transcript)
        assert result["continue"] is True
        assert "message" in result
        assert "未完了のTODO" in result["message"]
        assert "Fix something" in result["message"]
        assert "Add tests" in result["message"]

    def test_mixed_todos(self) -> None:
        """Test hook with mixed todos."""
        todos = [
            {"content": "Completed task", "status": "completed", "activeForm": "Done"},
            {"content": "In progress #999", "status": "in_progress", "activeForm": "Working"},
            {"content": "Pending without issue", "status": "pending", "activeForm": "Waiting"},
        ]
        transcript = create_transcript_with_todos(todos)
        result = run_hook(transcript)
        assert result["continue"] is True
        assert "message" in result
        assert "Pending without issue" in result["message"]
        # Issue referenced one should not be in warning
        assert "In progress #999" not in result["message"]

    def test_latest_todos_used(self) -> None:
        """Test that only the latest TodoWrite is used."""
        # First TodoWrite with incomplete todo
        entry1 = {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "TodoWrite",
                    "input": {
                        "todos": [{"content": "Old task", "status": "pending", "activeForm": "Old"}]
                    },
                }
            ],
        }
        # Second TodoWrite with completed todo
        entry2 = {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "TodoWrite",
                    "input": {
                        "todos": [
                            {"content": "New task", "status": "completed", "activeForm": "New"}
                        ]
                    },
                }
            ],
        }
        transcript = json.dumps(entry1) + "\n" + json.dumps(entry2)
        result = run_hook(transcript)
        assert result["continue"] is True
        assert "message" not in result  # Latest is completed, so no warning

    def test_issue_number_patterns(self) -> None:
        """Test various issue number patterns are recognized."""
        patterns = [
            "#123",
            "Issue #456",
            "fixes #789",
            "関連: #111",
        ]
        for pattern in patterns:
            todos = [{"content": f"Task {pattern}", "status": "pending", "activeForm": "Task"}]
            transcript = create_transcript_with_todos(todos)
            result = run_hook(transcript)
            assert result["continue"] is True
            assert "message" not in result, f"Pattern {pattern} should be recognized"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
