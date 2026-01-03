#!/usr/bin/env python3
"""Unit tests for ci_monitor.review_comments module."""

import json
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


class TestGetResolvedThreadHashes:
    """Tests for get_resolved_thread_hashes function (Issue #839)."""

    def test_returns_hashes_of_resolved_threads(self):
        """解決済みスレッドのボディハッシュが返されることを確認。

        Issue #1329: 実装がページネーションヘルパーを使用するようにリファクタリングされたため、
        run_gh_commandではなくfetch_all_review_threadsを直接モック。
        """
        mock_threads = [
            {
                "isResolved": True,
                "comments": {"nodes": [{"body": "Fix this bug", "path": "src/main.py"}]},
            },
            {
                "isResolved": False,
                "comments": {"nodes": [{"body": "Another issue", "path": "src/other.py"}]},
            },
            {
                "isResolved": True,
                "comments": {"nodes": [{"body": "Type error here", "path": "src/types.py"}]},
            },
        ]

        with (
            patch.object(ci_monitor, "get_repo_info", return_value=("owner", "repo")),
            patch.object(ci_monitor, "fetch_all_review_threads", return_value=mock_threads),
        ):
            result = ci_monitor.get_resolved_thread_hashes("123")
            # Should have 2 hashes (only resolved threads)
            assert len(result) == 2
            assert isinstance(result, set)

    def test_returns_empty_set_on_failure(self):
        """API失敗時に空のセットが返されることを確認。

        Issue #1329: get_repo_info失敗時は空のセットを返す。
        """
        with patch.object(ci_monitor, "get_repo_info", return_value=None):
            result = ci_monitor.get_resolved_thread_hashes("123")
            assert result == set()

    def test_returns_empty_set_on_api_failure(self):
        """API失敗時に空のセットが返されることを確認。

        Issue #1329: fetch_all_review_threadsはAPI失敗時にNoneを返す。
        """
        with (
            patch.object(ci_monitor, "get_repo_info", return_value=("owner", "repo")),
            patch.object(ci_monitor, "fetch_all_review_threads", return_value=None),
        ):
            result = ci_monitor.get_resolved_thread_hashes("123")
            assert result == set()

    def test_skips_empty_body_or_path(self):
        """空のbodyまたはpathを持つスレッドがスキップされることを確認。

        Issue #869: 空の値によるハッシュ衝突を防止。
        Issue #1329: fetch_all_review_threadsを直接モック。
        """
        mock_threads = [
            {
                "isResolved": True,
                "comments": {"nodes": [{"body": "Valid comment", "path": "src/main.py"}]},
            },
            {
                "isResolved": True,
                "comments": {
                    "nodes": [{"body": "", "path": "src/empty.py"}]  # Empty body
                },
            },
            {
                "isResolved": True,
                "comments": {
                    "nodes": [{"body": "Some comment", "path": ""}]  # Empty path
                },
            },
        ]

        with (
            patch.object(ci_monitor, "get_repo_info", return_value=("owner", "repo")),
            patch.object(ci_monitor, "fetch_all_review_threads", return_value=mock_threads),
        ):
            result = ci_monitor.get_resolved_thread_hashes("123")
            # Should have only 1 hash (threads with empty body/path are skipped)
            assert len(result) == 1


class TestResolveThreadById:
    """Tests for resolve_thread_by_id function (Issue #839)."""

    def test_successful_resolve(self):
        """Test successful thread resolution."""
        with patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh:
            mock_gh.return_value = (
                True,
                '{"data": {"resolveReviewThread": {"thread": {"isResolved": true}}}}',
                "",
            )
            result = ci_monitor.resolve_thread_by_id("thread_123")
            assert result is True

    def test_failed_resolve(self):
        """Test failed thread resolution."""
        with patch.object(ci_monitor, "run_gh_command_with_error") as mock_gh:
            mock_gh.return_value = (False, "", "")
            result = ci_monitor.resolve_thread_by_id("thread_123")
            assert result is False


