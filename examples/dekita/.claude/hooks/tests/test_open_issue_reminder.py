#!/usr/bin/env python3
"""Unit tests for open-issue-reminder.py"""

import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

import common as common_module

# Import with dynamic loading due to hyphens in filename
HOOK_PATH = Path(__file__).parent.parent / "open-issue-reminder.py"
_spec = importlib.util.spec_from_file_location("open_issue_reminder", HOOK_PATH)
open_issue_reminder = importlib.util.module_from_spec(_spec)
sys.modules["open_issue_reminder"] = open_issue_reminder
_spec.loader.exec_module(open_issue_reminder)


class TestHasPriorityLabel:
    """Tests for has_priority_label function."""

    def test_has_high_priority_label(self):
        """Should return True when issue has priority:high label."""
        issue = {
            "number": 1,
            "title": "Test issue",
            "labels": [{"name": "priority:high"}, {"name": "bug"}],
        }
        assert open_issue_reminder.has_priority_label(issue, "high")

    def test_no_priority_label(self):
        """Should return False when issue has no priority label."""
        issue = {
            "number": 1,
            "title": "Test issue",
            "labels": [{"name": "bug"}, {"name": "enhancement"}],
        }
        assert not open_issue_reminder.has_priority_label(issue, "high")

    def test_different_priority_level(self):
        """Should return False for different priority level."""
        issue = {
            "number": 1,
            "title": "Test issue",
            "labels": [{"name": "priority:medium"}],
        }
        assert not open_issue_reminder.has_priority_label(issue, "high")
        assert open_issue_reminder.has_priority_label(issue, "medium")

    def test_empty_labels(self):
        """Should return False when labels list is empty."""
        issue = {"number": 1, "title": "Test issue", "labels": []}
        assert not open_issue_reminder.has_priority_label(issue, "high")

    def test_no_labels_key(self):
        """Should return False when labels key is missing."""
        issue = {"number": 1, "title": "Test issue"}
        assert not open_issue_reminder.has_priority_label(issue, "high")


class TestIsHighPriorityIssue:
    """Tests for is_high_priority_issue function (Issue #1042)."""

    def test_p1_label_is_high_priority(self):
        """P1 label should be treated as high priority."""
        issue = {
            "number": 1,
            "title": "Critical bug",
            "labels": [{"name": "P1"}, {"name": "bug"}],
        }
        assert open_issue_reminder.is_high_priority_issue(issue)

    def test_p2_label_is_high_priority(self):
        """P2 label should be treated as high priority."""
        issue = {
            "number": 1,
            "title": "Important bug",
            "labels": [{"name": "P2"}],
        }
        assert open_issue_reminder.is_high_priority_issue(issue)

    def test_priority_high_label_is_high_priority(self):
        """priority:high label should still be treated as high priority."""
        issue = {
            "number": 1,
            "title": "High priority issue",
            "labels": [{"name": "priority:high"}],
        }
        assert open_issue_reminder.is_high_priority_issue(issue)

    def test_priority_critical_label_is_high_priority(self):
        """priority:critical label should be treated as high priority."""
        issue = {
            "number": 1,
            "title": "Critical issue",
            "labels": [{"name": "priority:critical"}],
        }
        assert open_issue_reminder.is_high_priority_issue(issue)

    def test_p3_label_is_not_high_priority(self):
        """P3 label should NOT be treated as high priority."""
        issue = {
            "number": 1,
            "title": "Low priority issue",
            "labels": [{"name": "P3"}],
        }
        assert not open_issue_reminder.is_high_priority_issue(issue)

    def test_enhancement_label_is_not_high_priority(self):
        """Enhancement label should NOT be treated as high priority."""
        issue = {
            "number": 1,
            "title": "Feature request",
            "labels": [{"name": "enhancement"}],
        }
        assert not open_issue_reminder.is_high_priority_issue(issue)

    def test_no_labels_is_not_high_priority(self):
        """Issue with no labels should NOT be treated as high priority."""
        issue = {"number": 1, "title": "Test issue", "labels": []}
        assert not open_issue_reminder.is_high_priority_issue(issue)

    def test_missing_labels_key_is_not_high_priority(self):
        """Issue without labels key should NOT be treated as high priority."""
        issue = {"number": 1, "title": "Test issue"}
        assert not open_issue_reminder.is_high_priority_issue(issue)

    def test_mixed_labels_with_p1(self):
        """Issue with P1 among other labels should be high priority."""
        issue = {
            "number": 1,
            "title": "Bug with P1",
            "labels": [{"name": "bug"}, {"name": "P1"}, {"name": "enhancement"}],
        }
        assert open_issue_reminder.is_high_priority_issue(issue)


