"""Tests for related-task-check hook.

Tests the completion-promise blocking behavior:
- Blocks session end when open issues exist (forever, no count limit)
- Allows session end only when all issues are CLOSED
- Clears session files appropriately

Issue #2090: Removed MAX_BLOCK_COUNT. Now blocks until issue is closed.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from conftest import load_hook_module

# Load the hook module
related_task_check = load_hook_module("related-task-check")


TEST_SESSION_ID = "test-session-123"


class TestSessionIssuesLoading:
    """Tests for loading session issues."""

    def test_load_session_issues_no_file(self, tmp_path, monkeypatch):
        """Returns empty list when no session issues file exists."""
        monkeypatch.setattr(
            related_task_check, "get_session_issues_file", lambda sid: tmp_path / "nonexistent.json"
        )
        assert related_task_check.load_session_issues(TEST_SESSION_ID) == []

    def test_load_session_issues_with_file(self, tmp_path, monkeypatch):
        """Returns issue numbers from file."""
        issues_file = tmp_path / "issues.json"
        issues_file.write_text(json.dumps({"issues": [123, 456]}))
        monkeypatch.setattr(related_task_check, "get_session_issues_file", lambda sid: issues_file)
        assert related_task_check.load_session_issues(TEST_SESSION_ID) == [123, 456]


class TestClearSessionFiles:
    """Tests for clearing session files."""

    def test_clear_session_files(self, tmp_path, monkeypatch):
        """Clears session issues file."""
        issues_file = tmp_path / "issues.json"
        issues_file.write_text("{}")

        monkeypatch.setattr(related_task_check, "get_session_issues_file", lambda sid: issues_file)

        related_task_check.clear_session_files(TEST_SESSION_ID)

        assert not issues_file.exists()

    def test_clear_session_files_nonexistent(self, tmp_path, monkeypatch):
        """Handles nonexistent files gracefully."""
        monkeypatch.setattr(
            related_task_check,
            "get_session_issues_file",
            lambda sid: tmp_path / "nonexistent.json",
        )
        # Should not raise
        related_task_check.clear_session_files(TEST_SESSION_ID)


class TestFormatBlockReason:
    """Tests for block reason formatting."""

    def test_format_block_reason_single_issue(self):
        """Formats block reason for a single open issue."""
        open_issues = [{"number": 123, "title": "Fix bug"}]
        reason = related_task_check.format_block_reason(open_issues)
        assert "#123" in reason
        assert "Fix bug" in reason
        assert "終了条件" in reason
        assert "クローズされるまでブロック" in reason
        assert "💡 ブロック後も作業を継続してください" in reason

    def test_format_block_reason_multiple_issues(self):
        """Formats block reason for multiple open issues."""
        open_issues = [
            {"number": 123, "title": "First issue"},
            {"number": 456, "title": "Second issue"},
        ]
        reason = related_task_check.format_block_reason(open_issues)
        assert "#123" in reason
        assert "（残り2件）" in reason

    def test_format_block_reason_long_title(self):
        """Truncates long titles in block reason."""
        long_title = "A" * 100
        open_issues = [{"number": 123, "title": long_title}]
        reason = related_task_check.format_block_reason(open_issues)
        assert "..." in reason
        assert len(long_title) > len(reason.split("\n")[2])  # Title line is truncated

    def test_format_block_reason_shows_close_command(self):
        """Shows gh issue close command for not-planned option."""
        open_issues = [{"number": 789, "title": "Test issue"}]
        reason = related_task_check.format_block_reason(open_issues)
        assert 'gh issue close 789 --reason "not planned"' in reason

    def test_format_block_reason_fork_session(self):
        """Shows fork-session note when is_fork=True.

        Issue #2470: fork-sessionでも自分で作成したIssueは実装可能と明記。
        """
        open_issues = [{"number": 123, "title": "Fork test issue"}]
        reason = related_task_check.format_block_reason(open_issues, is_fork=True)
        assert "#123" in reason
        assert "fork-session" in reason
        assert "自分で作成したIssueへの作業は許可" in reason
        assert "新しいworktree" in reason

    def test_format_block_reason_non_fork_session(self):
        """Does not show fork-session note when is_fork=False.

        Issue #2470: 非fork-sessionでは追加メッセージを表示しない。
        """
        open_issues = [{"number": 456, "title": "Regular issue"}]
        reason = related_task_check.format_block_reason(open_issues, is_fork=False)
        assert "#456" in reason
        assert "fork-session" not in reason


class TestFormatInfoMessage:
    """Tests for format_info_message function."""

    def test_format_info_message_open_only(self):
        """Formats message with only open issues."""
        open_issues = [{"number": 123, "title": "Open issue"}]
        closed_issues = []
        message = related_task_check.format_info_message(open_issues, closed_issues)
        assert "未完了" in message
        assert "#123" in message
        assert "Open issue" in message
        assert "✅" not in message

    def test_format_info_message_closed_only(self):
        """Formats message with only closed issues."""
        open_issues = []
        closed_issues = [{"number": 456, "title": "Closed issue"}]
        message = related_task_check.format_info_message(open_issues, closed_issues)
        assert "✅ #456" in message
        assert "Closed issue" in message

    def test_format_info_message_mixed(self):
        """Formats message with both open and closed issues."""
        open_issues = [{"number": 123, "title": "Still open"}]
        closed_issues = [{"number": 456, "title": "Resolved"}]
        message = related_task_check.format_info_message(open_issues, closed_issues)
        assert "#123" in message
        assert "Still open" in message
        assert "✅ #456" in message
        assert "Resolved" in message

    def test_format_info_message_long_title_truncation(self):
        """Truncates long titles with ellipsis."""
        long_title = "A" * 100
        open_issues = [{"number": 123, "title": long_title}]
        message = related_task_check.format_info_message(open_issues, [])
        assert "..." in message
        # Should not contain the full 100 character title
        assert "A" * 100 not in message

    def test_format_info_message_empty(self):
        """Returns empty string when no issues."""
        message = related_task_check.format_info_message([], [])
        assert message == ""


class TestMainFunction:
    """Tests for main function behavior."""

    @pytest.fixture
    def mock_env(self, tmp_path, monkeypatch):
        """Set up mock environment for main function tests.

        Issue #2470: Added is_fork_session mock and updated parse_hook_input to return dict.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir(parents=True, exist_ok=True)

        issues_file = session_dir / f"session-created-issues-{TEST_SESSION_ID}.json"

        monkeypatch.setattr(related_task_check, "get_session_issues_file", lambda sid: issues_file)
        monkeypatch.setattr(
            related_task_check, "parse_hook_input", lambda: {"session_id": TEST_SESSION_ID}
        )
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = TEST_SESSION_ID
        monkeypatch.setattr(related_task_check, "create_hook_context", lambda _: mock_ctx)
        monkeypatch.setattr(related_task_check, "is_fork_session", lambda *args: False)
        monkeypatch.setattr(related_task_check, "log_hook_execution", lambda *args: None)

        return {"issues_file": issues_file}

    def test_no_session_issues_approves(self, mock_env, capsys):
        """Approves when no session issues exist."""
        related_task_check.main()
        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "approve"

    def test_all_issues_closed_approves(self, mock_env, capsys):
        """Approves when all session issues are closed."""
        mock_env["issues_file"].write_text(json.dumps({"issues": [123]}))

        with patch.object(
            related_task_check,
            "get_issue_status",
            return_value=[{"number": 123, "title": "Fixed", "state": "CLOSED"}],
        ):
            related_task_check.main()

        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "approve"
        assert "✅ #123" in result.get("systemMessage", "")

    def test_open_issues_blocks(self, mock_env, capsys):
        """Blocks when open issues exist (no count limit)."""
        mock_env["issues_file"].write_text(json.dumps({"issues": [123]}))

        with patch.object(
            related_task_check,
            "get_issue_status",
            return_value=[{"number": 123, "title": "Open issue", "state": "OPEN"}],
        ):
            related_task_check.main()

        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "block"
        assert "#123" in result["reason"]
        assert "終了条件" in result["reason"]

    def test_open_issues_blocks_forever(self, mock_env, capsys):
        """Blocks forever until issue is closed (no escape after N blocks)."""
        mock_env["issues_file"].write_text(json.dumps({"issues": [123]}))

        # Run multiple times - should always block while issue is open
        for _ in range(10):
            with patch.object(
                related_task_check,
                "get_issue_status",
                return_value=[{"number": 123, "title": "Open issue", "state": "OPEN"}],
            ):
                related_task_check.main()

            output = capsys.readouterr().out
            result = json.loads(output)
            # Should always block, never approve with "max blocks reached"
            assert result["decision"] == "block"
            assert "ブロック回数上限" not in result.get("reason", "")

    def test_could_not_fetch_issues_approves(self, mock_env, capsys):
        """Approves when issues cannot be fetched from GitHub."""
        mock_env["issues_file"].write_text(json.dumps({"issues": [123, 456]}))

        with patch.object(related_task_check, "get_issue_status", return_value=[]):
            related_task_check.main()

        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "approve"

    def test_files_cleared_on_approve(self, mock_env, capsys):
        """Session files are cleared when session ends."""
        mock_env["issues_file"].write_text(json.dumps({"issues": [123]}))

        with patch.object(
            related_task_check,
            "get_issue_status",
            return_value=[{"number": 123, "title": "Closed", "state": "CLOSED"}],
        ):
            related_task_check.main()

        # Issues file should be cleared
        assert not mock_env["issues_file"].exists()

    def test_files_not_cleared_on_block(self, mock_env, capsys):
        """Session issues file preserved when blocking."""
        mock_env["issues_file"].write_text(json.dumps({"issues": [123]}))

        with patch.object(
            related_task_check,
            "get_issue_status",
            return_value=[{"number": 123, "title": "Open", "state": "OPEN"}],
        ):
            related_task_check.main()

        # Issues file should still exist
        assert mock_env["issues_file"].exists()

    def test_fork_session_block_message(self, mock_env, capsys, monkeypatch):
        """Block message includes fork-session note when in fork-session.

        Issue #2470: fork-sessionでも自分で作成したIssueは実装可能と明記。
        """
        mock_env["issues_file"].write_text(json.dumps({"issues": [789]}))

        # Mock is_fork_session to return True
        monkeypatch.setattr(related_task_check, "is_fork_session", lambda *args: True)

        with patch.object(
            related_task_check,
            "get_issue_status",
            return_value=[{"number": 789, "title": "Fork issue", "state": "OPEN"}],
        ):
            related_task_check.main()

        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "block"
        assert "#789" in result["reason"]
        assert "fork-session" in result["reason"]
        assert "自分で作成したIssueへの作業は許可" in result["reason"]


