#!/usr/bin/env python3
"""Unit tests for ci_monitor.ai_review module."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add scripts directory to path so we can import ci_monitor package
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the ci_monitor package for attribute access (Issue #2624)
import ci_monitor

# Import directly from ci_monitor package (Issue #2624)
from ci_monitor import (
    CheckStatus,
    EventType,
    MergeState,
    PRState,
    check_once,
    has_copilot_or_codex_reviewer,
    is_ai_reviewer,
)


class TestIsAIReviewer:
    """Tests for is_ai_reviewer function.

    Issue #1109: Tests for the centralized AI reviewer detection utility.
    This ensures consistent behavior when checking if a comment/review author
    is an AI reviewer (Copilot, Codex).
    """

    def test_empty_string(self):
        """Test with empty string returns False."""
        assert not is_ai_reviewer("")

    def test_human_user(self):
        """Test with human username returns False."""
        assert not is_ai_reviewer("john-doe")
        assert not is_ai_reviewer("reviewer123")
        assert not is_ai_reviewer("github-actions[bot]")

    def test_copilot_reviewer(self):
        """Test with Copilot reviewer returns True."""
        assert is_ai_reviewer("copilot-pull-request-reviewer")
        assert is_ai_reviewer("copilot-pull-request-reviewer[bot]")
        assert is_ai_reviewer("Copilot")

    def test_codex_reviewer(self):
        """Test with Codex reviewer returns True."""
        assert is_ai_reviewer("codex")
        assert is_ai_reviewer("chatgpt-codex-connector")
        assert is_ai_reviewer("chatgpt-codex-connector[bot]")

    def test_openai_reviewer(self):
        """Test with OpenAI reviewer returns True (Issue #1290)."""
        assert is_ai_reviewer("openai")
        assert is_ai_reviewer("openai-reviewer")
        assert is_ai_reviewer("openai-reviewer[bot]")

    def test_chatgpt_reviewer(self):
        """Test with ChatGPT reviewer returns True (Issue #1290)."""
        assert is_ai_reviewer("chatgpt")
        assert is_ai_reviewer("chatgpt-reviewer")
        assert is_ai_reviewer("chatgpt-reviewer[bot]")

    def test_case_insensitive(self):
        """Test case insensitivity for AI reviewer names."""
        assert is_ai_reviewer("COPILOT")
        assert is_ai_reviewer("CoPiLoT")
        assert is_ai_reviewer("CODEX")
        assert is_ai_reviewer("CoDeX")

    def test_partial_match(self):
        """Test that partial matches work (e.g., 'copilot' in 'copilot-reviewer')."""
        assert is_ai_reviewer("my-copilot-bot")
        assert is_ai_reviewer("codex-assistant")

    def test_human_with_similar_name(self):
        """Test that similar but non-matching names return False."""
        assert not is_ai_reviewer("copliot")  # typo
        assert not is_ai_reviewer("coedx")  # typo
        assert not is_ai_reviewer("pilot")  # substring but not 'copilot'


class TestHasCopilotOrCodexReviewer:
    """Tests for has_copilot_or_codex_reviewer function."""

    def test_empty_reviewers(self):
        """Test with empty reviewer list."""
        assert not has_copilot_or_codex_reviewer([])

    def test_no_ai_reviewers(self):
        """Test with human reviewers only."""
        reviewers = ["user1", "user2", "reviewer-bot"]
        assert not has_copilot_or_codex_reviewer(reviewers)

    def test_copilot_reviewer(self):
        """Test with Copilot reviewer."""
        reviewers = ["user1", "Copilot"]
        assert has_copilot_or_codex_reviewer(reviewers)

    def test_copilot_bot_reviewer(self):
        """Test with copilot-pull-request-reviewer[bot]."""
        reviewers = ["copilot-pull-request-reviewer[bot]"]
        assert has_copilot_or_codex_reviewer(reviewers)

    def test_codex_reviewer(self):
        """Test with Codex reviewer."""
        reviewers = ["user1", "codex"]
        assert has_copilot_or_codex_reviewer(reviewers)

    def test_case_insensitive(self):
        """Test case insensitivity."""
        assert has_copilot_or_codex_reviewer(["COPILOT"])
        assert has_copilot_or_codex_reviewer(["CoPiLoT"])
        assert has_copilot_or_codex_reviewer(["CODEX"])


class TestAsyncReviewerReRequest:
    """Tests for async AI reviewer re-request detection after rebase.

    PR #180 added logic to detect when Copilot/Codex is asynchronously
    re-requested as a reviewer after a rebase. These tests verify:
    1. Delay check occurs when rebase_count > 0
    2. Waiting continues when AI reviewer detected after delay
    3. Normal completion when no AI reviewer after delay
    4. Retry behavior when get_pr_state returns None after delay
    5. No delay check without rebase (rebase_count == 0)
    6. Loop restart when merge state changes to BEHIND after delay
    """

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.rebase_pr")
    @patch("ci_monitor.main_loop.has_local_changes", return_value=(False, ""))
    def test_delay_check_occurs_after_rebase(
        self, mock_local_changes, mock_rebase, mock_get_pr_state, mock_sleep
    ):
        """Test that delay check occurs when rebase_count > 0."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state check
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 2:
                # BEHIND detected
                return (
                    PRState(
                        merge_state=MergeState.BEHIND,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 3:
                # After rebase, CI pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 4:
                # CI passed
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            else:
                # After async delay check - no AI reviewer
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_rebase.return_value = ci_monitor.RebaseResult(success=True)

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert result.rebase_count == 1
        # Check that sleep was called with ASYNC_REVIEWER_CHECK_DELAY_SECONDS
        sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
        assert ci_monitor.ASYNC_REVIEWER_CHECK_DELAY_SECONDS in sleep_calls

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.rebase_pr")
    @patch("ci_monitor.main_loop.has_local_changes", return_value=(False, ""))
    def test_waiting_continues_when_ai_reviewer_detected_after_delay(
        self, mock_local_changes, mock_rebase, mock_get_pr_state, mock_get_comments, mock_sleep
    ):
        """Test that waiting continues when AI reviewer is detected after delay.

        The review completion is detected at the TOP of the loop, not after the delay check.
        So we need the AI reviewer to be present at loop start, then absent at a subsequent loop start.
        """
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 2:
                # BEHIND detected
                return (
                    PRState(
                        merge_state=MergeState.BEHIND,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 3:
                # After rebase, CI pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 4:
                # CI passed
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            elif call_count == 5:
                # After async delay - Copilot re-requested!
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            elif call_count == 6:
                # Next loop iteration start - Copilot still pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            elif call_count == 7:
                # After second delay check - Copilot still pending, continue
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            elif call_count == 8:
                # Next loop iteration start - Copilot finished (no pending reviewers)
                # This triggers review completion detection at loop start
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            else:
                # After final delay check - no AI reviewer, return success
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_rebase.return_value = ci_monitor.RebaseResult(success=True)
        mock_get_comments.return_value = [{"path": "test.py", "line": 1, "body": "suggestion"}]

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert result.rebase_count == 1
        # Should have detected Copilot re-request and waited for review
        assert result.review_completed

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.rebase_pr")
    @patch("ci_monitor.main_loop.has_local_changes", return_value=(False, ""))
    def test_normal_completion_when_no_ai_reviewer_after_delay(
        self, mock_local_changes, mock_rebase, mock_get_pr_state, mock_sleep
    ):
        """Test normal completion when no AI reviewer is detected after delay."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 2:
                # BEHIND detected
                return (
                    PRState(
                        merge_state=MergeState.BEHIND,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 3:
                # After rebase, CI pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 4:
                # CI passed
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            else:
                # After async delay - no AI reviewer
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_rebase.return_value = ci_monitor.RebaseResult(success=True)

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert result.rebase_count == 1
        # No AI reviewer was detected, so review_completed should be False
        assert not result.review_completed

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.rebase_pr")
    @patch("ci_monitor.main_loop.has_local_changes", return_value=(False, ""))
    def test_retry_when_state_none_after_delay(
        self, mock_local_changes, mock_rebase, mock_get_pr_state, mock_sleep
    ):
        """Test that monitoring retries when get_pr_state returns None after delay."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 2:
                # BEHIND detected
                return (
                    PRState(
                        merge_state=MergeState.BEHIND,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 3:
                # After rebase, CI pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 4:
                # CI passed
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            elif call_count == 5:
                # After async delay - API error (None)
                return (None, "mock API error")
            elif call_count == 6:
                # Retry - still pending (simulate recovery)
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 7:
                # CI passed again
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            else:
                # After second async delay - success
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_rebase.return_value = ci_monitor.RebaseResult(success=True)

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        # Should have called get_pr_state multiple times due to retry
        assert mock_get_pr_state.call_count >= 6
        # Verify rebase_count stays at 1 (no additional rebases from retry)
        assert result.rebase_count == 1

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_no_delay_check_without_rebase(self, mock_get_pr_state, mock_sleep):
        """Test that delay check does NOT occur when rebase_count is 0."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # CI passed immediately (no rebase needed)
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert result.rebase_count == 0
        # Verify ASYNC_REVIEWER_CHECK_DELAY_SECONDS was NOT in sleep calls
        sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
        assert ci_monitor.ASYNC_REVIEWER_CHECK_DELAY_SECONDS not in sleep_calls

    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.rebase_pr")
    @patch("ci_monitor.main_loop.has_local_changes", return_value=(False, ""))
    def test_restarts_loop_when_merge_state_changes_after_delay(
        self, mock_local_changes, mock_rebase, mock_get_pr_state, mock_sleep
    ):
        """Test that loop restarts when merge_state changes to BEHIND after delay."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 2:
                # BEHIND detected - first rebase
                return (
                    PRState(
                        merge_state=MergeState.BEHIND,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 3:
                # After first rebase, CI pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 4:
                # CI passed
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            elif call_count == 5:
                # After async delay - main advanced again (BEHIND)
                return (
                    PRState(
                        merge_state=MergeState.BEHIND,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            elif call_count == 6:
                # Second BEHIND detected
                return (
                    PRState(
                        merge_state=MergeState.BEHIND,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 7:
                # After second rebase, CI pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 8:
                # CI passed again
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            else:
                # After second async delay - success
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_rebase.return_value = ci_monitor.RebaseResult(success=True)

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        # Should have rebased twice
        assert result.rebase_count == 2


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


class TestCopilotReviewError:
    """Tests for Copilot review error detection feature.

    When Copilot fails to review a PR, it posts an error message like:
    "Copilot encountered an error and was unable to review this pull request."

    These tests verify:
    1. Error detection via is_copilot_review_error()
    2. REVIEW_ERROR event creation in check_once()
    3. MonitorResult failure when error is detected in monitor_pr()
    """

    @patch("ci_monitor.github_api.run_gh_command")
    def test_get_copilot_reviews_finds_reviews(self, mock_run_gh):
        """Test that Copilot reviews are detected."""
        mock_run_gh.return_value = (
            True,
            json.dumps(
                [
                    {
                        "id": 123,
                        "user": "copilot-pull-request-reviewer[bot]",
                        "submitted_at": "2025-01-01T00:00:00Z",
                        "state": "COMMENTED",
                        "body": "Code review summary...",
                    }
                ]
            ),
        )

        reviews = ci_monitor.get_copilot_reviews("42")

        assert len(reviews) == 1
        assert reviews[0]["id"] == 123

    @patch("ci_monitor.github_api.run_gh_command")
    def test_get_copilot_reviews_empty_when_no_reviews(self, mock_run_gh):
        """Test that empty list is returned when no Copilot reviews."""
        mock_run_gh.return_value = (True, "[]")

        reviews = ci_monitor.get_copilot_reviews("42")

        assert len(reviews) == 0

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_is_copilot_review_error_detects_error(self, mock_get_reviews):
        """Test that error is detected when Copilot posts error message."""
        mock_get_reviews.return_value = [
            {
                "id": 123,
                "user": "copilot-pull-request-reviewer[bot]",
                "body": "Copilot encountered an error and was unable to review this pull request. You can try again by re-requesting a review.",
            }
        ]

        is_error, error_message = ci_monitor.is_copilot_review_error("42")

        assert is_error
        assert "encountered an error" in error_message

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_is_copilot_review_error_false_for_normal_review(self, mock_get_reviews):
        """Test that no error is detected for normal review."""
        mock_get_reviews.return_value = [
            {
                "id": 123,
                "user": "copilot-pull-request-reviewer[bot]",
                "body": "This code looks good. Consider adding a test.",
            }
        ]

        is_error, error_message = ci_monitor.is_copilot_review_error("42")

        assert not is_error
        assert error_message is None

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_is_copilot_review_error_false_when_no_reviews(self, mock_get_reviews):
        """Test that no error is detected when no reviews."""
        mock_get_reviews.return_value = []

        is_error, error_message = ci_monitor.is_copilot_review_error("42")

        assert not is_error
        assert error_message is None

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_is_copilot_review_error_detects_various_patterns(self, mock_get_reviews):
        """Test that various error patterns are detected."""
        error_messages = [
            "Copilot encountered an error and was unable to review",
            "Unable to review this pull request",
            "Could not complete the review",
            "Failed to review the changes",
            "An error occurred during review",
        ]

        for msg in error_messages:
            mock_get_reviews.return_value = [
                {"id": 1, "body": msg, "submitted_at": "2025-01-01T00:00:00Z"}
            ]
            is_error, _ = ci_monitor.is_copilot_review_error("42")
            assert is_error, f"Expected error for: {msg}"

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_is_copilot_review_error_ignores_old_error_if_newer_success(self, mock_get_reviews):
        """Test that old error is ignored if a newer successful review exists."""
        mock_get_reviews.return_value = [
            {
                "id": 1,
                "body": "Copilot encountered an error and was unable to review",
                "submitted_at": "2025-01-01T00:00:00Z",
            },
            {
                "id": 2,
                "body": "This code looks good. No issues found.",
                "submitted_at": "2025-01-02T00:00:00Z",  # Newer
            },
        ]

        is_error, error_message = ci_monitor.is_copilot_review_error("42")

        assert not is_error
        assert error_message is None

    @patch("ci_monitor.ai_review.get_copilot_reviews")
    def test_is_copilot_review_error_detects_latest_error(self, mock_get_reviews):
        """Test that error is detected when the latest review is an error."""
        mock_get_reviews.return_value = [
            {
                "id": 1,
                "body": "This code looks good.",
                "submitted_at": "2025-01-01T00:00:00Z",
            },
            {
                "id": 2,
                "body": "Copilot encountered an error and was unable to review",
                "submitted_at": "2025-01-02T00:00:00Z",  # Newer and is error
            },
        ]

        is_error, error_message = ci_monitor.is_copilot_review_error("42")

        assert is_error
        assert "encountered an error" in error_message


class TestRequestCopilotReview:
    """Tests for request_copilot_review function (Issue #1394).

    Verifies that the function returns a tuple of (success, error_message)
    for better error diagnostics.
    """

    @patch("ci_monitor.pr_operations.subprocess.run")
    def test_request_copilot_review_success(self, mock_run):
        """Test successful Copilot review request returns (True, '')."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="{}",
            stderr="",
        )

        success, error_msg = ci_monitor.request_copilot_review("123")

        assert success is True
        assert error_msg == ""

    @patch("ci_monitor.pr_operations.subprocess.run")
    def test_request_copilot_review_failure_returns_error(self, mock_run):
        """Test failed request returns (False, error_message)."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Not Found: Resource not found",
        )

        success, error_msg = ci_monitor.request_copilot_review("123")

        assert success is False
        assert "Not Found" in error_msg

    @patch("ci_monitor.pr_operations.subprocess.run")
    def test_request_copilot_review_failure_no_stderr(self, mock_run):
        """Test failed request with empty stderr."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="",
        )

        success, error_msg = ci_monitor.request_copilot_review("123")

        assert success is False
        assert error_msg == "No stderr output"

    @patch("ci_monitor.pr_operations.subprocess.run")
    def test_request_copilot_review_exception_returns_error(self, mock_run):
        """Test exception handling returns (False, error_message)."""
        mock_run.side_effect = RuntimeError("Connection timeout")

        success, error_msg = ci_monitor.request_copilot_review("123")

        assert success is False
        assert "Connection timeout" in error_msg


class TestCopilotReviewErrorIntegration:
    """Integration tests for Copilot review error handling.

    These tests verify the integration between check_once(), monitor_pr()
    and the error detection/retry mechanism.
    """

    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_check_once_returns_review_error_event(
        self, mock_get_pr_state, mock_get_comments, mock_is_error
    ):
        """Test that check_once returns REVIEW_ERROR event when Copilot fails."""
        mock_get_pr_state.return_value = (
            PRState(
                merge_state=MergeState.CLEAN,
                pending_reviewers=[],  # Copilot removed after error
                check_status=CheckStatus.PENDING,
            ),
            None,
        )
        mock_is_error.return_value = (True, "Copilot encountered an error")

        # Previous state had Copilot as reviewer
        event = check_once("123", ["Copilot"])

        assert event is not None
        assert event.event_type == EventType.REVIEW_ERROR
        assert "error" in event.message.lower()
        assert event.suggested_action == "Re-request Copilot review"

    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_monitor_pr_fails_on_copilot_error(
        self, mock_get_pr_state, mock_sleep, mock_codex_pending, mock_is_error
    ):
        """Test that monitor_pr returns failure when Copilot review errors."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state: Copilot assigned
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # Copilot finished (with error)
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (
            True,
            "Copilot encountered an error and was unable to review",
        )

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert not result.success
        assert "Copilot review failed" in result.message
        assert not result.review_completed

    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_monitor_pr_succeeds_on_normal_review(
        self,
        mock_get_pr_state,
        mock_get_comments,
        mock_sleep,
        mock_get_threads,
        mock_codex_pending,
        mock_is_error,
    ):
        """Test that monitor_pr succeeds when Copilot review completes normally."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state: Copilot assigned
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count == 2:
                # Copilot finished (normal)
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
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
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (False, None)  # No error
        mock_get_comments.return_value = []
        mock_get_threads.return_value = []

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        assert result.success
        assert result.review_completed

    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.classify_review_comments")
    def test_monitor_pr_early_exit_on_review_comments(
        self,
        mock_classify,
        mock_get_pr_state,
        mock_get_comments,
        mock_sleep,
        mock_get_threads,
        mock_codex_pending,
        mock_is_error,
    ):
        """Test that monitor_pr exits early when early_exit=True and review comments are detected."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state: Copilot assigned
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=ci_monitor.CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # Copilot finished (normal) - still pending CI
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=ci_monitor.CheckStatus.PENDING,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (False, None)  # No error
        mock_get_comments.return_value = [
            {"body": "Consider adding a test", "line": 10, "path": "foo.py", "user": "Copilot"}
        ]
        mock_get_threads.return_value = []
        mock_classify.return_value = ci_monitor.ClassifiedComments(
            in_scope=[
                {"body": "Consider adding a test", "line": 10, "path": "foo.py", "user": "Copilot"}
            ],
            out_of_scope=[],
        )

        result = ci_monitor.monitor_pr("123", timeout_minutes=1, early_exit=True)

        # Early exit should return success with review_completed=True
        assert result.success
        assert result.review_completed
        assert "early exit" in result.message.lower()
        # CI is still pending, so ci_passed should be False
        assert not result.ci_passed

    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.classify_review_comments")
    def test_monitor_pr_early_exit_not_bypassing_ci_failure(
        self,
        mock_classify,
        mock_get_pr_state,
        mock_get_comments,
        mock_sleep,
        mock_get_threads,
        mock_codex_pending,
        mock_is_error,
    ):
        """Test that early_exit does not bypass CI failure handling."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state: Copilot assigned
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=ci_monitor.CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # Copilot finished AND CI failed
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=ci_monitor.CheckStatus.FAILURE,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (False, None)  # No Copilot error
        mock_get_comments.return_value = [
            {"body": "Consider adding a test", "line": 10, "path": "foo.py", "user": "Copilot"}
        ]
        mock_get_threads.return_value = []
        mock_classify.return_value = ci_monitor.ClassifiedComments(
            in_scope=[
                {"body": "Consider adding a test", "line": 10, "path": "foo.py", "user": "Copilot"}
            ],
            out_of_scope=[],
        )

        result = ci_monitor.monitor_pr("123", timeout_minutes=1, early_exit=True)

        # Even with early_exit=True, CI failure should take precedence
        assert not result.success
        assert "ci failed" in result.message.lower()
        assert not result.ci_passed

    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.classify_review_comments")
    def test_monitor_pr_early_exit_on_out_of_scope_comments(
        self,
        mock_classify,
        mock_get_pr_state,
        mock_get_comments,
        mock_sleep,
        mock_get_threads,
        mock_codex_pending,
        mock_is_error,
    ):
        """Test that early_exit triggers for out-of-scope comments too.

        This documents the intentional behavior: early exit is triggered for ANY
        review comments, including out-of-scope ones. This allows the agent to
        start creating follow-up Issues early, following the shift-left principle.
        """
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=ci_monitor.CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=ci_monitor.CheckStatus.PENDING,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (False, None)
        # Only out-of-scope comments (e.g., suggestions for other files)
        mock_get_comments.return_value = [
            {"body": "Consider updating docs", "line": 5, "path": "README.md", "user": "Copilot"}
        ]
        mock_get_threads.return_value = []
        mock_classify.return_value = ci_monitor.ClassifiedComments(
            in_scope=[],
            out_of_scope=[
                {
                    "body": "Consider updating docs",
                    "line": 5,
                    "path": "README.md",
                    "user": "Copilot",
                }
            ],
        )

        result = ci_monitor.monitor_pr("123", timeout_minutes=1, early_exit=True)

        # Early exit should trigger for out-of-scope comments too
        assert result.success
        assert result.review_completed
        assert "early exit" in result.message.lower()

    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.main_loop.classify_review_comments")
    def test_monitor_pr_early_exit_not_bypassing_ci_cancelled(
        self,
        mock_classify,
        mock_get_pr_state,
        mock_get_comments,
        mock_sleep,
        mock_get_threads,
        mock_codex_pending,
        mock_is_error,
    ):
        """Test that early_exit does not bypass CI cancelled handling."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=ci_monitor.CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # Copilot finished AND CI was cancelled
                return (
                    ci_monitor.PRState(
                        merge_state=ci_monitor.MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=ci_monitor.CheckStatus.CANCELLED,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (False, None)
        mock_get_comments.return_value = [
            {"body": "Consider adding a test", "line": 10, "path": "foo.py", "user": "Copilot"}
        ]
        mock_get_threads.return_value = []
        mock_classify.return_value = ci_monitor.ClassifiedComments(
            in_scope=[
                {"body": "Consider adding a test", "line": 10, "path": "foo.py", "user": "Copilot"}
            ],
            out_of_scope=[],
        )

        result = ci_monitor.monitor_pr("123", timeout_minutes=1, early_exit=True)

        # Even with early_exit=True, CI cancellation should take precedence
        assert not result.success
        assert "cancelled" in result.message.lower()
        assert not result.ci_passed


class TestCopilotRetryConstants:
    """Tests for Copilot retry-related constants (Issue #1353).

    Verifies that the retry constants are properly defined and have expected values.
    """

    def test_default_max_copilot_retry_exists(self):
        """Test DEFAULT_MAX_COPILOT_RETRY constant exists."""
        assert hasattr(ci_monitor, "DEFAULT_MAX_COPILOT_RETRY")
        assert ci_monitor.DEFAULT_MAX_COPILOT_RETRY == 3

    def test_default_max_retry_wait_polls_exists(self):
        """Test DEFAULT_MAX_RETRY_WAIT_POLLS constant exists."""
        assert hasattr(ci_monitor, "DEFAULT_MAX_RETRY_WAIT_POLLS")
        assert ci_monitor.DEFAULT_MAX_RETRY_WAIT_POLLS == 4

    def test_retry_wait_status_enum_exists(self):
        """Test RetryWaitStatus enum exists with expected values."""
        assert hasattr(ci_monitor, "RetryWaitStatus")
        assert ci_monitor.RetryWaitStatus.CONTINUE.value == "CONTINUE"
        assert ci_monitor.RetryWaitStatus.TIMEOUT.value == "TIMEOUT"


class TestCopilotRetryLogic:
    """Integration tests for Copilot retry logic (Issue #1353).

    Tests the retry mechanism when Copilot review encounters an error:
    1. Error detection triggers retry
    2. Retry count increments correctly
    3. Max retries leads to failure
    4. Successful retry after error
    5. Retry wait polling behavior
    """

    @patch("ci_monitor.main_loop.request_copilot_review")
    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_retry_triggered_on_copilot_error(
        self,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_is_error,
        mock_request_review,
    ):
        """Test that retry is triggered when Copilot error is detected."""
        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial state: Copilot assigned
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif call_count <= 5:
                # Copilot finished with error
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # After timeout
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (True, "Copilot encountered an error")
        mock_request_review.return_value = (True, "")  # Retry succeeds

        # Run with short timeout
        ci_monitor.monitor_pr("123", timeout_minutes=1)

        # request_copilot_review should have been called (retry triggered)
        # Should retry up to DEFAULT_MAX_COPILOT_RETRY times when error persists
        assert mock_request_review.called
        assert mock_request_review.call_count == ci_monitor.DEFAULT_MAX_COPILOT_RETRY

    @patch("ci_monitor.main_loop.request_copilot_review")
    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_max_retries_leads_to_failure(
        self,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_is_error,
        mock_request_review,
    ):
        """Test that exceeding max retries leads to failure."""
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
                # Always return no pending reviewers (Copilot keeps failing)
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        # Always return error
        mock_is_error.return_value = (True, "Copilot encountered an error")
        # Retry request succeeds but Copilot keeps failing
        mock_request_review.return_value = (True, "")

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        # Should fail after max retries
        assert not result.success
        assert "Copilot review failed" in result.message
        # Should have retried DEFAULT_MAX_COPILOT_RETRY times
        assert mock_request_review.call_count == ci_monitor.DEFAULT_MAX_COPILOT_RETRY

    @patch("ci_monitor.main_loop.request_copilot_review")
    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.get_unresolved_threads")
    @patch("ci_monitor.main_loop.get_review_comments")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_successful_retry_after_error(
        self,
        mock_get_pr_state,
        mock_sleep,
        mock_get_comments,
        mock_get_threads,
        mock_codex_pending,
        mock_is_error,
        mock_request_review,
    ):
        """Test successful recovery after retry."""
        get_pr_state_call_count = 0
        is_error_call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal get_pr_state_call_count
            get_pr_state_call_count += 1
            if get_pr_state_call_count == 1:
                # Initial: Copilot assigned
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif get_pr_state_call_count == 2:
                # Copilot finished with error (no pending reviewers)
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            elif get_pr_state_call_count <= 4:
                # After retry: Copilot re-assigned and pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["copilot-pull-request-reviewer[bot]"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # Copilot finished successfully, CI passed
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        def is_error_side_effect(pr_number):
            # Use independent counter to avoid synchronization issues
            nonlocal is_error_call_count
            is_error_call_count += 1
            # Return error only on first call (when Copilot first finishes)
            if is_error_call_count == 1:
                return (True, "Copilot encountered an error")
            return (False, None)

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.side_effect = is_error_side_effect
        mock_request_review.return_value = (True, "")
        mock_get_comments.return_value = []
        mock_get_threads.return_value = []

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        # Should succeed after retry
        assert result.success
        # Should have retried once
        assert mock_request_review.call_count == 1

    @patch("ci_monitor.main_loop.request_copilot_review")
    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_retry_request_failure_logged(
        self,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_is_error,
        mock_request_review,
    ):
        """Test that failed retry request is handled gracefully."""
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
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (True, "Copilot encountered an error")
        # Retry request fails
        mock_request_review.return_value = (False, "API rate limit exceeded")

        result = ci_monitor.monitor_pr("123", timeout_minutes=1)

        # Should still attempt retries even if request fails
        assert mock_request_review.called
        # After max retries, should fail
        assert not result.success

    @patch("ci_monitor.main_loop.request_copilot_review")
    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.events.log")
    def test_retry_count_in_log_message(
        self,
        mock_log,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_is_error,
        mock_request_review,
    ):
        """Test that retry count is included in log messages."""
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
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_is_error.return_value = (True, "Copilot encountered an error")
        mock_request_review.return_value = (True, "")

        ci_monitor.monitor_pr("123", timeout_minutes=1)

        # Check that log was called with retry count format
        # Extract first argument (log message) from each call
        log_messages = [call[0][0] for call in mock_log.call_args_list if call[0]]
        retry_logs = [msg for msg in log_messages if "retrying" in msg.lower()]
        assert len(retry_logs) > 0, "Should log retry attempts"

        # Verify format includes count like "1/3" or "2/3"
        has_retry_count = any(
            f"/{ci_monitor.DEFAULT_MAX_COPILOT_RETRY}" in msg for msg in log_messages
        )
        assert has_retry_count, "Should include retry count in format X/MAX"

    @patch("ci_monitor.main_loop.request_copilot_review")
    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    @patch("ci_monitor.events.log")
    @patch("ci_monitor.main_loop.time.time")
    def test_retry_wait_timeout_proceeds_to_next_retry(
        self,
        mock_time,
        mock_log,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_is_error,
        mock_request_review,
    ):
        """Test that retry wait timeout triggers next retry attempt (Issue #1501).

        When copilot_retry_wait_polls >= DEFAULT_MAX_RETRY_WAIT_POLLS,
        _handle_copilot_retry_wait() returns RetryWaitStatus.TIMEOUT
        and the system should proceed with the next retry attempt.
        """
        # Track time progression - allow enough iterations for retry wait timeout
        time_call_count = 0
        max_retry_wait_polls = ci_monitor.DEFAULT_MAX_RETRY_WAIT_POLLS

        def time_side_effect():
            nonlocal time_call_count
            time_call_count += 1
            # First few calls: within timeout (1 minute = 60 seconds)
            # After retry wait timeout + second retry, trigger overall timeout
            if time_call_count <= max_retry_wait_polls + 10:
                return time_call_count * 5  # 5 seconds per call, staying within timeout
            return 120  # Exceed 1 minute timeout to exit

        mock_time.side_effect = time_side_effect

        get_pr_state_call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal get_pr_state_call_count
            get_pr_state_call_count += 1
            if get_pr_state_call_count == 1:
                # Initial: Copilot assigned
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            # All subsequent calls: Copilot no longer assigned, triggers error check path
            return (
                PRState(
                    merge_state=MergeState.CLEAN,
                    pending_reviewers=[],
                    check_status=CheckStatus.PENDING,
                ),
                None,
            )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False

        # Keep returning error to trigger retry-wait polling
        # After first retry request succeeds, copilot_retry_in_progress becomes True
        # Then we wait for DEFAULT_MAX_RETRY_WAIT_POLLS before timing out
        is_error_call_count = 0

        def is_error_side_effect(pr_number):
            nonlocal is_error_call_count
            is_error_call_count += 1
            # Keep returning error to trigger the retry path
            return (True, "Copilot encountered an error")

        mock_is_error.side_effect = is_error_side_effect
        mock_request_review.return_value = (True, "")  # Retry request succeeds

        ci_monitor.monitor_pr("123", timeout_minutes=1)

        # Verify retry was attempted at least once
        assert mock_request_review.called, "Retry should be requested"

        # Verify log messages include retry wait timeout message
        log_messages = [call[0][0] for call in mock_log.call_args_list if call[0]]
        assert len(log_messages) > 0, "Should have log messages"

        # Verify retry wait polling occurred (looking for "Waiting for Copilot" message)
        waiting_logs = [msg for msg in log_messages if "Waiting for Copilot" in msg]
        timeout_logs = [msg for msg in log_messages if "timeout" in msg.lower()]

        # Either waiting logs or timeout logs should exist if retry-wait path was exercised
        assert len(waiting_logs) > 0 or len(timeout_logs) > 0, (
            f"Should have retry wait logs. Got: {log_messages[:10]}"
        )


class TestCopilotPendingTimeout:
    """Tests for Copilot pending timeout detection (Issue #1532)."""

    def test_constants_exist(self):
        """Should have timeout and max recreate constants."""
        assert hasattr(ci_monitor, "DEFAULT_COPILOT_PENDING_TIMEOUT")
        assert hasattr(ci_monitor, "DEFAULT_MAX_PR_RECREATE")
        assert ci_monitor.DEFAULT_COPILOT_PENDING_TIMEOUT == 300  # 5 minutes
        assert ci_monitor.DEFAULT_MAX_PR_RECREATE == 1

    def test_monitor_result_has_details_field(self):
        """MonitorResult should have details field for PR recreation info."""
        MonitorResult = ci_monitor.MonitorResult
        result = MonitorResult(
            success=False,
            message="Test",
            details={"recreated_pr": "456", "original_pr": "123"},
        )
        assert result.details["recreated_pr"] == "456"
        assert result.details["original_pr"] == "123"

    @patch("ci_monitor.main_loop.save_monitor_state")
    @patch("ci_monitor.main_loop.recreate_pr")
    @patch("ci_monitor.main_loop.log_ci_monitor_event")
    @patch("ci_monitor.main_loop.check_rate_limit")
    @patch("ci_monitor.main_loop.time.time")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_pr_recreation_on_pending_timeout(
        self,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_time,
        mock_check_rate,
        mock_log_event,
        mock_recreate_pr,
        mock_save_state,
    ):
        """Should recreate PR when Copilot is pending for more than 300 seconds."""
        # Mock rate limit to avoid API calls
        mock_check_rate.return_value = (5000, 5000, 0)

        # Simulate time progression for timeout detection:
        # - First few calls return 0 (start_time, copilot_pending_since initialization)
        # - Later calls return 400 (exceeds DEFAULT_COPILOT_PENDING_TIMEOUT of 300s)
        time_call_count = [0]

        def time_side_effect():
            time_call_count[0] += 1
            if time_call_count[0] <= 2:
                return 0  # start_time and initial checks
            elif time_call_count[0] <= 4:
                return 0  # First iteration: copilot_pending_since starts
            else:
                return 400  # Exceeds 300s timeout, triggers recreation

        mock_time.side_effect = time_side_effect

        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            # Always return Copilot as pending reviewer
            return (
                PRState(
                    merge_state=MergeState.CLEAN,
                    pending_reviewers=["Copilot"],
                    check_status=CheckStatus.PENDING,
                ),
                None,
            )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        mock_recreate_pr.return_value = (True, "456", "PR recreated successfully")

        result = ci_monitor.monitor_pr("123", timeout_minutes=10)

        # Verify recreate_pr was called
        mock_recreate_pr.assert_called_once_with("123")
        # Result should indicate PR was recreated
        assert not result.success  # Original PR monitoring ended
        assert result.details.get("recreated_pr") == "456"

    @patch("ci_monitor.main_loop.save_monitor_state")
    @patch("ci_monitor.main_loop.recreate_pr")
    @patch("ci_monitor.main_loop.log_ci_monitor_event")
    @patch("ci_monitor.main_loop.check_rate_limit")
    @patch("ci_monitor.main_loop.time.time")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_max_recreate_count_prevents_further_recreations(
        self,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_time,
        mock_check_rate,
        mock_log_event,
        mock_recreate_pr,
        mock_save_state,
    ):
        """Should not recreate PR more than DEFAULT_MAX_PR_RECREATE times.

        This test verifies that even if recreation fails, the counter prevents
        infinite recreation attempts. After reaching the max count, monitoring
        continues until CI succeeds or timeout.
        """
        # Mock rate limit to avoid API calls
        mock_check_rate.return_value = (5000, 5000, 0)

        # Time increases by 100s per get_pr_state call. After a few iterations,
        # pending_duration exceeds 300s timeout, triggering recreation attempt.
        call_index = [0]

        def time_side_effect():
            # Time = call_index * 100, incremented by get_pr_state_side_effect
            return call_index[0] * 100

        mock_time.side_effect = time_side_effect

        state_call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal state_call_count
            state_call_count += 1
            call_index[0] += 1  # Advance time by 100s per call

            if state_call_count > 5:
                # Return CI success with Copilot still pending
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],  # Keep pending for timeout logic
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )
            return (
                PRState(
                    merge_state=MergeState.CLEAN,
                    pending_reviewers=["Copilot"],
                    check_status=CheckStatus.PENDING,
                ),
                None,
            )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False
        # Recreation fails, so monitoring continues with limited retry count
        mock_recreate_pr.return_value = (False, None, "Recreation failed")

        ci_monitor.monitor_pr("123", timeout_minutes=10)

        # recreate_pr should be called exactly DEFAULT_MAX_PR_RECREATE times (1)
        assert mock_recreate_pr.call_count == ci_monitor.DEFAULT_MAX_PR_RECREATE

    @patch("ci_monitor.main_loop.save_monitor_state")
    @patch("ci_monitor.main_loop.recreate_pr")
    @patch("ci_monitor.main_loop.log_ci_monitor_event")
    @patch("ci_monitor.main_loop.check_rate_limit")
    @patch("ci_monitor.main_loop.is_copilot_review_error")
    @patch("ci_monitor.main_loop.time.time")
    @patch("ci_monitor.main_loop.is_codex_review_pending")
    @patch("ci_monitor.main_loop.time.sleep")
    @patch("ci_monitor.main_loop.get_pr_state")
    def test_pending_timer_resets_when_copilot_no_longer_pending(
        self,
        mock_get_pr_state,
        mock_sleep,
        mock_codex_pending,
        mock_time,
        mock_is_error,
        mock_check_rate,
        mock_log_event,
        mock_recreate_pr,
        mock_save_state,
    ):
        """Should reset pending timer when Copilot is no longer pending.

        This test verifies that copilot_pending_since is reset to None when
        Copilot finishes reviewing (pending_reviewers becomes empty). Without
        this reset, a new pending state would inherit the old timer and
        incorrectly trigger timeout.
        """
        # Mock rate limit and error check to avoid API calls
        mock_check_rate.return_value = (5000, 5000, 0)
        mock_is_error.return_value = (False, None)

        # Time always returns 0 - since Copilot finishes before timeout (300s),
        # no recreation should occur regardless of time values
        time_call_count = [0]

        def time_side_effect():
            time_call_count[0] += 1
            return 0  # No time progression needed for this test

        mock_time.side_effect = time_side_effect

        call_count = 0

        def get_pr_state_side_effect(pr_number):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # First 2 calls: Copilot pending (timer starts)
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=["Copilot"],
                        check_status=CheckStatus.PENDING,
                    ),
                    None,
                )
            else:
                # Third call: Copilot finished (timer resets), CI passed
                return (
                    PRState(
                        merge_state=MergeState.CLEAN,
                        pending_reviewers=[],  # Timer resets when this becomes empty
                        check_status=CheckStatus.SUCCESS,
                    ),
                    None,
                )

        mock_get_pr_state.side_effect = get_pr_state_side_effect
        mock_codex_pending.return_value = False

        result = ci_monitor.monitor_pr("123", timeout_minutes=10)

        # recreate_pr should NOT be called because Copilot finished normally
        mock_recreate_pr.assert_not_called()
        # CI passed successfully
        assert result.ci_passed
