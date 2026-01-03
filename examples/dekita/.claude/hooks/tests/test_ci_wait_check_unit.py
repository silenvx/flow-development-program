#!/usr/bin/env python3
"""Unit tests for ci-wait-check.py individual functions.

These tests complement the integration tests in test_ci_wait_check.py:
- Integration tests (test_ci_wait_check.py): Test the hook end-to-end via subprocess,
  verifying the hook behaves correctly when invoked as it is in production.
- Unit tests (this file): Test individual functions directly via module import,
  enabling fast and focused testing of specific logic like regex patterns.

The direct import approach used here executes module-level code at import time,
but ci-wait-check.py has no significant side effects outside of main().
"""

import re

from conftest import load_hook_module

# Load ci-wait-check.py module
_module = load_hook_module("ci-wait-check")

MANUAL_CHECK_PATTERNS = _module.MANUAL_CHECK_PATTERNS
detect_manual_polling = _module.detect_manual_polling
detect_manual_pr_check = _module.detect_manual_pr_check
extract_pr_number_from_command = _module.extract_pr_number_from_command
get_pr_number_from_checks = _module.get_pr_number_from_checks
is_command_with_comment_content = _module.is_command_with_comment_content
strip_quoted_content = _module.strip_quoted_content


class TestGetPrNumberFromChecks:
    """Tests for get_pr_number_from_checks function."""

    def test_pr_number_after_checks(self):
        """PR number immediately after 'checks'."""
        assert get_pr_number_from_checks("gh pr checks 123 --watch") == "123"

    def test_pr_number_with_flags_before(self):
        """PR number with flags before it."""
        assert get_pr_number_from_checks("gh pr checks --watch 456") == "456"

    def test_no_pr_number(self):
        """No PR number in command."""
        assert get_pr_number_from_checks("gh pr checks --watch") is None

    def test_unrelated_command(self):
        """Unrelated command."""
        assert get_pr_number_from_checks("ls -la") is None