class TestAutoResolveDuplicateThreads:
    """Tests for auto_resolve_duplicate_threads function (Issue #839)."""

    def test_returns_zero_with_empty_hashes(self):
        """Test that (0, empty set) is returned when pre_rebase_hashes is empty.

        Issue #1097: Updated to match tuple return type.
        """
        count, resolved_hashes = ci_monitor.auto_resolve_duplicate_threads("123", set())
        assert count == 0
        assert resolved_hashes == set()

    def test_resolves_matching_ai_threads(self):
        """Test that matching AI reviewer threads are resolved.

        Issue #1097: Updated to match tuple return type.
        """
        import hashlib

        # Create a hash that will match
        content = "src/main.py:Fix this bug"
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
        pre_rebase_hashes = {content_hash}

        mock_threads = [
            {
                "id": "thread_123",
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "body": "Fix this bug",
                            "path": "src/main.py",
                            "author": {"login": "copilot[bot]"},
                        }
                    ]
                },
            }
        ]

        with (
            patch.object(ci_monitor, "get_repo_info") as mock_repo,
            patch.object(ci_monitor, "fetch_all_review_threads") as mock_fetch,
            patch.object(ci_monitor, "resolve_thread_by_id") as mock_resolve,
        ):
            mock_repo.return_value = ("owner", "repo")
            mock_fetch.return_value = mock_threads
            mock_resolve.return_value = True  # Mock successful resolution
            count, resolved_hashes = ci_monitor.auto_resolve_duplicate_threads(
                "123", pre_rebase_hashes
            )
            assert count == 1
            assert content_hash in resolved_hashes
            mock_resolve.assert_called_once_with("thread_123")

    def test_skips_non_ai_threads(self):
        """Test that non-AI threads are not auto-resolved.

        Issue #1097: Updated to match tuple return type.
        """
        import hashlib

        content = "src/main.py:Fix this bug"
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
        pre_rebase_hashes = {content_hash}

        mock_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "id": "thread_123",
                                    "isResolved": False,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "body": "Fix this bug",
                                                "path": "src/main.py",
                                                "author": {"login": "humanreviewer"},
                                            }
                                        ]
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }

        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.side_effect = [
                (True, "owner\nrepo"),
                (True, json.dumps(mock_response)),
            ]
            count, resolved_hashes = ci_monitor.auto_resolve_duplicate_threads(
                "123", pre_rebase_hashes
            )
            assert count == 0
            assert resolved_hashes == set()

    def test_skips_non_matching_threads(self):
        """Test that non-matching threads are not auto-resolved.

        Issue #1097: Updated to match tuple return type.
        """
        pre_rebase_hashes = {"different_hash_12345"}

        mock_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "id": "thread_123",
                                    "isResolved": False,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "body": "Different comment",
                                                "path": "src/other.py",
                                                "author": {"login": "copilot[bot]"},
                                            }
                                        ]
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }

        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.side_effect = [
                (True, "owner\nrepo"),
                (True, json.dumps(mock_response)),
            ]
            count, resolved_hashes = ci_monitor.auto_resolve_duplicate_threads(
                "123", pre_rebase_hashes
            )
            assert count == 0
            assert resolved_hashes == set()

    def test_skips_resolved_threads(self):
        """Test that already resolved threads are skipped.

        Issue #869: Ensure isResolved=True threads are not processed.
        Issue #1097: Updated to match tuple return type.
        """
        import hashlib

        # Create a hash that would match if the thread were unresolved
        content = "src/main.py:Fix this bug"
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
        pre_rebase_hashes = {content_hash}

        mock_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "thread_123",
                                    "isResolved": True,  # Already resolved
                                    "comments": {
                                        "nodes": [
                                            {
                                                "body": "Fix this bug",
                                                "path": "src/main.py",
                                                "author": {"login": "copilot[bot]"},
                                            }
                                        ]
                                    },
                                }
                            ],
                        }
                    }
                }
            }
        }

        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.side_effect = [
                (True, "owner\nrepo"),
                (True, json.dumps(mock_response)),
            ]
            count, resolved_hashes = ci_monitor.auto_resolve_duplicate_threads(
                "123", pre_rebase_hashes
            )
            # Should return 0 because the thread is already resolved
            assert count == 0
            assert resolved_hashes == set()

    def test_skips_empty_body_or_path(self):
        """Test that threads with empty body or path are skipped.

        Issue #869: Prevent hash collisions from empty values.
        Issue #1097: Updated to match tuple return type.
        """
        import hashlib

        # Create a hash for empty content - this should not match
        content = ":"  # Empty path and body would produce this
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
        pre_rebase_hashes = {content_hash}

        mock_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "thread_empty_body",
                                    "isResolved": False,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "body": "",  # Empty body
                                                "path": "src/main.py",
                                                "author": {"login": "copilot[bot]"},
                                            }
                                        ]
                                    },
                                },
                                {
                                    "id": "thread_empty_path",
                                    "isResolved": False,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "body": "Some comment",
                                                "path": "",  # Empty path
                                                "author": {"login": "codex[bot]"},
                                            }
                                        ]
                                    },
                                },
                            ],
                        }
                    }
                }
            }
        }

        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.side_effect = [
                (True, "owner\nrepo"),
                (True, json.dumps(mock_response)),
            ]
            count, resolved_hashes = ci_monitor.auto_resolve_duplicate_threads(
                "123", pre_rebase_hashes
            )
            # Should return 0 because empty body/path threads are skipped
            assert count == 0
            assert resolved_hashes == set()


