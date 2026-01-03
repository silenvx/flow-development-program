#!/usr/bin/env python3
"""AIレビュー（Copilot/Codex）のコメント追跡・分析を行う。

Why:
    レビューコメントの対応状況・品質を追跡し、
    重複コメント検出・カテゴリ分類で効率的なレビュー対応を支援する。

What:
    - log_review_comment(): レビューコメントをログに記録
    - log_codex_review_execution(): Codex CLI実行をログに記録
    - find_similar_comments(): 類似コメントを検出（重複防止）
    - estimate_category(): コメント内容からカテゴリを推定

State:
    - writes: .claude/logs/metrics/review-quality-{session}.jsonl
    - writes: .claude/logs/metrics/codex-reviews-{session}.jsonl

Remarks:
    - 同一comment_idの重複記録を防止（リベース時の再記録対策）
    - 類似度0.85以上で重複と判定（difflib.SequenceMatcher使用）
    - カテゴリ: bug, style, performance, security, test, docs, refactor, other

Changelog:
    - silenvx/dekita#610: レビュー品質追跡を追加
    - silenvx/dekita#1233: Codex CLI実行ログ追加
    - silenvx/dekita#1263: 重複記録防止を追加
    - silenvx/dekita#1389: 類似コメント検出を追加
    - silenvx/dekita#1758: common.pyから分離
    - silenvx/dekita#1840: セッション固有ファイル形式に変更
    - silenvx/dekita#2529: ppidフォールバック完全廃止
"""

import difflib
import os
import re
from pathlib import Path
from typing import Any

from lib.git import get_current_branch
from lib.logging import log_to_session_file, read_all_session_log_entries
from lib.timestamp import get_local_timestamp


def _get_session_id_with_fallback(session_id: str | None) -> str:
    """Get session ID or PPID-based fallback.

    Security: Validates session_id to prevent path traversal attacks.

    Args:
        session_id: Session ID from caller, or None to use fallback.

    Returns:
        The validated session ID or None.

    Issue #2529: ppidフォールバック完全廃止、Noneを返す。
    """
    from lib.session import is_valid_session_id

    if session_id and is_valid_session_id(session_id):
        return session_id
    return None


# Category keywords for automatic classification
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "bug": ["bug", "error", "fix", "crash", "null", "undefined", "exception", "issue", "problem"],
    "style": ["style", "format", "naming", "convention", "indent", "spacing", "whitespace"],
    "performance": ["performance", "optimize", "slow", "memory", "cache", "efficient"],
    "security": ["security", "auth", "permission", "injection", "xss", "csrf", "vulnerable"],
    "test": ["test", "coverage", "assert", "mock", "spec", "unit", "integration"],
    "docs": ["doc", "comment", "readme", "jsdoc", "typedoc", "documentation"],
    "refactor": ["refactor", "extract", "simplify", "duplicate", "dry", "clean", "improve"],
}


def identify_reviewer(user_login: str) -> str:
    """Identify the reviewer type from GitHub user login.

    Categorizes reviewers into:
    - copilot: GitHub Copilot bot
    - codex_cloud: Codex running on GitHub (via @codex review comment)
    - unknown: Other reviewers (human or unrecognized bot)

    Note: codex_cli is identified by the hook context, not user login.

    Args:
        user_login: The GitHub user login (e.g., "copilot-pull-request-reviewer[bot]")

    Returns:
        One of: "copilot", "codex_cloud", "unknown"
    """
    login_lower = user_login.lower()

    # GitHub Copilot patterns
    if "copilot" in login_lower:
        return "copilot"

    # Codex Cloud patterns (GitHub-hosted Codex)
    if "codex" in login_lower or "chatgpt" in login_lower or "openai" in login_lower:
        return "codex_cloud"

    return "unknown"


def estimate_category(body: str) -> str:
    """Estimate the category of a review comment based on its content.

    Uses keyword matching to classify comments into categories.
    Returns "other" if no category matches.

    Args:
        body: The review comment body text

    Returns:
        One of: "bug", "style", "performance", "security", "test", "docs", "refactor", "other"
    """
    if not body:
        return "other"

    body_lower = body.lower()

    # Count keyword matches for each category
    category_scores: dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword in body_lower)
        if score > 0:
            category_scores[category] = score

    if not category_scores:
        return "other"

    # Return the category with the highest score
    return max(category_scores, key=lambda k: category_scores[k])


