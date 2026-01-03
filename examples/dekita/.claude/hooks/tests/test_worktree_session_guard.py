#!/usr/bin/env python3
"""Tests for worktree-session-guard.py hook."""

import json
import os
import subprocess
import sys
from pathlib import Path

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "worktree-session-guard.py"


def run_hook(input_data: dict, env: dict = None) -> tuple[int, str, str]:
    """Run the hook with given input and return (exit_code, stdout, stderr)."""
    process_env = os.environ.copy()
    if env:
        process_env.update(env)

    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        env=process_env,
    )
    return result.returncode, result.stdout, result.stderr


class TestWorktreeSessionGuard:
    """Tests for worktree-session-guard hook."""

    def test_exit_zero_always(self):
        """Hook should always exit with code 0."""
        test_cases = [
            {"tool_name": "Edit", "tool_input": {"file_path": "/some/project/file.txt"}},
            {"tool_name": "Write", "tool_input": {"file_path": "/some/project/file.txt"}},
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            {},
        ]

        for input_data in test_cases:
            exit_code, _, _ = run_hook(input_data)
            assert exit_code == 0

    def test_skip_non_edit_tools(self):
        """Should skip non-Edit/Write tools."""
        exit_code, stdout, _ = run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}})

        assert exit_code == 0
        # Should not output anything (skipped)

    def test_approve_file_not_in_worktree(self):
        """Should approve when file is not in a worktree."""
        exit_code, stdout, _ = run_hook(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "/some/project/src/file.ts"},
            }
        )

        assert exit_code == 0
        # Should be approved

    def test_approve_file_in_worktree_no_marker(self):
        """Should approve when worktree has no session marker."""
        # Create a temp worktree path without marker
        exit_code, stdout, _ = run_hook(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "/project/.worktrees/issue-123/src/file.ts"},
            }
        )

        assert exit_code == 0
        # Should be approved (no marker = legacy worktree)

    def test_approve_same_session(self, tmp_path):
        """Should approve when session matches marker."""
        # Create worktree with marker
        worktree = tmp_path / ".worktrees" / "issue-456"
        worktree.mkdir(parents=True)
        marker = worktree / ".claude-session"
        marker.write_text("test-session-123")

        # Mock session ID to match
        exit_code, stdout, _ = run_hook(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(worktree / "src" / "file.ts")},
                "session_id": "test-session-123",
            }
        )

        assert exit_code == 0

    def test_block_different_session(self, tmp_path):
        """Should block when session differs from marker."""
        # Create worktree with marker from different session
        worktree = tmp_path / ".worktrees" / "issue-789"
        worktree.mkdir(parents=True)
        marker = worktree / ".claude-session"
        marker_data = {
            "session_id": "other-session-abc",
            "created_at": "2025-12-30T09:30:00+00:00",
        }
        marker.write_text(json.dumps(marker_data))

        exit_code, stdout, _ = run_hook(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(worktree / "src" / "file.ts")},
                "session_id": "my-session-xyz",
            }
        )

        assert exit_code == 0
        result = json.loads(stdout)
        assert result.get("decision") == "block"
        assert "別のセッション" in result.get("reason", "")

    def test_approve_same_session_json_format(self, tmp_path):
        """Should approve when session matches JSON format marker."""
        # Create worktree with JSON format marker
        worktree = tmp_path / ".worktrees" / "issue-json-1"
        worktree.mkdir(parents=True)
        marker = worktree / ".claude-session"
        marker_data = {
            "session_id": "test-session-json-123",
            "created_at": "2025-12-30T09:30:00+00:00",
        }
        marker.write_text(json.dumps(marker_data))

        exit_code, stdout, _ = run_hook(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(worktree / "src" / "file.ts")},
                "session_id": "test-session-json-123",
            }
        )

        assert exit_code == 0

    def test_block_different_session_json_format(self, tmp_path):
        """Should block when session differs from JSON format marker."""
        # Create worktree with JSON format marker from different session
        worktree = tmp_path / ".worktrees" / "issue-json-2"
        worktree.mkdir(parents=True)
        marker = worktree / ".claude-session"
        marker_data = {
            "session_id": "other-session-json-abc",
            "created_at": "2025-12-30T08:00:00+00:00",
        }
        marker.write_text(json.dumps(marker_data))

        exit_code, stdout, _ = run_hook(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(worktree / "src" / "file.ts")},
                "session_id": "my-session-xyz",
            }
        )

        assert exit_code == 0
        result = json.loads(stdout)
        assert result.get("decision") == "block"
        assert "別のセッション" in result.get("reason", "")


