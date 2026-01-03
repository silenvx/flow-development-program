"""Tests for make_block_result function in common.py."""

import io
import sys
from unittest import mock

import pytest

# Issue #2496: Removed session_module import and reset_hook_session_id fixture
# Global state (_HOOK_SESSION_ID, set_hook_session_id) has been removed


@pytest.fixture
def no_repeated_blocks():
    """Mock _count_recent_blocks to return 0 to avoid repeated block warning.

    Issue #2401: 連続ブロック検出機能の追加により、テスト間でログが蓄積され
    警告が表示されてしまう問題を回避するためのフィクスチャ。
    """
    with mock.patch("lib.results._count_recent_blocks", return_value=0):
        yield


class TestMakeBlockResult:
    """Tests for make_block_result systemMessage and stderr output (Issue #1279)."""

    def test_systemMessage_contains_first_line(self, no_repeated_blocks):
        """Test that systemMessage contains first line of reason."""
        from lib.results import make_block_result

        captured = io.StringIO()
        sys.stderr = captured
        try:
            result = make_block_result("test-hook", "This is the reason\nSecond line")
        finally:
            sys.stderr = sys.__stderr__

        assert result["decision"] == "block"
        assert result["systemMessage"] == "❌ test-hook: This is the reason"

    def test_truncates_long_reason(self, no_repeated_blocks):
        """Test that long reason is truncated to 100 chars in systemMessage."""
        from lib.results import make_block_result

        long_reason = "x" * 150

        captured = io.StringIO()
        sys.stderr = captured
        try:
            result = make_block_result("test-hook", long_reason)
        finally:
            sys.stderr = sys.__stderr__

        # Should end with ...
        assert result["systemMessage"].endswith("...")
        # Should not contain full 150 chars
        assert "x" * 150 not in result["systemMessage"]
        # Should be 100 chars max (97 + "...")
        # ❌ test-hook: + 97 x's + ... = total message
        assert "x" * 97 in result["systemMessage"]

    def test_handles_empty_reason(self, no_repeated_blocks):
        """Test that empty reason shows default message in systemMessage."""
        from lib.results import make_block_result

        captured = io.StringIO()
        sys.stderr = captured
        try:
            result = make_block_result("test-hook", "")
        finally:
            sys.stderr = sys.__stderr__

        assert result["systemMessage"] == "❌ test-hook: ブロックされました"

    def test_handles_newline_only_reason(self, no_repeated_blocks):
        """Test that newline-only reason shows default message in systemMessage."""
        from lib.results import make_block_result

        captured = io.StringIO()
        sys.stderr = captured
        try:
            result = make_block_result("test-hook", "\n\n")
        finally:
            sys.stderr = sys.__stderr__

        assert result["systemMessage"] == "❌ test-hook: ブロックされました"

    def test_handles_whitespace_only_reason(self, no_repeated_blocks):
        """Test that whitespace-only reason shows default message in systemMessage."""
        from lib.results import make_block_result

        captured = io.StringIO()
        sys.stderr = captured
        try:
            result = make_block_result("test-hook", "   \n   ")
        finally:
            sys.stderr = sys.__stderr__

        assert result["systemMessage"] == "❌ test-hook: ブロックされました"

    def test_returns_correct_structure(self, no_repeated_blocks):
        """Test that return value has correct structure with systemMessage."""
        from lib.results import CONTINUATION_HINT, make_block_result

        captured = io.StringIO()
        sys.stderr = captured
        try:
            result = make_block_result("my-hook", "test reason")
        finally:
            sys.stderr = sys.__stderr__

        assert result["decision"] == "block"
        assert "[my-hook]" in result["reason"]
        assert "test reason" in result["reason"]
        assert CONTINUATION_HINT in result["reason"]
        assert "systemMessage" in result
        assert "❌ my-hook: test reason" == result["systemMessage"]

    def test_outputs_to_stderr_as_fallback(self, no_repeated_blocks):
        """Test that stderr output is maintained as fallback (Issue #938)."""
        from lib.results import make_block_result

        captured = io.StringIO()
        sys.stderr = captured
        try:
            make_block_result("test-hook", "This is the reason\nSecond line")
        finally:
            sys.stderr = sys.__stderr__

        output = captured.getvalue()
        assert "❌ test-hook: This is the reason" in output
        assert "Second line" not in output


