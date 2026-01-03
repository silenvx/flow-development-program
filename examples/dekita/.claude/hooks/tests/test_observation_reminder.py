#!/usr/bin/env python3
"""Tests for observation-reminder hook."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load the module
hook_path = Path(__file__).parent.parent / "observation-reminder.py"
spec = importlib.util.spec_from_file_location("observation_reminder", hook_path)
observation_reminder = importlib.util.module_from_spec(spec)
sys.modules["observation_reminder"] = observation_reminder
spec.loader.exec_module(observation_reminder)


class TestIsPrMergeCommand:
    """Tests for is_pr_merge_command function."""

    def test_basic_merge_command(self):
        assert observation_reminder.is_pr_merge_command("gh pr merge 123") is True

    def test_merge_with_squash(self):
        assert observation_reminder.is_pr_merge_command("gh pr merge 123 --squash") is True

    def test_non_merge_commands(self):
        assert observation_reminder.is_pr_merge_command("gh pr view 123") is False
        assert observation_reminder.is_pr_merge_command("gh pr create") is False
        assert observation_reminder.is_pr_merge_command("git commit -m 'msg'") is False


class TestFormatIssueAge:
    """Tests for format_issue_age function."""

    def test_recent(self):
        """Test formatting for very recent issues."""
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        # 30 minutes ago
        recent = (now - timedelta(minutes=30)).isoformat()
        assert observation_reminder.format_issue_age(recent) == "1時間以内"

    def test_hours_ago(self):
        """Test formatting for issues created hours ago."""
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        # 5 hours ago
        hours_ago = (now - timedelta(hours=5)).isoformat()
        assert observation_reminder.format_issue_age(hours_ago) == "5時間前"

    def test_days_ago(self):
        """Test formatting for issues created days ago."""
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        # 3 days ago
        days_ago = (now - timedelta(days=3)).isoformat()
        assert observation_reminder.format_issue_age(days_ago) == "3日前"

    def test_invalid_format(self):
        """Test handling of invalid date format."""
        assert observation_reminder.format_issue_age("invalid") == "不明"
        assert observation_reminder.format_issue_age("") == "不明"
        assert observation_reminder.format_issue_age(None) == "不明"


class TestGetObservationIssues:
    """Tests for get_observation_issues function."""

    def test_successful_response(self):
        """Test successful response from gh CLI."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            [
                {"number": 123, "title": "Test Issue 1", "createdAt": "2026-01-01T00:00:00Z"},
                {"number": 456, "title": "Test Issue 2", "createdAt": "2026-01-02T00:00:00Z"},
            ]
        )

        with patch("subprocess.run", return_value=mock_result):
            issues = observation_reminder.get_observation_issues()
            assert len(issues) == 2
            assert issues[0]["number"] == 123
            assert issues[1]["number"] == 456

    def test_empty_response(self):
        """Test empty response from gh CLI."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "[]"

        with patch("subprocess.run", return_value=mock_result):
            issues = observation_reminder.get_observation_issues()
            assert issues == []

    def test_gh_error(self):
        """Test gh CLI error returns empty list."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            issues = observation_reminder.get_observation_issues()
            assert issues == []

    def test_timeout(self):
        """Test timeout returns empty list."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=10)):
            issues = observation_reminder.get_observation_issues()
            assert issues == []

    def test_invalid_json(self):
        """Test invalid JSON returns empty list."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not valid json"

        with patch("subprocess.run", return_value=mock_result):
            issues = observation_reminder.get_observation_issues()
            assert issues == []