class TestFilterDuplicateComments:
    """Tests for filter_duplicate_comments function (Issue #1097)."""

    def test_returns_all_when_no_duplicates(self):
        """Test all comments are returned when duplicate_hashes is empty."""
        comments = [
            {"path": "src/main.py", "body": "Fix this"},
            {"path": "src/other.py", "body": "Update this"},
        ]
        result = ci_monitor.filter_duplicate_comments(comments, set())
        assert result == comments

    def test_filters_matching_hashes(self):
        """Test AI comments with matching hashes are filtered out."""
        import hashlib

        # Create a hash that matches one comment
        content = "src/main.py:Fix this"
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]

        comments = [
            # AI comment - should be filtered (matching hash)
            {"path": "src/main.py", "body": "Fix this", "user": "copilot[bot]"},
            # AI comment - should remain (non-matching hash)
            {"path": "src/other.py", "body": "Update this", "user": "copilot[bot]"},
        ]
        result = ci_monitor.filter_duplicate_comments(comments, {content_hash})
        assert len(result) == 1
        assert result[0]["path"] == "src/other.py"

    def test_keeps_comments_without_path_or_body(self):
        """Test comments missing path or body are kept."""
        comments = [
            {"body": "No path comment"},  # Missing path
            {"path": "src/main.py"},  # Missing body
            {},  # Empty comment
        ]
        result = ci_monitor.filter_duplicate_comments(comments, {"some_hash"})
        assert len(result) == 3

    def test_keeps_human_comments_even_with_matching_hash(self):
        """Test human comments are never filtered, even if hash matches.

        Issue #1097: Ensure human reviewer comments are always kept
        to prevent accidentally hiding actionable feedback.
        """
        import hashlib

        # Create a hash that matches
        content = "src/main.py:Fix this bug"
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]

        comments = [
            # Human comment with matching content - should NOT be filtered
            {"path": "src/main.py", "body": "Fix this bug", "user": "humanreviewer"},
            # AI comment with matching content - should be filtered
            {"path": "src/main.py", "body": "Fix this bug", "user": "copilot[bot]"},
            # Another AI comment with matching content - should be filtered
            {"path": "src/main.py", "body": "Fix this bug", "user": "chatgpt-codex-connector"},
        ]
        result = ci_monitor.filter_duplicate_comments(comments, {content_hash})

        # Only the human comment should remain
        assert len(result) == 1
        assert result[0]["user"] == "humanreviewer"


class TestFetchReviewCommentsRest:
    """Tests for fetch_review_comments_rest function (Issue #1318)."""

    def test_returns_comments_on_success(self):
        """Test successful REST API comment fetch."""
        mock_response = json.dumps(
            [
                {"id": 123, "path": "file.py", "line": 10, "body": "Fix this", "author": "user1"},
                {"id": 456, "path": "file.py", "line": 20, "body": "Update", "author": "user2"},
            ]
        )
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, mock_response)
            result = ci_monitor.fetch_review_comments_rest("owner", "repo", "123")
            assert result is not None
            assert len(result) == 2
            assert result[0]["id"] == 123
            assert result[0]["is_rest_fallback"] is True
            assert result[0]["isResolved"] is False

    def test_returns_none_on_failure(self):
        """Test returns None when REST API fails."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (False, "Error")
            result = ci_monitor.fetch_review_comments_rest("owner", "repo", "123")
            assert result is None

    def test_returns_none_on_invalid_json(self):
        """Test returns None on JSON parse error."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, "not valid json")
            result = ci_monitor.fetch_review_comments_rest("owner", "repo", "123")
            assert result is None

    def test_handles_empty_response(self):
        """Test handles empty comment list."""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, "[]")
            result = ci_monitor.fetch_review_comments_rest("owner", "repo", "123")
            assert result == []

    def test_handles_paginated_response(self):
        """Test handles multi-page REST API response."""
        # --paginate returns multiple JSON arrays, one per line
        mock_response = """[{"id": 1, "path": "a.py", "line": 1, "body": "a", "author": "u1"}]
[{"id": 2, "path": "b.py", "line": 2, "body": "b", "author": "u2"}]"""
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, mock_response)
            result = ci_monitor.fetch_review_comments_rest("owner", "repo", "123")
            assert result is not None
            assert len(result) == 2
            assert result[0]["id"] == 1
            assert result[1]["id"] == 2

    def test_handles_whitespace_only_lines(self):
        """Test handles responses with empty and whitespace-only lines."""
        # --paginate may include empty lines or whitespace between pages
        mock_response = """[{"id": 1, "path": "a.py", "line": 1, "body": "a", "author": "u1"}]


[{"id": 2, "path": "b.py", "line": 2, "body": "b", "author": "u2"}]
  """
        with patch.object(ci_monitor, "run_gh_command") as mock_gh:
            mock_gh.return_value = (True, mock_response)
            result = ci_monitor.fetch_review_comments_rest("owner", "repo", "123")
            assert result is not None
            assert len(result) == 2
            assert result[0]["id"] == 1
            assert result[1]["id"] == 2


