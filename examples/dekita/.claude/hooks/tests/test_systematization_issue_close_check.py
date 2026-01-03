#!/usr/bin/env python3
"""Tests for systematization-issue-close-check.py hook."""

import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import hook module for unit tests
sys.path.insert(0, str(Path(__file__).parent.parent))

from importlib import import_module

hook_module = import_module("systematization-issue-close-check")

HOOK_PATH = Path(__file__).parent.parent / "systematization-issue-close-check.py"


class TestExtractIssueNumber:
    """Tests for extract_issue_number function."""

    def test_basic_close(self) -> None:
        """Test basic gh issue close command."""
        assert hook_module.extract_issue_number("gh issue close 123") == "123"

    def test_with_hash(self) -> None:
        """Test with # prefix."""
        assert hook_module.extract_issue_number("gh issue close #456") == "456"

    def test_with_options(self) -> None:
        """Test with options."""
        assert hook_module.extract_issue_number("gh issue close 789 --reason completed") == "789"

    def test_not_close_command(self) -> None:
        """Test non-close command."""
        assert hook_module.extract_issue_number("gh issue view 123") is None

    def test_no_issue_number(self) -> None:
        """Test command without issue number."""
        assert hook_module.extract_issue_number("gh issue close") is None


class TestHasSystematizationKeyword:
    """Tests for has_systematization_keyword function."""

    def test_shikumika(self) -> None:
        """Test 仕組み化 keyword."""
        assert hook_module.has_systematization_keyword("仕組み化を実装", "")
        assert hook_module.has_systematization_keyword("", "仕組み化する")

    def test_hook_creation(self) -> None:
        """Test hook creation keywords."""
        assert hook_module.has_systematization_keyword("フックを作成", "")
        assert hook_module.has_systematization_keyword("hook追加", "")
        assert hook_module.has_systematization_keyword("hookを実装", "")

    def test_ci_addition(self) -> None:
        """Test CI addition keywords."""
        assert hook_module.has_systematization_keyword("CIを追加", "")
        assert hook_module.has_systematization_keyword("CI実装", "")

    def test_automation(self) -> None:
        """Test automation keywords."""
        assert hook_module.has_systematization_keyword("自動化する", "")
        assert hook_module.has_systematization_keyword("自動チェック", "")

    def test_enforcement(self) -> None:
        """Test enforcement keywords."""
        assert hook_module.has_systematization_keyword("強制機構", "")
        assert hook_module.has_systematization_keyword("強制チェック", "")

    def test_prevention(self) -> None:
        """Test prevention keywords."""
        assert hook_module.has_systematization_keyword("再発防止の仕組み", "")
        assert hook_module.has_systematization_keyword("再発防止フック", "")

    def test_no_keyword(self) -> None:
        """Test without keywords."""
        assert not hook_module.has_systematization_keyword("バグ修正", "テストを追加")
        assert not hook_module.has_systematization_keyword("ドキュメント更新", "README追加")


class TestDocsPrefix:
    """Tests for docs prefix exclusion."""

    def test_docs_colon_prefix_matches(self) -> None:
        """Test docs: prefix is detected correctly."""
        pattern = r"^docs[:\(]"
        assert re.match(pattern, "docs: update readme", re.IGNORECASE)
        assert re.match(pattern, "docs(hooks): add reference", re.IGNORECASE)
        assert re.match(pattern, "docs(agents): フック作成のルール", re.IGNORECASE)
        assert re.match(pattern, "DOCS: uppercase prefix", re.IGNORECASE)

    def test_non_docs_prefix_not_matched(self) -> None:
        """Test non-docs prefixes are not matched."""
        pattern = r"^docs[:\(]"
        assert not re.match(pattern, "feat: add hook", re.IGNORECASE)
        assert not re.match(pattern, "fix(hooks): fix issue", re.IGNORECASE)
        assert not re.match(pattern, "document this", re.IGNORECASE)
        assert not re.match(pattern, "仕組み化を実装", re.IGNORECASE)

    def test_docs_prefix_with_keyword_in_body(self) -> None:
        """Test docs prefix excludes even if body contains systematization keywords."""
        # Issue #2444 case: docs prefix with "フックを追加" in body
        title = "docs(hooks-reference): block-improvement-reminderの記載追加"
        body = "Issue #2432 でblock-improvement-reminderフックを追加したが..."

        # Should match docs prefix
        assert re.match(r"^docs[:\(]", title, re.IGNORECASE)

        # Body does contain keyword (but should be skipped due to title)
        assert hook_module.has_systematization_keyword(title, body)


