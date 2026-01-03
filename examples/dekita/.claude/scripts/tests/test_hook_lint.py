#!/usr/bin/env python3
"""Tests for hook_lint.py custom lint rules."""

import ast
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hook_lint import (
    LintError,
    check_except_pass_comment,
    check_hardcoded_tmp_path,
    check_log_hook_execution,
    check_log_hook_execution_requires_parse_hook_input,
    check_make_block_result,
    check_parse_hook_input,
    get_comment_lines,
    get_docstring_lines,
    parse_args,
    print_summary,
)


class TestGetCommentLines(unittest.TestCase):
    """Tests for get_comment_lines helper function."""

    def test_detects_inline_comment(self):
        """Should detect inline comments."""
        source = "x = 1  # this is a comment"
        lines = get_comment_lines(source)
        self.assertIn(1, lines)

    def test_detects_full_line_comment(self):
        """Should detect full-line comments."""
        source = "# this is a comment\nx = 1"
        lines = get_comment_lines(source)
        self.assertIn(1, lines)
        self.assertNotIn(2, lines)

    def test_ignores_hash_in_string(self):
        """Should NOT detect # inside string literals."""
        source = 'x = "val#123"'
        lines = get_comment_lines(source)
        self.assertNotIn(1, lines)

    def test_ignores_hash_in_fstring(self):
        """Should NOT detect # inside f-strings."""
        source = 'x = f"val#123"'
        lines = get_comment_lines(source)
        self.assertNotIn(1, lines)


class TestGetDocstringLines(unittest.TestCase):
    """Tests for get_docstring_lines helper function."""

    def test_detects_module_docstring(self):
        """Should detect module docstrings."""
        source = '"""Module docstring."""\nx = 1'
        tree = ast.parse(source)
        lines = get_docstring_lines(tree)
        self.assertIn(1, lines)
        self.assertNotIn(2, lines)

    def test_detects_function_docstring(self):
        """Should detect function docstrings."""
        source = '''def foo():
    """Function docstring."""
    pass'''
        tree = ast.parse(source)
        lines = get_docstring_lines(tree)
        self.assertIn(2, lines)

    def test_detects_multiline_docstring(self):
        """Should detect all lines of multiline docstrings."""
        source = '''def foo():
    """
    Multiline
    docstring.
    """
    pass'''
        tree = ast.parse(source)
        lines = get_docstring_lines(tree)
        self.assertIn(2, lines)
        self.assertIn(3, lines)
        self.assertIn(4, lines)
        self.assertIn(5, lines)

    def test_ignores_regular_strings(self):
        """Should NOT include regular string literals."""
        source = 'x = "not a docstring"'
        tree = ast.parse(source)
        lines = get_docstring_lines(tree)
        self.assertNotIn(1, lines)


