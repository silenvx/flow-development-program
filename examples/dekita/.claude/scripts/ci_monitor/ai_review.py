"""AI reviewer utilities for ci-monitor.

This module handles AI reviewer detection and review management.
Extracted from ci-monitor.py as part of Issue #1754 refactoring.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ci_monitor.constants import (
    AI_REVIEWER_IDENTIFIERS,
    COPILOT_CODEX_IDENTIFIERS,
    COPILOT_REVIEWER_LOGIN,
    GEMINI_REVIEWER_LOGIN,
)
from ci_monitor.github_api import run_gh_command
from ci_monitor.models import CodexReviewRequest

if TYPE_CHECKING:
    pass


def is_ai_reviewer(author: str) -> bool:
    """Check if the given author is an AI reviewer.

    Issue #1109: Centralized utility function for checking if a comment/review
    author is an AI reviewer. This ensures consistent behavior across the codebase
    and prevents accidentally filtering human comments.

    Args:
        author: The username/author string to check (e.g., "copilot-pull-request-reviewer")

    Returns:
        True if the author is an AI reviewer, False otherwise.

    Example:
        >>> is_ai_reviewer("copilot-pull-request-reviewer")
        True
        >>> is_ai_reviewer("john-doe")
        False
        >>> is_ai_reviewer("chatgpt-codex-connector")
        True
    """
    if not author:
        return False
    author_lower = author.lower()
    return any(ai in author_lower for ai in AI_REVIEWER_IDENTIFIERS)


def has_copilot_or_codex_reviewer(reviewers: list[str]) -> bool:
    """Check if Copilot or Codex is in the pending reviewers.

    Issue #2711: Uses COPILOT_CODEX_IDENTIFIERS instead of AI_REVIEWER_IDENTIFIERS
    to avoid matching Gemini reviewers. Gemini has separate handling via
    is_gemini_review_pending() with rate limit detection.
    """
    for reviewer in reviewers:
        reviewer_lower = reviewer.lower()
        if any(ai in reviewer_lower for ai in COPILOT_CODEX_IDENTIFIERS):
            return True
    return False


def _check_eyes_reaction(comment_id: int) -> bool:
    """Check if a comment has the 👀 (eyes) reaction from Codex."""
    success, output = run_gh_command(
        [
            "api",
            f"/repos/{{owner}}/{{repo}}/issues/comments/{comment_id}/reactions",
            "--jq",
            '[.[] | select(.content == "eyes")]',
        ]
    )

    if not success:
        return False

    try:
        reactions = json.loads(output)
        return bool(reactions)
    except json.JSONDecodeError:
        return False


def get_codex_review_requests(pr_number: str) -> list[CodexReviewRequest]:
    """Find @codex review comments in the PR.

    Returns a list of CodexReviewRequest objects with comment ID, creation time,
    and whether the 👀 reaction (indicating Codex started) exists.
    """
    success, output = run_gh_command(
        [
            "api",
            f"/repos/{{owner}}/{{repo}}/issues/{pr_number}/comments",
            "--jq",
            '[.[] | select(.body | test("@codex\\\\s+review"; "i")) | {id, created_at, body}]',
        ]
    )

    if not success:
        return []

    try:
        comments = json.loads(output)
    except json.JSONDecodeError:
        return []

    requests = []
    for comment in comments:
        comment_id = comment.get("id")
        if not comment_id:
            continue

        has_eyes = _check_eyes_reaction(comment_id)

        requests.append(
            CodexReviewRequest(
                comment_id=comment_id,
                created_at=comment.get("created_at", ""),
                has_eyes_reaction=has_eyes,
            )
        )

    return requests


def get_codex_reviews(pr_number: str) -> list[dict[str, Any]]:
    """Get reviews posted by Codex bot on the PR.

    Returns a list of reviews from chatgpt-codex-connector[bot].
    """
    success, output = run_gh_command(
        [
            "api",
            f"/repos/{{owner}}/{{repo}}/pulls/{pr_number}/reviews",
            "--jq",
            '[.[] | select(.user.login | test("codex"; "i")) '
            "| {id, user: .user.login, submitted_at, state, body}]",
        ]
    )

    if not success:
        return []

    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return []


def get_copilot_reviews(pr_number: str) -> list[dict[str, Any]]:
    """Get reviews posted by Copilot on the PR.

    Returns a list of reviews from copilot-pull-request-reviewer[bot] or similar.
    """
    success, output = run_gh_command(
        [
            "api",
            f"/repos/{{owner}}/{{repo}}/pulls/{pr_number}/reviews",
            "--jq",
            # Match official Copilot bot accounts only (e.g., copilot-pull-request-reviewer[bot])
            '[.[] | select(.user.login | test("^copilot.*\\\\[bot\\\\]$"; "i")) '
            "| {id, user: .user.login, submitted_at, state, body}]",
        ]
    )

    if not success:
        return []

    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return []


# Gemini Code Assist functions (Issue #2711)


def get_gemini_reviews(pr_number: str) -> list[dict[str, Any]]:
    """Get reviews posted by Gemini Code Assist on the PR.

    Issue #2711: Added to support Gemini review waiting.

    Returns a list of reviews from gemini-code-assist[bot].
    """
    success, output = run_gh_command(
        [
            "api",
            f"/repos/{{owner}}/{{repo}}/pulls/{pr_number}/reviews",
            "--jq",
            # Match official Gemini bot account only
            f'[.[] | select(.user.login == "{GEMINI_REVIEWER_LOGIN}") '
            "| {id, user: .user.login, submitted_at, state, body}]",
        ]
    )

    if not success:
        return []

    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return []


def has_gemini_reviewer(reviewers: list[str]) -> bool:
    """Check if Gemini is in the pending reviewers.

    Issue #2711: Check for gemini-code-assist[bot] in requested reviewers.
    Uses exact matching to avoid false positives.

    Args:
        reviewers: List of pending reviewer login names.

    Returns:
        True if Gemini is in the list.
    """
    return GEMINI_REVIEWER_LOGIN in reviewers


# Gemini rate limit patterns (Issue #2711)
GEMINI_RATE_LIMIT_PATTERNS = [
    "rate limit",
    "rate-limit",
    "quota exceeded",
    "too many requests",
]


def is_gemini_rate_limited(pr_number: str) -> tuple[bool, str | None]:
    """Check if Gemini has hit rate limits based on review comments.

    Issue #2711: Detect rate limiting from Gemini's review body.

    Returns:
        Tuple of (is_rate_limited, message). If rate limited, returns
        (True, rate_limit_message). Otherwise returns (False, None).
    """
    reviews = get_gemini_reviews(pr_number)

    if not reviews:
        return False, None

    # Sort by submitted_at descending to get the most recent review first
    sorted_reviews = sorted(
        reviews,
        key=lambda r: r.get("submitted_at", ""),
        reverse=True,
    )

    # Only check the most recent review
    latest_review = sorted_reviews[0]
    body = latest_review.get("body", "").lower()

    for pattern in GEMINI_RATE_LIMIT_PATTERNS:
        if pattern in body:
            return True, latest_review.get("body", "")

    return False, None


def is_gemini_review_pending(pr_number: str, pending_reviewers: list[str]) -> bool:
    """Check if Gemini review is pending and not rate limited.

    Issue #2711: A Gemini review is pending if:
    1. gemini-code-assist[bot] is in requested_reviewers, AND
    2. Gemini has not posted a rate limit error (if rate limited, skip waiting)

    Note: Unlike Copilot which checks for "no review posted", Gemini only checks
    for rate limiting because Gemini may post reviews while still in pending_reviewers.

    Args:
        pr_number: PR number to check.
        pending_reviewers: List of pending reviewers from GitHub API.

    Returns:
        True if Gemini review is pending and should be waited for.
    """
    # Check if Gemini is in pending reviewers
    if not has_gemini_reviewer(pending_reviewers):
        return False

    # Check if rate limited (don't wait if rate limited)
    is_limited, _ = is_gemini_rate_limited(pr_number)
    if is_limited:
        return False

    return True


# Error patterns that indicate Copilot review failure
COPILOT_ERROR_PATTERNS = [
    "encountered an error",
    "unable to review",
    "could not complete",
    "failed to review",
    "error occurred",
]


def is_copilot_review_error(pr_number: str) -> tuple[bool, str | None]:
    """Check if the most recent Copilot review ended with an error.

    Copilot may fail to review a PR and post an error message like:
    "Copilot encountered an error and was unable to review this pull request."

    Only checks the most recent Copilot review to avoid false positives
    when an old error review exists but a newer successful review was posted.

    Returns:
        Tuple of (is_error, error_message). If the most recent review is an error,
        returns (True, error_message). Otherwise returns (False, None).
    """
    reviews = get_copilot_reviews(pr_number)

    if not reviews:
        return False, None

    # Sort by submitted_at descending to get the most recent review first
    sorted_reviews = sorted(
        reviews,
        key=lambda r: r.get("submitted_at", ""),
        reverse=True,
    )

    # Only check the most recent review
    latest_review = sorted_reviews[0]
    body = latest_review.get("body", "").lower()

    for pattern in COPILOT_ERROR_PATTERNS:
        if pattern in body:
            return True, latest_review.get("body", "")

    return False, None


def request_copilot_review(pr_number: str) -> tuple[bool, str]:
    """Request Copilot to review the PR via GitHub API.

    This can be used to re-request a review after Copilot encountered an error.
    Issue #1305: Added for automatic retry on Copilot review errors.
    Issue #1394: Returns error details for better diagnostics.

    Args:
        pr_number: The PR number to request review for.

    Returns:
        Tuple of (success, message). On failure, message contains the error details.
    """
    import subprocess

    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/:owner/:repo/pulls/{pr_number}/requested_reviewers",
                "-X",
                "POST",
                "--input",
                "-",
            ],
            input=json.dumps({"reviewers": [COPILOT_REVIEWER_LOGIN]}),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr_msg = result.stderr.strip() if result.stderr else "No stderr output"
            return False, stderr_msg
        return True, ""
    except Exception as e:
        return False, str(e)


def check_and_report_contradictions(
    comments: list[dict[str, Any]],
    previous_comments: list[dict[str, Any]] | None,
    json_mode: bool,
    *,
    detect_fn: Any | None = None,
    format_fn: Any | None = None,
) -> None:
    """Check for contradictions in AI review comments and print warnings.

    Issue #1399: Detect potential contradictions between review comments.
    Issue #1596: Also detect within first batch when previous_comments is empty.
    Issue #1597: Extracted from main loop to reduce nesting.

    Only compares AI reviewer comments (Copilot/Codex) to avoid false positives.

    Args:
        comments: Current batch of review comments.
        previous_comments: Previous batch of review comments (may be None/empty).
        json_mode: If True, skip printing (JSON output mode).
        detect_fn: Function to detect contradictions (for dependency injection).
        format_fn: Function to format contradiction warnings (for dependency injection).
    """
    if not comments or json_mode:
        return

    ai_new = [c for c in comments if is_ai_reviewer(c.get("user", ""))]
    if not ai_new:
        return

    ai_prev: list[dict[str, Any]] = []
    new_ai_comments = ai_new

    # If we have previous comments, filter out already-seen ones
    if previous_comments:
        ai_prev = [c for c in previous_comments if is_ai_reviewer(c.get("user", ""))]
        if ai_prev:
            prev_ids = {c.get("id") for c in ai_prev if c.get("id") is not None}
            new_ai_comments = [
                c for c in ai_new if c.get("id") is not None and c.get("id") not in prev_ids
            ]

    if new_ai_comments and detect_fn and format_fn:
        contradictions = detect_fn(new_ai_comments, ai_prev)
        if contradictions:
            print(format_fn(contradictions))
