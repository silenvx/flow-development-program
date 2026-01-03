#!/usr/bin/env python3
"""Tests for feedback-detector.py.

Issue #2506: Fix UserPromptSubmit type:prompt crash
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# Add parent directory to path for lib module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Dynamic import for hyphenated module name
HOOK_PATH = Path(__file__).parent.parent / "feedback-detector.py"
_spec = importlib.util.spec_from_file_location("feedback_detector", HOOK_PATH)
feedback_detector = importlib.util.module_from_spec(_spec)
sys.modules["feedback_detector"] = feedback_detector
_spec.loader.exec_module(feedback_detector)

is_feedback = feedback_detector.is_feedback


class TestIsFeedback:
    """Test is_feedback function."""

    # --- Positive cases (should detect) ---

    @pytest.mark.parametrize(
        "text",
        [
            "動いてる？",
            "動いてる",
            "正常？",
            "大丈夫？",
            "問題ない？",
            "おかしい",
            "おかしくない？",
            "バグがある",
            "壊れてる",
            "動かない",
            "動作しない",
            "エラーが出る",
            "失敗した",
            "期待通りじゃない",
            "意図した動作ではない",
            "想定と違う",
            "確認した？",
            "テストした？",
            "検証した？",
            "チェックした？",
        ],
    )
    def test_detects_negative_feedback(self, text: str):
        """Should detect negative feedback patterns."""
        assert is_feedback(text) is True

    # --- Negative cases (should NOT detect) ---

    @pytest.mark.parametrize(
        "text",
        [
            "PRを作成して",
            "機能を追加して",
            "ファイルを読んで",
            "コードを修正して",
            "こんにちは",
            "ありがとう",
            "確認して",
            "見て",
            "",
            "a",
        ],
    )
    def test_excludes_normal_requests(self, text: str):
        """Should NOT detect normal work requests."""
        assert is_feedback(text) is False

    # --- Edge cases ---

    def test_empty_string(self):
        """Empty string should return False."""
        assert is_feedback("") is False

    def test_single_char(self):
        """Single character should return False."""
        assert is_feedback("あ") is False

    def test_none_handling(self):
        """None should be handled gracefully."""
        # The function expects str, but should handle edge cases
        assert is_feedback(None) is False  # type: ignore


class TestMain:
    """Test main function."""

    def test_no_user_prompt(self, monkeypatch, capsys):
        """Should output continue:true when no user_prompt."""
        import json

        monkeypatch.setattr(
            "sys.stdin",
            type("MockStdin", (), {"read": lambda self: json.dumps({})})(),
        )

        feedback_detector.main()
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result == {"continue": True}

    def test_no_feedback_pattern(self, monkeypatch, capsys):
        """Should output continue:true without systemMessage for normal input."""
        import json

        monkeypatch.setattr(
            "sys.stdin",
            type(
                "MockStdin",
                (),
                {"read": lambda self: json.dumps({"user_prompt": "PRを作成して"})},
            )(),
        )

        feedback_detector.main()
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result == {"continue": True}
        assert "systemMessage" not in result

    def test_feedback_pattern_detected(self, monkeypatch, capsys):
        """Should output systemMessage when feedback detected."""
        import json

        monkeypatch.setattr(
            "sys.stdin",
            type(
                "MockStdin",
                (),
                {"read": lambda self: json.dumps({"user_prompt": "バグがある"})},
            )(),
        )

        feedback_detector.main()
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["continue"] is True
        assert "systemMessage" in result
        assert "ACTION_REQUIRED" in result["systemMessage"]

    def test_exception_handling(self, monkeypatch, capsys):
        """Should handle exceptions silently and output continue:true."""
        import json

        # Mock parse_hook_input to raise an exception
        def mock_parse_hook_input():
            raise ValueError("Test error")

        monkeypatch.setattr(feedback_detector, "parse_hook_input", mock_parse_hook_input)

        feedback_detector.main()
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result == {"continue": True}
        # Error should be logged to stderr
        assert "feedback-detector:" in captured.err
        assert "Test error" in captured.err
