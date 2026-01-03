#!/usr/bin/env python3
"""Tests for issue-incomplete-close-check.py."""

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# issue-incomplete-close-check.py has hyphens, so we need dynamic import
HOOK_PATH = Path(__file__).parent.parent / "issue-incomplete-close-check.py"
_spec = importlib.util.spec_from_file_location("issue_incomplete_close_check", HOOK_PATH)
hook_module = importlib.util.module_from_spec(_spec)
sys.modules["issue_incomplete_close_check"] = hook_module
_spec.loader.exec_module(hook_module)


class TestExtractIssueNumber:
    """Tests for extract_issue_number function."""

    def test_simple_close(self):
        """Test simple gh issue close command."""
        result = hook_module.extract_issue_number("gh issue close 123")
        assert result == "123"

    def test_close_with_hash(self):
        """Test close with # prefix."""
        result = hook_module.extract_issue_number("gh issue close #456")
        assert result == "456"

    def test_close_with_options(self):
        """Test close with options."""
        result = hook_module.extract_issue_number('gh issue close 789 --comment "done"')
        assert result == "789"

    def test_close_with_reason(self):
        """Test close with reason flag."""
        result = hook_module.extract_issue_number("gh issue close 100 --reason completed")
        assert result == "100"

    def test_not_close_command(self):
        """Test non-close commands return None."""
        assert hook_module.extract_issue_number("gh issue view 123") is None
        assert hook_module.extract_issue_number("gh issue list") is None
        assert hook_module.extract_issue_number("gh pr close 123") is None

    def test_no_issue_number(self):
        """Test close without number returns None."""
        assert hook_module.extract_issue_number("gh issue close") is None


class TestParseCheckboxes:
    """Tests for parse_checkboxes function."""

    def test_all_checked(self):
        """Test parsing when all checkboxes are checked."""
        body = """
## Tasks
- [x] Task 1
- [x] Task 2
- [x] Task 3
"""
        checked, unchecked = hook_module.parse_checkboxes(body)
        assert len(checked) == 3
        assert len(unchecked) == 0
        assert "Task 1" in checked

    def test_all_unchecked(self):
        """Test parsing when all checkboxes are unchecked."""
        body = """
## Tasks
- [ ] Task 1
- [ ] Task 2
"""
        checked, unchecked = hook_module.parse_checkboxes(body)
        assert len(checked) == 0
        assert len(unchecked) == 2
        assert "Task 1" in unchecked

    def test_mixed_checkboxes(self):
        """Test parsing with mixed checked/unchecked."""
        body = """
## Tasks
- [x] Completed task
- [ ] Pending task 1
- [X] Another completed (uppercase X)
- [ ] Pending task 2
"""
        checked, unchecked = hook_module.parse_checkboxes(body)
        assert len(checked) == 2
        assert len(unchecked) == 2
        assert "Completed task" in checked
        assert "Pending task 1" in unchecked

    def test_no_checkboxes(self):
        """Test parsing when no checkboxes exist."""
        body = """
## Description
This is a simple issue with no checkboxes.
- Just a bullet point
"""
        checked, unchecked = hook_module.parse_checkboxes(body)
        assert len(checked) == 0
        assert len(unchecked) == 0

    def test_asterisk_checkboxes(self):
        """Test parsing with asterisk bullets."""
        body = """
* [x] Task with asterisk
* [ ] Unchecked with asterisk
"""
        checked, unchecked = hook_module.parse_checkboxes(body)
        assert len(checked) == 1
        assert len(unchecked) == 1

    def test_long_text_truncation(self):
        """Test that long checkbox text is truncated."""
        long_text = "A" * 100
        body = f"- [ ] {long_text}"
        checked, unchecked = hook_module.parse_checkboxes(body)
        assert len(unchecked) == 1
        # Should be truncated to 80 chars (77 + ...)
        assert len(unchecked[0]) <= 80
        assert unchecked[0].endswith("...")


class TestMainFunction:
    """Integration tests for main function."""

    def test_non_bash_tool_approves(self):
        """Test that non-Bash tools are approved."""
        captured_output = io.StringIO()

        with (
            patch("sys.stdin", io.StringIO('{"tool_name": "Read"}')),
            patch("sys.stdout", captured_output),
        ):
            hook_module.main()

        result = json.loads(captured_output.getvalue())
        # Issue #1607: Now uses print_continue_and_log_skip which returns {"continue": True}
        assert result.get("continue") is True or result.get("decision") == "approve"

    def test_non_close_command_approves(self):
        """Test that non-close commands are approved."""
        captured_output = io.StringIO()
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue view 123"},
        }

        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("sys.stdout", captured_output),
            patch.object(hook_module, "log_hook_execution"),
        ):
            hook_module.main()

        result = json.loads(captured_output.getvalue())
        # Issue #1607: Now uses print_continue_and_log_skip which returns {"continue": True}
        assert result.get("continue") is True or result.get("decision") == "approve"

    def test_skip_env_var_approves(self):
        """Test that SKIP_INCOMPLETE_CHECK=1 bypasses check."""
        captured_output = io.StringIO()
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue close 123"},
        }

        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("sys.stdout", captured_output),
            patch.dict("os.environ", {"SKIP_INCOMPLETE_CHECK": "1"}),
            patch.object(hook_module, "log_hook_execution"),
        ):
            hook_module.main()

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"

    def test_no_checkboxes_approves(self):
        """Test that issues without checkboxes are approved."""
        captured_output = io.StringIO()
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue close 123"},
        }

        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("sys.stdout", captured_output),
            patch.object(hook_module, "get_issue_body", return_value="No checkboxes here"),
            patch.object(hook_module, "log_hook_execution"),
        ):
            hook_module.main()

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"

    def test_all_checked_approves(self):
        """Test that issues with all checked boxes are approved."""
        captured_output = io.StringIO()
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue close 123"},
        }
        body = "- [x] Done 1\n- [x] Done 2"

        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("sys.stdout", captured_output),
            patch.object(hook_module, "get_issue_body", return_value=body),
            patch.object(hook_module, "log_hook_execution"),
        ):
            hook_module.main()

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"

    def test_unchecked_items_blocks(self):
        """Test that issues with unchecked items are blocked."""
        captured_output = io.StringIO()
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue close 123"},
        }
        body = "- [x] Done\n- [ ] Not done"

        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("sys.stdout", captured_output),
            patch.object(hook_module, "get_issue_body", return_value=body),
            patch.object(hook_module, "log_hook_execution"),
        ):
            hook_module.main()

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "block"
        assert "未完了項目" in result["reason"]
        assert "Not done" in result["reason"]

    def test_body_fetch_failure_approves(self):
        """Test that API failures don't block."""
        captured_output = io.StringIO()
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue close 123"},
        }

        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("sys.stdout", captured_output),
            patch.object(hook_module, "get_issue_body", return_value=None),
            patch.object(hook_module, "log_hook_execution"),
        ):
            hook_module.main()

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"
