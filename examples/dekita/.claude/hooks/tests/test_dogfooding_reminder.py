#!/usr/bin/env python3
"""Tests for dogfooding-reminder.py hook.

Issue #1942: Tests for the Dogfooding reminder hook that prompts developers
to test scripts with real data before committing.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from lib.session import create_hook_context

# Import module with hyphen in name
spec = importlib.util.spec_from_file_location(
    "dogfooding_reminder", HOOKS_DIR / "dogfooding-reminder.py"
)
dogfooding_reminder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dogfooding_reminder)

build_reminder_message = dogfooding_reminder.build_reminder_message
has_data_processing_patterns = dogfooding_reminder.has_data_processing_patterns
is_new_script = dogfooding_reminder.is_new_script
mark_as_reminded = dogfooding_reminder.mark_as_reminded
was_already_reminded = dogfooding_reminder.was_already_reminded


class TestHasDataProcessingPatterns:
    """Tests for has_data_processing_patterns function."""

    def test_detects_requests_usage(self):
        """Should detect requests library usage."""
        content = """
import requests
response = requests.get(url)
"""
        assert has_data_processing_patterns(content) is True

    def test_detects_subprocess_usage(self):
        """Should detect subprocess usage."""
        content = """
import subprocess
result = subprocess.run(['ls', '-la'])
"""
        assert has_data_processing_patterns(content) is True

    def test_detects_json_parsing(self):
        """Should detect JSON parsing."""
        content = """
data = json.loads(response_text)
"""
        assert has_data_processing_patterns(content) is True

    def test_detects_file_reading(self):
        """Should detect file reading operations."""
        content = """
with open(filename) as f:
    data = f.read()
"""
        assert has_data_processing_patterns(content) is True

    def test_detects_run_gh_command(self):
        """Should detect run_gh_command helper usage."""
        content = """
output = run_gh_command(['issue', 'list'])
"""
        assert has_data_processing_patterns(content) is True

    def test_no_data_processing(self):
        """Should return False for simple scripts without data processing."""
        content = """
def hello():
    print("Hello, World!")

if __name__ == "__main__":
    hello()
"""
        assert has_data_processing_patterns(content) is False

    def test_detects_string_split(self):
        """Should detect string split operations (common parsing)."""
        content = """
parts = line.split(",")
"""
        assert has_data_processing_patterns(content) is True


class TestIsNewScript:
    """Tests for is_new_script function."""

    def test_write_tool_new_file(self):
        """Write tool with non-existent file is new script."""
        with patch.object(dogfooding_reminder, "Path") as mock_path:
            mock_path.return_value.exists.return_value = False
            assert is_new_script("/nonexistent/file.py", "Write", "") is True

    def test_write_tool_existing_file(self):
        """Write tool with existing file is not new script."""
        with patch.object(dogfooding_reminder, "Path") as mock_path:
            mock_path.return_value.exists.return_value = True
            assert is_new_script("/existing/file.py", "Write", "") is False

    def test_edit_tool_with_empty_old_string(self):
        """Edit tool with empty old_string might be new content."""
        assert is_new_script("/file.py", "Edit", "") is True

    def test_edit_tool_with_short_old_string(self):
        """Edit tool with very short old_string might be initial content."""
        assert is_new_script("/file.py", "Edit", "# comment") is True

    def test_edit_tool_with_substantial_old_string(self):
        """Edit tool with substantial old_string is not new script."""
        # Content must be > 50 characters to be considered substantial
        old_content = """def foo():
    '''This is a function that does something useful.'''
    result = calculate_complex_operation()
    return result
"""
        assert is_new_script("/file.py", "Edit", old_content) is False


class TestReminderTracking:
    """Tests for reminder tracking functions."""

    TEST_SESSION_ID = "test-session-tracking"

    def setup_method(self):
        """Create temp directory for tracking files."""
        import tempfile

        self.temp_dir = tempfile.mkdtemp()
        # Patch the tracking directory
        dogfooding_reminder._TRACKING_DIR = Path(self.temp_dir)
        # Create HookContext for tests
        self.ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})

    def teardown_method(self):
        """Clean up temp directory."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_was_already_reminded_false_initially(self):
        """Should return False for files not yet reminded."""
        assert was_already_reminded(self.ctx, "/path/to/file.py") is False

    def test_mark_and_check_reminded(self):
        """Should track reminded files correctly."""
        file_path = "/path/to/script.py"
        mark_as_reminded(self.ctx, file_path)
        assert was_already_reminded(self.ctx, file_path) is True

    def test_multiple_files_tracked(self):
        """Should track multiple files."""
        file1 = "/path/to/script1.py"
        file2 = "/path/to/script2.py"
        mark_as_reminded(self.ctx, file1)
        mark_as_reminded(self.ctx, file2)
        assert was_already_reminded(self.ctx, file1) is True
        assert was_already_reminded(self.ctx, file2) is True
        assert was_already_reminded(self.ctx, "/other/file.py") is False


