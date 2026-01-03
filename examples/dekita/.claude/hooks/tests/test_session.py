"""Tests for lib/session.py - HookContext and DI pattern (Issue #2413)."""

import io
import os
import sys
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.session import HookContext, create_hook_context


class TestHookContext:
    """Tests for HookContext class."""

    def test_get_session_id_with_session_id(self):
        """Returns session_id when set."""
        ctx = HookContext(session_id="test-session-123")
        assert ctx.get_session_id() == "test-session-123"

    def test_get_session_id_returns_none_when_not_set(self):
        """Returns None when session_id is not set (Issue #2529: ppid fallback removed)."""
        ctx = HookContext(session_id=None)
        result = ctx.get_session_id()
        assert result is None

    def test_default_session_id_is_none(self):
        """Default session_id is None."""
        ctx = HookContext()
        assert ctx.session_id is None

    def test_debug_log_with_session_id(self):
        """Debug log outputs correct source when session_id is set."""
        ctx = HookContext(session_id="abc-123-def-456-ghi")
        stderr = io.StringIO()
        with patch.dict(os.environ, {"CLAUDE_DEBUG": "1"}):
            with patch("sys.stderr", stderr):
                ctx.get_session_id()
        output = stderr.getvalue()
        assert "source=hook_input" in output
        assert "abc-123-def-456-" in output  # First 16 chars
        assert "..." in output  # Truncation indicator

    def test_debug_log_short_session_id_no_ellipsis(self):
        """Debug log does not add ellipsis for short session_id."""
        ctx = HookContext(session_id="short-id")
        stderr = io.StringIO()
        with patch.dict(os.environ, {"CLAUDE_DEBUG": "1"}):
            with patch("sys.stderr", stderr):
                ctx.get_session_id()
        output = stderr.getvalue()
        assert "value=short-id" in output
        assert "..." not in output  # No ellipsis for short IDs

    def test_debug_log_none_source(self):
        """Debug log outputs None source when session_id is None (Issue #2529)."""
        ctx = HookContext(session_id=None)
        stderr = io.StringIO()
        with patch.dict(os.environ, {"CLAUDE_DEBUG": "1"}):
            with patch("sys.stderr", stderr):
                ctx.get_session_id()
        output = stderr.getvalue()
        assert "source=None" in output
        assert "session_id not provided" in output


class TestCreateHookContext:
    """Tests for create_hook_context function."""

    def test_creates_context_with_session_id(self):
        """Creates context with session_id from hook input."""
        hook_input = {"session_id": "abc-123", "tool_name": "Bash"}
        ctx = create_hook_context(hook_input)
        assert ctx.session_id == "abc-123"
        assert ctx.get_session_id() == "abc-123"

    def test_creates_context_without_session_id(self):
        """Creates context with None when session_id not in input."""
        hook_input = {"tool_name": "Bash"}
        ctx = create_hook_context(hook_input)
        assert ctx.session_id is None

    def test_empty_input(self):
        """Handles empty input dict."""
        ctx = create_hook_context({})
        assert ctx.session_id is None
