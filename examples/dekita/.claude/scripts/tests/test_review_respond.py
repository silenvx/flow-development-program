#!/usr/bin/env python3
"""
Unit tests for review-respond.py

Tests cover:
- post_reply function (correct endpoint usage)
- resolve_thread function
- get_repo_info function
- parse_quality_options function (Issue #1432)
- Quality tracking integration (Issue #1432)
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# review-respond.py has a hyphen in the name, so we need to import it dynamically
SCRIPT_PATH = Path(__file__).parent.parent / "review-respond.py"
_spec = importlib.util.spec_from_file_location("review_respond", SCRIPT_PATH)
review_respond = importlib.util.module_from_spec(_spec)
sys.modules["review_respond"] = review_respond
_spec.loader.exec_module(review_respond)

# Import symbols from the dynamically loaded module
post_reply = review_respond.post_reply
resolve_thread = review_respond.resolve_thread
get_repo_info = review_respond.get_repo_info
format_verified_message = review_respond.format_verified_message
parse_quality_options = review_respond.parse_quality_options


class TestPostReply:
    """Tests for post_reply function."""

    @patch("review_respond.subprocess.run")
    def test_uses_replies_endpoint_with_pr_number(self, mock_run: MagicMock):
        """Test that post_reply uses the /replies endpoint with PR number (Issue #748, #754)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = post_reply("123", "456789", "Test message", "owner", "repo")

        assert result is not None  # 0 indicates success with unknown ID
        # Verify the correct endpoint is used (must include PR number per GitHub API docs)
        call_args = mock_run.call_args
        command = call_args[0][0]
        # The endpoint should be /repos/{owner}/{repo}/pulls/{pr_number}/comments/{comment_id}/replies
        assert "/repos/owner/repo/pulls/123/comments/456789/replies" in command
        # Verify in_reply_to is NOT used
        assert "in_reply_to" not in command

    @patch("review_respond.subprocess.run")
    def test_includes_pr_number_in_endpoint(self, mock_run: MagicMock):
        """Test that the endpoint includes PR number (Issue #754)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        post_reply("999", "123456", "Test", "owner", "repo")

        call_args = mock_run.call_args
        command = call_args[0][0]
        # The endpoint MUST include /pulls/{pr_number}/comments/ per GitHub API docs
        endpoint_str = " ".join(command)
        assert "/pulls/999/comments/123456/replies" in endpoint_str

    @patch("review_respond.subprocess.run")
    def test_adds_signature_if_missing(self, mock_run: MagicMock):
        """Test that Claude Code signature is added if not present."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        post_reply("123", "456", "Test message", "owner", "repo")

        call_args = mock_run.call_args
        command = call_args[0][0]
        # Find the body parameter
        body_param = None
        for arg in command:
            if arg.startswith("body="):
                body_param = arg
                break
        assert body_param is not None
        assert "-- Claude Code" in body_param

    @patch("review_respond.subprocess.run")
    def test_does_not_duplicate_signature(self, mock_run: MagicMock):
        """Test that signature is not duplicated if already present."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        post_reply("123", "456", "Test message\n\n-- Claude Code", "owner", "repo")

        call_args = mock_run.call_args
        command = call_args[0][0]
        body_param = None
        for arg in command:
            if arg.startswith("body="):
                body_param = arg
                break
        # Should only have one occurrence of the signature
        assert body_param.count("-- Claude Code") == 1

    @patch("review_respond.subprocess.run")
    def test_returns_false_on_error(self, mock_run: MagicMock):
        """Test that post_reply returns False on subprocess error."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")

        result = post_reply("123", "456", "Test", "owner", "repo")

        assert not result

    @patch("review_respond.subprocess.run")
    def test_returns_false_on_exception(self, mock_run: MagicMock):
        """Test that post_reply returns False on exception."""
        mock_run.side_effect = Exception("Timeout")

        result = post_reply("123", "456", "Test", "owner", "repo")

        assert not result


