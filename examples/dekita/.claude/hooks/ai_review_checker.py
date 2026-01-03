#!/usr/bin/env python3
"""merge-check用のAIレビュー状態確認ユーティリティ。

Why:
    AIレビュー（Copilot/Codex）がレビュー中やエラー状態のままマージすると、
    レビューなしでコードがマージされる。マージ前にAIレビュー状態を確認する。

What:
    - AIレビュアーがレビュー中かを確認
    - AIレビューエラーの検出
    - Copilotレビューの再リクエスト
    - 連続エラー時の警告付きマージ許可

Remarks:
    - フックではなくユーティリティモジュール
    - merge-check.pyから呼び出される
    - 連続エラー時は警告付きでマージ許可（緩和処理）

Changelog:
    - silenvx/dekita#630: 連続エラー時の緩和処理
    - silenvx/dekita#646: エラーログ出力の改善
"""

import json
import subprocess
import sys

from check_utils import truncate_body
from lib.constants import TIMEOUT_HEAVY, TIMEOUT_MEDIUM


def check_ai_reviewing(pr_number: str) -> list[str]:
    """Check if Copilot/Codex is currently reviewing the PR.

    Returns list of AI reviewers found in requested_reviewers.
    """
    try:
        # Use relative path - gh api automatically resolves :owner/:repo
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/:owner/:repo/pulls/{pr_number}",
                "--jq",
                ".requested_reviewers[].login",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            # On subprocess error, fail open
            return []
        reviewers = result.stdout.strip().split("\n") if result.stdout.strip() else []
        # Check for Copilot or Codex (case-insensitive contains)
        ai_reviewers = []
        for reviewer in reviewers:
            reviewer_lower = reviewer.lower()
            if "copilot" in reviewer_lower:
                ai_reviewers.append(reviewer)
            elif "codex" in reviewer_lower:
                ai_reviewers.append(reviewer)
        return ai_reviewers
    except Exception:
        # On error, don't block (fail open)
        return []


# Error message pattern that indicates Copilot failed to review
AI_REVIEW_ERROR_PATTERN = "encountered an error"

# Copilot reviewer login name for API requests
COPILOT_REVIEWER_LOGIN = "copilot-pull-request-reviewer[bot]"


def request_copilot_review(pr_number: str) -> bool:
    """Request Copilot to review the PR via GitHub API.

    This can be used to re-request a review after Copilot encountered an error.

    Args:
        pr_number: The PR number to request review for.

    Returns:
        True if the request was successful, False otherwise.
    """
    try:
        # Use stdin to pass JSON body (avoids shell escaping issues)
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
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            # Log stderr for diagnosis (Issue #646)
            stderr_msg = result.stderr.strip() if result.stderr else "No stderr output"
            print(
                f"[merge-check] request_copilot_review failed for PR #{pr_number}: {stderr_msg}",
                file=sys.stderr,
            )
            return False
        return True
    except Exception as e:
        # Log exception for diagnosis (Issue #646)
        print(
            f"[merge-check] request_copilot_review exception for PR #{pr_number}: {e}",
            file=sys.stderr,
        )
        return False


# Threshold for consecutive error reviews before allowing merge with warning
# When Copilot fails this many times consecutively, it's likely a service issue
AI_REVIEW_ERROR_RETRY_THRESHOLD = 2


def check_ai_review_error(pr_number: str) -> dict | None:
    """Check if AI reviews (Copilot/Codex) have errors.

    When Copilot fails to review, it leaves a comment containing
    "encountered an error". This is NOT a successful review.

    This function collects all reviews from AI reviewers and analyzes them
    to detect error patterns.

    Special case (Issue #630): If there are 2+ consecutive error reviews AND
    there was a successful review earlier, the merge is allowed with a warning.
    This handles persistent Copilot service issues that prevent re-reviews.

    Returns:
        Dict with the following fields if error found:
        - reviewer: AI reviewer login name
        - message: Truncated error message
        - allow_with_warning: True if merge should be allowed with warning
        - consecutive_errors: Number of consecutive error reviews
        None if no error is found.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "--paginate",
                f"repos/:owner/:repo/pulls/{pr_number}/reviews",
                "--jq",
                '.[] | select(.user.login | test("copilot|codex"; "i")) | {author: .user.login, body: .body, state: .state, submitted_at: .submitted_at}',
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,  # Increased for pagination
        )
        if result.returncode != 0:
            return None

        # Parse NDJSON output and collect all AI reviews per reviewer
        # Note: jq filter already selects only Copilot/Codex reviewers
        ai_reviews_by_author: dict[str, list[dict]] = {}
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                review = json.loads(line)
                author = review.get("author", "")
                if author not in ai_reviews_by_author:
                    ai_reviews_by_author[author] = []
                ai_reviews_by_author[author].append(review)
            except json.JSONDecodeError:
                continue

        # Sort reviews by submitted_at for each author (chronological order)
        for author in ai_reviews_by_author:
            ai_reviews_by_author[author].sort(key=lambda r: r.get("submitted_at") or "")

        # Check each AI reviewer
        for author, reviews in ai_reviews_by_author.items():
            if not reviews:
                continue

            latest_review = reviews[-1]
            body = latest_review.get("body") or ""

            if AI_REVIEW_ERROR_PATTERN in body.lower():
                # Count consecutive errors from the end
                consecutive_errors = 0
                has_successful_review = False

                for review in reversed(reviews):
                    review_body = review.get("body") or ""
                    if AI_REVIEW_ERROR_PATTERN in review_body.lower():
                        consecutive_errors += 1
                    else:
                        # Found a non-error review
                        has_successful_review = True
                        break

                # If 2+ consecutive errors and there's a successful review earlier,
                # allow merge with warning (Issue #630)
                if consecutive_errors >= AI_REVIEW_ERROR_RETRY_THRESHOLD and has_successful_review:
                    return {
                        "reviewer": author,
                        "message": truncate_body(body, 200),
                        "allow_with_warning": True,
                        "consecutive_errors": consecutive_errors,
                    }

                # Otherwise, block as usual
                return {
                    "reviewer": author,
                    "message": truncate_body(body, 200),
                    "allow_with_warning": False,
                    "consecutive_errors": consecutive_errors,
                }
        return None
    except Exception:
        # On error, don't block (fail open)
        return None
