#!/usr/bin/env python3
"""Tests for python-lint-check.py hook."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

HOOK_PATH = Path(__file__).parent.parent / "python-lint-check.py"


def run_hook(input_data: dict, env: dict | None = None) -> dict:
    """Run the hook with given input and return the result.

    Args:
        input_data: The JSON input data to pass to the hook.
        env: Optional environment variables to set for the subprocess.
             If provided, these are merged with the current environment.
    """
    # Merge with current environment if custom env is provided
    subprocess_env = None
    if env:
        subprocess_env = os.environ.copy()
        subprocess_env.update(env)

    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        env=subprocess_env,
    )
    return json.loads(result.stdout)


class TestPythonLintCheckIntegration:
    """Integration tests for python-lint-check hook."""

    def test_ignores_non_git_commit_commands(self):
        """Should approve non-git-commit commands."""
        result = run_hook({"tool_input": {"command": "git status"}})
        assert result["decision"] == "approve"

    def test_ignores_git_push(self):
        """Should approve git push commands."""
        result = run_hook({"tool_input": {"command": "git push"}})
        assert result["decision"] == "approve"

    def test_ignores_ls_command(self):
        """Should approve unrelated commands."""
        result = run_hook({"tool_input": {"command": "ls -la"}})
        assert result["decision"] == "approve"

    def test_handles_empty_command(self):
        """Should handle empty command gracefully."""
        result = run_hook({"tool_input": {"command": ""}})
        assert result["decision"] == "approve"

    def test_handles_missing_tool_input(self):
        """Should handle missing tool_input gracefully."""
        result = run_hook({})
        assert result["decision"] == "approve"

    # Tests for various git commit command formats
    def test_rejects_false_positive_echo_git_commit(self):
        """Should NOT match 'echo git commit' (anchored regex)."""
        result = run_hook({"tool_input": {"command": "echo 'git commit'"}})
        assert result["decision"] == "approve"

    def test_rejects_false_positive_comment_git_commit(self):
        """Should NOT match '# git commit' (anchored regex)."""
        result = run_hook({"tool_input": {"command": "# git commit -m 'test'"}})
        assert result["decision"] == "approve"


class TestPythonLintCheckUnit:
    """Unit tests for python-lint-check hook functions."""

    def setup_method(self):
        """Import module functions for testing."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("python_lint_check", str(HOOK_PATH))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    @patch("subprocess.run")
    def test_get_staged_python_files_returns_py_only(self, mock_run):
        """Should only return .py files."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="file1.py\nfile2.js\nfile3.py\nREADME.md\n",
        )
        result = self.module.get_staged_python_files()
        assert result == ["file1.py", "file3.py"]

    @patch("subprocess.run")
    def test_get_staged_python_files_empty_output(self, mock_run):
        """Should handle empty output."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = self.module.get_staged_python_files()
        assert result == []

    @patch("subprocess.run")
    def test_get_staged_python_files_git_error(self, mock_run):
        """Should handle git errors gracefully."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = self.module.get_staged_python_files()
        assert result == []

    @patch("subprocess.run")
    def test_check_ruff_format_passes(self, mock_run):
        """Should return True when format passes."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        passed, err = self.module.check_ruff_format(["test.py"])
        assert passed
        assert err == ""

    @patch("subprocess.run")
    def test_check_ruff_format_fails(self, mock_run):
        """Should return False when format fails."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="Would reformat: test.py\n",
            stderr="",
        )
        passed, err = self.module.check_ruff_format(["test.py"])
        assert not passed
        assert "Would reformat" in err

    @patch("subprocess.run")
    def test_check_ruff_format_empty_files(self, mock_run):
        """Should return True for empty file list."""
        passed, err = self.module.check_ruff_format([])
        assert passed
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_check_ruff_lint_passes(self, mock_run):
        """Should return True when lint passes."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        passed, err = self.module.check_ruff_lint(["test.py"])
        assert passed
        assert err == ""

    @patch("subprocess.run")
    def test_check_ruff_lint_fails(self, mock_run):
        """Should return False when lint fails."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="test.py:1:1: F401 unused import\n",
            stderr="",
        )
        passed, err = self.module.check_ruff_lint(["test.py"])
        assert not passed
        assert "F401" in err

    @patch("subprocess.run")
    def test_check_ruff_lint_empty_files(self, mock_run):
        """Should return True for empty file list."""
        passed, err = self.module.check_ruff_lint([])
        assert passed
        mock_run.assert_not_called()


class TestGitCommitRegex:
    """Tests for git commit command regex matching."""

    def setup_method(self):
        """Import module for testing."""
        import importlib.util
        import re

        self.re = re
        spec = importlib.util.spec_from_file_location("python_lint_check", str(HOOK_PATH))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        # Extract the regex pattern used in main()
        self.pattern = r"^\s*git\s+commit(\s|$)"

    def test_matches_git_commit(self):
        """Should match 'git commit'."""
        assert self.re.search(self.pattern, "git commit")

    def test_matches_git_commit_with_message(self):
        """Should match 'git commit -m \"message\"'."""
        assert self.re.search(self.pattern, 'git commit -m "message"')

    def test_matches_git_commit_amend(self):
        """Should match 'git commit --amend'."""
        assert self.re.search(self.pattern, "git commit --amend")

    def test_matches_git_commit_all(self):
        """Should match 'git commit -a -m \"message\"'."""
        assert self.re.search(self.pattern, 'git commit -a -m "message"')

    def test_matches_git_commit_with_leading_space(self):
        """Should match '  git commit' (leading whitespace)."""
        assert self.re.search(self.pattern, "  git commit")

    def test_not_matches_echo_git_commit(self):
        """Should NOT match 'echo git commit'."""
        assert not self.re.search(self.pattern, "echo 'git commit'")

    def test_not_matches_comment_git_commit(self):
        """Should NOT match '# git commit'."""
        assert not self.re.search(self.pattern, "# git commit")

    def test_not_matches_git_committed(self):
        """Should NOT match 'git committed' (different word)."""
        # This tests that our regex correctly handles word boundary
        result = self.re.search(self.pattern, "git committed")
        # "git committed" starts with "git commit" but is followed by "t" not whitespace
        # With pattern r"^\s*git\s+commit(\s|$)", "git committed" should NOT match
        # because after "commit" comes "ted" not whitespace or end
        assert not result


class TestIsGitCommitCommand:
    """Tests for is_git_commit_command function (Issue #959).

    This function handles command chains like:
    - git add && git commit -m "msg"
    - git status; git commit
    """

    def setup_method(self):
        """Import module for testing."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("python_lint_check", str(HOOK_PATH))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_simple_git_commit(self):
        """Should detect simple git commit."""
        assert self.module.is_git_commit_command("git commit")

    def test_git_commit_with_message(self):
        """Should detect git commit with message."""
        assert self.module.is_git_commit_command('git commit -m "test"')

    def test_git_add_and_commit_chain(self):
        """Should detect git commit in && chain."""
        assert self.module.is_git_commit_command('git add . && git commit -m "test"')

    def test_git_status_semicolon_commit(self):
        """Should detect git commit after semicolon."""
        assert self.module.is_git_commit_command("git status; git commit")

    def test_git_add_or_commit_chain(self):
        """Should detect git commit in || chain."""
        assert self.module.is_git_commit_command("false || git commit -m 'test'")

    def test_triple_chain_with_commit(self):
        """Should detect git commit in longer chain."""
        assert self.module.is_git_commit_command('git add . && git commit -m "msg" && git push')

    def test_echo_git_commit_ignored(self):
        """Should NOT detect 'echo git commit' (quoted)."""
        assert not self.module.is_git_commit_command('echo "git commit"')

    def test_non_commit_command(self):
        """Should NOT detect non-commit commands."""
        assert not self.module.is_git_commit_command("git status")

    def test_git_push_chain(self):
        """Should NOT detect chain without commit."""
        assert not self.module.is_git_commit_command("git add . && git push")

    def test_empty_command(self):
        """Should handle empty command."""
        assert not self.module.is_git_commit_command("")


class TestMainFunctionIntegration:
    """Integration tests for main function via subprocess.

    These tests run the actual hook script via subprocess.
    Uses _TEST_NO_STAGED_FILES=1 to simulate no staged Python files,
    ensuring tests are isolated from actual git state.
    """

    def test_git_commit_with_no_staged_files(self):
        """Should approve 'git commit' when no Python files are staged."""
        result = run_hook(
            {"tool_input": {"command": "git commit -m 'test'"}},
            env={"_TEST_NO_STAGED_FILES": "1"},
        )
        assert result["decision"] == "approve"

    def test_git_commit_amend_with_no_staged_files(self):
        """Should approve 'git commit --amend' when no Python files are staged."""
        result = run_hook(
            {"tool_input": {"command": "git commit --amend"}},
            env={"_TEST_NO_STAGED_FILES": "1"},
        )
        assert result["decision"] == "approve"

    def test_git_commit_all_with_no_staged_files(self):
        """Should approve 'git commit -a' when no Python files are staged."""
        result = run_hook(
            {"tool_input": {"command": "git commit -a -m 'test'"}},
            env={"_TEST_NO_STAGED_FILES": "1"},
        )
        assert result["decision"] == "approve"


class TestBlockingScenarios:
    """Tests for blocking scenarios (format/lint failures).

    Note: Full integration tests for blocking scenarios require either:
    1. Actually staging files with formatting issues (not practical in unit tests)
    2. Significant refactoring to make the main() function more testable

    These tests verify the component functions that would trigger blocking.
    """

    def setup_method(self):
        """Import module for testing."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("python_lint_check", str(HOOK_PATH))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    @patch("subprocess.run")
    def test_format_failure_returns_error_message(self, mock_run):
        """Should return False with error message on format failure."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="Would reformat test.py\n",
            stderr="1 file would be reformatted\n",
        )
        passed, err = self.module.check_ruff_format(["test.py"])
        assert not passed
        assert "Would reformat" in err

    @patch("subprocess.run")
    def test_lint_failure_returns_error_message(self, mock_run):
        """Should return False with error message on lint failure."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="test.py:1:1: E401 module level import not at top of file\n",
            stderr="Found 1 error\n",
        )
        passed, err = self.module.check_ruff_lint(["test.py"])
        assert not passed
        assert "E401" in err

    @patch("subprocess.run")
    def test_format_exception_fails_open(self, mock_run):
        """Should return True (fail open) when ruff format throws exception."""
        mock_run.side_effect = Exception("uvx not found")
        passed, err = self.module.check_ruff_format(["test.py"])
        assert passed
        assert "Warning" in err

    @patch("subprocess.run")
    def test_lint_exception_fails_open(self, mock_run):
        """Should return True (fail open) when ruff check throws exception."""
        mock_run.side_effect = Exception("uvx not found")
        passed, err = self.module.check_ruff_lint(["test.py"])
        assert passed
        assert "Warning" in err


class TestGetGitToplevel:
    """Tests for get_git_toplevel function (Issue #2162).

    This function gets the root directory of the current git worktree.
    """

    def setup_method(self):
        """Import module for testing."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("python_lint_check", str(HOOK_PATH))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    @patch("subprocess.run")
    def test_returns_git_toplevel(self, mock_run):
        """Should return the git toplevel directory."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/path/to/worktree\n",
        )
        result = self.module.get_git_toplevel()
        assert result == "/path/to/worktree"

    @patch("subprocess.run")
    def test_strips_trailing_newline(self, mock_run):
        """Should strip trailing newline from output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/path/to/worktree\n\n",
        )
        result = self.module.get_git_toplevel()
        assert result == "/path/to/worktree"

    @patch("subprocess.run")
    @patch("os.getcwd")
    def test_git_error_returns_cwd(self, mock_getcwd, mock_run):
        """Should return CWD when git command fails."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="fatal: not a git repository",
        )
        mock_getcwd.return_value = "/fallback/cwd"
        result = self.module.get_git_toplevel()
        assert result == "/fallback/cwd"

    @patch("subprocess.run")
    @patch("os.getcwd")
    def test_exception_returns_cwd(self, mock_getcwd, mock_run):
        """Should return CWD when exception occurs."""
        mock_run.side_effect = Exception("git not found")
        mock_getcwd.return_value = "/fallback/cwd"
        result = self.module.get_git_toplevel()
        assert result == "/fallback/cwd"

    @patch("subprocess.run")
    @patch("os.getcwd")
    def test_timeout_returns_cwd(self, mock_getcwd, mock_run):
        """Should return CWD when timeout occurs."""
        mock_run.side_effect = subprocess.TimeoutExpired("git", 30)
        mock_getcwd.return_value = "/fallback/cwd"
        result = self.module.get_git_toplevel()
        assert result == "/fallback/cwd"


class TestToAbsolutePaths:
    """Tests for to_absolute_paths function (Issue #2162).

    This function converts relative paths to absolute paths based on git root.
    """

    def setup_method(self):
        """Import module for testing."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("python_lint_check", str(HOOK_PATH))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_converts_relative_to_absolute(self):
        """Should convert relative paths to absolute paths."""
        files = ["src/main.py", "tests/test_main.py"]
        git_root = "/path/to/worktree"
        result = self.module.to_absolute_paths(files, git_root)
        assert result == [
            "/path/to/worktree/src/main.py",
            "/path/to/worktree/tests/test_main.py",
        ]

    def test_empty_file_list(self):
        """Should return empty list for empty input."""
        result = self.module.to_absolute_paths([], "/path/to/worktree")
        assert result == []

    def test_single_file(self):
        """Should handle single file correctly."""
        result = self.module.to_absolute_paths(["hook.py"], "/path/to/worktree")
        assert result == ["/path/to/worktree/hook.py"]

    def test_nested_paths(self):
        """Should handle deeply nested paths."""
        files = [".claude/hooks/tests/test_hook.py"]
        git_root = "/path/to/worktree"
        result = self.module.to_absolute_paths(files, git_root)
        assert result == ["/path/to/worktree/.claude/hooks/tests/test_hook.py"]

    def test_already_absolute_paths(self):
        """Should leave already absolute paths unchanged."""
        files = ["/absolute/path/file.py", "relative/file.py"]
        git_root = "/path/to/worktree"
        result = self.module.to_absolute_paths(files, git_root)
        assert result == ["/absolute/path/file.py", "/path/to/worktree/relative/file.py"]


class TestGetChangedFiles:
    """Tests for get_changed_files function (Issue #1712).

    This function detects files with unstaged changes using git diff.
    """

    def setup_method(self):
        """Import module for testing."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("python_lint_check", str(HOOK_PATH))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_empty_file_list(self):
        """Should return empty list for empty input."""
        result = self.module.get_changed_files([])
        assert result == []

    @patch("subprocess.run")
    def test_returns_changed_files_only(self, mock_run):
        """Should return only files that have unstaged changes."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="file1.py\nfile3.py\n",
        )
        result = self.module.get_changed_files(["file1.py", "file2.py", "file3.py"])
        assert result == ["file1.py", "file3.py"]

    @patch("subprocess.run")
    def test_no_changed_files(self, mock_run):
        """Should return empty list when no files have changes."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
        )
        result = self.module.get_changed_files(["file1.py", "file2.py"])
        assert result == []

    @patch("subprocess.run")
    def test_git_diff_failure_returns_all_files(self, mock_run):
        """Should fallback to all files when git diff fails."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="fatal: not a git repository",
        )
        files = ["file1.py", "file2.py"]
        result = self.module.get_changed_files(files)
        assert result == files

    @patch("subprocess.run")
    def test_timeout_exception_returns_all_files(self, mock_run):
        """Should fallback to all files when TimeoutExpired occurs."""
        mock_run.side_effect = subprocess.TimeoutExpired("git", 30)
        files = ["file1.py", "file2.py"]
        result = self.module.get_changed_files(files)
        assert result == files

    @patch("subprocess.run")
    def test_file_not_found_returns_all_files(self, mock_run):
        """Should fallback to all files when git is not found."""
        mock_run.side_effect = FileNotFoundError("git not found")
        files = ["file1.py", "file2.py"]
        result = self.module.get_changed_files(files)
        assert result == files

    @patch("subprocess.run")
    def test_strips_whitespace_from_output(self, mock_run):
        """Should strip whitespace from file paths."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="  file1.py  \n  file2.py  \n",
        )
        result = self.module.get_changed_files(["file1.py", "file2.py"])
        assert result == ["file1.py", "file2.py"]

    @patch("subprocess.run")
    def test_filters_empty_lines(self, mock_run):
        """Should filter out empty lines from output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="file1.py\n\n\nfile2.py\n",
        )
        result = self.module.get_changed_files(["file1.py", "file2.py"])
        assert result == ["file1.py", "file2.py"]
