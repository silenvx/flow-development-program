"""Tests for signature_change_check.py hook."""

import unittest
from unittest.mock import patch

# Import the module under test
import signature_change_check


class TestExtractSignatureChanges(unittest.TestCase):
    """Test cases for extract_signature_changes function."""

    def test_detects_return_type_change(self):
        """Test detection of return type changes."""
        diff = """
-def foo(x: int) -> int:
+def foo(x: int) -> tuple[int, str]:
"""
        changes = signature_change_check.extract_signature_changes(diff)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["function_name"], "foo")
        self.assertEqual(changes[0]["change_type"], "return")
        self.assertEqual(changes[0]["old_return"], "int")
        self.assertEqual(changes[0]["new_return"], "tuple[int, str]")

    def test_detects_argument_change(self):
        """Test detection of argument changes."""
        diff = """
-def bar(x: int) -> str:
+def bar(x: int, y: str) -> str:
"""
        changes = signature_change_check.extract_signature_changes(diff)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["function_name"], "bar")
        self.assertEqual(changes[0]["change_type"], "args")

    def test_detects_both_changes(self):
        """Test detection of both argument and return type changes."""
        diff = """
-def baz(a: int) -> int:
+def baz(a: int, b: str) -> list[int]:
"""
        changes = signature_change_check.extract_signature_changes(diff)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["function_name"], "baz")
        self.assertEqual(changes[0]["change_type"], "both")

    def test_ignores_new_functions(self):
        """Test that newly added functions are ignored (not signature changes)."""
        diff = """
+def new_func(x: int) -> str:
+    return str(x)
"""
        changes = signature_change_check.extract_signature_changes(diff)
        self.assertEqual(len(changes), 0)

    def test_ignores_removed_functions(self):
        """Test that removed functions are ignored."""
        diff = """
-def old_func(x: int) -> str:
-    return str(x)
"""
        changes = signature_change_check.extract_signature_changes(diff)
        self.assertEqual(len(changes), 0)

    def test_ignores_unchanged_signatures(self):
        """Test that functions with unchanged signatures are ignored."""
        diff = """
 def unchanged(x: int) -> str:
-    return str(x)
+    return f"{x}"
"""
        changes = signature_change_check.extract_signature_changes(diff)
        self.assertEqual(len(changes), 0)

    def test_handles_no_return_type(self):
        """Test handling of functions without return type annotations."""
        diff = """
-def no_return(x):
+def no_return(x, y):
"""
        changes = signature_change_check.extract_signature_changes(diff)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["function_name"], "no_return")
        self.assertEqual(changes[0]["change_type"], "args")

    def test_handles_empty_diff(self):
        """Test handling of empty diff."""
        changes = signature_change_check.extract_signature_changes("")
        self.assertEqual(len(changes), 0)


class TestFindTestFile(unittest.TestCase):
    """Test cases for find_test_file function."""

    def test_finds_hook_test_file(self):
        """Test finding test file for hooks."""
        result = signature_change_check.find_test_file(".claude/hooks/foo.py")
        self.assertEqual(result, ".claude/hooks/tests/test_foo.py")

    def test_finds_script_test_file(self):
        """Test finding test file for scripts."""
        result = signature_change_check.find_test_file(".claude/scripts/bar.py")
        self.assertEqual(result, ".claude/scripts/tests/test_bar.py")

    def test_returns_none_for_test_files(self):
        """Test that test files return None."""
        result = signature_change_check.find_test_file(".claude/hooks/tests/test_foo.py")
        self.assertIsNone(result)

    def test_handles_nested_paths(self):
        """Test handling of nested directory paths.

        For .claude/scripts and .claude/hooks, tests are in the central tests/ directory,
        not nested subdirectories.
        """
        result = signature_change_check.find_test_file(".claude/scripts/sub/baz.py")
        # Tests are in .claude/scripts/tests/, not .claude/scripts/sub/tests/
        self.assertEqual(result, ".claude/scripts/tests/test_baz.py")

    def test_normalizes_hyphens_to_underscores(self):
        """Test that hyphenated filenames are normalized for test file lookup.

        Hook files like 'active-worktree-check.py' have tests named
        'test_active_worktree_check.py' with underscores.
        """
        result = signature_change_check.find_test_file(".claude/hooks/active-worktree-check.py")
        self.assertEqual(result, ".claude/hooks/tests/test_active_worktree_check.py")


class TestGetModifiedPythonFiles(unittest.TestCase):
    """Test cases for get_modified_python_files function."""

    @patch("signature_change_check.subprocess.run")
    def test_filters_python_files(self, mock_run):
        """Test that only Python files are returned."""
        mock_run.return_value.stdout = "foo.py\nbar.ts\nbaz.py\nqux.js\n"
        mock_run.return_value.returncode = 0

        result = signature_change_check.get_modified_python_files()

        self.assertEqual(result, ["foo.py", "baz.py"])

    @patch("signature_change_check.subprocess.run")
    def test_handles_empty_output(self, mock_run):
        """Test handling of no modified files."""
        mock_run.return_value.stdout = ""
        mock_run.return_value.returncode = 0

        result = signature_change_check.get_modified_python_files()

        self.assertEqual(result, [])


class TestMain(unittest.TestCase):
    """Test cases for main function."""

    @patch("signature_change_check.get_modified_python_files")
    def test_returns_zero_when_no_files(self, mock_get_files):
        """Test that main returns 0 when no files are modified."""
        mock_get_files.return_value = []

        result = signature_change_check.main()

        self.assertEqual(result, 0)

    @patch("signature_change_check.get_modified_python_files")
    def test_returns_zero_when_no_claude_files(self, mock_get_files):
        """Test that main returns 0 when no .claude/ files are modified."""
        mock_get_files.return_value = ["frontend/foo.py", "worker/bar.py"]

        result = signature_change_check.main()

        self.assertEqual(result, 0)

    @patch("signature_change_check.get_diff_for_file")
    @patch("signature_change_check.get_modified_python_files")
    def test_warns_on_signature_change_without_test(self, mock_get_files, mock_get_diff):
        """Test warning when signature changes but test file not updated."""
        mock_get_files.return_value = [".claude/hooks/foo.py"]
        mock_get_diff.return_value = """
-def bar(x: int) -> int:
+def bar(x: int) -> tuple[int, str]:
"""

        # Should print warning but return 0 (don't block)
        result = signature_change_check.main()

        self.assertEqual(result, 0)

    @patch("signature_change_check.get_diff_for_file")
    @patch("signature_change_check.get_modified_python_files")
    def test_no_warning_when_test_updated(self, mock_get_files, mock_get_diff):
        """Test no warning when test file is also updated."""
        mock_get_files.return_value = [
            ".claude/hooks/foo.py",
            ".claude/hooks/tests/test_foo.py",
        ]
        mock_get_diff.return_value = """
-def bar(x: int) -> int:
+def bar(x: int) -> tuple[int, str]:
"""

        result = signature_change_check.main()

        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
