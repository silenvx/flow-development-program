"""Tests for lib/transcript module.

Issue #1915: Shared transcript utilities
Issue #2261: Added load_transcript tests
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib import transcript as transcript_module
from lib.transcript import extract_assistant_responses, is_in_code_block, load_transcript


class TestIsInCodeBlock:
    """Tests for is_in_code_block function."""

    def test_not_in_code_block(self):
        """Text outside code block returns False."""
        text = "Hello world"
        assert is_in_code_block(text, 5) is False

    def test_in_code_block(self):
        """Text inside code block returns True."""
        text = "Hello ```code here``` world"
        # Position 10 is inside "code here"
        assert is_in_code_block(text, 10) is True

    def test_after_code_block(self):
        """Text after code block returns False."""
        text = "Hello ```code``` world"
        # Position 20 is in "world"
        assert is_in_code_block(text, 20) is False

    def test_between_code_blocks(self):
        """Text between code blocks returns False."""
        text = "```first``` middle ```second```"
        # Position 14 is in "middle"
        assert is_in_code_block(text, 14) is False

    def test_nested_appears_as_closed(self):
        """Odd number of backtick groups means inside."""
        text = "```outer ```nested``` still outer```"
        # This behavior is based on simple counting
        assert is_in_code_block(text, 5) is True

    def test_position_at_start(self):
        """Position at start of string."""
        text = "```code```"
        assert is_in_code_block(text, 0) is False

    def test_multiline_code_block(self):
        """Multiline code block handling."""
        text = "text\n```\ncode\nmore code\n```\nafter"
        # Position in "code" line
        assert is_in_code_block(text, 12) is True
        # Position in "after"
        assert is_in_code_block(text, 30) is False


class TestExtractAssistantResponses:
    """Tests for extract_assistant_responses function."""

    def test_jsonl_format(self):
        """Extract from JSONL format (one JSON per line)."""
        content = '{"role": "assistant", "content": "Hello"}\n{"role": "user", "content": "Hi"}\n'
        responses = extract_assistant_responses(content)
        assert responses == ["Hello"]

    def test_jsonl_multiple_assistants(self):
        """Extract multiple assistant responses from JSONL."""
        content = (
            '{"role": "assistant", "content": "First"}\n'
            '{"role": "user", "content": "Question"}\n'
            '{"role": "assistant", "content": "Second"}\n'
        )
        responses = extract_assistant_responses(content)
        assert responses == ["First", "Second"]

    def test_json_array_format(self):
        """Extract from JSON array format."""
        content = '[{"role": "assistant", "content": "Hello"}, {"role": "user", "content": "Hi"}]'
        responses = extract_assistant_responses(content)
        assert responses == ["Hello"]

    def test_empty_content(self):
        """Empty content returns empty list."""
        responses = extract_assistant_responses("")
        assert responses == []

    def test_no_assistant_responses(self):
        """Content without assistant responses returns empty list."""
        content = '{"role": "user", "content": "Hello"}\n'
        responses = extract_assistant_responses(content)
        assert responses == []

    def test_assistant_without_content(self):
        """Assistant message without content field is skipped."""
        content = '{"role": "assistant"}\n'
        responses = extract_assistant_responses(content)
        assert responses == []

    def test_assistant_with_empty_content(self):
        """Assistant message with empty content is filtered out."""
        content = '{"role": "assistant", "content": ""}\n'
        responses = extract_assistant_responses(content)
        # 空contentはJSONL処理・正規表現フォールバック共に除外される
        assert responses == []

    def test_array_in_jsonl_skipped(self):
        """Array lines in JSONL are skipped."""
        content = '[1, 2, 3]\n{"role": "assistant", "content": "Hello"}\n'
        responses = extract_assistant_responses(content)
        assert responses == ["Hello"]

    def test_invalid_json_skipped(self):
        """Invalid JSON lines are skipped."""
        content = 'not json\n{"role": "assistant", "content": "Hello"}\n'
        responses = extract_assistant_responses(content)
        assert responses == ["Hello"]

    def test_escaped_content(self):
        """Content with JSON escapes is properly decoded."""
        content = '{"role": "assistant", "content": "Hello\\nWorld"}\n'
        responses = extract_assistant_responses(content)
        assert responses == ["Hello\nWorld"]

    def test_unicode_content(self):
        """Unicode content is handled correctly."""
        content = '{"role": "assistant", "content": "こんにちは"}\n'
        responses = extract_assistant_responses(content)
        assert responses == ["こんにちは"]

    def test_regex_fallback(self):
        """Fallback to regex when JSON parsing fails."""
        # Content that's not valid JSONL or JSON array but contains assistant pattern
        content = 'some prefix {"role": "assistant", "content": "Hello"} some suffix'
        responses = extract_assistant_responses(content)
        assert responses == ["Hello"]


class TestIntegration:
    """Integration tests combining both functions."""

    def test_skip_code_blocks_in_response(self):
        """Code blocks in assistant responses can be identified."""
        content = '{"role": "assistant", "content": "Here is code:\\n```python\\nprint(hello)\\n```\\nEnd"}\n'
        responses = extract_assistant_responses(content)
        assert len(responses) == 1
        response = responses[0]
        # Check positions
        code_pos = response.find("print")
        end_pos = response.find("End")
        assert is_in_code_block(response, code_pos) is True
        assert is_in_code_block(response, end_pos) is False


class TestLoadTranscript:
    """Tests for load_transcript function.

    Issue #2261: Extracted from 4 Stop hooks to common utility.
    """

    def test_loads_json_file(self) -> None:
        """Loads regular JSON transcript file."""
        transcript = [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Hi!"}]},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(transcript, f)
            temp_path = f.name
        try:
            with patch.object(transcript_module, "is_safe_transcript_path", return_value=True):
                result = load_transcript(temp_path)
            assert result == transcript
        finally:
            Path(temp_path).unlink()

    def test_loads_jsonl_file(self) -> None:
        """Loads JSON Lines transcript file (Issue #2254)."""
        transcript = [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Hi!"}]},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for entry in transcript:
                f.write(json.dumps(entry) + "\n")
            temp_path = f.name
        try:
            with patch.object(transcript_module, "is_safe_transcript_path", return_value=True):
                result = load_transcript(temp_path)
            assert result == transcript
        finally:
            Path(temp_path).unlink()

    def test_returns_none_for_unsafe_path(self) -> None:
        """Returns None when path validation fails."""
        with patch.object(transcript_module, "is_safe_transcript_path", return_value=False):
            result = load_transcript("/some/path.json")
        assert result is None

    def test_returns_none_for_nonexistent_file(self) -> None:
        """Returns None when file does not exist."""
        with patch.object(transcript_module, "is_safe_transcript_path", return_value=True):
            result = load_transcript("/nonexistent/path.json")
        assert result is None

    def test_returns_none_for_invalid_json(self) -> None:
        """Returns None when JSON parsing fails."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("invalid json content")
            temp_path = f.name
        try:
            with patch.object(transcript_module, "is_safe_transcript_path", return_value=True):
                result = load_transcript(temp_path)
            assert result is None
        finally:
            Path(temp_path).unlink()

    def test_handles_empty_jsonl_file(self) -> None:
        """Handles empty JSONL file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("")
            temp_path = f.name
        try:
            with patch.object(transcript_module, "is_safe_transcript_path", return_value=True):
                result = load_transcript(temp_path)
            assert result == []
        finally:
            Path(temp_path).unlink()

    def test_handles_jsonl_with_blank_lines(self) -> None:
        """JSONL file with blank lines is handled correctly."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"role": "user", "content": "Hello"}\n')
            f.write("\n")
            f.write('{"role": "assistant", "content": "Hi"}\n')
            temp_path = f.name
        try:
            with patch.object(transcript_module, "is_safe_transcript_path", return_value=True):
                result = load_transcript(temp_path)
            assert len(result) == 2
            assert result[0]["role"] == "user"
            assert result[1]["role"] == "assistant"
        finally:
            Path(temp_path).unlink()
