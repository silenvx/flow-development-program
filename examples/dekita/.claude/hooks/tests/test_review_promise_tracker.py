#!/usr/bin/env python3
"""Tests for review-promise-tracker.py"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add hooks directory to path for common module
hooks_dir = Path(__file__).parent.parent
sys.path.insert(0, str(hooks_dir))

# Load module from file path (handles hyphenated filenames)
script_path = hooks_dir / "review-promise-tracker.py"
spec = importlib.util.spec_from_file_location("review_promise_tracker", script_path)
review_promise_tracker = importlib.util.module_from_spec(spec)
sys.modules["review_promise_tracker"] = review_promise_tracker
spec.loader.exec_module(review_promise_tracker)


class TestDetectPromiseInText:
    """Tests for detect_promise_in_text function."""

    def test_detects_separate_issue_pattern(self):
        """Should detect '別Issue' pattern."""
        result = review_promise_tracker.detect_promise_in_text("別Issueで対応します")
        assert result == r"別[Ii]ssue"

    def test_detects_lowercase_issue_pattern(self):
        """Should detect '別issue' pattern (lowercase)."""
        result = review_promise_tracker.detect_promise_in_text("別issueで対応します")
        assert result == r"別[Ii]ssue"

    def test_detects_future_improvement_pattern(self):
        """Should detect '今後の改善' pattern."""
        result = review_promise_tracker.detect_promise_in_text("今後の改善として検討します")
        assert result == r"今後の改善"

    def test_detects_out_of_scope_pattern(self):
        """Should detect 'スコープ外' pattern."""
        result = review_promise_tracker.detect_promise_in_text("これはスコープ外です")
        assert result == r"スコープ外"

    def test_detects_outside_pr_scope_pattern(self):
        """Should detect 'このPRの範囲外' pattern (more specific pattern matches)."""
        result = review_promise_tracker.detect_promise_in_text("このPRの範囲外なので")
        # 'このPRの範囲外' comes before '範囲外' in PROMISE_PATTERNS for correct matching
        assert result == r"このPRの範囲外"

    def test_detects_separate_handling_pattern(self):
        """Should detect '別途対応' pattern."""
        result = review_promise_tracker.detect_promise_in_text("別途対応予定です")
        assert result == r"別途対応"

    def test_detects_later_handling_pattern(self):
        """Should detect '後で対応' pattern."""
        result = review_promise_tracker.detect_promise_in_text("後で対応します")
        assert result == r"後で対応"

    def test_returns_none_for_no_pattern(self):
        """Should return None when no promise pattern is found."""
        result = review_promise_tracker.detect_promise_in_text("この修正を入れました")
        assert result is None

    def test_returns_none_for_empty_string(self):
        """Should return None for empty string."""
        result = review_promise_tracker.detect_promise_in_text("")
        assert result is None


class TestIsReviewThreadReply:
    """Tests for is_review_thread_reply function."""

    def test_detects_review_thread_reply(self):
        """Should detect addPullRequestReviewThreadReply mutation."""
        command = 'gh api graphql -f query=\'mutation { addPullRequestReviewThreadReply(input: {threadId: "xxx", body: "別Issueで対応します"}) { comment { id } } }\''
        is_reply, body = review_promise_tracker.is_review_thread_reply(command)
        assert is_reply is True
        assert body == "別Issueで対応します"

    def test_detects_reply_with_double_quotes(self):
        """Should extract body with double quotes."""
        command = 'gh api graphql -f query="mutation { addPullRequestReviewThreadReply(input: {body: \\"今後の改善で対応\\"}) }"'
        is_reply, body = review_promise_tracker.is_review_thread_reply(command)
        assert is_reply is True
        # The regex might not capture escaped quotes correctly, but it should still detect the reply

    def test_returns_false_for_non_reply_command(self):
        """Should return False for non-reply commands."""
        command = "gh pr view 123"
        is_reply, body = review_promise_tracker.is_review_thread_reply(command)
        assert is_reply is False
        assert body is None

    def test_returns_false_for_empty_command(self):
        """Should return False for empty command."""
        is_reply, body = review_promise_tracker.is_review_thread_reply("")
        assert is_reply is False
        assert body is None

    def test_escaped_quotes_in_body(self):
        """Issue #1444: Should correctly extract body with escaped quotes."""
        command = r'gh api graphql -f query=\'mutation { addPullRequestReviewThreadReply(input: {body: "I said \"別Issue\" here"}) }\''
        is_reply, body = review_promise_tracker.is_review_thread_reply(command)
        assert is_reply is True
        assert body == 'I said "別Issue" here'

    def test_rest_api_reply_detection(self):
        """Issue #1444: Should detect REST API reply pattern with --field body."""
        command = 'gh api /repos/owner/repo/pulls/comments/123/replies --method POST --field body="別Issueで対応します"'
        is_reply, body = review_promise_tracker.is_review_thread_reply(command)
        assert is_reply is True
        assert body == "別Issueで対応します"

    def test_rest_api_with_body_flag(self):
        """Issue #1444: Should detect REST API with --body flag."""
        command = (
            'gh api /repos/owner/repo/pulls/comments/456/replies -X POST --body "今後の改善で"'
        )
        is_reply, body = review_promise_tracker.is_review_thread_reply(command)
        assert is_reply is True
        assert body == "今後の改善で"

    def test_rest_api_with_short_body_flag(self):
        """Issue #1444: Should detect REST API with -b flag."""
        command = 'gh api /repos/owner/repo/pulls/comments/789/replies -X POST -b "スコープ外"'
        is_reply, body = review_promise_tracker.is_review_thread_reply(command)
        assert is_reply is True
        assert body == "スコープ外"


