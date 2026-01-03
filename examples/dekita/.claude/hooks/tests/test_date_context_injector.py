#!/usr/bin/env python3
"""Tests for date-context-injector hook (Issue #2279)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

# Import module with hyphen in name using importlib
spec = importlib.util.spec_from_file_location(
    "date_context_injector",
    HOOKS_DIR / "date-context-injector.py",
)
date_context_injector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(date_context_injector)


class TestBuildOutput:
    """Tests for build_output function."""

    def test_basic_output(self):
        """Test basic output without session info."""
        result = date_context_injector.build_output(
            "2026-01-01 Thursday 12:00:00 JST",
            "2026-01-01T12:00:00+09:00",
            None,
            None,
        )
        assert (
            result
            == "[CONTEXT] 現在日時: 2026-01-01 Thursday 12:00:00 JST | ISO: 2026-01-01T12:00:00+09:00"
        )

    def test_with_session_id(self):
        """Test output with session_id."""
        result = date_context_injector.build_output(
            "2026-01-01 Thursday 12:00:00 JST",
            "2026-01-01T12:00:00+09:00",
            "abc-123",
            None,
        )
        assert "Session: abc-123" in result
        assert "Source:" not in result

    def test_with_source(self):
        """Test output with source field."""
        result = date_context_injector.build_output(
            "2026-01-01 Thursday 12:00:00 JST",
            "2026-01-01T12:00:00+09:00",
            None,
            "startup",
        )
        assert "Source: startup" in result
        assert "Session:" not in result

    def test_with_session_id_and_source(self):
        """Test output with both session_id and source."""
        result = date_context_injector.build_output(
            "2026-01-01 Thursday 12:00:00 JST",
            "2026-01-01T12:00:00+09:00",
            "abc-123",
            "resume",
        )
        assert "Session: abc-123" in result
        assert "Source: resume" in result
        # Verify order: session_id comes before source
        assert result.index("Session:") < result.index("Source:")

    def test_with_error(self):
        """Test output with error message."""
        result = date_context_injector.build_output(
            "2026-01-01 Thursday 12:00:00",
            "2026-01-01T12:00:00",
            "abc-123",
            "startup",
            "No time zone found",
        )
        assert "(TZエラー: No time zone found)" in result

    def test_all_source_values(self):
        """Test output with different source values (startup, resume, clear, compact)."""
        for source_value in ["startup", "resume", "clear", "compact"]:
            result = date_context_injector.build_output(
                "2026-01-01 Thursday 12:00:00 JST",
                "2026-01-01T12:00:00+09:00",
                "test-id",
                source_value,
            )
            assert f"Source: {source_value}" in result


class TestGetSessionInfoFromInput:
    """Tests for get_session_info_from_input function."""

    def test_returns_empty_dict_when_stdin_is_tty(self, monkeypatch):
        """Test that empty dict is returned when stdin is a tty."""
        import io

        mock_stdin = io.StringIO()
        mock_stdin.isatty = lambda: True
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        result = date_context_injector.get_session_info_from_input()
        assert result == {}

    def test_returns_empty_dict_on_empty_input(self, monkeypatch):
        """Test that empty dict is returned on empty input."""
        import io

        mock_stdin = io.StringIO("")
        mock_stdin.isatty = lambda: False
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        result = date_context_injector.get_session_info_from_input()
        assert result == {}

    def test_returns_session_info(self, monkeypatch):
        """Test that session info is correctly parsed."""
        import io

        input_data = {"session_id": "test-123", "source": "resume"}
        mock_stdin = io.StringIO(json.dumps(input_data))
        mock_stdin.isatty = lambda: False
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        result = date_context_injector.get_session_info_from_input()
        assert result["session_id"] == "test-123"
        assert result["source"] == "resume"

    def test_returns_empty_dict_on_invalid_json(self, monkeypatch):
        """Test that empty dict is returned on invalid JSON."""
        import io

        mock_stdin = io.StringIO("not valid json")
        mock_stdin.isatty = lambda: False
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        result = date_context_injector.get_session_info_from_input()
        assert result == {}
