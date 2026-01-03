#!/usr/bin/env python3
"""Unit tests for AI reviewer waiting behavior in ci_monitor.

Covers:
- AI reviewer waiting behavior
- Codex Cloud review detection
"""

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


class TestAIReviewerWaiting:
    """Tests for AI reviewer waiting behavior.

    Issue #2454: wait_review is now always enabled (hardcoded True).
    These tests verify that monitoring waits for AI review completion.
    """

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_waits_when_ai_reviewer_detected(
        self, mock_get_pr_state, mock_get_comments, mock_sleep
    ):
        """Test that monitoring waits when AI reviewer is detected."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state: AI reviewer present
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 2:
                # CI passed but Copilot still reviewing
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            elif call_count == 3:
                # Still reviewing (waiting for review completion)
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            else:
                # Copilot finished
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_get_comments.return_value = [{"path": "test.py", "line": 1, "body": "suggestion"}]

        # AI reviewer is present
        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert result.ci_passed
        # Should have waited for review completion
        assert result.review_completed
        # Should have called get_pr_state multiple times (waiting for review)
        assert mock_get_pr_state.call_count >= 4

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_waits_after_ci_passes(self, mock_get_pr_state, mock_get_comments, mock_sleep):
        """Test that it continues waiting after CI passes when AI review pending."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial: Copilot assigned
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["copilot-pull-request-reviewer[bot]"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 2:
                # CI passed but Copilot still pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["copilot-pull-request-reviewer[bot]"],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            elif call_count == 3:
                # Still waiting
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["copilot-pull-request-reviewer[bot]"],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            else:
                # Review done
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_get_comments.return_value = []

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        # Waited for review completion
        assert result.review_completed
        # Verified it continued after CI passed (call_count >= 4)
        assert mock_get_pr_state.call_count >= 4

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_no_auto_enable_without_ai_reviewer(self, mock_get_pr_state, mock_sleep):
        """Test that auto-enable does NOT trigger without AI reviewers."""
        mock_get_pr_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=["human-reviewer"],  # No AI reviewer
                check_status=CheckStatus.SUCCESS,
            ),
            None,
        )

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert result.ci_passed
        # Should NOT wait for review (no auto-enable)
        assert not result.review_completed
        # Should exit quickly (only 2 calls: initial + first loop)
        assert mock_get_pr_state.call_count == 2

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_auto_enable_with_codex_reviewer(
        self, mock_get_pr_state, mock_get_comments, mock_sleep
    ):
        """Test that auto-enable works with Codex reviewer as well."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial: Codex assigned
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["chatgpt-codex-connector[bot]"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 2:
                # CI passed but Codex still pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["chatgpt-codex-connector[bot]"],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            else:
                # Review done
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_get_comments.return_value = []

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert result.review_completed


class TestCodexCloudDetection:
    """Tests for Codex Cloud review detection (Issue #228).

    Codex Cloud reviews work differently from GitHub Copilot:
    1. User posts @codex review comment on PR
    2. Codex adds 👀 reaction to indicate it started
    3. Codex posts a PR review when complete

    These tests verify detection of this workflow.
    """

    @patch("ci_monitor.github_api.run_gh_command")
    def test_get_codex_review_requests_finds_comment(self, mock_run_gh):
        """Test that @codex review comments are detected."""
        mock_run_gh.return_value = (
            True,
            json.dumps(
                [{"id": 123, "created_at": "2025-01-01T00:00:00Z", "body": "@codex review please"}]
            ),
        )

        with patch("ci_monitor.ai_review._check_eyes_reaction", return_value=True):
            requests = ci_monitor.get_codex_review_requests("42")

        assert len(requests) == 1
        assert requests[0].comment_id == 123
        assert requests[0].has_eyes_reaction

    @patch("ci_monitor.github_api.run_gh_command")
    def test_get_codex_review_requests_empty_when_no_comments(self, mock_run_gh):
        """Test that empty list is returned when no @codex review comments."""
        mock_run_gh.return_value = (True, "[]")

        requests = ci_monitor.get_codex_review_requests("42")

        assert len(requests) == 0

    @patch("ci_monitor.github_api.run_gh_command")
    def test_check_eyes_reaction_detects_reaction(self, mock_run_gh):
        """Test that 👀 reaction is detected."""
        mock_run_gh.return_value = (
            True,
            json.dumps([{"content": "eyes", "user": {"login": "codex-bot"}}]),
        )

        result = ci_monitor._check_eyes_reaction(123)

        assert result

    @patch("ci_monitor.github_api.run_gh_command")
    def test_check_eyes_reaction_returns_false_when_no_reaction(self, mock_run_gh):
        """Test that False is returned when no 👀 reaction."""
        mock_run_gh.return_value = (True, "[]")

        result = ci_monitor._check_eyes_reaction(123)

        assert not result

    @patch("ci_monitor.github_api.run_gh_command")
    def test_get_codex_reviews_finds_reviews(self, mock_run_gh):
        """Test that Codex reviews are detected."""
        mock_run_gh.return_value = (
            True,
            json.dumps(
                [
                    {
                        "id": 456,
                        "user": "chatgpt-codex-connector[bot]",
                        "submitted_at": "2025-01-01T01:00:00Z",
                        "state": "COMMENTED",
                        "body": "Code review summary...",
                    }
                ]
            ),
        )

        reviews = ci_monitor.get_codex_reviews("42")

        assert len(reviews) == 1
        assert reviews[0]["id"] == 456

    @patch("ci_monitor.ai_review.get_codex_reviews")
    @patch("ci_monitor.ai_review.get_codex_review_requests")
    def test_is_codex_review_pending_when_started_not_complete(
        self, mock_get_requests, mock_get_reviews
    ):
        """Test that pending is True when review started but not complete."""
        mock_get_requests.return_value = [
            ci_monitor.CodexReviewRequest(
                comment_id=123,
                created_at="2025-01-01T00:00:00Z",
                has_eyes_reaction=True,  # Started
            )
        ]
        mock_get_reviews.return_value = []  # No review posted yet

        result = ci_monitor.is_codex_review_pending("42")

        assert result

    @patch("ci_monitor.ai_review.get_codex_reviews")
    @patch("ci_monitor.ai_review.get_codex_review_requests")
    def test_is_codex_review_pending_false_when_complete(self, mock_get_requests, mock_get_reviews):
        """Test that pending is False when review is complete."""
        mock_get_requests.return_value = [
            ci_monitor.CodexReviewRequest(
                comment_id=123,
                created_at="2025-01-01T00:00:00Z",
                has_eyes_reaction=True,
            )
        ]
        mock_get_reviews.return_value = [
            {"submitted_at": "2025-01-01T01:00:00Z"}  # Posted after request
        ]

        result = ci_monitor.is_codex_review_pending("42")

        assert not result

    @patch("ci_monitor.ai_review.get_codex_review_requests")
    def test_is_codex_review_pending_false_when_no_requests(self, mock_get_requests):
        """Test that pending is False when no @codex review requests."""
        mock_get_requests.return_value = []

        result = ci_monitor.is_codex_review_pending("42")

        assert not result

    @patch("ci_monitor.ai_review.get_codex_reviews")
    @patch("ci_monitor.ai_review.get_codex_review_requests")
    def test_is_codex_review_pending_when_not_yet_started(
        self, mock_get_requests, mock_get_reviews
    ):
        """Test that pending is True when request exists but no review posted yet."""
        mock_get_requests.return_value = [
            ci_monitor.CodexReviewRequest(
                comment_id=123,
                created_at="2025-01-01T10:00:00Z",
                has_eyes_reaction=False,  # Codex hasn't acknowledged yet
            )
        ]
        mock_get_reviews.return_value = []  # No review posted

        result = ci_monitor.is_codex_review_pending("42")

        # Should be True because no review has been posted
        assert result

    @patch("ci_monitor.ai_review.get_codex_reviews")
    @patch("ci_monitor.ai_review.get_codex_review_requests")
    def test_is_codex_review_pending_false_when_no_eyes_but_review_posted(
        self, mock_get_requests, mock_get_reviews
    ):
        """Test that pending is False when review posted even without 👀 reaction (Codex P1 fix)."""
        mock_get_requests.return_value = [
            ci_monitor.CodexReviewRequest(
                comment_id=123,
                created_at="2025-01-01T10:00:00Z",
                has_eyes_reaction=False,  # No eyes reaction (API failure or skipped)
            )
        ]
        mock_get_reviews.return_value = [
            {"submitted_at": "2025-01-01T11:00:00Z"}  # Review posted after request
        ]

        result = ci_monitor.is_codex_review_pending("42")

        # Should be False because review was posted (even without 👀)
        assert not result

    @patch("ci_monitor.main_loop.is_codex_review_pending")
    def test_has_ai_review_pending_with_codex_cloud(self, mock_codex_pending):
        """Test that has_ai_review_pending detects Codex Cloud reviews."""
        mock_codex_pending.return_value = True

        result = ci_monitor.has_ai_review_pending("42", [])  # No GitHub reviewers

        assert result

    @patch("ci_monitor.main_loop.is_codex_review_pending")
    def test_has_ai_review_pending_with_github_reviewer(self, mock_codex_pending):
        """Test that has_ai_review_pending detects GitHub reviewers."""
        mock_codex_pending.return_value = False

        result = ci_monitor.has_ai_review_pending("42", ["Copilot"])

        assert result

    @patch("ci_monitor.main_loop.is_codex_review_pending")
    def test_has_ai_review_pending_false_when_nothing_pending(self, mock_codex_pending):
        """Test that has_ai_review_pending returns False when nothing pending."""
        mock_codex_pending.return_value = False

        result = ci_monitor.has_ai_review_pending("42", ["human-reviewer"])

        assert not result