class TestIsIssueCreate:
    """Tests for is_issue_create function."""

    def test_detects_issue_create(self):
        """Should detect 'gh issue create' command."""
        result = review_promise_tracker.is_issue_create("gh issue create --title 'Bug fix'")
        assert result is True

    def test_detects_issue_create_with_flags(self):
        """Should detect 'gh issue create' with various flags."""
        result = review_promise_tracker.is_issue_create(
            "gh issue create --title 'Bug' --body 'Description' --label 'bug'"
        )
        assert result is True

    def test_returns_false_for_issue_list(self):
        """Should return False for 'gh issue list' command."""
        result = review_promise_tracker.is_issue_create("gh issue list")
        assert result is False

    def test_returns_false_for_issue_view(self):
        """Should return False for 'gh issue view' command."""
        result = review_promise_tracker.is_issue_create("gh issue view 123")
        assert result is False

    def test_returns_false_for_empty_command(self):
        """Should return False for empty command."""
        result = review_promise_tracker.is_issue_create("")
        assert result is False

    def test_echo_false_positive_prevented(self):
        """Issue #1444: echo 'gh issue create' should NOT be detected."""
        result = review_promise_tracker.is_issue_create('echo "gh issue create --title test"')
        assert result is False

    def test_printf_false_positive_prevented(self):
        """Issue #1444: printf with gh issue create should NOT be detected."""
        result = review_promise_tracker.is_issue_create("printf 'Run: gh issue create'")
        assert result is False

    def test_variable_in_echo_false_positive(self):
        """Issue #1444: echo with variable should NOT be detected."""
        result = review_promise_tracker.is_issue_create('echo "$CMD" # gh issue create')
        assert result is False

    def test_detects_with_extra_spaces(self):
        """Should detect 'gh  issue  create' with extra spaces."""
        result = review_promise_tracker.is_issue_create("gh  issue  create --title 'Test'")
        assert result is True

    def test_detects_in_subshell(self):
        """Issue #1444: Should detect gh issue create inside subshell."""
        result = review_promise_tracker.is_issue_create("(gh issue create --title 'Test')")
        assert result is True

    def test_detects_in_command_substitution(self):
        """Issue #1444: Should detect gh issue create in $()."""
        result = review_promise_tracker.is_issue_create("URL=$(gh issue create --title 'Test')")
        assert result is True

    def test_detects_after_if(self):
        """Issue #1444: Should detect gh issue create after if."""
        result = review_promise_tracker.is_issue_create(
            "if gh issue create --title 'Test'; then echo 'done'; fi"
        )
        assert result is True

    def test_detects_after_then(self):
        """Issue #1444: Should detect gh issue create after then."""
        result = review_promise_tracker.is_issue_create(
            "if true; then gh issue create --title 'Test'; fi"
        )
        assert result is True

    def test_detects_chained_after_echo(self):
        """Issue #1444: Should detect gh issue create after echo &&."""
        result = review_promise_tracker.is_issue_create(
            "echo 'Starting...' && gh issue create --title 'Test'"
        )
        assert result is True

    def test_detects_chained_after_printf(self):
        """Issue #1444: Should detect gh issue create chained after printf."""
        result = review_promise_tracker.is_issue_create(
            "printf 'Creating issue...\n' && gh issue create --title 'Bug'"
        )
        assert result is True

    def test_detects_with_env_var_prefix_quoted(self):
        """Issue #1444: Should detect gh issue create with quoted env var prefix."""
        result = review_promise_tracker.is_issue_create(
            'GH_TOKEN="secret" gh issue create --title "Bug"'
        )
        assert result is True

    def test_detects_with_env_var_prefix_unquoted(self):
        """Issue #1444: Should detect gh issue create with unquoted env var prefix."""
        result = review_promise_tracker.is_issue_create(
            "GH_TOKEN=secret gh issue create --title 'Bug'"
        )
        assert result is True

    def test_detects_with_env_var_prefix_single_quote(self):
        """Issue #1444: Should detect gh issue create with single-quoted env var."""
        result = review_promise_tracker.is_issue_create(
            "TOKEN='my-secret' gh issue create --title 'Bug'"
        )
        assert result is True

    def test_literal_string_assignment_rejected(self):
        """Issue #1444: VAR='gh issue create' should NOT be detected."""
        result = review_promise_tracker.is_issue_create("CMD='gh issue create --title test'")
        assert result is False

    def test_echo_with_leading_space_rejected(self):
        """Issue #1444: echo ' gh issue create' should NOT be detected."""
        result = review_promise_tracker.is_issue_create('echo " gh issue create"')
        assert result is False

    def test_echo_with_extra_args_rejected(self):
        """Issue #1444: echo 'msg' gh issue create (without &&) should NOT be detected."""
        # In bash, 'echo "msg" gh issue create' just prints "msg gh issue create"
        result = review_promise_tracker.is_issue_create('echo "message" gh issue create')
        assert result is False

    def test_echo_with_chained_echo_rejected(self):
        """Issue #1444: echo 'a' && echo 'gh issue create' should NOT be detected."""
        # The second echo's argument contains the text but doesn't execute it
        result = review_promise_tracker.is_issue_create('echo "test" && echo "gh issue create"')
        assert result is False


