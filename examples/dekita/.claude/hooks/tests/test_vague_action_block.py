#!/usr/bin/env python3
"""Tests for vague-action-block.py hook."""

import json
import os
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path for imports
TESTS_DIR = Path(__file__).parent
HOOKS_DIR = TESTS_DIR.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from conftest import load_hook_module

MODULE_NAME = "vague-action-block"


class TestVagueActionPatterns:
    """Tests for vague action pattern detection."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_detects_guard_rule_pattern(self):
        """Test detection of 'ガイドを守る' pattern."""
        messages = ["対策として、ガイドを守るようにします"]
        excerpts = self.module.find_vague_patterns(messages)
        assert len(excerpts) > 0, "Should detect 'ガイドを守る'"

    def test_detects_attention_pattern(self):
        """Test detection of '注意する' pattern."""
        messages = ["改善点として、注意するようにします"]
        excerpts = self.module.find_vague_patterns(messages)
        assert len(excerpts) > 0, "Should detect '注意する'"

    def test_detects_thorough_pattern(self):
        """Test detection of '徹底する' pattern."""
        messages = ["今後は確認を徹底します"]
        excerpts = self.module.find_vague_patterns(messages)
        assert len(excerpts) > 0, "Should detect '徹底する'"

    def test_detects_mindful_pattern(self):
        """Test detection of '心がける' pattern."""
        messages = ["対策として、品質を意識するようにします"]
        excerpts = self.module.find_vague_patterns(messages)
        assert len(excerpts) > 0, "Should detect '意識する'"

    def test_detects_rule_obey_pattern(self):
        """Test detection of 'ルールを遵守' pattern in countermeasure context."""
        # Needs countermeasure context to trigger
        messages = ["対策として、ルールを遵守します"]
        excerpts = self.module.find_vague_patterns(messages)
        assert len(excerpts) > 0, "Should detect 'ルールを遵守' in countermeasure context"

    def test_no_detection_without_countermeasure_context(self):
        """Test that patterns without countermeasure context are not detected."""
        # No countermeasure context - should NOT trigger
        messages = ["ルールを遵守します"]
        excerpts = self.module.find_vague_patterns(messages)
        assert len(excerpts) == 0, "Should not detect without countermeasure context"

    def test_allows_concrete_action_with_issue(self):
        """Test that concrete actions with Issue are allowed."""
        messages = ["対策として、Issue #1234 を作成しました"]
        excerpts = self.module.find_vague_patterns(messages)
        assert len(excerpts) == 0, "Should allow concrete action with Issue"

    def test_allows_concrete_action_with_hook(self):
        """Test that concrete actions with hook creation are allowed."""
        messages = ["対策として、フックを作成しました"]
        excerpts = self.module.find_vague_patterns(messages)
        assert len(excerpts) == 0, "Should allow concrete action with hook"

    def test_allows_concrete_action_with_ci(self):
        """Test that concrete actions with CI are allowed."""
        messages = ["改善として、CIで自動チェックを追加しました"]
        excerpts = self.module.find_vague_patterns(messages)
        assert len(excerpts) == 0, "Should allow concrete action with CI"

    def test_no_vague_patterns_in_normal_text(self):
        """Test that normal text doesn't trigger detection."""
        messages = ["コードを修正しました。テストを追加しました。"]
        excerpts = self.module.find_vague_patterns(messages)
        assert len(excerpts) == 0, "Should not detect in normal text"

    def test_multiple_vague_patterns(self):
        """Test detection of multiple vague patterns in different messages."""
        messages = [
            "対策として、ガイドを守ります",
            "今後は注意するようにします",
        ]
        excerpts = self.module.find_vague_patterns(messages)
        assert len(excerpts) == 2, "Should detect multiple vague patterns"


class TestConcreteActionPatterns:
    """Tests for concrete action pattern detection."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_issue_pattern(self):
        """Test Issue pattern detection."""
        assert self.module.has_concrete_action("Issue #1234 を作成")
        assert self.module.has_concrete_action("Issue を作成しました")

    def test_hook_pattern(self):
        """Test hook pattern detection."""
        assert self.module.has_concrete_action("フックを作成しました")
        assert self.module.has_concrete_action("hookを追加")

    def test_ci_pattern(self):
        """Test CI pattern detection."""
        assert self.module.has_concrete_action("CIでチェックを追加")
        assert self.module.has_concrete_action("CIに追加しました")

    def test_implementation_pattern(self):
        """Test implementation pattern detection."""
        assert self.module.has_concrete_action("実装しました")
        assert self.module.has_concrete_action("コード修正しました")
        assert self.module.has_concrete_action("修正しました")

    def test_no_concrete_action(self):
        """Test text without concrete action."""
        assert not self.module.has_concrete_action("注意するようにします")
        assert not self.module.has_concrete_action("ガイドを守ります")


class TestTranscriptProcessing:
    """Tests for transcript processing functions."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_extract_claude_messages(self):
        """Test extracting messages from transcript."""
        transcript = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "対策として、ガイドを守ります"}],
            }
        ]
        messages = self.module.extract_claude_messages(transcript)
        assert len(messages) == 1
        assert "ガイドを守り" in messages[0]

    def test_skip_user_messages(self):
        """Test that user messages are skipped."""
        transcript = [
            {"role": "user", "content": [{"type": "text", "text": "User message"}]},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Assistant message"}],
            },
        ]
        messages = self.module.extract_claude_messages(transcript)
        assert len(messages) == 1
        assert messages[0] == "Assistant message"


