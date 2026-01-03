#!/usr/bin/env python3
"""Tests for codex-review-output-logger.py hook."""

import importlib.util
import json
import sys
from pathlib import Path

# Add hooks directory to path for common module import
hooks_dir = str(Path(__file__).parent.parent)
if hooks_dir not in sys.path:
    sys.path.insert(0, hooks_dir)

# Load module with hyphens using importlib
hook_path = Path(__file__).parent.parent / "codex-review-output-logger.py"
spec = importlib.util.spec_from_file_location("codex_review_output_logger", hook_path)
module = importlib.util.module_from_spec(spec)
sys.modules["codex_review_output_logger"] = module
spec.loader.exec_module(module)

is_codex_review_command = module.is_codex_review_command
parse_file_line_comment = module.parse_file_line_comment
parse_json_output = module.parse_json_output
parse_codex_review_output = module.parse_codex_review_output
extract_tokens_used = module.extract_tokens_used
extract_base_branch = module.extract_base_branch


class TestExtractTokensUsed:
    """Tests for extract_tokens_used function."""

    def test_tokens_with_comma(self):
        """Should extract tokens with comma formatting."""
        output = "some output...\ntokens used: 6,356\n"
        assert extract_tokens_used(output) == 6356

    def test_tokens_without_comma(self):
        """Should extract tokens without comma."""
        output = "tokens used: 1234"
        assert extract_tokens_used(output) == 1234

    def test_tokens_case_insensitive(self):
        """Should be case insensitive."""
        output = "Tokens Used: 999"
        assert extract_tokens_used(output) == 999

    def test_tokens_large_number(self):
        """Should handle large numbers with multiple commas."""
        output = "tokens used: 1,234,567"
        assert extract_tokens_used(output) == 1234567

    def test_no_tokens_in_output(self):
        """Should return None when no tokens info found."""
        output = "some output without token info"
        assert extract_tokens_used(output) is None

    def test_empty_output(self):
        """Should return None for empty output."""
        assert extract_tokens_used("") is None


class TestExtractBaseBranch:
    """Tests for extract_base_branch function."""

    def test_base_main(self):
        """Should extract main as base branch."""
        command = "codex review --base main"
        assert extract_base_branch(command) == "main"

    def test_base_with_origin(self):
        """Should extract origin/main as base branch."""
        command = "codex review --base origin/main"
        assert extract_base_branch(command) == "origin/main"

    def test_base_at_end(self):
        """Should extract base when it's the last argument."""
        command = "codex review --uncommitted --base develop"
        assert extract_base_branch(command) == "develop"

    def test_no_base(self):
        """Should return None when no base is specified."""
        command = "codex review --uncommitted"
        assert extract_base_branch(command) is None

    def test_base_with_slash(self):
        """Should handle branch names with slashes."""
        command = "codex review --base feature/my-branch"
        assert extract_base_branch(command) == "feature/my-branch"


class TestIsCodexReviewCommand:
    """Tests for is_codex_review_command function."""

    def test_codex_review_basic(self):
        """Should detect basic codex review command."""
        assert is_codex_review_command("codex review")

    def test_codex_review_with_base(self):
        """Should detect codex review with --base option."""
        assert is_codex_review_command("codex review --base main")

    def test_codex_review_with_uncommitted(self):
        """Should detect codex review with --uncommitted option."""
        assert is_codex_review_command("codex review --uncommitted")

    def test_not_codex_command(self):
        """Should not match non-codex commands."""
        assert not is_codex_review_command("git status")
        assert not is_codex_review_command("npm run test")

    def test_codex_review_in_quotes(self):
        """Should not match codex review inside quoted strings."""
        assert not is_codex_review_command('echo "codex review"')

    def test_empty_command(self):
        """Should return False for empty command."""
        assert not is_codex_review_command("")
        assert not is_codex_review_command("   ")