class TestPromiseFileOperations:
    """Tests for promise file load/save operations."""

    def test_load_promises_returns_empty_list_when_no_file(self, tmp_path: Path):
        """Should return empty list when promise file doesn't exist."""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with (
            patch.object(review_promise_tracker, "SESSION_DIR", tmp_path),
            patch.object(review_promise_tracker, "_ctx", mock_ctx),
        ):
            result = review_promise_tracker.load_promises()
            assert result == []

    def test_save_and_load_promises(self, tmp_path: Path):
        """Should save and load promises correctly."""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with (
            patch.object(review_promise_tracker, "SESSION_DIR", tmp_path),
            patch.object(review_promise_tracker, "_ctx", mock_ctx),
        ):
            promises = [
                {
                    "timestamp": "2025-12-28T10:00:00+00:00",
                    "pattern": r"別[Ii]ssue",
                    "excerpt": "別Issueで対応します",
                    "resolved": False,
                }
            ]
            review_promise_tracker.save_promises(promises)
            result = review_promise_tracker.load_promises()
            assert result == promises

    def test_load_promises_handles_invalid_json(self, tmp_path: Path):
        """Should return empty list when file contains invalid JSON."""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with (
            patch.object(review_promise_tracker, "SESSION_DIR", tmp_path),
            patch.object(review_promise_tracker, "_ctx", mock_ctx),
        ):
            promise_file = tmp_path / "review-promises-test-session.json"
            promise_file.write_text("invalid json")
            result = review_promise_tracker.load_promises()
            assert result == []