class TestResolveThread:
    """Tests for resolve_thread function."""

    @patch("review_respond.subprocess.run")
    def test_resolves_thread_successfully(self, mock_run: MagicMock):
        """Test successful thread resolution."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"data": {"resolveReviewThread": {"thread": {"isResolved": true}}}}',
            stderr="",
        )

        result = resolve_thread("PRRT_123")

        assert result

    @patch("review_respond.subprocess.run")
    def test_returns_false_on_not_resolved(self, mock_run: MagicMock):
        """Test returns False when thread is not resolved."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"data": {"resolveReviewThread": {"thread": {"isResolved": false}}}}',
            stderr="",
        )

        result = resolve_thread("PRRT_123")

        assert not result

    @patch("review_respond.subprocess.run")
    def test_returns_false_on_error(self, mock_run: MagicMock):
        """Test returns False on subprocess error."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")

        result = resolve_thread("PRRT_123")

        assert not result


class TestGetRepoInfo:
    """Tests for get_repo_info function."""

    @patch("review_respond.subprocess.run")
    def test_returns_owner_and_repo(self, mock_run: MagicMock):
        """Test successful repo info retrieval."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"owner": {"login": "testowner"}, "name": "testrepo"}',
            stderr="",
        )

        owner, repo = get_repo_info()

        assert owner == "testowner"
        assert repo == "testrepo"

    @patch("review_respond.subprocess.run")
    def test_returns_empty_on_error(self, mock_run: MagicMock):
        """Test returns empty strings on error."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")

        owner, repo = get_repo_info()

        assert owner == ""
        assert repo == ""


class TestFormatVerifiedMessage:
    """Tests for format_verified_message function."""

    def test_adds_prefixes_when_missing(self):
        """Test that prefixes are added when not present."""
        result = format_verified_message("処理順序修正", "file.py:10-20")

        assert result == "修正済み: 処理順序修正\n\nVerified: file.py:10-20"

    def test_preserves_existing_fix_prefix(self):
        """Test that existing fix prefix is preserved."""
        result = format_verified_message("修正済み: 処理順序修正", "file.py:10-20")

        assert result == "修正済み: 処理順序修正\n\nVerified: file.py:10-20"

    def test_preserves_existing_verify_prefix(self):
        """Test that existing verify prefix is preserved."""
        result = format_verified_message("処理順序修正", "Verified: file.py:10-20")

        assert result == "修正済み: 処理順序修正\n\nVerified: file.py:10-20"

    def test_preserves_both_existing_prefixes(self):
        """Test that both existing prefixes are preserved."""
        result = format_verified_message("修正済み: 処理順序修正", "Verified: file.py:10-20")

        assert result == "修正済み: 処理順序修正\n\nVerified: file.py:10-20"

    def test_fix_prefix_without_space_gets_added(self):
        """Test that fix without colon+space gets prefix added."""
        # "修正済み" without ": " should still get prefix added
        result = format_verified_message("修正済みtest", "file.py:10-20")

        assert result == "修正済み: 修正済みtest\n\nVerified: file.py:10-20"

    def test_verify_prefix_without_space_gets_added(self):
        """Test that verify without colon+space gets prefix added."""
        # "Verified" without ": " should still get prefix added
        result = format_verified_message("fix", "Verifiedtest")

        assert result == "修正済み: fix\n\nVerified: Verifiedtest"

    def test_does_not_include_signature(self):
        """Test that signature is not added by this function (added by post_reply)."""
        result = format_verified_message("fix", "verify")

        assert "-- Claude Code" not in result


class TestParseQualityOptions:
    """Tests for parse_quality_options function (Issue #1432)."""

    def test_default_resolution_is_accepted(self):
        """Test that default resolution is 'accepted'."""
        opts = parse_quality_options([])

        assert opts.resolution == "accepted"
        assert opts.validity is None
        assert opts.category is None
        assert opts.issue is None
        assert opts.reason is None

    def test_parses_all_options(self):
        """Test parsing all quality options."""
        opts = parse_quality_options(
            [
                "--resolution",
                "rejected",
                "--validity",
                "invalid",
                "--category",
                "style",
                "--issue",
                "123",
                "--reason",
                "False positive",
            ]
        )

        assert opts.resolution == "rejected"
        assert opts.validity == "invalid"
        assert opts.category == "style"
        assert opts.issue == "123"
        assert opts.reason == "False positive"

    def test_parses_issue_created(self):
        """Test parsing issue_created resolution."""
        opts = parse_quality_options(
            [
                "--resolution",
                "issue_created",
                "--issue",
                "456",
            ]
        )

        assert opts.resolution == "issue_created"
        assert opts.issue == "456"

    def test_invalid_resolution_raises(self):
        """Test that invalid resolution value raises error."""
        with pytest.raises(SystemExit):
            parse_quality_options(["--resolution", "invalid_value"])

    def test_invalid_validity_raises(self):
        """Test that invalid validity value raises error."""
        with pytest.raises(SystemExit):
            parse_quality_options(["--validity", "invalid_value"])


class TestQualityTrackingIntegration:
    """Tests for quality tracking integration (Issue #1432)."""

    @patch("review_respond.record_response")
    @patch("review_respond.resolve_thread")
    @patch("review_respond.post_reply")
    @patch("review_respond.get_repo_info")
    def test_records_response_on_success(
        self,
        mock_repo: MagicMock,
        mock_reply: MagicMock,
        mock_resolve: MagicMock,
        mock_record: MagicMock,
    ):
        """Test that record_response is called after successful reply and resolve."""
        mock_repo.return_value = ("owner", "repo")
        mock_reply.return_value = 123  # Success
        mock_resolve.return_value = True  # Success

        # Simulate: review-respond.py 123 456 PRRT_xxx "message"
        with patch.object(sys, "argv", ["script", "123", "456", "PRRT_xxx", "message"]):
            # main() returns normally on success (no sys.exit)
            review_respond.main()

        mock_record.assert_called_once_with(
            pr_number="123",
            comment_id="456",
            resolution="accepted",  # Default
            validity=None,
            category=None,
            issue_created=None,
            reason=None,
        )

    @patch("review_respond.record_response")
    @patch("review_respond.resolve_thread")
    @patch("review_respond.post_reply")
    @patch("review_respond.get_repo_info")
    def test_records_with_custom_resolution(
        self,
        mock_repo: MagicMock,
        mock_reply: MagicMock,
        mock_resolve: MagicMock,
        mock_record: MagicMock,
    ):
        """Test that custom resolution options are passed to record_response."""
        mock_repo.return_value = ("owner", "repo")
        mock_reply.return_value = 123
        mock_resolve.return_value = True

        with patch.object(
            sys,
            "argv",
            [
                "script",
                "123",
                "456",
                "PRRT_xxx",
                "message",
                "--resolution",
                "rejected",
                "--validity",
                "invalid",
                "--reason",
                "False positive",
            ],
        ):
            review_respond.main()

        mock_record.assert_called_once_with(
            pr_number="123",
            comment_id="456",
            resolution="rejected",
            validity="invalid",
            category=None,
            issue_created=None,
            reason="False positive",
        )

    @patch("review_respond.record_response")
    @patch("review_respond.resolve_thread")
    @patch("review_respond.post_reply")
    @patch("review_respond.get_repo_info")
    def test_does_not_record_on_reply_failure(
        self,
        mock_repo: MagicMock,
        mock_reply: MagicMock,
        mock_resolve: MagicMock,
        mock_record: MagicMock,
    ):
        """Test that record_response is NOT called when reply fails."""
        mock_repo.return_value = ("owner", "repo")
        mock_reply.return_value = None  # Failure

        with patch.object(sys, "argv", ["script", "123", "456", "PRRT_xxx", "message"]):
            with pytest.raises(SystemExit) as exc_info:
                review_respond.main()
            assert exc_info.value.code == 1

        mock_record.assert_not_called()

    @patch("review_respond.record_response")
    @patch("review_respond.resolve_thread")
    @patch("review_respond.post_reply")
    @patch("review_respond.get_repo_info")
    def test_does_not_record_on_resolve_failure(
        self,
        mock_repo: MagicMock,
        mock_reply: MagicMock,
        mock_resolve: MagicMock,
        mock_record: MagicMock,
    ):
        """Test that record_response is NOT called when resolve fails."""
        mock_repo.return_value = ("owner", "repo")
        mock_reply.return_value = 123  # Success
        mock_resolve.return_value = False  # Failure

        with patch.object(sys, "argv", ["script", "123", "456", "PRRT_xxx", "message"]):
            with pytest.raises(SystemExit) as exc_info:
                review_respond.main()
            assert exc_info.value.code == 1

        mock_record.assert_not_called()

    def test_issue_created_requires_issue_flag(self, capsys):
        """Test that --resolution issue_created requires --issue flag."""
        with patch.object(
            sys,
            "argv",
            [
                "script",
                "123",
                "456",
                "PRRT_xxx",
                "message",
                "--resolution",
                "issue_created",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                review_respond.main()
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "--issue is required" in captured.err

    @patch("review_respond.record_response")
    @patch("review_respond.resolve_thread")
    @patch("review_respond.post_reply")
    @patch("review_respond.get_repo_info")
    def test_verified_mode_with_quality_options(
        self,
        mock_repo: MagicMock,
        mock_reply: MagicMock,
        mock_resolve: MagicMock,
        mock_record: MagicMock,
    ):
        """Test that --verified mode works with quality tracking options."""
        mock_repo.return_value = ("owner", "repo")
        mock_reply.return_value = 123
        mock_resolve.return_value = True

        # --verified mode combined with --resolution rejected
        with patch.object(
            sys,
            "argv",
            [
                "script",
                "123",
                "456",
                "PRRT_xxx",
                "--verified",
                "fix message",
                "verify details",
                "--resolution",
                "rejected",
                "--validity",
                "invalid",
            ],
        ):
            review_respond.main()

        mock_record.assert_called_once_with(
            pr_number="123",
            comment_id="456",
            resolution="rejected",
            validity="invalid",
            category=None,
            issue_created=None,
            reason=None,
        )

    @patch("review_respond.record_response")
    @patch("review_respond.resolve_thread")
    @patch("review_respond.post_reply")
    @patch("review_respond.get_repo_info")
    def test_continues_on_record_failure(
        self,
        mock_repo: MagicMock,
        mock_reply: MagicMock,
        mock_resolve: MagicMock,
        mock_record: MagicMock,
        capsys,
    ):
        """Test that script succeeds even if record_response fails (Issue #1432 P2)."""
        mock_repo.return_value = ("owner", "repo")
        mock_reply.return_value = 123
        mock_resolve.return_value = True
        mock_record.side_effect = OSError("Permission denied")

        with patch.object(sys, "argv", ["script", "123", "456", "PRRT_xxx", "message"]):
            # Should NOT raise - reply/resolve succeeded
            review_respond.main()

        captured = capsys.readouterr()
        assert "Warning: Failed to record response" in captured.err
        assert "Comment replied and thread resolved" in captured.out

    @patch("review_respond.log_review_comment_response")
    @patch("review_respond.record_response")
    @patch("review_respond.resolve_thread")
    @patch("review_respond.post_reply")
    @patch("review_respond.get_repo_info")
    def test_logs_review_comment_on_success(
        self,
        mock_repo: MagicMock,
        mock_reply: MagicMock,
        mock_resolve: MagicMock,
        mock_record: MagicMock,
        mock_log: MagicMock,
    ):
        """Test that log_review_comment_response is called from main() (Issue #1639)."""
        mock_repo.return_value = ("owner", "repo")
        mock_reply.return_value = 123
        mock_resolve.return_value = True

        with patch.object(sys, "argv", ["script", "123", "456", "PRRT_xxx", "test message"]):
            review_respond.main()

        mock_log.assert_called_once_with(
            pr_number="123",
            comment_id="456",
            message="test message",
            resolution="accepted",
            category=None,
            issue_created=None,
        )


class TestLogReviewCommentResponse:
    """Tests for log_review_comment_response function (Issue #1639)."""

    def test_creates_log_entry(self, tmp_path):
        """Test that log_review_comment_response creates a valid log entry."""
        log_file = tmp_path / "review-comments.jsonl"

        with patch.object(review_respond, "REVIEW_COMMENTS_LOG", log_file):
            with patch.object(review_respond, "EXECUTION_LOG_DIR", tmp_path):
                with patch.object(
                    review_respond, "_get_session_id_fallback", return_value="test-session"
                ):
                    review_respond.log_review_comment_response(
                        pr_number="123",
                        comment_id="456789",
                        message="Test response message",
                        resolution="accepted",
                        category="bug",
                        issue_created=None,
                    )

        assert log_file.exists()
        with open(log_file) as f:
            entry = json.loads(f.read().strip())

        assert entry["pr_number"] == 123
        assert entry["comment_id"] == 456789
        assert entry["resolution"] == "accepted"
        assert entry["response"] == "Test response message"
        assert entry["category"] == "bug"
        assert entry["session_id"] == "test-session"
        assert "timestamp" in entry

    def test_truncates_long_messages(self, tmp_path):
        """Test that long messages are truncated to 200 chars."""
        log_file = tmp_path / "review-comments.jsonl"
        long_message = "x" * 300

        with patch.object(review_respond, "REVIEW_COMMENTS_LOG", log_file):
            with patch.object(review_respond, "EXECUTION_LOG_DIR", tmp_path):
                with patch.object(review_respond, "_get_session_id_fallback", return_value="test"):
                    review_respond.log_review_comment_response(
                        pr_number="123",
                        comment_id="456",
                        message=long_message,
                        resolution="accepted",
                        category=None,
                        issue_created=None,
                    )

        with open(log_file) as f:
            entry = json.loads(f.read().strip())

        assert len(entry["response"]) == 200

    def test_includes_issue_created(self, tmp_path):
        """Test that issue_created is included when provided."""
        log_file = tmp_path / "review-comments.jsonl"

        with patch.object(review_respond, "REVIEW_COMMENTS_LOG", log_file):
            with patch.object(review_respond, "EXECUTION_LOG_DIR", tmp_path):
                with patch.object(review_respond, "_get_session_id_fallback", return_value="test"):
                    review_respond.log_review_comment_response(
                        pr_number="123",
                        comment_id="456",
                        message="Issue created",
                        resolution="issue_created",
                        category=None,
                        issue_created="789",
                    )

        with open(log_file) as f:
            entry = json.loads(f.read().strip())

        assert entry["issue_created"] == 789

    def test_does_not_fail_on_error(self, tmp_path, capsys):
        """Test that logging failure does not raise exception.

        Uses mock to simulate I/O error for cross-platform compatibility.
        """
        log_file = tmp_path / "review-comments.jsonl"

        with patch.object(review_respond, "REVIEW_COMMENTS_LOG", log_file):
            with patch.object(review_respond, "EXECUTION_LOG_DIR", tmp_path):
                # Mock open() to raise IOError for cross-platform compatibility
                with patch("builtins.open", side_effect=OSError("Simulated I/O error")):
                    # Should not raise
                    review_respond.log_review_comment_response(
                        pr_number="123",
                        comment_id="456",
                        message="Test",
                        resolution="accepted",
                        category=None,
                        issue_created=None,
                    )

        captured = capsys.readouterr()
        assert "Warning: Failed to log review response" in captured.err
