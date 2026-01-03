#!/usr/bin/env python3
"""Unit tests for e2e-test-check.py"""

import importlib.util
import sys
from pathlib import Path

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# e2e-test-check.py has hyphens, so we need dynamic import
HOOK_PATH = Path(__file__).parent.parent / "e2e-test-check.py"
_spec = importlib.util.spec_from_file_location("e2e_test_check", HOOK_PATH)
e2e_test_check = importlib.util.module_from_spec(_spec)
sys.modules["e2e_test_check"] = e2e_test_check
_spec.loader.exec_module(e2e_test_check)

is_git_push_command = e2e_test_check.is_git_push_command
has_e2e_test_changes = e2e_test_check.has_e2e_test_changes
get_changed_e2e_files = e2e_test_check.get_changed_e2e_files


class TestIsGitPushCommand:
    """Tests for is_git_push_command function."""

    def test_detects_git_push(self):
        """Should detect git push command."""
        assert is_git_push_command("git push")
        assert is_git_push_command("git push origin main")
        assert is_git_push_command("git push -u origin feature")

    def test_ignores_help(self):
        """Should ignore git push --help."""
        assert not is_git_push_command("git push --help")

    def test_ignores_other_git_commands(self):
        """Should not flag other git commands."""
        assert not is_git_push_command("git pull")
        assert not is_git_push_command("git status")
        assert not is_git_push_command("git commit -m 'test'")

    def test_ignores_empty(self):
        """Should not flag empty commands."""
        assert not is_git_push_command("")
        assert not is_git_push_command("   ")


class TestHasE2eTestChanges:
    """Tests for has_e2e_test_changes function."""

    def test_detects_e2e_test_files(self):
        """Should detect E2E test file changes."""
        assert has_e2e_test_changes(["tests/example.spec.ts"])
        assert has_e2e_test_changes(["tests/stories/workshop.spec.ts"])

    def test_ignores_non_test_files(self):
        """Should not flag non-test files."""
        assert not has_e2e_test_changes(["src/app.ts"])
        assert not has_e2e_test_changes(["tests/README.md"])
        assert not has_e2e_test_changes([])


class TestGetChangedE2eFiles:
    """Tests for get_changed_e2e_files function."""

    def test_filters_e2e_files(self):
        """Should return only E2E test files."""
        files = ["src/app.ts", "tests/example.spec.ts", "README.md"]
        result = get_changed_e2e_files(files)
        assert result == ["tests/example.spec.ts"]

    def test_returns_empty_for_no_tests(self):
        """Should return empty list when no test files."""
        files = ["src/app.ts", "README.md"]
        result = get_changed_e2e_files(files)
        assert result == []
