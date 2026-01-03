#!/usr/bin/env python3
"""Tests for observation-session-reminder hook."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load the module
hook_path = Path(__file__).parent.parent / "observation-session-reminder.py"
spec = importlib.util.spec_from_file_location("observation_session_reminder", hook_path)
observation_session_reminder = importlib.util.module_from_spec(spec)
sys.modules["observation_session_reminder"] = observation_session_reminder
spec.loader.exec_module(observation_session_reminder)


class TestGetObservationIssues:
    """Tests for get_observation_issues function."""

    def test_successful_response(self):
        """Test successful response from gh CLI."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            [
                {"number": 123, "title": "Test Issue 1"},
                {"number": 456, "title": "Test Issue 2"},
            ]
        )

        with patch("subprocess.run", return_value=mock_result):
            issues = observation_session_reminder.get_observation_issues()
            assert len(issues) == 2
            assert issues[0]["number"] == 123
            assert issues[1]["number"] == 456

    def test_empty_response(self):
        """Test empty response from gh CLI."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "[]"

        with patch("subprocess.run", return_value=mock_result):
            issues = observation_session_reminder.get_observation_issues()
            assert issues == []

    def test_gh_error(self):
        """Test gh CLI error returns empty list."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            issues = observation_session_reminder.get_observation_issues()
            assert issues == []

    def test_timeout(self):
        """Test timeout returns empty list."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=10)):
            issues = observation_session_reminder.get_observation_issues()
            assert issues == []

    def test_invalid_json(self):
        """Test invalid JSON returns empty list."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not valid json"

        with patch("subprocess.run", return_value=mock_result):
            issues = observation_session_reminder.get_observation_issues()
            assert issues == []


class TestMain:
    """Tests for main function."""

    def test_no_input_data(self):
        """Test early return when no input data."""
        with patch.object(observation_session_reminder, "parse_hook_input", return_value=None):
            # Should not raise, just return early
            observation_session_reminder.main()

    def test_no_observation_issues(self, capsys):
        """Test no output when no observation issues."""
        with (
            patch.object(
                observation_session_reminder,
                "parse_hook_input",
                return_value={"session_id": "test-session"},
            ),
            patch.object(observation_session_reminder, "get_observation_issues", return_value=[]),
            patch.object(observation_session_reminder, "log_hook_execution"),
        ):
            observation_session_reminder.main()
            captured = capsys.readouterr()
            # No output when no issues
            assert "動作確認Issue" not in captured.out

    def test_with_observation_issues(self, capsys):
        """Test reminder output when observation issues exist."""
        mock_issues = [
            {"number": 123, "title": "Test 1"},
            {"number": 456, "title": "Test 2"},
        ]

        with (
            patch.object(
                observation_session_reminder,
                "parse_hook_input",
                return_value={"session_id": "test-session"},
            ),
            patch.object(
                observation_session_reminder,
                "get_observation_issues",
                return_value=mock_issues,
            ),
            patch.object(observation_session_reminder, "log_hook_execution"),
        ):
            observation_session_reminder.main()
            captured = capsys.readouterr()
            assert "動作確認Issue 2件" in captured.out
            assert "#123" in captured.out
            assert "#456" in captured.out
            assert "gh issue close" in captured.out

    def test_single_issue(self, capsys):
        """Test reminder output with single issue."""
        mock_issues = [{"number": 789, "title": "Single Issue"}]

        with (
            patch.object(
                observation_session_reminder,
                "parse_hook_input",
                return_value={"session_id": "test-session"},
            ),
            patch.object(
                observation_session_reminder,
                "get_observation_issues",
                return_value=mock_issues,
            ),
            patch.object(observation_session_reminder, "log_hook_execution"),
        ):
            observation_session_reminder.main()
            captured = capsys.readouterr()
            assert "動作確認Issue 1件" in captured.out
            assert "#789" in captured.out

    def test_log_hook_execution_called(self):
        """Test that log_hook_execution is called with correct arguments."""
        mock_issues = [{"number": 123, "title": "Test"}]

        with (
            patch.object(
                observation_session_reminder,
                "parse_hook_input",
                return_value={"session_id": "test-session"},
            ),
            patch.object(
                observation_session_reminder,
                "get_observation_issues",
                return_value=mock_issues,
            ),
            patch.object(observation_session_reminder, "log_hook_execution") as mock_log,
        ):
            observation_session_reminder.main()
            mock_log.assert_called_once_with(
                "observation-session-reminder",
                "approve",
                "reminded about 1 observation issue(s) at session start",
            )

    def test_log_hook_execution_no_issues(self):
        """Test log_hook_execution when no issues."""
        with (
            patch.object(
                observation_session_reminder,
                "parse_hook_input",
                return_value={"session_id": "test-session"},
            ),
            patch.object(observation_session_reminder, "get_observation_issues", return_value=[]),
            patch.object(observation_session_reminder, "log_hook_execution") as mock_log,
        ):
            observation_session_reminder.main()
            mock_log.assert_called_once_with(
                "observation-session-reminder",
                "approve",
                "no pending observation issues at session start",
            )
