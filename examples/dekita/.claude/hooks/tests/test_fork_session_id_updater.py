#!/usr/bin/env python3
"""Tests for fork-session-id-updater.py hook.

Issue #2363: Tests for session ID output in UserPromptSubmit hook.
Issue #2372: Tests for timestamp and explanation in output.
"""

import json
import re
from unittest.mock import patch

from conftest import load_hook_module


class TestMain:
    """Tests for main function."""

    def setup_method(self):
        self.module = load_hook_module("fork-session-id-updater")

    def test_no_output_when_no_input(self, capsys):
        """Test no output when parse_hook_input returns empty dict."""
        with patch.object(self.module, "parse_hook_input", return_value={}):
            self.module.main()

        captured = capsys.readouterr()
        assert captured.out == ""

    def test_no_output_when_no_session_id(self, capsys):
        """Test no output when session_id is not in input."""
        with patch.object(self.module, "parse_hook_input", return_value={"tool_name": "Read"}):
            self.module.main()

        captured = capsys.readouterr()
        assert captured.out == ""

    def test_outputs_session_id_in_additional_context(self, capsys):
        """Test outputs session_id in additionalContext with timestamp and explanation."""
        session_id = "test-session-id-12345"
        hook_input = {
            "session_id": session_id,
            "source": "resume",
        }

        with patch.object(self.module, "parse_hook_input", return_value=hook_input):
            self.module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        context = result["hookSpecificOutput"]["additionalContext"]

        # session_idが含まれている
        assert session_id in context
        assert "[USER_PROMPT_SESSION_ID]" in context

        # Issue #2372: タイムスタンプが含まれている（ISO形式）
        # 形式: 2026-01-02T10:45:50+09:00 または 2026-01-02T10:45:50+00:00 (UTC)
        iso_pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2}|Z)"
        assert re.search(iso_pattern, context), f"ISO timestamp not found in: {context}"

        # Issue #2372: 説明が含まれている
        assert "fork元" in context
        assert "fork-session" in context


class TestGetCurrentTimestamp:
    """Tests for get_current_timestamp function."""

    def setup_method(self):
        self.module = load_hook_module("fork-session-id-updater")

    def test_returns_iso_format(self):
        """Test that timestamp is in ISO format."""
        timestamp = self.module.get_current_timestamp()
        # ISO 8601形式: 2026-01-02T10:45:50+09:00 または +00:00 (UTC)
        iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2}|Z)$"
        assert re.match(iso_pattern, timestamp), f"Invalid ISO format: {timestamp}"

    def test_utc_fallback_when_zoneinfo_fails(self):
        """Test that UTC is used when ZoneInfo fails."""
        from zoneinfo import ZoneInfoNotFoundError

        with patch.object(self.module, "ZoneInfo", side_effect=ZoneInfoNotFoundError("Asia/Tokyo")):
            timestamp = self.module.get_current_timestamp()

        # UTC形式: +00:00
        assert "+00:00" in timestamp, f"Expected UTC format, got: {timestamp}"
