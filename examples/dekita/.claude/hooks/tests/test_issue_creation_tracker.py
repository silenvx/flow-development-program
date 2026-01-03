#!/usr/bin/env python3
"""Unit tests for issue-creation-tracker.py"""

import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Import with dynamic loading due to hyphens in filename
HOOK_PATH = Path(__file__).parent.parent / "issue-creation-tracker.py"
_spec = importlib.util.spec_from_file_location("issue_creation_tracker", HOOK_PATH)
issue_creation_tracker = importlib.util.module_from_spec(_spec)
sys.modules["issue_creation_tracker"] = issue_creation_tracker
_spec.loader.exec_module(issue_creation_tracker)


class TestExtractIssueNumber:
    """Tests for extract_issue_number function."""

    def test_extracts_issue_number_from_github_url(self):
        """Should extract issue number from GitHub URL."""
        output = "https://github.com/owner/repo/issues/123\n"
        result = issue_creation_tracker.extract_issue_number(output)
        assert result == 123

    def test_extracts_from_multiline_output(self):
        """Should extract issue number from multiline output."""
        output = """Creating issue in owner/repo

https://github.com/owner/repo/issues/456
"""
        result = issue_creation_tracker.extract_issue_number(output)
        assert result == 456

    def test_returns_none_for_no_url(self):
        """Should return None when no GitHub URL found."""
        output = "Some random output without URL"
        result = issue_creation_tracker.extract_issue_number(output)
        assert result is None

    def test_returns_none_for_empty_string(self):
        """Should return None for empty string."""
        result = issue_creation_tracker.extract_issue_number("")
        assert result is None

    def test_handles_pr_url(self):
        """Should not match PR URLs."""
        output = "https://github.com/owner/repo/pull/789"
        result = issue_creation_tracker.extract_issue_number(output)
        assert result is None


class TestSessionIssuesPersistence:
    """Tests for load_session_issues and save_session_issues functions."""

    def setup_method(self):
        """Create temporary directory for session data."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.session_dir_patcher = patch.object(
            issue_creation_tracker, "_get_session_log_dir", return_value=self.temp_path
        )
        self.session_dir_patcher.start()
        self.test_session_id = "test-session-123"

    def teardown_method(self):
        """Clean up temporary directory."""
        self.session_dir_patcher.stop()
        self.temp_dir.cleanup()

    def test_load_returns_empty_list_when_file_not_exists(self):
        """Should return empty list when session file doesn't exist."""
        result = issue_creation_tracker.load_session_issues(self.test_session_id)
        assert result == []

    def test_save_and_load_issues(self):
        """Should save and load issues correctly."""
        issues = [123, 456, 789]
        issue_creation_tracker.save_session_issues(self.test_session_id, issues)
        result = issue_creation_tracker.load_session_issues(self.test_session_id)
        assert result == issues

    def test_load_handles_invalid_json(self):
        """Should return empty list for invalid JSON."""
        issues_file = issue_creation_tracker.get_session_issues_file(self.test_session_id)
        issues_file.parent.mkdir(parents=True, exist_ok=True)
        issues_file.write_text("not valid json")
        result = issue_creation_tracker.load_session_issues(self.test_session_id)
        assert result == []

    def test_load_handles_missing_issues_key(self):
        """Should return empty list when issues key is missing."""
        issues_file = issue_creation_tracker.get_session_issues_file(self.test_session_id)
        issues_file.parent.mkdir(parents=True, exist_ok=True)
        issues_file.write_text('{"other": "data"}')
        result = issue_creation_tracker.load_session_issues(self.test_session_id)
        assert result == []

    def test_files_are_session_specific(self):
        """Should use session-specific filenames to avoid conflicts."""
        session_a = "session-a"
        session_b = "session-b"

        # Save different issues to different sessions
        issue_creation_tracker.save_session_issues(session_a, [100])
        issue_creation_tracker.save_session_issues(session_b, [200])

        # Each session should see only its own issues
        assert issue_creation_tracker.load_session_issues(session_a) == [100]
        assert issue_creation_tracker.load_session_issues(session_b) == [200]