class TestFormatIssuesMessage:
    """Tests for format_issues_message function."""

    def test_empty_issues(self):
        """Should return empty string for empty issues list."""
        result = open_issue_reminder.format_issues_message([])
        assert result == ""

    def test_high_priority_only(self):
        """Should show high priority issues with emphasis."""
        issues = [
            {
                "number": 1,
                "title": "Critical bug",
                "labels": [{"name": "priority:high"}, {"name": "bug"}],
            }
        ]
        result = open_issue_reminder.format_issues_message(issues)
        assert "🚨" in result
        assert "高優先度Issue" in result
        assert "#1" in result
        assert "Critical bug" in result
        assert "priority:high" in result

    def test_regular_issues_only(self):
        """Should show regular issues without high priority section."""
        issues = [
            {
                "number": 2,
                "title": "Regular issue",
                "labels": [{"name": "enhancement"}],
            }
        ]
        result = open_issue_reminder.format_issues_message(issues)
        assert "🚨" not in result
        assert "📋" in result
        assert "#2" in result
        assert "Regular issue" in result

    def test_mixed_priority_issues(self):
        """Should show both high priority and regular issues."""
        issues = [
            {
                "number": 1,
                "title": "Critical bug",
                "labels": [{"name": "priority:high"}],
            },
            {
                "number": 2,
                "title": "Regular issue",
                "labels": [{"name": "enhancement"}],
            },
        ]
        result = open_issue_reminder.format_issues_message(issues)
        assert "🚨" in result
        assert "📋" in result
        assert "#1" in result
        assert "#2" in result

    def test_high_priority_comes_first(self):
        """High priority issues should appear before regular issues."""
        issues = [
            {"number": 1, "title": "Regular", "labels": []},
            {
                "number": 2,
                "title": "High priority",
                "labels": [{"name": "priority:high"}],
            },
        ]
        result = open_issue_reminder.format_issues_message(issues)
        high_priority_pos = result.find("High priority")
        regular_pos = result.find("Regular")
        assert high_priority_pos < regular_pos

    def test_max_five_regular_issues(self):
        """Should show max 5 regular issues with count for more."""
        issues = [{"number": i, "title": f"Issue {i}", "labels": []} for i in range(1, 8)]
        result = open_issue_reminder.format_issues_message(issues)
        # Should show issues 1-5
        for i in range(1, 6):
            assert f"#{i}" in result
        # Should indicate more issues
        assert "他 2 件" in result

    def test_multiple_high_priority_issues(self):
        """Should show all high priority issues (no limit)."""
        issues = [
            {
                "number": i,
                "title": f"Critical bug {i}",
                "labels": [{"name": "priority:high"}],
            }
            for i in range(1, 4)
        ]
        result = open_issue_reminder.format_issues_message(issues)
        # All high priority issues should be shown
        assert "#1" in result
        assert "#2" in result
        assert "#3" in result
        # All high priority issues should have arrow indicator
        assert "→ #1" in result
        assert "→ #2" in result
        assert "→ #3" in result

    def test_labels_display_format(self):
        """Should display multiple labels correctly for high priority issues.

        Tests that when an issue has priority:high label along with other labels,
        all labels are shown in the output within brackets.
        """
        issues = [
            {
                "number": 1,
                "title": "Test issue",
                "labels": [
                    {"name": "bug"},
                    {"name": "priority:high"},
                    {"name": "urgent"},
                ],
            }
        ]
        result = open_issue_reminder.format_issues_message(issues)
        # Issue appears in high priority section due to priority:high label
        assert "🚨" in result
        assert "→ #1" in result
        # Labels should be in brackets and include all labels
        assert "[" in result
        assert "]" in result
        assert "bug" in result
        assert "priority:high" in result
        assert "urgent" in result

    def test_low_priority_not_treated_as_high(self):
        """Low priority issues should appear in regular section."""
        issues = [
            {
                "number": 1,
                "title": "Low priority issue",
                "labels": [{"name": "priority:low"}],
            }
        ]
        result = open_issue_reminder.format_issues_message(issues)
        # Should be in regular section, not high priority
        assert "🚨" not in result
        assert "📋" in result
        assert "priority:low" in result

    def test_p1_label_shown_as_high_priority(self):
        """P1 labeled issues should appear in high priority section (Issue #1042)."""
        issues = [
            {
                "number": 1,
                "title": "P1 Bug",
                "labels": [{"name": "P1"}, {"name": "bug"}],
            }
        ]
        result = open_issue_reminder.format_issues_message(issues)
        assert "🚨" in result
        assert "高優先度Issue" in result
        assert "→ #1" in result
        assert "P1 Bug" in result

    def test_p2_label_shown_as_high_priority(self):
        """P2 labeled issues should appear in high priority section (Issue #1042)."""
        issues = [
            {
                "number": 2,
                "title": "P2 Issue",
                "labels": [{"name": "P2"}],
            }
        ]
        result = open_issue_reminder.format_issues_message(issues)
        assert "🚨" in result
        assert "高優先度Issue" in result
        assert "→ #2" in result

    def test_p3_label_not_shown_as_high_priority(self):
        """P3 labeled issues should appear in regular section (Issue #1042)."""
        issues = [
            {
                "number": 3,
                "title": "P3 Issue",
                "labels": [{"name": "P3"}],
            }
        ]
        result = open_issue_reminder.format_issues_message(issues)
        assert "🚨" not in result
        assert "📋" in result
        assert "- #3" in result

    def test_issue_with_missing_title(self):
        """Should handle issues with missing title."""
        issues = [{"number": 1, "labels": []}]
        result = open_issue_reminder.format_issues_message(issues)
        assert "#1" in result
        assert "No title" in result

    def test_issue_with_missing_number(self):
        """Should handle issues with missing number."""
        issues = [{"title": "Test issue", "labels": []}]
        result = open_issue_reminder.format_issues_message(issues)
        assert "#?" in result
        assert "Test issue" in result

    def test_includes_footer_with_command(self):
        """Should include footer with complete gh command for viewing issues."""
        issues = [{"number": 1, "title": "Test", "labels": []}]
        result = open_issue_reminder.format_issues_message(issues)
        # Verify the complete footer format with gh issue list command
        assert "gh issue list --state open" in result


