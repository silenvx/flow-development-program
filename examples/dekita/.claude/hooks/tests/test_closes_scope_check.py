#!/usr/bin/env python3
"""Tests for closes-scope-check.py hook.

Issue #1986: Prevents premature Issue closure.
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

HOOK_PATH = Path(__file__).parent.parent / "closes-scope-check.py"


def load_module():
    """Load the hook module for testing."""
    parent_dir = str(Path(__file__).parent.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    spec = importlib.util.spec_from_file_location("closes_scope_check", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestExtractPrBodyFromCommand:
    """Tests for extracting PR body from command."""

    def test_extract_body_with_double_quotes(self):
        """Should extract body from --body with double quotes."""
        module = load_module()
        command = 'gh pr create --title "Test" --body "Closes #123"'
        result = module.extract_pr_body_from_command(command)
        assert result == "Closes #123"

    def test_extract_body_with_single_quotes(self):
        """Should extract body from --body with single quotes."""
        module = load_module()
        command = "gh pr create --title 'Test' --body 'Fixes #456'"
        result = module.extract_pr_body_from_command(command)
        assert result == "Fixes #456"

    def test_extract_body_with_short_flag(self):
        """Should extract body from -b flag."""
        module = load_module()
        command = 'gh pr create -t "Test" -b "Closes #789"'
        result = module.extract_pr_body_from_command(command)
        assert result == "Closes #789"

    def test_no_body_returns_none(self):
        """Should return None when no body is found."""
        module = load_module()
        command = 'gh pr create --title "Test"'
        result = module.extract_pr_body_from_command(command)
        assert result is None


class TestHasIssueReference:
    """Tests for detecting Issue references in text."""

    def test_hash_number(self):
        """Should detect #123 format."""
        module = load_module()
        assert module.has_issue_reference("→ #123 で対応")

    def test_issue_hash_number(self):
        """Should detect Issue #123 format."""
        module = load_module()
        assert module.has_issue_reference("Issue #456 で対応")

    def test_no_reference(self):
        """Should return False when no reference."""
        module = load_module()
        assert not module.has_issue_reference("→ スコープ外")

    def test_plain_text(self):
        """Should return False for plain text."""
        module = load_module()
        assert not module.has_issue_reference("P2項目の追跡フックを実装")


class TestIsPrCreateCommand:
    """Tests for detecting PR create commands."""

    def test_simple_pr_create(self):
        """Should detect simple gh pr create."""
        module = load_module()
        assert module.is_pr_create_command("gh pr create --title 'Test'")

    def test_pr_create_with_body(self):
        """Should detect gh pr create with body."""
        module = load_module()
        assert module.is_pr_create_command('gh pr create --body "Closes #123"')

    def test_not_pr_create(self):
        """Should not match other gh commands."""
        module = load_module()
        assert not module.is_pr_create_command("gh pr merge 123")
        assert not module.is_pr_create_command("gh pr view 123")

    def test_chained_command(self):
        """Should detect PR create in chained commands."""
        module = load_module()
        assert module.is_pr_create_command("cd repo && gh pr create --title 'Test'")