class TestCheckParseHookInput(unittest.TestCase):
    """Tests for HOOK001: Use parse_hook_input() instead of json.loads(sys.stdin.read())."""

    def test_detects_json_loads_stdin_read(self):
        """Should detect json.loads(sys.stdin.read())."""
        source = """
import json
import sys
data = json.loads(sys.stdin.read())
"""
        tree = ast.parse(source)
        errors = check_parse_hook_input(tree, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK001")

    def test_allows_parse_hook_input(self):
        """Should allow parse_hook_input()."""
        source = """
from common import parse_hook_input
data = parse_hook_input()
"""
        tree = ast.parse(source)
        errors = check_parse_hook_input(tree, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_json_loads_with_other_source(self):
        """Should allow json.loads() with other sources."""
        source = """
import json
data = json.loads(some_string)
"""
        tree = ast.parse(source)
        errors = check_parse_hook_input(tree, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_json_loads_file_read(self):
        """Should allow json.loads(file.read())."""
        source = """
import json
with open("file.json") as f:
    data = json.loads(f.read())
"""
        tree = ast.parse(source)
        errors = check_parse_hook_input(tree, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_non_sys_stdin_read(self):
        """Should not flag json.loads(obj.stdin.read()) when obj is not sys."""
        source = """
import json
data = json.loads(custom_obj.stdin.read())
"""
        tree = ast.parse(source)
        errors = check_parse_hook_input(tree, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_mock_stdin_read(self):
        """Should not flag json.loads(mock.stdin.read())."""
        source = """
import json
data = json.loads(mock.stdin.read())
"""
        tree = ast.parse(source)
        errors = check_parse_hook_input(tree, "test.py")
        self.assertEqual(len(errors), 0)


class TestCheckLogHookExecution(unittest.TestCase):
    """Tests for HOOK002: log_hook_execution argument count."""

    def test_detects_too_few_args(self):
        """Should detect log_hook_execution with less than 2 args."""
        source = """
log_hook_execution("hook_name")
"""
        tree = ast.parse(source)
        errors = check_log_hook_execution(tree, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK002")
        self.assertIn("at least 2 arguments", errors[0].message)

    def test_detects_too_many_args(self):
        """Should detect log_hook_execution with more than 5 args."""
        source = """
log_hook_execution("hook_name", "approve", "reason", {"details": 1}, 100, "extra")
"""
        tree = ast.parse(source)
        errors = check_log_hook_execution(tree, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK002")
        self.assertIn("at most 5 arguments", errors[0].message)

    def test_allows_two_args(self):
        """Should allow log_hook_execution with 2 args."""
        source = """
log_hook_execution("hook_name", "approve")
"""
        tree = ast.parse(source)
        errors = check_log_hook_execution(tree, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_three_args(self):
        """Should allow log_hook_execution with 3 args."""
        source = """
log_hook_execution("hook_name", "approve", "reason")
"""
        tree = ast.parse(source)
        errors = check_log_hook_execution(tree, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_four_args(self):
        """Should allow log_hook_execution with 4 args."""
        source = """
log_hook_execution("hook_name", "approve", "reason", {"details": 1})
"""
        tree = ast.parse(source)
        errors = check_log_hook_execution(tree, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_five_args(self):
        """Should allow log_hook_execution with 5 args (with duration_ms)."""
        source = """
log_hook_execution("hook_name", "approve", "reason", {"details": 1}, 45)
"""
        tree = ast.parse(source)
        errors = check_log_hook_execution(tree, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_keyword_args(self):
        """Should allow log_hook_execution with keyword args."""
        source = """
log_hook_execution("hook_name", "approve", details={"key": "value"})
"""
        tree = ast.parse(source)
        errors = check_log_hook_execution(tree, "test.py")
        self.assertEqual(len(errors), 0)


class TestCheckMakeBlockResult(unittest.TestCase):
    """Tests for HOOK003: make_block_result requires 2-3 arguments.

    Issue #2456: HookContext DI移行により、オプショナルなctx引数を追加。
    Signature: make_block_result(hook_name, reason, ctx=None)
    """

    def test_detects_one_arg(self):
        """Should detect make_block_result with 1 arg."""
        source = """
make_block_result("reason only")
"""
        tree = ast.parse(source)
        errors = check_make_block_result(tree, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK003")
        self.assertIn("at least 2 arguments", errors[0].message)

    def test_detects_four_args(self):
        """Should detect make_block_result with 4 args."""
        source = """
make_block_result("hook_name", "reason", ctx, "extra")
"""
        tree = ast.parse(source)
        errors = check_make_block_result(tree, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK003")
        self.assertIn("at most 3 arguments", errors[0].message)

    def test_allows_two_args(self):
        """Should allow make_block_result with 2 args."""
        source = """
make_block_result("hook_name", "reason")
"""
        tree = ast.parse(source)
        errors = check_make_block_result(tree, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_three_args(self):
        """Should allow make_block_result with 3 args (with ctx).

        Issue #2456: HookContext DI移行により、オプショナルなctx引数を追加。
        """
        source = """
make_block_result("hook_name", "reason", ctx)
"""
        tree = ast.parse(source)
        errors = check_make_block_result(tree, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_keyword_args(self):
        """Should allow make_block_result with keyword args."""
        source = """
make_block_result(hook_name="my_hook", reason="blocked")
"""
        tree = ast.parse(source)
        errors = check_make_block_result(tree, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_ctx_keyword_arg(self):
        """Should allow make_block_result with ctx as keyword arg.

        Issue #2456: HookContext DI移行により、ctx=ctx形式をサポート。
        """
        source = """
make_block_result("hook_name", "reason", ctx=ctx)
"""
        tree = ast.parse(source)
        errors = check_make_block_result(tree, "test.py")
        self.assertEqual(len(errors), 0)


class TestCheckExceptPassComment(unittest.TestCase):
    """Tests for HOOK004: except-pass blocks should have explanatory comments."""

    def test_detects_except_pass_without_comment(self):
        """Should detect except-pass without comment."""
        source = """
try:
    do_something()
except OSError:
    pass
"""
        tree = ast.parse(source)
        errors = check_except_pass_comment(tree, source, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK004")
        self.assertIn("OSError", errors[0].message)

    def test_allows_except_pass_with_inline_comment_on_except(self):
        """Should allow except-pass with inline comment on except line."""
        source = """
try:
    do_something()
except OSError:  # Best effort, ignore errors
    pass
"""
        tree = ast.parse(source)
        errors = check_except_pass_comment(tree, source, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_except_pass_with_inline_comment_on_pass(self):
        """Should allow except-pass with inline comment on pass line."""
        source = """
try:
    do_something()
except OSError:
    pass  # Intentionally ignored
"""
        tree = ast.parse(source)
        errors = check_except_pass_comment(tree, source, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_except_pass_with_comment_between(self):
        """Should allow except-pass with comment between except and pass."""
        source = """
try:
    do_something()
except OSError:
    # This error is expected when file doesn't exist
    pass
"""
        tree = ast.parse(source)
        errors = check_except_pass_comment(tree, source, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_except_with_body(self):
        """Should allow except with actual body (not just pass)."""
        source = """
try:
    do_something()
except OSError:
    log_error()
"""
        tree = ast.parse(source)
        errors = check_except_pass_comment(tree, source, "test.py")
        self.assertEqual(len(errors), 0)

    def test_detects_multiple_exceptions(self):
        """Should detect tuple of exceptions."""
        source = """
try:
    do_something()
except (OSError, ValueError):
    pass
"""
        tree = ast.parse(source)
        errors = check_except_pass_comment(tree, source, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertIn("OSError, ValueError", errors[0].message)

    def test_detects_bare_except(self):
        """Should detect bare except."""
        source = """
try:
    do_something()
except:
    pass
"""
        tree = ast.parse(source)
        errors = check_except_pass_comment(tree, source, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK004")

    def test_tokenize_based_comment_detection(self):
        """Verify tokenize-based comment detection is used (Issue #1193).

        The tokenize module correctly distinguishes real comments from # in strings.
        This test verifies the implementation uses tokenize, not simple string search.
        """
        # Code with real comment - should NOT error
        source_with_comment = """
try:
    do_something()
except OSError:  # This is a real comment
    pass
"""
        tree = ast.parse(source_with_comment)
        errors = check_except_pass_comment(tree, source_with_comment, "test.py")
        self.assertEqual(len(errors), 0)

        # Code without any comment - should error
        source_without_comment = """
try:
    do_something()
except OSError:
    pass
"""
        tree = ast.parse(source_without_comment)
        errors = check_except_pass_comment(tree, source_without_comment, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK004")

    def test_no_false_positive_for_hash_in_string(self):
        """Should NOT treat # in string as a comment (Issue #1200).

        A string containing # in the try block should not be treated as a
        comment for the except block. If the implementation incorrectly
        treats # in strings as comments, this test would pass when it should fail.
        """
        source = """
try:
    x = "# not a comment"
except ValueError:
    pass
"""
        tree = ast.parse(source)
        errors = check_except_pass_comment(tree, source, "test.py")
        # Should error because there's no real comment in the except block
        # The # in the string should not count as a comment
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK004")

    def test_no_false_positive_for_hash_in_fstring(self):
        """Should NOT treat # in f-string as a comment (Issue #1200).

        An f-string containing # should not be treated as a comment.
        """
        source = """
try:
    msg = f"Error #{code}"
except ValueError:
    pass
"""
        tree = ast.parse(source)
        errors = check_except_pass_comment(tree, source, "test.py")
        # Should error because there's no real comment
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK004")


class TestCheckHardcodedTmpPath(unittest.TestCase):
    """Tests for HOOK005: Hardcoded /tmp paths should use tempfile.gettempdir()."""

    def test_detects_tmp_path(self):
        """Should detect hardcoded /tmp path."""
        source = """
from pathlib import Path
path = Path("/tmp")
"""
        tree = ast.parse(source)
        errors = check_hardcoded_tmp_path(tree, source, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK005")

    def test_detects_tmp_subpath(self):
        """Should detect hardcoded /tmp subpath."""
        source = """
path = "/tmp/my-app/file.txt"
"""
        tree = ast.parse(source)
        errors = check_hardcoded_tmp_path(tree, source, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK005")

    def test_allows_tempfile_gettempdir(self):
        """Should allow tempfile.gettempdir()."""
        source = """
import tempfile
from pathlib import Path
path = Path(tempfile.gettempdir())
"""
        tree = ast.parse(source)
        errors = check_hardcoded_tmp_path(tree, source, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_similar_paths(self):
        """Should allow paths that contain 'tmp' but are not /tmp."""
        source = """
path = "/templates/foo"
path2 = "/temporary/bar"
"""
        tree = ast.parse(source)
        errors = check_hardcoded_tmp_path(tree, source, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_relative_tmp_paths(self):
        """Should allow relative paths with 'tmp' in them."""
        source = """
path = "tmp/foo"
path2 = "./tmp/bar"
"""
        tree = ast.parse(source)
        errors = check_hardcoded_tmp_path(tree, source, "test.py")
        self.assertEqual(len(errors), 0)

    def test_no_false_positive_in_module_docstring(self):
        """Should NOT detect /tmp in module docstring (Issue #1193)."""
        source = '''"""
Module docstring.

Example:
    Files are stored in /tmp/my-app
"""
import os
'''
        tree = ast.parse(source)
        errors = check_hardcoded_tmp_path(tree, source, "test.py")
        self.assertEqual(len(errors), 0)

    def test_no_false_positive_in_function_docstring(self):
        """Should NOT detect /tmp in function docstring (Issue #1193)."""
        source = '''
def process_file():
    """Process a file.

    Files are temporarily stored in /tmp/processing.
    """
    pass
'''
        tree = ast.parse(source)
        errors = check_hardcoded_tmp_path(tree, source, "test.py")
        self.assertEqual(len(errors), 0)

    def test_no_false_positive_in_class_docstring(self):
        """Should NOT detect /tmp in class docstring (Issue #1193)."""
        source = '''
class FileProcessor:
    """Process files.

    Default temp directory: /tmp/processor
    """
    pass
'''
        tree = ast.parse(source)
        errors = check_hardcoded_tmp_path(tree, source, "test.py")
        self.assertEqual(len(errors), 0)

    def test_still_detects_tmp_in_code(self):
        """Should still detect /tmp in actual code even with docstrings."""
        source = '''
def process_file():
    """This function processes files."""
    path = "/tmp/my-file"
'''
        tree = ast.parse(source)
        errors = check_hardcoded_tmp_path(tree, source, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK005")

    def test_no_false_positive_in_comment(self):
        """Should NOT detect /tmp in comments (Issue #1200)."""
        source = """
# Files are stored in /tmp/cache for performance
def process():
    pass
"""
        tree = ast.parse(source)
        errors = check_hardcoded_tmp_path(tree, source, "test.py")
        self.assertEqual(len(errors), 0)

    def test_no_false_positive_in_inline_comment(self):
        """Should NOT detect /tmp in inline comments (Issue #1200)."""
        source = """
def process():
    x = 1  # Default uses /tmp/app for caching
"""
        tree = ast.parse(source)
        errors = check_hardcoded_tmp_path(tree, source, "test.py")
        self.assertEqual(len(errors), 0)

    def test_detects_tmp_in_fstring(self):
        """Should detect /tmp in f-strings (Issue #1200).

        f-strings with hardcoded /tmp are still violations,
        as they represent actual paths used in code.
        """
        source = """
name = "file"
path = f"/tmp/{name}"
"""
        tree = ast.parse(source)
        errors = check_hardcoded_tmp_path(tree, source, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK005")


class TestParseArgs(unittest.TestCase):
    """Tests for parse_args() function (Issue #1198)."""

    def setUp(self):
        """Save original sys.argv before each test."""
        self._original_argv = sys.argv.copy()

    def tearDown(self):
        """Restore original sys.argv after each test."""
        sys.argv = self._original_argv

    def test_no_args(self):
        """Should parse with no arguments."""
        sys.argv = ["hook_lint.py"]
        args = parse_args()
        self.assertEqual(args.files, [])
        self.assertFalse(args.check_only)

    def test_files_args(self):
        """Should parse file arguments."""
        sys.argv = ["hook_lint.py", "file1.py", "file2.py"]
        args = parse_args()
        self.assertEqual(args.files, ["file1.py", "file2.py"])
        self.assertFalse(args.check_only)

    def test_check_only_flag(self):
        """Should parse --check-only flag."""
        sys.argv = ["hook_lint.py", "--check-only"]
        args = parse_args()
        self.assertEqual(args.files, [])
        self.assertTrue(args.check_only)

    def test_check_only_with_files(self):
        """Should parse --check-only with files."""
        sys.argv = ["hook_lint.py", "--check-only", "file1.py"]
        args = parse_args()
        self.assertEqual(args.files, ["file1.py"])
        self.assertTrue(args.check_only)


class TestPrintSummary(unittest.TestCase):
    """Tests for print_summary() function (Issue #1198)."""

    def test_no_errors(self):
        """Should print 'No violations found' when no errors."""
        f = io.StringIO()
        with redirect_stdout(f):
            print_summary([], 10)
        output = f.getvalue()
        self.assertIn("No violations found", output)
        self.assertIn("10 file(s)", output)

    def test_single_error_type(self):
        """Should format single error type correctly."""
        errors = [
            LintError("test.py", 1, "HOOK001", "message"),
            LintError("test.py", 2, "HOOK001", "message"),
        ]
        f = io.StringIO()
        with redirect_stdout(f):
            print_summary(errors, 5)
        output = f.getvalue()
        self.assertIn("2 violations found", output)
        self.assertIn("HOOK001: 2", output)

    def test_multiple_error_types(self):
        """Should format multiple error types correctly."""
        errors = [
            LintError("test.py", 1, "HOOK001", "message"),
            LintError("test.py", 2, "HOOK004", "message"),
            LintError("test.py", 3, "HOOK004", "message"),
            LintError("test.py", 4, "HOOK005", "message"),
        ]
        f = io.StringIO()
        with redirect_stdout(f):
            print_summary(errors, 3)
        output = f.getvalue()
        self.assertIn("4 violations found", output)
        self.assertIn("HOOK001: 1", output)
        self.assertIn("HOOK004: 2", output)
        self.assertIn("HOOK005: 1", output)


class TestCheckLogHookExecutionRequiresParseHookInput(unittest.TestCase):
    """Tests for HOOK006: log_hook_execution requires parse_hook_input first."""

    def test_detects_missing_parse_hook_input(self):
        """Should detect log_hook_execution without parse_hook_input."""
        source = """
def main():
    log_hook_execution("hook", "approve")
"""
        tree = ast.parse(source)
        errors = check_log_hook_execution_requires_parse_hook_input(tree, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK006")
        self.assertIn("requires parse_hook_input()", errors[0].message)

    def test_allows_correct_order(self):
        """Should allow parse_hook_input before log_hook_execution."""
        source = """
def main():
    data = parse_hook_input()
    log_hook_execution("hook", "approve")
"""
        tree = ast.parse(source)
        errors = check_log_hook_execution_requires_parse_hook_input(tree, "test.py")
        self.assertEqual(len(errors), 0)

    def test_detects_wrong_order_in_main(self):
        """Should detect log_hook_execution before parse_hook_input in main() (Issue #1303)."""
        source = """
def main():
    log_hook_execution("hook", "approve")
    data = parse_hook_input()
"""
        tree = ast.parse(source)
        errors = check_log_hook_execution_requires_parse_hook_input(tree, "test.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "HOOK006")
        self.assertIn("must be called before", errors[0].message)

    def test_allows_no_log_hook_execution(self):
        """Should allow files without log_hook_execution."""
        source = """
def main():
    data = parse_hook_input()
    print(data)
"""
        tree = ast.parse(source)
        errors = check_log_hook_execution_requires_parse_hook_input(tree, "test.py")
        self.assertEqual(len(errors), 0)

    def test_allows_log_in_helper_function(self):
        """Should allow log_hook_execution in helper function when parse_hook_input is in main()."""
        source = """
def run_review(issue_number):
    log_hook_execution("hook", "approve", "Review script not found")
    return None

def main():
    data = parse_hook_input()
    run_review(123)
"""
        tree = ast.parse(source)
        errors = check_log_hook_execution_requires_parse_hook_input(tree, "test.py")
        # Should not error because order check is only within main()
        # and parse_hook_input is called in main() (even if log_hook_execution
        # is in a helper function that's called later)
        self.assertEqual(len(errors), 0)

    def test_allows_no_main_function(self):
        """Should not error if there's no main() function."""
        source = """
def other_function():
    log_hook_execution("hook", "approve")
    data = parse_hook_input()
"""
        tree = ast.parse(source)
        errors = check_log_hook_execution_requires_parse_hook_input(tree, "test.py")
        # No main() function, so order check is skipped
        self.assertEqual(len(errors), 0)


if __name__ == "__main__":
    unittest.main()
