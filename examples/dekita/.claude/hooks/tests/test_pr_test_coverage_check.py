#!/usr/bin/env python3
"""Unit tests for pr-test-coverage-check.py"""

import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Import with dynamic loading due to hyphens in filename
HOOK_PATH = Path(__file__).parent.parent / "pr-test-coverage-check.py"
_spec = importlib.util.spec_from_file_location("pr_test_coverage_check", HOOK_PATH)
pr_test_coverage_check = importlib.util.module_from_spec(_spec)
sys.modules["pr_test_coverage_check"] = pr_test_coverage_check
_spec.loader.exec_module(pr_test_coverage_check)


class TestIsGhPrCreateCommand:
    """Tests for is_gh_pr_create_command function."""

    def test_simple_gh_pr_create(self):
        """Should detect simple gh pr create command."""
        assert pr_test_coverage_check.is_gh_pr_create_command("gh pr create")

    def test_gh_pr_create_with_options(self):
        """Should detect gh pr create with options."""
        cmd = 'gh pr create --title "Test" --body "Body"'
        assert pr_test_coverage_check.is_gh_pr_create_command(cmd)

    def test_not_gh_pr_create(self):
        """Should not detect other gh commands."""
        assert not pr_test_coverage_check.is_gh_pr_create_command("gh pr list")
        assert not pr_test_coverage_check.is_gh_pr_create_command("gh pr view")
        assert not pr_test_coverage_check.is_gh_pr_create_command("gh issue create")

    def test_empty_command(self):
        """Should return False for empty command."""
        assert not pr_test_coverage_check.is_gh_pr_create_command("")
        assert not pr_test_coverage_check.is_gh_pr_create_command("   ")

    def test_quoted_gh_pr_create(self):
        """Should not detect gh pr create inside quotes."""
        cmd = "echo 'gh pr create'"
        assert not pr_test_coverage_check.is_gh_pr_create_command(cmd)


class TestGetHookFilesWithoutTests:
    """Tests for get_hook_files_without_tests function."""

    def test_non_hooks_files_ignored(self):
        """Files outside .claude/hooks should be ignored."""
        changed = ["src/app.py", "frontend/component.tsx"]
        result = pr_test_coverage_check.get_hook_files_without_tests(changed)
        assert result == []

    def test_test_files_ignored(self):
        """Test files themselves should be ignored."""
        changed = [".claude/hooks/tests/test_something.py"]
        result = pr_test_coverage_check.get_hook_files_without_tests(changed)
        assert result == []

    def test_non_python_files_ignored(self):
        """Non-Python files should be ignored."""
        changed = [".claude/hooks/README.md", ".claude/hooks/config.json"]
        result = pr_test_coverage_check.get_hook_files_without_tests(changed)
        assert result == []

    def test_common_py_ignored(self):
        """common.py should be ignored (utility file)."""
        changed = [".claude/hooks/common.py"]
        result = pr_test_coverage_check.get_hook_files_without_tests(changed)
        assert result == []

    def test_init_py_ignored(self):
        """__init__.py should be ignored."""
        changed = [".claude/hooks/__init__.py"]
        result = pr_test_coverage_check.get_hook_files_without_tests(changed)
        assert result == []

    def test_hook_with_existing_test(self):
        """Hook with existing test file should not be reported."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create hook file
            hooks_dir = Path(tmpdir) / ".claude" / "hooks"
            tests_dir = hooks_dir / "tests"
            tests_dir.mkdir(parents=True)

            hook_file = hooks_dir / "my-hook.py"
            hook_file.write_text("# hook")

            test_file = tests_dir / "test_my_hook.py"
            test_file.write_text("# test")

            with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": tmpdir}):
                changed = [".claude/hooks/my-hook.py"]
                result = pr_test_coverage_check.get_hook_files_without_tests(changed)
                assert result == []

    def test_hook_without_test(self):
        """Hook without test file should be reported."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create hook file only (no test)
            hooks_dir = Path(tmpdir) / ".claude" / "hooks"
            tests_dir = hooks_dir / "tests"
            tests_dir.mkdir(parents=True)

            hook_file = hooks_dir / "my-hook.py"
            hook_file.write_text("# hook")

            with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": tmpdir}):
                changed = [".claude/hooks/my-hook.py"]
                result = pr_test_coverage_check.get_hook_files_without_tests(changed)
                assert len(result) == 1
                assert result[0][0] == ".claude/hooks/my-hook.py"
                assert result[0][1] == ".claude/hooks/tests/test_my_hook.py"

    def test_hyphen_to_underscore_conversion(self):
        """Hyphens in hook names should be converted to underscores for test names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / ".claude" / "hooks"
            tests_dir = hooks_dir / "tests"
            tests_dir.mkdir(parents=True)

            hook_file = hooks_dir / "my-cool-hook.py"
            hook_file.write_text("# hook")

            with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": tmpdir}):
                changed = [".claude/hooks/my-cool-hook.py"]
                result = pr_test_coverage_check.get_hook_files_without_tests(changed)
                # Should expect test_my_cool_hook.py (underscores)
                assert result[0][1] == ".claude/hooks/tests/test_my_cool_hook.py"


class TestFormatWarningMessage:
    """Tests for format_warning_message function."""

    def test_single_missing_test(self):
        """Should format message for single missing test."""
        missing = [(".claude/hooks/my-hook.py", ".claude/hooks/tests/test_my_hook.py")]
        result = pr_test_coverage_check.format_warning_message(missing)

        assert "テストファイル不足" in result
        assert "my-hook.py" in result
        assert "test_my_hook.py" in result

    def test_multiple_missing_tests(self):
        """Should format message for multiple missing tests."""
        missing = [
            (".claude/hooks/hook1.py", ".claude/hooks/tests/test_hook1.py"),
            (".claude/hooks/hook2.py", ".claude/hooks/tests/test_hook2.py"),
        ]
        result = pr_test_coverage_check.format_warning_message(missing)

        assert "hook1.py" in result
        assert "hook2.py" in result

    def test_includes_non_blocking_notice(self):
        """Should indicate that the warning doesn't block."""
        missing = [(".claude/hooks/test.py", ".claude/hooks/tests/test_test.py")]
        result = pr_test_coverage_check.format_warning_message(missing)

        assert "ブロックしません" in result


