#!/usr/bin/env python3
"""Tests for defer-keyword-check.py hook.

Note: Tests for shared transcript utilities have been moved to test_transcript.py.
This file tests hook-specific functionality only.
"""

import json
import tempfile

from conftest import load_hook_module


class TestHasIssueReferenceNearby:
    """Tests for has_issue_reference_nearby function."""

    def setup_method(self):
        self.module = load_hook_module("defer-keyword-check")

    def test_issue_reference_present(self):
        """Test when issue reference is present."""
        text = "スコープ外のため #123 で対応予定"
        pos = text.find("スコープ外")
        assert self.module.has_issue_reference_nearby(text, pos)

    def test_issue_reference_absent(self):
        """Test when issue reference is absent."""
        text = "スコープ外のため後で対応"
        pos = text.find("スコープ外")
        assert not self.module.has_issue_reference_nearby(text, pos)

    def test_issue_reference_far_away(self):
        """Test when issue reference is too far."""
        # Window is 100 chars
        text = "スコープ外" + "x" * 200 + "#123"
        pos = 0
        assert not self.module.has_issue_reference_nearby(text, pos)


class TestCheckDeferKeywords:
    """Tests for check_defer_keywords function."""

    def setup_method(self):
        self.module = load_hook_module("defer-keyword-check")

    def test_no_violations(self):
        """Test when no defer keywords found."""
        content = json.dumps([{"role": "assistant", "content": "普通のテキスト"}])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            result = self.module.check_defer_keywords(f.name)

        assert result["violations"] == []
        assert result["defer_count"] == 0

    def test_defer_keyword_with_issue_ref(self):
        """Test defer keyword with issue reference (no violation)."""
        content = json.dumps(
            [{"role": "assistant", "content": "スコープ外のため #123 で対応します"}]
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            result = self.module.check_defer_keywords(f.name)

        # Has issue reference, so no violation
        assert result["with_issue_ref"] >= result["defer_count"] - len(result["violations"])

    def test_defer_keyword_without_issue_ref(self):
        """Test defer keyword without issue reference (violation)."""
        content = json.dumps([{"role": "assistant", "content": "スコープ外のため後で対応します"}])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            result = self.module.check_defer_keywords(f.name)

        assert result["defer_count"] > 0
        assert len(result["violations"]) > 0

    def test_file_not_found(self):
        """Test when file does not exist."""
        result = self.module.check_defer_keywords("/nonexistent/path.json")
        assert result["violations"] == []

    def test_exclude_code_block(self):
        """Test that code blocks are excluded."""
        content = json.dumps([{"role": "assistant", "content": "```\nスコープ外のため\n```"}])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            result = self.module.check_defer_keywords(f.name)

        # Code block should be excluded
        assert result["violations"] == []


class TestStage2Functions:
    """Tests for Stage 2 LLM evaluation functions."""

    def setup_method(self):
        self.module = load_hook_module("defer-keyword-check")

    def test_escape_backticks(self):
        """Test backtick escaping for prompt injection prevention."""
        # Normal text unchanged
        assert self.module._escape_backticks("normal text") == "normal text"

        # Single backtick escaped
        assert self.module._escape_backticks("code `inline`") == "code \\`inline\\`"

        # Triple backticks escaped
        assert self.module._escape_backticks("```code```") == "\\`\\`\\`code\\`\\`\\`"

        # Empty string
        assert self.module._escape_backticks("") == ""

    def test_build_llm_evaluation_prompt_format(self):
        """Test LLM prompt format with proper escaping."""
        potential_texts = [
            {"keyword": "検討します", "context": "この件は検討します。"},
            {"keyword": "様子を見", "context": "しばらく様子を見ましょう。"},
        ]

        prompt = self.module.build_llm_evaluation_prompt(potential_texts)

        # Should contain the keywords
        assert "検討します" in prompt
        assert "様子を見" in prompt

        # Should contain triple backticks for code blocks
        assert "```" in prompt

        # Should contain judgment criteria
        assert "先送り" in prompt

    def test_build_llm_evaluation_prompt_escapes_backticks(self):
        """Test that backticks in context are escaped."""
        potential_texts = [
            {"keyword": "検討します", "context": "Use `code` for this 検討します"},
        ]

        prompt = self.module.build_llm_evaluation_prompt(potential_texts)

        # Backticks should be escaped
        assert "\\`code\\`" in prompt

    def test_build_llm_evaluation_prompt_truncation(self):
        """Test that long contexts are truncated with '...'."""
        long_context = "x" * 150  # Over 100 chars
        short_context = "short"

        # Long context should have "..."
        long_texts = [{"keyword": "検討します", "context": long_context}]
        long_prompt = self.module.build_llm_evaluation_prompt(long_texts)
        assert "..." in long_prompt

        # Short context should not have "..."
        short_texts = [{"keyword": "検討します", "context": short_context}]
        short_prompt = self.module.build_llm_evaluation_prompt(short_texts)
        # "..." should only appear in criteria section, not in the context
        lines_with_keyword = [line for line in short_prompt.split("\n") if "検討します」" in line]
        for line in lines_with_keyword:
            # The line with the keyword context should not end with ...```
            if "short" in line:
                assert not line.endswith("...```")

    def test_build_llm_evaluation_prompt_max_items(self):
        """Test that prompt limits to 5 items."""
        potential_texts = [{"keyword": f"keyword{i}", "context": f"context{i}"} for i in range(10)]

        prompt = self.module.build_llm_evaluation_prompt(potential_texts)

        # Should only include first 5
        assert "keyword0" in prompt
        assert "keyword4" in prompt
        assert "keyword5" not in prompt


class TestPotentialDeferPatterns:
    """Tests for Stage 2 potential defer pattern detection."""

    def setup_method(self):
        self.module = load_hook_module("defer-keyword-check")

    def test_potential_defer_pattern_matches(self):
        """Test POTENTIAL_DEFER_PATTERN matches expected expressions."""
        pattern = self.module.POTENTIAL_DEFER_PATTERN
        should_match = [
            "検討します",
            "検討する",
            "検討中",
            "検討予定",
            "様子を見",
            "今度",
            "そのうち",
            "機会があれば",
            "時間があれば",
            "時間ができたら",
            "余裕があれば",
            "余裕ができたら",
            "いずれ",
            "追って",
        ]
        for text in should_match:
            assert pattern.search(text), f"Pattern should match: {text}"

    def test_potential_defer_pattern_not_matches(self):
        """Test POTENTIAL_DEFER_PATTERN does not match unrelated text."""
        pattern = self.module.POTENTIAL_DEFER_PATTERN
        should_not_match = [
            "完了しました",
            "修正済み",
            "対応しました",
            "問題ありません",
        ]
        for text in should_not_match:
            assert not pattern.search(text), f"Pattern should not match: {text}"

    def test_stage2_detection_without_issue_ref(self):
        """Test Stage 2 detection when no Stage 1 violation and no issue ref."""
        import json
        import tempfile

        # Use a potential defer pattern without issue reference
        content = json.dumps([{"role": "assistant", "content": "この件は検討します。"}])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            result = self.module.check_defer_keywords(f.name)

        # Should have potential_defer_texts for Stage 2
        assert len(result["potential_defer_texts"]) > 0
        assert result["potential_defer_texts"][0]["keyword"] == "検討します"

    def test_stage2_skipped_when_issue_ref_present(self):
        """Test Stage 2 patterns are skipped when issue reference is nearby."""
        import json
        import tempfile

        # Potential defer pattern with issue reference
        content = json.dumps([{"role": "assistant", "content": "この件は #123 で検討します。"}])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            result = self.module.check_defer_keywords(f.name)

        # Should not have potential_defer_texts (issue ref present)
        assert len(result["potential_defer_texts"]) == 0


class TestDeferKeywordPatterns:
    """Tests for defer keyword pattern detection."""

    def setup_method(self):
        self.module = load_hook_module("defer-keyword-check")
        import re

        self.DEFER_KEYWORDS = self.module.DEFER_KEYWORDS
        self.re = re

    def test_scope_out_pattern(self):
        """Test scope out patterns."""
        patterns = [
            "スコープ外のため",
            "スコープ外なので",
            "本PRのスコープ外",
        ]
        for text in patterns:
            matched = any(self.re.search(p, text) for p in self.DEFER_KEYWORDS)
            assert matched, f"Pattern not matched: {text}"

    def test_separate_handling_pattern(self):
        """Test separate handling patterns."""
        patterns = [
            "別途対応します",
            "別途対応する",
            "別途対応が必要",
        ]
        for text in patterns:
            matched = any(self.re.search(p, text) for p in self.DEFER_KEYWORDS)
            assert matched, f"Pattern not matched: {text}"

    def test_future_pattern(self):
        """Test future improvement patterns."""
        patterns = [
            "将来的に",
            "将来の改善",
            "将来の課題",
        ]
        for text in patterns:
            matched = any(self.re.search(p, text) for p in self.DEFER_KEYWORDS)
            assert matched, f"Pattern not matched: {text}"

    def test_followup_pattern(self):
        """Test followup patterns."""
        patterns = [
            "フォローアップとして",
            "フォローアップで",
            "フォローアップが",
        ]
        for text in patterns:
            matched = any(self.re.search(p, text) for p in self.DEFER_KEYWORDS)
            assert matched, f"Pattern not matched: {text}"
