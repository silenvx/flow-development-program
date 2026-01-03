#!/usr/bin/env python3
"""Unit tests for empty-return-check.py"""

import importlib.util
import sys
from pathlib import Path

import pytest

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# empty-return-check.py has hyphens, so we need dynamic import
HOOK_PATH = Path(__file__).parent.parent / "empty-return-check.py"
_spec = importlib.util.spec_from_file_location("empty_return_check", HOOK_PATH)
empty_return_check = importlib.util.module_from_spec(_spec)
sys.modules["empty_return_check"] = empty_return_check
_spec.loader.exec_module(empty_return_check)

EmptyReturnInExceptChecker = empty_return_check.EmptyReturnInExceptChecker


class TestEmptyReturnInExceptChecker:
    """Tests for EmptyReturnInExceptChecker class."""

    def setup_method(self):
        self.checker = EmptyReturnInExceptChecker()

    def test_detects_empty_list_in_except(self):
        """Should detect return [] in except block."""
        code = """
def fetch_data():
    try:
        return get_remote_data()
    except Exception:
        return []
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 1
        assert "Empty collection" in issues[0]["message"]

    def test_detects_empty_dict_in_except(self):
        """Should detect return {} in except block."""
        code = """
def fetch_config():
    try:
        return load_config()
    except FileNotFoundError:
        return {}
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 1

    def test_detects_list_call_in_except(self):
        """Should detect return list() in except block."""
        code = """
def get_items():
    try:
        return fetch_items()
    except Exception:
        return list()
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 1

    def test_detects_dict_call_in_except(self):
        """Should detect return dict() in except block."""
        code = """
def get_config():
    try:
        return load()
    except Exception:
        return dict()
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 1

    def test_ignores_empty_return_outside_except(self):
        """Should not flag return [] outside except block."""
        code = """
def empty_list():
    return []

def conditional_empty(condition):
    if condition:
        return [1, 2, 3]
    return []
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 0

    def test_ignores_none_return_in_except(self):
        """Should not flag return None in except (correct pattern)."""
        code = """
def fetch_data():
    try:
        return get_remote_data()
    except Exception:
        return None
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 0

    def test_ignores_non_empty_return_in_except(self):
        """Should not flag return with non-empty values in except."""
        code = """
def fetch_data():
    try:
        return get_remote_data()
    except Exception:
        return ["default"]
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 0

    def test_detects_nested_except(self):
        """Should detect in nested try-except blocks."""
        code = """
def fetch():
    try:
        try:
            return inner()
        except ValueError:
            return []
    except Exception:
        return None
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 1

    def test_no_false_positive_on_nested_try_fallback(self):
        """Should not flag valid return in nested try block (Issue #224).

        When a nested try-except is used for fallback, the return inside
        the nested try's body is legitimate and should not be flagged.
        Only the innermost except's empty return should be flagged.
        """
        code = """
def fetch_with_fallback():
    try:
        return primary_fetch()
    except Exception:
        try:
            return fallback_fetch()  # This is valid - not in except
        except Exception:
            return []  # Only this should be flagged
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 1
        # Verify it's the innermost except's return that's flagged
        assert issues[0]["line"] == 9

    def test_detects_return_in_if_inside_except(self):
        """Should detect empty return inside if block within except."""
        code = """
def fetch_conditional():
    try:
        return fetch_data()
    except Exception as e:
        if is_retryable(e):
            return retry_fetch()
        else:
            return []  # Should be flagged
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 1
        assert issues[0]["line"] == 9

    def test_ignores_return_in_nested_function_inside_except(self):
        """Should not flag return in nested function defined inside except."""
        code = """
def fetch_data():
    try:
        return get_remote_data()
    except Exception:
        def fallback():
            return []  # This is in a nested function, not the except block
        return fallback()
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 0

    def test_detects_multiple_issues(self):
        """Should detect multiple antipatterns in same file."""
        code = """
def fetch_list():
    try:
        return get_list()
    except Exception:
        return []

def fetch_dict():
    try:
        return get_dict()
    except Exception:
        return {}
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 2

    def test_handles_syntax_error(self):
        """Should not crash on syntax errors."""
        code = """
def broken(
    return []
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 0  # Silently skip

    def test_detects_empty_tuple_in_except(self):
        """Should detect return () in except block."""
        code = """
def fetch_tuple():
    try:
        return get_tuple()
    except Exception:
        return ()
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 1

    def test_detects_empty_set_in_except(self):
        """Should detect return set() in except block."""
        code = """
def fetch_set():
    try:
        return get_set()
    except Exception:
        return set()
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 1

    def test_detects_in_nested_try_else(self):
        """Should detect empty return in try-else clause within except (Issue #271)."""
        code = """
def fetch_with_fallback():
    try:
        return primary_fetch()
    except Exception:
        try:
            result = secondary_fetch()
        except Exception:
            pass
        else:
            return []  # Should be flagged - try-else within except
        return None
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 1
        assert "Empty collection" in issues[0]["message"]

    def test_detects_in_nested_try_finally(self):
        """Should detect empty return in try-finally clause within except (Issue #271)."""
        code = """
def fetch_with_cleanup():
    try:
        return primary_fetch()
    except Exception:
        try:
            secondary_fetch()
        finally:
            return []  # Should be flagged - try-finally within except
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 1

    @pytest.mark.skipif(sys.version_info < (3, 10), reason="Match statement requires Python 3.10+")
    def test_detects_in_match_statement(self):
        """Should detect empty return in match case within except (Python 3.10+)."""
        code = """
def handle_error():
    try:
        return fetch_data()
    except Exception as e:
        match type(e).__name__:
            case "ValueError":
                return None
            case "KeyError":
                return []  # Should be flagged
            case _:
                return {}  # Should also be flagged
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 2

    def test_ignores_nested_try_body(self):
        """Should not flag return in nested try body (valid pattern)."""
        code = """
def fetch_with_fallback():
    try:
        return primary_fetch()
    except Exception:
        try:
            return fallback_fetch()  # Valid - in try body, not except
        except Exception:
            return None  # Valid - returns None
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 0

    def test_detects_both_nested_and_direct(self):
        """Should detect in both nested try-else and direct except."""
        code = """
def complex_fetch():
    try:
        return primary_fetch()
    except Exception:
        try:
            x = attempt()
        except Exception:
            return []  # Flagged - inner except
        else:
            return {}  # Flagged - try-else
        return None
"""
        issues = self.checker.check_file("test.py", code)
        assert len(issues) == 2
