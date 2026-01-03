#!/usr/bin/env python3
"""Tests for doc-edit-check hook."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Issue #2496: Removed session_module import
# Global state (_HOOK_SESSION_ID, set_hook_session_id) has been removed
from doc_edit_check import (
    get_confirmation_file_path,
    is_confirmed_in_session,
    load_confirmed_files,
    main,
    mark_as_confirmed,
    matches_target_pattern,
)
from lib.session import HookContext, create_hook_context


def _create_ctx(session_id: str) -> HookContext:
    """Create a HookContext with the given session ID."""
    return create_hook_context({"session_id": session_id})


# Issue #2496: Removed reset_hook_session_id fixture
# Global state no longer exists - HookContext is used for session tracking


class TestMatchesTargetPattern:
    """Tests for matches_target_pattern function."""

    def test_matches_skills_md(self):
        """Skills MD files should match."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/project"}):
            assert matches_target_pattern("/project/.claude/skills/test.md") is True

    def test_matches_prompts_md(self):
        """Prompts MD files should match."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/project"}):
            assert matches_target_pattern("/project/.claude/prompts/guide.md") is True

    def test_matches_nested_skills(self):
        """Nested skills MD files should match."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/project"}):
            assert matches_target_pattern("/project/.claude/skills/sub/nested.md") is True

    def test_matches_agents_md(self):
        """AGENTS.md should match."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/project"}):
            assert matches_target_pattern("/project/AGENTS.md") is True

    def test_does_not_match_readme(self):
        """README.md should not match."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/project"}):
            assert matches_target_pattern("/project/README.md") is False

    def test_does_not_match_changelog(self):
        """CHANGELOG.md should not match."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/project"}):
            assert matches_target_pattern("/project/CHANGELOG.md") is False

    def test_does_not_match_docs(self):
        """docs/ MD files should not match."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/project"}):
            assert matches_target_pattern("/project/docs/guide.md") is False

    def test_does_not_match_outside_project(self):
        """Files outside project should not match."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/project"}):
            assert matches_target_pattern("/other/AGENTS.md") is False

    def test_does_not_match_similar_prefix_project(self):
        """Files in /project-other should not match /project."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/project"}):
            assert matches_target_pattern("/project-other/AGENTS.md") is False

    def test_does_not_match_non_md(self):
        """Non-MD files should not match."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/project"}):
            assert matches_target_pattern("/project/.claude/skills/test.py") is False


class TestSessionConfirmation:
    """Tests for session-scoped confirmation tracking."""

    def _prepare_session(self, session_id: str) -> HookContext:
        """Set up session and clean up any existing confirmation file.

        Issue #2326: Extracted common test setup pattern.
        Issue #2456: Changed to return HookContext instead of using global state.
        Issue #2496: Removed set_hook_session_id() call - no longer using global state.

        Args:
            session_id: The session ID to use for this test.

        Returns:
            HookContext for use in tests.
        """
        ctx = _create_ctx(session_id)
        conf_file = get_confirmation_file_path(ctx)
        if conf_file.exists():
            conf_file.unlink()
        return ctx

    def teardown_method(self):
        """Clean up test confirmation files."""
        hooks_dir = Path(tempfile.gettempdir()) / "claude-hooks"
        for sid in ["test-session", "session-1", "session-2", "test"]:
            conf_file = hooks_dir / f"doc-edit-confirmed-{sid}.json"
            if conf_file.exists():
                conf_file.unlink()

    def test_is_confirmed_returns_false_initially(self):
        """Files should not be confirmed initially."""
        ctx = self._prepare_session("test-session")
        assert is_confirmed_in_session(ctx, "/project/AGENTS.md") is False

    def test_mark_as_confirmed_and_check(self):
        """Marked files should be confirmed."""
        ctx = self._prepare_session("test-session")
        mark_as_confirmed(ctx, "/project/AGENTS.md")
        assert is_confirmed_in_session(ctx, "/project/AGENTS.md") is True

    def test_different_sessions_are_isolated(self):
        """Different sessions should have separate confirmed files."""
        ctx1 = self._prepare_session("session-1")
        mark_as_confirmed(ctx1, "/project/AGENTS.md")

        ctx2 = self._prepare_session("session-2")
        assert is_confirmed_in_session(ctx2, "/project/AGENTS.md") is False

    def test_multiple_files_in_session(self):
        """Multiple files can be confirmed in same session."""
        ctx = self._prepare_session("test-session")
        mark_as_confirmed(ctx, "/project/AGENTS.md")
        mark_as_confirmed(ctx, "/project/.claude/skills/test.md")
        assert is_confirmed_in_session(ctx, "/project/AGENTS.md") is True
        assert is_confirmed_in_session(ctx, "/project/.claude/skills/test.md") is True
        assert is_confirmed_in_session(ctx, "/project/other.md") is False


class TestMain:
    """Tests for main function."""

    def setup_method(self):
        """Clean up test confirmation files before each test."""
        hooks_dir = Path(tempfile.gettempdir()) / "claude-hooks"
        for sid in ["test", "test-session"]:
            conf_file = hooks_dir / f"doc-edit-confirmed-{sid}.json"
            if conf_file.exists():
                conf_file.unlink()

    def teardown_method(self):
        """Clean up test confirmation files after each test."""
        hooks_dir = Path(tempfile.gettempdir()) / "claude-hooks"
        for sid in ["test", "test-session"]:
            conf_file = hooks_dir / f"doc-edit-confirmed-{sid}.json"
            if conf_file.exists():
                conf_file.unlink()

    def test_warns_on_first_edit_to_target(self, capsys):
        """Should warn on first edit to target document."""
        # Issue #2496: Include session_id in input JSON instead of using global state
        input_data = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/project/AGENTS.md"},
            "session_id": "test",
        }

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/project"}):
            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                main()

        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "approve"
        assert "仕様ドキュメント編集の確認" in result.get("systemMessage", "")

    def test_no_warn_on_second_edit(self, capsys):
        """Should not warn on second edit in same session."""
        # Issue #2496: Include session_id in input JSON instead of using global state
        input_data = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/project/AGENTS.md"},
            "session_id": "test",
        }

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/project"}):
            # First edit - should warn
            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                main()

            # Second edit - should be confirmed
            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                main()

        output = capsys.readouterr().out
        lines = output.strip().split("\n")
        second_result = json.loads(lines[-1])
        assert "セッション内で確認済み" in second_result.get("systemMessage", "")

    def test_no_warn_for_non_target(self, capsys):
        """Should not warn for non-target files."""
        # Issue #2496: Include session_id in input JSON instead of using global state
        input_data = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/project/README.md"},
            "session_id": "test",
        }

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/project"}):
            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                main()

        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "approve"
        assert "systemMessage" not in result or "確認" not in result.get("systemMessage", "")

    def test_no_file_path(self, capsys):
        """Should skip when no file path provided."""
        # Issue #2496: Include session_id in input JSON instead of using global state
        input_data = {
            "tool_name": "Edit",
            "tool_input": {},
            "session_id": "test",
        }

        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            main()

        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "approve"
        assert "パス未指定" in result.get("systemMessage", "")

    def test_warns_on_write_tool(self, capsys):
        """Should warn when using Write tool on target document."""
        # Issue #2496: Include session_id in input JSON instead of using global state
        input_data = {
            "tool_name": "Write",
            "tool_input": {"file_path": "/project/.claude/skills/new.md"},
            "session_id": "test",
        }

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/project"}):
            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                main()

        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "approve"
        assert "仕様ドキュメント編集の確認" in result.get("systemMessage", "")


class TestCorruptedFiles:
    """Tests for corrupted confirmation file handling."""

    def teardown_method(self):
        """Clean up test confirmation files."""
        hooks_dir = Path(tempfile.gettempdir()) / "claude-hooks"
        conf_file = hooks_dir / "doc-edit-confirmed-corrupted-test.json"
        if conf_file.exists():
            conf_file.unlink()

    def test_corrupted_json_returns_empty_set(self):
        """Corrupted JSON file should be treated as empty."""
        # Issue #2456: Changed to use HookContext for DI pattern
        # Issue #2496: Removed set_hook_session_id() call - no longer using global state
        ctx = _create_ctx("corrupted-test")
        # Create corrupted JSON file
        conf_file = get_confirmation_file_path(ctx)
        conf_file.parent.mkdir(parents=True, exist_ok=True)
        conf_file.write_text("{ invalid json }")

        # Should return empty set, not raise exception
        result = load_confirmed_files(ctx)
        assert result == set()
        assert is_confirmed_in_session(ctx, "/project/AGENTS.md") is False


class TestExceptionHandling:
    """Tests for exception handling in main function."""

    def test_parse_error_approves_with_reason(self, capsys):
        """Should approve and log error when parse_hook_input fails."""
        # Issue #2496: Removed set_hook_session_id() call - no longer using global state
        # This test mocks parse_hook_input to raise, so session_id is not relevant
        with patch(
            "doc_edit_check.parse_hook_input",
            side_effect=ValueError("Invalid input"),
        ):
            main()

        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "approve"
        assert "Hook error" in result.get("reason", "")
