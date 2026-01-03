"""Review comment operations for ci-monitor.

This module handles review comment fetching, classification, and thread management.
Extracted from ci-monitor.py as part of Issue #1754 refactoring.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import TYPE_CHECKING, Any

from ci_monitor.ai_review import is_ai_reviewer
from ci_monitor.constants import CODE_BLOCK_PATTERN, GITHUB_FILES_LIMIT
from ci_monitor.github_api import (
    get_repo_info,
    is_rate_limit_error,
    run_gh_command,
    run_gh_command_with_error,
)
from ci_monitor.models import ClassifiedComments
from ci_monitor.rate_limit import print_rate_limit_warning, should_prefer_rest_api

if TYPE_CHECKING:
    from collections.abc import Callable


def strip_code_blocks(text: str) -> str:
    """Remove code blocks and inline code from text.

    This prevents false positives when checking for checkboxes
    that may appear in code examples or suggestions.

    Args:
        text: The text to process.

    Returns:
        Text with code blocks and inline code removed.
    """
    return CODE_BLOCK_PATTERN.sub("", text)


def get_review_comments(pr_number: str) -> list[dict[str, Any]]:
    """Fetch inline code review comments from the PR."""
    success, output = run_gh_command(
        [
            "api",
            f"/repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments",
            "--jq",
            "[.[] | {path, line, body, user: .user.login, id}]",
        ]
    )

    if not success:
        return []

    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return []


def get_pr_changed_files(pr_number: str) -> set[str] | None:
    """Get the list of files changed in the PR.

    Returns:
        A set of file paths that were changed in the PR, or None if the lookup failed.
        None indicates an error (auth/network/etc) vs. empty set which means no files changed.

    Note:
        The GitHub API (via `gh pr view --json files`) returns at most 100 files due to
        pagination limitations. If exactly 100 files are returned, we treat this as
        potentially incomplete and return None to ensure all comments are treated as in-scope.
        See: https://github.com/silenvx/dekita/issues/324
    """
    success, output = run_gh_command(
        ["pr", "view", pr_number, "--json", "files", "--jq", "[.files[].path] | .[]"]
    )

    if not success:
        return None

    if not output.strip():
        return set()

    files = set(output.strip().split("\n"))

    # If we hit the API limit, assume there might be more files we couldn't retrieve
    if len(files) >= GITHUB_FILES_LIMIT:
        return None

    return files


def classify_review_comments(
    pr_number: str, comments: list[dict[str, Any]] | None = None
) -> ClassifiedComments:
    """Classify review comments as in-scope or out-of-scope for the PR.

    A comment is considered in-scope if it's on a file that was changed in the PR.

    In-scope comments should be addressed directly in the PR.
    Out-of-scope comments may warrant a separate Issue for follow-up.

    If the changed files lookup fails, all comments are treated as in-scope
    to avoid incorrectly suggesting users ignore important feedback.

    Args:
        pr_number: PR number to analyze
        comments: Optional pre-fetched comments. If None, will fetch.

    Returns:
        ClassifiedComments with in_scope and out_of_scope lists.
    """
    if comments is None:
        comments = get_review_comments(pr_number)

    if not comments:
        return ClassifiedComments(in_scope=[], out_of_scope=[])

    changed_files = get_pr_changed_files(pr_number)

    # If file lookup failed, treat all comments as in-scope (safe default)
    if changed_files is None:
        return ClassifiedComments(in_scope=comments, out_of_scope=[])

    in_scope = []
    out_of_scope = []

    for comment in comments:
        comment_path = comment.get("path", "")
        if comment_path in changed_files:
            in_scope.append(comment)
        else:
            out_of_scope.append(comment)

    return ClassifiedComments(in_scope=in_scope, out_of_scope=out_of_scope)


def print_comment(comment: dict[str, Any]) -> None:
    """Print a single comment with path, line, user, and truncated body."""
    print(f"  [{comment.get('path')}:{comment.get('line')}] ({comment.get('user')})")
    body = comment.get("body", "")
    if len(body) > 100:
        print(f"    {body[:100]}...")
    else:
        print(f"    {body}")


def fetch_review_comments_rest(
    owner: str, name: str, pr_number: str
) -> list[dict[str, Any]] | None:
    """Fetch review comments via REST API (fallback for GraphQL rate limit).

    Issue #1318: Fallback when GraphQL API is rate limited.

    REST API doesn't provide thread structure or isResolved status,
    so this is a degraded fallback that returns comments without thread grouping.

    Args:
        owner: Repository owner
        name: Repository name
        pr_number: PR number

    Returns:
        List of comments in simplified format, or None on failure.
    """
    success, output = run_gh_command(
        [
            "api",
            f"/repos/{owner}/{name}/pulls/{pr_number}/comments",
            "--paginate",
            "--jq",
            "[.[] | {id: .id, path: .path, line: (.line // .original_line), "
            "body: .body, author: .user.login}]",
        ],
        timeout=60,
    )

    if not success:
        return None

    try:
        stripped_output = output.strip()
        if not stripped_output:
            return []

        comments: list[dict[str, Any]] = []
        for line in stripped_output.split("\n"):
            if line.strip():
                page_comments = json.loads(line)
                comments.extend(page_comments)

        # Mark as REST fallback so callers know thread info is unavailable
        for comment in comments:
            comment["is_rest_fallback"] = True
            comment["isResolved"] = False

        return comments
    except (json.JSONDecodeError, TypeError):
        return None


def convert_rest_comments_to_thread_format(
    comments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert REST API comments to GraphQL thread-like format.

    Issue #1318: Adapter for REST API fallback.

    Since REST API doesn't have thread structure, each comment becomes
    a pseudo-thread. This allows existing code to work with fallback data.

    Args:
        comments: Comments from fetch_review_comments_rest()

    Returns:
        List of pseudo-threads with GraphQL-compatible structure.
    """
    threads = []
    for comment in comments:
        thread = {
            "id": f"rest-{comment.get('id', 'unknown')}",
            "isResolved": comment.get("isResolved", False),
            "is_rest_fallback": True,
            "comments": {
                "nodes": [
                    {
                        "body": comment.get("body", ""),
                        "path": comment.get("path", ""),
                        "line": comment.get("line"),
                        "author": {"login": comment.get("author", "unknown")},
                    }
                ]
            },
        }
        threads.append(thread)
    return threads


