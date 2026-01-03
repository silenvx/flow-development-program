"""Tests for ci_monitor.review_comments module."""

from unittest.mock import MagicMock, patch

from ci_monitor.review_comments import (
    auto_resolve_duplicate_threads,
    classify_review_comments,
    convert_rest_comments_to_thread_format,
    fetch_review_comments_rest,
    filter_duplicate_comments,
    get_pr_changed_files,
    get_review_comments,
    get_unresolved_ai_threads,
    get_unresolved_threads,
    log_review_comments_to_quality_log,
    normalize_comment_body,
    print_comment,
    resolve_thread_by_id,
    strip_code_blocks,
)


class TestStripCodeBlocks:
    """Tests for strip_code_blocks function."""

    def test_removes_fenced_code_block(self):
        """Test removal of fenced code blocks."""
        text = "Before\n```python\ncode here\n```\nAfter"
        result = strip_code_blocks(text)
        assert "code here" not in result
        assert "Before" in result
        assert "After" in result

    def test_removes_inline_code(self):
        """Test removal of inline code."""
        text = "Use `func()` to call"
        result = strip_code_blocks(text)
        assert "`func()`" not in result

    def test_preserves_regular_text(self):
        """Test that regular text is preserved."""
        text = "This is normal text without code"
        result = strip_code_blocks(text)
        assert result == text


class TestGetReviewComments:
    """Tests for get_review_comments function."""

    @patch("ci_monitor.review_comments.run_gh_command")
    def test_successful_fetch(self, mock_run):
        """Test successful comment fetch."""
        mock_run.return_value = (
            True,
            '[{"path": "test.py", "line": 10, "body": "Fix this", "user": "copilot", "id": 1}]',
        )
        comments = get_review_comments("42")
        assert len(comments) == 1
        assert comments[0]["path"] == "test.py"

    @patch("ci_monitor.review_comments.run_gh_command")
    def test_command_failure(self, mock_run):
        """Test when gh command fails."""
        mock_run.return_value = (False, "error")
        comments = get_review_comments("42")
        assert len(comments) == 0

    @patch("ci_monitor.review_comments.run_gh_command")
    def test_invalid_json(self, mock_run):
        """Test handling of invalid JSON."""
        mock_run.return_value = (True, "not json")
        comments = get_review_comments("42")
        assert len(comments) == 0


class TestGetPrChangedFiles:
    """Tests for get_pr_changed_files function."""

    @patch("ci_monitor.review_comments.run_gh_command")
    def test_successful_fetch(self, mock_run):
        """Test successful file list fetch."""
        mock_run.return_value = (True, "file1.py\nfile2.py")
        files = get_pr_changed_files("42")
        assert files == {"file1.py", "file2.py"}

    @patch("ci_monitor.review_comments.run_gh_command")
    def test_command_failure_returns_none(self, mock_run):
        """Test that command failure returns None."""
        mock_run.return_value = (False, "error")
        files = get_pr_changed_files("42")
        assert files is None

    @patch("ci_monitor.review_comments.run_gh_command")
    def test_empty_output(self, mock_run):
        """Test empty output returns empty set."""
        mock_run.return_value = (True, "")
        files = get_pr_changed_files("42")
        assert files == set()

    @patch("ci_monitor.review_comments.run_gh_command")
    def test_limit_reached_returns_none(self, mock_run):
        """Test that hitting file limit returns None."""
        # Create exactly GITHUB_FILES_LIMIT files
        from ci_monitor.constants import GITHUB_FILES_LIMIT

        files = "\n".join([f"file{i}.py" for i in range(GITHUB_FILES_LIMIT)])
        mock_run.return_value = (True, files)
        result = get_pr_changed_files("42")
        assert result is None


class TestClassifyReviewComments:
    """Tests for classify_review_comments function."""

    @patch("ci_monitor.review_comments.get_pr_changed_files")
    @patch("ci_monitor.review_comments.get_review_comments")
    def test_classifies_in_scope(self, mock_comments, mock_files):
        """Test that comments on changed files are in-scope."""
        mock_files.return_value = {"changed.py"}
        mock_comments.return_value = [{"path": "changed.py", "body": "Fix"}]

        result = classify_review_comments("42")
        assert len(result.in_scope) == 1
        assert len(result.out_of_scope) == 0

    @patch("ci_monitor.review_comments.get_pr_changed_files")
    @patch("ci_monitor.review_comments.get_review_comments")
    def test_classifies_out_of_scope(self, mock_comments, mock_files):
        """Test that comments on unchanged files are out-of-scope."""
        mock_files.return_value = {"changed.py"}
        mock_comments.return_value = [{"path": "unchanged.py", "body": "Fix"}]

        result = classify_review_comments("42")
        assert len(result.in_scope) == 0
        assert len(result.out_of_scope) == 1

    @patch("ci_monitor.review_comments.get_pr_changed_files")
    @patch("ci_monitor.review_comments.get_review_comments")
    def test_file_lookup_failure_treats_all_as_in_scope(self, mock_comments, mock_files):
        """Test that file lookup failure treats all comments as in-scope."""
        mock_files.return_value = None  # Failure
        mock_comments.return_value = [
            {"path": "any.py", "body": "Fix1"},
            {"path": "other.py", "body": "Fix2"},
        ]

        result = classify_review_comments("42")
        assert len(result.in_scope) == 2
        assert len(result.out_of_scope) == 0