def _is_comment_already_logged(
    comment_id: str,
    pr_number: int | str | None,
    metrics_log_dir: Path,
) -> bool:
    """Check if a comment is already logged to avoid duplicates.

    Issue #1263: Prevent duplicate entries when rebases trigger re-recording.
    Issue #1840: Now searches across all session files using read_all_session_log_entries.

    Args:
        comment_id: The GitHub comment ID to check
        pr_number: The PR number for context
        metrics_log_dir: Directory containing metrics log files.

    Returns:
        True if the comment is already logged, False otherwise.
    """
    if not comment_id:
        return False

    # Issue #1840: Search across all session files
    entries = read_all_session_log_entries(metrics_log_dir, "review-quality")

    for entry in entries:
        if entry.get("comment_id") == comment_id:
            # For same PR, consider it a duplicate
            # Normalize both to strings for robust comparison
            # Note: Skip comparison if either is None to avoid false positives
            entry_pr = entry.get("pr_number")
            if entry_pr is not None and pr_number is not None:
                if str(entry_pr) == str(pr_number):
                    return True

    return False


def log_review_comment(
    metrics_log_dir: Path,
    pr_number: int | str,
    comment_id: int | str,
    reviewer: str,
    category: str | None = None,
    file_path: str | None = None,
    line_number: int | None = None,
    body_preview: str | None = None,
    resolution: str | None = None,
    validity: str | None = None,
    issue_created: int | None = None,
    reason: str | None = None,
    session_id: str | None = None,
) -> None:
    """Log a review comment to the review quality log.

    Creates a JSON Lines entry for tracking review comment handling.
    Used for both initial comment recording and resolution updates.

    Issue #1263: Skips initial recording if comment_id already exists (prevents
    duplicates when rebases trigger re-recording). Resolution updates are always
    logged to allow tracking comment handling outcomes.
    Issue #1840: Now writes to session-specific file.

    Args:
        metrics_log_dir: Directory for metrics log files.
        pr_number: The PR number
        comment_id: The GitHub comment ID (or generated ID for CLI reviews)
        reviewer: One of "copilot", "codex_cloud", "codex_cli"
        category: Comment category (auto-estimated if None)
        file_path: Path to the file being commented on
        line_number: Line number in the file
        body_preview: First 200 chars of comment body
        resolution: One of "accepted", "rejected", "issue_created", None
        validity: One of "valid", "invalid", "partially_valid", None
        issue_created: Issue number if resolution is "issue_created"
        reason: Reason for rejection or partial validity
        session_id: Session ID (uses PPID fallback if None). Issue #2496.
    """
    resolved_session_id = _get_session_id_with_fallback(session_id)

    # Issue #1263: Skip if comment already logged (duplicate prevention)
    # Only skip for initial recordings (resolution is None) - allow resolution updates
    # Note: Use `is None` instead of `not resolution` to correctly handle empty strings
    # Issue #1840: Now searches across all session files
    comment_id_str = str(comment_id) if comment_id else None
    if (
        comment_id_str
        and resolution is None  # Allow resolution updates to pass through
        and _is_comment_already_logged(comment_id_str, pr_number, metrics_log_dir)
    ):
        return

    # Parse PR number: keep as int if numeric, otherwise keep as string or None
    parsed_pr_number: int | str | None = None
    if pr_number:
        if isinstance(pr_number, int):
            parsed_pr_number = pr_number
        elif str(pr_number).isdigit():
            parsed_pr_number = int(pr_number)
        else:
            parsed_pr_number = str(pr_number)  # Keep "unknown" or other non-numeric values

    entry: dict[str, Any] = {
        "timestamp": get_local_timestamp(),
        "session_id": resolved_session_id,
        "pr_number": parsed_pr_number,
        "comment_id": str(comment_id) if comment_id else None,
        "reviewer": reviewer,
        "category": category or "other",
    }

    # Add optional fields
    if file_path:
        entry["file_path"] = file_path
    if line_number is not None:
        entry["line_number"] = line_number
    if body_preview:
        entry["body_preview"] = body_preview[:200]

    # Resolution fields (may be None for initial recording)
    if resolution:
        entry["resolution"] = resolution
    if validity:
        entry["validity"] = validity
    if issue_created:
        entry["issue_created"] = issue_created
    if reason:
        entry["reason"] = reason

    # Add branch context
    branch = get_current_branch()
    if branch:
        entry["branch"] = branch

    # Issue #1840: Write to session-specific file
    log_to_session_file(metrics_log_dir, "review-quality", resolved_session_id, entry)