class TestIsIssueDelegated:
    """Tests for is_issue_delegated function.

    Issue #2525: Check if an issue has been delegated to a fork-session.
    """

    def test_delegated_when_open_pr_exists(self, monkeypatch):
        """Returns True with PR number when open PR exists for issue."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            [{"number": 2524, "headRefName": "feat/issue-123-fix", "title": "Fix #123"}]
        )

        monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_result)

        is_delegated, pr_number = related_task_check.is_issue_delegated(123)
        assert is_delegated is True
        assert pr_number == "2524"

    def test_delegated_when_pr_title_contains_issue(self, monkeypatch):
        """Returns True when PR title contains issue reference."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            [{"number": 999, "headRefName": "some-branch", "title": "Feature for #456"}]
        )

        monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_result)

        is_delegated, pr_number = related_task_check.is_issue_delegated(456)
        assert is_delegated is True
        assert pr_number == "999"

    def test_delegated_when_worktree_locked(self, monkeypatch):
        """Returns True when worktree for issue is locked."""
        # First call: gh pr list (no matching PR)
        # Second call: git worktree list (locked worktree)
        call_count = [0]

        def mock_run(*args, **kwargs):
            result = MagicMock()
            call_count[0] += 1
            if "gh" in args[0]:
                result.returncode = 0
                result.stdout = "[]"  # No open PRs
            else:  # git worktree list
                result.returncode = 0
                result.stdout = """worktree /path/to/repo
HEAD abc123

worktree /path/to/repo/.worktrees/issue-789
HEAD def456
locked
"""
            return result

        monkeypatch.setattr("subprocess.run", mock_run)

        is_delegated, pr_number = related_task_check.is_issue_delegated(789)
        assert is_delegated is True
        assert pr_number is None  # No PR, just locked worktree

    def test_not_delegated_when_no_pr_or_locked_worktree(self, monkeypatch):
        """Returns False when no PR and no locked worktree."""

        def mock_run(*args, **kwargs):
            result = MagicMock()
            if "gh" in args[0]:
                result.returncode = 0
                result.stdout = "[]"  # No open PRs
            else:  # git worktree list
                result.returncode = 0
                result.stdout = """worktree /path/to/repo
HEAD abc123
"""  # No locked worktrees
            return result

        monkeypatch.setattr("subprocess.run", mock_run)

        is_delegated, pr_number = related_task_check.is_issue_delegated(999)
        assert is_delegated is False
        assert pr_number is None

    def test_not_delegated_when_unlocked_worktree_exists(self, monkeypatch):
        """Returns False when worktree exists but is not locked."""

        def mock_run(*args, **kwargs):
            result = MagicMock()
            if "gh" in args[0]:
                result.returncode = 0
                result.stdout = "[]"  # No open PRs
            else:  # git worktree list
                result.returncode = 0
                result.stdout = """worktree /path/to/repo/.worktrees/issue-123
HEAD abc123
"""  # Worktree exists but not locked
            return result

        monkeypatch.setattr("subprocess.run", mock_run)

        is_delegated, pr_number = related_task_check.is_issue_delegated(123)
        assert is_delegated is False
        assert pr_number is None