class TestConvertRestCommentsToThreadFormat:
    """Tests for convert_rest_comments_to_thread_format function (Issue #1318)."""

    def test_converts_single_comment(self):
        """Test conversion of single comment to thread format."""
        comments = [
            {
                "id": 123,
                "path": "file.py",
                "line": 10,
                "body": "Fix this",
                "author": "user1",
                "isResolved": False,
            }
        ]
        result = ci_monitor.convert_rest_comments_to_thread_format(comments)
        assert len(result) == 1
        thread = result[0]
        assert thread["id"] == "rest-123"
        assert thread["isResolved"] is False
        assert thread["is_rest_fallback"] is True
        assert thread["comments"]["nodes"][0]["body"] == "Fix this"
        assert thread["comments"]["nodes"][0]["path"] == "file.py"
        assert thread["comments"]["nodes"][0]["author"]["login"] == "user1"

    def test_converts_multiple_comments(self):
        """Test conversion of multiple comments."""
        comments = [
            {"id": 1, "path": "a.py", "line": 1, "body": "a", "author": "u1", "isResolved": False},
            {"id": 2, "path": "b.py", "line": 2, "body": "b", "author": "u2", "isResolved": False},
        ]
        result = ci_monitor.convert_rest_comments_to_thread_format(comments)
        assert len(result) == 2
        assert result[0]["id"] == "rest-1"
        assert result[1]["id"] == "rest-2"

    def test_handles_empty_list(self):
        """Test handles empty comment list."""
        result = ci_monitor.convert_rest_comments_to_thread_format([])
        assert result == []

    def test_handles_missing_fields(self):
        """Test handles comments with missing fields gracefully."""
        comments = [{"id": 123}]  # Missing most fields
        result = ci_monitor.convert_rest_comments_to_thread_format(comments)
        assert len(result) == 1
        thread = result[0]
        assert thread["id"] == "rest-123"
        node = thread["comments"]["nodes"][0]
        assert node["body"] == ""
        assert node["author"]["login"] == "unknown"
        assert node["path"] == ""  # Defaults to empty string
        assert node["line"] is None  # Defaults to None (no default value)


class TestFetchAllReviewThreadsRestFallback:
    """Tests for REST API fallback in fetch_all_review_threads (Issue #1318)."""

    def test_falls_back_to_rest_on_rate_limit(self, capsys):
        """Test falls back to REST API when GraphQL is rate limited."""
        rest_comments = [
            {
                "id": 1,
                "path": "file.py",
                "line": 10,
                "body": "Fix",
                "author": "user",
                "isResolved": False,
            }
        ]
        with (
            patch.object(ci_monitor, "run_gh_command_with_error") as mock_graphql,
            patch.object(ci_monitor, "fetch_review_comments_rest") as mock_rest,
            patch.object(ci_monitor, "print_rate_limit_warning"),
        ):
            # GraphQL fails with rate limit error
            mock_graphql.return_value = (
                False,
                '{"errors": [{"type": "RATE_LIMITED"}]}',
                "rate limit exceeded",
            )
            # REST fallback succeeds
            mock_rest.return_value = rest_comments

            result = ci_monitor.fetch_all_review_threads("owner", "repo", "123", "id isResolved")

            assert result is not None
            assert len(result) == 1
            assert result[0]["is_rest_fallback"] is True
            captured = capsys.readouterr()
            assert "REST API" in captured.err

    def test_returns_none_when_rest_fallback_also_fails(self, capsys):
        """Test returns None when GraphQL is rate limited and REST fallback also fails."""
        with (
            patch.object(ci_monitor, "run_gh_command_with_error") as mock_graphql,
            patch.object(ci_monitor, "fetch_review_comments_rest") as mock_rest,
            patch.object(ci_monitor, "print_rate_limit_warning"),
        ):
            # GraphQL fails with rate limit error, triggering REST fallback
            mock_graphql.return_value = (False, "", "rate limit exceeded")
            # REST fallback also fails
            mock_rest.return_value = None

            result = ci_monitor.fetch_all_review_threads("owner", "repo", "123", "id")

            # REST fallback was attempted
            mock_rest.assert_called_once()
            assert result is None

    def test_no_fallback_for_non_rate_limit_errors(self):
        """Test doesn't fall back for non-rate-limit errors."""
        with (
            patch.object(ci_monitor, "run_gh_command_with_error") as mock_graphql,
            patch.object(ci_monitor, "fetch_review_comments_rest") as mock_rest,
        ):
            mock_graphql.return_value = (False, "network error", "timeout")

            result = ci_monitor.fetch_all_review_threads("owner", "repo", "123", "id")

            # Should not call REST fallback
            mock_rest.assert_not_called()
            assert result is None
