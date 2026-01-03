#!/usr/bin/env python3
"""Tests for merge-check.py - ai_review module."""

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "merge-check.py"

# Import submodules for correct mock targets after modularization (Issue #1756)
# These imports enable tests to mock functions at their actual definition locations
import review_checker


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


class TestMergeCheckAIReviewer:
    """Tests for AI reviewer check functionality.

    Note: The check_ai_reviewing function makes actual GitHub API calls.
    These tests would require mocking subprocess.run to test properly.
    Currently, only the integration tests above cover this indirectly.
    """

    # TODO: Add unit tests for check_ai_reviewing with mocked gh api calls
    # - Test blocking when Copilot is in requested_reviewers
    # - Test blocking when Codex is in requested_reviewers
    # - Test approving when no AI reviewers are pending
    # - Test fail-open behavior on API errors
    pass


class TestMergeCheckUnresolvedAIThreads:
    """Tests for unresolved AI review threads check (Check #6).

    These tests use mocked subprocess calls to test the check_unresolved_ai_threads
    function without making actual GitHub API calls.
    """

    def setup_method(self):
        """Load the merge-check module for testing."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def _make_graphql_response(self, threads: list[dict]) -> str:
        """Create a mock GraphQL response JSON string."""
        return json.dumps(
            {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": threads}}}}}
        )

    def _make_thread(
        self,
        author: str,
        is_resolved: bool = False,
        body: str = "Test comment",
        path: str = "test.py",
        line: int = 10,
    ) -> dict:
        """Create a mock thread object."""
        return {
            "id": f"thread_{author}_{line}",
            "isResolved": is_resolved,
            "comments": {
                "nodes": [{"body": body, "path": path, "line": line, "author": {"login": author}}]
            },
        }

    def test_detect_unresolved_copilot_thread(self):
        """Should detect unresolved threads from Copilot."""
        threads = [self._make_thread("copilot[bot]", is_resolved=False)]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_unresolved_ai_threads("123")

        assert len(result) == 1
        assert result[0]["author"] == "copilot[bot]"

    def test_detect_unresolved_codex_thread(self):
        """Should detect unresolved threads from Codex."""
        threads = [self._make_thread("codex-bot", is_resolved=False)]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_unresolved_ai_threads("123")

        assert len(result) == 1
        assert result[0]["author"] == "codex-bot"

    def test_ignore_resolved_threads(self):
        """Should ignore resolved threads from AI reviewers."""
        threads = [self._make_thread("copilot[bot]", is_resolved=True)]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_unresolved_ai_threads("123")

        assert len(result) == 0

    def test_ignore_non_ai_threads(self):
        """Should ignore threads from non-AI authors."""
        threads = [self._make_thread("human-user", is_resolved=False)]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_unresolved_ai_threads("123")

        assert len(result) == 0

    def test_fail_open_on_api_error(self):
        """Should return empty list (fail open) on API errors."""
        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="")
                result = self.module.check_unresolved_ai_threads("123")

        assert result == []

    def test_fail_open_when_repo_info_unavailable(self):
        """Should return empty list when get_repo_owner_and_name returns None."""
        with patch.object(review_checker, "get_repo_owner_and_name", return_value=None):
            result = self.module.check_unresolved_ai_threads("123")

        assert result == []

    def test_fail_open_on_json_parse_error(self):
        """Should return empty list when GraphQL response is invalid JSON."""
        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="invalid json")
                result = self.module.check_unresolved_ai_threads("123")

        assert result == []

    def test_fail_open_on_malformed_response(self):
        """Should return empty list when GraphQL response structure is unexpected."""
        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                # Missing expected nested structure
                mock_run.return_value = MagicMock(returncode=0, stdout='{"data": {}}')
                result = self.module.check_unresolved_ai_threads("123")

        assert result == []

    def test_empty_thread_list(self):
        """Should handle PRs with no review threads."""
        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response([])
                )
                result = self.module.check_unresolved_ai_threads("123")

        assert result == []

    def test_case_insensitive_author_matching(self):
        """Should match AI authors case-insensitively."""
        # Test uppercase COPILOT
        threads = [self._make_thread("COPILOT-BOT", is_resolved=False)]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_unresolved_ai_threads("123")

        assert len(result) == 1

    def test_truncate_long_body(self):
        """Should truncate long comment bodies to 100 characters."""
        long_body = "x" * 150
        threads = [self._make_thread("copilot[bot]", is_resolved=False, body=long_body)]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_unresolved_ai_threads("123")

        assert len(result) == 1
        assert len(result[0]["body"]) == 103  # 100 + "..."
        assert result[0]["body"].endswith("...")


class TestRequestCopilotReview:
    """Tests for request_copilot_review function."""

    def setup_method(self):
        """Load the module once for all tests."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_returns_true_on_success(self):
        """Should return True when API call succeeds."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            result = self.module.request_copilot_review("123")

        assert result

    def test_returns_false_on_api_failure(self):
        """Should return False when API call fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            result = self.module.request_copilot_review("123")

        assert not result

    def test_returns_false_on_exception(self):
        """Should return False when an exception occurs."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Network error")
            result = self.module.request_copilot_review("123")

        assert not result

    def test_passes_correct_arguments(self):
        """Should pass correct arguments to gh api command."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            self.module.request_copilot_review("456")

            # Verify the call arguments
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "gh" in cmd
            assert "api" in cmd
            assert "repos/:owner/:repo/pulls/456/requested_reviewers" in cmd
            assert "-X" in cmd
            assert "POST" in cmd
            assert "--input" in cmd
            assert "-" in cmd

            # Verify JSON input structure (Issue #647)
            input_json = call_args[1].get("input", "")
            assert isinstance(input_json, str)
            assert input_json.strip() != ""
            try:
                parsed = json.loads(input_json)
            except json.JSONDecodeError as e:
                pytest.fail(f"request_copilot_review passed invalid JSON to gh api: {e}")
            assert "reviewers" in parsed
            assert isinstance(parsed["reviewers"], list)
            assert len(parsed["reviewers"]) == 1
            assert parsed["reviewers"][0] == "copilot-pull-request-reviewer[bot]"

    def test_logs_stderr_on_api_failure(self):
        """Should log stderr when API call fails (Issue #646)."""
        # Capture stderr output
        captured_stderr = io.StringIO()
        with patch("subprocess.run") as mock_run:
            with patch.object(sys, "stderr", captured_stderr):
                mock_run.return_value = MagicMock(
                    returncode=1, stdout="", stderr="API error: not found"
                )
                result = self.module.request_copilot_review("789")

        assert not result
        stderr_output = captured_stderr.getvalue()
        assert "[merge-check]" in stderr_output
        assert "request_copilot_review failed" in stderr_output
        assert "PR #789" in stderr_output
        assert "API error: not found" in stderr_output

    def test_logs_exception_on_error(self):
        """Should log exception when an error occurs (Issue #646)."""
        # Capture stderr output
        captured_stderr = io.StringIO()
        with patch("subprocess.run") as mock_run:
            with patch.object(sys, "stderr", captured_stderr):
                mock_run.side_effect = Exception("Connection timeout")
                result = self.module.request_copilot_review("999")

        assert not result
        stderr_output = captured_stderr.getvalue()
        assert "[merge-check]" in stderr_output
        assert "request_copilot_review exception" in stderr_output
        assert "PR #999" in stderr_output
        assert "Connection timeout" in stderr_output