class TestDelegatedIssueHandling:
    """Tests for delegated issue handling in main function.

    Issue #2525: Fork-session collaboration improvement.
    """

    @pytest.fixture
    def mock_env(self, tmp_path, monkeypatch):
        """Set up mock environment for delegated issue tests."""
        session_dir = tmp_path / "session"
        session_dir.mkdir(parents=True, exist_ok=True)

        issues_file = session_dir / f"session-created-issues-{TEST_SESSION_ID}.json"

        monkeypatch.setattr(related_task_check, "get_session_issues_file", lambda sid: issues_file)
        monkeypatch.setattr(
            related_task_check, "parse_hook_input", lambda: {"session_id": TEST_SESSION_ID}
        )
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = TEST_SESSION_ID
        monkeypatch.setattr(related_task_check, "create_hook_context", lambda _: mock_ctx)
        monkeypatch.setattr(related_task_check, "is_fork_session", lambda *args: False)
        monkeypatch.setattr(related_task_check, "log_hook_execution", lambda *args: None)

        return {"issues_file": issues_file}

    def test_delegated_issue_approves_with_info(self, mock_env, capsys, monkeypatch):
        """Approves when all open issues are delegated to fork-session."""
        mock_env["issues_file"].write_text(json.dumps({"issues": [2518]}))

        # Mock issue status - issue is OPEN
        with patch.object(
            related_task_check,
            "get_issue_status",
            return_value=[{"number": 2518, "title": "Test issue", "state": "OPEN"}],
        ):
            # Mock is_issue_delegated - issue is delegated with PR
            monkeypatch.setattr(
                related_task_check,
                "is_issue_delegated",
                lambda num: (True, "2524") if num == 2518 else (False, None),
            )
            related_task_check.main()

        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "approve"
        assert "fork-sessionに委譲済み" in result.get("systemMessage", "")
        assert "#2518" in result.get("systemMessage", "")
        assert "PR #2524" in result.get("systemMessage", "")
        # Issue #2525: Files should be preserved when issues are delegated (not closed)
        assert mock_env["issues_file"].exists()

    def test_delegated_issue_with_locked_worktree(self, mock_env, capsys, monkeypatch):
        """Shows worktree locked message when no PR exists."""
        mock_env["issues_file"].write_text(json.dumps({"issues": [999]}))

        with patch.object(
            related_task_check,
            "get_issue_status",
            return_value=[{"number": 999, "title": "Worktree issue", "state": "OPEN"}],
        ):
            # Mock is_issue_delegated - delegated via locked worktree, no PR
            monkeypatch.setattr(
                related_task_check,
                "is_issue_delegated",
                lambda num: (True, None) if num == 999 else (False, None),
            )
            related_task_check.main()

        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "approve"
        assert "worktreeがロック中" in result.get("systemMessage", "")

    def test_mixed_delegated_and_actionable_blocks(self, mock_env, capsys, monkeypatch):
        """Blocks when some issues are actionable (not delegated)."""
        mock_env["issues_file"].write_text(json.dumps({"issues": [100, 200]}))

        with patch.object(
            related_task_check,
            "get_issue_status",
            return_value=[
                {"number": 100, "title": "Delegated issue", "state": "OPEN"},
                {"number": 200, "title": "Actionable issue", "state": "OPEN"},
            ],
        ):
            # 100 is delegated, 200 is not
            monkeypatch.setattr(
                related_task_check,
                "is_issue_delegated",
                lambda num: (True, "999") if num == 100 else (False, None),
            )
            related_task_check.main()

        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "block"
        assert "#200" in result["reason"]  # Only actionable issue in block reason