class TestExitCode2Behavior:
    """Tests for exit code 2 behavior."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def _create_transcript_file(self, transcript: list) -> str:
        """Helper to create a temporary transcript file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(transcript, f)
            return f.name

    def _run_hook_with_input(self, input_data: dict) -> tuple:
        """Helper to run hook with mocked stdin and capture output."""
        mock_input = json.dumps(input_data)
        captured_stdout = StringIO()
        captured_stderr = StringIO()
        exit_info = None

        with patch("sys.stdin", StringIO(mock_input)):
            with patch("sys.stdout", captured_stdout):
                with patch("sys.stderr", captured_stderr):
                    with patch.object(self.module, "log_hook_execution"):
                        try:
                            self.module.main()
                        except SystemExit as e:
                            exit_info = e

        return captured_stdout.getvalue(), captured_stderr.getvalue(), exit_info

    def test_exit_code_0_with_action_required_when_vague_patterns_detected(self):
        """Should exit with code 0 and output ACTION_REQUIRED when vague patterns detected.

        Issue #2026: Changed from exit 2 (blocking) to exit 0 with ACTION_REQUIRED message.
        """
        transcript = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "対策として、ガイドを守ります"}],
            }
        ]

        temp_path = self._create_transcript_file(transcript)
        try:
            stdout_output, stderr_output, exit_info = self._run_hook_with_input(
                {"transcript_path": temp_path, "stop_hook_active": False}
            )

            # Should exit with code 0 (not blocking)
            assert exit_info is None

            # Should output approve result
            result = json.loads(stdout_output)
            assert result["decision"] == "approve"

            # Should output ACTION_REQUIRED to stderr
            assert "[ACTION_REQUIRED: CONCRETE_ACTION]" in stderr_output
            assert "[vague-action-block]" in stderr_output
        finally:
            os.unlink(temp_path)

    def test_exit_code_0_when_stop_hook_active(self):
        """Should exit 0 when stop_hook_active is True."""
        stdout_output, _, exit_info = self._run_hook_with_input(
            {"transcript_path": "/nonexistent/path.json", "stop_hook_active": True}
        )

        # Should not raise SystemExit
        assert exit_info is None

        # Should output JSON with approve
        result = json.loads(stdout_output)
        assert result["decision"] == "approve"

    def test_approve_when_no_vague_patterns(self):
        """Should approve when no vague patterns detected."""
        transcript = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Issue #1234 を作成しました"}],
            }
        ]

        temp_path = self._create_transcript_file(transcript)
        try:
            stdout_output, _, exit_info = self._run_hook_with_input(
                {"transcript_path": temp_path, "stop_hook_active": False}
            )

            # Should not raise SystemExit
            assert exit_info is None

            result = json.loads(stdout_output)
            assert result["decision"] == "approve"
        finally:
            os.unlink(temp_path)

    def test_approve_when_concrete_action_present(self):
        """Should approve when concrete action is present alongside vague expression."""
        transcript = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "ガイドを守るためにフックを作成しました",
                    }
                ],
            }
        ]

        temp_path = self._create_transcript_file(transcript)
        try:
            stdout_output, _, exit_info = self._run_hook_with_input(
                {"transcript_path": temp_path, "stop_hook_active": False}
            )

            # Should not raise SystemExit because concrete action is present
            assert exit_info is None

            result = json.loads(stdout_output)
            assert result["decision"] == "approve"
        finally:
            os.unlink(temp_path)

    def test_stderr_contains_remediation_info(self):
        """Should include remediation information in stderr output.

        Issue #2026: Changed from exit 2 to exit 0 with ACTION_REQUIRED.
        """
        transcript = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "対策として、注意します"}],
            }
        ]

        temp_path = self._create_transcript_file(transcript)
        try:
            stdout_output, stderr_output, exit_info = self._run_hook_with_input(
                {"transcript_path": temp_path, "stop_hook_active": False}
            )

            # Should exit with code 0 (not blocking)
            assert exit_info is None

            # Should output approve result
            result = json.loads(stdout_output)
            assert result["decision"] == "approve"

            # Should contain ACTION_REQUIRED and remediation suggestions
            assert "[ACTION_REQUIRED: CONCRETE_ACTION]" in stderr_output
            assert "フック作成" in stderr_output
            assert "Issue作成" in stderr_output
            assert "精神論" in stderr_output
        finally:
            os.unlink(temp_path)
