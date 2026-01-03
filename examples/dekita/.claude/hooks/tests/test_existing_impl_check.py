#!/usr/bin/env python3
"""Tests for existing-impl-check.py hook."""

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HOOK_PATH = Path(__file__).parent.parent / "existing-impl-check.py"


def load_module():
    """Load the hook module for testing."""
    # Temporarily add hooks directory to path for common module import
    hooks_dir = str(HOOK_PATH.parent)
    sys.path.insert(0, hooks_dir)
    try:
        spec = importlib.util.spec_from_file_location("existing_impl_check", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        # Clean up path
        if hooks_dir in sys.path:
            sys.path.remove(hooks_dir)


class TestExtractIssueNumberFromCommand:
    """Tests for extract_issue_number_from_command function."""

    def setup_method(self):
        self.module = load_module()

    def test_extract_from_path_issue_prefix(self):
        """Should extract issue number from path with issue- prefix."""
        cmd = "git worktree add .worktrees/issue-454 -b feat/worktree-auto-assign main"
        result = self.module.extract_issue_number_from_command(cmd)
        assert result == 454

    def test_extract_from_branch_name(self):
        """Should extract issue number from branch name."""
        cmd = "git worktree add .worktrees/foo -b feat/issue-123-description main"
        result = self.module.extract_issue_number_from_command(cmd)
        assert result == 123

    def test_extract_from_hash_prefix(self):
        """Should extract issue number from #123 pattern."""
        cmd = "git worktree add .worktrees/foo -b feat/#456-fix main"
        result = self.module.extract_issue_number_from_command(cmd)
        assert result == 456

    def test_no_issue_number(self):
        """Should return None when no issue number in command."""
        cmd = "git worktree add .worktrees/feature -b feat/new-feature main"
        result = self.module.extract_issue_number_from_command(cmd)
        assert result is None

    def test_not_worktree_command(self):
        """Should return None for non-worktree commands."""
        cmd = "git status"
        result = self.module.extract_issue_number_from_command(cmd)
        assert result is None


class TestExtractKeywordsFromTitle:
    """Tests for extract_keywords_from_title function."""

    def setup_method(self):
        self.module = load_module()

    def test_extract_scope(self):
        """Should extract scope from feat(scope) pattern."""
        title = "feat(hooks): worktree作成時にIssue自動アサイン"
        keywords = self.module.extract_keywords_from_title(title)
        assert "hooks" in keywords

    def test_extract_hyphenated(self):
        """Should extract hyphenated words."""
        title = "Add auto-assign functionality"
        keywords = self.module.extract_keywords_from_title(title)
        assert "auto-assign" in keywords

    def test_extract_japanese_keywords(self):
        """Should map Japanese keywords to English."""
        title = "自動アサイン機能を追加"
        keywords = self.module.extract_keywords_from_title(title)
        assert "auto" in keywords
        assert "assign" in keywords

    def test_extract_review_keyword(self):
        """Should extract review-related keywords."""
        title = "レビューコメントの自動チェック"
        keywords = self.module.extract_keywords_from_title(title)
        assert "review" in keywords
        assert "auto" in keywords
        assert "check" in keywords


class TestSearchRelatedCode:
    """Tests for search_related_code function."""

    def setup_method(self):
        self.module = load_module()

    def test_find_by_issue_number(self):
        """Should find files mentioning issue number."""
        with patch.object(
            self.module.subprocess,
            "run",
            return_value=MagicMock(
                returncode=0,
                stdout=".claude/hooks/issue-auto-assign.py\n",
            ),
        ):
            result = self.module.search_related_code(454, None)
            assert ".claude/hooks/issue-auto-assign.py" in result

    def test_find_by_keyword(self):
        """Should find files by keyword from title."""

        def mock_subprocess(cmd, **kwargs):
            # cmd is a list like ["git", "grep", "-l", "#999"]
            if "grep" in cmd:
                return MagicMock(returncode=1, stdout="")
            if "ls-files" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=".claude/hooks/auto-assign.py\n",
                )
            return MagicMock(returncode=1, stdout="")

        with patch.object(self.module.subprocess, "run", side_effect=mock_subprocess):
            result = self.module.search_related_code(999, "feat(hooks): auto-assign implementation")
            assert any("auto-assign" in f for f in result)

    def test_deduplicate_results(self):
        """Should deduplicate file results."""
        with patch.object(
            self.module.subprocess,
            "run",
            return_value=MagicMock(
                returncode=0,
                stdout="file.py\nfile.py\nfile.py\n",
            ),
        ):
            result = self.module.search_related_code(123, None)
            assert result.count("file.py") == 1

    def test_limit_results(self):
        """Should limit results to 5 files."""
        files = "\n".join([f"file{i}.py" for i in range(10)])
        with patch.object(
            self.module.subprocess,
            "run",
            return_value=MagicMock(returncode=0, stdout=files),
        ):
            result = self.module.search_related_code(123, None)
            assert len(result) <= 5


class TestMain:
    """Tests for main function."""

    def setup_method(self):
        self.module = load_module()

    def test_warn_when_related_code_exists(self):
        """Should warn when related code exists."""

        def mock_subprocess(cmd, **kwargs):
            if "issue" in cmd and "view" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout='{"title": "feat(hooks): auto-assign test"}',
                )
            if "grep" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=".claude/hooks/auto-assign.py\n",
                )
            return MagicMock(returncode=1, stdout="")

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .worktrees/issue-123 -b feat/test main"},
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
        assert "systemMessage" in result
        assert "既存実装の検証が必要" in result["systemMessage"]

    def test_no_warn_when_no_related_code(self):
        """Should not warn when no related code exists."""

        def mock_subprocess(cmd, **kwargs):
            if "issue" in cmd and "view" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout='{"title": "New unique feature"}',
                )
            return MagicMock(returncode=1, stdout="")

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .worktrees/issue-999 -b feat/new main"},
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

    def test_ignore_non_worktree_commands(self):
        """Should ignore non-worktree commands."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
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