class TestDetectManualPrCheck:
    """Tests for detect_manual_pr_check function."""

    # gh pr view with --json mergeStateStatus
    def test_merge_state_status_pr_first(self):
        """gh pr view 123 --json mergeStateStatus."""
        is_match, pr = detect_manual_pr_check("gh pr view 123 --json mergeStateStatus")
        assert is_match
        assert pr == "123"

    def test_merge_state_status_pr_last(self):
        """gh pr view --json mergeStateStatus 123."""
        is_match, pr = detect_manual_pr_check("gh pr view --json mergeStateStatus 123")
        assert is_match
        assert pr == "123"

    def test_merge_state_status_with_repo_flag(self):
        """gh pr view --repo owner/repo 123 --json mergeStateStatus."""
        is_match, pr = detect_manual_pr_check(
            "gh pr view --repo owner/repo 123 --json mergeStateStatus"
        )
        assert is_match
        assert pr == "123"

    # gh api /repos/.../pulls/...
    def test_api_pr_basic(self):
        """gh api /repos/owner/repo/pulls/456."""
        is_match, pr = detect_manual_pr_check("gh api /repos/owner/repo/pulls/456")
        assert is_match
        assert pr == "456"

    def test_api_pr_with_jq(self):
        """gh api /repos/owner/repo/pulls/456 --jq '.requested_reviewers'."""
        is_match, pr = detect_manual_pr_check(
            "gh api /repos/owner/repo/pulls/456 --jq '.requested_reviewers[].login'"
        )
        assert is_match
        assert pr == "456"

    def test_api_pr_trailing_slash_blocked(self):
        """gh api /repos/owner/repo/pulls/789/ should be BLOCKED.

        Trailing slash still accesses the same PR resource.
        """
        is_match, pr = detect_manual_pr_check("gh api /repos/owner/repo/pulls/789/")
        assert is_match
        assert pr == "789"

    def test_api_comments_allowed(self):
        """gh api /repos/owner/repo/pulls/456/comments should be ALLOWED.

        Issue #192: Review comment endpoints need to be accessible for posting
        replies to Copilot/Codex review comments.
        """
        is_match, pr = detect_manual_pr_check("gh api /repos/owner/repo/pulls/456/comments")
        assert not is_match
        assert pr is None

    def test_api_reviews_allowed(self):
        """gh api /repos/owner/repo/pulls/789/reviews should be ALLOWED.

        Issue #192: Review endpoints need to be accessible for review operations.
        """
        is_match, pr = detect_manual_pr_check("gh api /repos/owner/repo/pulls/789/reviews")
        assert not is_match
        assert pr is None

    def test_api_requested_reviewers_allowed(self):
        """gh api /repos/owner/repo/pulls/123/requested_reviewers should be ALLOWED.

        Issue #192: Requested reviewers endpoint needs to be accessible.
        """
        is_match, pr = detect_manual_pr_check(
            "gh api /repos/owner/repo/pulls/123/requested_reviewers"
        )
        assert not is_match
        assert pr is None

    def test_api_comments_post_allowed(self):
        """gh api /repos/owner/repo/pulls/456/comments -X POST should be ALLOWED.

        Issue #192: Posting review comments needs to be accessible.
        """
        is_match, pr = detect_manual_pr_check(
            "gh api /repos/owner/repo/pulls/456/comments -X POST -f body='test'"
        )
        assert not is_match
        assert pr is None

    # gh pr view with --json reviews
    def test_json_reviews_pr_first(self):
        """gh pr view 999 --json reviews."""
        is_match, pr = detect_manual_pr_check("gh pr view 999 --json reviews")
        assert is_match
        assert pr == "999"

    def test_json_reviews_pr_last(self):
        """gh pr view --json reviews 999."""
        is_match, pr = detect_manual_pr_check("gh pr view --json reviews 999")
        assert is_match
        assert pr == "999"

    # gh pr view with --json requested_reviewers
    def test_json_requested_reviewers_pr_first(self):
        """gh pr view 111 --json requested_reviewers."""
        is_match, pr = detect_manual_pr_check("gh pr view 111 --json requested_reviewers")
        assert is_match
        assert pr == "111"

    def test_json_requested_reviewers_pr_last(self):
        """gh pr view --json requested_reviewers 222."""
        is_match, pr = detect_manual_pr_check("gh pr view --json requested_reviewers 222")
        assert is_match
        assert pr == "222"

    # Non-matching commands
    def test_unrelated_command(self):
        """Unrelated command should not match."""
        is_match, pr = detect_manual_pr_check("ls -la")
        assert not is_match
        assert pr is None

    def test_gh_pr_merge(self):
        """gh pr merge should not match."""
        is_match, pr = detect_manual_pr_check("gh pr merge 123")
        assert not is_match
        assert pr is None

    def test_gh_pr_checks_watch(self):
        """gh pr checks --watch should not match (handled separately)."""
        is_match, pr = detect_manual_pr_check("gh pr checks 123 --watch")
        assert not is_match
        assert pr is None

    def test_empty_command(self):
        """Empty command should not match."""
        is_match, pr = detect_manual_pr_check("")
        assert not is_match
        assert pr is None


class TestManualCheckPatterns:
    """Tests for MANUAL_CHECK_PATTERNS constant."""

    def test_patterns_exist(self):
        """Ensure patterns list is not empty."""
        assert len(MANUAL_CHECK_PATTERNS) > 0

    def test_patterns_are_valid_regex(self):
        """Ensure all patterns are valid regex."""
        for pattern in MANUAL_CHECK_PATTERNS:
            # Should not raise exception
            re.compile(pattern)


class TestExtractPrNumberFromCommand:
    """Tests for extract_pr_number_from_command function."""

    def test_extracts_from_gh_pr_view(self):
        """Should extract PR number from gh pr view command."""
        assert extract_pr_number_from_command("gh pr view 123") == "123"

    def test_extracts_from_gh_pr_checks(self):
        """Should extract PR number from gh pr checks command."""
        assert extract_pr_number_from_command("gh pr checks 456") == "456"

    def test_extracts_from_gh_api_pulls(self):
        """Should extract PR number from gh api /pulls/ command."""
        assert extract_pr_number_from_command("gh api repos/owner/repo/pulls/789") == "789"

    def test_returns_none_for_no_pr_number(self):
        """Should return None when no PR number found."""
        assert extract_pr_number_from_command("gh pr list") is None
        assert extract_pr_number_from_command("git status") is None