class TestCheckIssuesForIncompleteItems:
    """Tests for checking Issues for incomplete items.

    Uses fetch_issue_acceptance_criteria from issue_checker.py which returns
    3-element tuples: (is_completed, is_strikethrough, text).
    """

    def test_issue_with_incomplete_items(self):
        """Should detect issues with unchecked items lacking Issue refs."""
        module = load_module()

        # 3-element tuples: (is_completed, is_strikethrough, text)
        mock_criteria = [
            (True, False, "完了した項目"),
            (False, False, "未完了項目（リンクなし）"),
        ]

        with mock.patch.object(
            module,
            "fetch_issue_acceptance_criteria",
            return_value=(True, "Test Issue", mock_criteria),
        ):
            result = module.check_issues_for_incomplete_items(["123"])
            assert len(result) == 1
            assert result[0]["issue_number"] == "123"
            assert "未完了項目" in result[0]["unchecked_items"][0]

    def test_issue_with_properly_deferred_items(self):
        """Should not flag items with Issue references."""
        module = load_module()

        mock_criteria = [
            (True, False, "完了した項目"),
            (False, False, "P2対応 → #456 で対応"),  # Has Issue reference
        ]

        with mock.patch.object(
            module,
            "fetch_issue_acceptance_criteria",
            return_value=(True, "Test Issue", mock_criteria),
        ):
            result = module.check_issues_for_incomplete_items(["123"])
            assert len(result) == 0

    def test_issue_with_all_complete(self):
        """Should not flag issues with all items complete."""
        module = load_module()

        mock_criteria = [
            (True, False, "完了した項目1"),
            (True, False, "完了した項目2"),
        ]

        with mock.patch.object(
            module,
            "fetch_issue_acceptance_criteria",
            return_value=(True, "Test Issue", mock_criteria),
        ):
            result = module.check_issues_for_incomplete_items(["123"])
            assert len(result) == 0

    def test_issue_with_strikethrough_items(self):
        """Should treat strikethrough items as complete (Issue #823)."""
        module = load_module()

        # Strikethrough items are marked as is_completed=True by fetch_issue_acceptance_criteria
        mock_criteria = [
            (True, False, "完了した項目"),
            (True, True, "~~スコープ外~~ → #456"),  # is_completed=True due to strikethrough
        ]

        with mock.patch.object(
            module,
            "fetch_issue_acceptance_criteria",
            return_value=(True, "Test Issue", mock_criteria),
        ):
            result = module.check_issues_for_incomplete_items(["123"])
            assert len(result) == 0

    def test_issue_with_mixed_strikethrough_and_incomplete(self):
        """Should flag incomplete items but not strikethrough items."""
        module = load_module()

        mock_criteria = [
            (True, False, "完了した項目"),
            (True, True, "~~スコープ外~~"),  # strikethrough = complete
            (False, False, "未完了項目（リンクなし）"),  # incomplete without ref
        ]

        with mock.patch.object(
            module,
            "fetch_issue_acceptance_criteria",
            return_value=(True, "Test Issue", mock_criteria),
        ):
            result = module.check_issues_for_incomplete_items(["123"])
            assert len(result) == 1
            assert "未完了項目" in result[0]["unchecked_items"][0]


class TestFormatBlockMessage:
    """Tests for block message formatting."""

    def test_format_single_issue(self):
        """Should format message for single issue."""
        module = load_module()
        issues = [
            {
                "issue_number": "123",
                "title": "Test Issue",
                "unchecked_items": ["未完了項目"],
                "total_unchecked": 1,
            }
        ]
        result = module.format_block_message(issues)
        assert "Issue #123" in result
        assert "Test Issue" in result
        assert "未完了項目" in result
        assert "対処方法" in result


class TestMainFunction:
    """Tests for main function."""

    def test_skip_non_pr_create(self):
        """Should skip non-PR create commands."""
        module = load_module()

        mock_input = {"tool_input": {"command": "gh pr merge 123"}}

        with mock.patch.object(module, "parse_hook_input", return_value=mock_input):
            with mock.patch.object(module, "log_hook_execution"):
                # Should exit(0) for skip
                with mock.patch("sys.exit") as mock_exit:
                    module.main()
                    mock_exit.assert_called_with(0)

    def test_block_on_incomplete_items(self):
        """Should block when Issues have incomplete items."""
        module = load_module()

        mock_input = {"tool_input": {"command": 'gh pr create --body "Closes #123"'}}
        mock_issues = [
            {
                "issue_number": "123",
                "title": "Test",
                "unchecked_items": ["未完了"],
                "total_unchecked": 1,
            }
        ]

        captured_output = []

        def capture_print(msg, **kwargs):
            # Ignore file=sys.stderr from make_block_result
            if "file" not in kwargs:
                captured_output.append(msg)

        with mock.patch.object(module, "parse_hook_input", return_value=mock_input):
            with mock.patch.object(
                module, "check_issues_for_incomplete_items", return_value=mock_issues
            ):
                with mock.patch.object(module, "log_hook_execution"):
                    with mock.patch("builtins.print", side_effect=capture_print):
                        module.main()

        assert len(captured_output) == 1
        result = json.loads(captured_output[0])
        assert result.get("decision") == "block"
