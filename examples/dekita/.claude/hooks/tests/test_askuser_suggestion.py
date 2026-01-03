#!/usr/bin/env python3
"""Tests for askuser-suggestion.py hook.

Note: Tests for shared transcript utilities have been moved to test_transcript.py.
This file tests hook-specific functionality only.
"""

import json
import tempfile

from conftest import load_hook_module


class TestCheckAskuserUsage:
    """Tests for check_askuser_usage function."""

    def setup_method(self):
        self.module = load_hook_module("askuser-suggestion")

    def test_no_violations(self):
        """Test when no choice patterns found."""
        content = json.dumps([{"role": "assistant", "content": "普通のテキスト"}])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            result = self.module.check_askuser_usage(f.name)

        assert result["violations"] == []
        assert result["choice_text_count"] == 0

    def test_choice_pattern_detected(self):
        """Test when choice pattern is detected."""
        content = json.dumps(
            [{"role": "assistant", "content": "A案とB案があります。どちらにしますか"}]
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            result = self.module.check_askuser_usage(f.name)

        assert result["choice_text_count"] > 0

    def test_file_not_found(self):
        """Test when file does not exist."""
        result = self.module.check_askuser_usage("/nonexistent/path.json")
        assert result["violations"] == []

    def test_exclude_code_block(self):
        """Test that code blocks are excluded."""
        content = json.dumps([{"role": "assistant", "content": "```\nA案とB案があります\n```"}])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            result = self.module.check_askuser_usage(f.name)

        # Code block should be excluded
        assert result["violations"] == []


class TestChoicePatterns:
    """Tests for choice pattern detection."""

    def setup_method(self):
        self.module = load_hook_module("askuser-suggestion")
        import re

        self.CHOICE_PATTERNS = self.module.CHOICE_PATTERNS
        self.re = re

    def test_ab_pattern(self):
        """Test A案/B案 pattern."""
        text = "A案はシンプル、B案は拡張性重視"
        matched = any(self.re.search(p, text) for p in self.CHOICE_PATTERNS)
        assert matched

    def test_question_pattern(self):
        """Test question patterns."""
        patterns = [
            "どちらにしますか",
            "どれを選びますか",
            "以下から選んでください",
        ]
        for text in patterns:
            matched = any(self.re.search(p, text) for p in self.CHOICE_PATTERNS)
            assert matched, f"Pattern not matched: {text}"
