#!/usr/bin/env python3
"""Unit tests for resolve-thread-guard.py.

Tests the thread ID extraction logic and thread response checking.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from conftest import load_hook_module

# Load resolve-thread-guard.py module
_module = load_hook_module("resolve-thread-guard")

is_japanese_char = _module.is_japanese_char
extract_thread_id = _module.extract_thread_id
check_thread_has_response = _module.check_thread_has_response
_has_claude_code_signature = _module._has_claude_code_signature
_has_fix_claim_without_verification = _module._has_fix_claim_without_verification
_has_verification = _module._has_verification
_has_out_of_scope_without_issue = _module._has_out_of_scope_without_issue


class TestExtractThreadId:
    """Tests for extract_thread_id function."""

    def test_gh_cli_dash_F_format(self):
        """gh api graphql -F threadId=PRRT_xxx."""
        cmd = 'gh api graphql -F threadId=PRRT_abc123 -f query="mutation..."'
        assert extract_thread_id(cmd) == "PRRT_abc123"

    def test_gh_cli_dash_f_format(self):
        """gh api graphql -f threadId=PRRT_xxx."""
        cmd = 'gh api graphql -f threadId=PRRT_def456 -f query="mutation..."'
        assert extract_thread_id(cmd) == "PRRT_def456"

    def test_gh_cli_quoted_format(self):
        """gh api graphql -F threadId="PRRT_xxx"."""
        cmd = 'gh api graphql -F threadId="PRRT_quoted123" -f query="mutation..."'
        assert extract_thread_id(cmd) == "PRRT_quoted123"

    def test_inline_threadId_double_quote(self):
        """threadId: "PRRT_xxx" in query string."""
        cmd = "gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: \"PRRT_inline123\"}) }'"
        assert extract_thread_id(cmd) == "PRRT_inline123"

    def test_inline_threadId_single_quote(self):
        """threadId: 'PRRT_xxx' in query string."""
        cmd = "gh api graphql -f query=\"mutation { resolveReviewThread(input: {threadId: 'PRRT_single456'}) }\""
        assert extract_thread_id(cmd) == "PRRT_single456"

    def test_escaped_quotes(self):
        """threadId: \\"PRRT_xxx\\" format."""
        cmd = r'gh api graphql -f query="mutation { resolveReviewThread(input: {threadId: \"PRRT_escaped789\"}) }"'
        assert extract_thread_id(cmd) == "PRRT_escaped789"

    def test_json_style(self):
        """JSON style: "threadId": "PRRT_xxx"."""
        cmd = '{"threadId": "PRRT_json123"}'
        assert extract_thread_id(cmd) == "PRRT_json123"

    def test_no_thread_id(self):
        """Command without threadId returns None."""
        cmd = "gh api graphql -f query='query { viewer { login } }'"
        assert extract_thread_id(cmd) is None

    def test_unrelated_command(self):
        """Unrelated command returns None."""
        cmd = "git status"
        assert extract_thread_id(cmd) is None


class TestHasClaudeCodeSignature:
    """Tests for _has_claude_code_signature function (Issue #753)."""

    def test_signature_on_own_line(self):
        """Signature on its own line should be detected."""
        body = "Fixed the issue.\n\n-- Claude Code"
        assert _has_claude_code_signature(body)

    def test_signature_with_leading_whitespace(self):
        """Signature with leading whitespace should be detected."""
        body = "Fixed.\n\n  -- Claude Code"
        assert _has_claude_code_signature(body)

    def test_signature_in_code_block_not_detected(self):
        """Signature inside code block should NOT be detected."""
        body = "```\nprint('-- Claude Code')\n```"
        # The signature is inside a code block, not a standalone line
        assert not _has_claude_code_signature(body)

    def test_signature_as_part_of_text_not_detected(self):
        """Signature as part of longer text should NOT be detected."""
        body = "The user mentioned -- Claude Code in their message"
        assert not _has_claude_code_signature(body)

    def test_no_signature(self):
        """No signature should return False."""
        body = "This is a regular comment without signature."
        assert not _has_claude_code_signature(body)

    def test_empty_body(self):
        """Empty body should return False."""
        assert not _has_claude_code_signature("")

    def test_signature_with_trailing_text(self):
        """Signature with trailing text should be detected."""
        body = "Fixed.\n\n-- Claude Code\nSome extra text"
        assert _has_claude_code_signature(body)


class TestCheckThreadHasResponse:
    """Tests for check_thread_has_response function (Issue #751)."""

    @patch.object(_module, "get_repo_owner_and_name")
    def test_fails_open_when_repo_info_unavailable(self, mock_get_repo: MagicMock):
        """Fail open when repository info cannot be retrieved."""
        mock_get_repo.return_value = None

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert not result["thread_found"]

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_fails_open_when_graphql_api_fails(
        self, mock_get_repo: MagicMock, mock_subprocess: MagicMock
    ):
        """Fail open when GraphQL API call fails."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(returncode=1, stdout="", stderr="error")

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert not result["thread_found"]

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_fails_open_when_thread_not_found(
        self, mock_get_repo: MagicMock, mock_subprocess: MagicMock
    ):
        """Fail open when thread is not found (node is null)."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"data": {"node": None}}),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert not result["thread_found"]

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_fails_open_when_thread_has_no_comments(
        self, mock_get_repo: MagicMock, mock_subprocess: MagicMock
    ):
        """Fail open when thread exists but has no comments (edge case)."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "comments": {"nodes": []},
                        }
                    }
                }
            ),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert result["thread_found"]

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_detects_claude_code_response(
        self, mock_get_repo: MagicMock, mock_subprocess: MagicMock
    ):
        """Detect when thread has a Claude Code response comment."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "comments": {
                                "nodes": [
                                    {
                                        "body": "Please fix this issue",
                                        "author": {"login": "copilot"},
                                    },
                                    {
                                        "body": "Fixed in commit abc123\n\n-- Claude Code",
                                        "author": {"login": "user"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert result["thread_found"]
        assert result["author"] == "copilot"
        assert "Please fix" in result["original_comment"]

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_detects_missing_claude_code_response(
        self, mock_get_repo: MagicMock, mock_subprocess: MagicMock
    ):
        """Detect when thread has no Claude Code response comment."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "comments": {
                                "nodes": [
                                    {
                                        "body": "Please add error handling",
                                        "author": {"login": "reviewer"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        assert not result["has_response"]
        assert result["thread_found"]
        assert result["author"] == "reviewer"
        assert "error handling" in result["original_comment"]

    @patch("subprocess.run")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_fails_open_on_timeout(self, mock_get_repo: MagicMock, mock_run: MagicMock):
        """Fail open when subprocess times out."""
        import subprocess

        mock_get_repo.return_value = ("owner", "repo")
        mock_run.side_effect = subprocess.TimeoutExpired("gh", 30)

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert not result["thread_found"]

    @patch("subprocess.run")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_fails_open_on_json_error(self, mock_get_repo: MagicMock, mock_run: MagicMock):
        """Fail open when JSON parsing fails."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_run.return_value = MagicMock(returncode=0, stdout="not valid json", stderr="")

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert not result["thread_found"]

    @patch("subprocess.run")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_fails_open_on_os_error(self, mock_get_repo: MagicMock, mock_run: MagicMock):
        """Fail open when OSError occurs (e.g., gh not found)."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_run.side_effect = OSError("gh command not found")

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert not result["thread_found"]


class TestHasFixClaimWithoutVerification:
    """Tests for _has_fix_claim_without_verification function (Issue #970).

    This function detects when a comment claims a fix but lacks verification,
    preventing premature thread resolution.
    """

    def test_japanese_fix_claim_only(self):
        """「修正済み」のみ → True."""
        body = "修正済み: コミット abc123 で対応しました\n\n-- Claude Code"
        assert _has_fix_claim_without_verification(body)

    def test_japanese_fix_claim_with_verification(self):
        """「修正済み」+ 「Verified:」 → False."""
        body = (
            "修正済み: コミット abc123 で対応しました\n\n"
            "Verified: file.py:42 で修正を確認\n\n-- Claude Code"
        )
        assert not _has_fix_claim_without_verification(body)

    def test_japanese_fix_claim_with_japanese_verification(self):
        """「修正済み」+ 「検証済み:」 → False."""
        body = "修正済み: コミット abc123\n\n検証済み: 動作確認完了\n\n-- Claude Code"
        assert not _has_fix_claim_without_verification(body)

    def test_japanese_fix_claim_with_confirmed_verification(self):
        """「修正済み」+ 「確認済み:」 → False."""
        body = "修正済み: コミット abc123\n\n確認済み: テストパス\n\n-- Claude Code"
        assert not _has_fix_claim_without_verification(body)

    def test_english_fixed_claim_only(self):
        """「Fixed:」のみ → True."""
        body = "Fixed: commit abc123 addresses this issue\n\n-- Claude Code"
        assert _has_fix_claim_without_verification(body)

    def test_english_fixed_claim_with_verification(self):
        """「Fixed:」+ 「Verified:」 → False."""
        body = (
            "Fixed: commit abc123\n\n"
            "Verified: Checked file.py:42 - the issue is resolved\n\n-- Claude Code"
        )
        assert not _has_fix_claim_without_verification(body)

    def test_english_fixed_claim_with_verified_at(self):
        """「Fixed:」+ 「Verified at」 → False."""
        body = "Fixed: in commit abc123\n\nVerified at file.py:42\n\n-- Claude Code"
        assert not _has_fix_claim_without_verification(body)

    def test_no_fix_claim(self):
        """通常のコメント（修正主張なし） → False."""
        body = "This looks good to me.\n\n-- Claude Code"
        assert not _has_fix_claim_without_verification(body)

    def test_added_without_verification(self):
        """「Added」のみ → True."""
        body = "Added error handling for edge cases\n\n-- Claude Code"
        assert _has_fix_claim_without_verification(body)

    def test_updated_without_verification(self):
        """「Updated」のみ → True."""
        body = "Updated the function to handle null values\n\n-- Claude Code"
        assert _has_fix_claim_without_verification(body)

    def test_changed_without_verification(self):
        """「Changed」のみ → True."""
        body = "Changed the implementation as suggested\n\n-- Claude Code"
        assert _has_fix_claim_without_verification(body)

    def test_implemented_without_verification(self):
        """「Implemented」のみ → True."""
        body = "Implemented the new feature\n\n-- Claude Code"
        assert _has_fix_claim_without_verification(body)

    def test_already_addressed_without_verification(self):
        """「Already addressed:」のみ → True."""
        body = "Already addressed: this was fixed in a previous commit\n\n-- Claude Code"
        assert _has_fix_claim_without_verification(body)

    def test_taiou_zumi_without_verification(self):
        """「対応済み」のみ → True."""
        body = "対応済み: 前回のコミットで修正\n\n-- Claude Code"
        assert _has_fix_claim_without_verification(body)

    def test_empty_body(self):
        """空のボディ → False."""
        assert not _has_fix_claim_without_verification("")

    def test_case_insensitive_fixed(self):
        """「FIXED:」(大文字) → True."""
        body = "FIXED: issue resolved\n\n-- Claude Code"
        assert _has_fix_claim_without_verification(body)

    def test_case_insensitive_verified(self):
        """「Fixed:」+ 「VERIFIED:」(大文字) → False."""
        body = "Fixed: issue\n\nVERIFIED: checked the fix\n\n-- Claude Code"
        assert not _has_fix_claim_without_verification(body)


class TestHasUnverifiedFixIntegration:
    """Integration tests for has_unverified_fix field (Issue #970).

    Tests the full flow of check_thread_has_response including
    unverified fix detection.
    """

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_claude_code_with_fix_claim_no_verification(
        self, mock_get_repo: MagicMock, mock_subprocess: MagicMock
    ):
        """Claude Code署名 + 修正主張 + 検証なし → has_unverified_fix=True."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "comments": {
                                "nodes": [
                                    {
                                        "body": "Please fix the error handling",
                                        "author": {"login": "copilot"},
                                    },
                                    {
                                        "body": "修正済み: コミット abc123 で対応\n\n-- Claude Code",
                                        "author": {"login": "user"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert result["has_unverified_fix"]
        assert result["thread_found"]

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_claude_code_with_fix_claim_and_verification(
        self, mock_get_repo: MagicMock, mock_subprocess: MagicMock
    ):
        """Claude Code署名 + 修正主張 + 検証あり → has_unverified_fix=False."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "comments": {
                                "nodes": [
                                    {
                                        "body": "Please fix the error handling",
                                        "author": {"login": "copilot"},
                                    },
                                    {
                                        "body": (
                                            "修正済み: コミット abc123 で対応\n\n"
                                            "Verified: file.py:42 で修正を確認\n\n"
                                            "-- Claude Code"
                                        ),
                                        "author": {"login": "user"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert not result["has_unverified_fix"]
        assert result["thread_found"]

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_no_claude_code_signature_with_fix_claim(
        self, mock_get_repo: MagicMock, mock_subprocess: MagicMock
    ):
        """Claude Code署名なし + 修正主張 → has_unverified_fix=False."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "comments": {
                                "nodes": [
                                    {
                                        "body": "Please fix the error handling",
                                        "author": {"login": "copilot"},
                                    },
                                    {
                                        # Fix claim but no Claude Code signature
                                        "body": "修正済み: コミット abc123 で対応しました",
                                        "author": {"login": "user"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        # No Claude Code signature, so has_response is False
        assert not result["has_response"]
        # has_unverified_fix only applies to Claude Code comments
        assert not result["has_unverified_fix"]
        assert result["thread_found"]

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_claude_code_no_fix_claim(self, mock_get_repo: MagicMock, mock_subprocess: MagicMock):
        """Claude Code署名 + 修正主張なし → has_unverified_fix=False."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "comments": {
                                "nodes": [
                                    {
                                        "body": "Please fix the error handling",
                                        "author": {"login": "copilot"},
                                    },
                                    {
                                        # Claude Code signature but no fix claim
                                        "body": "この指摘は対象外です。\n\n-- Claude Code",
                                        "author": {"login": "user"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert not result["has_unverified_fix"]
        assert result["thread_found"]

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_english_fixed_without_verification(
        self, mock_get_repo: MagicMock, mock_subprocess: MagicMock
    ):
        """English 「Fixed:」 without verification → has_unverified_fix=True."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "comments": {
                                "nodes": [
                                    {
                                        "body": "Add error handling",
                                        "author": {"login": "copilot"},
                                    },
                                    {
                                        "body": "Fixed: Added try-catch block\n\n-- Claude Code",
                                        "author": {"login": "user"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert result["has_unverified_fix"]
        assert result["thread_found"]

    @patch.object(_module, "get_repo_owner_and_name")
    def test_fails_open_no_unverified_fix(self, mock_get_repo: MagicMock):
        """Fail-open時は has_unverified_fix=False."""
        mock_get_repo.return_value = None

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert not result["has_unverified_fix"]
        assert not result["thread_found"]


class TestHasVerification:
    """Tests for _has_verification function (Issue #1018)."""

    def test_verified_english(self):
        """Should detect English verification patterns."""
        assert _has_verification("Verified: test passed")
        assert _has_verification("verified at commit abc123")

    def test_verified_japanese(self):
        """Should detect Japanese verification patterns."""
        assert _has_verification("検証済み: テスト実行")
        assert _has_verification("確認済み: 問題なし")

    def test_no_verification(self):
        """Should not detect when no verification pattern."""
        assert not _has_verification("Just a comment")
        assert not _has_verification("修正済み: fixed something")


class TestThreadLevelVerification:
    """Tests for thread-level verification logic (Issue #1018 fix).

    Issue #1018: When a fix claim is in one comment and verification is in
    another comment, the thread should be considered verified.
    """

    def test_separate_fix_and_verification_comments(self):
        """Should consider verified when fix and verification are in separate comments.

        This is the main Issue #1018 scenario:
        - Comment 1: "修正済み: xxx\n\n-- Claude Code" (fix without verification)
        - Comment 2: "Verified: yyy\n\n-- Claude Code" (just verification)

        The thread should be considered verified because there's a Verified: somewhere.
        """
        comments = [
            {"body": "修正済み: xxx\n\n-- Claude Code"},
            {"body": "Verified: テスト実行済み\n\n-- Claude Code"},
        ]

        has_fix_claim = any(
            _has_claude_code_signature(c["body"]) and _has_fix_claim_without_verification(c["body"])
            for c in comments
        )
        thread_has_verification = any(_has_verification(c["body"]) for c in comments)
        has_unverified_fix = has_fix_claim and not thread_has_verification

        assert has_fix_claim is True
        assert thread_has_verification is True
        assert has_unverified_fix is False

    def test_fix_without_any_verification_in_thread(self):
        """Should flag when there's no verification anywhere in thread."""
        comments = [
            {"body": "Original review comment"},
            {"body": "修正済み: xxx\n\n-- Claude Code"},
        ]

        has_fix_claim = any(
            _has_claude_code_signature(c["body"]) and _has_fix_claim_without_verification(c["body"])
            for c in comments
        )
        thread_has_verification = any(_has_verification(c["body"]) for c in comments)
        has_unverified_fix = has_fix_claim and not thread_has_verification

        assert has_fix_claim is True
        assert thread_has_verification is False
        assert has_unverified_fix is True

    def test_verification_from_any_author(self):
        """Verification from any author (including non-Claude) should count."""
        comments = [
            {"body": "修正済み: xxx\n\n-- Claude Code"},
            {"body": "Verified: looks good"},  # Human verification, no signature
        ]

        has_fix_claim = any(
            _has_claude_code_signature(c["body"]) and _has_fix_claim_without_verification(c["body"])
            for c in comments
        )
        thread_has_verification = any(_has_verification(c["body"]) for c in comments)
        has_unverified_fix = has_fix_claim and not thread_has_verification

        assert thread_has_verification is True
        assert has_unverified_fix is False


class TestCheckThreadHasResponsePRInfo:
    """Tests for PR number and comment ID extraction (Issue #1332).

    Issue #1332: check_thread_has_response should return pr_number and
    comment_id for review quality logging.
    """

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_returns_pr_number_and_comment_id(
        self, mock_get_repo: MagicMock, mock_subprocess: MagicMock
    ):
        """Should return PR number and comment ID from thread."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "pullRequest": {"number": 42},
                            "comments": {
                                "nodes": [
                                    {
                                        "databaseId": 12345,
                                        "body": "Please fix this",
                                        "author": {"login": "copilot"},
                                    },
                                    {
                                        "databaseId": 12346,
                                        "body": "Fixed.\n\nVerified: test passed\n\n-- Claude Code",
                                        "author": {"login": "user"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        assert result["pr_number"] == 42
        assert result["comment_id"] == 12345  # First comment's ID
        assert result["has_response"]
        assert result["thread_found"]

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_handles_missing_pr_number(self, mock_get_repo: MagicMock, mock_subprocess: MagicMock):
        """Should handle case where pullRequest is missing."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            # No pullRequest field
                            "comments": {
                                "nodes": [
                                    {
                                        "databaseId": 12345,
                                        "body": "Comment\n\n-- Claude Code",
                                        "author": {"login": "user"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        assert result["pr_number"] is None
        assert result["comment_id"] == 12345
        assert result["has_response"]

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_handles_missing_database_id(
        self, mock_get_repo: MagicMock, mock_subprocess: MagicMock
    ):
        """Should handle case where databaseId is missing."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "pullRequest": {"number": 42},
                            "comments": {
                                "nodes": [
                                    {
                                        # No databaseId field
                                        "body": "Comment\n\n-- Claude Code",
                                        "author": {"login": "user"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        assert result["pr_number"] == 42
        assert result["comment_id"] is None
        assert result["has_response"]


class TestRestApiReplies:
    """Tests for REST API fallback (Issue #1271).

    Issue #1271: GraphQL may not immediately reflect comments added via REST API.
    The hook should also check REST API for replies to ensure consistency.
    """

    @patch.object(_module, "subprocess")
    def test_check_rest_api_replies_success(self, mock_subprocess: MagicMock):
        """Should return replies from REST API."""
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                [
                    {"id": 100, "body": "Original comment", "in_reply_to_id": None},
                    {"id": 101, "body": "Reply 1\n\n-- Claude Code", "in_reply_to_id": 100},
                    {"id": 102, "body": "Reply 2", "in_reply_to_id": 100},
                    {"id": 103, "body": "Unrelated reply", "in_reply_to_id": 99},
                ]
            ),
            stderr="",
        )

        # Import the function
        _check_rest_api_replies = _module._check_rest_api_replies

        result = _check_rest_api_replies("owner", "repo", 42, 100)

        # Should only return replies to comment 100
        assert len(result) == 2
        assert result[0]["id"] == 101
        assert result[1]["id"] == 102

    @patch.object(_module, "subprocess")
    def test_check_rest_api_replies_multipage(self, mock_subprocess: MagicMock):
        """Should handle multi-page output from --paginate (Issue #1271)."""
        # gh api --paginate outputs multiple JSON arrays separated by newlines
        page1 = json.dumps(
            [
                {"id": 100, "body": "Original comment", "in_reply_to_id": None},
                {"id": 101, "body": "Reply 1", "in_reply_to_id": 100},
            ]
        )
        page2 = json.dumps(
            [
                {"id": 102, "body": "Reply 2\n\n-- Claude Code", "in_reply_to_id": 100},
                {"id": 103, "body": "Unrelated", "in_reply_to_id": 99},
            ]
        )
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=f"{page1}\n{page2}",
            stderr="",
        )

        _check_rest_api_replies = _module._check_rest_api_replies

        result = _check_rest_api_replies("owner", "repo", 42, 100)

        # Should find replies from both pages
        assert len(result) == 2
        assert result[0]["id"] == 101
        assert result[1]["id"] == 102

    @patch.object(_module, "subprocess")
    def test_check_rest_api_replies_api_failure(self, mock_subprocess: MagicMock):
        """Should return empty list on API failure (fail-open)."""
        mock_subprocess.run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="API error",
        )

        _check_rest_api_replies = _module._check_rest_api_replies

        result = _check_rest_api_replies("owner", "repo", 42, 100)

        assert result == []

    @patch.object(_module, "subprocess")
    def test_check_rest_api_replies_timeout(self, mock_subprocess: MagicMock):
        """Should return empty list on timeout (fail-open)."""
        from subprocess import TimeoutExpired

        mock_subprocess.run.side_effect = TimeoutExpired("gh", 30)

        _check_rest_api_replies = _module._check_rest_api_replies

        result = _check_rest_api_replies("owner", "repo", 42, 100)

        assert result == []

    @patch.object(_module, "subprocess")
    def test_check_rest_api_replies_json_error(self, mock_subprocess: MagicMock):
        """Should return empty list on JSON parse error (fail-open)."""
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout="not valid json",
            stderr="",
        )

        _check_rest_api_replies = _module._check_rest_api_replies

        result = _check_rest_api_replies("owner", "repo", 42, 100)

        assert result == []


class TestRestApiIntegration:
    """Integration tests for REST API fallback in check_thread_has_response (Issue #1271)."""

    @patch.object(_module, "_check_rest_api_replies")
    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_finds_claude_code_response_via_rest_api(
        self,
        mock_get_repo: MagicMock,
        mock_subprocess: MagicMock,
        mock_rest_api: MagicMock,
    ):
        """Should find Claude Code response via REST API when GraphQL doesn't have it."""
        mock_get_repo.return_value = ("owner", "repo")

        # GraphQL returns thread with no Claude Code response
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "pullRequest": {"number": 42},
                            "comments": {
                                "nodes": [
                                    {
                                        "databaseId": 100,
                                        "body": "Please fix this issue",
                                        "author": {"login": "copilot"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        # REST API returns a Claude Code reply
        mock_rest_api.return_value = [
            {"body": "Fixed in commit abc123\n\n-- Claude Code", "id": 101},
        ]

        result = check_thread_has_response("PRRT_test123")

        # Should find response via REST API
        assert result["has_response"]
        assert result["thread_found"]

        # Verify REST API was called with correct parameters
        mock_rest_api.assert_called_once_with("owner", "repo", 42, 100)

    @patch.object(_module, "_check_rest_api_replies")
    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_rest_api_verification_counts(
        self,
        mock_get_repo: MagicMock,
        mock_subprocess: MagicMock,
        mock_rest_api: MagicMock,
    ):
        """Verification in REST API reply should count for thread-level verification."""
        mock_get_repo.return_value = ("owner", "repo")

        # GraphQL: Claude Code fix claim without verification
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "pullRequest": {"number": 42},
                            "comments": {
                                "nodes": [
                                    {
                                        "databaseId": 100,
                                        "body": "Please fix this",
                                        "author": {"login": "copilot"},
                                    },
                                    {
                                        "databaseId": 101,
                                        "body": "修正済み: commit abc123\n\n-- Claude Code",
                                        "author": {"login": "user"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        # REST API has verification
        mock_rest_api.return_value = [
            {"body": "Verified: test passed\n\n-- Claude Code", "id": 102},
        ]

        result = check_thread_has_response("PRRT_test123")

        # Should have response and no unverified fix
        assert result["has_response"]
        assert not result["has_unverified_fix"]  # REST API verification counts

    @patch.object(_module, "_check_rest_api_replies")
    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_rest_api_not_called_when_no_pr_number(
        self,
        mock_get_repo: MagicMock,
        mock_subprocess: MagicMock,
        mock_rest_api: MagicMock,
    ):
        """REST API should not be called when PR number is missing."""
        mock_get_repo.return_value = ("owner", "repo")

        # GraphQL returns thread without PR number
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            # No pullRequest field
                            "comments": {
                                "nodes": [
                                    {
                                        "databaseId": 100,
                                        "body": "Please fix this",
                                        "author": {"login": "copilot"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        check_thread_has_response("PRRT_test123")

        # REST API should not be called
        mock_rest_api.assert_not_called()


class TestLogReviewCommentOnApproval:
    """Tests for log_review_comment call on resolution approval (Issue #1332).

    When a thread resolution is approved, the hook should log the review
    comment to review-quality.jsonl using log_review_comment.
    """

    @patch.object(_module, "log_review_comment")
    @patch.object(_module, "log_hook_execution")
    @patch.object(_module, "check_thread_has_response")
    @patch.object(_module, "parse_hook_input")
    def test_logs_review_comment_on_approval(
        self,
        mock_parse: MagicMock,
        mock_check: MagicMock,
        mock_log_hook: MagicMock,
        mock_log_review: MagicMock,
        capsys,
    ):
        """Should call log_review_comment when thread resolution is approved."""
        mock_parse.return_value = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "gh api graphql -F threadId=PRRT_test123 -f query='mutation...resolveReviewThread...'"
            },
        }
        mock_check.return_value = {
            "has_response": True,
            "has_unverified_fix": False,
            "thread_found": True,
            "original_comment": "Fix this",
            "author": "copilot-pull-request-reviewer[bot]",
            "pr_number": 42,
            "comment_id": 12345,
        }

        # Run main
        _module.main()

        # Check log_review_comment was called with normalized reviewer
        mock_log_review.assert_called_once_with(
            pr_number=42,
            comment_id=12345,
            reviewer="copilot",  # Normalized from copilot-pull-request-reviewer[bot]
            resolution="accepted",
        )

        # Verify output
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["decision"] == "approve"

    @patch.object(_module, "log_review_comment")
    @patch.object(_module, "log_hook_execution")
    @patch.object(_module, "check_thread_has_response")
    @patch.object(_module, "parse_hook_input")
    def test_does_not_log_when_pr_number_missing(
        self,
        mock_parse: MagicMock,
        mock_check: MagicMock,
        mock_log_hook: MagicMock,
        mock_log_review: MagicMock,
        capsys,
    ):
        """Should not call log_review_comment when pr_number is None."""
        mock_parse.return_value = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "gh api graphql -F threadId=PRRT_test123 -f query='mutation...resolveReviewThread...'"
            },
        }
        mock_check.return_value = {
            "has_response": True,
            "has_unverified_fix": False,
            "thread_found": True,
            "original_comment": "Fix this",
            "author": "copilot",
            "pr_number": None,  # Missing PR number
            "comment_id": 12345,
        }

        _module.main()

        mock_log_review.assert_not_called()

    @patch.object(_module, "log_review_comment")
    @patch.object(_module, "log_hook_execution")
    @patch.object(_module, "check_thread_has_response")
    @patch.object(_module, "parse_hook_input")
    def test_does_not_log_when_comment_id_missing(
        self,
        mock_parse: MagicMock,
        mock_check: MagicMock,
        mock_log_hook: MagicMock,
        mock_log_review: MagicMock,
        capsys,
    ):
        """Should not call log_review_comment when comment_id is None."""
        mock_parse.return_value = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "gh api graphql -F threadId=PRRT_test123 -f query='mutation...resolveReviewThread...'"
            },
        }
        mock_check.return_value = {
            "has_response": True,
            "has_unverified_fix": False,
            "thread_found": True,
            "original_comment": "Fix this",
            "author": "copilot",
            "pr_number": 42,
            "comment_id": None,  # Missing comment ID
        }

        _module.main()

        mock_log_review.assert_not_called()

    @patch.object(_module, "log_review_comment")
    @patch.object(_module, "log_hook_execution")
    @patch.object(_module, "check_thread_has_response")
    @patch.object(_module, "parse_hook_input")
    def test_continues_on_logging_error(
        self,
        mock_parse: MagicMock,
        mock_check: MagicMock,
        mock_log_hook: MagicMock,
        mock_log_review: MagicMock,
        capsys,
    ):
        """Should not block resolution if log_review_comment fails."""
        mock_parse.return_value = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "gh api graphql -F threadId=PRRT_test123 -f query='mutation...resolveReviewThread...'"
            },
        }
        mock_check.return_value = {
            "has_response": True,
            "has_unverified_fix": False,
            "thread_found": True,
            "original_comment": "Fix this",
            "author": "copilot",
            "pr_number": 42,
            "comment_id": 12345,
        }
        # Simulate logging failure
        mock_log_review.side_effect = OSError("Disk full")

        _module.main()

        # Should still approve
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["decision"] == "approve"

    @patch.object(_module, "log_review_comment")
    @patch.object(_module, "log_hook_execution")
    @patch.object(_module, "check_thread_has_response")
    @patch.object(_module, "parse_hook_input")
    def test_normalizes_codex_reviewer(
        self,
        mock_parse: MagicMock,
        mock_check: MagicMock,
        mock_log_hook: MagicMock,
        mock_log_review: MagicMock,
        capsys,
    ):
        """Should normalize codex reviewer name correctly."""
        mock_parse.return_value = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "gh api graphql -F threadId=PRRT_test123 -f query='mutation...resolveReviewThread...'"
            },
        }
        mock_check.return_value = {
            "has_response": True,
            "has_unverified_fix": False,
            "thread_found": True,
            "original_comment": "Fix this",
            "author": "chatgpt-codex-connector[bot]",  # Codex bot
            "pr_number": 42,
            "comment_id": 12345,
        }

        _module.main()

        mock_log_review.assert_called_once_with(
            pr_number=42,
            comment_id=12345,
            reviewer="codex_cloud",  # Normalized from chatgpt-codex-connector[bot]
            resolution="accepted",
        )


class TestHasOutOfScopeWithoutIssue:
    """Tests for _has_out_of_scope_without_issue function (Issue #1657).

    This function detects when a comment uses out-of-scope keywords
    without referencing an Issue number, preventing the common mistake
    of deferring work without proper tracking.
    """

    def test_japanese_out_of_scope_without_issue(self):
        """「範囲外」のみ → has_problem=True."""
        body = "これは範囲外のため対応しません。\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert has_problem
        assert keyword == "範囲外"

    def test_japanese_out_of_scope_with_issue(self):
        """「範囲外」+ Issue参照あり → has_problem=False."""
        body = "範囲外のため、Issue #1234 を作成しました。\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert not has_problem
        assert keyword is None

    def test_japanese_scope_gai_without_issue(self):
        """「スコープ外」のみ → has_problem=True."""
        body = "このPRのスコープ外です。\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert has_problem
        assert keyword == "スコープ外"

    def test_japanese_future_improvement_without_issue(self):
        """「将来対応」のみ → has_problem=True."""
        body = "将来対応として検討します。\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert has_problem
        assert keyword == "将来対応"

    def test_japanese_follow_up_without_issue(self):
        """「後でフォローアップ」のみ → has_problem=True."""
        body = "後でフォローアップとして対応します。\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert has_problem
        assert keyword == "後でフォローアップ"

    def test_japanese_follow_up_as_without_issue(self):
        """「フォローアップとして」のみ → has_problem=True."""
        body = "フォローアップとして検討します。\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert has_problem
        assert keyword == "フォローアップとして"

    def test_japanese_future_improvement_kaizen_without_issue(self):
        """「今後の改善」のみ → has_problem=True."""
        body = "今後の改善として検討します。\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert has_problem
        assert keyword == "今後の改善"

    def test_japanese_separate_handling_without_issue(self):
        """「別途対応」のみ → has_problem=True."""
        body = "別途対応が必要です。\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert has_problem
        assert keyword == "別途対応"

    def test_english_out_of_scope_without_issue(self):
        """「out of scope」のみ → has_problem=True."""
        body = "This is out of scope for this PR.\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert has_problem
        assert keyword == "out of scope"

    def test_english_out_of_scope_with_issue(self):
        """「out of scope」+ Issue参照あり → has_problem=False."""
        body = "This is out of scope. Created Issue #5678.\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert not has_problem
        assert keyword is None

    def test_english_future_improvement_without_issue(self):
        """「future improvement」のみ → has_problem=True."""
        body = "This is a future improvement.\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert has_problem
        assert keyword == "future improvement"

    def test_english_follow_up_hyphen_without_issue(self):
        """「follow-up」のみ → has_problem=True."""
        body = "This needs follow-up work.\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert has_problem
        assert keyword == "follow-up"

    def test_english_follow_up_space_without_issue(self):
        """「follow up」のみ → has_problem=True."""
        body = "I will follow up on this later.\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert has_problem
        assert keyword == "follow up"

    def test_issue_reference_hash_format(self):
        """Issue参照 #123 形式で検出 → has_problem=False."""
        body = "範囲外のため #1234 で追跡します。\n\n-- Claude Code"
        has_problem, _ = _has_out_of_scope_without_issue(body)
        assert not has_problem

    def test_issue_reference_issue_hash_format(self):
        """Issue参照 Issue #123 形式で検出 → has_problem=False."""
        body = "スコープ外のため Issue #999 を作成しました。\n\n-- Claude Code"
        has_problem, _ = _has_out_of_scope_without_issue(body)
        assert not has_problem

    def test_issue_reference_issue_space_format(self):
        """Issue参照 Issue 123 形式で検出 → has_problem=False."""
        body = "Out of scope, created Issue 42.\n\n-- Claude Code"
        has_problem, _ = _has_out_of_scope_without_issue(body)
        assert not has_problem

    def test_no_out_of_scope_keyword(self):
        """範囲外キーワードなし → has_problem=False."""
        body = "修正済み: コミット abc123 で対応しました。\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert not has_problem
        assert keyword is None

    def test_empty_body(self):
        """空のボディ → has_problem=False."""
        has_problem, keyword = _has_out_of_scope_without_issue("")
        assert not has_problem
        assert keyword is None

    def test_case_insensitive_keyword(self):
        """キーワードは大文字小文字を区別しない."""
        body = "This is OUT OF SCOPE.\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert has_problem
        assert keyword == "out of scope"

    def test_case_insensitive_issue_reference(self):
        """Issue参照は大文字小文字を区別しない."""
        body = "Out of scope, see issue #123.\n\n-- Claude Code"
        has_problem, _ = _has_out_of_scope_without_issue(body)
        assert not has_problem

    def test_no_false_positive_following_update(self):
        """「following update」は「follow up」にマッチしない."""
        body = "I am following update procedures.\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert not has_problem
        assert keyword is None

    def test_no_false_positive_out_of_scopes(self):
        """「out of scopes」(複数形)は検出しない（誤検知防止）."""
        body = "This is out of scopes for the project.\n\n-- Claude Code"
        # 単語境界 \b を使用しているため、「out of scopes」は「out of scope」にマッチしない
        # （scopeとsの間に単語境界がないため）
        # これは意図した動作で、複数形など派生形での誤検知を防ぐ
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert not has_problem
        assert keyword is None

    def test_no_false_positive_scope_out(self):
        """「scope out」は「out of scope」にマッチしない."""
        body = "I will scope out the problem.\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        assert not has_problem
        assert keyword is None

    def test_no_false_positive_url_fragment(self):
        """URLフラグメント(#123)はIssue参照と誤認しない."""
        body = "範囲外です。See https://example.com/page#123\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        # URLフラグメントはIssue参照ではないのでブロックされる
        assert has_problem
        assert keyword == "範囲外"

    def test_no_false_positive_markdown_heading(self):
        """Markdown見出し(### 123)はIssue参照と誤認しない."""
        body = "範囲外です。\n### 123 手順\n\n-- Claude Code"
        has_problem, keyword = _has_out_of_scope_without_issue(body)
        # Markdown見出しはIssue参照ではないのでブロックされる
        assert has_problem
        assert keyword == "範囲外"

    def test_issue_reference_at_start_of_line(self):
        """行頭の#123はIssue参照として認識."""
        body = "範囲外です。\n#123 で追跡します。\n\n-- Claude Code"
        has_problem, _ = _has_out_of_scope_without_issue(body)
        assert not has_problem

    def test_issue_reference_after_space(self):
        """スペース後の#123はIssue参照として認識."""
        body = "範囲外です。Issue作成 #456 を参照\n\n-- Claude Code"
        has_problem, _ = _has_out_of_scope_without_issue(body)
        assert not has_problem


class TestOutOfScopeIntegration:
    """Integration tests for out-of-scope keyword detection (Issue #1657).

    Tests the full flow of check_thread_has_response including
    out-of-scope keyword detection.
    """

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_claude_code_out_of_scope_without_issue(
        self, mock_get_repo: MagicMock, mock_subprocess: MagicMock
    ):
        """Claude Code署名 + 範囲外キーワード + Issue参照なし → out_of_scope_keyword設定."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "pullRequest": {"number": 42},
                            "comments": {
                                "nodes": [
                                    {
                                        "databaseId": 12345,
                                        "body": "Please add error handling",
                                        "author": {"login": "copilot"},
                                    },
                                    {
                                        "databaseId": 12346,
                                        "body": "範囲外のため対応しません。\n\n-- Claude Code",
                                        "author": {"login": "user"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert result["out_of_scope_keyword"] == "範囲外"
        assert result["thread_found"]

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_claude_code_out_of_scope_with_issue(
        self, mock_get_repo: MagicMock, mock_subprocess: MagicMock
    ):
        """Claude Code署名 + 範囲外キーワード + Issue参照あり → out_of_scope_keyword=None."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "pullRequest": {"number": 42},
                            "comments": {
                                "nodes": [
                                    {
                                        "databaseId": 12345,
                                        "body": "Please add error handling",
                                        "author": {"login": "copilot"},
                                    },
                                    {
                                        "databaseId": 12346,
                                        "body": "範囲外のため Issue #999 を作成しました。\n\n-- Claude Code",
                                        "author": {"login": "user"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        assert result["has_response"]
        assert result["out_of_scope_keyword"] is None
        assert result["thread_found"]

    @patch.object(_module, "subprocess")
    @patch.object(_module, "get_repo_owner_and_name")
    def test_no_claude_code_signature_with_out_of_scope(
        self, mock_get_repo: MagicMock, mock_subprocess: MagicMock
    ):
        """Claude Code署名なし + 範囲外キーワード → out_of_scope_keyword=None."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "node": {
                            "id": "PRRT_test123",
                            "isResolved": False,
                            "pullRequest": {"number": 42},
                            "comments": {
                                "nodes": [
                                    {
                                        "databaseId": 12345,
                                        "body": "Please add error handling",
                                        "author": {"login": "copilot"},
                                    },
                                    {
                                        "databaseId": 12346,
                                        # Out of scope but no Claude Code signature
                                        "body": "範囲外のため対応しません。",
                                        "author": {"login": "user"},
                                    },
                                ]
                            },
                        }
                    }
                }
            ),
            stderr="",
        )

        result = check_thread_has_response("PRRT_test123")

        # No Claude Code signature, so out_of_scope_keyword is not checked
        assert not result["has_response"]
        assert result["out_of_scope_keyword"] is None
        assert result["thread_found"]


class TestOutOfScopeBlocking:
    """Tests for out-of-scope blocking in main() (Issue #1657).

    Tests that the hook properly blocks thread resolution when
    out-of-scope keywords are used without Issue references.
    """

    @patch.object(_module, "log_hook_execution")
    @patch.object(_module, "check_thread_has_response")
    @patch.object(_module, "parse_hook_input")
    def test_blocks_out_of_scope_without_issue(
        self,
        mock_parse: MagicMock,
        mock_check: MagicMock,
        mock_log_hook: MagicMock,
        capsys,
    ):
        """Should block when out-of-scope keyword without Issue reference."""
        mock_parse.return_value = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "gh api graphql -F threadId=PRRT_test123 -f query='mutation...resolveReviewThread...'"
            },
        }
        mock_check.return_value = {
            "has_response": True,
            "has_unverified_fix": False,
            "out_of_scope_keyword": "範囲外",
            "thread_found": True,
            "original_comment": "Please fix this",
            "author": "copilot",
            "pr_number": 42,
            "comment_id": 12345,
        }

        _module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["decision"] == "block"
        assert "範囲外発言にIssue番号がありません" in output["reason"]
        assert "範囲外" in output["reason"]

    @patch.object(_module, "log_review_comment")
    @patch.object(_module, "log_hook_execution")
    @patch.object(_module, "check_thread_has_response")
    @patch.object(_module, "parse_hook_input")
    def test_approves_out_of_scope_with_issue(
        self,
        mock_parse: MagicMock,
        mock_check: MagicMock,
        mock_log_hook: MagicMock,
        mock_log_review: MagicMock,
        capsys,
    ):
        """Should approve when out-of-scope keyword has Issue reference."""
        mock_parse.return_value = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "gh api graphql -F threadId=PRRT_test123 -f query='mutation...resolveReviewThread...'"
            },
        }
        mock_check.return_value = {
            "has_response": True,
            "has_unverified_fix": False,
            "out_of_scope_keyword": None,  # Has Issue reference
            "thread_found": True,
            "original_comment": "Please fix this",
            "author": "copilot",
            "pr_number": 42,
            "comment_id": 12345,
        }

        _module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["decision"] == "approve"

    @patch.object(_module, "log_hook_execution")
    @patch.object(_module, "check_thread_has_response")
    @patch.object(_module, "parse_hook_input")
    def test_block_message_contains_detected_keyword(
        self,
        mock_parse: MagicMock,
        mock_check: MagicMock,
        mock_log_hook: MagicMock,
        capsys,
    ):
        """Block message should contain the detected keyword."""
        mock_parse.return_value = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "gh api graphql -F threadId=PRRT_test123 -f query='mutation...resolveReviewThread...'"
            },
        }
        mock_check.return_value = {
            "has_response": True,
            "has_unverified_fix": False,
            "out_of_scope_keyword": "future improvement",
            "thread_found": True,
            "original_comment": "Add feature",
            "author": "copilot",
            "pr_number": 42,
            "comment_id": 12345,
        }

        _module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["decision"] == "block"
        assert "future improvement" in output["reason"]
        assert "gh issue create" in output["reason"]


class TestIsJapaneseChar:
    """Tests for is_japanese_char function (Issue #1685).

    Issue #1685: ord(c) > 127 では Latin-1 文字（é, ñ, ü等）も
    日本語として誤判定されるため、正確なUnicode範囲チェックに改善。
    """

    def test_hiragana(self):
        """ひらがなは日本語として検出."""
        assert is_japanese_char("あ")
        assert is_japanese_char("ん")
        assert is_japanese_char("を")

    def test_katakana(self):
        """カタカナは日本語として検出."""
        assert is_japanese_char("ア")
        assert is_japanese_char("ン")
        assert is_japanese_char("ー")  # 長音記号

    def test_cjk_kanji(self):
        """CJK統合漢字は日本語として検出."""
        assert is_japanese_char("日")
        assert is_japanese_char("本")
        assert is_japanese_char("語")

    def test_halfwidth_katakana(self):
        """半角カタカナは日本語として検出."""
        assert is_japanese_char("ｱ")
        assert is_japanese_char("ﾝ")

    def test_japanese_punctuation(self):
        """和文記号は日本語として検出."""
        assert is_japanese_char("。")
        assert is_japanese_char("、")
        assert is_japanese_char("々")  # 踊り字
        assert is_japanese_char("　")  # 全角スペース

    def test_ascii_not_detected(self):
        """ASCII文字は日本語として検出しない."""
        assert not is_japanese_char("a")
        assert not is_japanese_char("Z")
        assert not is_japanese_char("0")
        assert not is_japanese_char(" ")
        assert not is_japanese_char("!")

    def test_latin1_not_detected(self):
        """Latin-1文字は日本語として検出しない（Issue #1685の主要修正点）."""
        assert not is_japanese_char("é")  # French
        assert not is_japanese_char("ñ")  # Spanish
        assert not is_japanese_char("ü")  # German
        assert not is_japanese_char("ø")  # Nordic
        assert not is_japanese_char("ç")  # French/Portuguese

    def test_emoji_not_detected(self):
        """絵文字は日本語として検出しない."""
        assert not is_japanese_char("😀")
        assert not is_japanese_char("🎉")

    def test_chinese_simplified_detected(self):
        """中国語簡体字（CJK範囲内）は検出される."""
        # これは期待される動作：CJK統合漢字範囲なので検出される
        assert is_japanese_char("中")
        assert is_japanese_char("国")

    def test_korean_not_detected(self):
        """韓国語ハングルは日本語として検出しない."""
        assert not is_japanese_char("한")
        assert not is_japanese_char("글")

    def test_multi_char_raises_error(self):
        """複数文字の場合はValueErrorを発生."""
        with pytest.raises(ValueError):
            is_japanese_char("あい")
        with pytest.raises(ValueError):
            is_japanese_char("ab")

    def test_empty_string_raises_error(self):
        """空文字列の場合はValueErrorを発生."""
        with pytest.raises(ValueError):
            is_japanese_char("")