class TestCheckAiReviewError:
    """Tests for check_ai_review_error function (Issue #640).

    This function detects AI review errors (Copilot/Codex encountering errors).
    It implements:
    - Detection of error pattern in review body
    - Consecutive error counting
    - allow_with_warning for 2+ consecutive errors with earlier success
    """

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def _make_review(
        self, author: str, body: str, submitted_at: str = "2025-01-01T00:00:00Z"
    ) -> str:
        """Create a mock review JSON string."""
        return json.dumps(
            {"author": author, "body": body, "state": "COMMENTED", "submitted_at": submitted_at}
        )

    def _make_api_response(self, reviews: list[str]) -> str:
        """Create mock API response from review JSON strings (NDJSON format)."""
        return "\n".join(reviews)

    def test_detect_single_error(self):
        """Should detect single error and return allow_with_warning=False."""
        reviews = [
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "I encountered an error while reviewing this PR.",
                "2025-01-01T00:00:00Z",
            )
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._make_api_response(reviews))
            result = self.module.check_ai_review_error("123")

        assert result is not None
        assert result["reviewer"] == "copilot-pull-request-reviewer[bot]"
        assert not result["allow_with_warning"]
        assert result["consecutive_errors"] == 1

    def test_consecutive_errors_with_success_allows_with_warning(self):
        """Should allow with warning when 2+ consecutive errors follow a success."""
        reviews = [
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "LGTM! This looks good.",
                "2025-01-01T00:00:00Z",
            ),
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "I encountered an error while reviewing.",
                "2025-01-01T01:00:00Z",
            ),
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "I encountered an error while reviewing.",
                "2025-01-01T02:00:00Z",
            ),
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._make_api_response(reviews))
            result = self.module.check_ai_review_error("123")

        assert result is not None
        assert result["allow_with_warning"]
        assert result["consecutive_errors"] == 2

    def test_consecutive_errors_without_success_blocks(self):
        """Should block when all reviews are errors (no successful review)."""
        reviews = [
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "I encountered an error while reviewing.",
                "2025-01-01T00:00:00Z",
            ),
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "I encountered an error while reviewing.",
                "2025-01-01T01:00:00Z",
            ),
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._make_api_response(reviews))
            result = self.module.check_ai_review_error("123")

        assert result is not None
        assert not result["allow_with_warning"]
        assert result["consecutive_errors"] == 2

    def test_no_error_returns_none(self):
        """Should return None when latest review has no error."""
        reviews = [
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "LGTM! This looks good.",
                "2025-01-01T00:00:00Z",
            )
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._make_api_response(reviews))
            result = self.module.check_ai_review_error("123")

        assert result is None

    def test_fail_open_on_api_error(self):
        """Should return None on API errors (fail open)."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = self.module.check_ai_review_error("123")

        assert result is None

    def test_fail_open_on_exception(self):
        """Should return None on exception (fail open)."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Network error")
            result = self.module.check_ai_review_error("123")

        assert result is None

    def test_empty_reviews_returns_none(self):
        """Should return None when no AI reviews exist."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            result = self.module.check_ai_review_error("123")

        assert result is None

    def test_three_consecutive_errors_with_success(self):
        """Should count 3 consecutive errors correctly."""
        reviews = [
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "Good code!",
                "2025-01-01T00:00:00Z",
            ),
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "I encountered an error",
                "2025-01-01T01:00:00Z",
            ),
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "I encountered an error",
                "2025-01-01T02:00:00Z",
            ),
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "I encountered an error",
                "2025-01-01T03:00:00Z",
            ),
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._make_api_response(reviews))
            result = self.module.check_ai_review_error("123")

        assert result is not None
        assert result["allow_with_warning"]
        assert result["consecutive_errors"] == 3

    def test_codex_reviewer_detected(self):
        """Should detect Codex reviewer errors."""
        reviews = [
            self._make_review(
                "codex-bot",
                "I encountered an error while reviewing.",
                "2025-01-01T00:00:00Z",
            )
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._make_api_response(reviews))
            result = self.module.check_ai_review_error("123")

        assert result is not None
        assert result["reviewer"] == "codex-bot"

    def test_error_pattern_case_insensitive(self):
        """Should detect error pattern case-insensitively."""
        reviews = [
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "I ENCOUNTERED AN ERROR while processing.",
                "2025-01-01T00:00:00Z",
            )
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._make_api_response(reviews))
            result = self.module.check_ai_review_error("123")

        assert result is not None

    def test_message_truncation(self):
        """Should truncate long error messages."""
        long_message = "I encountered an error " + "x" * 300
        reviews = [
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                long_message,
                "2025-01-01T00:00:00Z",
            )
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._make_api_response(reviews))
            result = self.module.check_ai_review_error("123")

        assert result is not None
        assert len(result["message"]) <= 203  # 200 + "..."

    def test_reviews_sorted_by_submitted_at(self):
        """Should process reviews in chronological order."""
        # Reviews provided out of order
        reviews = [
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "I encountered an error",
                "2025-01-01T02:00:00Z",  # Latest
            ),
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "Good code!",
                "2025-01-01T00:00:00Z",  # Earliest (success)
            ),
            self._make_review(
                "copilot-pull-request-reviewer[bot]",
                "I encountered an error",
                "2025-01-01T01:00:00Z",  # Middle
            ),
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._make_api_response(reviews))
            result = self.module.check_ai_review_error("123")

        # Should allow with warning because there's a success before errors
        assert result is not None
        assert result["allow_with_warning"]
        assert result["consecutive_errors"] == 2
