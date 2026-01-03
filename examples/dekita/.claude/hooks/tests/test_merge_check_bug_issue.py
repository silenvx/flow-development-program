#!/usr/bin/env python3
"""Tests for merge-check.py - bug_issue module."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "merge-check.py"

# Import submodules for correct mock targets after modularization (Issue #1756)
# These imports enable tests to mock functions at their actual definition locations
import issue_checker


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


class TestBugIssueFromReview:
    """Tests for bug Issue detection from review comments (Issue #1130)."""

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_issue_creation_pattern_matches_issue_to_sakusei(self):
        """Pattern should match 'Issue #123 を作成'."""
        pattern = self.module.ISSUE_CREATION_PATTERN
        match = pattern.search("Issue #456 を作成しました")
        assert match is not None
        # Extract issue number from matched groups
        issue_num = next((g for g in match.groups() if g is not None), None)
        assert issue_num == "456"

    def test_issue_creation_pattern_matches_as_touroku(self):
        """Pattern should match '#123 として登録'."""
        pattern = self.module.ISSUE_CREATION_PATTERN
        match = pattern.search("#789 として登録済み")
        assert match is not None
        issue_num = next((g for g in match.groups() if g is not None), None)
        assert issue_num == "789"

    def test_issue_creation_pattern_matches_parentheses(self):
        """Pattern should match '(Issue #123)' format."""
        pattern = self.module.ISSUE_CREATION_PATTERN
        match = pattern.search("範囲外のため (Issue #111) で対応")
        assert match is not None
        issue_num = next((g for g in match.groups() if g is not None), None)
        assert issue_num == "111"

    def test_issue_creation_pattern_matches_hash_only(self):
        """Pattern should match '(#123)' format."""
        pattern = self.module.ISSUE_CREATION_PATTERN
        match = pattern.search("別途対応 (#222)")
        assert match is not None
        issue_num = next((g for g in match.groups() if g is not None), None)
        assert issue_num == "222"

    def test_bug_title_keywords_detection(self):
        """BUG_ISSUE_TITLE_KEYWORDS should include common patterns."""
        keywords = self.module.BUG_ISSUE_TITLE_KEYWORDS
        # Check that common patterns are included
        assert any("fix:" in k.lower() for k in keywords)
        assert any("bug" in k.lower() for k in keywords)
        assert any("バグ" in k for k in keywords)

    def test_check_bug_issue_from_review_returns_empty_on_error(self):
        """Should return empty list when API calls fail."""
        with patch.object(issue_checker, "get_repo_owner_and_name", return_value=None):
            result = self.module.check_bug_issue_from_review("123")
        assert result == []

    def test_check_bug_issue_from_review_with_no_threads(self):
        """Should return empty list when no review threads exist."""
        mock_graphql_response = {
            "data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}
        }
        with (
            patch.object(issue_checker, "get_repo_owner_and_name", return_value=("owner", "repo")),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(mock_graphql_response),
            )
            result = self.module.check_bug_issue_from_review("123")
        assert result == []

    def test_check_bug_issue_blocks_when_open_bug_issue_found(self):
        """Should detect open bug Issues created from review."""
        # Mock GraphQL response with a Claude Code comment referencing Issue #999
        mock_graphql_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "comments": {
                                        "nodes": [
                                            {
                                                "body": "バグを発見。Issue #999 を作成しました\n\n-- Claude Code",
                                                "author": {"login": "claude-code"},
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        }

        # Mock Issue #999 response - open bug issue referencing PR #123
        mock_issue_response = json.dumps(
            {
                "title": "fix: hooks-design-check.pyのバグ",
                "state": "OPEN",
                "labels": [{"name": "bug"}],
                "body": "PR #123 のレビューで発見したバグ",
            }
        )

        with (
            patch.object(issue_checker, "get_repo_owner_and_name", return_value=("owner", "repo")),
            patch("subprocess.run") as mock_run,
        ):
            # Return different responses for different commands
            def side_effect(*args, **kwargs):
                cmd = args[0] if args else kwargs.get("args", [])
                if "graphql" in cmd:
                    return MagicMock(
                        returncode=0,
                        stdout=json.dumps(mock_graphql_response),
                    )
                elif "issue" in cmd and "view" in cmd:
                    return MagicMock(
                        returncode=0,
                        stdout=mock_issue_response,
                    )
                return MagicMock(returncode=1, stdout="")

            mock_run.side_effect = side_effect
            result = self.module.check_bug_issue_from_review("123")

        # Should detect Issue #999 as a bug issue from review
        assert len(result) == 1
        assert result[0]["issue_number"] == "999"
        assert "fix:" in result[0]["title"].lower()

    def test_check_bug_issue_ignores_closed_issues(self):
        """Should not flag closed Issues."""
        mock_graphql_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "comments": {
                                        "nodes": [
                                            {
                                                "body": "Issue #888 を作成\n\n-- Claude Code",
                                                "author": {"login": "claude-code"},
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        }

        mock_issue_response = json.dumps(
            {
                "title": "fix: closed bug",
                "state": "CLOSED",  # Already closed
                "labels": [{"name": "bug"}],
                "body": "PR #123 のレビューで発見",
            }
        )

        with (
            patch.object(issue_checker, "get_repo_owner_and_name", return_value=("owner", "repo")),
            patch("subprocess.run") as mock_run,
        ):

            def side_effect(*args, **kwargs):
                cmd = args[0] if args else kwargs.get("args", [])
                if "graphql" in cmd:
                    return MagicMock(returncode=0, stdout=json.dumps(mock_graphql_response))
                elif "issue" in cmd and "view" in cmd:
                    return MagicMock(returncode=0, stdout=mock_issue_response)
                return MagicMock(returncode=1, stdout="")

            mock_run.side_effect = side_effect
            result = self.module.check_bug_issue_from_review("123")

        # Closed issues should not be flagged
        assert result == []

    def test_check_bug_issue_ignores_non_bug_issues(self):
        """Should not flag Issues without bug-related title/labels."""
        mock_graphql_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "comments": {
                                        "nodes": [
                                            {
                                                "body": "Issue #777 として登録\n\n-- Claude Code",
                                                "author": {"login": "claude-code"},
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        }

        mock_issue_response = json.dumps(
            {
                "title": "feat: new feature request",  # Not a bug
                "state": "OPEN",
                "labels": [{"name": "enhancement"}],  # Not a bug label
                "body": "PR #123 のレビューで提案",
            }
        )

        with (
            patch.object(issue_checker, "get_repo_owner_and_name", return_value=("owner", "repo")),
            patch("subprocess.run") as mock_run,
        ):

            def side_effect(*args, **kwargs):
                cmd = args[0] if args else kwargs.get("args", [])
                if "graphql" in cmd:
                    return MagicMock(returncode=0, stdout=json.dumps(mock_graphql_response))
                elif "issue" in cmd and "view" in cmd:
                    return MagicMock(returncode=0, stdout=mock_issue_response)
                return MagicMock(returncode=1, stdout="")

            mock_run.side_effect = side_effect
            result = self.module.check_bug_issue_from_review("123")

        # Non-bug issues should not be flagged
        assert result == []


class TestCollectIssueRefsFromReview:
    """Tests for _collect_issue_refs_from_review helper function (Issue #1152)."""

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_returns_empty_on_api_error(self):
        """Should return empty dict when API call fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result, pr_created_at = self.module._collect_issue_refs_from_review(
                "123", "owner", "repo"
            )
        assert result == {}
        assert pr_created_at == ""

    def test_returns_empty_when_no_threads(self):
        """Should return empty dict when no review threads exist."""
        mock_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "createdAt": "2025-01-01T00:00:00Z",
                        "reviewThreads": {"nodes": []},
                    }
                }
            }
        }
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(mock_response))
            result, pr_created_at = self.module._collect_issue_refs_from_review(
                "123", "owner", "repo"
            )
        assert result == {}
        assert pr_created_at == "2025-01-01T00:00:00Z"

    def test_extracts_issue_refs_from_claude_comments(self):
        """Should extract Issue references from Claude Code comments."""
        mock_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "createdAt": "2025-01-01T00:00:00Z",
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "comments": {
                                        "nodes": [
                                            {
                                                "body": "Issue #456 を作成しました\n\n-- Claude Code",
                                                "author": {"login": "silenvx"},
                                            }
                                        ]
                                    }
                                }
                            ]
                        },
                    }
                }
            }
        }
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(mock_response))
            result, pr_created_at = self.module._collect_issue_refs_from_review(
                "123", "owner", "repo"
            )
        assert "456" in result
        assert pr_created_at == "2025-01-01T00:00:00Z"

    def test_ignores_non_claude_comments(self):
        """Should ignore comments without Claude Code signature."""
        mock_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "createdAt": "2025-01-01T00:00:00Z",
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "comments": {
                                        "nodes": [
                                            {
                                                "body": "Issue #789 を作成しました",  # No signature
                                                "author": {"login": "someone"},
                                            }
                                        ]
                                    }
                                }
                            ]
                        },
                    }
                }
            }
        }
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(mock_response))
            result, _ = self.module._collect_issue_refs_from_review("123", "owner", "repo")
        assert result == {}


class TestIsBugIssue:
    """Tests for _is_bug_issue helper function (Issue #1152)."""

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_bug_label_returns_true(self):
        """Should return True when Issue has bug label."""
        assert self.module._is_bug_issue("Some title", ["bug"]) is True
        assert self.module._is_bug_issue("Some title", ["bugfix"]) is True
        assert self.module._is_bug_issue("Some title", ["バグ"]) is True

    def test_exclusion_label_returns_false(self):
        """Should return False when Issue has exclusion label."""
        assert self.module._is_bug_issue("fix: bug title", ["enhancement"]) is False
        assert self.module._is_bug_issue("bug: something", ["documentation"]) is False
        assert self.module._is_bug_issue("fix: issue", ["refactor"]) is False

    def test_exclusion_label_overrides_bug_title(self):
        """Exclusion label should take priority over bug keywords in title."""
        # Even with "fix:" in title, enhancement label excludes it
        assert self.module._is_bug_issue("fix: some enhancement", ["enhancement"]) is False

    def test_title_keywords_fallback(self):
        """Should detect bug by title keywords when no labels."""
        assert self.module._is_bug_issue("fix: broken function", []) is True
        assert self.module._is_bug_issue("bug: TypeError in handler", []) is True
        assert self.module._is_bug_issue("バグ修正", []) is True

    def test_non_bug_title_without_labels(self):
        """Should return False for non-bug titles without labels."""
        assert self.module._is_bug_issue("feat: new feature", []) is False
        assert self.module._is_bug_issue("docs: update readme", []) is False


class TestReferencesPr:
    """Tests for _references_pr helper function (Issue #1152)."""

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_pr_hash_format(self):
        """Should match 'PR #123' format."""
        assert self.module._references_pr("This is from PR #123", "123") is True
        assert self.module._references_pr("See PR #456 for details", "456") is True

    def test_pr_without_hash(self):
        """Should match 'PR 123' format without hash."""
        assert self.module._references_pr("This is from PR 123", "123") is True

    def test_pull_request_format(self):
        """Should match 'pull request 123' format."""
        assert self.module._references_pr("See pull request 123", "123") is True
        assert self.module._references_pr("From pull request #123", "123") is True

    def test_case_insensitive(self):
        """Should match case-insensitively."""
        assert self.module._references_pr("See PR #123", "123") is True
        assert self.module._references_pr("see pr #123", "123") is True
        assert self.module._references_pr("PULL REQUEST #123", "123") is True

    def test_word_boundary(self):
        """Should not match without word boundary."""
        # "somePR" should not match
        assert self.module._references_pr("somePR #123", "123") is False
        # Numbers embedded in other text should not match
        assert self.module._references_pr("PR #1234", "123") is False

    def test_no_match(self):
        """Should return False when PR is not referenced."""
        assert self.module._references_pr("This Issue is unrelated", "123") is False
        assert self.module._references_pr("See Issue #123", "123") is False
