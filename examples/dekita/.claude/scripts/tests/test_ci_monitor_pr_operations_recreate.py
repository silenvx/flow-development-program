#!/usr/bin/env python3
"""Unit tests for PR recreation functions in ci_monitor.pr_operations module.

Covers:
- recreate_pr function
- reopen_pr_with_retry function
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add scripts directory to path so we can import ci_monitor package
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the ci_monitor package for attribute access (Issue #2624)
import ci_monitor


class TestRecreatePr:
    """Tests for recreate_pr function (Issue #1532)."""

    def test_recreate_pr_success(self):
        """Should close old PR and create new one successfully."""
        recreate_pr = ci_monitor.recreate_pr

        pr_data = json.dumps(
            {
                "title": "Test PR",
                "body": "Test body",
                "headRefName": "feat/test",
                "baseRefName": "main",
                "labels": [{"name": "bug"}],
                "assignees": [{"login": "user1"}],
                "isDraft": False,
            }
        )

        with patch.object(ci_monitor, "run_gh_command") as mock_run:
            # Mock: view returns PR data, close succeeds, create returns URL
            mock_run.side_effect = [
                (True, pr_data),  # pr view
                (True, ""),  # pr close
                (True, "https://github.com/owner/repo/pull/456"),  # pr create
            ]

            success, new_pr_number, message = recreate_pr("123")

            assert success is True
            assert new_pr_number == "456"
            assert "PR #123" in message
            assert "PR #456" in message

    def test_recreate_pr_view_fails(self):
        """Should return error when PR view fails."""
        recreate_pr = ci_monitor.recreate_pr

        with patch.object(ci_monitor, "run_gh_command") as mock_run:
            mock_run.return_value = (False, "Not found")

            success, new_pr_number, message = recreate_pr("123")

            assert success is False
            assert new_pr_number is None
            assert "Failed to get PR details" in message

    def test_recreate_pr_close_fails(self):
        """Should return error when PR close fails."""
        recreate_pr = ci_monitor.recreate_pr

        pr_data = json.dumps(
            {
                "title": "Test PR",
                "body": "Test body",
                "headRefName": "feat/test",
                "baseRefName": "main",
                "labels": [],
                "assignees": [],
                "isDraft": False,
            }
        )

        with patch.object(ci_monitor, "run_gh_command") as mock_run:
            mock_run.side_effect = [
                (True, pr_data),  # pr view
                (False, "Cannot close"),  # pr close
            ]

            success, new_pr_number, message = recreate_pr("123")

            assert success is False
            assert new_pr_number is None
            assert "PRのクローズに失敗しました" in message

    def test_recreate_pr_create_fails_but_reopen_succeeds(self):
        """Should reopen original PR when new PR creation fails."""
        recreate_pr = ci_monitor.recreate_pr

        pr_data = json.dumps(
            {
                "title": "Test PR",
                "body": "Test body",
                "headRefName": "feat/test",
                "baseRefName": "main",
                "labels": [],
                "assignees": [],
                "isDraft": False,
            }
        )

        with patch.object(ci_monitor, "run_gh_command") as mock_run:
            mock_run.side_effect = [
                (True, pr_data),  # pr view
                (True, ""),  # pr close
                (False, "Cannot create"),  # pr create
                (True, ""),  # pr reopen
            ]

            success, new_pr_number, message = recreate_pr("123")

            assert success is False
            assert new_pr_number is None
            assert "新しいPRの作成に失敗しました" in message
            assert "再オープンしました" in message
            # Verify reopen was called
            assert mock_run.call_count == 4
            reopen_call = mock_run.call_args_list[3]
            assert "reopen" in reopen_call[0][0]

    def test_recreate_pr_create_fails_and_reopen_fails(self):
        """Should report both failures when create and reopen fail after 3 retries."""
        recreate_pr = ci_monitor.recreate_pr

        pr_data = json.dumps(
            {
                "title": "Test PR",
                "body": "Test body",
                "headRefName": "feat/test",
                "baseRefName": "main",
                "labels": [],
                "assignees": [],
                "isDraft": False,
            }
        )

        with (
            patch.object(ci_monitor, "run_gh_command") as mock_run,
            patch.object(ci_monitor, "save_monitor_state") as mock_save_state,
            patch("time.sleep"),  # Skip delay between retries
        ):
            mock_run.side_effect = [
                (True, pr_data),  # pr view
                (True, ""),  # pr close
                (False, "Cannot create"),  # pr create
                (False, "Cannot reopen 1"),  # pr reopen attempt 1
                (False, "Cannot reopen 2"),  # pr reopen attempt 2
                (False, "Cannot reopen 3"),  # pr reopen attempt 3
            ]

            success, new_pr_number, message = recreate_pr("123")

            assert success is False
            assert new_pr_number is None
            assert "新しいPRの作成に失敗しました" in message
            assert "再オープンにも失敗しました（3回リトライ）" in message
            # Verify state was saved (Issue #1558)
            mock_save_state.assert_called_once()
            saved_state = mock_save_state.call_args[0][1]
            assert saved_state["status"] == "pr_recovery_needed"
            assert saved_state["closed_pr"] == "123"
            assert saved_state["reopen_attempts"] == 3

    def test_recreate_pr_empty_title_fails(self):
        """Should return error when PR title is empty."""
        recreate_pr = ci_monitor.recreate_pr

        pr_data = json.dumps(
            {
                "title": "",  # Empty title
                "body": "Test body",
                "headRefName": "feat/test",
                "baseRefName": "main",
                "labels": [],
                "assignees": [],
                "isDraft": False,
            }
        )

        with patch.object(ci_monitor, "run_gh_command") as mock_run:
            mock_run.return_value = (True, pr_data)

            success, new_pr_number, message = recreate_pr("123")

            assert success is False
            assert new_pr_number is None
            assert "PRタイトルを取得できませんでした" in message
            # Should not have tried to close or create
            assert mock_run.call_count == 1

    def test_recreate_pr_empty_head_branch_fails(self):
        """Should return error when PR head branch is empty."""
        recreate_pr = ci_monitor.recreate_pr

        pr_data = json.dumps(
            {
                "title": "Test PR",
                "body": "Test body",
                "headRefName": "",  # Empty head branch
                "baseRefName": "main",
                "labels": [],
                "assignees": [],
                "isDraft": False,
            }
        )

        with patch.object(ci_monitor, "run_gh_command") as mock_run:
            mock_run.return_value = (True, pr_data)

            success, new_pr_number, message = recreate_pr("123")

            assert success is False
            assert new_pr_number is None
            assert "ブランチ名を取得できませんでした" in message
            # Should not have tried to close or create
            assert mock_run.call_count == 1

    def test_recreate_pr_null_body_handled(self):
        """Should handle null body from GitHub API."""
        recreate_pr = ci_monitor.recreate_pr

        pr_data = json.dumps(
            {
                "title": "Test PR",
                "body": None,  # Null body from API
                "headRefName": "feat/test",
                "baseRefName": "main",
                "labels": [],
                "assignees": [],
                "isDraft": False,
            }
        )

        with patch.object(ci_monitor, "run_gh_command") as mock_run:
            mock_run.side_effect = [
                (True, pr_data),  # pr view
                (True, ""),  # pr close
                (True, "https://github.com/owner/repo/pull/456"),  # pr create
            ]

            success, new_pr_number, message = recreate_pr("123")

            assert success is True
            assert new_pr_number == "456"
            # Check that body was handled correctly (should contain recreation note)
            create_call = mock_run.call_args_list[2]
            create_args = create_call[0][0]
            body_index = create_args.index("--body") + 1
            assert "自動再作成" in create_args[body_index]

    def test_recreate_pr_unexpected_url_format(self):
        """Should return success with None pr_number when URL format is unexpected."""
        recreate_pr = ci_monitor.recreate_pr

        pr_data = json.dumps(
            {
                "title": "Test PR",
                "body": "Test body",
                "headRefName": "feat/test",
                "baseRefName": "main",
                "labels": [],
                "assignees": [],
                "isDraft": False,
            }
        )

        with patch.object(ci_monitor, "run_gh_command") as mock_run:
            mock_run.side_effect = [
                (True, pr_data),  # pr view
                (True, ""),  # pr close
                (True, "Created PR successfully"),  # Unexpected format (no URL)
            ]

            success, new_pr_number, message = recreate_pr("123")

            assert success is True
            assert new_pr_number is None  # Could not extract PR number
            assert "PR番号を取得できませんでした" in message

    def test_recreate_pr_json_decode_error(self):
        """Should return error when PR view returns invalid JSON."""
        recreate_pr = ci_monitor.recreate_pr

        with patch.object(ci_monitor, "run_gh_command") as mock_run:
            # Return invalid JSON
            mock_run.return_value = (True, "not valid json {{{")

            success, new_pr_number, message = recreate_pr("123")

            assert success is False
            assert new_pr_number is None
            assert "Failed to parse PR details" in message
            # Should not have tried to close or create
            assert mock_run.call_count == 1

    def test_recreate_pr_preserves_draft_flag(self):
        """Should preserve draft flag when recreating PR."""
        recreate_pr = ci_monitor.recreate_pr

        pr_data = json.dumps(
            {
                "title": "Test PR",
                "body": "Test body",
                "headRefName": "feat/test",
                "baseRefName": "main",
                "labels": [],
                "assignees": [],
                "isDraft": True,  # Draft PR
            }
        )

        with patch.object(ci_monitor, "run_gh_command") as mock_run:
            mock_run.side_effect = [
                (True, pr_data),
                (True, ""),
                (True, "https://github.com/owner/repo/pull/456"),
            ]

            recreate_pr("123")

            # Check that --draft was in the create call
            create_call = mock_run.call_args_list[2]
            assert "--draft" in create_call[0][0]

    def test_recreate_pr_preserves_labels_and_assignees(self):
        """Should preserve labels and assignees when recreating PR."""
        recreate_pr = ci_monitor.recreate_pr

        pr_data = json.dumps(
            {
                "title": "Test PR",
                "body": "Test body",
                "headRefName": "feat/test",
                "baseRefName": "main",
                "labels": [{"name": "bug"}, {"name": "P1"}],
                "assignees": [{"login": "user1"}, {"login": "user2"}],
                "isDraft": False,
            }
        )

        with patch.object(ci_monitor, "run_gh_command") as mock_run:
            mock_run.side_effect = [
                (True, pr_data),
                (True, ""),
                (True, "https://github.com/owner/repo/pull/456"),
            ]

            recreate_pr("123")

            # Check that labels and assignees were in the create call
            create_call = mock_run.call_args_list[2]
            create_args = create_call[0][0]
            assert "--label" in create_args
            assert "bug" in create_args
            assert "P1" in create_args
            assert "--assignee" in create_args
            assert "user1" in create_args
            assert "user2" in create_args


class TestReopenPrWithRetry:
    """Tests for reopen_pr_with_retry function (Issue #1558)."""

    def test_reopen_pr_with_retry_success_first_attempt(self):
        """Should succeed on first attempt without retry."""
        reopen_pr_with_retry = ci_monitor.reopen_pr_with_retry

        with patch.object(ci_monitor, "run_gh_command") as mock_run:
            mock_run.return_value = (True, "")

            success, error, attempts = reopen_pr_with_retry("123", "Test comment")

            assert success is True
            assert error == ""
            assert attempts == 1
            assert mock_run.call_count == 1

    def test_reopen_pr_with_retry_success_second_attempt(self):
        """Should succeed on second attempt after first failure."""
        reopen_pr_with_retry = ci_monitor.reopen_pr_with_retry

        with (
            patch.object(ci_monitor, "run_gh_command") as mock_run,
            patch("time.sleep") as mock_sleep,
        ):
            mock_run.side_effect = [
                (False, "Network error"),  # First attempt fails
                (True, ""),  # Second attempt succeeds
            ]

            success, error, attempts = reopen_pr_with_retry("123", "Test comment")

            assert success is True
            assert error == ""
            assert attempts == 2
            assert mock_run.call_count == 2
            mock_sleep.assert_called_once_with(1)

    def test_reopen_pr_with_retry_success_third_attempt(self):
        """Should succeed on third attempt after two failures."""
        reopen_pr_with_retry = ci_monitor.reopen_pr_with_retry

        with (
            patch.object(ci_monitor, "run_gh_command") as mock_run,
            patch("time.sleep") as mock_sleep,
        ):
            mock_run.side_effect = [
                (False, "Error 1"),  # First attempt fails
                (False, "Error 2"),  # Second attempt fails
                (True, ""),  # Third attempt succeeds
            ]

            success, error, attempts = reopen_pr_with_retry("123", "Test comment")

            assert success is True
            assert error == ""
            assert attempts == 3
            assert mock_run.call_count == 3
            assert mock_sleep.call_count == 2

    def test_reopen_pr_with_retry_all_fail(self):
        """Should return error when all retry attempts fail."""
        reopen_pr_with_retry = ci_monitor.reopen_pr_with_retry

        with (
            patch.object(ci_monitor, "run_gh_command") as mock_run,
            patch("time.sleep") as mock_sleep,
        ):
            mock_run.side_effect = [
                (False, "Error 1"),
                (False, "Error 2"),
                (False, "Final error"),
            ]

            success, error, attempts = reopen_pr_with_retry("123", "Test comment")

            assert success is False
            assert error == "Final error"
            assert attempts == 3
            assert mock_run.call_count == 3
            # Sleep called between attempts 1-2 and 2-3
            assert mock_sleep.call_count == 2

    def test_reopen_pr_with_retry_custom_max_retries(self):
        """Should respect custom max_retries parameter."""
        reopen_pr_with_retry = ci_monitor.reopen_pr_with_retry

        with (
            patch.object(ci_monitor, "run_gh_command") as mock_run,
            patch("time.sleep"),
        ):
            mock_run.return_value = (False, "Keep failing")

            success, error, attempts = reopen_pr_with_retry("123", "Test comment", max_retries=5)

            assert success is False
            assert attempts == 5
            assert mock_run.call_count == 5

    def test_reopen_pr_with_retry_zero_max_retries(self):
        """Should use minimum of 1 when max_retries is 0 or negative."""
        reopen_pr_with_retry = ci_monitor.reopen_pr_with_retry

        with patch.object(ci_monitor, "run_gh_command") as mock_run:
            mock_run.return_value = (False, "Failed")

            # Test with 0
            success, error, attempts = reopen_pr_with_retry("123", "Test comment", max_retries=0)
            assert success is False
            assert attempts == 1
            assert mock_run.call_count == 1

            mock_run.reset_mock()

            # Test with negative
            success, error, attempts = reopen_pr_with_retry("123", "Test comment", max_retries=-5)
            assert success is False
            assert attempts == 1
            assert mock_run.call_count == 1

    def test_reopen_pr_with_retry_passes_comment(self):
        """Should pass the comment to gh command."""
        reopen_pr_with_retry = ci_monitor.reopen_pr_with_retry

        with patch.object(ci_monitor, "run_gh_command") as mock_run:
            mock_run.return_value = (True, "")

            reopen_pr_with_retry("456", "My custom comment")

            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert "pr" in call_args
            assert "reopen" in call_args
            assert "456" in call_args
            assert "--comment" in call_args
            assert "My custom comment" in call_args
