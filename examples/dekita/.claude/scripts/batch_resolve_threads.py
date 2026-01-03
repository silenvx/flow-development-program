#!/usr/bin/env python3
"""PRの未解決レビュースレッドを一括resolveする。

Why:
    複数のレビュースレッドに同一メッセージで返信し、
    一括でresolveする作業を自動化するため。

What:
    - list_unresolved_threads(): 未解決スレッドを取得
    - reply_and_resolve(): 返信してresolve

Remarks:
    - --dry-run で実行内容を確認可能
    - GitHub GraphQL APIを使用

Changelog:
    - silenvx/dekita#1395: 一括resolve機能を追加
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Add hooks directory to path for importing lib modules
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
from lib.execution import log_hook_execution


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
    except Exception as e:
        print(f"Error getting repo info: {e}", file=sys.stderr)
    return "", ""


def list_unresolved_threads(pr_number: str, owner: str, repo: str) -> list[dict]:
    """List all unresolved review threads with comment details.

    Uses pagination to fetch all threads (up to 50 per page).

    Returns:
        List of unresolved thread dictionaries.
    """
    all_threads: list[dict] = []
    has_next_page = True
    cursor: str | None = None

    # GraphQL query with pagination support
    query = """
    query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $pr) {
          reviewThreads(first: 50, after: $cursor) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              id
              isResolved
              path
              line
              comments(first: 1) {
                nodes {
                  id
                  databaseId
                  body
                  author { login }
                }
              }
            }
          }
        }
      }
    }
    """

    while has_next_page:
        try:
            cmd = [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-F",
                f"owner={owner}",
                "-F",
                f"repo={repo}",
                "-F",
                f"pr={pr_number}",
            ]
            if cursor:
                cmd.extend(["-F", f"cursor={cursor}"])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                print(f"Error listing threads: {result.stderr}", file=sys.stderr)
                return []

            data = json.loads(result.stdout)
            review_threads = (
                data.get("data", {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("reviewThreads", {})
            )

            nodes = review_threads.get("nodes", [])
            all_threads.extend(nodes)

            page_info = review_threads.get("pageInfo", {})
            has_next_page = page_info.get("hasNextPage", False)
            cursor = page_info.get("endCursor")

        except Exception as e:
            print(f"Error fetching review threads: {e}", file=sys.stderr)
            return []

    return [t for t in all_threads if not t.get("isResolved")]


def post_reply(pr_number: str, comment_id: int, message: str, owner: str, repo: str) -> bool:
    """Post a reply to a review comment."""
    # Add signature if not present
    if "-- Claude Code" not in message:
        message = f"{message}\n\n-- Claude Code"

    try:
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
            print(f"  Error posting reply: {result.stderr}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"  Error posting reply: {e}", file=sys.stderr)
        return False


def resolve_thread(thread_id: str) -> bool:
    """Resolve a review thread."""
    query = """
    mutation($threadId: ID!) {
      resolveReviewThread(input: {threadId: $threadId}) {
        thread { isResolved }
      }
    }
    """

    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-F",
                f"threadId={thread_id}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(f"  Error resolving thread: {result.stderr}", file=sys.stderr)
            return False

        data = json.loads(result.stdout)
        return (
            data.get("data", {})
            .get("resolveReviewThread", {})
            .get("thread", {})
            .get("isResolved", False)
        )
    except Exception as e:
        print(f"  Error resolving thread: {e}", file=sys.stderr)
        return False


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage:")
        print('  batch_resolve_threads.py <pr_number> "<message>"')
        print("  batch_resolve_threads.py <pr_number> --dry-run")
        print()
        print("Examples:")
        print('  batch_resolve_threads.py 1395 "修正しました。Verified: 全指摘に対応"')
        print("  batch_resolve_threads.py 1395 --dry-run")
        sys.exit(1)

    pr_number = sys.argv[1]

    # Validate PR number is a positive integer
    try:
        pr_int = int(pr_number)
        if pr_int <= 0:
            raise ValueError("PR number must be positive")
    except ValueError as e:
        print(f"Error: Invalid PR number '{pr_number}': {e}", file=sys.stderr)
        sys.exit(1)

    dry_run = len(sys.argv) >= 3 and sys.argv[2] == "--dry-run"
    message = sys.argv[2] if len(sys.argv) >= 3 and not dry_run else None

    if not dry_run and not message:
        print("Error: Message is required unless using --dry-run", file=sys.stderr)
        sys.exit(1)

    # Get repo info
    owner, repo = get_repo_info()
    if not owner or not repo:
        print("Error: Could not determine repository info", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching unresolved threads for PR #{pr_number}...")
    threads = list_unresolved_threads(pr_number, owner, repo)

    if not threads:
        print("No unresolved threads found.")
        return

    print(f"Found {len(threads)} unresolved thread(s):\n")

    success_count = 0
    fail_count = 0

    for i, t in enumerate(threads, 1):
        thread_id = t.get("id")
        path = t.get("path", "unknown")
        line = t.get("line", "?")
        comments = t.get("comments", {}).get("nodes", [])

        if not comments:
            print(f"[{i}/{len(threads)}] Skipping thread with no comments: {thread_id}")
            continue

        comment = comments[0]
        comment_id = comment.get("databaseId")
        author = comment.get("author", {}).get("login", "unknown")
        body_preview = comment.get("body", "")[:60].replace("\n", " ")

        print(f"[{i}/{len(threads)}] {path}:{line} ({author})")
        print(f"  Preview: {body_preview}...")
        print(f"  Thread ID: {thread_id}")
        print(f"  Comment ID: {comment_id}")

        if dry_run:
            print("  [DRY RUN] Would post reply and resolve")
            success_count += 1
            continue

        # Post reply
        print("  Posting reply...")
        if not post_reply(pr_number, comment_id, message, owner, repo):
            print("  ❌ Failed to post reply")
            fail_count += 1
            continue

        # Resolve thread
        print("  Resolving thread...")
        if resolve_thread(thread_id):
            print("  ✅ Done")
            success_count += 1
        else:
            print("  ❌ Failed to resolve")
            fail_count += 1

        print()

    # Summary
    print("=" * 50)
    if dry_run:
        print(f"[DRY RUN] Would process {success_count} thread(s)")
    else:
        print(f"✅ Success: {success_count} thread(s)")
        if fail_count > 0:
            print(f"❌ Failed: {fail_count} thread(s)")

    # Issue #1419: Log batch resolve execution to hook-execution.log
    log_hook_execution(
        "batch-resolve-threads",
        "approve",
        f"Batch resolved {success_count} thread(s)" if not dry_run else "Dry run",
        {
            "pr_number": pr_number,
            "total_threads": len(threads),
            "resolved_count": success_count,
            "failed_count": fail_count,
            "dry_run": dry_run,
        },
    )


if __name__ == "__main__":
    main()
