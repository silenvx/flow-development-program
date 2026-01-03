#!/usr/bin/env python3
"""Tests for pr-body-quality-check.py hook."""

import json
import subprocess

from conftest import HOOKS_DIR, load_hook_module

HOOK_PATH = HOOKS_DIR / "pr-body-quality-check.py"


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestHasWhySection:
    """Tests for has_why_section function."""

    def setup_method(self):
        self.module = load_hook_module("pr-body-quality-check")

    def test_markdown_headers(self):
        """Test detection of markdown headers."""
        test_cases = [
            ("## Why\nSome content", True),
            ("## なぜ\n説明", True),
            ("## 背景\n背景説明", True),
            ("## 理由\n理由説明", True),
            ("## Motivation\nSome motivation", True),
            ("## Background\nBackground info", True),
            ("# Why\nSingle hash header", True),
        ]
        for body, expected in test_cases:
            with self.subTest(body=body[:30]):
                result = self.module.has_why_section(body)
                assert result == expected

    def test_bold_headers(self):
        """Test detection of bold headers."""
        test_cases = [
            ("**Why**\nSome content", True),
            ("**なぜ**\n説明", True),
            ("**背景**\n背景説明", True),
            ("**理由**\n理由説明", True),
        ]
        for body, expected in test_cases:
            with self.subTest(body=body[:30]):
                result = self.module.has_why_section(body)
                assert result == expected

    def test_colon_format(self):
        """Test detection of colon format headers."""
        test_cases = [
            ("Why: Some reason", True),
            ("なぜ: 理由", True),
            ("背景: 背景説明", True),
            ("理由: 理由説明", True),
            ("背景：全角コロン", True),
        ]
        for body, expected in test_cases:
            with self.subTest(body=body[:30]):
                result = self.module.has_why_section(body)
                assert result == expected

    def test_no_why_section(self):
        """Test when no why section exists."""
        test_cases = [
            "## Summary\nJust a summary",
            "Some description without any headers",
            "## What\nThis is what was done",
        ]
        for body in test_cases:
            with self.subTest(body=body[:30]):
                result = self.module.has_why_section(body)
                assert not result

    def test_empty_body(self):
        """Test empty or None body."""
        assert not self.module.has_why_section("")
        assert not self.module.has_why_section(None)

    def test_case_insensitive(self):
        """Test case insensitivity."""
        test_cases = [
            ("## WHY\nContent", True),
            ("## why\nContent", True),
            ("**WHY**\nContent", True),
        ]
        for body, expected in test_cases:
            with self.subTest(body=body[:30]):
                result = self.module.has_why_section(body)
                assert result == expected


class TestHasReference:
    """Tests for has_reference function."""

    def setup_method(self):
        self.module = load_hook_module("pr-body-quality-check")

    def test_issue_number(self):
        """Test detection of Issue numbers."""
        test_cases = [
            ("#123", True),
            ("Closes #456", True),
            ("Fixes #789", True),
            ("Related to #100", True),
        ]
        for body, expected in test_cases:
            with self.subTest(body=body):
                result = self.module.has_reference(body)
                assert result == expected

    def test_github_url(self):
        """Test detection of GitHub URLs."""
        test_cases = [
            ("https://github.com/owner/repo/issues/123", True),
            ("https://github.com/owner/repo/pull/456", True),
            ("See: github.com/foo/bar/issues/789", True),
        ]
        for body, expected in test_cases:
            with self.subTest(body=body[:40]):
                result = self.module.has_reference(body)
                assert result == expected

    def test_reference_section(self):
        """Test detection of reference sections."""
        test_cases = [
            ("## Refs\nSome references", True),
            ("## References\nDoc links", True),
            ("## 参照\nリンク集", True),
            ("## 関連\n関連ドキュメント", True),
            ("Refs: See documentation", True),
            ("参照: ドキュメント", True),
        ]
        for body, expected in test_cases:
            with self.subTest(body=body[:30]):
                result = self.module.has_reference(body)
                assert result == expected

    def test_no_reference(self):
        """Test when no reference exists."""
        test_cases = [
            "Just a description without any reference",
            "## Summary\nNo Issue number here",
            "Some changes were made",
        ]
        for body in test_cases:
            with self.subTest(body=body[:30]):
                result = self.module.has_reference(body)
                assert not result

    def test_empty_body(self):
        """Test empty or None body."""
        assert not self.module.has_reference("")
        assert not self.module.has_reference(None)