class TestHasEnforcementFile:
    """Tests for has_enforcement_file function."""

    def test_hook_file(self) -> None:
        """Test hook file detection."""
        files = [".claude/hooks/my-hook.py", "README.md"]
        result = hook_module.has_enforcement_file(files)
        assert ".claude/hooks/my-hook.py" in result

    def test_workflow_file(self) -> None:
        """Test workflow file detection."""
        files = [".github/workflows/check.yml", "src/main.ts"]
        result = hook_module.has_enforcement_file(files)
        assert ".github/workflows/check.yml" in result

    def test_script_file(self) -> None:
        """Test script file detection."""
        files = [".claude/scripts/check.sh", ".claude/scripts/validate.py"]
        result = hook_module.has_enforcement_file(files)
        assert len(result) == 2

    def test_no_enforcement_file(self) -> None:
        """Test without enforcement files."""
        files = ["AGENTS.md", "README.md", "src/index.ts"]
        result = hook_module.has_enforcement_file(files)
        assert result == []


class TestGetPrFiles:
    """Tests for get_pr_files function."""

    @patch("subprocess.run")
    def test_get_files_success(self, mock_run: MagicMock) -> None:
        """Test successful retrieval of PR files."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=".claude/hooks/my-hook.py\nREADME.md\n",
        )
        result = hook_module.get_pr_files(123)
        assert result == [".claude/hooks/my-hook.py", "README.md"]
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert "123" in call_args[0][0]  # PR number in command

    @patch("subprocess.run")
    def test_get_files_empty(self, mock_run: MagicMock) -> None:
        """Test PR with no files."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
        )
        result = hook_module.get_pr_files(456)
        assert result == []

    @patch("subprocess.run")
    def test_get_files_failure(self, mock_run: MagicMock) -> None:
        """Test gh command failure."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error",
        )
        result = hook_module.get_pr_files(789)
        assert result == []

    @patch("subprocess.run")
    def test_get_files_timeout(self, mock_run: MagicMock) -> None:
        """Test timeout handling."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = hook_module.get_pr_files(111)
        assert result == []


class TestSearchPrsByIssue:
    """Tests for search_prs_by_issue function."""

    @patch("subprocess.run")
    def test_search_finds_prs(self, mock_run: MagicMock) -> None:
        """Test finding PRs by issue number."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="100\n101\n102\n",
        )
        result = hook_module.search_prs_by_issue("123")
        assert result == [100, 101, 102]
        # Check that --state all is used
        call_args = mock_run.call_args[0][0]
        assert "--state" in call_args
        assert "all" in call_args

    @patch("subprocess.run")
    def test_search_no_results(self, mock_run: MagicMock) -> None:
        """Test no PRs found."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
        )
        result = hook_module.search_prs_by_issue("999")
        assert result == []

    @patch("subprocess.run")
    def test_search_failure(self, mock_run: MagicMock) -> None:
        """Test gh command failure."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
        )
        result = hook_module.search_prs_by_issue("123")
        assert result == []

    @patch("subprocess.run")
    def test_search_timeout(self, mock_run: MagicMock) -> None:
        """Test timeout handling."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = hook_module.search_prs_by_issue("123")
        assert result == []

    @patch("subprocess.run")
    def test_search_filters_non_numeric(self, mock_run: MagicMock) -> None:
        """Test that non-numeric lines are ignored."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="100\nabc\n101\n",
        )
        result = hook_module.search_prs_by_issue("123")
        assert result == [100, 101]


class TestGetLinkedPrFilesWithFallback:
    """Tests for get_linked_pr_files with fallback to search."""

    @patch.object(hook_module, "get_pr_files")
    @patch.object(hook_module, "search_prs_by_issue")
    @patch("subprocess.run")
    def test_fallback_to_search(
        self,
        mock_run: MagicMock,
        mock_search: MagicMock,
        mock_get_files: MagicMock,
    ) -> None:
        """Test fallback to PR search when linkedPullRequests is empty."""
        # linkedPullRequests returns empty
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"linkedPullRequests": []}),
        )
        # Fallback search finds PRs
        mock_search.return_value = [100, 101]
        mock_get_files.side_effect = [
            [".claude/hooks/hook1.py"],
            ["README.md"],
        ]

        result = hook_module.get_linked_pr_files("123")
        assert ".claude/hooks/hook1.py" in result
        assert "README.md" in result
        mock_search.assert_called_once_with("123")


class TestHookIntegration:
    """Integration tests for the hook."""

    def test_not_bash_tool(self) -> None:
        """Test hook ignores non-Bash tools."""
        hook_input = json.dumps({"tool_name": "Write", "tool_input": {}})
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input=hook_input,
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = json.loads(result.stdout)
        assert output.get("decision") == "approve" or output.get("continue") is True

    def test_not_issue_close(self) -> None:
        """Test hook ignores non-close commands."""
        hook_input = json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "gh issue view 123"},
            }
        )
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input=hook_input,
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = json.loads(result.stdout)
        assert output.get("decision") == "approve" or output.get("continue") is True

    def test_skip_env_variable(self) -> None:
        """Test skip with inline environment variable."""
        hook_input = json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "SKIP_SYSTEMATIZATION_CHECK=1 gh issue close 123"},
            }
        )
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input=hook_input,
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = json.loads(result.stdout)
        assert output.get("decision") == "approve"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
