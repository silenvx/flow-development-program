#!/usr/bin/env python3
"""Tests for systematization-check.py hook."""

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

MODULE_NAME = "systematization-check"


class TestLessonPatterns:
    """Tests for lesson detection patterns."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_japanese_lesson_patterns(self):
        """Test Japanese lesson pattern detection."""
        test_cases = [
            ("教訓として、これを覚えておく", True),
            ("反省点として記録", True),
            ("学びとして共有", True),
            ("気づいたことがある", True),
            ("今後はこうする必要がある", True),
            ("改善点を見つけた", True),
        ]
        for text, should_match in test_cases:
            with self.subTest(text=text):
                lessons, _ = self.module.find_lesson_patterns([text])
                if should_match:
                    assert len(lessons) > 0, f"Should match: {text}"
                else:
                    assert len(lessons) == 0, f"Should not match: {text}"

    def test_english_lesson_patterns(self):
        """Test English lesson pattern detection."""
        test_cases = [
            ("lessons learned from this", True),
            ("key takeaway is", True),
            ("should have done this differently", True),
            ("in the future we should", True),
            ("to prevent this from happening", True),
        ]
        for text, should_match in test_cases:
            with self.subTest(text=text):
                lessons, _ = self.module.find_lesson_patterns([text])
                if should_match:
                    assert len(lessons) > 0, f"Should match: {text}"
                else:
                    assert len(lessons) == 0, f"Should not match: {text}"

    def test_strong_lesson_patterns(self):
        """Test strong lesson pattern detection."""
        test_cases = [
            ("仕組み化する必要がある", True),
            ("hookを作成する", True),
            ("フックで対応する", True),
            ("CIで自動化すべき", True),
        ]
        for text, should_be_strong in test_cases:
            with self.subTest(text=text):
                _, has_strong = self.module.find_lesson_patterns([text])
                assert has_strong == should_be_strong, f"Strong pattern for: {text}"

    def test_no_lesson_patterns(self):
        """Test text without lesson patterns."""
        text = "This is just a regular message about code."
        lessons, has_strong = self.module.find_lesson_patterns([text])
        assert len(lessons) == 0
        assert not has_strong


class TestFalsePositivePatterns:
    """Tests for false positive detection."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_completion_patterns(self):
        """Test that completion messages are detected as false positives."""
        test_cases = [
            "仕組み化しました",
            "hookを作成完了",
            "フックを追加済み",
        ]
        for text in test_cases:
            with self.subTest(text=text):
                result = self.module.has_false_positive([text])
                assert result, f"Should be false positive: {text}"

    def test_not_false_positive(self):
        """Test that actual lessons are not false positives."""
        text = "教訓として、次回から気をつける"
        result = self.module.has_false_positive([text])
        assert not result


class TestSystematizationPatterns:
    """Tests for systematization file detection."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_hook_files(self):
        """Test hook file pattern detection."""
        files = [".claude/hooks/new-hook.py"]
        result = self.module.find_systematization_files(files)
        assert len(result) == 1

    def test_workflow_files(self):
        """Test workflow file pattern detection."""
        files = [".github/workflows/new-check.yml"]
        result = self.module.find_systematization_files(files)
        assert len(result) == 1

    def test_script_files(self):
        """Test script file pattern detection."""
        files = [".claude/scripts/new-script.py", ".claude/scripts/new-script.sh"]
        result = self.module.find_systematization_files(files)
        assert len(result) == 2

    def test_non_systematization_files(self):
        """Test that regular files are not detected."""
        files = ["README.md", "src/app.py", "AGENTS.md"]
        result = self.module.find_systematization_files(files)
        assert len(result) == 0


class TestExtractClaudeMessages:
    """Tests for Claude message extraction."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_extract_text_blocks(self):
        """Test extracting text from assistant messages."""
        transcript = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "This is a lesson learned."}],
            }
        ]
        messages = self.module.extract_claude_messages(transcript)
        assert len(messages) == 1
        assert messages[0] == "This is a lesson learned."

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


class TestExtractFileOperations:
    """Tests for file operation extraction."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_extract_edit_operations(self):
        """Test extracting Edit tool file paths."""
        transcript = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "/path/to/file.py"},
                    }
                ],
            }
        ]
        files = self.module.extract_file_operations(transcript)
        assert len(files) == 1
        assert files[0] == "/path/to/file.py"

    def test_extract_write_operations(self):
        """Test extracting Write tool file paths."""
        transcript = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": "/path/to/new-file.py"},
                    }
                ],
            }
        ]
        files = self.module.extract_file_operations(transcript)
        assert len(files) == 1

    def test_skip_other_tools(self):
        """Test that other tools are skipped."""
        transcript = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    }
                ],
            }
        ]
        files = self.module.extract_file_operations(transcript)
        assert len(files) == 0


class TestExitCode2Behavior:
    """Tests for exit code 2 behavior (Issue #1124, #1127)."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def _create_transcript_file(self, transcript: list) -> str:
        """Helper to create a temporary transcript file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(transcript, f)
            return f.name

    def _run_hook_with_input(self, input_data: dict) -> tuple:
        """Helper to run hook with mocked stdin and capture output.

        Returns:
            Tuple of (stdout_output, stderr_output, exit_info)
            exit_info is SystemExit if raised, else None
        """
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

    def test_exit_code_0_with_action_required_when_lessons_not_systematized(self):
        """Should exit with code 0 and output ACTION_REQUIRED when lessons detected but not systematized.

        Issue #2026: Changed from exit 2 (blocking) to exit 0 with ACTION_REQUIRED message.
        """
        transcript = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "これは教訓として記録すべきです。仕組み化が必要です。",
                    }
                ],
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
            assert "[ACTION_REQUIRED: SYSTEMATIZATION]" in stderr_output
            assert "[systematization-check]" in stderr_output
        finally:
            os.unlink(temp_path)

    def test_exit_code_0_when_stop_hook_active(self):
        """Should exit 0 when stop_hook_active is True to prevent infinite loops.

        Note: transcript_path points to a non-existent file, but when
        stop_hook_active=True, the hook exits early without reading it.
        """
        stdout_output, _, exit_info = self._run_hook_with_input(
            {"transcript_path": "/nonexistent/path.json", "stop_hook_active": True}
        )

        # Should not raise SystemExit
        assert exit_info is None

        # Should output JSON with approve
        result = json.loads(stdout_output)
        assert result["decision"] == "approve"

    def test_approve_when_lessons_systematized(self):
        """Should approve when lessons detected and systematization files exist."""
        transcript = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "これは教訓として記録します"},
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": ".claude/hooks/new-check.py"},
                    },
                ],
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

    def test_stderr_contains_remediation_info(self):
        """Should include remediation information in stderr output.

        Issue #2026: Changed from exit 2 to exit 0 with ACTION_REQUIRED.
        """
        transcript = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "仕組み化する必要があります"}],
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

            # Should contain ACTION_REQUIRED and remediation info
            assert "[ACTION_REQUIRED: SYSTEMATIZATION]" in stderr_output
            assert ".claude/hooks/" in stderr_output
        finally:
            os.unlink(temp_path)
