"""Tests for script-test-reminder.py PostToolUse hook."""

import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest

# Load the module with hyphen in filename
spec = importlib.util.spec_from_file_location(
    "script_test_reminder",
    Path(__file__).parent.parent / "script-test-reminder.py",
)
script_test_reminder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script_test_reminder)


class TestDetectNewFunctions:
    """Tests for detect_new_functions function."""

    def test_detects_single_new_function(self) -> None:
        """Should detect a single newly added function."""
        old_string = ""
        new_string = "def foo():\n    pass"
        result = script_test_reminder.detect_new_functions(old_string, new_string)
        assert result == ["foo"]

    def test_detects_multiple_new_functions(self) -> None:
        """Should detect multiple newly added functions."""
        old_string = "def existing():\n    pass"
        new_string = "def existing():\n    pass\n\ndef bar():\n    pass\n\ndef baz():\n    pass"
        result = script_test_reminder.detect_new_functions(old_string, new_string)
        assert sorted(result) == ["bar", "baz"]

    def test_ignores_existing_functions(self) -> None:
        """Should not report functions that already exist."""
        old_string = "def foo():\n    pass"
        new_string = "def foo():\n    # modified\n    pass"
        result = script_test_reminder.detect_new_functions(old_string, new_string)
        assert result == []

    def test_handles_empty_strings(self) -> None:
        """Should handle empty input strings."""
        assert script_test_reminder.detect_new_functions("", "") == []

    def test_ignores_indented_def(self) -> None:
        """Should only detect top-level function definitions."""
        old_string = ""
        new_string = "class Foo:\n    def method(self):\n        pass"
        result = script_test_reminder.detect_new_functions(old_string, new_string)
        # Indented def should not be detected as new function
        assert result == []

    def test_detects_function_with_args(self) -> None:
        """Should detect functions with arguments."""
        old_string = ""
        new_string = "def foo(arg1: str, arg2: int = 0) -> bool:\n    return True"
        result = script_test_reminder.detect_new_functions(old_string, new_string)
        assert result == ["foo"]

    def test_detects_async_function(self) -> None:
        """Should detect async function definitions."""
        old_string = ""
        new_string = "async def fetch_data():\n    pass"
        result = script_test_reminder.detect_new_functions(old_string, new_string)
        assert result == ["fetch_data"]

    def test_detects_mixed_sync_and_async_functions(self) -> None:
        """Should detect both sync and async functions."""
        old_string = ""
        new_string = "def sync_func():\n    pass\n\nasync def async_func():\n    pass"
        result = script_test_reminder.detect_new_functions(old_string, new_string)
        assert sorted(result) == ["async_func", "sync_func"]


class TestFindTestFile:
    """Tests for find_test_file function."""

    def test_finds_existing_test_file(self, tmp_path: Path) -> None:
        """Should return path when test file exists."""
        # Create test directory structure
        scripts_dir = tmp_path / ".claude" / "scripts"
        tests_dir = scripts_dir / "tests"
        tests_dir.mkdir(parents=True)

        # Create script and test file
        script_file = scripts_dir / "my_script.py"
        script_file.touch()
        test_file = tests_dir / "test_my_script.py"
        test_file.touch()

        result = script_test_reminder.find_test_file(str(script_file))
        assert result == test_file

    def test_returns_none_when_test_file_missing(self, tmp_path: Path) -> None:
        """Should return None when test file doesn't exist."""
        scripts_dir = tmp_path / ".claude" / "scripts"
        tests_dir = scripts_dir / "tests"
        tests_dir.mkdir(parents=True)

        script_file = scripts_dir / "no_test_script.py"
        script_file.touch()

        result = script_test_reminder.find_test_file(str(script_file))
        assert result is None

    def test_converts_hyphen_to_underscore(self, tmp_path: Path) -> None:
        """Should convert hyphens to underscores in test file name."""
        scripts_dir = tmp_path / ".claude" / "scripts"
        tests_dir = scripts_dir / "tests"
        tests_dir.mkdir(parents=True)

        # Create script with hyphen and test with underscore
        script_file = scripts_dir / "my-hyphen-script.py"
        script_file.touch()
        test_file = tests_dir / "test_my_hyphen_script.py"
        test_file.touch()

        result = script_test_reminder.find_test_file(str(script_file))
        assert result == test_file


