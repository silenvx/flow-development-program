#!/usr/bin/env python3
"""merge-checkフックのレビューコメント・スレッド検証機能。

Why:
    PRマージ前にレビューコメントへの対応を検証することで、
    未解決のスレッドや不適切なdismissalを防ぐ。

What:
    - Dismissal検証（Issue参照必須）
    - 応答検証（Claude Code応答必須）
    - 未解決スレッド検出

Remarks:
    - fix_verification_checker.py: 修正主張の検証
    - ai_review_checker.py: AIレビュアーステータス確認
    - DISMISSAL_KEYWORDS/ACTION_KEYWORDS/DISMISSAL_EXCLUSIONSは意図的に冗長
      （substring matchingでも網羅性・可読性のため明示的に記載）

Changelog:
    - silenvx/dekita#432: ACTION_KEYWORDS追加（修正報告と却下を区別）
    - silenvx/dekita#662: DISMISSAL_EXCLUSIONS追加（技術用語の誤検知防止）
    - silenvx/dekita#1123: verified:/検証済み/確認済み追加
"""

import json
import subprocess

from check_utils import (
    ISSUE_REFERENCE_PATTERN,
    get_repo_owner_and_name,
    strip_code_blocks,
    truncate_body,
)
from lib.constants import TIMEOUT_HEAVY

# Dismissal keywords that indicate a review comment was skipped/deferred
#
# Design note: Redundant entries (e.g., "範囲外" and "今回は範囲外") are INTENTIONAL.
# Since we use substring matching (keyword.lower() in body_lower), "範囲外" already
# matches "今回は範囲外". However, explicit entries improve readability and make the
# full set of recognized phrases clear. This is NOT a bug - do not remove entries.
DISMISSAL_KEYWORDS = [
    "範囲外",
    "今回は範囲外",
    "軽微",
    "out of scope",
    "defer",
    "deferred",
    "後回し",
    "後で対応",
    "スコープ外",
    "対象外",
    "false positive",
    "誤検知",
]

# Action keywords that indicate a fix/response, not a dismissal
#
# Design note: When these keywords are present in a comment, the comment is
# reporting an action taken (e.g., "修正しました") rather than dismissing a review.
# Even if dismissal keywords like "誤検知" appear in such comments (e.g.,
# "誤検知リスクのため警告のみとする設計に修正しました"), they are used in a design
# explanation context, not as a reason to skip the review. See Issue #432.
ACTION_KEYWORDS = [
    "修正しました",
    "対応しました",
    "実装しました",
    "変更しました",
    "追加しました",
    "削除しました",
    "更新しました",
    "修正済み",  # Issue #1123: "Fixed:" style action indicator
    "verified:",  # Issue #1123: Verification action indicator (English)
    "検証済み",  # Issue #1123: Verification action indicator (Japanese)
    "確認済み",  # Issue #1123: Confirmation action indicator (Japanese)
]

# Exclusion patterns for technical terms that contain dismissal keywords
#
# Design note: These patterns prevent false positives where technical terms
# like "範囲外アクセス" (out-of-bounds access) are incorrectly matched by
# dismissal keywords like "範囲外" (out of scope). See Issue #662.
DISMISSAL_EXCLUSIONS = [
    "範囲外アクセス",
    "範囲外参照",
    "範囲外読み取り",
    "範囲外書き込み",
    "範囲外エラー",
    # Issue #1123: Technical terms describing what gets excluded from checking
    "チェック対象外",
    "対象外となります",
    "警告対象外",
]