class TestDetectManualPolling:
    """Tests for detect_manual_polling function."""

    # Basic detection cases
    def test_detects_sleep_and_gh(self):
        """Should detect sleep && gh pattern."""
        is_polling, _ = detect_manual_polling("sleep 30 && gh api repos/...")
        assert is_polling

    def test_detects_sleep_semicolon_gh(self):
        """Should detect sleep ; gh pattern."""
        is_polling, _ = detect_manual_polling("sleep 30; gh pr list")
        assert is_polling

    def test_detects_while_sleep_gh_single_line(self):
        """Should detect single-line while loop with sleep and gh."""
        cmd = "while true; do sleep 10; gh api ...; done"
        is_polling, _ = detect_manual_polling(cmd)
        assert is_polling

    def test_detects_while_sleep_gh_multiline(self):
        """Should detect multiline while loop with sleep and gh."""
        cmd = "while true\ndo\n  sleep 10\n  gh api ...\ndone"
        is_polling, _ = detect_manual_polling(cmd)
        assert is_polling

    # PR number extraction
    def test_extracts_pr_number_from_gh_pr_view(self):
        """Should extract PR number from gh pr view in polling pattern."""
        is_polling, pr_number = detect_manual_polling("sleep 30 && gh pr view 123")
        assert is_polling
        assert pr_number == "123"

    def test_extracts_pr_number_from_gh_api_pulls(self):
        """Should extract PR number from gh api pulls in polling pattern."""
        is_polling, pr_number = detect_manual_polling("sleep 30; gh api repos/owner/repo/pulls/456")
        assert is_polling
        assert pr_number == "456"

    def test_returns_none_when_no_pr_number(self):
        """Should return None for PR number when not extractable."""
        is_polling, pr_number = detect_manual_polling("sleep 30 && gh pr list")
        assert is_polling
        assert pr_number is None

    # Non-matching cases
    def test_ignores_simple_sleep(self):
        """Should not flag simple sleep without gh."""
        is_polling, _ = detect_manual_polling("sleep 10")
        assert not is_polling

    def test_ignores_simple_gh(self):
        """Should not flag simple gh without sleep."""
        is_polling, _ = detect_manual_polling("gh pr list")
        assert not is_polling

    def test_ignores_unrelated_commands(self):
        """Should not flag unrelated commands."""
        is_polling, _ = detect_manual_polling("npm run build")
        assert not is_polling
        is_polling, _ = detect_manual_polling("git status")
        assert not is_polling

    # Edge cases
    def test_ignores_sleep_without_gh_chained(self):
        """Should not flag sleep chained with non-gh commands."""
        is_polling, _ = detect_manual_polling("sleep 30 && echo 'done'")
        assert not is_polling
        is_polling, _ = detect_manual_polling("sleep 10; npm run build")
        assert not is_polling

    def test_ignores_gh_without_sleep_chained(self):
        """Should not flag gh chained with non-sleep commands."""
        is_polling, _ = detect_manual_polling("echo 'start' && gh pr list")
        assert not is_polling

    def test_ignores_while_as_text(self):
        """Should not flag 'while' when it appears as text, not a loop."""
        # 'while' appears as text but not as actual loop syntax
        is_polling, _ = detect_manual_polling("echo 'while loop is cool'")
        assert not is_polling
        # 'while' in comment-like text
        is_polling, _ = detect_manual_polling("# TODO: implement while loop later")
        assert not is_polling

    def test_ignores_sleeping_word(self):
        """Should not flag 'sleeping' as a partial match for 'sleep'."""
        # 'sleeping' is not 'sleep N'
        is_polling, _ = detect_manual_polling("echo 'sleeping beauty' && gh pr list")
        assert not is_polling

    def test_detects_sleep_with_various_durations(self):
        """Should detect sleep with various duration formats."""
        is_polling, _ = detect_manual_polling("sleep 1 && gh pr list")
        assert is_polling
        is_polling, _ = detect_manual_polling("sleep 300 && gh api repos/...")
        assert is_polling


