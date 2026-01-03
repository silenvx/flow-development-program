"""Tests for check_utils module.

This module contains common utility functions extracted from merge-check.py.
Full functional tests are in test_merge_check.py.
"""

import sys
from pathlib import Path

# Add parent directory to path for module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


class TestCheckUtilsImports:
    """Test that check_utils module can be imported and has expected exports."""

    def test_module_imports(self):
        """Module should be importable."""
        import check_utils

        assert check_utils is not None

    def test_truncate_body_exists(self):
        """truncate_body function should exist."""
        from check_utils import truncate_body

        assert callable(truncate_body)

    def test_strip_code_blocks_exists(self):
        """strip_code_blocks function should exist."""
        from check_utils import strip_code_blocks

        assert callable(strip_code_blocks)

    def test_get_repo_owner_and_name_exists(self):
        """get_repo_owner_and_name function should exist."""
        from check_utils import get_repo_owner_and_name

        assert callable(get_repo_owner_and_name)

    def test_patterns_exist(self):
        """Module should export expected patterns."""
        from check_utils import CODE_BLOCK_PATTERN, ISSUE_REFERENCE_PATTERN

        assert CODE_BLOCK_PATTERN is not None
        assert ISSUE_REFERENCE_PATTERN is not None


class TestTruncateBody:
    """Test truncate_body function."""

    def test_short_text_unchanged(self):
        """Short text should not be truncated."""
        from check_utils import truncate_body

        result = truncate_body("short text", max_length=100)
        assert result == "short text"

    def test_long_text_truncated(self):
        """Long text should be truncated with ellipsis."""
        from check_utils import truncate_body

        result = truncate_body("a" * 150, max_length=100)
        assert len(result) == 103  # 100 chars + "..."
        assert result.endswith("...")


class TestStripCodeBlocks:
    """Test strip_code_blocks function."""

    def test_removes_fenced_code_blocks(self):
        """Should remove fenced code blocks."""
        from check_utils import strip_code_blocks

        text = "Before\n```python\ncode here\n```\nAfter"
        result = strip_code_blocks(text)
        assert "code here" not in result
        assert "Before" in result
        assert "After" in result

    def test_removes_inline_code(self):
        """Should remove inline code."""
        from check_utils import strip_code_blocks

        text = "Use `some_function()` here"
        result = strip_code_blocks(text)
        assert "some_function" not in result
        assert "Use" in result
        assert "here" in result


class TestHasWhySection:
    """Test has_why_section function."""

    def test_markdown_headers(self):
        """Should detect markdown headers like ## Why, ## なぜ, ## 背景."""
        from check_utils import has_why_section

        # Japanese
        assert has_why_section("## なぜ\n説明") is True
        assert has_why_section("## 背景\n説明") is True
        assert has_why_section("## 理由\n説明") is True
        # English
        assert has_why_section("## Why\nReason") is True
        assert has_why_section("## Motivation\nReason") is True
        assert has_why_section("## Background\nInfo") is True

    def test_bold_headers(self):
        """Should detect bold headers like **Why**, **なぜ**."""
        from check_utils import has_why_section

        assert has_why_section("**なぜ**\n説明") is True
        assert has_why_section("**Why**\nReason") is True

    def test_colon_format(self):
        """Should detect colon format like Why:, なぜ:."""
        from check_utils import has_why_section

        assert has_why_section("Why: Some reason") is True
        assert has_why_section("なぜ: 理由") is True
        assert has_why_section("背景：全角コロン") is True

    def test_no_why_section(self):
        """Should return False when no why section exists."""
        from check_utils import has_why_section

        assert has_why_section("## Summary\nJust a summary") is False
        assert has_why_section("Some description") is False

    def test_empty_body(self):
        """Should return False for empty or None body."""
        from check_utils import has_why_section

        assert has_why_section("") is False
        assert has_why_section(None) is False


class TestHasReference:
    """Test has_reference function."""

    def test_issue_number(self):
        """Should detect Issue numbers like #123."""
        from check_utils import has_reference

        assert has_reference("#123") is True
        assert has_reference("Closes #456") is True
        assert has_reference("Fixes #789") is True

    def test_github_url(self):
        """Should detect GitHub URLs."""
        from check_utils import has_reference

        assert has_reference("https://github.com/owner/repo/issues/123") is True
        assert has_reference("https://github.com/owner/repo/pull/456") is True

    def test_reference_section(self):
        """Should detect reference sections."""
        from check_utils import has_reference

        assert has_reference("## Refs\nSome refs") is True
        assert has_reference("## 参照\nリンク") is True
        assert has_reference("Refs: See docs") is True

    def test_no_reference(self):
        """Should return False when no reference exists."""
        from check_utils import has_reference

        assert has_reference("Just a description") is False
        assert has_reference("## Summary\nNo refs here") is False

    def test_empty_body(self):
        """Should return False for empty or None body."""
        from check_utils import has_reference

        assert has_reference("") is False
        assert has_reference(None) is False