class TestCheckBodyQuality:
    """Tests for check_body_quality function."""

    def setup_method(self):
        self.module = load_hook_module("pr-body-quality-check")

    def test_valid_body(self):
        """Test body with all required sections."""
        body = """## なぜ
バグ修正が必要

## 何を
修正内容

Closes #123
"""
        is_valid, missing = self.module.check_body_quality(body)
        assert is_valid
        assert len(missing) == 0

    def test_missing_why(self):
        """Test body missing why section."""
        body = """## Summary
Just a summary

Closes #123
"""
        is_valid, missing = self.module.check_body_quality(body)
        assert not is_valid
        assert "「なぜ」セクション" in missing[0]

    def test_missing_reference(self):
        """Test body missing reference."""
        body = """## なぜ
理由説明

## 何を
変更内容
"""
        is_valid, missing = self.module.check_body_quality(body)
        assert not is_valid
        assert "参照" in missing[0]

    def test_missing_both(self):
        """Test body missing both sections."""
        body = "Just a simple description"
        is_valid, missing = self.module.check_body_quality(body)
        assert not is_valid
        assert len(missing) == 2


class TestIsGhPrCreateCommand:
    """Tests for is_gh_pr_create_command function."""

    def setup_method(self):
        self.module = load_hook_module("pr-body-quality-check")

    def test_basic_command(self):
        """Should detect basic gh pr create commands."""
        assert self.module.is_gh_pr_create_command("gh pr create")
        assert self.module.is_gh_pr_create_command("gh pr create --title 'test'")
        assert self.module.is_gh_pr_create_command("gh  pr  create")

    def test_exclude_quoted(self):
        """Should not detect commands inside quotes."""
        assert not self.module.is_gh_pr_create_command("echo 'gh pr create'")
        assert not self.module.is_gh_pr_create_command('echo "gh pr create"')

    def test_empty(self):
        """Should return False for empty commands."""
        assert not self.module.is_gh_pr_create_command("")
        assert not self.module.is_gh_pr_create_command("   ")


class TestIsGhPrMergeCommand:
    """Tests for is_gh_pr_merge_command function."""

    def setup_method(self):
        self.module = load_hook_module("pr-body-quality-check")

    def test_basic_command(self):
        """Should detect basic gh pr merge commands."""
        assert self.module.is_gh_pr_merge_command("gh pr merge")
        assert self.module.is_gh_pr_merge_command("gh pr merge 123")
        assert self.module.is_gh_pr_merge_command("gh pr merge --squash")
        assert self.module.is_gh_pr_merge_command("gh  pr  merge  123")

    def test_exclude_quoted(self):
        """Should not detect commands inside quotes."""
        assert not self.module.is_gh_pr_merge_command("echo 'gh pr merge'")
        assert not self.module.is_gh_pr_merge_command('echo "gh pr merge"')

    def test_empty(self):
        """Should return False for empty commands."""
        assert not self.module.is_gh_pr_merge_command("")
        assert not self.module.is_gh_pr_merge_command("   ")


class TestExtractPrNumberFromMerge:
    """Tests for extract_pr_number_from_merge function."""

    def setup_method(self):
        self.module = load_hook_module("pr-body-quality-check")

    def test_explicit_number(self):
        """Should extract explicit PR number."""
        assert self.module.extract_pr_number_from_merge("gh pr merge 123") == "123"
        assert self.module.extract_pr_number_from_merge("gh pr merge #456") == "456"
        assert self.module.extract_pr_number_from_merge("gh pr merge 789 --squash") == "789"

    def test_no_number(self):
        """Should return None when no explicit number."""
        assert self.module.extract_pr_number_from_merge("gh pr merge") is None
        assert self.module.extract_pr_number_from_merge("gh pr merge --squash") is None


