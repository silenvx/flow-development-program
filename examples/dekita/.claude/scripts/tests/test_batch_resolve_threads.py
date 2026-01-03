#!/usr/bin/env python3
"""
Tests for batch_resolve_threads.py
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from batch_resolve_threads import (
    get_repo_info,
    list_unresolved_threads,
    post_reply,
    resolve_thread,
)


class TestGetRepoInfo:
    """Tests for get_repo_info function."""

    def test_returns_owner_and_repo(self):
        """Returns owner and repo name on success."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"owner": {"login": "silenvx"}, "name": "dekita"})

        with patch("batch_resolve_threads.subprocess.run", return_value=mock_result):
            owner, repo = get_repo_info()
            assert owner == "silenvx"
            assert repo == "dekita"

    def test_returns_empty_on_failure(self):
        """Returns empty strings on failure."""
        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("batch_resolve_threads.subprocess.run", return_value=mock_result):
            owner, repo = get_repo_info()
            assert owner == ""
            assert repo == ""


class TestListUnresolvedThreads:
    """Tests for list_unresolved_threads function."""

    def test_returns_unresolved_threads(self):
        """Returns only unresolved threads."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [
                                    {"id": "PRRT_1", "isResolved": False},
                                    {"id": "PRRT_2", "isResolved": True},
                                    {"id": "PRRT_3", "isResolved": False},
                                ],
                            }
                        }
                    }
                }
            }
        )

        with patch("batch_resolve_threads.subprocess.run", return_value=mock_result):
            threads = list_unresolved_threads("123", "owner", "repo")
            assert len(threads) == 2
            assert threads[0]["id"] == "PRRT_1"
            assert threads[1]["id"] == "PRRT_3"

    def test_returns_empty_on_error(self):
        """Returns empty list on error."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error"

        with patch("batch_resolve_threads.subprocess.run", return_value=mock_result):
            threads = list_unresolved_threads("123", "owner", "repo")
            assert threads == []

    def test_paginates_through_multiple_pages(self):
        """Handles pagination for large PRs."""
        # First page
        first_page_result = MagicMock()
        first_page_result.returncode = 0
        first_page_result.stdout = json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": True, "endCursor": "cursor1"},
                                "nodes": [
                                    {"id": "PRRT_1", "isResolved": False},
                                ],
                            }
                        }
                    }
                }
            }
        )

        # Second page
        second_page_result = MagicMock()
        second_page_result.returncode = 0
        second_page_result.stdout = json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [
                                    {"id": "PRRT_2", "isResolved": False},
                                ],
                            }
                        }
                    }
                }
            }
        )

        with patch(
            "batch_resolve_threads.subprocess.run",
            side_effect=[first_page_result, second_page_result],
        ):
            threads = list_unresolved_threads("123", "owner", "repo")
            assert len(threads) == 2
            assert threads[0]["id"] == "PRRT_1"
            assert threads[1]["id"] == "PRRT_2"


class TestPostReply:
    """Tests for post_reply function."""

    def test_adds_signature_if_missing(self):
        """Adds Claude Code signature if not present."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("batch_resolve_threads.subprocess.run", return_value=mock_result) as mock_run:
            post_reply("123", 456, "Test message", "owner", "repo")

            # Check that signature was added
            call_args = mock_run.call_args[0][0]
            body_arg = [a for a in call_args if a.startswith("body=")][0]
            assert "-- Claude Code" in body_arg

    def test_does_not_duplicate_signature(self):
        """Does not add duplicate signature."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("batch_resolve_threads.subprocess.run", return_value=mock_result) as mock_run:
            post_reply("123", 456, "Test message\n\n-- Claude Code", "owner", "repo")

            call_args = mock_run.call_args[0][0]
            body_arg = [a for a in call_args if a.startswith("body=")][0]
            # Should only have one signature
            assert body_arg.count("-- Claude Code") == 1

    def test_returns_true_on_success(self):
        """Returns True on success."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("batch_resolve_threads.subprocess.run", return_value=mock_result):
            result = post_reply("123", 456, "Test", "owner", "repo")
            assert result is True

    def test_returns_false_on_failure(self):
        """Returns False on failure."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error"

        with patch("batch_resolve_threads.subprocess.run", return_value=mock_result):
            result = post_reply("123", 456, "Test", "owner", "repo")
            assert result is False


class TestResolveThread:
    """Tests for resolve_thread function."""

    def test_returns_true_on_success(self):
        """Returns True when thread is resolved."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {"data": {"resolveReviewThread": {"thread": {"isResolved": True}}}}
        )

        with patch("batch_resolve_threads.subprocess.run", return_value=mock_result):
            result = resolve_thread("PRRT_xxx")
            assert result is True

    def test_returns_false_on_failure(self):
        """Returns False on failure."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error"

        with patch("batch_resolve_threads.subprocess.run", return_value=mock_result):
            result = resolve_thread("PRRT_xxx")
            assert result is False
