#!/usr/bin/env python3
"""Tests for similar-code-check.py hook."""

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HOOK_PATH = Path(__file__).parent.parent / "similar-code-check.py"


def load_module():
    """Load the hook module for testing."""
    # Temporarily add hooks directory to path for common module import
    hooks_dir = str(HOOK_PATH.parent)
    sys.path.insert(0, hooks_dir)
    try:
        spec = importlib.util.spec_from_file_location("similar_code_check", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        # Clean up path
        if hooks_dir in sys.path:
            sys.path.remove(hooks_dir)


class TestIsHookFile:
    """Tests for is_hook_file function."""

    def setup_method(self):
        self.module = load_module()

    def test_valid_hook_file(self):
        """Should return True for valid hook files."""
        assert self.module.is_hook_file(".claude/hooks/my-hook.py") is True

    def test_full_path_hook_file(self):
        """Should return True for full path hook files."""
        assert self.module.is_hook_file("/project/.claude/hooks/my-hook.py") is True

    def test_test_file(self):
        """Should return False for test files."""
        assert self.module.is_hook_file(".claude/hooks/tests/test_hook.py") is False

    def test_non_python_file(self):
        """Should return False for non-Python files."""
        assert self.module.is_hook_file(".claude/hooks/config.json") is False

    def test_other_directory(self):
        """Should return False for files in other directories."""
        assert self.module.is_hook_file("src/utils/helper.py") is False

    def test_empty_path(self):
        """Should return False for empty path."""
        assert self.module.is_hook_file("") is False

    def test_none_path(self):
        """Should return False for None path."""
        assert self.module.is_hook_file(None) is False


class TestExtractFunctionNames:
    """Tests for extract_function_names function."""

    def setup_method(self):
        self.module = load_module()

    def test_single_function(self):
        """Should extract a single function name."""
        content = "def check_something():\n    pass"
        result = self.module.extract_function_names(content)
        assert result == ["check_something"]

    def test_multiple_functions(self):
        """Should extract multiple function names."""
        content = """
def has_skip_flag():
    pass

def check_status():
    pass

def format_output():
    pass
"""
        result = self.module.extract_function_names(content)
        assert "has_skip_flag" in result
        assert "check_status" in result
        assert "format_output" in result

    def test_function_with_arguments(self):
        """Should extract function with arguments."""
        content = "def get_data(arg1, arg2=None):\n    pass"
        result = self.module.extract_function_names(content)
        assert result == ["get_data"]

    def test_method_in_class_not_extracted(self):
        """Should NOT extract indented methods from classes (only top-level functions)."""
        content = """
class MyClass:
    def check_value(self):
        pass
"""
        result = self.module.extract_function_names(content)
        # Intentionally only extract top-level functions, not class methods
        assert "check_value" not in result

    def test_no_functions(self):
        """Should return empty list when no functions."""
        content = "# Just a comment\nx = 1"
        result = self.module.extract_function_names(content)
        assert result == []

    def test_empty_content(self):
        """Should return empty list for empty content."""
        result = self.module.extract_function_names("")
        assert result == []

    def test_none_content(self):
        """Should return empty list for None content."""
        result = self.module.extract_function_names(None)
        assert result == []


class TestSearchSimilarFunctions:
    """Tests for search_similar_functions function."""

    def setup_method(self):
        self.module = load_module()

    def test_find_has_skip_pattern(self):
        """Should find similar has_skip_* functions."""

        def mock_subprocess(cmd, **kwargs):
            if "grep" in cmd and "has_skip" in str(cmd):
                return MagicMock(
                    returncode=0,
                    stdout=".claude/hooks/existing.py\n",
                )
            return MagicMock(returncode=1, stdout="")

        with (
            patch("lib.repo.get_repo_root", return_value=Path("/project")),
            patch.object(self.module, "get_repo_root", return_value=Path("/project")),
            patch.object(self.module.subprocess, "run", side_effect=mock_subprocess),
        ):
            result = self.module.search_similar_functions(["has_skip_something"])
            assert len(result) > 0
            assert any("has_skip_something" in key for key in result)

    def test_find_check_pattern(self):
        """Should find similar check_* functions."""

        def mock_subprocess(cmd, **kwargs):
            if "grep" in cmd and "check_" in str(cmd):
                return MagicMock(
                    returncode=0,
                    stdout=".claude/hooks/validator.py\n",
                )
            return MagicMock(returncode=1, stdout="")

        with (
            patch("lib.repo.get_repo_root", return_value=Path("/project")),
            patch.object(self.module, "get_repo_root", return_value=Path("/project")),
            patch.object(self.module.subprocess, "run", side_effect=mock_subprocess),
        ):
            result = self.module.search_similar_functions(["check_value"])
            assert len(result) > 0

    def test_no_matches(self):
        """Should return empty dict when no matches."""

        def mock_subprocess(cmd, **kwargs):
            if "rev-parse" in cmd:
                return MagicMock(returncode=0, stdout="/project\n")
            return MagicMock(returncode=1, stdout="")

        with patch.object(self.module.subprocess, "run", side_effect=mock_subprocess):
            result = self.module.search_similar_functions(["unique_function_name"])
            assert result == {}

    def test_limit_files_per_pattern(self):
        """Should limit files to 5 per pattern."""
        files = "\n".join([f".claude/hooks/file{i}.py" for i in range(10)])

        def mock_subprocess(cmd, **kwargs):
            if "rev-parse" in cmd:
                return MagicMock(returncode=0, stdout="/project\n")
            if "grep" in cmd:
                return MagicMock(returncode=0, stdout=files)
            return MagicMock(returncode=1, stdout="")

        with patch.object(self.module.subprocess, "run", side_effect=mock_subprocess):
            result = self.module.search_similar_functions(["check_something"])
            for files_list in result.values():
                assert len(files_list) <= 5

    def test_fail_open_on_timeout(self):
        """Should handle timeout gracefully."""

        def mock_subprocess(cmd, **kwargs):
            if "rev-parse" in cmd:
                return MagicMock(returncode=0, stdout="/project\n")
            from subprocess import TimeoutExpired

            raise TimeoutExpired(cmd, 5)

        with patch.object(self.module.subprocess, "run", side_effect=mock_subprocess):
            result = self.module.search_similar_functions(["check_something"])
            assert result == {}

    def test_returns_empty_when_repo_root_unavailable(self):
        """Should return empty dict when repo root cannot be determined."""

        def mock_subprocess(cmd, **kwargs):
            # Simulate git rev-parse failure (not in a git repo)
            return MagicMock(returncode=128, stdout="")

        with patch.object(self.module.subprocess, "run", side_effect=mock_subprocess):
            result = self.module.search_similar_functions(["check_something"])
            assert result == {}


class TestFormatSuggestions:
    """Tests for format_suggestions function."""

    def setup_method(self):
        self.module = load_module()

    def test_format_single_pattern(self):
        """Should format single pattern result."""
        similar = {"`check_value` (検証/チェック関数)": [".claude/hooks/validator.py"]}
        result = self.module.format_suggestions(similar)
        assert "類似コードが見つかりました" in result
        assert "check_value" in result
        assert ".claude/hooks/validator.py" in result

    def test_format_multiple_patterns(self):
        """Should format multiple pattern results."""
        similar = {
            "`check_value` (検証/チェック関数)": [".claude/hooks/validator.py"],
            "`has_skip_flag` (スキップ判定関数)": [".claude/hooks/skipper.py"],
        }
        result = self.module.format_suggestions(similar)
        assert "check_value" in result
        assert "has_skip_flag" in result

    def test_empty_similar(self):
        """Should return empty string for empty similar dict."""
        result = self.module.format_suggestions({})
        assert result == ""

    def test_contains_consistency_message(self):
        """Should contain consistency guidance message."""
        similar = {"`get_data` (データ取得関数)": [".claude/hooks/fetcher.py"]}
        result = self.module.format_suggestions(similar)
        assert "一貫性" in result


class TestMain:
    """Tests for main function."""

    def setup_method(self):
        self.module = load_module()

    def test_suggest_when_similar_code_exists(self):
        """Should suggest similar code when found."""

        def mock_subprocess(cmd, **kwargs):
            if "rev-parse" in cmd:
                return MagicMock(returncode=0, stdout="/project\n")
            if "grep" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=".claude/hooks/existing.py\n",
                )
            return MagicMock(returncode=1, stdout="")

        input_data = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": ".claude/hooks/new-hook.py",
                "content": """
def check_status():
    pass
""",
            },
        }

        captured_output = io.StringIO()
        with (
            patch("lib.repo.get_repo_root", return_value=Path("/project")),
            patch.object(self.module, "get_repo_root", return_value=Path("/project")),
            patch.object(self.module.subprocess, "run", side_effect=mock_subprocess),
            patch("sys.stdin.read", return_value=json.dumps(input_data)),
            patch("sys.stdout", captured_output),
        ):
            self.module.main()

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "類似コード" in result["systemMessage"]

    def test_no_suggest_when_no_similar_code(self):
        """Should not suggest when no similar code."""

        def mock_subprocess(cmd, **kwargs):
            if "rev-parse" in cmd:
                return MagicMock(returncode=0, stdout="/project\n")
            return MagicMock(returncode=1, stdout="")

        input_data = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": ".claude/hooks/new-hook.py",
                "content": """
def unique_function():
    pass
""",
            },
        }

        captured_output = io.StringIO()
        with (
            patch.object(self.module.subprocess, "run", side_effect=mock_subprocess),
            patch("sys.stdin.read", return_value=json.dumps(input_data)),
            patch("sys.stdout", captured_output),
        ):
            self.module.main()

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_ignore_non_hook_files(self):
        """Should ignore non-hook files."""
        input_data = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "src/utils/helper.py",
                "content": "def check_something(): pass",
            },
        }

        captured_output = io.StringIO()
        with (
            patch("sys.stdin.read", return_value=json.dumps(input_data)),
            patch("sys.stdout", captured_output),
        ):
            self.module.main()

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_ignore_test_files(self):
        """Should ignore test files in hooks directory."""
        input_data = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": ".claude/hooks/tests/test_hook.py",
                "content": "def check_something(): pass",
            },
        }

        captured_output = io.StringIO()
        with (
            patch("sys.stdin.read", return_value=json.dumps(input_data)),
            patch("sys.stdout", captured_output),
        ):
            self.module.main()

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_handle_edit_tool(self):
        """Should handle Edit tool using new_string as content."""

        def mock_subprocess(cmd, **kwargs):
            if "rev-parse" in cmd:
                return MagicMock(returncode=0, stdout="/project\n")
            if "grep" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=".claude/hooks/existing.py\n",
                )
            return MagicMock(returncode=1, stdout="")

        input_data = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": ".claude/hooks/new-hook.py",
                "old_string": "",
                "new_string": "def check_status():\n    pass",
            },
        }

        captured_output = io.StringIO()
        with (
            patch("lib.repo.get_repo_root", return_value=Path("/project")),
            patch.object(self.module, "get_repo_root", return_value=Path("/project")),
            patch.object(self.module.subprocess, "run", side_effect=mock_subprocess),
            patch("sys.stdin.read", return_value=json.dumps(input_data)),
            patch("sys.stdout", captured_output),
        ):
            self.module.main()

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"
        # Edit tool uses new_string - should suggest similar code
        assert "systemMessage" in result
        assert "類似コード" in result["systemMessage"]

    def test_fail_open_on_error(self):
        """Should approve on errors (fail-open)."""
        captured_output = io.StringIO()
        with (
            patch("sys.stdin.read", side_effect=Exception("Test error")),
            patch("sys.stdout", captured_output),
        ):
            self.module.main()

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"

    def test_no_functions_in_content(self):
        """Should not suggest when no functions in content."""
        input_data = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": ".claude/hooks/new-hook.py",
                "content": "# Just a comment\nX = 1",
            },
        }

        captured_output = io.StringIO()
        with (
            patch("sys.stdin.read", return_value=json.dumps(input_data)),
            patch("sys.stdout", captured_output),
        ):
            self.module.main()

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"
        assert "systemMessage" not in result
