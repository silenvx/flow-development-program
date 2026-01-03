#!/usr/bin/env python3
"""レビューコメントに返信してスレッドをresolveする。

Why:
    レビュー対応を効率化し、返信・resolve・品質記録を
    一括で行うため。

What:
    - post_reply(): コメントに返信
    - resolve_thread(): スレッドをresolve
    - record_response(): 対応を品質ログに記録

Remarks:
    - --verified で修正と検証を1メッセージに統合
    - --resolution でaccepted/rejected/issue_createdを指定
    - GitHub GraphQL APIを使用

Changelog:
    - silenvx/dekita#610: レビュー返信・resolve機能を追加
"""

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

_HOOKS_DIR = Path(__file__).parent.parent / "hooks"
sys.path.insert(0, str(_HOOKS_DIR))
# Import from hooks common module (Issue #1639: add logging)
# Issue #2496: Use PPID-based fallback instead of get_claude_session_id
import os

from common import EXECUTION_LOG_DIR


def _get_session_id_fallback() -> str:
    """Get session ID using PPID fallback."""
    return f"ppid-{os.getppid()}"


sys.path.pop(0)

# Log file for review comment responses (Issue #1639)
REVIEW_COMMENTS_LOG = EXECUTION_LOG_DIR / "review-comments.jsonl"

# Import record_response from record-review-response.py (Issue #1432)
# The file has a hyphen in the name, so we need to import it dynamically
_SCRIPT_DIR = Path(__file__).parent
_record_response_path = _SCRIPT_DIR / "record-review-response.py"
_spec = importlib.util.spec_from_file_location("record_review_response", _record_response_path)
_record_response_module = importlib.util.module_from_spec(_spec)
sys.modules["record_review_response"] = _record_response_module
# record-review-response.py のモジュールレベルで sys.path が変更されるため、
# ここで一旦現在の sys.path を保存し、インポート後に復元して副作用を局所化する
_original_sys_path = list(sys.path)
try:
    _spec.loader.exec_module(_record_response_module)
finally:
    sys.path = _original_sys_path
record_response = _record_response_module.record_response


