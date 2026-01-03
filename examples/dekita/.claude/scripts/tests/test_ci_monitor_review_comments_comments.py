#!/usr/bin/env python3
"""Unit tests for review comment handling in ci_monitor.review_comments module.

Covers:
- Review comment warning feature
- Comment classification
- Comment printing
- Code block stripping
- Comment body normalization
"""

import sys
from pathlib import Path
from unittest.mock import patch

# Add scripts directory to path so we can import ci_monitor package
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the ci_monitor package for attribute access (Issue #2624)
import ci_monitor

# Import directly from ci_monitor package (Issue #2624)
from ci_monitor import (
    CheckStatus,
    MergeState,
    PRState,
)


class TestReviewCommentWarning:
    """Tests for review comment warning feature (Issue #258).

    When review comments exist, the output should:
    1. Display a warning message prompting action
    2. Include comment count in the result message
    3. Include requires_action flag in JSON output
    """

    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_result_message_includes_unresolved_thread_count(
        self,
        mock_get_pr_state,
        mock_get_comments,
        mock_sleep,
        mock_get_threads,
        mock_codex_pending,
    ):
        """Test that result message includes unresolved thread count (Issue #1171)."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 2:
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],  # Review complete
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            else:
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_get_comments.return_value = [
            {"path": "test.py", "line": 10, "body": "Fix this", "user": "Copilot"},
            {"path": "test.py", "line": 20, "body": "Also fix", "user": "Copilot"},
        ]
        # Issue #1171: Only unresolved threads count towards the message
        mock_get_threads.return_value = [
            {
                "id": "thread1",
                "path": "test.py",
                "line": 10,
                "body": "Fix this",
                "author": "Copilot",
            },
            {
                "id": "thread2",
                "path": "test.py",
                "line": 20,
                "body": "Also fix",
                "author": "Copilot",
            },
        ]
        mock_codex_pending.return_value = False

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        # Issue #1171: Message now counts unresolved threads, not all comments
        assert "2 unresolved thread(s) to address" in result.message

    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_no_warning_when_all_threads_resolved(
        self,
        mock_get_pr_state,
        mock_get_comments,
        mock_sleep,
        mock_get_threads,
        mock_codex_pending,
    ):
        """Test that no warning is shown when all review threads are resolved (Issue #1171)."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        # Comments exist but all threads are resolved
        mock_get_comments.return_value = [
            {"path": "test.py", "line": 10, "body": "Fix this", "user": "Copilot"},
            {"path": "test.py", "line": 20, "body": "Also fix", "user": "Copilot"},
        ]
        mock_get_threads.return_value = []  # All resolved
        mock_codex_pending.return_value = False

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        # Issue #1171: No unresolved threads means no warning in message
        assert "unresolved" not in result.message.lower()
        assert "comment(s) to address" not in result.message

    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_no_comment_count_when_no_comments(
        self,
        mock_get_pr_state,
        mock_get_comments,
        mock_sleep,
        mock_get_threads,
        mock_codex_pending,
    ):
        """Test that result message doesn't include comment count when no comments."""
        mock_get_pr_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=[],
                check_status=CheckStatus.SUCCESS,
            ),
            None,
        )
        mock_get_comments.return_value = []
        mock_get_threads.return_value = []
        mock_codex_pending.return_value = False

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert "comment(s) to address" not in result.message


class TestClassifyReviewComments:
    """Test the classify_review_comments function."""

    @patch("ci_monitor.review_comments.get_pr_changed_files")
    @patch("ci_monitor.main_loop.get_review_comments")
    def test_classifies_in_scope_comments(self, mock_get_comments, mock_get_files):
        """Test that comments on changed files are classified as in-scope."""
        mock_get_files.return_value = {"src/file1.ts", "src/file2.ts"}
        mock_get_comments.return_value = [
            {"path": "src/file1.ts", "line": 10, "body": "fix this"},
            {"path": "src/file2.ts", "line": 20, "body": "and this"},
        ]

        result = ci_monitor.classify_review_comments("123")

        assert len(result.in_scope) == 2
        assert len(result.out_of_scope) == 0

    @patch("ci_monitor.review_comments.get_pr_changed_files")
    @patch("ci_monitor.main_loop.get_review_comments")
    def test_classifies_out_of_scope_comments(self, mock_get_comments, mock_get_files):
        """Test that comments on unchanged files are classified as out-of-scope."""
        mock_get_files.return_value = {"src/file1.ts"}
        mock_get_comments.return_value = [
            {"path": "src/file1.ts", "line": 10, "body": "in scope"},
            {"path": "other/file.ts", "line": 20, "body": "out of scope"},
        ]

        result = ci_monitor.classify_review_comments("123")

        assert len(result.in_scope) == 1
        assert len(result.out_of_scope) == 1
        assert result.in_scope[0]["path"] == "src/file1.ts"
        assert result.out_of_scope[0]["path"] == "other/file.ts"

    @patch("ci_monitor.review_comments.get_pr_changed_files")
    @patch("ci_monitor.main_loop.get_review_comments")
    def test_treats_all_as_in_scope_when_file_lookup_fails(self, mock_get_comments, mock_get_files):
        """Test that all comments are in-scope when file lookup fails (safe default)."""
        mock_get_files.return_value = None  # Failure case
        mock_get_comments.return_value = [
            {"path": "file1.ts", "line": 10, "body": "comment 1"},
            {"path": "file2.ts", "line": 20, "body": "comment 2"},
        ]

        result = ci_monitor.classify_review_comments("123")

        assert len(result.in_scope) == 2
        assert len(result.out_of_scope) == 0

    @patch("ci_monitor.review_comments.get_pr_changed_files")
    @patch("ci_monitor.main_loop.get_review_comments")
    def test_handles_empty_comments(self, mock_get_comments, mock_get_files):
        """Test that empty comment list returns empty classification."""
        mock_get_files.return_value = {"file1.ts"}
        mock_get_comments.return_value = []

        result = ci_monitor.classify_review_comments("123")

        assert len(result.in_scope) == 0
        assert len(result.out_of_scope) == 0