class TestParseFileLineComment:
    """Tests for parse_file_line_comment function."""

    def test_file_line_message_format(self):
        """Should parse file:line: message format."""
        result = parse_file_line_comment("src/app.ts:10: Consider using const")
        assert result is not None
        assert result["file_path"] == "src/app.ts"
        assert result["line_number"] == 10
        assert result["body"] == "Consider using const"

    def test_file_line_column_message_format(self):
        """Should parse file:line:column: message format."""
        result = parse_file_line_comment("src/app.ts:10:5: Consider using const")
        assert result is not None
        assert result["file_path"] == "src/app.ts"
        assert result["line_number"] == 10
        assert result["body"] == "Consider using const"

    def test_file_with_line_in_parens(self):
        """Should parse file (line N): message format."""
        result = parse_file_line_comment("src/component.tsx (line 25): This could be improved")
        assert result is not None
        assert result["file_path"] == "src/component.tsx"
        assert result["line_number"] == 25
        assert result["body"] == "This could be improved"

    def test_file_hash_line_format(self):
        """Should parse file#LN: message format."""
        result = parse_file_line_comment("src/utils.ts#L42: Missing type annotation")
        assert result is not None
        assert result["file_path"] == "src/utils.ts"
        assert result["line_number"] == 42
        assert result["body"] == "Missing type annotation"

    def test_no_match(self):
        """Should return None for non-matching lines."""
        assert parse_file_line_comment("This is a general comment") is None
        assert parse_file_line_comment("") is None
        assert parse_file_line_comment("   ") is None


class TestParseJsonOutput:
    """Tests for parse_json_output function."""

    def test_json_array_with_body(self):
        """Should parse JSON array with body field."""
        output = json.dumps(
            [
                {"file": "src/app.ts", "line": 10, "body": "Consider using const"},
                {"file": "src/utils.ts", "line": 20, "body": "Missing type"},
            ]
        )
        result = parse_json_output(output)
        assert len(result) == 2
        assert result[0]["file_path"] == "src/app.ts"
        assert result[0]["line_number"] == 10
        assert result[0]["body"] == "Consider using const"

    def test_json_array_with_message(self):
        """Should parse JSON array with message field (not body)."""
        output = json.dumps(
            [
                {"file": "src/app.ts", "line": 10, "message": "Consider using const"},
            ]
        )
        result = parse_json_output(output)
        assert len(result) == 1
        assert result[0]["body"] == "Consider using const"

    def test_json_array_with_comment(self):
        """Should parse JSON array with comment field."""
        output = json.dumps(
            [
                {"file": "src/app.ts", "line": 10, "comment": "Consider using const"},
            ]
        )
        result = parse_json_output(output)
        assert len(result) == 1
        assert result[0]["body"] == "Consider using const"

    def test_json_array_with_text(self):
        """Should parse JSON array with text field."""
        output = json.dumps(
            [
                {"file": "src/app.ts", "line": 10, "text": "Consider using const"},
            ]
        )
        result = parse_json_output(output)
        assert len(result) == 1
        assert result[0]["body"] == "Consider using const"

    def test_json_object_with_comments(self):
        """Should parse JSON object with comments array."""
        output = json.dumps(
            {
                "comments": [
                    {"file": "src/app.ts", "line": 10, "body": "Consider using const"},
                ]
            }
        )
        result = parse_json_output(output)
        assert len(result) == 1
        assert result[0]["file_path"] == "src/app.ts"

    def test_json_single_object(self):
        """Should parse single JSON object with body."""
        output = json.dumps({"file": "src/app.ts", "line": 10, "body": "Consider using const"})
        result = parse_json_output(output)
        assert len(result) == 1
        assert result[0]["body"] == "Consider using const"

    def test_invalid_json(self):
        """Should return empty list for invalid JSON."""
        result = parse_json_output("not valid json")
        assert result == []


class TestParseCodexReviewOutput:
    """Tests for parse_codex_review_output function."""

    def test_line_by_line_parsing(self):
        """Should parse line-by-line output."""
        output = """
src/app.ts:10: Consider using const instead of let
src/utils.ts:20: Missing type annotation
"""
        result = parse_codex_review_output(output)
        assert len(result) == 2
        assert result[0]["file_path"] == "src/app.ts"
        assert result[0]["line_number"] == 10
        assert result[1]["file_path"] == "src/utils.ts"

    def test_multiline_comment(self):
        """Should handle multiline comments."""
        output = """
src/app.ts:10: Consider using const
This is a continuation of the comment
with multiple lines

src/utils.ts:20: Another comment
"""
        result = parse_codex_review_output(output)
        assert len(result) == 2
        assert "continuation" in result[0]["body"]
        assert "multiple lines" in result[0]["body"]

    def test_json_output_priority(self):
        """Should prefer JSON parsing when valid."""
        output = json.dumps(
            [
                {"file": "src/app.ts", "line": 10, "body": "Comment from JSON"},
            ]
        )
        result = parse_codex_review_output(output)
        assert len(result) == 1
        assert result[0]["body"] == "Comment from JSON"

    def test_empty_output(self):
        """Should return empty list for empty output."""
        result = parse_codex_review_output("")
        assert result == []

    def test_no_comments(self):
        """Should return empty list when no comments found."""
        output = """
Review complete.
No issues found.
"""
        result = parse_codex_review_output(output)
        assert result == []