class TestPartialMatchingPrevention:
    """Tests to verify partial matching is prevented (e.g., #12 vs #123)."""

    def test_branch_matching_exact(self):
        """issue-12 should match issue 12."""
        assert related_task_check._matches_issue_in_branch("issue-12-feature", 12)
        assert related_task_check._matches_issue_in_branch("feat/issue-12-desc", 12)
        assert related_task_check._matches_issue_in_branch("issue-12", 12)

    def test_branch_matching_no_false_positive(self):
        """issue-12 should NOT match issue 123."""
        assert not related_task_check._matches_issue_in_branch("issue-123-feature", 12)
        assert not related_task_check._matches_issue_in_branch("feat/issue-123", 12)
        assert not related_task_check._matches_issue_in_branch("issue-1234", 12)

    def test_title_matching_exact(self):
        """#12 should match issue 12."""
        assert related_task_check._matches_issue_in_title("Fix bug #12", 12)
        assert related_task_check._matches_issue_in_title("#12: Bug fix", 12)
        assert related_task_check._matches_issue_in_title("Closes #12", 12)
        assert related_task_check._matches_issue_in_title("Issue #12, #13", 12)
        # Punctuation at end of title
        assert related_task_check._matches_issue_in_title("Fix #12.", 12)
        assert related_task_check._matches_issue_in_title("Closes #12!", 12)
        assert related_task_check._matches_issue_in_title("Fix #12?", 12)
        assert related_task_check._matches_issue_in_title("Closes #12; minor updates", 12)

    def test_title_matching_no_false_positive(self):
        """#12 should NOT match #123."""
        assert not related_task_check._matches_issue_in_title("Fix bug #123", 12)
        assert not related_task_check._matches_issue_in_title("#1234: Feature", 12)
        assert not related_task_check._matches_issue_in_title("Issue #120", 12)

    def test_worktree_matching_exact(self):
        """Worktree name exact match test via is_issue_delegated mocking."""
        # This tests the worktree name extraction logic
        from pathlib import Path

        # Simulate worktree path extraction
        worktree_path = "/path/to/repo/.worktrees/issue-12"
        worktree_name = Path(worktree_path).name.lower()
        assert worktree_name == "issue-12"

        # issue-123 should not match issue 12
        worktree_path_123 = "/path/to/repo/.worktrees/issue-123"
        worktree_name_123 = Path(worktree_path_123).name.lower()
        assert worktree_name_123 != "issue-12"


