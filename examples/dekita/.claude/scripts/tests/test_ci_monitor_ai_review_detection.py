#!/usr/bin/env python3
"""Unit tests for AI reviewer detection in ci_monitor.

Covers:
- is_ai_reviewer function
- has_copilot_or_codex_reviewer function
- Async reviewer re-request detection after rebase
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