class TestMain:
    """Integration tests for main() function."""

    def setup_method(self):
        """Create temporary directory for session data."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.session_dir_patcher = patch.object(
            issue_creation_tracker, "_get_session_log_dir", return_value=self.temp_path
        )
        self.session_dir_patcher.start()
        self.test_session_id = "test-main-session"

    def teardown_method(self):
        """Clean up temporary directory."""
        self.session_dir_patcher.stop()
        self.temp_dir.cleanup()

    def _run_main_with_input(self, input_data: dict) -> dict:
        """Helper to run main() with given input and capture output."""
        from unittest.mock import MagicMock

        captured_output = io.StringIO()
        input_json = json.dumps(input_data)

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = self.test_session_id

        with (
            patch("sys.stdin", io.StringIO(input_json)),
            patch("sys.stdout", captured_output),
            patch("issue_creation_tracker.log_hook_execution"),
            patch("issue_creation_tracker.create_hook_context", return_value=mock_ctx),
        ):
            issue_creation_tracker.main()

        return json.loads(captured_output.getvalue())

    def test_ignores_non_bash_tool(self):
        """Should ignore non-Bash tools."""
        input_data = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/some/file.py"},
            "tool_result": {"stdout": "File edited", "exit_code": 0},
        }
        result = self._run_main_with_input(input_data)
        assert result["continue"]

    def test_ignores_non_issue_create_command(self):
        """Should ignore non-issue-create commands."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr list"},
            "tool_result": {"stdout": "PR list output", "exit_code": 0},
        }
        result = self._run_main_with_input(input_data)
        assert result["continue"]

    def test_records_issue_from_gh_issue_create(self):
        """Should record issue number from gh issue create output."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test'"},
            "tool_result": {
                "stdout": "https://github.com/owner/repo/issues/999",
                "exit_code": 0,
            },
        }
        result = self._run_main_with_input(input_data)
        assert result["continue"]

        # Check issue was recorded
        issues = issue_creation_tracker.load_session_issues(self.test_session_id)
        assert 999 in issues

    def test_does_not_duplicate_issues(self):
        """Should not record duplicate issue numbers."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test'"},
            "tool_result": {
                "stdout": "https://github.com/owner/repo/issues/100",
                "exit_code": 0,
            },
        }

        # Run twice with same issue number
        self._run_main_with_input(input_data)
        self._run_main_with_input(input_data)

        # Should only have one entry
        issues = issue_creation_tracker.load_session_issues(self.test_session_id)
        assert issues.count(100) == 1

    def test_handles_empty_output(self):
        """Should handle empty tool output gracefully."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test'"},
            "tool_result": {"stdout": "", "exit_code": 0},
        }
        result = self._run_main_with_input(input_data)
        assert result["continue"]

    def test_does_not_record_on_failed_command(self):
        """Should not record issue when command fails (non-zero exit_code)."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test'"},
            "tool_result": {
                "stdout": "https://github.com/owner/repo/issues/888",
                "exit_code": 1,
            },
        }
        result = self._run_main_with_input(input_data)
        assert result["continue"]

        # Issue should NOT be recorded due to failed command
        issues = issue_creation_tracker.load_session_issues(self.test_session_id)
        assert 888 not in issues

    def test_ignores_echo_command_with_gh_issue_create(self):
        """Should not record from echo commands that mention gh issue create."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": 'echo "gh issue create --title Test"'},
            "tool_result": {
                "stdout": "gh issue create --title Test",
                "exit_code": 0,
            },
        }
        result = self._run_main_with_input(input_data)
        assert result["continue"]

        # No issue URL in output, so nothing should be recorded
        issues = issue_creation_tracker.load_session_issues(self.test_session_id)
        assert issues == []


class TestExtractPriorityFromCommand:
    """Tests for extract_priority_from_command function (Issue #1950)."""

    def test_extracts_p0_from_label_option(self):
        """Should extract P0 from --label P0."""
        command = "gh issue create --title 'Test' --label P0"
        result = issue_creation_tracker.extract_priority_from_command(command)
        assert result == "P0"

    def test_extracts_p1_from_label_option(self):
        """Should extract P1 from --label P1."""
        command = "gh issue create --title 'Test' --label P1"
        result = issue_creation_tracker.extract_priority_from_command(command)
        assert result == "P1"

    def test_extracts_p2_from_label_option(self):
        """Should extract P2 from --label P2."""
        command = "gh issue create --title 'Test' --label P2"
        result = issue_creation_tracker.extract_priority_from_command(command)
        assert result == "P2"

    def test_extracts_from_short_option(self):
        """Should extract priority from -l option."""
        command = "gh issue create -l P0"
        result = issue_creation_tracker.extract_priority_from_command(command)
        assert result == "P0"

    def test_extracts_from_quoted_label(self):
        """Should extract priority from quoted label."""
        command = 'gh issue create --label "P1"'
        result = issue_creation_tracker.extract_priority_from_command(command)
        assert result == "P1"

    def test_extracts_from_priority_prefix(self):
        """Should extract priority from priority:P0 format."""
        command = "gh issue create --label priority:P0"
        result = issue_creation_tracker.extract_priority_from_command(command)
        assert result == "P0"

    def test_returns_none_for_no_priority(self):
        """Should return None when no priority label found."""
        command = "gh issue create --title 'Test' --label bug"
        result = issue_creation_tracker.extract_priority_from_command(command)
        assert result is None

    def test_returns_highest_priority_p0_over_p1(self):
        """Should return P0 when both P0 and P1 are present."""
        command = "gh issue create --label P1 --label P0"
        result = issue_creation_tracker.extract_priority_from_command(command)
        assert result == "P0"

    def test_returns_highest_priority_p1_over_p2(self):
        """Should return P1 when both P1 and P2 are present."""
        command = "gh issue create --label P2 --label P1"
        result = issue_creation_tracker.extract_priority_from_command(command)
        assert result == "P1"

    def test_case_insensitive(self):
        """Should match priority labels case-insensitively."""
        command = "gh issue create --label p0"
        result = issue_creation_tracker.extract_priority_from_command(command)
        assert result == "P0"

    def test_extracts_from_equals_syntax(self):
        """Should extract priority from --label=P0 syntax."""
        command = "gh issue create --label=P0"
        result = issue_creation_tracker.extract_priority_from_command(command)
        assert result == "P0"

    def test_extracts_from_short_equals_syntax(self):
        """Should extract priority from -l=P1 syntax."""
        command = 'gh issue create -l="P1"'
        result = issue_creation_tracker.extract_priority_from_command(command)
        assert result == "P1"

    def test_extracts_from_comma_separated_labels(self):
        """Should extract priority from comma-separated labels like --label 'bug,P1'."""
        command = 'gh issue create --label "bug,P1"'
        result = issue_creation_tracker.extract_priority_from_command(command)
        assert result == "P1"

    def test_extracts_from_complex_comma_separated(self):
        """Should extract priority from complex comma-separated labels."""
        command = 'gh issue create --label "type:bug,priority:P2,frontend"'
        result = issue_creation_tracker.extract_priority_from_command(command)
        assert result == "P2"


class TestApiPriorityFallback:
    """Tests for API fallback when priority not in command (Issue #1950)."""

    def test_fallback_to_api_when_no_label_in_command(self):
        """Should use API to get priority when command has no priority label."""
        from unittest.mock import MagicMock

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with (
            patch("issue_creation_tracker.create_hook_context", return_value=mock_ctx),
            patch("issue_creation_tracker.load_session_issues", return_value=[]),
            patch("issue_creation_tracker.save_session_issues"),
            patch("issue_creation_tracker.get_issue_priority", return_value="P1") as mock_api,
            patch("issue_creation_tracker.log_hook_execution"),
        ):
            input_data = {
                "tool_name": "Bash",
                "tool_input": {"command": "gh issue create --label bug --title 'Test'"},
                "tool_result": {
                    "stdout": "https://github.com/owner/repo/issues/123",
                    "exit_code": 0,
                },
            }
            with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
                with patch("builtins.print") as mock_print:
                    issue_creation_tracker.main()

                    # Verify API was called (fallback triggered)
                    mock_api.assert_called_once_with(123)

                    # Verify P1 message was output
                    output = mock_print.call_args[0][0]
                    result = json.loads(output)
                    assert "P1 Issue を作成しました" in result["systemMessage"]


class TestGetIssuePriority:
    """Tests for get_issue_priority function (Issue #1943)."""

    def test_returns_p0_for_p0_label(self):
        """Should return 'P0' when issue has P0 label."""
        with patch("issue_creation_tracker.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"labels": [{"name": "P0"}]}'

            result = issue_creation_tracker.get_issue_priority(123)
            assert result == "P0"

    def test_returns_p0_for_priority_p0_label(self):
        """Should return 'P0' when issue has priority:P0 label."""
        with patch("issue_creation_tracker.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"labels": [{"name": "priority:P0"}]}'

            result = issue_creation_tracker.get_issue_priority(123)
            assert result == "P0"

    def test_returns_p1_for_p1_label(self):
        """Should return 'P1' when issue has P1 label."""
        with patch("issue_creation_tracker.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"labels": [{"name": "p1"}]}'

            result = issue_creation_tracker.get_issue_priority(123)
            assert result == "P1"

    def test_returns_p2_for_p2_label(self):
        """Should return 'P2' when issue has P2 label."""
        with patch("issue_creation_tracker.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"labels": [{"name": "P2"}]}'

            result = issue_creation_tracker.get_issue_priority(123)
            assert result == "P2"

    def test_returns_none_for_no_priority_label(self):
        """Should return None when issue has no priority label."""
        with patch("issue_creation_tracker.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"labels": [{"name": "bug"}, {"name": "enhancement"}]}'

            result = issue_creation_tracker.get_issue_priority(123)
            assert result is None

    def test_returns_none_for_empty_labels(self):
        """Should return None when issue has no labels."""
        with patch("issue_creation_tracker.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"labels": []}'

            result = issue_creation_tracker.get_issue_priority(123)
            assert result is None

    def test_returns_none_on_command_failure(self):
        """Should return None when gh command fails."""
        with patch("issue_creation_tracker.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""

            result = issue_creation_tracker.get_issue_priority(123)
            assert result is None

    def test_returns_none_on_timeout(self):
        """Should return None when gh command times out."""
        import subprocess

        with patch("issue_creation_tracker.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=5)

            result = issue_creation_tracker.get_issue_priority(123)
            assert result is None

    def test_returns_none_on_invalid_json(self):
        """Should return None when gh command returns invalid JSON."""
        with patch("issue_creation_tracker.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "not valid json"

            result = issue_creation_tracker.get_issue_priority(123)
            assert result is None

    def test_returns_p0_when_p0_and_p1_both_exist(self):
        """Should return P0 when both P0 and P1 labels exist (higher priority first)."""
        with patch("issue_creation_tracker.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"labels": [{"name": "P1"}, {"name": "P0"}]}'

            result = issue_creation_tracker.get_issue_priority(123)
            assert result == "P0"

    def test_returns_p1_when_p1_and_p2_both_exist(self):
        """Should return P1 when both P1 and P2 labels exist (higher priority first)."""
        with patch("issue_creation_tracker.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"labels": [{"name": "P2"}, {"name": "P1"}]}'

            result = issue_creation_tracker.get_issue_priority(123)
            assert result == "P1"


class TestMainWithPriorityMessage:
    """Tests for main() with priority-based messages (Issue #1943)."""

    def setup_method(self):
        """Create temporary directory for session data."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.session_dir_patcher = patch.object(
            issue_creation_tracker, "_get_session_log_dir", return_value=self.temp_path
        )
        self.session_dir_patcher.start()
        self.test_session_id = "test-priority-session"

    def teardown_method(self):
        """Clean up temporary directory."""
        self.session_dir_patcher.stop()
        self.temp_dir.cleanup()

    def _run_main_with_input(self, input_data: dict, priority: str | None = None) -> dict:
        """Helper to run main() with given input and capture output."""
        from unittest.mock import MagicMock

        captured_output = io.StringIO()
        input_json = json.dumps(input_data)

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = self.test_session_id
        with (
            patch("sys.stdin", io.StringIO(input_json)),
            patch("sys.stdout", captured_output),
            patch("issue_creation_tracker.log_hook_execution"),
            patch("issue_creation_tracker.create_hook_context", return_value=mock_ctx),
            patch("issue_creation_tracker.get_issue_priority", return_value=priority),
        ):
            issue_creation_tracker.main()

        return json.loads(captured_output.getvalue())

    def test_p0_issue_outputs_warning_message(self):
        """Should output warning message for P0 issue."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test' --label P0"},
            "tool_result": {
                "stdout": "https://github.com/owner/repo/issues/500",
                "exit_code": 0,
            },
        }
        result = self._run_main_with_input(input_data, priority="P0")
        assert result["continue"]
        assert "systemMessage" in result
        assert "P0 Issue" in result["systemMessage"]
        assert "即時実装が必要" in result["systemMessage"]
        assert "#500" in result["systemMessage"]
        # Issue #2121: Verify prohibition and required action messages
        assert "禁止" in result["systemMessage"]
        assert "実装しますか？" in result["systemMessage"]
        assert "必須" in result["systemMessage"]
        assert "worktreeを作成" in result["systemMessage"]

    def test_p1_issue_outputs_info_message(self):
        """Should output info message with P1 explicitly shown (Issue #1951)."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test' --label P1"},
            "tool_result": {
                "stdout": "https://github.com/owner/repo/issues/501",
                "exit_code": 0,
            },
        }
        result = self._run_main_with_input(input_data, priority="P1")
        assert result["continue"]
        assert "systemMessage" in result
        assert "P1 Issue を作成しました" in result["systemMessage"]
        assert "現在のタスクを完遂後" in result["systemMessage"]
        assert "#501" in result["systemMessage"]
        # Issue #2121: Verify prohibition and required action messages
        assert "禁止" in result["systemMessage"]
        assert "実装しますか？" in result["systemMessage"]
        assert "必須" in result["systemMessage"]
        assert "worktreeを作成" in result["systemMessage"]

    def test_no_priority_outputs_info_message(self):
        """Should output info message for issue without priority label."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test'"},
            "tool_result": {
                "stdout": "https://github.com/owner/repo/issues/502",
                "exit_code": 0,
            },
        }
        result = self._run_main_with_input(input_data, priority=None)
        assert result["continue"]
        assert "systemMessage" in result
        assert "Issue を作成しました" in result["systemMessage"]
        assert "#502" in result["systemMessage"]
        assert "現在のタスクを完遂後" in result["systemMessage"]
        # Issue #2121: Verify prohibition and required action messages
        assert "禁止" in result["systemMessage"]
        assert "実装しますか？" in result["systemMessage"]
        assert "必須" in result["systemMessage"]
        assert "worktreeを作成" in result["systemMessage"]

    def test_p2_issue_outputs_info_message(self):
        """Should output info message with P2 explicitly shown (Issue #1951)."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test' --label P2"},
            "tool_result": {
                "stdout": "https://github.com/owner/repo/issues/504",
                "exit_code": 0,
            },
        }
        result = self._run_main_with_input(input_data, priority="P2")
        assert result["continue"]
        assert "systemMessage" in result
        assert "P2 Issue を作成しました" in result["systemMessage"]
        assert "現在のタスクを完遂後" in result["systemMessage"]
        assert "#504" in result["systemMessage"]
        # Issue #2121: Verify prohibition and required action messages
        assert "禁止" in result["systemMessage"]
        assert "実装しますか？" in result["systemMessage"]
        assert "必須" in result["systemMessage"]
        assert "worktreeを作成" in result["systemMessage"]

    def test_duplicate_issue_does_not_output_message(self):
        """Should not output message for duplicate issue recording."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test'"},
            "tool_result": {
                "stdout": "https://github.com/owner/repo/issues/503",
                "exit_code": 0,
            },
        }
        # First call - should have message
        result1 = self._run_main_with_input(input_data, priority="P1")
        assert "systemMessage" in result1

        # Second call with same issue - should NOT have message (duplicate)
        result2 = self._run_main_with_input(input_data, priority="P1")
        assert "systemMessage" not in result2