class TestCheckBodyQuality:
    """Test check_body_quality function."""

    def test_valid_body(self):
        """Should return is_valid=True for body with all required sections."""
        from check_utils import check_body_quality

        body = """## なぜ
バグ修正が必要

Closes #123
"""
        is_valid, missing = check_body_quality(body)
        assert is_valid is True
        assert len(missing) == 0

    def test_missing_why(self):
        """Should return is_valid=False when why section is missing."""
        from check_utils import check_body_quality

        body = """## Summary
Just a summary

Closes #123
"""
        is_valid, missing = check_body_quality(body)
        assert is_valid is False
        assert any("なぜ" in m for m in missing)

    def test_missing_reference(self):
        """Should return is_valid=False when reference is missing."""
        from check_utils import check_body_quality

        body = """## なぜ
理由説明

## 何を
変更内容
"""
        is_valid, missing = check_body_quality(body)
        assert is_valid is False
        assert any("参照" in m for m in missing)

    def test_missing_both(self):
        """Should return is_valid=False with two missing items."""
        from check_utils import check_body_quality

        body = "Just a simple description"
        is_valid, missing = check_body_quality(body)
        assert is_valid is False
        assert len(missing) == 2


class TestHasIncrementalKeywords:
    """Test has_incremental_keywords function."""

    def test_detects_dandankai(self):
        """Should detect 段階的 keyword."""
        from check_utils import has_incremental_keywords

        assert has_incremental_keywords("段階的に移行する") is True
        assert has_incremental_keywords("段階的移行") is True

    def test_detects_dai_n_dankai(self):
        """Should detect 第N段階 pattern."""
        from check_utils import has_incremental_keywords

        assert has_incremental_keywords("第1段階として実装") is True
        assert has_incremental_keywords("第2段階完了") is True
        assert has_incremental_keywords("第10段階") is True

    def test_detects_kouzoku_task(self):
        """Should detect 後続タスク keyword."""
        from check_utils import has_incremental_keywords

        assert has_incremental_keywords("後続タスクで対応") is True

    def test_detects_shourai_ikou(self):
        """Should detect 将来の...移行 pattern."""
        from check_utils import has_incremental_keywords

        assert has_incremental_keywords("将来の完全移行で対応") is True
        assert has_incremental_keywords("将来のAPI移行") is True

    def test_detects_konkai_nomi(self):
        """Should detect 今回は...のみ pattern."""
        from check_utils import has_incremental_keywords

        assert has_incremental_keywords("今回は基本機能のみ実装") is True
        assert has_incremental_keywords("今回はコア部分のみ") is True

    def test_detects_zanri_ikou(self):
        """Should detect 残りは...移行 pattern."""
        from check_utils import has_incremental_keywords

        assert has_incremental_keywords("残りは次のPRで移行") is True

    def test_no_incremental_keywords(self):
        """Should return False when no incremental keywords exist."""
        from check_utils import has_incremental_keywords

        assert has_incremental_keywords("バグ修正") is False
        assert has_incremental_keywords("新機能追加") is False
        assert has_incremental_keywords("## なぜ\nリファクタリング") is False

    def test_empty_body(self):
        """Should return False for empty or None body."""
        from check_utils import has_incremental_keywords

        assert has_incremental_keywords("") is False
        assert has_incremental_keywords(None) is False

    def test_ignores_keywords_in_code_blocks(self):
        """Should ignore incremental keywords inside code blocks."""
        from check_utils import has_incremental_keywords

        # Keywords in fenced code block
        assert (
            has_incremental_keywords(
                """## なぜ
バグ修正

## 何を
```python
# 段階的に実行
# 第1段階として処理
```
"""
            )
            is False
        )

        # Keywords in inline code
        assert has_incremental_keywords("今回は`段階的`パターンをサポートします") is False


class TestCheckIncrementalPr:
    """Test check_incremental_pr function."""

    def test_valid_incremental_pr_with_issue(self):
        """Should pass when incremental keywords exist with Issue reference."""
        from check_utils import check_incremental_pr

        body = """## なぜ
段階的移行が必要

## 何を
第1段階として基本機能を実装

関連: #2607（第2段階以降）
"""
        is_valid, reason = check_incremental_pr(body)
        assert is_valid is True
        assert reason is None

    def test_invalid_incremental_pr_without_issue(self):
        """Should fail when incremental keywords exist without Issue reference."""
        from check_utils import check_incremental_pr

        body = """## なぜ
段階的移行が必要

## 何を
第1段階として基本機能を実装
残りは後で対応
"""
        is_valid, reason = check_incremental_pr(body)
        assert is_valid is False
        assert reason is not None
        assert "残タスク" in reason or "Issue" in reason

    def test_non_incremental_pr_passes(self):
        """Should pass when no incremental keywords exist."""
        from check_utils import check_incremental_pr

        body = """## なぜ
バグ修正

## 何を
修正内容
"""
        is_valid, reason = check_incremental_pr(body)
        assert is_valid is True
        assert reason is None

    def test_empty_body(self):
        """Should pass for empty body (no keywords)."""
        from check_utils import check_incremental_pr

        is_valid, reason = check_incremental_pr("")
        assert is_valid is True

        is_valid, reason = check_incremental_pr(None)
        assert is_valid is True