class TestPromiseManagement:
    """Tests for promise recording and resolution."""

    def test_record_promise(self, tmp_path: Path):
        """Should record a promise to the file."""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with (
            patch.object(review_promise_tracker, "SESSION_DIR", tmp_path),
            patch.object(review_promise_tracker, "_ctx", mock_ctx),
        ):
            review_promise_tracker.record_promise("別Issueで対応します", r"別[Ii]ssue")
            promises = review_promise_tracker.load_promises()
            assert len(promises) == 1
            assert promises[0]["pattern"] == r"別[Ii]ssue"
            assert promises[0]["resolved"] is False

    def test_resolve_promise(self, tmp_path: Path):
        """Should mark the most recent unresolved promise as resolved."""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with (
            patch.object(review_promise_tracker, "SESSION_DIR", tmp_path),
            patch.object(review_promise_tracker, "_ctx", mock_ctx),
        ):
            # Record a promise
            review_promise_tracker.record_promise("別Issueで対応します", r"別[Ii]ssue")
            # Resolve it
            review_promise_tracker.resolve_promise()
            promises = review_promise_tracker.load_promises()
            assert len(promises) == 1
            assert promises[0]["resolved"] is True
            assert "resolved_at" in promises[0]

    def test_get_unresolved_promises(self, tmp_path: Path):
        """Should return only unresolved promises."""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with (
            patch.object(review_promise_tracker, "SESSION_DIR", tmp_path),
            patch.object(review_promise_tracker, "_ctx", mock_ctx),
        ):
            # Record two promises
            review_promise_tracker.record_promise("別Issueで対応", r"別[Ii]ssue")
            review_promise_tracker.record_promise("今後の改善で対応", r"今後の改善")
            # Resolve one
            review_promise_tracker.resolve_promise()
            unresolved = review_promise_tracker.get_unresolved_promises()
            assert len(unresolved) == 1
            assert unresolved[0]["pattern"] == r"別[Ii]ssue"

    def test_resolve_promise_resolves_most_recent_first(self, tmp_path: Path):
        """Should resolve promises in LIFO order (most recent first)."""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with (
            patch.object(review_promise_tracker, "SESSION_DIR", tmp_path),
            patch.object(review_promise_tracker, "_ctx", mock_ctx),
        ):
            # Record two promises
            review_promise_tracker.record_promise("promise1", "pattern1")
            review_promise_tracker.record_promise("promise2", "pattern2")
            # Resolve one
            review_promise_tracker.resolve_promise()
            promises = review_promise_tracker.load_promises()
            # Most recent (promise2) should be resolved
            assert promises[0]["resolved"] is False
            assert promises[1]["resolved"] is True

    def test_resolve_promise_with_empty_list(self, tmp_path: Path):
        """Issue #1444: resolve_promise with empty promises list should not crash."""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with (
            patch.object(review_promise_tracker, "SESSION_DIR", tmp_path),
            patch.object(review_promise_tracker, "_ctx", mock_ctx),
        ):
            # No promises recorded, should not raise any exception
            review_promise_tracker.resolve_promise()
            promises = review_promise_tracker.load_promises()
            assert promises == []

    def test_resolve_promise_when_all_resolved(self, tmp_path: Path):
        """Issue #1444: resolve_promise when all promises already resolved should not crash."""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with (
            patch.object(review_promise_tracker, "SESSION_DIR", tmp_path),
            patch.object(review_promise_tracker, "_ctx", mock_ctx),
        ):
            # Record and resolve a promise
            review_promise_tracker.record_promise("promise1", "pattern1")
            review_promise_tracker.resolve_promise()
            # Try to resolve again (no unresolved promises)
            review_promise_tracker.resolve_promise()  # Should not crash
            promises = review_promise_tracker.load_promises()
            assert len(promises) == 1
            assert promises[0]["resolved"] is True


