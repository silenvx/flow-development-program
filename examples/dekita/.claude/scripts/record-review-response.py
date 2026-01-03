#!/usr/bin/env python3
"""レビューコメントへの対応を記録する。

Why:
    レビューコメントの対応状況を追跡し、
    品質分析に活用するため。

What:
    - record_response(): 対応結果を記録
    - update_comment(): 既存レコードを更新

State:
    - writes: .claude/logs/metrics/review-quality-*.jsonl

Remarks:
    - resolution: accepted（対応済）, rejected（対応しない）, issue_created（Issue化）
    - validity: valid, invalid, partially_valid
    - --reason で理由を記録

Changelog:
    - silenvx/dekita#1800: レビュー対応記録機能を追加
    - silenvx/dekita#2496: PPIDベースフォールバックに対応
"""

import argparse

# Import from hooks common module
# Issue #2496: Use PPID-based fallback instead of get_claude_session_id
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
from common import METRICS_LOG_DIR
from lib.logging import log_to_session_file


def _get_session_id_fallback() -> str:
    """Get session ID using PPID fallback."""
    return f"ppid-{os.getppid()}"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Record review comment response for quality tracking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--pr",
        required=True,
        help="PR number",
    )
    parser.add_argument(
        "--comment-id",
        required=True,
        help="Comment ID (from auto-recorded logs)",
    )
    parser.add_argument(
        "--resolution",
        required=True,
        choices=["accepted", "rejected", "issue_created"],
        help="How the comment was handled",
    )
    parser.add_argument(
        "--validity",
        choices=["valid", "invalid", "partially_valid"],
        help="Whether the comment was valid (default: inferred from resolution)",
    )
    parser.add_argument(
        "--category",
        choices=["bug", "style", "performance", "security", "test", "docs", "refactor", "other"],
        help="Override the auto-detected category",
    )
    parser.add_argument(
        "--issue",
        help="Issue number created (when resolution=issue_created)",
    )
    parser.add_argument(
        "--reason",
        help="Reason for rejection or partial validity",
    )

    return parser.parse_args()


def infer_validity(resolution: str) -> str:
    """Infer validity from resolution if not explicitly provided."""
    if resolution == "accepted":
        return "valid"
    elif resolution == "rejected":
        return "invalid"
    elif resolution == "issue_created":
        return "valid"  # If we created an issue, the comment was valid
    return "valid"


def record_response(
    pr_number: str,
    comment_id: str,
    resolution: str,
    validity: str | None,
    category: str | None,
    issue_created: str | None,
    reason: str | None,
) -> bool:
    """Record the response to a review comment.

    This appends a new record with resolution/validity to the log.
    The analysis script will use the latest record for each comment_id.

    Issue #2194: Now writes to session-specific files instead of global file.

    Returns:
        True if recording was successful, False otherwise.
    """
    session_id = _get_session_id_fallback()
    if not session_id:
        print("Error: Could not get session ID", file=sys.stderr)
        return False

    # Infer validity if not provided
    if validity is None:
        validity = infer_validity(resolution)

    # Build the record
    # Use int types for numeric IDs for consistency with other logs (Issue #1687)
    try:
        pr_number_int = int(pr_number)
        comment_id_int = int(comment_id)
    except ValueError as e:
        print(
            f"Error: pr_number and comment_id must be numeric. "
            f"Got: pr_number='{pr_number}', comment_id='{comment_id}'",
            file=sys.stderr,
        )
        raise ValueError(f"Invalid numeric ID: {e}") from e

    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": session_id,
        "pr_number": pr_number_int,
        "comment_id": comment_id_int,
        "resolution": resolution,
        "validity": validity,
        "record_type": "response",  # Distinguish from initial recording
    }

    # Add optional fields
    if category:
        record["category"] = category
    if issue_created:
        try:
            record["issue_created"] = int(issue_created)
        except ValueError:
            print(
                f"Warning: issue_created must be numeric, got '{issue_created}'. Skipping field.",
                file=sys.stderr,
            )
    if reason:
        record["reason"] = reason

    # Append to session-specific log file
    log_to_session_file(METRICS_LOG_DIR, "review-quality", session_id, record)

    print(f"Recorded response for comment {comment_id} on PR #{pr_number}")
    print(f"  Resolution: {resolution}")
    print(f"  Validity: {validity}")
    if category:
        print(f"  Category: {category}")
    if "issue_created" in record:
        print(f"  Issue created: #{record['issue_created']}")
    if reason:
        print(f"  Reason: {reason}")

    return True


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Validate issue_created requirement
    if args.resolution == "issue_created" and not args.issue:
        print("Error: --issue is required when resolution is 'issue_created'", file=sys.stderr)
        return 1

    success = record_response(
        pr_number=args.pr,
        comment_id=args.comment_id,
        resolution=args.resolution,
        validity=args.validity,
        category=args.category,
        issue_created=args.issue,
        reason=args.reason,
    )

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