class TestExtractPrBody:
    """Tests for extract_pr_body function."""

    def setup_method(self):
        self.module = load_hook_module("pr-body-quality-check")

    def test_body_double_quotes(self):
        """Test extracting body with double quotes."""
        command = 'gh pr create --title "test" --body "## なぜ\nReason\n\nCloses #123"'
        result = self.module.extract_pr_body(command)
        assert result is not None
        assert "なぜ" in result
        assert "#123" in result

    def test_body_single_quotes(self):
        """Test extracting body with single quotes."""
        command = "gh pr create --title 'test' --body '## Why\nReason\n\nCloses #123'"
        result = self.module.extract_pr_body(command)
        assert result is not None
        assert "Why" in result

    def test_body_short_flag(self):
        """Test extracting body with -b flag."""
        command = 'gh pr create --title "test" -b "## なぜ\nReason\n\nCloses #123"'
        result = self.module.extract_pr_body(command)
        assert result is not None

    def test_no_body(self):
        """Test when no body is specified."""
        command = 'gh pr create --title "test"'
        result = self.module.extract_pr_body(command)
        assert result is None


class TestHookIntegration:
    """Integration tests for the hook."""

    def test_approve_non_pr_commands(self):
        """Should approve commands that are not gh pr create/merge."""
        test_cases = [
            "ls -la",
            "git status",
            "gh pr view 123",
            "echo 'gh pr create'",
        ]
        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "approve"

    def test_approve_empty_command(self):
        """Should approve when command is empty."""
        result = run_hook({"tool_input": {"command": ""}})
        assert result["decision"] == "approve"

    def test_block_pr_create_missing_why(self):
        """Should block pr create with missing why section."""
        command = 'gh pr create --title "test" --body "## Summary\nJust summary\n\nCloses #123"'
        result = run_hook({"tool_input": {"command": command}})
        assert result["decision"] == "block"
        assert "なぜ" in result.get("reason", "")

    def test_block_pr_create_missing_reference(self):
        """Should block pr create with missing reference."""
        command = 'gh pr create --title "test" --body "## なぜ\nSome reason\n\n## 何を\nChanges"'
        result = run_hook({"tool_input": {"command": command}})
        assert result["decision"] == "block"
        assert "参照" in result.get("reason", "")

    def test_approve_pr_create_valid_body(self):
        """Should approve pr create with valid body."""
        command = 'gh pr create --title "test" --body "## なぜ\nSome reason\n\nCloses #123"'
        result = run_hook({"tool_input": {"command": command}})
        assert result["decision"] == "approve"
        assert "systemMessage" in result

    # Note: gh pr merge integration tests require mocking the GitHub API
    # since the hook calls `gh pr view` to get the PR body.
    # Unit tests for is_gh_pr_merge_command and extract_pr_number_from_merge
    # are covered in TestIsGhPrMergeCommand and TestExtractPrNumberFromMerge.


class TestGhPrMergeIntegration:
    """Integration tests for gh pr merge path (with API mocking)."""

    def setup_method(self):
        self.module = load_hook_module("pr-body-quality-check")

    def test_check_body_quality_for_merge_valid(self):
        """Test body quality check logic for merge with valid body."""
        body = """## なぜ
マージ時の品質チェック

Closes #123
"""
        is_valid, missing = self.module.check_body_quality(body)
        assert is_valid
        assert len(missing) == 0

    def test_check_body_quality_for_merge_missing_why(self):
        """Test body quality check logic for merge with missing why."""
        body = """## Summary
Some summary

Closes #123
"""
        is_valid, missing = self.module.check_body_quality(body)
        assert not is_valid
        assert any("なぜ" in m for m in missing)

    def test_check_body_quality_for_merge_missing_reference(self):
        """Test body quality check logic for merge with missing reference."""
        body = """## なぜ
Some reason

## 何を
Changes
"""
        is_valid, missing = self.module.check_body_quality(body)
        assert not is_valid
        assert any("参照" in m for m in missing)

    def test_format_block_message_for_merge(self):
        """Test block message format for merge."""
        missing = ["「なぜ」セクション（背景・動機）"]
        message = self.module.format_block_message(missing, is_merge=True)
        assert "マージをブロック" in message
        assert "gh pr edit" in message