class TestEdgeCasesUnit:
    """Edge case tests for individual functions."""

    # get_pr_number_from_checks edge cases
    def test_get_pr_number_empty_string(self):
        """Should return None for empty string."""
        assert get_pr_number_from_checks("") is None

    def test_get_pr_number_whitespace(self):
        """Should return None for whitespace-only string."""
        assert get_pr_number_from_checks("   \n\t") is None

    def test_get_pr_number_multiple_numbers(self):
        """Should extract first PR number when multiple numbers present."""
        result = get_pr_number_from_checks("gh pr checks 123 --watch 456")
        assert result == "123"

    def test_get_pr_number_large_number(self):
        """Should handle large PR numbers."""
        result = get_pr_number_from_checks("gh pr checks 999999 --watch")
        assert result == "999999"

    # detect_manual_pr_check edge cases
    def test_detect_manual_pr_check_whitespace(self):
        """Should not match whitespace-only string."""
        is_match, pr = detect_manual_pr_check("   \n\t")
        assert not is_match
        assert pr is None

    def test_detect_manual_pr_check_case_sensitivity(self):
        """Should be case-sensitive for gh commands."""
        # gh commands are lowercase
        is_match, pr = detect_manual_pr_check("GH PR VIEW 123 --json mergeStateStatus")
        assert not is_match
        assert pr is None

    def test_detect_manual_pr_check_multiple_repos(self):
        """Should handle commands with multiple repo-like patterns."""
        is_match, pr = detect_manual_pr_check("gh api /repos/owner/repo/pulls/123")
        assert is_match
        assert pr == "123"