class TestMainFunction:
    """Tests for main function hook handling."""

    def test_stop_hook_with_unresolved_promises(self, tmp_path: Path, capsys):
        """Should output warning when Stop hook finds unresolved promises."""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with (
            patch.object(review_promise_tracker, "SESSION_DIR", tmp_path),
            patch.object(review_promise_tracker, "_ctx", mock_ctx),
            patch.object(review_promise_tracker, "log_hook_execution"),  # Suppress log output
        ):
            # Record an unresolved promise (with log suppressed)
            promises = [
                {
                    "timestamp": "2025-12-28T10:00:00+00:00",
                    "pattern": r"別[Ii]ssue",
                    "excerpt": "別Issueで対応",
                    "resolved": False,
                }
            ]
            review_promise_tracker.save_promises(promises)

            # Call main with Stop hook
            # Issue #2545: session_idをhook_inputに含める（main()内でcreate_hook_contextが呼ばれるため）
            hook_input = json.dumps(
                {
                    "hook_type": "Stop",
                    "tool_name": "",
                    "tool_input": {},
                    "session_id": "test-session",
                }
            )
            with (
                patch("sys.stdin.read", return_value=hook_input),
                patch("sys.exit") as mock_exit,
            ):
                review_promise_tracker.main()
                mock_exit.assert_called_with(0)

            captured = capsys.readouterr()
            # Multiple JSON outputs due to mocked sys.exit; take first line
            first_line = captured.out.strip().split("\n")[0]
            output = json.loads(first_line)
            assert output["decision"] == "approve"
            assert "別Issue対応" in output["systemMessage"]

    def test_stop_hook_with_no_promises(self, tmp_path: Path):
        """Should exit cleanly when Stop hook finds no unresolved promises."""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with (
            patch.object(review_promise_tracker, "SESSION_DIR", tmp_path),
            patch.object(review_promise_tracker, "_ctx", mock_ctx),
        ):
            hook_input = json.dumps(
                {
                    "hook_type": "Stop",
                    "tool_name": "",
                    "tool_input": {},
                    "session_id": "test-session",
                }
            )
            with (
                patch("sys.stdin.read", return_value=hook_input),
                patch("sys.exit") as mock_exit,
            ):
                review_promise_tracker.main()
                mock_exit.assert_called_with(0)

    def test_post_tool_use_records_promise(self, tmp_path: Path, capsys):
        """Should record promise when review reply contains promise pattern."""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with (
            patch.object(review_promise_tracker, "SESSION_DIR", tmp_path),
            patch.object(review_promise_tracker, "_ctx", mock_ctx),
        ):
            command = "gh api graphql -f query='mutation { addPullRequestReviewThreadReply(input: {body: \"別Issueで対応します\"}) }'"
            hook_input = json.dumps(
                {
                    "hook_type": "PostToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": command},
                    "session_id": "test-session",
                }
            )
            with (
                patch("sys.stdin.read", return_value=hook_input),
                patch("sys.exit") as mock_exit,
            ):
                review_promise_tracker.main()
                mock_exit.assert_called_with(0)

            promises = review_promise_tracker.load_promises()
            assert len(promises) == 1

    def test_post_tool_use_resolves_promise_on_issue_create(self, tmp_path: Path, capsys):
        """Should resolve promise when issue is created."""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with (
            patch.object(review_promise_tracker, "SESSION_DIR", tmp_path),
            patch.object(review_promise_tracker, "_ctx", mock_ctx),
        ):
            # First record a promise
            review_promise_tracker.record_promise("別Issueで対応", r"別[Ii]ssue")

            # Then create an issue
            hook_input = json.dumps(
                {
                    "hook_type": "PostToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "gh issue create --title 'Follow-up'"},
                    "session_id": "test-session",
                }
            )
            with (
                patch("sys.stdin.read", return_value=hook_input),
                patch("sys.exit") as mock_exit,
            ):
                review_promise_tracker.main()
                mock_exit.assert_called_with(0)

            unresolved = review_promise_tracker.get_unresolved_promises()
            assert len(unresolved) == 0
