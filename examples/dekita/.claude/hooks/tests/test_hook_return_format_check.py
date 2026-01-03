#!/usr/bin/env python3
"""Tests for hook-return-format-check.py"""

import sys
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestGetHookTypeForFile:
    """Tests for get_hook_type_for_file function."""

    def test_stop_hook(self):
        """Stop hook should be detected correctly."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        settings = {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 ".claude/hooks/session-end-main-check.py"',
                            }
                        ]
                    }
                ]
            }
        }

        result = hook_module.get_hook_type_for_file(
            ".claude/hooks/session-end-main-check.py", settings
        )
        assert result == "Stop"

    def test_pretooluse_hook(self):
        """PreToolUse hook should be detected correctly."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {"type": "command", "command": 'python3 ".claude/hooks/merge-check.py"'}
                        ],
                    }
                ]
            }
        }

        result = hook_module.get_hook_type_for_file(".claude/hooks/merge-check.py", settings)
        assert result == "PreToolUse"

    def test_posttooluse_hook(self):
        """PostToolUse hook should be detected correctly."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        settings = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 ".claude/hooks/rework-tracker.py"',
                            }
                        ],
                    }
                ]
            }
        }

        result = hook_module.get_hook_type_for_file(".claude/hooks/rework-tracker.py", settings)
        assert result == "PostToolUse"

    def test_unknown_hook(self):
        """Unknown hook should return None."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        settings = {"hooks": {}}

        result = hook_module.get_hook_type_for_file(".claude/hooks/unknown-hook.py", settings)
        assert result is None


class TestCheckReturnFormatUsage:
    """Tests for check_return_format_usage function."""

    def test_continue_function_used(self):
        """Should detect print_continue_and_log_skip usage."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        content = """
from lib.results import print_continue_and_log_skip

def main():
    print_continue_and_log_skip("my-hook", "skip reason")
"""
        result = hook_module.check_return_format_usage(content)
        assert len(result["print_continue_and_log_skip"]) == 1
        assert result["print_continue_and_log_skip"][0] == 5  # Line 5

    def test_approve_function_used(self):
        """Should detect print_approve_and_log_skip usage."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        content = """
from lib.results import print_approve_and_log_skip

def main():
    print_approve_and_log_skip("my-hook", "skip reason")
"""
        result = hook_module.check_return_format_usage(content)
        assert len(result["print_approve_and_log_skip"]) == 1
        assert result["print_approve_and_log_skip"][0] == 5  # Line 5

    def test_both_functions_used(self):
        """Should detect both functions when used."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        content = """
from lib.results import print_continue_and_log_skip, print_approve_and_log_skip

def main():
    if some_condition:
        print_continue_and_log_skip("my-hook", "continue")
    else:
        print_approve_and_log_skip("my-hook", "approve")
"""
        result = hook_module.check_return_format_usage(content)
        assert len(result["print_continue_and_log_skip"]) == 1
        assert len(result["print_approve_and_log_skip"]) == 1

    def test_ignores_comments(self):
        """Should not count commented-out usages."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        content = """
# print_continue_and_log_skip("my-hook", "skip reason")
"""
        result = hook_module.check_return_format_usage(content)
        assert len(result["print_continue_and_log_skip"]) == 0

    def test_ignores_docstrings(self):
        """Should not count function names in docstrings."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        content = '''
def some_function():
    """This function demonstrates print_continue_and_log_skip usage.

    You should use print_approve_and_log_skip for Stop hooks.
    """
    pass
'''
        result = hook_module.check_return_format_usage(content)
        assert len(result["print_continue_and_log_skip"]) == 0
        assert len(result["print_approve_and_log_skip"]) == 0

    def test_ignores_single_line_docstrings(self):
        """Should not count function names in single-line docstrings."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        content = '''
def some_function():
    """Use print_continue_and_log_skip for non-Stop hooks."""
    pass