class TestIsCommandWithCommentContent:
    """Tests for is_command_with_comment_content function (Issue #1008, #2052)."""

    # Original create command tests
    def test_gh_issue_create(self):
        """Should detect standalone gh issue create."""
        assert is_command_with_comment_content("gh issue create --title 'test'")

    def test_gh_pr_create(self):
        """Should detect standalone gh pr create."""
        assert is_command_with_comment_content("gh pr create --title 'test'")

    def test_gh_issue_create_with_blocked_pattern_in_body(self):
        """Should detect create even when body contains blocked patterns."""
        cmd = 'gh issue create --body "Detected gh pr checks --watch pattern"'
        assert is_command_with_comment_content(cmd)

    def test_gh_pr_create_with_blocked_pattern_in_body(self):
        """Should detect create even when body contains blocked patterns."""
        cmd = 'gh pr create --body "sleep 30 && gh api repos/..."'
        assert is_command_with_comment_content(cmd)

    # Issue #2052: New comment commands
    def test_gh_issue_close_with_comment(self):
        """Should detect gh issue close with --comment (Issue #2052)."""
        cmd = 'gh issue close 123 --comment "Fixed the issue"'
        assert is_command_with_comment_content(cmd)

    def test_gh_issue_close_with_blocked_pattern_in_comment(self):
        """Should detect gh issue close even when comment contains blocked patterns."""
        cmd = 'gh issue close 123 --comment "Detected gh pr checks --watch pattern"'
        assert is_command_with_comment_content(cmd)

    def test_gh_issue_comment(self):
        """Should detect gh issue comment."""
        cmd = 'gh issue comment 123 --body "Some comment"'
        assert is_command_with_comment_content(cmd)

    def test_gh_pr_comment(self):
        """Should detect gh pr comment."""
        cmd = 'gh pr comment 123 --body "Some comment"'
        assert is_command_with_comment_content(cmd)

    def test_gh_pr_comment_with_blocked_pattern(self):
        """Should detect gh pr comment even with blocked patterns in body."""
        cmd = 'gh pr comment 123 --body "Use ci-monitor.py instead of gh pr checks --watch"'
        assert is_command_with_comment_content(cmd)

    def test_gh_pr_review(self):
        """Should detect gh pr review."""
        cmd = 'gh pr review 123 --body "LGTM"'
        assert is_command_with_comment_content(cmd)

    def test_gh_pr_close_with_comment(self):
        """Should detect gh pr close with --comment."""
        cmd = 'gh pr close 123 --comment "Closing"'
        assert is_command_with_comment_content(cmd)

    # Issue #2062: git commit -m tests
    def test_git_commit_m(self):
        """Should detect git commit -m (Issue #2062)."""
        cmd = 'git commit -m "fix: some fix"'
        assert is_command_with_comment_content(cmd)

    def test_git_commit_m_with_blocked_pattern(self):
        """Should detect git commit -m even with blocked patterns in message."""
        cmd = 'git commit -m "fix: remove gh pr checks --watch usage"'
        assert is_command_with_comment_content(cmd)

    def test_git_commit_m_with_all_flag(self):
        """Should detect git commit -a -m."""
        cmd = 'git commit -a -m "fix: some fix"'
        assert is_command_with_comment_content(cmd)

    def test_git_commit_with_message_flag(self):
        """Should detect git commit --message."""
        cmd = 'git commit --message "fix: some fix"'
        # Both -m and --message are detected by the pattern
        assert is_command_with_comment_content(cmd)

    def test_git_commit_am(self):
        """Should detect git commit -am (combined -a and -m)."""
        cmd = 'git commit -am "quick fix"'
        assert is_command_with_comment_content(cmd)

    def test_not_git_commit_chained_with_blocked(self):
        """Should NOT approve git commit chained with blocked command."""
        cmd = 'git commit -m "fix" && gh pr checks --watch'
        assert not is_command_with_comment_content(cmd)

    def test_not_blocked_command_before_git_commit(self):
        """Should NOT approve when blocked gh command comes before git commit."""
        # This tests the bypass fix: gh pr checks --watch && git commit -m "msg"
        cmd = 'gh pr checks --watch && git commit -m "fix"'
        assert not is_command_with_comment_content(cmd)

    def test_not_blocked_command_before_git_commit_semicolon(self):
        """Should NOT approve when blocked gh command comes before git commit (semicolon)."""
        cmd = 'gh run watch; git commit -m "fix"'
        assert not is_command_with_comment_content(cmd)

    def test_git_commit_chained_with_non_gh_command(self):
        """Should approve git commit chained with non-gh command."""
        # Chain detection only blocks gh commands, not npm/npx/etc
        cmd = 'git commit -m "fix" && npm test'
        assert is_command_with_comment_content(cmd)

    def test_non_gh_command_before_git_commit(self):
        """Should approve non-gh command before git commit."""
        cmd = 'npm test && git commit -m "add tests"'
        assert is_command_with_comment_content(cmd)

    # Non-matching commands
    def test_not_gh_pr_view(self):
        """Should not match gh pr view."""
        assert not is_command_with_comment_content("gh pr view 123")

    def test_not_gh_pr_checks(self):
        """Should not match gh pr checks."""
        assert not is_command_with_comment_content("gh pr checks 123 --watch")

    def test_not_gh_issue_view(self):
        """Should not match gh issue view."""
        assert not is_command_with_comment_content("gh issue view 123")

    def test_not_unrelated_command(self):
        """Should not match unrelated commands."""
        assert not is_command_with_comment_content("git status")
        assert not is_command_with_comment_content("npm run build")

    # Chained command tests (Codex review feedback)
    def test_not_chained_with_blocked_command_and(self):
        """Should NOT early approve when chained with blocked command via &&."""
        cmd = "gh pr create --title 'test' && gh pr checks 123 --watch"
        assert not is_command_with_comment_content(cmd)

    def test_not_chained_with_blocked_command_semicolon(self):
        """Should NOT early approve when chained with blocked command via ;."""
        cmd = "gh pr create --title 'test'; gh pr checks 123 --watch"
        assert not is_command_with_comment_content(cmd)

    def test_not_chained_with_any_gh_command(self):
        """Should NOT early approve when chained with any gh command."""
        cmd = "gh issue create --title 'test' && gh pr list"
        assert not is_command_with_comment_content(cmd)

    def test_not_chained_with_or_operator(self):
        """Should NOT early approve when chained with || operator."""
        cmd = "gh pr create --title 'test' || gh pr checks --watch"
        assert not is_command_with_comment_content(cmd)

    def test_not_chained_with_pipe(self):
        """Should NOT early approve when chained with pipe."""
        cmd = "gh pr create --title 'test' | gh pr list"
        assert not is_command_with_comment_content(cmd)

    def test_not_gh_issue_close_chained(self):
        """Should NOT approve chained gh issue close."""
        cmd = "gh issue close 123 --comment 'Fixed' && gh pr checks --watch"
        assert not is_command_with_comment_content(cmd)