class TestFetchReviewCommentsRest:
    """Tests for fetch_review_comments_rest function."""

    @patch("ci_monitor.review_comments.run_gh_command")
    def test_successful_fetch(self, mock_run):
        """Test successful REST API fetch."""
        mock_run.return_value = (
            True,
            '[{"id": 1, "path": "test.py", "line": 10, "body": "Fix", "user": {"login": "reviewer"}}]',
        )
        comments = fetch_review_comments_rest("owner", "repo", "42")
        assert comments is not None
        assert len(comments) == 1
        assert comments[0]["is_rest_fallback"] is True

    @patch("ci_monitor.review_comments.run_gh_command")
    def test_command_failure(self, mock_run):
        """Test REST API command failure."""
        mock_run.return_value = (False, "error")
        comments = fetch_review_comments_rest("owner", "repo", "42")
        assert comments is None


class TestConvertRestCommentsToThreadFormat:
    """Tests for convert_rest_comments_to_thread_format function."""

    def test_converts_to_thread_format(self):
        """Test conversion to thread format."""
        comments = [{"id": 1, "path": "test.py", "line": 10, "body": "Fix", "author": "reviewer"}]
        threads = convert_rest_comments_to_thread_format(comments)
        assert len(threads) == 1
        assert threads[0]["id"] == "rest-1"
        assert threads[0]["is_rest_fallback"] is True
        assert threads[0]["isResolved"] is False
        assert len(threads[0]["comments"]["nodes"]) == 1

    def test_empty_comments(self):
        """Test with empty comments list."""
        threads = convert_rest_comments_to_thread_format([])
        assert len(threads) == 0


class TestNormalizeCommentBody:
    """Tests for normalize_comment_body function."""

    def test_removes_line_numbers(self):
        """Test removal of line number references."""
        body = "Fix issue on line 42"
        result = normalize_comment_body(body)
        assert "42" not in result
        assert "line" not in result.lower()

    def test_removes_line_ranges(self):
        """Test removal of line ranges."""
        body = "Check lines 10-20"
        result = normalize_comment_body(body)
        assert "10" not in result
        assert "20" not in result

    def test_normalizes_whitespace(self):
        """Test whitespace normalization."""
        body = "  multiple   spaces  "
        result = normalize_comment_body(body)
        assert result == "multiple spaces"

    def test_preserves_main_content(self):
        """Test that main content is preserved."""
        body = "The function should handle null values"
        result = normalize_comment_body(body)
        assert "null values" in result


class TestGetUnresolved:
    """Tests for get_unresolved_threads and get_unresolved_ai_threads."""

    @patch("ci_monitor.review_comments.fetch_all_review_threads")
    @patch("ci_monitor.review_comments.get_repo_info")
    def test_get_unresolved_threads(self, mock_repo, mock_fetch):
        """Test fetching unresolved threads."""
        mock_repo.return_value = ("owner", "repo")
        mock_fetch.return_value = [
            {
                "id": "thread1",
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "path": "test.py",
                            "line": 10,
                            "body": "Fix",
                            "author": {"login": "reviewer"},
                        }
                    ]
                },
            },
            {
                "id": "thread2",
                "isResolved": True,
                "comments": {
                    "nodes": [
                        {
                            "path": "other.py",
                            "line": 20,
                            "body": "Done",
                            "author": {"login": "user"},
                        }
                    ]
                },
            },
        ]

        threads = get_unresolved_threads("42")
        assert threads is not None
        assert len(threads) == 1
        assert threads[0]["id"] == "thread1"

    @patch("ci_monitor.review_comments.get_unresolved_threads")
    def test_get_unresolved_ai_threads(self, mock_get):
        """Test filtering for AI reviewer threads."""
        mock_get.return_value = [
            {"id": "1", "author": "copilot-reviewer", "body": "Fix"},
            {"id": "2", "author": "human-user", "body": "Comment"},
        ]

        threads = get_unresolved_ai_threads("42")
        assert threads is not None
        assert len(threads) == 1
        assert threads[0]["id"] == "1"