class TestForkSessionWorktreeAccess:
    """Tests for Issue #2331: Fork-session worktree access."""

    def _create_transcript(self, tmp_path: Path, entries: list[dict]) -> Path:
        """Create a transcript JSONL file with the given entries."""
        transcript_path = tmp_path / "test-transcript.jsonl"
        with transcript_path.open("w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        return transcript_path

    def test_child_can_access_parent_worktree(self, tmp_path):
        """Child session should access parent session's worktree."""
        parent_session = "parent-session-001"
        child_session = "child-session-002"

        # Create worktree with parent's marker
        worktree = tmp_path / ".worktrees" / "issue-parent"
        worktree.mkdir(parents=True)
        marker = worktree / ".claude-session"
        marker.write_text(json.dumps({"session_id": parent_session}))

        # Create transcript with parent first, then child
        transcript = self._create_transcript(
            tmp_path,
            [
                {"type": "user", "sessionId": parent_session},
                {"type": "user", "sessionId": child_session},
            ],
        )

        exit_code, stdout, _ = run_hook(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(worktree / "src" / "file.ts")},
                "session_id": child_session,
                "transcript_path": str(transcript),
            },
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert exit_code == 0
        result = json.loads(stdout)
        assert result.get("decision") == "approve"
        assert "Ancestor" in result.get("systemMessage", "")

    def test_sibling_cannot_access_sibling_worktree(self, tmp_path):
        """Sibling sessions should NOT access each other's worktrees.

        Real fork scenario:
        - Parent A forks to child B: B's transcript = [A, B]
        - Parent A forks to child C: C's transcript = [A, C]
        B and C are siblings, and B's session ID is NOT in C's transcript.
        So when C tries to access B's worktree, B is not in ancestry.
        """
        parent_session = "parent-session-001"
        sibling_a = "sibling-session-A"
        sibling_b = "sibling-session-B"

        # Create worktree with sibling A's marker
        worktree = tmp_path / ".worktrees" / "issue-sibling-a"
        worktree.mkdir(parents=True)
        marker = worktree / ".claude-session"
        marker.write_text(json.dumps({"session_id": sibling_a}))

        # sibling_b's transcript does NOT contain sibling_a (they're true siblings)
        transcript = self._create_transcript(
            tmp_path,
            [
                {"type": "user", "sessionId": parent_session},
                {"type": "user", "sessionId": sibling_b},  # No sibling_a here
            ],
        )

        exit_code, stdout, _ = run_hook(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(worktree / "src" / "file.ts")},
                "session_id": sibling_b,  # sibling_b trying to access sibling_a's worktree
                "transcript_path": str(transcript),
            },
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert exit_code == 0
        result = json.loads(stdout)
        # Should be blocked because sibling_a is not in sibling_b's ancestry
        assert result.get("decision") == "block"

    def test_grandchild_can_access_grandparent_worktree(self, tmp_path):
        """Grandchild session should access grandparent's worktree."""
        grandparent = "grandparent-session"
        parent = "parent-session"
        grandchild = "grandchild-session"

        # Create worktree with grandparent's marker
        worktree = tmp_path / ".worktrees" / "issue-grandparent"
        worktree.mkdir(parents=True)
        marker = worktree / ".claude-session"
        marker.write_text(json.dumps({"session_id": grandparent}))

        # Transcript: grandparent -> parent -> grandchild
        transcript = self._create_transcript(
            tmp_path,
            [
                {"type": "user", "sessionId": grandparent},
                {"type": "user", "sessionId": parent},
                {"type": "user", "sessionId": grandchild},
            ],
        )

        exit_code, stdout, _ = run_hook(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(worktree / "src" / "file.ts")},
                "session_id": grandchild,
                "transcript_path": str(transcript),
            },
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert exit_code == 0
        result = json.loads(stdout)
        assert result.get("decision") == "approve"
        assert "Ancestor" in result.get("systemMessage", "")

    def test_no_transcript_falls_back_to_block(self, tmp_path):
        """Without transcript, different session should be blocked."""
        other_session = "other-session-xyz"
        current_session = "current-session-abc"

        # Create worktree with other session's marker
        worktree = tmp_path / ".worktrees" / "issue-no-transcript"
        worktree.mkdir(parents=True)
        marker = worktree / ".claude-session"
        marker.write_text(json.dumps({"session_id": other_session}))

        exit_code, stdout, _ = run_hook(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(worktree / "src" / "file.ts")},
                "session_id": current_session,
                # No transcript_path provided
            },
        )

        assert exit_code == 0
        result = json.loads(stdout)
        assert result.get("decision") == "block"


class TestGetWorktreeFromPath:
    """Tests for get_worktree_from_path function."""

    def test_extract_worktree_path(self):
        """Should correctly extract worktree path from file path."""
        from conftest import load_hook_module

        hook = load_hook_module("worktree-session-guard")

        # Test with .worktrees in path
        result = hook.get_worktree_from_path("/project/.worktrees/issue-123/src/file.ts")
        assert result is not None
        assert result == Path("/project/.worktrees/issue-123")

        # Test without .worktrees
        result = hook.get_worktree_from_path("/project/src/file.ts")
        assert result is None

        # Test with nested .worktrees (should take first)
        result = hook.get_worktree_from_path(
            "/project/.worktrees/issue-456/.worktrees/nested/file.ts"
        )
        assert result is not None
        assert result == Path("/project/.worktrees/issue-456")