class TestStripQuotedContent:
    """Tests for strip_quoted_content function."""

    def test_removes_double_quoted_content(self):
        """Should remove content inside double quotes."""
        result = strip_quoted_content('cmd --body "some text"')
        assert result == 'cmd --body ""'

    def test_removes_single_quoted_content(self):
        """Should remove content inside single quotes."""
        result = strip_quoted_content("cmd --body 'some text'")
        assert result == "cmd --body ''"

    def test_removes_multiple_quoted_strings(self):
        """Should handle multiple quoted strings."""
        result = strip_quoted_content('cmd --title "title" --body "body"')
        assert result == 'cmd --title "" --body ""'

    def test_preserves_unquoted_content(self):
        """Should preserve unquoted content."""
        result = strip_quoted_content("cmd --flag value")
        assert result == "cmd --flag value"

    def test_handles_empty_quotes(self):
        """Should handle empty quotes."""
        result = strip_quoted_content('cmd --body ""')
        assert result == 'cmd --body ""'

    def test_handles_escaped_double_quote(self):
        """Should handle escaped double quotes inside double-quoted string."""
        result = strip_quoted_content(r'cmd --body "He said \"hello\""')
        assert result == 'cmd --body ""'

    def test_handles_escaped_single_quote(self):
        """Should handle escaped single quotes inside single-quoted string."""
        result = strip_quoted_content(r"cmd --body 'It\'s working'")
        assert result == "cmd --body ''"

    def test_handles_unclosed_double_quote(self):
        """Should handle unclosed double quote by treating rest as quoted."""
        result = strip_quoted_content('cmd --body "unclosed text')
        assert result == 'cmd --body "'

    def test_handles_unclosed_single_quote(self):
        """Should handle unclosed single quote by treating rest as quoted."""
        result = strip_quoted_content("cmd --body 'unclosed text")
        assert result == "cmd --body '"

    def test_handles_mixed_quotes_in_double(self):
        """Should handle single quote inside double-quoted string."""
        result = strip_quoted_content('cmd --body "it\'s a test"')
        assert result == 'cmd --body ""'

    def test_handles_mixed_quotes_in_single(self):
        """Should handle double quote inside single-quoted string."""
        result = strip_quoted_content("cmd --body 'say \"hello\"'")
        assert result == "cmd --body ''"

    def test_handles_backslash_at_end(self):
        """Should handle backslash at end of quoted string."""
        result = strip_quoted_content(r'cmd --body "path\\"')
        assert result == 'cmd --body ""'

    def test_escaped_quote_outside_string_not_treated_as_delimiter(self):
        """Escaped quotes outside strings should not start a quoted section."""
        # This should preserve the && chain since \" is not a real quote
        result = strip_quoted_content(r"cmd --title \"foo\" && gh pr checks")
        assert result == r"cmd --title \"foo\" && gh pr checks"

    def test_escaped_single_quote_outside_string(self):
        """Escaped single quotes outside strings should not start a quoted section."""
        result = strip_quoted_content(r"cmd --title \'bar\' && gh api")
        assert result == r"cmd --title \'bar\' && gh api"

    def test_mixed_escaped_and_real_quotes(self):
        """Mix of escaped quotes outside and real quotes should work."""
        result = strip_quoted_content(r'cmd \"escaped\" "real content" more')
        assert result == r'cmd \"escaped\" "" more'