def check_dismissal_without_issue(pr_number: str) -> list[dict]:
    """Check if any review threads have dismissals without Issue reference.

    Issue #1181: Changed from comment-level to thread-level checking.
    Now checks if ANY comment in a thread has an Issue reference, not just
    the dismissal comment itself. This allows adding Issue references as
    follow-up comments in the same thread.

    Issue #1202: Implements pagination to handle PRs with >50 threads.

    GraphQL Limitations (per thread, still applicable):
        - Previously: reviewThreads(first: 50) limited checking to the first 50
          threads only.
        - Now: All review threads are retrieved via pagination, so this
          thread-count limitation no longer applies.
        - comments(first: 30): Only the first 30 comments per thread are checked.

    Returns list of threads with dismissals that lack Issue references.
    Each item contains: path, line, body (snippet of dismissal comment)
    """
    try:
        repo_info = get_repo_owner_and_name()
        if not repo_info:
            return []

        owner, name = repo_info

        # GraphQL query with pagination support (Issue #1202)
        # - allComments: All comments in thread (up to 30) to check for Issue references
        # - pageInfo: For cursor-based pagination
        query = """
        query($owner: String!, $name: String!, $pr: Int!, $cursor: String) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $pr) {
              reviewThreads(first: 50, after: $cursor) {
                nodes {
                  id
                  path
                  line
                  allComments: comments(first: 30) {
                    nodes {
                      body
                    }
                  }
                }
                pageInfo {
                  hasNextPage
                  endCursor
                }
              }
            }
          }
        }
        """

        # Fetch all threads with pagination (Issue #1202)
        threads: list[dict] = []
        cursor: str | None = None
        max_pages = 10  # Safety limit: 500 threads max

        for _ in range(max_pages):
            cmd = [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"pr={pr_number}",
            ]
            if cursor:
                cmd.extend(["-F", f"cursor={cursor}"])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_HEAVY,
            )

            if result.returncode != 0 or not result.stdout.strip():
                break

            data = json.loads(result.stdout)
            review_threads = (
                data.get("data", {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("reviewThreads", {})
            )

            page_threads = review_threads.get("nodes", [])
            threads.extend(page_threads)

            page_info = review_threads.get("pageInfo", {})
            cursor = page_info.get("endCursor")
            # Issue #1325: ページネーション終了条件
            # hasNextPage が False または endCursor が無効（None/空文字列）の場合は終了
            if not page_info.get("hasNextPage") or not cursor:
                break

        threads_with_dismissal_without_issue = []

        for thread in threads:
            comments = thread.get("allComments", {}).get("nodes", [])
            if not comments:
                continue

            # Check ALL comments in thread for Issue references
            # If ANY comment has an Issue reference, the thread is OK
            thread_has_issue_ref = False
            # Issue #1231: Track dismissals/actions with their target reviewer comment
            # Each entry: (index, body, target_reviewer_idx)
            dismissal_entries: list[tuple[int, str, int | None]] = []
            # Each entry: (index, target_reviewer_idx)
            action_entries: list[tuple[int, int | None]] = []
            # Track non-Claude comments (potential review comments to respond to)
            reviewer_comment_indices: list[int] = []

            for i, comment in enumerate(comments):
                body = comment.get("body", "")

                # Strip code blocks to avoid false positives from keywords in code examples.
                # See Issue #797
                body_stripped = strip_code_blocks(body)

                # Check if any comment in thread has Issue reference
                # Use body_stripped to avoid matching patterns in code blocks (PR #1194)
                if ISSUE_REFERENCE_PATTERN.search(body_stripped):
                    thread_has_issue_ref = True
                body_lower = body_stripped.lower()

                # Check if this is a Claude Code comment (has signature at the end)
                # Design note: Case-sensitive check is INTENTIONAL. See Issue #1135.
                if not body_stripped.strip().endswith("-- Claude Code"):
                    # Issue #1231: Track non-Claude comments as potential targets
                    reviewer_comment_indices.append(i)
                    continue

                # Find the preceding reviewer comment (the target of this response)
                # Issue #1231: This allows matching dismissals with their corresponding actions
                target_reviewer_idx: int | None = None
                for idx in reversed(reviewer_comment_indices):
                    if idx < i:
                        target_reviewer_idx = idx
                        break

                # Check if comment contains action keywords (fix/response, not dismissal)
                # If present, this is a status report, not a dismissal. See Issue #432.
                has_action = any(keyword.lower() in body_lower for keyword in ACTION_KEYWORDS)
                if has_action:
                    action_entries.append((i, target_reviewer_idx))  # Issue #1231
                    continue

                # Check if comment contains exclusion patterns (technical terms)
                # See Issue #662.
                has_exclusion = any(
                    exclusion.lower() in body_lower for exclusion in DISMISSAL_EXCLUSIONS
                )
                if has_exclusion:
                    continue

                # Check if comment contains dismissal keywords
                has_dismissal = any(keyword.lower() in body_lower for keyword in DISMISSAL_KEYWORDS)
                if has_dismissal:
                    dismissal_entries.append((i, body, target_reviewer_idx))  # Issue #1231

            # Issue #1231: Filter out dismissals that were superseded by a later action
            # targeting the SAME reviewer comment.
            # Example: ReviewerA -> [対象外] -> 修正済み: filters out the dismissal
            # But: ReviewerA -> [対象外] -> ReviewerB -> 修正済み: keeps the dismissal
            # because the action targets ReviewerB, not ReviewerA
            filtered_dismissals: list[tuple[int, str]] = []
            for d_idx, d_body, d_target in dismissal_entries:
                # Check if there's a later action targeting the same reviewer comment
                superseded = False
                for a_idx, a_target in action_entries:
                    if a_idx > d_idx and a_target == d_target:
                        # This dismissal was superseded by a later action on same target
                        superseded = True
                        break
                if not superseded:
                    filtered_dismissals.append((d_idx, d_body))

            # Extract just the bodies for reporting
            dismissal_comments = [body for _, body in filtered_dismissals]

            # Only flag thread if it has dismissal(s) but NO Issue reference anywhere
            if dismissal_comments and not thread_has_issue_ref:
                threads_with_dismissal_without_issue.append(
                    {
                        "path": thread.get("path", "unknown"),
                        "line": thread.get("line"),
                        "body": truncate_body(dismissal_comments[0]),
                    }
                )

        return threads_with_dismissal_without_issue
    except Exception:
        # On error, don't block (fail open)
        return []


def check_resolved_without_response(pr_number: str) -> list[dict]:
    """Check if any review threads were resolved without Claude Code response.

    This catches the case where threads are resolved by clicking "Resolve" in the UI
    without Claude Code leaving a response comment.

    GraphQL Limitations (Issue #561):
        - reviewThreads(first: 50): Only the first 50 threads per PR are checked.
          PRs with >50 threads will have later threads unchecked.
        - comments(last: 20): Only the last 20 comments per thread are checked
          for Claude Code responses.

    Returns list of threads resolved without proper response.
    Each item contains: thread_id, author, body (snippet of original review comment)
    """
    try:
        repo_info = get_repo_owner_and_name()
        if not repo_info:
            return []

        owner, name = repo_info

        # GraphQL query - see docstring for limitations (Issue #561)
        # - firstComment: Original AI review comment for author identification
        # - recentComments: Last 20 comments to find Claude Code responses
        query = """
        query($owner: String!, $name: String!, $pr: Int!) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $pr) {
              reviewThreads(first: 50) {
                nodes {
                  id
                  isResolved
                  firstComment: comments(first: 1) {
                    nodes {
                      body
                      author { login }
                    }
                  }
                  recentComments: comments(last: 20) {
                    nodes {
                      body
                    }
                  }
                }
              }
            }
          }
        }
        """

        result = subprocess.run(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"pr={pr_number}",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )

        # Issue #1026: Check for empty stdout before JSON parsing
        # gh api may return 200 OK with empty body in edge cases
        if result.returncode != 0 or not result.stdout.strip():
            return []

        data = json.loads(result.stdout)
        threads = (
            data.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
            .get("nodes", [])
        )

        threads_without_response = []

        for thread in threads:
            if not thread.get("isResolved"):
                continue

            # Get the first comment (original AI review comment) for author identification
            first_comments = thread.get("firstComment", {}).get("nodes", [])
            if not first_comments:
                continue

            first_comment = first_comments[0]
            first_body = first_comment.get("body", "")
            author = first_comment.get("author", {}).get("login", "unknown")

            # Skip threads started by the user (not AI reviewer)
            # AI reviewers contain "copilot" or "codex" in their login
            author_lower = author.lower()
            if "copilot" not in author_lower and "codex" not in author_lower:
                continue

            # Check if any recent comment in this thread has Claude Code signature
            recent_comments = thread.get("recentComments", {}).get("nodes", [])
            has_claude_response = False
            for comment in recent_comments:
                body = comment.get("body", "")
                if "-- Claude Code" in body:
                    has_claude_response = True
                    break

            if not has_claude_response:
                threads_without_response.append(
                    {
                        "thread_id": thread.get("id", "unknown"),
                        "author": author,
                        "body": truncate_body(first_body),
                    }
                )

        return threads_without_response
    except Exception:
        # On error, don't block (fail open)
        return []


def check_unresolved_ai_threads(pr_number: str) -> list[dict]:
    """Check if any AI review threads are still unresolved.

    This catches the case where Copilot/Codex left review comments that
    haven't been addressed (neither resolved nor responded to).

    GraphQL Limitations (Issue #561):
        - reviewThreads(first: 50): Only the first 50 threads per PR are checked.
          PRs with >50 threads will have later threads unchecked.
        - comments(first: 1): Only the first comment is fetched per thread
          (sufficient for identifying the author).

    Returns list of unresolved threads from AI reviewers.
    Each item contains: thread_id, author, body (snippet), path, line
    """
    try:
        repo_info = get_repo_owner_and_name()
        if not repo_info:
            return []

        owner, name = repo_info

        # GraphQL query - see docstring for limitations (Issue #561)
        # Only the first comment is needed to identify the thread author
        query = """
        query($owner: String!, $name: String!, $pr: Int!) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $pr) {
              reviewThreads(first: 50) {
                nodes {
                  id
                  isResolved
                  comments(first: 1) {
                    nodes {
                      body
                      path
                      line
                      author { login }
                    }
                  }
                }
              }
            }
          }
        }
        """

        result = subprocess.run(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"pr={pr_number}",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )

        # Issue #1026: Check for empty stdout before JSON parsing
        # gh api may return 200 OK with empty body in edge cases
        if result.returncode != 0 or not result.stdout.strip():
            return []

        data = json.loads(result.stdout)
        threads = (
            data.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
            .get("nodes", [])
        )

        unresolved_ai_threads = []

        for thread in threads:
            # Only check unresolved threads
            if thread.get("isResolved"):
                continue

            comments = thread.get("comments", {}).get("nodes", [])
            if not comments:
                continue

            # Get the first comment (the original review comment)
            first_comment = comments[0]
            author = first_comment.get("author", {}).get("login", "unknown")

            # Only check threads started by AI reviewers (Copilot/Codex)
            author_lower = author.lower()
            if "copilot" not in author_lower and "codex" not in author_lower:
                continue

            unresolved_ai_threads.append(
                {
                    "thread_id": thread.get("id", "unknown"),
                    "author": author,
                    "path": first_comment.get("path", "unknown"),
                    "line": first_comment.get("line"),
                    "body": first_comment.get("body", "")[:100]
                    + ("..." if len(first_comment.get("body", "")) > 100 else ""),
                }
            )

        return unresolved_ai_threads
    except Exception:
        # On error, don't block (fail open)
        return []