class TestFormatBlockMessage:
    """Tests for format_block_message function."""

    def setup_method(self):
        self.module = load_hook_module("pr-body-quality-check")

    def test_create_message(self):
        """Test block message for PR creation."""
        missing = ["「なぜ」セクション（背景・動機）"]
        message = self.module.format_block_message(missing, is_merge=False)
        assert "作成をブロック" in message
        assert "## なぜ" in message
        assert "--body" in message

    def test_merge_message(self):
        """Test block message for PR merge."""
        missing = ["参照（Issue番号 #XXX または関連リンク）"]
        message = self.module.format_block_message(missing, is_merge=True)
        assert "マージをブロック" in message
        assert "gh pr edit" in message


class TestCheckIncrementalPr:
    """Tests for check_incremental_pr function (Issue #2608)."""

    def setup_method(self):
        self.module = load_hook_module("pr-body-quality-check")

    def test_pass_no_incremental_keywords(self):
        """Should pass when no incremental keywords exist."""
        body = """## なぜ
バグ修正

## 何を
修正内容

Closes #123
"""
        is_valid, reason = self.module.check_incremental_pr(body)
        assert is_valid
        assert reason is None

    def test_pass_incremental_with_issue(self):
        """Should pass when incremental keywords exist with Issue reference."""
        body = """## なぜ
段階的移行が必要

## 何を
第1段階として基本機能を実装

関連: #2607（第2段階以降）
"""
        is_valid, reason = self.module.check_incremental_pr(body)
        assert is_valid
        assert reason is None

    def test_fail_incremental_without_issue(self):
        """Should fail when incremental keywords exist without Issue reference."""
        body = """## なぜ
段階的移行が必要

## 何を
第1段階として基本機能を実装
残りは後で対応
"""
        is_valid, reason = self.module.check_incremental_pr(body)
        assert not is_valid
        assert reason is not None
        assert "残タスク" in reason

    def test_pass_empty_body(self):
        """Should pass for empty body."""
        is_valid, reason = self.module.check_incremental_pr("")
        assert is_valid

        is_valid, reason = self.module.check_incremental_pr(None)
        assert is_valid


class TestPrMergeIncrementalCheck:
    """Integration tests for incremental PR check in merge flow (Issue #2608)."""

    def test_pass_incremental_with_any_issue_reference(self):
        """Should pass incremental check when ANY Issue reference exists.

        This tests the known limitation: the check doesn't distinguish between
        "Closes #100" (current issue) and "#200 for follow-up".
        As long as SOME Issue is referenced, the check passes.
        """
        body = """## なぜ
段階的移行が必要

## 何を
第1段階として基本機能を実装
残りは後で対応します

Closes #100
"""
        module = load_hook_module("pr-body-quality-check")

        # Verify body quality passes
        is_valid, missing = module.check_body_quality(body)
        assert is_valid, f"Expected body quality to pass, but got missing: {missing}"

        # Verify incremental check passes because #100 is present
        is_valid, reason = module.check_incremental_pr(body)
        assert is_valid, f"Expected to pass with Issue reference, but failed: {reason}"

    def test_block_incremental_merge_truly_without_issue(self):
        """Should block when truly no Issue reference exists."""
        body = """## なぜ
段階的移行が必要

## 何を
第1段階として基本機能を実装
残りは後で対応します
"""
        # This body has no Issue reference at all
        module = load_hook_module("pr-body-quality-check")

        # Body quality check will fail (no #XXX or reference section)
        is_valid, missing = module.check_body_quality(body)
        assert not is_valid, f"Expected body quality to fail without Issue number: {missing}"

        # Incremental check would also fail
        is_valid, reason = module.check_incremental_pr(body)
        assert not is_valid, "Expected incremental check to fail without Issue number"
