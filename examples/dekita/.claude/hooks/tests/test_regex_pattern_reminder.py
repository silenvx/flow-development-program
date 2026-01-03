"""Tests for regex-pattern-reminder hook."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent))
import importlib

regex_pattern_reminder = importlib.import_module("regex-pattern-reminder")


class TestContainsRegexPattern:
    """Tests for contains_regex_pattern function."""

    def test_detects_re_compile(self):
        """Should detect re.compile pattern."""
        assert regex_pattern_reminder.contains_regex_pattern("pattern = re.compile(r'test')")

    def test_detects_re_search(self):
        """Should detect re.search pattern."""
        assert regex_pattern_reminder.contains_regex_pattern("if re.search(r'test', text):")

    def test_detects_re_match(self):
        """Should detect re.match pattern."""
        assert regex_pattern_reminder.contains_regex_pattern("match = re.match(r'^test', text)")

    def test_detects_re_findall(self):
        """Should detect re.findall pattern."""
        assert regex_pattern_reminder.contains_regex_pattern("results = re.findall(r'\\d+', text)")

    def test_detects_re_sub(self):
        """Should detect re.sub pattern."""
        assert regex_pattern_reminder.contains_regex_pattern("text = re.sub(r'old', 'new', text)")

    def test_detects_re_split(self):
        """Should detect re.split pattern."""
        assert regex_pattern_reminder.contains_regex_pattern("parts = re.split(r'\\s+', text)")

    def test_detects_pattern_constant(self):
        """Should detect PATTERN = constant definition."""
        assert regex_pattern_reminder.contains_regex_pattern("MY_PATTERN = r'test'")

    def test_detects_patterns_constant(self):
        """Should detect PATTERNS = constant definition."""
        assert regex_pattern_reminder.contains_regex_pattern(
            "REGEX_PATTERNS = [r'test1', r'test2']"
        )

    def test_detects_underscore_pattern(self):
        """Should detect _PATTERN = constant definition."""
        assert regex_pattern_reminder.contains_regex_pattern("_INTERNAL_PATTERN = r'test'")

    def test_ignores_regular_code(self):
        """Should not detect regular code without regex patterns."""
        assert not regex_pattern_reminder.contains_regex_pattern(
            "def my_function():\n    return 42"
        )

    def test_detects_pattern_in_comment(self):
        """Should detect pattern even in comments (conservative approach)."""
        # We detect the pattern regardless of context - this is intentional
        # as false positives are acceptable (just shows a reminder)
        # Note: needs to match the full pattern (e.g., re.compile() with parenthesis)
        assert regex_pattern_reminder.contains_regex_pattern("# pattern = re.compile(r'test')")

    def test_empty_string(self):
        """Should return False for empty string."""
        assert not regex_pattern_reminder.contains_regex_pattern("")

    def test_none_string(self):
        """Should return False for None."""
        assert not regex_pattern_reminder.contains_regex_pattern(None)


class TestIsPythonFile:
    """Tests for is_python_file function."""

    def test_python_file(self):
        """Should return True for .py files."""
        assert regex_pattern_reminder.is_python_file("/path/to/file.py")

    def test_non_python_file(self):
        """Should return False for non-.py files."""
        assert not regex_pattern_reminder.is_python_file("/path/to/file.js")
        assert not regex_pattern_reminder.is_python_file("/path/to/file.ts")
        assert not regex_pattern_reminder.is_python_file("/path/to/file.txt")

    def test_python_in_name_but_not_extension(self):
        """Should return False for files with python in name but different extension."""
        assert not regex_pattern_reminder.is_python_file("/path/to/python_script.sh")


class TestSessionTracking:
    """Tests for session-based tracking functions."""

    def test_load_save_reminded_files(self):
        """Should correctly save and load reminded files."""
        with patch.object(
            regex_pattern_reminder, "get_session_id", return_value="test-session-123"
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch("tempfile.gettempdir", return_value=tmpdir):
                    # Initially empty
                    files = regex_pattern_reminder.load_reminded_files()
                    assert files == set()

                    # Save some files
                    regex_pattern_reminder.save_reminded_files(
                        {"/path/to/file1.py", "/path/to/file2.py"}
                    )

                    # Load and verify
                    files = regex_pattern_reminder.load_reminded_files()
                    assert files == {"/path/to/file1.py", "/path/to/file2.py"}

    def test_is_reminded_in_session(self):
        """Should correctly check if file has been reminded."""
        with patch.object(
            regex_pattern_reminder, "get_session_id", return_value="test-session-456"
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch("tempfile.gettempdir", return_value=tmpdir):
                    # Initially not reminded
                    assert not regex_pattern_reminder.is_reminded_in_session("/path/to/file.py")

                    # Mark as reminded
                    regex_pattern_reminder.mark_as_reminded("/path/to/file.py")

                    # Now should be reminded
                    assert regex_pattern_reminder.is_reminded_in_session("/path/to/file.py")


class TestMain:
    """Tests for main function."""

    def test_shows_reminder_for_python_file_with_regex(self):
        """Should show reminder when editing Python file with regex patterns."""
        hook_input = {
            "tool_input": {
                "file_path": "/project/test.py",
                "new_string": "pattern = re.compile(r'test')",
            }
        }

        with patch.object(regex_pattern_reminder, "get_session_id", return_value="test-session"):
            with patch.object(regex_pattern_reminder, "parse_hook_input", return_value=hook_input):
                with patch.object(
                    regex_pattern_reminder, "is_reminded_in_session", return_value=False
                ):
                    with patch.object(regex_pattern_reminder, "mark_as_reminded"):
                        with patch.object(regex_pattern_reminder, "log_hook_execution"):
                            with patch("builtins.print") as mock_print:
                                regex_pattern_reminder.main()

                                # Verify output
                                output = mock_print.call_args[0][0]
                                result = json.loads(output)
                                assert result["decision"] == "approve"
                                assert "systemMessage" in result
                                assert (
                                    "パターンマッチング実装チェックリスト"
                                    in result["systemMessage"]
                                )

    def test_skips_non_python_file(self):
        """Should skip non-Python files."""
        hook_input = {
            "tool_input": {"file_path": "/project/test.js", "new_string": "const pattern = /test/"}
        }

        with patch.object(regex_pattern_reminder, "parse_hook_input", return_value=hook_input):
            with patch.object(regex_pattern_reminder, "log_hook_execution"):
                with patch("builtins.print") as mock_print:
                    regex_pattern_reminder.main()

                    output = mock_print.call_args[0][0]
                    result = json.loads(output)
                    assert result["decision"] == "approve"
                    assert "systemMessage" not in result

    def test_skips_code_without_regex(self):
        """Should skip Python files without regex patterns."""
        hook_input = {
            "tool_input": {
                "file_path": "/project/test.py",
                "new_string": "def hello():\n    print('world')",
            }
        }

        with patch.object(regex_pattern_reminder, "parse_hook_input", return_value=hook_input):
            with patch.object(regex_pattern_reminder, "log_hook_execution"):
                with patch("builtins.print") as mock_print:
                    regex_pattern_reminder.main()

                    output = mock_print.call_args[0][0]
                    result = json.loads(output)
                    assert result["decision"] == "approve"
                    assert "systemMessage" not in result

    def test_skips_already_reminded(self):
        """Should skip if already reminded in session."""
        hook_input = {
            "tool_input": {
                "file_path": "/project/test.py",
                "new_string": "pattern = re.compile(r'test')",
            }
        }

        with patch.object(regex_pattern_reminder, "get_session_id", return_value="test-session"):
            with patch.object(regex_pattern_reminder, "parse_hook_input", return_value=hook_input):
                with patch.object(
                    regex_pattern_reminder, "is_reminded_in_session", return_value=True
                ):
                    with patch.object(regex_pattern_reminder, "log_hook_execution"):
                        with patch("builtins.print") as mock_print:
                            regex_pattern_reminder.main()

                            output = mock_print.call_args[0][0]
                            result = json.loads(output)
                            assert result["decision"] == "approve"
                            assert "systemMessage" not in result