class TestMain:
    """Integration tests for main function."""

    def test_shows_reminder_for_new_function_with_test_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Should show reminder when new function added and test file exists."""
        # Create test directory structure
        scripts_dir = tmp_path / ".claude" / "scripts"
        tests_dir = scripts_dir / "tests"
        tests_dir.mkdir(parents=True)

        script_file = scripts_dir / "example.py"
        script_file.touch()
        test_file = tests_dir / "test_example.py"
        test_file.touch()

        input_data = {
            "tool_input": {
                "file_path": str(script_file),
                "old_string": "",
                "new_string": "def new_function():\n    pass",
            }
        }

        with mock.patch.object(script_test_reminder, "parse_hook_input", return_value=input_data):
            with mock.patch.object(script_test_reminder, "log_hook_execution"):
                script_test_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)

        assert result["continue"] is True
        assert "systemMessage" in result
        assert "テストファイルが存在します" in result["systemMessage"]
        assert "new_function" in result["systemMessage"]

    def test_no_reminder_for_non_script_files(self, capsys: pytest.CaptureFixture) -> None:
        """Should not show reminder for files outside .claude/scripts/."""
        input_data = {
            "tool_input": {
                "file_path": "/some/other/path.py",
                "old_string": "",
                "new_string": "def new_function():\n    pass",
            }
        }

        with mock.patch.object(script_test_reminder, "parse_hook_input", return_value=input_data):
            script_test_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)

        assert result["continue"] is True
        assert "systemMessage" not in result

    def test_no_reminder_for_test_files(self, capsys: pytest.CaptureFixture) -> None:
        """Should not show reminder when editing test files themselves."""
        input_data = {
            "tool_input": {
                "file_path": "/project/.claude/scripts/tests/test_example.py",
                "old_string": "",
                "new_string": "def test_new_function():\n    pass",
            }
        }

        with mock.patch.object(script_test_reminder, "parse_hook_input", return_value=input_data):
            script_test_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)

        assert result["continue"] is True
        assert "systemMessage" not in result

    def test_no_reminder_when_no_new_functions(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Should not show reminder when no new functions are added."""
        scripts_dir = tmp_path / ".claude" / "scripts"
        tests_dir = scripts_dir / "tests"
        tests_dir.mkdir(parents=True)

        script_file = scripts_dir / "example.py"
        script_file.touch()
        test_file = tests_dir / "test_example.py"
        test_file.touch()

        input_data = {
            "tool_input": {
                "file_path": str(script_file),
                "old_string": "x = 1",
                "new_string": "x = 2",
            }
        }

        with mock.patch.object(script_test_reminder, "parse_hook_input", return_value=input_data):
            script_test_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)

        assert result["continue"] is True
        assert "systemMessage" not in result

    def test_no_reminder_when_test_file_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Should not show reminder when test file doesn't exist."""
        scripts_dir = tmp_path / ".claude" / "scripts"
        tests_dir = scripts_dir / "tests"
        tests_dir.mkdir(parents=True)

        script_file = scripts_dir / "no_test_script.py"
        script_file.touch()
        # Don't create test file

        input_data = {
            "tool_input": {
                "file_path": str(script_file),
                "old_string": "",
                "new_string": "def new_function():\n    pass",
            }
        }

        with mock.patch.object(script_test_reminder, "parse_hook_input", return_value=input_data):
            script_test_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)

        assert result["continue"] is True
        assert "systemMessage" not in result

    def test_handles_exception_gracefully(self, capsys: pytest.CaptureFixture) -> None:
        """Should continue execution even if an exception occurs."""
        with mock.patch.object(
            script_test_reminder, "parse_hook_input", side_effect=Exception("Test error")
        ):
            script_test_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)

        assert result["continue"] is True
        assert "systemMessage" not in result
