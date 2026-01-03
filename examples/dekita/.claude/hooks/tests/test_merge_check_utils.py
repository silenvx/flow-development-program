#!/usr/bin/env python3
"""Tests for merge-check.py - utils module."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "merge-check.py"

# Import submodules for correct mock targets after modularization (Issue #1756)
# These imports enable tests to mock functions at their actual definition locations


def run_hook(input_data: dict) -> dict | None:
    """Run the hook with given input and return the result.

    Returns None if no output (silent approval per design principle).
    """
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        return None  # Silent approval
    return json.loads(result.stdout)


class TestTruncateBody:
    """Tests for truncate_body helper function."""

    def setup_method(self):
        """Load the module."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_short_body_unchanged(self):
        """Should return short body unchanged."""
        body = "Short text"
        result = self.module.truncate_body(body)
        assert result == body

    def test_exact_length_unchanged(self):
        """Should return body unchanged if exactly max_length."""
        body = "x" * 100
        result = self.module.truncate_body(body)
        assert result == body

    def test_truncate_long_body(self):
        """Should truncate long body with ellipsis."""
        body = "x" * 150
        result = self.module.truncate_body(body)
        assert len(result) == 103  # 100 + "..."
        assert result.endswith("...")
        assert result[:100] == "x" * 100

    def test_custom_max_length(self):
        """Should support custom max_length parameter."""
        body = "x" * 50
        result = self.module.truncate_body(body, max_length=30)
        assert len(result) == 33  # 30 + "..."
        assert result.endswith("...")

    def test_empty_body(self):
        """Should handle empty body."""
        result = self.module.truncate_body("")
        assert result == ""


class TestStripCodeBlocks:
    """Tests for strip_code_blocks helper function (Issue #797).

    This function removes code blocks from text to prevent false positives
    when detecting keywords like "-- Claude Code" that may appear in code
    examples or Copilot suggestions.
    """

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_strip_fenced_code_block(self):
        """Should strip fenced code blocks (``` ... ```)."""
        text = """Some text before
```python
def hello():
    print("Hello")
```
Some text after"""
        result = self.module.strip_code_blocks(text)
        assert "def hello()" not in result
        assert "print" not in result
        assert "Some text before" in result
        assert "Some text after" in result

    def test_strip_inline_code(self):
        """Should strip inline code (`...`)."""
        text = "Use `-- Claude Code` signature for responses."
        result = self.module.strip_code_blocks(text)
        assert "-- Claude Code" not in result
        assert "Use" in result
        assert "signature for responses." in result

    def test_strip_multiline_code_block(self):
        """Should strip code blocks spanning multiple lines."""
        text = """Comment before
```
-- Claude Code
False positive: This is just a code example
```
Comment after"""
        result = self.module.strip_code_blocks(text)
        assert "-- Claude Code" not in result
        assert "False positive" not in result
        assert "Comment before" in result
        assert "Comment after" in result

    def test_strip_code_block_with_language(self):
        """Should strip code blocks with language specifier."""
        text = """```bash
gh api /repos/:owner/:repo/pulls/comments/{id} -X PATCH \\
  -f body='False positive: #797\n\n-- Claude Code'
```"""
        result = self.module.strip_code_blocks(text)
        assert "False positive" not in result
        assert "-- Claude Code" not in result

    def test_preserve_text_outside_code_blocks(self):
        """Should preserve text outside code blocks."""
        text = """This is a real dismissal. False positive: The existing code is correct.

```
This is just an example of a false positive comment
```

-- Claude Code"""
        result = self.module.strip_code_blocks(text)
        # The real dismissal text should be preserved
        assert "This is a real dismissal" in result
        assert "-- Claude Code" in result  # The signature outside code block should remain

    def test_empty_text(self):
        """Should handle empty text."""
        result = self.module.strip_code_blocks("")
        assert result == ""

    def test_no_code_blocks(self):
        """Should return text unchanged when no code blocks."""
        text = "Just plain text without any code"
        result = self.module.strip_code_blocks(text)
        assert result == text

    def test_multiple_code_blocks(self):
        """Should strip multiple code blocks."""
        text = """Before
```
Block 1 with -- Claude Code
```
Middle
```python
Block 2 with False positive
```
After"""
        result = self.module.strip_code_blocks(text)
        assert "Block 1" not in result
        assert "Block 2" not in result
        assert "Before" in result
        assert "Middle" in result
        assert "After" in result

    def test_multiple_inline_codes(self):
        """Should strip multiple inline codes."""
        text = "Use `foo` and `-- Claude Code` in your code"
        result = self.module.strip_code_blocks(text)
        assert "foo" not in result
        assert "-- Claude Code" not in result
        assert "Use" in result
        assert "and" in result

    def test_unmatched_backtick_does_not_cross_lines(self):
        """Should not match across lines when there's an unmatched backtick.

        Markdown inline code cannot span multiple lines, so the regex should
        not match text across newlines even if there's an unmatched backtick.
        """
        text = """This has a ` in the middle
and later: Use -- Claude Code"""
        result = self.module.strip_code_blocks(text)
        # Should NOT remove "-- Claude Code" because it's not in a valid code block
        assert "-- Claude Code" in result
        assert "in the middle" in result