class TestMain:
    """Integration tests for main() function."""

    def _run_main_with_input(self, input_data: dict) -> dict:
        """Helper to run main() with given input."""
        captured_output = io.StringIO()

        with (
            patch("pr_test_coverage_check.parse_hook_input", return_value=input_data),
            patch("sys.stdout", captured_output),
            patch("pr_test_coverage_check.log_hook_execution"),
            pytest.raises(SystemExit) as ctx,
        ):
            pr_test_coverage_check.main()

        assert ctx.value.code == 0
        return json.loads(captured_output.getvalue())

    def test_non_gh_pr_create_command(self):
        """Should approve non-gh-pr-create commands without message."""
        input_data = {"tool_input": {"command": "git status"}}
        result = self._run_main_with_input(input_data)

        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_gh_pr_create_no_changed_hooks(self):
        """Should approve when no hooks are changed."""
        input_data = {"tool_input": {"command": "gh pr create"}}

        with patch("pr_test_coverage_check.get_changed_files", return_value=["src/app.py"]):
            result = self._run_main_with_input(input_data)

        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_gh_pr_create_with_missing_tests(self):
        """Should show warning when hooks are missing tests."""
        input_data = {"tool_input": {"command": "gh pr create"}}

        with (
            patch(
                "pr_test_coverage_check.get_changed_files",
                return_value=[".claude/hooks/my-hook.py"],
            ),
            patch(
                "pr_test_coverage_check.get_hook_files_without_tests",
                return_value=[
                    (
                        ".claude/hooks/my-hook.py",
                        ".claude/hooks/tests/test_my_hook.py",
                    )
                ],
            ),
        ):
            result = self._run_main_with_input(input_data)

        assert result["decision"] == "approve"  # Warning only, no blocking
        assert "systemMessage" in result
        assert "テストファイル不足" in result["systemMessage"]

    def test_handles_exceptions_gracefully(self):
        """Should not block on errors - fail-open design."""
        input_data = {"tool_input": {"command": "gh pr create"}}

        with patch(
            "pr_test_coverage_check.get_changed_files",
            side_effect=Exception("Test error"),
        ):
            result = self._run_main_with_input(input_data)

        assert result["decision"] == "approve"
