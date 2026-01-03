#!/usr/bin/env python3
"""Tests for development-workflow-tracker.py."""

import importlib.util
import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestExtractIssueNumber:
    """Tests for extract_issue_number_from_worktree function."""

    def setup_method(self):
        """Load the module."""
        # Add parent directory to path
        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        # Load the module dynamically
        spec = importlib.util.spec_from_file_location(
            "development_workflow_tracker",
            Path(__file__).parent.parent / "development-workflow-tracker.py",
        )
        self.module = importlib.util.module_from_spec(spec)

        # Mock common module
        mock_common = MagicMock()
        mock_common.start_flow = MagicMock(return_value="test-instance-id")
        mock_common.complete_flow_step = MagicMock(return_value=True)
        mock_common.log_hook_execution = MagicMock()

        with patch.dict("sys.modules", {"common": mock_common}):
            spec.loader.exec_module(self.module)

    def test_extract_issue_number_standard_format(self):
        """Extract issue number from standard worktree add command."""
        command = "git worktree add ../.worktrees/issue-123 -b issue-123"
        result = self.module.extract_issue_number_from_worktree(command)
        assert result == 123

    def test_extract_issue_number_absolute_path(self):
        """Extract issue number from absolute path."""
        command = "git worktree add /path/to/issue-456 -b issue-456"
        result = self.module.extract_issue_number_from_worktree(command)
        assert result == 456

    def test_extract_issue_number_only_in_branch(self):
        """Extract issue number when only in branch name."""
        command = "git worktree add ../temp -b issue-789"
        result = self.module.extract_issue_number_from_worktree(command)
        assert result == 789

    def test_extract_issue_number_case_insensitive(self):
        """Extract issue number case-insensitively."""
        command = "git worktree add ../.worktrees/Issue-101"
        result = self.module.extract_issue_number_from_worktree(command)
        assert result == 101

    def test_extract_issue_number_no_issue(self):
        """Return None when no issue number pattern."""
        command = "git worktree add ../feature-branch -b feature-branch"
        result = self.module.extract_issue_number_from_worktree(command)
        assert result is None


class TestMainFunction:
    """Tests for main function."""

    def setup_method(self):
        """Set up test fixtures."""
        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

    def _run_main_with_input(self, input_data: dict, mock_common: MagicMock) -> dict:
        """Helper to run main with input data and return output."""
        spec = importlib.util.spec_from_file_location(
            "development_workflow_tracker",
            Path(__file__).parent.parent / "development-workflow-tracker.py",
        )
        module = importlib.util.module_from_spec(spec)

        # Issue #2014: Mock lib modules directly since they are now imported separately
        mock_lib_session = MagicMock()
        mock_lib_session.parse_hook_input = MagicMock(return_value=input_data)
        # Issue #2607: Also mock create_hook_context
        mock_lib_session.create_hook_context = MagicMock(return_value=MagicMock())

        mock_lib_results = MagicMock()

        # Issue #2607: Accept ctx keyword argument
        def mock_print_continue_and_log_skip(hook_name: str, reason: str, ctx=None) -> None:
            print(json.dumps({"continue": True}))

        mock_lib_results.print_continue_and_log_skip = mock_print_continue_and_log_skip

        mock_lib_execution = MagicMock()
        mock_lib_execution.log_hook_execution = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "common": mock_common,
                "lib.session": mock_lib_session,
                "lib.results": mock_lib_results,
                "lib.execution": mock_lib_execution,
            },
        ):
            spec.loader.exec_module(module)

            # Mock stdout
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                module.main()
                return json.loads(mock_stdout.getvalue())

    def test_main_ignores_non_bash_tool(self):
        """main should ignore non-Bash tools."""
        mock_common = MagicMock()

        input_data = {"tool_name": "Edit", "tool_input": {"file_path": "test.py"}}

        result = self._run_main_with_input(input_data, mock_common)

        assert result.get("continue")
        mock_common.start_flow.assert_not_called()

    def test_main_ignores_failed_commands(self):
        """main should ignore failed commands."""
        mock_common = MagicMock()

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add ../.worktrees/issue-123"},
            "tool_result": {"exit_code": 1},
        }

        result = self._run_main_with_input(input_data, mock_common)

        assert result.get("continue")
        mock_common.start_flow.assert_not_called()

    def test_main_starts_flow_for_worktree_add(self):
        """main should start flow for git worktree add with issue number."""
        mock_common = MagicMock()
        mock_common.start_flow = MagicMock(return_value="test-instance-id")
        mock_common.complete_flow_step = MagicMock(return_value=True)
        mock_common.log_hook_execution = MagicMock()

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add ../.worktrees/issue-123 -b issue-123"},
            "tool_result": {"exit_code": 0},
        }

        result = self._run_main_with_input(input_data, mock_common)

        assert result.get("continue")
        assert "systemMessage" in result
        assert "123" in result["systemMessage"]

        mock_common.start_flow.assert_called_once_with(
            "development-workflow", {"issue_number": 123}
        )
        mock_common.complete_flow_step.assert_called_once_with(
            "test-instance-id", "worktree_created", "development-workflow"
        )

    def test_main_handles_worktree_without_issue(self):
        """main should handle worktree add without issue number."""
        mock_common = MagicMock()
        mock_common.log_hook_execution = MagicMock()

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add ../feature-branch -b feature"},
            "tool_result": {"exit_code": 0},
        }

        result = self._run_main_with_input(input_data, mock_common)

        assert result.get("continue")
        assert "systemMessage" not in result
        mock_common.start_flow.assert_not_called()

    def test_main_starts_flow_for_cd_and_worktree_add(self):
        """main should start flow for cd ... && git worktree add pattern.

        Issue #2534: Commands with `cd /path && git worktree add` prefix
        should also trigger development workflow start.
        """
        mock_common = MagicMock()
        mock_common.start_flow = MagicMock(return_value="test-instance-id")
        mock_common.complete_flow_step = MagicMock(return_value=True)
        mock_common.log_hook_execution = MagicMock()

        input_data = {
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "cd /Users/test/repo && "
                    "git worktree add --lock .worktrees/issue-2525 "
                    "-b feat/issue-2525-fix"
                )
            },
            "tool_result": {"exit_code": 0},
        }

        result = self._run_main_with_input(input_data, mock_common)

        assert result.get("continue")
        assert "systemMessage" in result
        assert "2525" in result["systemMessage"]

        mock_common.start_flow.assert_called_once_with(
            "development-workflow", {"issue_number": 2525}
        )
        mock_common.complete_flow_step.assert_called_once_with(
            "test-instance-id", "worktree_created", "development-workflow"
        )
