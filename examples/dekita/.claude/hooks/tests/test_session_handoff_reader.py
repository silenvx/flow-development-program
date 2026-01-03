#!/usr/bin/env python3
"""Tests for session-handoff-reader.py."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))


class TestSessionHandoffReader:
    """Tests for session-handoff-reader.py."""

    def test_is_memo_valid_recent(self):
        """Test memo validity check for recent memo."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "session_handoff_reader",
            HOOKS_DIR / "session-handoff-reader.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        recent_time = datetime.now(UTC) - timedelta(hours=1)
        memo = {"generated_at": recent_time.isoformat()}

        assert module.is_memo_valid(memo)

    def test_is_memo_valid_old(self):
        """Test memo validity check for old memo."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "session_handoff_reader",
            HOOKS_DIR / "session-handoff-reader.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        old_time = datetime.now(UTC) - timedelta(hours=30)
        memo = {"generated_at": old_time.isoformat()}

        assert not module.is_memo_valid(memo)

    def test_format_handoff_message_own_session(self):
        """Test handoff message formatting for own session memo."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "session_handoff_reader",
            HOOKS_DIR / "session-handoff-reader.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        memo = {
            "generated_at": datetime.now(UTC).isoformat(),
            "session_id": "test-session-123",
            "work_status": "作業途中",
            "next_action": "コミットしてください",
            "git": {
                "branch": "feature/test",
                "uncommitted_changes": 2,
                "untracked_files": 1,
            },
            "open_prs": [{"number": 123, "title": "Test PR", "branch": "feature/test"}],
            "worktrees": [],
            "session_summary": {"blocks": 0, "block_reasons": []},
            "pending_tasks": [],
            "lessons_learned": [],
        }

        message = module.format_handoff_message([memo], "test-session-123")

        assert "前回のセッションからの引き継ぎ" in message
        assert "作業途中" in message
        assert "feature/test" in message
        assert "未コミットの変更: 2件" in message
        assert "#123" in message

    def test_format_handoff_message_other_session(self):
        """Test handoff message formatting for other session memo."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "session_handoff_reader",
            HOOKS_DIR / "session-handoff-reader.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        memo = {
            "generated_at": datetime.now(UTC).isoformat(),
            "session_id": "other-session-456",
            "work_status": "作業途中",
            "next_action": "コミットしてください",
            "git": {
                "branch": "feature/test",
                "uncommitted_changes": 0,
                "untracked_files": 0,
            },
            "open_prs": [],
            "worktrees": [],
            "session_summary": {"blocks": 0, "block_reasons": []},
            "pending_tasks": [],
            "lessons_learned": [],
        }

        message = module.format_handoff_message([memo], "my-session-123")

        assert "別セッションからの引き継ぎ" in message

    def test_format_handoff_message_prioritizes_own_session(self):
        """Test that own session memo is prioritized over other sessions."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "session_handoff_reader",
            HOOKS_DIR / "session-handoff-reader.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        now = datetime.now(UTC)
        # Other session's memo is newer
        other_memo = {
            "generated_at": now.isoformat(),
            "session_id": "other-session",
            "work_status": "他セッションの状態",
            "next_action": "他セッションのアクション",
            "git": {"branch": "other-branch", "uncommitted_changes": 0, "untracked_files": 0},
            "open_prs": [],
            "worktrees": [],
            "session_summary": {"blocks": 0, "block_reasons": []},
            "pending_tasks": [],
            "lessons_learned": [],
        }
        # Own session's memo is older but should be prioritized
        own_memo = {
            "generated_at": (now - timedelta(hours=1)).isoformat(),
            "session_id": "my-session",
            "work_status": "自分の状態",
            "next_action": "自分のアクション",
            "git": {"branch": "my-branch", "uncommitted_changes": 0, "untracked_files": 0},
            "open_prs": [],
            "worktrees": [],
            "session_summary": {"blocks": 0, "block_reasons": []},
            "pending_tasks": [],
            "lessons_learned": [],
        }

        # Pass memos sorted by time (other_memo is first because it's newer)
        message = module.format_handoff_message([other_memo, own_memo], "my-session")

        # Should show own session's info, not the other session's
        assert "前回のセッションからの引き継ぎ" in message
        assert "自分の状態" in message
        assert "他セッションの状態" not in message

    def test_main_no_memos(self):
        """Test main function when no memos exist."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "session_handoff_reader",
            HOOKS_DIR / "session-handoff-reader.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        from unittest.mock import MagicMock

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"

        with (
            patch.object(module, "load_all_handoff_memos", return_value=[]),
            patch.object(module, "parse_hook_input", return_value={}),
            patch.object(module, "create_hook_context", return_value=mock_ctx),
            patch("builtins.print") as mock_print,
        ):
            module.main()

            mock_print.assert_called_once()
            output = json.loads(mock_print.call_args[0][0])
            assert output["continue"]
            assert "message" not in output

    def test_main_with_valid_memos(self):
        """Test main function with valid memos."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "session_handoff_reader",
            HOOKS_DIR / "session-handoff-reader.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        recent_memo = {
            "generated_at": datetime.now(UTC).isoformat(),
            "session_id": "test-session",
            "work_status": "待機状態",
            "next_action": "新しいタスクを開始",
            "git": {
                "branch": "main",
                "uncommitted_changes": 0,
                "untracked_files": 0,
            },
            "open_prs": [],
            "worktrees": [],
            "session_summary": {"blocks": 0, "block_reasons": []},
            "pending_tasks": [],
            "lessons_learned": [],
        }

        from unittest.mock import MagicMock

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"

        with (
            patch.object(module, "load_all_handoff_memos", return_value=[recent_memo]),
            patch.object(module, "parse_hook_input", return_value={}),
            patch.object(module, "create_hook_context", return_value=mock_ctx),
            patch("builtins.print") as mock_print,
        ):
            module.main()

            mock_print.assert_called_once()
            output = json.loads(mock_print.call_args[0][0])
            assert output["continue"]
            assert "message" in output
            assert "前回のセッションからの引き継ぎ" in output["message"]