class TestMakeBlockResultLogging:
    """Tests for make_block_result auto-logging to session files (Issue #2023)."""

    def test_logs_block_to_session_file_with_ctx(self, tmp_path, monkeypatch, no_repeated_blocks):
        """Test that make_block_result logs block to session file when ctx is provided.

        Issue #2496: Updated to use HookContext instead of removed global state.
        """
        import json

        from lib.results import make_block_result
        from lib.session import HookContext

        # Set up test session ID and log directory
        test_session_id = "test-session-123"
        ctx = HookContext(session_id=test_session_id)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Create log directory
        log_dir = tmp_path / ".claude" / "logs" / "execution"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Capture stderr (make_block_result writes to stderr as side effect)
        captured = io.StringIO()
        sys.stderr = captured
        try:
            result = make_block_result("test-hook", "Test block reason", ctx=ctx)
        finally:
            sys.stderr = sys.__stderr__

        # Verify result structure
        assert result["decision"] == "block"

        # Verify session log file was created
        session_log = log_dir / f"hook-execution-{test_session_id}.jsonl"
        assert session_log.exists(), f"Session log file not created: {session_log}"

        # Verify log content
        with open(session_log) as f:
            lines = f.readlines()

        assert len(lines) >= 1, "No log entries found"

        # Parse the log entry
        entry = json.loads(lines[-1])
        assert entry["hook"] == "test-hook"
        assert entry["decision"] == "block"
        assert entry["reason"] == "Test block reason"
        assert entry["session_id"] == test_session_id

    def test_no_session_log_when_no_ctx(self, tmp_path, monkeypatch, no_repeated_blocks):
        """Test that make_block_result does not create session log when no ctx provided.

        Issue #2529: ppidフォールバック完全廃止。ctx=Noneの場合はセッション固有のログを作成しない。
        """
        from lib.results import make_block_result

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Create log directory
        log_dir = tmp_path / ".claude" / "logs" / "execution"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Capture stderr
        captured = io.StringIO()
        sys.stderr = captured
        try:
            result = make_block_result("test-hook", "Test block reason")
        finally:
            sys.stderr = sys.__stderr__

        # Verify result structure still works
        assert result["decision"] == "block"

        # Issue #2529: ctx=Noneの場合、セッション固有のログは作成されない
        session_files = list(log_dir.glob("hook-execution-*.jsonl"))
        assert len(session_files) == 0, "No session log should be created when ctx is None"


class TestMakeBlockResultWithHookContext:
    """Tests for make_block_result with HookContext parameter (Issue #2456)."""

    def test_uses_ctx_session_id_when_provided(self, tmp_path, monkeypatch, no_repeated_blocks):
        """Test that make_block_result uses ctx.get_session_id() when ctx is provided."""
        import json

        from lib.results import make_block_result
        from lib.session import HookContext

        # Set up log directory
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        log_dir = tmp_path / ".claude" / "logs" / "execution"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Create HookContext with specific session_id
        ctx = HookContext(session_id="ctx-session-456")

        # Capture stderr
        import io
        import sys

        captured = io.StringIO()
        sys.stderr = captured
        try:
            result = make_block_result("test-hook", "Test reason", ctx=ctx)
        finally:
            sys.stderr = sys.__stderr__

        # Verify result structure
        assert result["decision"] == "block"

        # Session file with ctx session_id should be created
        session_log = log_dir / "hook-execution-ctx-session-456.jsonl"
        assert session_log.exists(), f"Session log with ctx session_id not created: {session_log}"

        # Verify log content uses ctx session_id
        with open(session_log) as f:
            lines = f.readlines()

        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert entry["session_id"] == "ctx-session-456"

    def test_no_session_log_when_ctx_none_explicit(self, tmp_path, monkeypatch, no_repeated_blocks):
        """Test that make_block_result does not create session log when ctx is explicitly None.

        Issue #2529: ppidフォールバック完全廃止。ctx=Noneを明示した場合もログ作成しない。
        """
        from lib.results import make_block_result

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        log_dir = tmp_path / ".claude" / "logs" / "execution"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Capture stderr
        import io
        import sys

        captured = io.StringIO()
        sys.stderr = captured
        try:
            result = make_block_result("test-hook", "Test reason", ctx=None)
        finally:
            sys.stderr = sys.__stderr__

        assert result["decision"] == "block"

        # Issue #2529: ctx=Noneの場合、セッション固有のログは作成されない
        session_files = list(log_dir.glob("hook-execution-*.jsonl"))
        assert len(session_files) == 0, "No session log should be created when ctx is None"