class TestResolveThreadById:
    """Tests for resolve_thread_by_id function."""

    @patch("ci_monitor.review_comments.run_gh_command_with_error")
    def test_successful_resolve(self, mock_run):
        """Test successful thread resolution."""
        mock_run.return_value = (True, '{"data": {}}', "")
        result = resolve_thread_by_id("thread_id")
        assert result is True

    @patch("ci_monitor.review_comments.run_gh_command_with_error")
    def test_failed_resolve(self, mock_run):
        """Test failed thread resolution."""
        mock_run.return_value = (False, "", "error")
        result = resolve_thread_by_id("thread_id")
        assert result is False


class TestAutoResolveDuplicateThreads:
    """Tests for auto_resolve_duplicate_threads function."""

    def test_empty_hashes_returns_zero(self):
        """Test with empty pre-rebase hashes."""
        count, hashes = auto_resolve_duplicate_threads("42", set())
        assert count == 0
        assert len(hashes) == 0

    @patch("ci_monitor.review_comments.get_repo_info")
    def test_repo_info_failure(self, mock_repo):
        """Test when repo info lookup fails."""
        mock_repo.return_value = None
        count, hashes = auto_resolve_duplicate_threads("42", {"hash1"})
        assert count == 0


class TestFilterDuplicateComments:
    """Tests for filter_duplicate_comments function."""

    def test_empty_hashes_returns_all(self):
        """Test with empty duplicate hashes."""
        comments = [{"path": "test.py", "body": "Fix", "user": "reviewer"}]
        result = filter_duplicate_comments(comments, set())
        assert len(result) == 1

    def test_keeps_human_comments(self):
        """Test that human comments are never filtered."""
        comments = [{"path": "test.py", "body": "Fix", "user": "human-user"}]
        result = filter_duplicate_comments(comments, {"some-hash"})
        assert len(result) == 1

    def test_filters_matching_ai_comments(self):
        """Test that matching AI comments are filtered."""
        import hashlib

        body = "Fix this issue"
        path = "test.py"
        normalized = normalize_comment_body(body)
        content = f"{path}:{normalized}"
        hash_value = hashlib.sha256(content.encode()).hexdigest()[:32]

        comments = [{"path": path, "body": body, "user": "copilot-reviewer"}]
        result = filter_duplicate_comments(comments, {hash_value})
        assert len(result) == 0


class TestLogReviewCommentsToQualityLog:
    """Tests for log_review_comments_to_quality_log function."""

    def test_skips_without_log_fn(self):
        """Test that function skips when log_fn is None."""
        comments = [{"user": "copilot", "body": "test", "id": 1}]
        # Should not raise
        log_review_comments_to_quality_log("42", comments, log_comment_fn=None)

    def test_skips_empty_comments(self):
        """Test that function skips with empty comments."""
        log_fn = MagicMock()
        log_review_comments_to_quality_log("42", [], log_comment_fn=log_fn)
        log_fn.assert_not_called()

    def test_only_logs_ai_reviewers(self):
        """Test that only AI reviewer comments are logged."""
        log_fn = MagicMock()
        comments = [
            {
                "user": "copilot-reviewer",
                "body": "AI comment",
                "id": 1,
                "path": "test.py",
                "line": 10,
            },
            {"user": "human-user", "body": "Human comment", "id": 2, "path": "test.py", "line": 20},
        ]
        log_review_comments_to_quality_log(
            "42",
            comments,
            log_comment_fn=log_fn,
            identify_reviewer_fn=lambda x: "copilot",
            estimate_category_fn=lambda x: "general",
        )
        assert log_fn.call_count == 1


class TestPrintComment:
    """Tests for print_comment function."""

    def test_prints_short_body(self, capsys):
        """Test printing comment with short body."""
        comment = {"path": "test.py", "line": 10, "user": "reviewer", "body": "Short"}
        print_comment(comment)
        captured = capsys.readouterr()
        assert "test.py:10" in captured.out
        assert "reviewer" in captured.out
        assert "Short" in captured.out

    def test_truncates_long_body(self, capsys):
        """Test that long body is truncated."""
        comment = {"path": "test.py", "line": 10, "user": "reviewer", "body": "x" * 200}
        print_comment(comment)
        captured = capsys.readouterr()
        assert "..." in captured.out