def fetch_all_review_threads(
    owner: str,
    name: str,
    pr_number: str,
    fields: str,
    *,
    require_resolved_status: bool = True,
) -> list[dict[str, Any]] | None:
    """Fetch all review threads using cursor-based pagination.

    Issue #860: GraphQL API limits results to 100 per request.
    This function handles pagination to fetch all threads.

    Issue #1195: Returns None on API failure to distinguish from empty list.

    Issue #1360: Proactively uses REST API when approaching rate limit.
    Note: REST API does not provide isResolved status, so callers that need
    accurate resolution status should set require_resolved_status=True (default).

    Args:
        owner: Repository owner
        name: Repository name
        pr_number: PR number
        fields: GraphQL fields to include in each thread node
        require_resolved_status: If True (default), skip REST priority mode
            because REST API cannot provide isResolved status. Set to False
            for operations that don't need resolution status.

    Returns:
        List of all review thread nodes, or None if API call failed.
    """
    # Issue #1360: Proactively use REST API when approaching rate limit
    if not require_resolved_status and should_prefer_rest_api():
        rest_comments = fetch_review_comments_rest(owner, name, pr_number)
        if rest_comments is not None:
            print(
                f"  ✓ REST優先モード: {len(rest_comments)}件のコメントを取得",
                file=sys.stderr,
            )
            return convert_rest_comments_to_thread_format(rest_comments)
        print("  ⚠️ REST優先モード失敗、GraphQLを試行", file=sys.stderr)

    all_threads: list[dict[str, Any]] = []
    cursor: str | None = None
    max_pages = 10
    api_failed = False

    for _ in range(max_pages):
        cursor_arg = f', after: "{cursor}"' if cursor else ""
        query = f"""
        query($owner: String!, $name: String!, $pr: Int!) {{
          repository(owner: $owner, name: $name) {{
            pullRequest(number: $pr) {{
              reviewThreads(first: 100{cursor_arg}) {{
                pageInfo {{
                  hasNextPage
                  endCursor
                }}
                nodes {{
                  {fields}
                }}
              }}
            }}
          }}
        }}
        """

        success, output, stderr = run_gh_command_with_error(
            [
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
            timeout=30,
        )

        if not success:
            if is_rate_limit_error(output, stderr):
                print_rate_limit_warning()
                print("  → REST APIへフォールバック中...", file=sys.stderr)
                rest_comments = fetch_review_comments_rest(owner, name, pr_number)
                if rest_comments is not None:
                    print(
                        f"  ✓ REST APIで{len(rest_comments)}件のコメントを取得",
                        file=sys.stderr,
                    )
                    print(
                        "  ⚠️ 注意: スレッドの解決状態は取得できません",
                        file=sys.stderr,
                    )
                    return convert_rest_comments_to_thread_format(rest_comments)
                print("  ⚠️ REST APIフォールバックも失敗しました", file=sys.stderr)
            api_failed = True
            break

        try:
            data = json.loads(output)
            review_threads = (
                data.get("data", {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("reviewThreads", {})
            )

            nodes = review_threads.get("nodes", [])
            all_threads.extend(nodes)

            page_info = review_threads.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break

            cursor = page_info.get("endCursor")
            if not cursor:
                break

        except (json.JSONDecodeError, KeyError):
            api_failed = True
            break

    if api_failed:
        return None
    return all_threads


def get_unresolved_threads(pr_number: str) -> list[dict[str, Any]] | None:
    """Fetch unresolved review threads from the PR using GraphQL API.

    Returns a list of unresolved threads with their first comment's path and body.

    Issue #860: Uses pagination to fetch all threads.
    Issue #862: Uses get_repo_info() for repository info retrieval.
    Issue #1195: Returns None on API failure.
    """
    repo_info = get_repo_info()
    if not repo_info:
        return None
    owner, name = repo_info

    fields = """
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
    """

    threads = fetch_all_review_threads(owner, name, pr_number, fields)

    if threads is None:
        return None

    unresolved = []
    for thread in threads:
        if not thread.get("isResolved"):
            comments = thread.get("comments", {}).get("nodes", [])
            if comments:
                first = comments[0]
                item = {
                    "id": thread.get("id", ""),
                    "path": first.get("path", ""),
                    "line": first.get("line"),
                    "body": first.get("body", "")[:100],
                    "author": first.get("author", {}).get("login", "unknown"),
                }
                if thread.get("is_rest_fallback"):
                    item["is_rest_fallback"] = True
                unresolved.append(item)
    return unresolved


def get_unresolved_ai_threads(pr_number: str) -> list[dict[str, Any]] | None:
    """Get unresolved threads from AI reviewers (Copilot/Codex).

    Issue #989: Filters unresolved threads to only include those from AI reviewers.
    Issue #1195: Returns None on API failure.

    Args:
        pr_number: The PR number.

    Returns:
        List of unresolved threads from AI reviewers, or None on API failure.
    """
    threads = get_unresolved_threads(pr_number)
    if threads is None:
        return None
    ai_threads = []
    for thread in threads:
        author = thread.get("author", "")
        if is_ai_reviewer(author):
            ai_threads.append(thread)
    return ai_threads


def normalize_comment_body(body: str) -> str:
    """Normalize comment body for duplicate detection.

    Issue #1372: After rebase, AI reviewers may post the same comment with
    different line numbers. This function normalizes the body to improve
    duplicate detection by removing volatile content like line numbers.

    Args:
        body: The original comment body.

    Returns:
        Normalized body text for hashing.
    """
    normalized = re.sub(r"\b(?:on\s+)?lines?\s*\d+(?:\s*-\s*\d+)?", "", body, flags=re.IGNORECASE)
    normalized = re.sub(r"\(L\d+(?:-L?\d+)?\)", "", normalized)
    normalized = re.sub(r"\bL\d+(?:-L?\d+)?\b", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized


def get_resolved_thread_hashes(pr_number: str) -> set[str]:
    """Get body hashes of resolved threads before rebase.

    This is used to detect duplicate comments after rebase.

    Returns a set of SHA-256 hashes (first 32 chars) of resolved thread bodies.

    Issue #860: Uses pagination to fetch all threads.
    Issue #862: Uses get_repo_info() for repository info retrieval.
    """
    repo_info = get_repo_info()
    if not repo_info:
        return set()
    owner, name = repo_info

    fields = """
        isResolved
        comments(first: 1) {
            nodes {
                body
                path
            }
        }
    """

    threads = fetch_all_review_threads(owner, name, pr_number, fields)

    if threads is None:
        return set()

    hashes = set()
    for thread in threads:
        if thread.get("isResolved"):
            comments = thread.get("comments", {}).get("nodes", [])
            if comments:
                first = comments[0]
                body = first.get("body", "")
                path = first.get("path", "")
                if not body or not path:
                    continue
                normalized_body = normalize_comment_body(body)
                if not normalized_body:
                    continue
                content = f"{path}:{normalized_body}"
                content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
                hashes.add(content_hash)
    return hashes


def resolve_thread_by_id(thread_id: str) -> bool:
    """Resolve a review thread by its GraphQL ID.

    Uses the resolveReviewThread mutation.

    Issue #1096: Detects rate limit errors and shows helpful message.
    """
    mutation = """
    mutation($threadId: ID!) {
      resolveReviewThread(input: {threadId: $threadId}) {
        thread {
          isResolved
        }
      }
    }
    """

    success, output, stderr = run_gh_command_with_error(
        [
            "api",
            "graphql",
            "-f",
            f"query={mutation}",
            "-F",
            f"threadId={thread_id}",
        ],
        timeout=30,
    )

    if not success and is_rate_limit_error(output, stderr):
        print_rate_limit_warning()
        print(
            "  ⚠️ スレッド解決はREST APIでは対応できません。",
            file=sys.stderr,
        )
        print(
            "  → GitHub Web UIで手動でResolve conversationしてください。",
            file=sys.stderr,
        )
        print(
            f"  → スレッドID: {thread_id}",
            file=sys.stderr,
        )

    return success


def auto_resolve_duplicate_threads(
    pr_number: str,
    pre_rebase_hashes: set[str],
    json_mode: bool = False,
    *,
    log_fn: Callable[[str, bool, dict[str, Any] | None], None] | None = None,
) -> tuple[int, set[str]]:
    """Auto-resolve threads that match pre-rebase resolved thread hashes.

    After rebase, AI reviewers may re-post the same comments. This function
    detects and auto-resolves threads that have the same body+path as
    threads that were already resolved before rebase.

    Args:
        pr_number: The PR number.
        pre_rebase_hashes: Set of body hashes from resolved threads before rebase.
        json_mode: Whether to log in JSON format.
        log_fn: Optional logging function. Signature: (message, json_mode, data) -> None.

    Returns:
        Tuple of (number of threads auto-resolved, set of resolved hashes).
        Issue #1097: Now returns resolved hashes for comment filtering.

    Issue #860: Uses pagination to fetch all threads.
    Issue #862: Uses get_repo_info() for repository info retrieval.
    """
    if not pre_rebase_hashes:
        return 0, set()

    repo_info = get_repo_info()
    if not repo_info:
        return 0, set()
    owner, name = repo_info

    fields = """
        id
        isResolved
        comments(first: 1) {
            nodes {
                body
                path
                author { login }
            }
        }
    """

    threads = fetch_all_review_threads(owner, name, pr_number, fields)

    if threads is None:
        return 0, set()

    resolved_count = 0
    resolved_hashes: set[str] = set()
    for thread in threads:
        if thread.get("isResolved"):
            continue

        comments = thread.get("comments", {}).get("nodes", [])
        if not comments:
            continue

        first = comments[0]
        body = first.get("body", "")
        path = first.get("path", "")
        author = first.get("author", {}).get("login", "")

        if not is_ai_reviewer(author):
            continue

        if not body or not path:
            continue

        normalized_body = normalize_comment_body(body)
        if not normalized_body:
            continue
        content = f"{path}:{normalized_body}"
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]

        if content_hash in pre_rebase_hashes:
            thread_id = thread.get("id", "")
            if thread_id and resolve_thread_by_id(thread_id):
                resolved_count += 1
                resolved_hashes.add(content_hash)
                if log_fn:
                    log_fn(
                        f"Auto-resolved duplicate thread: {path}",
                        json_mode,
                        {"path": path, "hash": content_hash} if json_mode else None,
                    )

    return resolved_count, resolved_hashes


def filter_duplicate_comments(
    comments: list[dict[str, Any]], duplicate_hashes: set[str]
) -> list[dict[str, Any]]:
    """Filter out AI reviewer comments that match duplicate thread hashes.

    Issue #1097: Remove comments from auto-resolved duplicate threads
    to prevent them from being displayed as needing attention.

    Only filters AI reviewer comments (Copilot/Codex) to ensure human
    reviewer comments are never accidentally hidden.

    Args:
        comments: List of review comments from get_review_comments().
        duplicate_hashes: Set of content hashes from auto-resolved threads.

    Returns:
        Filtered list of comments.
    """
    if not duplicate_hashes:
        return comments

    filtered = []
    for comment in comments:
        path = comment.get("path", "")
        body = comment.get("body", "")
        user = comment.get("user", "")

        if not is_ai_reviewer(user):
            filtered.append(comment)
            continue

        if not path or not body:
            filtered.append(comment)
            continue

        normalized_body = normalize_comment_body(body)
        if not normalized_body:
            filtered.append(comment)
            continue
        content = f"{path}:{normalized_body}"
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]

        if content_hash not in duplicate_hashes:
            filtered.append(comment)

    return filtered


def log_review_comments_to_quality_log(
    pr_number: str,
    comments: list[dict[str, Any]],
    *,
    log_comment_fn: Any | None = None,
    identify_reviewer_fn: Any | None = None,
    estimate_category_fn: Any | None = None,
) -> None:
    """Log review comments to the review quality log for tracking.

    Records each AI review comment (Copilot/Codex Cloud) to the review quality
    metrics log for later analysis of review quality and acceptance rates.

    Args:
        pr_number: PR number
        comments: List of review comments from get_review_comments()
        log_comment_fn: Function to log comments (for dependency injection).
        identify_reviewer_fn: Function to identify reviewer (for dependency injection).
        estimate_category_fn: Function to estimate category (for dependency injection).
    """
    if not comments or not log_comment_fn:
        return

    for comment in comments:
        user = comment.get("user", "")

        if not is_ai_reviewer(user):
            continue

        reviewer = identify_reviewer_fn(user) if identify_reviewer_fn else "unknown"
        body = comment.get("body", "")
        category = estimate_category_fn(body) if estimate_category_fn else "general"

        log_comment_fn(
            pr_number=pr_number,
            comment_id=comment.get("id", ""),
            reviewer=reviewer,
            category=category,
            file_path=comment.get("path"),
            line_number=comment.get("line"),
            body_preview=body[:200] if body else None,
        )