class TestMain:
    """Integration tests for main() function."""

    def setup_method(self):
        """Create temporary directory for session markers."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        # Patch SESSION_DIR in common module (used by check_and_update_session_marker)
        self.session_dir_patcher = patch.object(common_module, "SESSION_DIR", self.temp_path)
        self.session_dir_patcher.start()

    def teardown_method(self):
        """Clean up temporary directory."""
        self.session_dir_patcher.stop()
        self.temp_dir.cleanup()

    def _run_main(self) -> dict:
        """Helper to run main() and capture output."""
        captured_output = io.StringIO()

        with (
            patch("sys.stdout", captured_output),
            patch("open_issue_reminder.log_hook_execution"),
            patch("open_issue_reminder.get_open_issues", return_value=[]),
            pytest.raises(SystemExit) as ctx,
        ):
            open_issue_reminder.main()

        assert ctx.value.code == 0
        return json.loads(captured_output.getvalue())

    def test_returns_approve_decision(self):
        """Should return approve decision."""
        result = self._run_main()
        assert result["decision"] == "approve"

    def test_outputs_valid_json(self):
        """Should output valid JSON."""
        captured_output = io.StringIO()

        with (
            patch("sys.stdout", captured_output),
            patch("open_issue_reminder.log_hook_execution"),
            patch("open_issue_reminder.get_open_issues", return_value=[]),
            pytest.raises(SystemExit),
        ):
            open_issue_reminder.main()

        output = captured_output.getvalue()
        # Should be valid JSON
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_includes_system_message_when_issues_exist(self):
        """Should include systemMessage when there are open issues."""
        test_issues = [{"number": 1, "title": "Test issue", "labels": []}]
        captured_output = io.StringIO()

        with (
            patch("sys.stdout", captured_output),
            patch("open_issue_reminder.log_hook_execution"),
            patch("open_issue_reminder.get_open_issues", return_value=test_issues),
            pytest.raises(SystemExit),
        ):
            open_issue_reminder.main()

        result = json.loads(captured_output.getvalue())
        assert "systemMessage" in result
        assert "#1" in result["systemMessage"]

    def test_no_system_message_when_no_issues(self):
        """Should not include systemMessage when there are no open issues."""
        result = self._run_main()
        assert "systemMessage" not in result

    def test_no_system_message_on_same_session(self):
        """Should not include systemMessage within same session."""
        test_issues = [{"number": 1, "title": "Test issue", "labels": []}]

        # First call - new session
        captured_output1 = io.StringIO()
        with (
            patch("sys.stdout", captured_output1),
            patch("open_issue_reminder.log_hook_execution"),
            patch("open_issue_reminder.get_open_issues", return_value=test_issues),
            pytest.raises(SystemExit),
        ):
            open_issue_reminder.main()
        result1 = json.loads(captured_output1.getvalue())
        assert "systemMessage" in result1

        # Second call - same session
        captured_output2 = io.StringIO()
        with (
            patch("sys.stdout", captured_output2),
            patch("open_issue_reminder.log_hook_execution"),
            patch("open_issue_reminder.get_open_issues", return_value=test_issues),
            pytest.raises(SystemExit),
        ):
            open_issue_reminder.main()
        result2 = json.loads(captured_output2.getvalue())
        assert "systemMessage" not in result2

    def test_handles_exceptions_gracefully(self):
        """Should not block on errors."""
        captured_output = io.StringIO()

        with (
            patch("sys.stdout", captured_output),
            patch("open_issue_reminder.log_hook_execution"),
            patch.object(
                open_issue_reminder,
                "check_and_update_session_marker",
                side_effect=Exception("Test error"),
            ),
            pytest.raises(SystemExit),
        ):
            open_issue_reminder.main()

        result = json.loads(captured_output.getvalue())
        # Should still return approve
        assert result["decision"] == "approve"
