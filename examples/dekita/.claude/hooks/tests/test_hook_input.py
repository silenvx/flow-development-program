"""Tests for lib/hook_input.py"""

import importlib.util
import sys
from pathlib import Path

# Load the module
lib_path = Path(__file__).parent.parent / "lib" / "hook_input.py"
spec = importlib.util.spec_from_file_location("hook_input", lib_path)
hook_input = importlib.util.module_from_spec(spec)
sys.modules["hook_input"] = hook_input
spec.loader.exec_module(hook_input)


class TestGetToolResult:
    """Test cases for get_tool_result function"""

    def test_returns_tool_result_first(self):
        """Test that tool_result is returned when present"""
        input_data = {
            "tool_result": {"exit_code": 0, "stdout": "success"},
            "tool_response": {"exit_code": 1, "stdout": "failed"},
            "tool_output": {"exit_code": 2, "stdout": "other"},
        }
        result = hook_input.get_tool_result(input_data)
        assert result == {"exit_code": 0, "stdout": "success"}

    def test_returns_tool_response_second(self):
        """Test that tool_response is returned when tool_result is not present"""
        input_data = {
            "tool_response": {"exit_code": 0, "stdout": "success"},
            "tool_output": {"exit_code": 1, "stdout": "failed"},
        }
        result = hook_input.get_tool_result(input_data)
        assert result == {"exit_code": 0, "stdout": "success"}

    def test_returns_tool_output_last(self):
        """Test that tool_output is returned as fallback"""
        input_data = {
            "tool_output": {"exit_code": 0, "stdout": "success"},
        }
        result = hook_input.get_tool_result(input_data)
        assert result == {"exit_code": 0, "stdout": "success"}

    def test_returns_none_when_no_result(self):
        """Test that None is returned when no result field is present"""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
        }
        result = hook_input.get_tool_result(input_data)
        assert result is None

    def test_returns_string_result(self):
        """Test that string results are handled correctly"""
        input_data = {
            "tool_result": "some output string",
        }
        result = hook_input.get_tool_result(input_data)
        assert result == "some output string"

    def test_returns_empty_dict(self):
        """Test that empty dict result is returned correctly"""
        input_data = {
            "tool_result": {},
        }
        result = hook_input.get_tool_result(input_data)
        assert result == {}

    def test_empty_input(self):
        """Test with empty input dictionary"""
        result = hook_input.get_tool_result({})
        assert result is None

    def test_tool_result_none_value(self):
        """Test that explicit None value in tool_result is returned (not fallback)"""
        input_data = {
            "tool_result": None,
            "tool_response": {"exit_code": 0, "stdout": "fallback"},
        }
        result = hook_input.get_tool_result(input_data)
        # Key presence takes priority - explicit None is preserved
        assert result is None

    def test_tool_result_zero_value(self):
        """Test that falsy but valid value in tool_result is returned"""
        input_data = {
            "tool_result": 0,
            "tool_response": {"exit_code": 0, "stdout": "fallback"},
        }
        result = hook_input.get_tool_result(input_data)
        assert result == 0

    def test_tool_result_list_value(self):
        """Test that list values are handled correctly"""
        input_data = {
            "tool_result": ["item1", "item2"],
        }
        result = hook_input.get_tool_result(input_data)
        assert result == ["item1", "item2"]


class TestGetExitCode:
    """Test cases for get_exit_code function (Issue #2203)"""

    def test_returns_exit_code_from_dict(self):
        """Test that exit_code is extracted from dict"""
        tool_result = {"exit_code": 1, "stdout": "output"}
        assert hook_input.get_exit_code(tool_result) == 1

    def test_returns_default_when_no_exit_code(self):
        """Test that default 0 is returned when exit_code is missing"""
        tool_result = {"stdout": "output"}
        assert hook_input.get_exit_code(tool_result) == 0

    def test_returns_default_when_none(self):
        """Test that default 0 is returned when tool_result is None"""
        assert hook_input.get_exit_code(None) == 0

    def test_returns_default_when_string(self):
        """Test that default 0 is returned when tool_result is string"""
        assert hook_input.get_exit_code("some string output") == 0

    def test_returns_default_when_empty_dict(self):
        """Test that default 0 is returned when tool_result is empty dict"""
        assert hook_input.get_exit_code({}) == 0

    def test_custom_default_value(self):
        """Test that custom default value is used"""
        assert hook_input.get_exit_code(None, default=1) == 1
        assert hook_input.get_exit_code({}, default=99) == 99

    def test_zero_exit_code(self):
        """Test that exit_code 0 is correctly returned (not default)"""
        tool_result = {"exit_code": 0}
        # default=1 should NOT be used since exit_code is explicitly 0
        assert hook_input.get_exit_code(tool_result, default=1) == 0