'''
        result = hook_module.check_return_format_usage(content)
        assert len(result["print_continue_and_log_skip"]) == 0

    def test_ignores_string_literals_without_call(self):
        """Should not count mentions in string literals without function call."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        # Note: This tests that strings like error messages don't trigger false positives
        # The current implementation looks for "func_name(" pattern, so strings without
        # the opening parenthesis won't be detected
        content = """
message = "use print_continue_and_log_skip for this"
"""
        result = hook_module.check_return_format_usage(content)
        # Without the opening parenthesis, this should not be detected
        assert len(result["print_continue_and_log_skip"]) == 0

    def test_detects_actual_call_with_string_nearby(self):
        """Should detect actual function calls even when strings contain function name."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        content = """
from lib.results import print_continue_and_log_skip

def main():
    # This is an actual call
    print_continue_and_log_skip("hook", "reason")
"""
        result = hook_module.check_return_format_usage(content)
        assert len(result["print_continue_and_log_skip"]) == 1


class TestFileNameMatching:
    """Tests for file name matching edge cases."""

    def test_similar_filename_exact_match(self):
        """Should match exact filename, not partial match."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {"type": "command", "command": 'python3 ".claude/hooks/merge-check.py"'}
                        ]
                    }
                ],
                "PostToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 ".claude/hooks/pre-merge-check.py"',
                            }
                        ]
                    }
                ],
            }
        }

        # merge-check.py should be PreToolUse
        result1 = hook_module.get_hook_type_for_file(".claude/hooks/merge-check.py", settings)
        assert result1 == "PreToolUse"

        # pre-merge-check.py should be PostToolUse
        result2 = hook_module.get_hook_type_for_file(".claude/hooks/pre-merge-check.py", settings)
        assert result2 == "PostToolUse"

    def test_different_directory_same_filename(self):
        """Files with same name in different directories match by basename.

        Note: Current implementation uses basename matching, which means
        files with the same basename will match regardless of directory.
        """
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        settings = {
            "hooks": {
                "PreToolUse": [
                    {"hooks": [{"type": "command", "command": 'python3 ".claude/hooks/check.py"'}]}
                ]
            }
        }

        # Both paths have basename "check.py" so both will match PreToolUse
        result1 = hook_module.get_hook_type_for_file(".claude/hooks/check.py", settings)
        result2 = hook_module.get_hook_type_for_file("scripts/check.py", settings)

        assert result1 == "PreToolUse"
        # scripts/check.py also matches because basename "check.py" matches
        assert result2 == "PreToolUse"

    def test_no_partial_match(self):
        """Should not match when filename is a substring of registered hook.

        This tests that editing check.py does NOT match a hook registered as pre-check.py.
        """
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {"type": "command", "command": 'python3 ".claude/hooks/pre-check.py"'}
                        ]
                    }
                ]
            }
        }

        # pre-check.py is registered, but we're editing check.py
        # check.py should NOT match because basename of pre-check.py is pre-check.py
        result = hook_module.get_hook_type_for_file(".claude/hooks/check.py", settings)
        assert result is None  # No match expected


class TestAnalyzeHookFile:
    """Tests for analyze_hook_file function."""

    def test_stop_hook_with_continue_function(self):
        """Stop hook using print_continue_and_log_skip should be flagged as error."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        content = """
from lib.results import print_continue_and_log_skip

def main():
    print_continue_and_log_skip("session-end-main-check", "skip")
"""
        settings = {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 ".claude/hooks/session-end-main-check.py"',
                            }
                        ]
                    }
                ]
            }
        }

        issues = hook_module.analyze_hook_file(
            ".claude/hooks/session-end-main-check.py", content, settings
        )

        assert len(issues) == 1
        assert issues[0]["severity"] == "error"
        assert "print_continue_and_log_skip" in issues[0]["message"]
        assert "print_approve_and_log_skip" in issues[0]["message"]

    def test_stop_hook_with_approve_function(self):
        """Stop hook using print_approve_and_log_skip should pass."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        content = """
from lib.results import print_approve_and_log_skip

def main():
    print_approve_and_log_skip("session-end-main-check", "skip")
"""
        settings = {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 ".claude/hooks/session-end-main-check.py"',
                            }
                        ]
                    }
                ]
            }
        }

        issues = hook_module.analyze_hook_file(
            ".claude/hooks/session-end-main-check.py", content, settings
        )

        assert len(issues) == 0

    def test_posttooluse_hook_with_approve_function(self):
        """PostToolUse hook using print_approve_and_log_skip should be flagged as warning."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        content = """
from lib.results import print_approve_and_log_skip

def main():
    print_approve_and_log_skip("rework-tracker", "skip")
"""
        settings = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Edit",
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 ".claude/hooks/rework-tracker.py"',
                            }
                        ],
                    }
                ]
            }
        }

        issues = hook_module.analyze_hook_file(".claude/hooks/rework-tracker.py", content, settings)

        assert len(issues) == 1
        assert issues[0]["severity"] == "warning"

    def test_pretooluse_hook_either_function(self):
        """PreToolUse hook can use either function without issues."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        content = """
from lib.results import print_continue_and_log_skip

def main():
    print_continue_and_log_skip("merge-check", "skip")
"""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {"type": "command", "command": 'python3 ".claude/hooks/merge-check.py"'}
                        ],
                    }
                ]
            }
        }

        issues = hook_module.analyze_hook_file(".claude/hooks/merge-check.py", content, settings)

        assert len(issues) == 0


class TestMainIntegration:
    """Integration tests for main function."""

    def test_non_edit_write_tool(self, capsys):
        """Non-Edit/Write tools should be skipped."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        input_data = {"tool_name": "Bash", "tool_input": {"command": "ls"}}

        with patch.object(hook_module, "parse_hook_input", return_value=input_data):
            # print_continue_and_log_skip returns, no sys.exit
            hook_module.main()

        captured = capsys.readouterr()
        assert '"continue": true' in captured.out.lower()

    def test_non_python_file(self, capsys):
        """Non-Python files should be skipped."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        input_data = {"tool_name": "Edit", "tool_input": {"file_path": ".claude/hooks/config.json"}}

        with patch.object(hook_module, "parse_hook_input", return_value=input_data):
            hook_module.main()

        captured = capsys.readouterr()
        assert '"continue": true' in captured.out.lower()

    def test_non_hook_directory(self, capsys):
        """Files outside .claude/hooks/ should be skipped."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        input_data = {"tool_name": "Edit", "tool_input": {"file_path": "scripts/some-script.py"}}

        with patch.object(hook_module, "parse_hook_input", return_value=input_data):
            hook_module.main()

        captured = capsys.readouterr()
        assert '"continue": true' in captured.out.lower()

    def test_test_file_skipped(self, capsys):
        """Test files should be skipped."""
        from importlib import import_module

        hook_module = import_module("hook-return-format-check")

        input_data = {
            "tool_name": "Edit",
            "tool_input": {"file_path": ".claude/hooks/tests/test_something.py"},
        }

        with patch.object(hook_module, "parse_hook_input", return_value=input_data):
            hook_module.main()

        captured = capsys.readouterr()
        assert '"continue": true' in captured.out.lower()