class TestPrintComment:
    """Test the print_comment helper function."""

    @patch("builtins.print")
    def test_prints_short_comment(self, mock_print):
        """Test printing a comment with body shorter than 100 chars."""
        comment = {"path": "file.ts", "line": 42, "user": "copilot", "body": "Short body"}

        ci_monitor.print_comment(comment)

        mock_print.assert_any_call("  [file.ts:42] (copilot)")
        mock_print.assert_any_call("    Short body")

    @patch("builtins.print")
    def test_truncates_long_comment(self, mock_print):
        """Test that long comment body is truncated with ellipsis."""
        long_body = "x" * 150  # More than 100 chars
        comment = {"path": "file.ts", "line": 42, "user": "copilot", "body": long_body}

        ci_monitor.print_comment(comment)

        # Check that truncation happened
        calls = [str(c) for c in mock_print.call_args_list]
        assert any("..." in c for c in calls)


# =============================================================================
# Tests for Issue #848: Acceptance Criteria Check Functions
# =============================================================================


class TestStripCodeBlocks:
    """Tests for strip_code_blocks function."""

    def test_fenced_code_block_removal(self):
        """Test removal of fenced code blocks (```)."""
        text = """Some text
```python
- [ ] checkbox inside code
print("hello")
```
After code"""
        result = ci_monitor.strip_code_blocks(text)
        assert "checkbox inside code" not in result
        assert "Some text" in result
        assert "After code" in result

    def test_inline_code_removal(self):
        """Test removal of inline code (`...`)."""
        text = "Use `- [ ] task` syntax for checkboxes"
        result = ci_monitor.strip_code_blocks(text)
        assert "- [ ] task" not in result
        assert "Use" in result
        assert "syntax for checkboxes" in result

    def test_mixed_code_blocks(self):
        """Test removal of both fenced and inline code blocks."""
        text = """# Title
- [ ] Real checkbox
Use `- [ ] example` for demo
```
- [ ] In code block
```
- [ ] Another real one"""
        result = ci_monitor.strip_code_blocks(text)
        assert "Real checkbox" in result
        assert "Another real one" in result
        assert "In code block" not in result
        assert "example" not in result

    def test_empty_text(self):
        """Test with empty text."""
        result = ci_monitor.strip_code_blocks("")
        assert result == ""

    def test_no_code_blocks(self):
        """Test text without any code blocks."""
        text = "Plain text without code"
        result = ci_monitor.strip_code_blocks(text)
        assert result == text

    def test_multiple_fenced_blocks(self):
        """Test multiple fenced code blocks."""
        text = """```
block1
```
middle
```js
block2
```"""
        result = ci_monitor.strip_code_blocks(text)
        assert "block1" not in result
        assert "block2" not in result
        assert "middle" in result


class TestNormalizeCommentBody:
    """Tests for normalize_comment_body function (Issue #1372)."""

    def test_removes_line_number_references(self):
        """Test that line number references are removed."""
        body = "Fix the issue on line 5"
        result = ci_monitor.normalize_comment_body(body)
        assert "5" not in result
        assert "line" not in result.lower()
        assert "Fix the issue" in result

    def test_removes_line_range_references(self):
        """Test that line range references are removed."""
        body = "This affects lines 10-20"
        result = ci_monitor.normalize_comment_body(body)
        assert "10" not in result
        assert "20" not in result
        assert "This affects" in result

    def test_removes_L_format_references(self):
        """Test that L-format line references are removed."""
        body = "See the issue (L123) for details"
        result = ci_monitor.normalize_comment_body(body)
        assert "(L123)" not in result
        assert "See the issue for details" in result

    def test_removes_L_range_references(self):
        """Test that L-range references are removed."""
        body = "Check L50-L60 in the file"
        result = ci_monitor.normalize_comment_body(body)
        assert "L50" not in result
        assert "L60" not in result
        assert result == "Check in the file"

    def test_normalizes_whitespace(self):
        """Test that multiple spaces are collapsed."""
        body = "Multiple   spaces   here"
        result = ci_monitor.normalize_comment_body(body)
        assert "  " not in result
        assert result == "Multiple spaces here"

    def test_preserves_non_line_content(self):
        """Test that content without line references is preserved."""
        body = "Module `common` is imported with both `import` and `import from`"
        result = ci_monitor.normalize_comment_body(body)
        assert result == body

    def test_returns_empty_for_line_only_content(self):
        """Test that line-only comments result in empty string.

        This is important to prevent hash collisions when comments
        contain only line number references with no other content.
        """
        # Single L-format reference
        assert ci_monitor.normalize_comment_body("L123") == ""
        # L-range reference
        assert ci_monitor.normalize_comment_body("L50-L60") == ""
        # "line N" format
        assert ci_monitor.normalize_comment_body("line 42") == ""
        # "on line N" format
        assert ci_monitor.normalize_comment_body("on line 5") == ""
        # Multiple line references only
        assert ci_monitor.normalize_comment_body("L10 and L20") == "and"

    def test_handles_mixed_content(self):
        """Test body with multiple line references and other content."""
        body = "Fix the bug on line 42 and also check L100-L110 for related issues"
        result = ci_monitor.normalize_comment_body(body)
        assert "42" not in result
        assert "100" not in result
        assert "110" not in result
        assert "bug" in result
        assert "related issues" in result