class TestStripOptionValues:
    """Tests for strip_option_values helper function (Issue #2384, Copilot review)."""

    def setup_method(self):
        """Load the module."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_strip_double_quoted_body(self):
        """Should strip double-quoted --body value."""
        cmd = 'gh pr merge 123 --body "Some text with --admin inside"'
        result = self.module.strip_option_values(cmd)
        assert '--body ""' in result
        assert "--admin" not in result

    def test_strip_single_quoted_body(self):
        """Should strip single-quoted --body value."""
        cmd = "gh pr merge 123 --body 'Some text with --auto inside'"
        result = self.module.strip_option_values(cmd)
        assert "--body ''" in result
        assert "--auto" not in result

    def test_strip_nested_quotes_in_body(self):
        """Should strip body with nested quotes (Copilot review case)."""
        cmd = """gh pr merge 123 --body "The '--admin' option should be avoided" """
        result = self.module.strip_option_values(cmd)
        assert '--body ""' in result
        assert "'--admin'" not in result

    def test_preserve_standalone_quoted_option(self):
        """Should preserve standalone quoted options like '--admin'."""
        cmd = "gh pr merge 123 '--admin'"
        result = self.module.strip_option_values(cmd)
        assert "'--admin'" in result

    def test_preserve_unquoted_option(self):
        """Should preserve unquoted --admin option."""
        cmd = "gh pr merge 123 --admin"
        result = self.module.strip_option_values(cmd)
        assert "--admin" in result

    def test_strip_subject_value(self):
        """Should strip --subject value (gh pr merge uses --subject, not --title)."""
        cmd = 'gh pr merge 123 --subject "PR with --admin mention"'
        result = self.module.strip_option_values(cmd)
        assert '--subject ""' in result
        assert "--admin" not in result

    def test_strip_t_flag_value(self):
        """Should strip -t value (short for --subject)."""
        cmd = 'gh pr merge 123 -t "Subject with --admin"'
        result = self.module.strip_option_values(cmd)
        assert '-t ""' in result
        assert "--admin" not in result

    def test_strip_m_value(self):
        """Should strip -m value."""
        cmd = 'gh pr merge 123 -m "Message with --auto"'
        result = self.module.strip_option_values(cmd)
        assert '-m ""' in result
        assert "--auto" not in result

    def test_multiple_option_values(self):
        """Should strip multiple option values."""
        cmd = 'gh pr merge 123 --body "body --admin" --subject "subject --auto"'
        result = self.module.strip_option_values(cmd)
        assert '--body ""' in result
        assert '--subject ""' in result
        assert "--admin" not in result
        assert "--auto" not in result

    def test_strip_body_equals_syntax(self):
        """Should strip --body= value (equals-separated syntax)."""
        cmd = 'gh pr merge 123 --body="text with --admin inside"'
        result = self.module.strip_option_values(cmd)
        assert '--body=""' in result
        assert "--admin" not in result

    def test_strip_b_flag_value(self):
        """Should strip -b value (short for --body)."""
        cmd = 'gh pr merge 123 -b "The --admin option"'
        result = self.module.strip_option_values(cmd)
        assert '-b ""' in result
        assert "--admin" not in result

    def test_strip_b_flag_equals_syntax(self):
        """Should strip -b= value (equals-separated syntax)."""
        cmd = 'gh pr merge 123 -b="--auto handling"'
        result = self.module.strip_option_values(cmd)
        assert '-b=""' in result
        assert "--auto" not in result