class TestBuildReminderMessage:
    """Tests for build_reminder_message function."""

    def test_new_script_message(self):
        """Should include '新規スクリプト作成' for new scripts."""
        message = build_reminder_message("/path/to/script.py", is_new=True)
        assert "新規スクリプト作成" in message
        assert "/path/to/script.py" in message
        assert "Issue #1942" in message

    def test_modified_script_message(self):
        """Should include 'スクリプト変更' for modified scripts."""
        message = build_reminder_message("/path/to/script.py", is_new=False)
        assert "スクリプト変更" in message
        assert "/path/to/script.py" in message

    def test_includes_checklist(self):
        """Should include Dogfooding checklist items."""
        message = build_reminder_message("/path/to/script.py", is_new=True)
        assert "実際のデータで動作確認" in message
        assert "エッジケース" in message
        assert "テストファイル" in message


class TestHookIntegration:
    """Integration tests for the hook."""

    def setup_method(self):
        """Create temp directory for tracking files."""
        import tempfile

        self.temp_dir = tempfile.mkdtemp()

    def teardown_method(self):
        """Clean up temp directory."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_hook_continues_for_non_script_file(self):
        """Hook should continue for non-script files."""
        hook_input = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/path/to/component.tsx",
                "content": "import React from 'react'",
            },
        }
        result = run_hook_with_input(hook_input, self.temp_dir)
        assert result["continue"] is True
        assert "systemMessage" not in result

    def test_hook_continues_for_test_file(self):
        """Hook should continue for test files in scripts/tests/."""
        hook_input = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": ".claude/scripts/tests/test_something.py",
                "content": "import subprocess\nsubprocess.run(['test'])",
            },
        }
        result = run_hook_with_input(hook_input, self.temp_dir)
        assert result["continue"] is True
        assert "systemMessage" not in result

    def test_hook_shows_reminder_for_script_with_data_processing(self):
        """Hook should show reminder for scripts with data processing."""
        hook_input = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": ".claude/scripts/new_script.py",
                "content": """
import json
data = json.loads(response)
""",
            },
        }
        result = run_hook_with_input(hook_input, self.temp_dir)
        assert result["continue"] is True
        assert "systemMessage" in result
        assert "Dogfooding" in result["systemMessage"]

    def test_hook_skips_script_without_data_processing(self):
        """Hook should skip scripts without data processing patterns."""
        hook_input = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": ".claude/scripts/simple_script.py",
                "content": """
def hello():
    print("Hello")
""",
            },
        }
        result = run_hook_with_input(hook_input, self.temp_dir)
        assert result["continue"] is True
        assert "systemMessage" not in result

    def test_hook_deduplication_no_repeat_reminder(self):
        """Hook should not show reminder twice for the same file in same session."""
        hook_input = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": ".claude/scripts/dedup_test_script.py",
                "content": """
import json
data = json.loads(response)
""",
            },
        }
        # First call should show reminder
        result1 = run_hook_with_input(hook_input, self.temp_dir)
        assert result1["continue"] is True
        assert "systemMessage" in result1
        assert "Dogfooding" in result1["systemMessage"]

        # Second call for same file should NOT show reminder
        result2 = run_hook_with_input(hook_input, self.temp_dir)
        assert result2["continue"] is True
        assert "systemMessage" not in result2


def run_hook_with_input(hook_input: dict, temp_dir: str | None = None) -> dict:
    """Run the hook with given input and return the result.

    Args:
        hook_input: Dictionary with tool_name and tool_input
        temp_dir: Optional temp directory for tracking files

    Returns:
        Parsed JSON result from the hook
    """
    import tempfile

    hook_path = HOOKS_DIR / "dogfooding-reminder.py"

    # Use provided temp_dir or create a new one
    if temp_dir is None:
        temp_dir = tempfile.mkdtemp()

    # Set environment to use the temp directory for tracking
    # Use temp_dir itself (not parent) to ensure isolation between test runs
    env = {
        **os.environ,
        "CLAUDE_PROJECT_DIR": str(temp_dir),
    }

    # Ensure session_id is present for HookContext
    if "session_id" not in hook_input:
        hook_input = {**hook_input, "session_id": "test-session"}

    result = subprocess.run(
        ["python3", str(hook_path)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(result.stdout)