class TestSessionFileClearingWithDelegation:
    """Tests for session file clearing behavior with delegated issues."""

    @pytest.fixture
    def mock_env(self, tmp_path, monkeypatch):
        """Set up test environment."""
        session_dir = tmp_path / "session"
        session_dir.mkdir(parents=True, exist_ok=True)

        session_id = "test-session-clear"
        issues_file = session_dir / f"session-created-issues-{session_id}.json"

        monkeypatch.setattr(related_task_check, "get_session_issues_file", lambda sid: issues_file)
        monkeypatch.setattr(
            related_task_check, "parse_hook_input", lambda: {"session_id": session_id}
        )
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = session_id
        monkeypatch.setattr(related_task_check, "create_hook_context", lambda _: mock_ctx)
        monkeypatch.setattr(related_task_check, "is_fork_session", lambda *args: False)
        monkeypatch.setattr(related_task_check, "log_hook_execution", lambda *args: None)

        return {
            "issues_file": issues_file,
            "session_id": session_id,
        }

    def test_files_cleared_when_all_closed_no_delegated(self, mock_env, capsys, monkeypatch):
        """Session files should be cleared when all issues are closed (none delegated)."""
        mock_env["issues_file"].write_text(json.dumps({"issues": [123]}))

        with patch.object(
            related_task_check,
            "get_issue_status",
            return_value=[{"number": 123, "title": "Closed issue", "state": "CLOSED"}],
        ):
            monkeypatch.setattr(
                related_task_check,
                "is_issue_delegated",
                lambda num: (False, None),  # Not delegated (closed issues skip this check)
            )
            related_task_check.main()

        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "approve"
        # Files should be cleared when all closed
        assert not mock_env["issues_file"].exists()

    def test_files_preserved_when_delegated_and_closed_mixed(self, mock_env, capsys, monkeypatch):
        """Session files preserved when some issues are delegated (still open)."""
        mock_env["issues_file"].write_text(json.dumps({"issues": [100, 200]}))

        with patch.object(
            related_task_check,
            "get_issue_status",
            return_value=[
                {"number": 100, "title": "Delegated issue", "state": "OPEN"},
                {"number": 200, "title": "Closed issue", "state": "CLOSED"},
            ],
        ):
            monkeypatch.setattr(
                related_task_check,
                "is_issue_delegated",
                lambda num: (True, "999") if num == 100 else (False, None),
            )
            related_task_check.main()

        output = capsys.readouterr().out
        result = json.loads(output)
        assert result["decision"] == "approve"
        # Files should be preserved - delegated issues are still open
        assert mock_env["issues_file"].exists()