def post_reply(pr_number: str, comment_id: str, message: str, owner: str, repo: str) -> int | None:
    """Post a reply to a review comment.

    Uses the /replies endpoint which correctly creates inline thread replies.
    The in_reply_to parameter on the /comments endpoint does not work as expected.
    See: Issue #748

    Returns:
        The created reply comment ID on success, None on failure.
    """
    # Add signature if not present
    if "-- Claude Code" not in message:
        message = f"{message}\n\n-- Claude Code"

    try:
        # Use /replies endpoint for proper inline thread replies (Issue #748, #754)
        # Note: GitHub API path requires PR number: /pulls/{pr}/comments/{id}/replies
        result = subprocess.run(
            [
                "gh",
                "api",
                f"/repos/{owner}/{repo}/pulls/{pr_number}/comments/{comment_id}/replies",
                "-X",
                "POST",
                "-f",
                f"body={message}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(f"Error posting reply: {result.stderr}", file=sys.stderr)
            return None

        # Parse response to get created comment ID (Issue #873)
        try:
            response_data = json.loads(result.stdout)
            reply_id = response_data.get("id")
            print(f"✅ Reply posted to comment #{comment_id}")
            if reply_id:
                print(f"✅ Created reply comment #{reply_id}")
            # GitHub comment IDs are always positive, so 0 means "unknown but success"
            return reply_id if reply_id else 0
        except json.JSONDecodeError:
            # API succeeded but response parsing failed - still a success
            print(f"✅ Reply posted to comment #{comment_id}")
            print("⚠️ Could not parse response to get reply comment ID", file=sys.stderr)
            return 0  # Return 0 indicating success but unknown ID (GitHub IDs are always positive)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return None


def resolve_thread(thread_id: str) -> bool:
    """Resolve a review thread."""
    query = f"""
    mutation {{
      resolveReviewThread(input: {{threadId: "{thread_id}"}}) {{
        thread {{ isResolved }}
      }}
    }}
    """

    try:
        result = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={query}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(f"Error resolving thread: {result.stderr}", file=sys.stderr)
            return False

        data = json.loads(result.stdout)
        if data.get("data", {}).get("resolveReviewThread", {}).get("thread", {}).get("isResolved"):
            print(f"✅ Thread {thread_id} resolved")
            return True
        else:
            print(f"⚠️ Thread resolution may have failed: {result.stdout}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return False


def get_repo_info() -> tuple[str, str]:
    """Get repository owner and name from git remote."""
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "owner,name"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("owner", {}).get("login", ""), data.get("name", "")
    except Exception:
        pass
    return "", ""


def list_unresolved_threads(pr_number: str) -> list[dict]:
    """List all unresolved review threads."""
    owner, repo = get_repo_info()
    if not owner or not repo:
        print("Error: Could not determine repository info", file=sys.stderr)
        return []

    query = f"""
    query($pr: Int!) {{
      repository(owner: "{owner}", name: "{repo}") {{
        pullRequest(number: $pr) {{
          reviewThreads(first: 50) {{
            nodes {{
              id
              isResolved
              comments(first: 1) {{
                nodes {{
                  id
                  databaseId
                  body
                  author {{ login }}
                }}
              }}
            }}
          }}
        }}
      }}
    }}
    """

    try:
        result = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={query}", "-F", f"pr={pr_number}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(f"Error listing threads: {result.stderr}", file=sys.stderr)
            return []

        data = json.loads(result.stdout)
        threads = (
            data.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
            .get("nodes", [])
        )
        return [t for t in threads if not t.get("isResolved")]
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return []


def format_verified_message(fix_msg: str, verify_msg: str) -> str:
    """Format fix claim and verification into a combined message.

    Output format:
        修正済み: [fix_msg]

        Verified: [verify_msg]

    Note: The "-- Claude Code" signature is added by post_reply(), not here.
    """
    # Ensure fix message has proper prefix (check with colon+space for consistency)
    if not fix_msg.startswith("修正済み: "):
        fix_msg = f"修正済み: {fix_msg}"

    # Ensure verify message has proper prefix (check with colon+space for consistency)
    if not verify_msg.startswith("Verified: "):
        verify_msg = f"Verified: {verify_msg}"

    return f"{fix_msg}\n\n{verify_msg}"


def parse_quality_options(args: list[str]) -> argparse.Namespace:
    """Parse optional quality tracking arguments.

    Args:
        args: List of arguments after the main positional args

    Returns:
        Namespace with resolution, validity, category, issue, reason
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--resolution",
        choices=["accepted", "rejected", "issue_created"],
        default="accepted",
        help="How the comment was handled",
    )
    parser.add_argument(
        "--validity",
        choices=["valid", "invalid", "partially_valid"],
        help="Whether the comment was valid",
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

    return parser.parse_args(args)


def log_review_comment_response(
    pr_number: str,
    comment_id: str,
    message: str,
    resolution: str,
    category: str | None,
    issue_created: str | None,
) -> None:
    """Log review comment response to review-comments.jsonl.

    Issue #1639: Add logging for review comment processing history.

    Args:
        pr_number: PR number
        comment_id: Comment ID being responded to
        message: Response message
        resolution: How the comment was handled (accepted, rejected, issue_created)
        category: Category of the comment (optional)
        issue_created: Issue number if resolution is issue_created (optional)
    """
    try:
        EXECUTION_LOG_DIR.mkdir(parents=True, exist_ok=True)

        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": _get_session_id_fallback(),
            "pr_number": int(pr_number),
            "comment_id": int(comment_id),
            "resolution": resolution,
            "response": message[:200] if message else "",  # Truncate long messages
        }

        # Optional fields
        if category:
            log_entry["category"] = category
        if issue_created:
            log_entry["issue_created"] = int(issue_created)

        with open(REVIEW_COMMENTS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    except Exception as e:
        # Don't fail the main operation if logging fails
        print(f"⚠️ Warning: Failed to log review response: {e}", file=sys.stderr)


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage:")
        print('  review-respond.py <pr_number> <comment_id> <thread_id> "<message>" [options]')
        print(
            '  review-respond.py <pr_number> <comment_id> <thread_id> --verified "<fix>" "<verify>" [options]'
        )
        print("  review-respond.py <pr_number> --list  (list unresolved threads)")
        print()
        print("Options:")
        print("  --resolution   accepted (default), rejected, issue_created")
        print("  --validity     valid, invalid, partially_valid")
        print("  --category     bug, style, performance, security, test, docs, refactor, other")
        print("  --issue        Issue number (required for issue_created)")
        print("  --reason       Reason for rejection")
        print()
        print("Examples:")
        print('  review-respond.py 464 123456789 PRRT_xxx "修正済み: 追加"')
        print(
            '  review-respond.py 464 123456789 PRRT_xxx --verified "処理順序修正" "file.py:10-20"'
        )
        print('  review-respond.py 464 123456789 PRRT_xxx "False positive" --resolution rejected')
        print("  review-respond.py 464 --list")
        sys.exit(1)

    pr_number = sys.argv[1]

    # List mode
    if len(sys.argv) == 3 and sys.argv[2] == "--list":
        threads = list_unresolved_threads(pr_number)
        if not threads:
            print("No unresolved threads found.")
            return

        print(f"Found {len(threads)} unresolved thread(s):\n")
        for t in threads:
            thread_id = t.get("id")
            comments = t.get("comments", {}).get("nodes", [])
            if comments:
                comment = comments[0]
                comment_id = comment.get("databaseId")
                author = comment.get("author", {}).get("login", "unknown")
                body = comment.get("body", "")[:80].replace("\n", " ")
                print(f"Thread: {thread_id}")
                print(f"  Comment ID: {comment_id}")
                print(f"  Author: {author}")
                print(f"  Body: {body}...")
                print(
                    f'  Command: python3 .claude/scripts/review-respond.py {pr_number} {comment_id} {thread_id} "<message>"'
                )
                print()
        return

    # Track where optional args start
    extra_args_start = 5  # Default: after pr, comment_id, thread_id, message

    # Check for --verified mode (requires 7 args: script, pr, comment_id, thread_id, --verified, fix, verify)
    if len(sys.argv) >= 5 and sys.argv[4] == "--verified":
        # Validate that both fix and verify messages are provided
        if len(sys.argv) < 7:
            print("Error: --verified requires both fix and verify messages.", file=sys.stderr)
            print(
                'Usage: review-respond.py <pr_number> <comment_id> <thread_id> --verified "<fix>" "<verify>"'
            )
            sys.exit(1)

        # --verified mode: <pr_number> <comment_id> <thread_id> --verified "<fix>" "<verify>"
        comment_id = sys.argv[2]
        thread_id = sys.argv[3]
        fix_msg = sys.argv[5]
        verify_msg = sys.argv[6]
        message = format_verified_message(fix_msg, verify_msg)
        extra_args_start = 7  # Optional args start after verify_msg
    elif len(sys.argv) >= 5:
        # Standard mode: <pr> <comment_id> <thread_id> "<message>"
        comment_id = sys.argv[2]
        thread_id = sys.argv[3]
        message = sys.argv[4]
        extra_args_start = 5  # Optional args start after message
    else:
        print("Error: Missing arguments.", file=sys.stderr)
        print('Usage: review-respond.py <pr_number> <comment_id> <thread_id> "<message>"')
        sys.exit(1)

    # Parse optional quality tracking arguments (Issue #1432)
    quality_opts = parse_quality_options(sys.argv[extra_args_start:])

    # Validate: --issue is required for issue_created
    if quality_opts.resolution == "issue_created" and not quality_opts.issue:
        print("Error: --issue is required when resolution is 'issue_created'", file=sys.stderr)
        sys.exit(1)

    # Get repo info for API calls
    owner, repo = get_repo_info()
    if not owner or not repo:
        print("Error: Could not determine repository info", file=sys.stderr)
        sys.exit(1)

    # Post reply
    reply_id = post_reply(pr_number, comment_id, message, owner, repo)
    if reply_id is None:
        sys.exit(1)

    # Resolve thread
    if not resolve_thread(thread_id):
        sys.exit(1)

    # Record response for quality tracking (Issue #1432)
    # Log review comment response (Issue #1639)
    # Note: log_review_comment_response handles its own exceptions internally
    record_succeeded = False

    try:
        record_response(
            pr_number=pr_number,
            comment_id=comment_id,
            resolution=quality_opts.resolution,
            validity=quality_opts.validity,
            category=quality_opts.category,
            issue_created=quality_opts.issue,
            reason=quality_opts.reason,
        )
        record_succeeded = True
    except Exception as e:
        print(f"\n⚠️ Warning: Failed to record response: {e}", file=sys.stderr)

    # log_review_comment_response handles its own exceptions and won't raise
    log_review_comment_response(
        pr_number=pr_number,
        comment_id=comment_id,
        message=message,
        resolution=quality_opts.resolution,
        category=quality_opts.category,
        issue_created=quality_opts.issue,
    )

    if record_succeeded:
        print("\n✅ Done! Comment replied, thread resolved, and responses logged.")
    else:
        print("✅ Comment replied and thread resolved (recording skipped).")


if __name__ == "__main__":
    main()