def log_codex_review_execution(
    metrics_log_dir: Path,
    *,
    branch: str | None = None,
    base: str | None = None,
    verdict: str,
    comment_count: int,
    tokens_used: int | None = None,
    exit_code: int = 0,
    session_id: str | None = None,
) -> None:
    """Log a Codex CLI review execution to codex-reviews.jsonl.

    This function logs the full review execution metadata, not just individual comments.
    It should be called for every codex review execution, regardless of whether issues
    were found.

    Issue #1840: Now writes to session-specific file.

    Args:
        metrics_log_dir: Directory for metrics log files.
        branch: The branch being reviewed (defaults to current branch)
        base: The base branch for the review (e.g., "main")
        verdict: Review verdict:
            - "pass": No issues found
            - "fail": Issues found (comment_count > 0)
            - "error": Command failed (exit_code != 0)
        comment_count: Number of review comments/issues found
        tokens_used: Number of tokens used by the API (if available)
        exit_code: Exit code of the codex review command
        session_id: Session ID (uses PPID fallback if None). Issue #2496.
    """
    resolved_session_id = _get_session_id_with_fallback(session_id)

    entry: dict[str, Any] = {
        "timestamp": get_local_timestamp(),
        "session_id": resolved_session_id,
        "branch": branch or get_current_branch(),
        "base": base,
        "verdict": verdict,
        "comment_count": comment_count,
        "exit_code": exit_code,
    }

    if tokens_used is not None:
        entry["tokens_used"] = tokens_used

    # Issue #1840: Write to session-specific file
    log_to_session_file(metrics_log_dir, "codex-reviews", resolved_session_id, entry)


# =============================================================================
# Review comment similarity detection (Issue #1389)
# =============================================================================


def normalize_comment_text(text: str | None) -> str:
    """Normalize comment text for comparison.

    Issue #1389: Prepare text for similarity comparison by normalizing:
    - Lowercase conversion
    - Whitespace normalization (multiple spaces/newlines → single space)
    - Common punctuation removal

    Args:
        text: The review comment body text

    Returns:
        Normalized text suitable for similarity comparison
    """
    if not text:
        return ""

    # Lowercase
    text = text.lower()

    # Normalize whitespace (newlines, tabs, multiple spaces → single space)
    text = re.sub(r"\s+", " ", text)

    # Remove common markdown formatting
    text = re.sub(r"\*\*|__|\*|_|`", "", text)

    # Remove common punctuation that doesn't affect meaning
    text = re.sub(r"[.,;:!?()\[\]{}\"']", "", text)

    return text.strip()


def calculate_comment_similarity(text1: str, text2: str) -> float:
    """Calculate similarity between two comment texts.

    Issue #1389: Uses difflib.SequenceMatcher for lightweight similarity calculation.
    Returns a ratio between 0.0 (completely different) and 1.0 (identical).

    Args:
        text1: First comment text
        text2: Second comment text

    Returns:
        Similarity ratio (0.0-1.0)
    """
    if not text1 or not text2:
        return 0.0

    # Normalize both texts for comparison
    normalized1 = normalize_comment_text(text1)
    normalized2 = normalize_comment_text(text2)

    if not normalized1 or not normalized2:
        return 0.0

    # Use SequenceMatcher for similarity calculation
    return difflib.SequenceMatcher(None, normalized1, normalized2).ratio()


def find_similar_comments(
    new_comment: dict[str, Any],
    previous_comments: list[dict[str, Any]],
    threshold: float = 0.85,
) -> list[dict[str, Any]]:
    """Find similar comments from previous review threads.

    Issue #1389: Detect duplicate review comments by comparing:
    1. Same reviewer (e.g., Copilot)
    2. Same or nearby file path
    3. Text similarity above threshold

    Args:
        new_comment: The new comment to check, with keys:
            - body: Comment text
            - reviewer: Reviewer name/login (e.g., "Copilot"). Required for match -
              None/empty values result in non-match to avoid false duplicate detection.
            - path: File path (optional)
        previous_comments: List of previous comments to compare against
        threshold: Minimum similarity ratio to consider a match (default: 0.85)

    Returns:
        List of similar comments from previous_comments, each with added
        "similarity_score" field
    """
    if not new_comment.get("body"):
        return []

    new_body = new_comment.get("body", "")
    new_reviewer = (new_comment.get("reviewer") or "").lower()
    new_path = new_comment.get("path", "")

    similar = []
    for prev in previous_comments:
        prev_body = prev.get("body", "")
        prev_reviewer = (prev.get("reviewer") or "").lower()
        prev_path = prev.get("path", "")

        # Skip if reviewers don't match (we only care about same reviewer duplicates)
        # Require both reviewers to exist and be equal - missing reviewer = non-match
        if not new_reviewer or not prev_reviewer or new_reviewer != prev_reviewer:
            continue

        # Skip if completely different file paths
        if new_path and prev_path and new_path != prev_path:
            # Allow partial path matches (e.g., same filename in different dirs)
            new_filename = os.path.basename(new_path)
            prev_filename = os.path.basename(prev_path)
            if new_filename != prev_filename:
                continue

        # Calculate text similarity
        score = calculate_comment_similarity(new_body, prev_body)
        if score >= threshold:
            result = prev.copy()
            result["similarity_score"] = score
            similar.append(result)

    # Sort by similarity score (highest first)
    similar.sort(key=lambda x: x.get("similarity_score", 0), reverse=True)
    return similar
