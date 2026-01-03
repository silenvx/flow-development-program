#!/usr/bin/env python3
"""Tests for bug-issue-creation-guard.py hook."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "bug-issue-creation-guard.py"


def load_hook_module():
    """Load the hook module for testing."""
    spec = importlib.util.spec_from_file_location("hook", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_hook(input_data: dict, env: dict = None) -> tuple[int, str, str]:
    """Run the hook with given input and return (exit_code, stdout, stderr)."""
    process_env = os.environ.copy()
    if env:
        process_env.update(env)

    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        env=process_env,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


class TestExtractIssueTitle:
    """Tests for extract_issue_title function."""

    def setup_method(self):
        """Load the module once per test method."""
        self.module = load_hook_module()

    def test_double_quoted_title(self):
        """Should extract title from --title with double quotes."""
        result = self.module.extract_issue_title('gh issue create --title "fix: bug found"')
        assert result == "fix: bug found"

    def test_single_quoted_title(self):
        """Should extract title from --title with single quotes."""
        result = self.module.extract_issue_title("gh issue create --title 'bug: some issue'")
        assert result == "bug: some issue"

    def test_short_flag(self):
        """Should extract title from -t flag."""
        result = self.module.extract_issue_title('gh issue create -t "fix: problem"')
        assert result == "fix: problem"

    def test_unquoted_title(self):
        """Should extract unquoted single-word title."""
        result = self.module.extract_issue_title("gh issue create --title bugfix")
        assert result == "bugfix"

    def test_no_title(self):
        """Should return None when no title is found."""
        result = self.module.extract_issue_title("gh issue create --body 'some body'")
        assert result is None


class TestIsPrScopeIssue:
    """Tests for is_pr_scope_issue function."""

    def setup_method(self):
        """Load the module once per test method."""
        self.module = load_hook_module()

    def test_fix_prefix(self):
        """Should detect fix: prefix."""
        assert self.module.is_pr_scope_issue("fix: something broken") is True
        assert self.module.is_pr_scope_issue("fix(scope): something") is True

    def test_bug_prefix(self):
        """Should detect bug: prefix."""
        assert self.module.is_pr_scope_issue("bug: found an issue") is True
        assert self.module.is_pr_scope_issue("bug(auth): login fails") is True

    def test_japanese_bug_keywords(self):
        """Should detect Japanese bug-related keywords."""
        assert self.module.is_pr_scope_issue("バグを発見") is True
        assert self.module.is_pr_scope_issue("修正が必要") is True
        assert self.module.is_pr_scope_issue("不具合の報告") is True

    def test_test_related_patterns(self):
        """Should detect test-related patterns (Issue #1175 case)."""
        # English patterns (singular and plural)
        assert self.module.is_pr_scope_issue("test: add unit tests") is True
        assert self.module.is_pr_scope_issue("tests: add missing coverage") is True
        assert self.module.is_pr_scope_issue("test(hooks): coverage") is True
        assert self.module.is_pr_scope_issue("tests(api): integration") is True
        assert self.module.is_pr_scope_issue("Improve test coverage") is True
        # Japanese patterns
        assert self.module.is_pr_scope_issue("テスト追加") is True
        assert self.module.is_pr_scope_issue("API操作ログ機能のテスト追加") is True
        assert self.module.is_pr_scope_issue("テスト不足の解消") is True
        assert self.module.is_pr_scope_issue("テストカバレッジ向上") is True

    def test_edge_case_patterns(self):
        """Should detect edge case patterns."""
        assert self.module.is_pr_scope_issue("エッジケース対応") is True
        assert self.module.is_pr_scope_issue("Handle edge case in parser") is True
        assert self.module.is_pr_scope_issue("edge case: empty input") is True

    def test_non_pr_scope_title(self):
        """Should not detect non-PR-scope titles."""
        assert self.module.is_pr_scope_issue("feat: add new feature") is False
        assert self.module.is_pr_scope_issue("docs: update readme") is False
        assert self.module.is_pr_scope_issue("refactor: cleanup code") is False
        assert self.module.is_pr_scope_issue("chore: update deps") is False


class TestBugIssueCreationGuard:
    """Integration tests for bug-issue-creation-guard hook."""

    def test_exit_zero_for_non_block_cases(self):
        """Hook should exit with code 0 for non-block cases."""
        # These are cases that should not trigger a block:
        # - Non-issue commands
        # - Non-PR-scope issues (feat:, docs:, etc.)
        # - Empty inputs
        test_cases = [
            {"tool_input": {"command": "ls -la"}},
            {"tool_input": {"command": 'gh issue create --title "feat: new feature"'}},
            {"tool_input": {}},
            {},
        ]

        for input_data in test_cases:
            exit_code, _, _ = run_hook(input_data)
            assert exit_code == 0

    def test_approve_non_issue_command(self):
        """Should approve non-issue create commands (silent)."""
        exit_code, stdout, _ = run_hook({"tool_input": {"command": "ls -la"}})

        assert exit_code == 0
        # Non-target commands exit silently (no output)
        assert stdout.strip() == ""

    def test_approve_non_pr_scope_issue(self):
        """Should approve non-PR-scope issue creation (silent)."""
        exit_code, stdout, _ = run_hook(
            {"tool_input": {"command": 'gh issue create --title "feat: new feature"'}}
        )

        assert exit_code == 0
        # Non-PR-scope issues exit silently (no output)
        assert stdout.strip() == ""

    def test_approve_when_no_title(self):
        """Should approve when no title can be extracted (silent)."""
        exit_code, stdout, _ = run_hook(
            {"tool_input": {"command": "gh issue create --body 'some body'"}}
        )

        assert exit_code == 0
        # No title found exits silently (no output)
        assert stdout.strip() == ""

    def test_block_when_bug_issue_and_pr_open(self):
        """Should block when creating bug issue while PR is open (Issue #2240)."""
        # This test needs to mock subprocess calls to simulate:
        # 1. Being on a feature branch
        # 2. Having an open PR for that branch

        with patch("subprocess.run") as mock_run:
            # First call: git branch --show-current
            mock_branch = MagicMock()
            mock_branch.returncode = 0
            mock_branch.stdout = "feature-branch\n"

            # Second call: gh pr list
            mock_pr = MagicMock()
            mock_pr.returncode = 0
            mock_pr.stdout = json.dumps(
                [{"number": 123, "title": "Test PR", "headRefName": "feature-branch"}]
            )

            mock_run.side_effect = [mock_branch, mock_pr]

            # Import and run the hook functions directly
            module = load_hook_module()

            # Test get_current_pr returns PR info
            pr_info = module.get_current_pr()
            assert pr_info is not None
            assert pr_info["number"] == 123

    def test_approve_when_no_open_pr(self):
        """Should approve when no open PR exists for the branch."""
        with patch("subprocess.run") as mock_run:
            # First call: git branch --show-current
            mock_branch = MagicMock()
            mock_branch.returncode = 0
            mock_branch.stdout = "feature-branch\n"

            # Second call: gh pr list returns empty
            mock_pr = MagicMock()
            mock_pr.returncode = 0
            mock_pr.stdout = "[]"

            mock_run.side_effect = [mock_branch, mock_pr]

            module = load_hook_module()
            pr_info = module.get_current_pr()
            assert pr_info is None

    def test_approve_when_on_main_branch(self):
        """Should approve when on main branch (no PR warning needed)."""
        with patch("subprocess.run") as mock_run:
            mock_branch = MagicMock()
            mock_branch.returncode = 0
            mock_branch.stdout = "main\n"

            mock_run.return_value = mock_branch

            module = load_hook_module()
            pr_info = module.get_current_pr()
            assert pr_info is None

    def test_handle_subprocess_error(self):
        """Should return None when subprocess fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.SubprocessError("Command failed")

            module = load_hook_module()
            pr_info = module.get_current_pr()
            assert pr_info is None
