#!/usr/bin/env python3
"""セッション活動サマリーを生成する。

Why:
    フック実行ログからセッション単位の活動を分析し、
    ブロック・トリガー・時間を可視化するため。

What:
    - analyze_session(): セッションを分析
    - generate_summary(): サマリーを生成
    - list_sessions(): 最近のセッションを一覧表示

State:
    - reads: .claude/logs/session/*/hook-execution-*.jsonl

Remarks:
    - --session でセッションID指定
    - --json でJSON形式出力
    - --list で最近のセッション一覧

Changelog:
    - silenvx/dekita#1242: セッションサマリー機能を追加
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

# Add hooks directory to path for lib imports
SCRIPT_DIR = Path(__file__).parent
HOOKS_DIR = SCRIPT_DIR.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
from lib.logging import read_all_session_log_entries


# Log file path - use CLAUDE_PROJECT_DIR for consistency across worktrees
def get_log_dir() -> Path:
    """Get the log directory path.

    Uses CLAUDE_PROJECT_DIR environment variable if set (for worktree compatibility),
    otherwise falls back to the script's parent directory.

    Security: The path is resolved and validated to prevent directory traversal.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        base_path = Path(project_dir)
        # Prevent directory traversal by checking for ".." in path components
        if ".." in base_path.parts:
            return Path(__file__).parents[2] / ".claude" / "logs" / "execution"
        # Resolve to absolute path after validation
        base_path = base_path.resolve()
        log_path = base_path / ".claude" / "logs" / "execution"
        return log_path
    # Fallback: script location relative path (project root two levels up)
    return Path(__file__).parents[2] / ".claude" / "logs" / "execution"


LOG_DIR = get_log_dir()


def parse_log_entries() -> list[dict[str, Any]]:
    """Parse hook execution log files (all sessions).

    Returns:
        List of log entries as dictionaries from all session log files.
    """
    return read_all_session_log_entries(LOG_DIR, "hook-execution")


def get_session_entries(
    entries: list[dict[str, Any]], session_id: str | None = None
) -> list[dict[str, Any]]:
    """Filter entries by session ID.

    Args:
        entries: List of all log entries.
        session_id: Session ID to filter by. If None, returns empty list.
            Issue #2317: CLAUDE_SESSION_ID環境変数を廃止。

    Returns:
        Filtered list of entries for the specified session.
    """
    if not session_id:
        return []

    return [e for e in entries if e.get("session_id") == session_id]


def calculate_duration(
    entries: list[dict[str, Any]],
) -> tuple[datetime | None, datetime | None, str]:
    """Calculate session duration from log entries.

    Args:
        entries: List of log entries for a session.

    Returns:
        Tuple of (start_time, end_time, duration_string).
    """
    if not entries:
        return None, None, "N/A"

    timestamps = []
    for entry in entries:
        ts = entry.get("timestamp")
        if ts:
            try:
                # Parse ISO format timestamp
                dt = datetime.fromisoformat(ts)
                timestamps.append(dt)
            except (ValueError, TypeError):
                continue

    if not timestamps:
        return None, None, "N/A"

    start = min(timestamps)
    end = max(timestamps)
    duration = end - start

    hours, remainder = divmod(int(duration.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours > 0:
        duration_str = f"{hours}h {minutes}m"
    elif minutes > 0:
        duration_str = f"{minutes}m {seconds}s"
    else:
        duration_str = f"{seconds}s"

    return start, end, duration_str


def _count_by_decision(entries: list[dict[str, Any]], decision: str) -> dict[str, int]:
    """Count entries by decision type, grouped by hook name.

    Args:
        entries: List of log entries for a session.
        decision: Decision type to count (e.g., "block", "approve").

    Returns:
        Dictionary mapping hook name to count.
    """
    counts: Counter[str] = Counter()
    for entry in entries:
        if entry.get("decision") == decision:
            hook = entry.get("hook", "unknown")
            counts[hook] += 1
    return dict(counts)


def count_blocks(entries: list[dict[str, Any]]) -> dict[str, int]:
    """Count blocks by hook name.

    Args:
        entries: List of log entries for a session.

    Returns:
        Dictionary mapping hook name to block count.
    """
    return _count_by_decision(entries, "block")


def count_approves(entries: list[dict[str, Any]]) -> dict[str, int]:
    """Count approvals by hook name.

    Args:
        entries: List of log entries for a session.

    Returns:
        Dictionary mapping hook name to approval count.
    """
    return _count_by_decision(entries, "approve")


def extract_pr_info(entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Extract PR-related information from log entries.

    Note:
        Tracks PRs where ci-monitor completed successfully (monitor_complete with
        result=success). This indicates CI passed and the PR was ready for merge,
        though it does not directly confirm the merge action itself.
        PR creation detection is not implemented as gh pr create is not captured
        by hooks.

    Args:
        entries: List of log entries for a session.

    Returns:
        Dictionary with 'completed' key containing PR numbers where CI monitoring
        completed successfully.
    """
    prs_completed: set[str] = set()

    for entry in entries:
        details = entry.get("details", {})
        if isinstance(details, dict):
            pr_number = details.get("pr_number")

            # Check ci-monitor events for successful monitoring completion
            hook = entry.get("hook")
            if hook == "ci-monitor":
                action = details.get("action")
                if action == "monitor_complete" and details.get("result") == "success":
                    if pr_number:
                        prs_completed.add(str(pr_number))

    return {
        "completed": sorted(prs_completed),
    }


def list_recent_sessions(entries: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """List recent sessions with basic info.

    Args:
        entries: List of all log entries.
        limit: Maximum number of sessions to return.

    Returns:
        List of session info dictionaries.
    """
    sessions: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        session_id = entry.get("session_id")
        if session_id:
            sessions.setdefault(session_id, []).append(entry)

    session_infos = []
    for session_id, session_entries in sessions.items():
        start, end, duration = calculate_duration(session_entries)
        blocks = count_blocks(session_entries)
        session_infos.append(
            {
                "session_id": session_id,
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "duration": duration,
                "entry_count": len(session_entries),
                "block_count": sum(blocks.values()),
            }
        )

    # Sort by start time (most recent first)
    # Use epoch as fallback for None to place sessions without timestamps at the end
    session_infos.sort(key=lambda x: x["start"] or "1970-01-01T00:00:00", reverse=True)
    return session_infos[:limit]


def generate_summary(entries: list[dict[str, Any]], session_id: str) -> dict[str, Any]:
    """Generate session summary.

    Args:
        entries: List of log entries for a session.
        session_id: The session ID.

    Returns:
        Summary dictionary.
    """
    start, end, duration = calculate_duration(entries)
    blocks = count_blocks(entries)
    approves = count_approves(entries)
    pr_info = extract_pr_info(entries)

    return {
        "session_id": session_id,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "duration": duration,
        "entry_count": len(entries),
        "blocks": {
            "total": sum(blocks.values()),
            "by_hook": blocks,
        },
        "approvals": {
            "total": sum(approves.values()),
            "by_hook": approves,
        },
        "prs": pr_info,
    }


def print_summary(summary: dict[str, Any]) -> None:
    """Print summary in human-readable format.

    Args:
        summary: Summary dictionary.
    """
    print("\n=== Session Summary ===")
    print(f"Session ID: {summary['session_id']}")
    print(f"Duration: {summary['duration']}")
    if summary["start"]:
        print(f"Start: {summary['start']}")
    if summary["end"]:
        print(f"End: {summary['end']}")
    print(f"Log entries: {summary['entry_count']}")

    # PRs with completed CI monitoring (tracked via ci-monitor logs)
    prs = summary.get("prs", {})
    if prs.get("completed"):
        print(f"CI monitoring completed: #{', #'.join(prs['completed'])} ({len(prs['completed'])})")

    # Blocks
    blocks = summary.get("blocks", {})
    total_blocks = blocks.get("total", 0)
    if total_blocks > 0:
        print(f"Blocks encountered: {total_blocks}")
        for hook, count in sorted(blocks.get("by_hook", {}).items(), key=lambda x: -x[1]):
            print(f"  - {hook}: {count}")
    else:
        print("Blocks encountered: 0")

    print("")


def print_sessions_list(sessions: list[dict[str, Any]]) -> None:
    """Print list of recent sessions.

    Args:
        sessions: List of session info dictionaries.
    """
    print("\n=== Recent Sessions ===")
    for i, session in enumerate(sessions, 1):
        print(f"\n{i}. {session['session_id'][:8]}...")
        print(f"   Duration: {session['duration']}")
        print(f"   Entries: {session['entry_count']}, Blocks: {session['block_count']}")
        if session["start"]:
            print(f"   Start: {session['start']}")
    print("")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate session summary from hook execution logs"
    )
    parser.add_argument(
        "--session",
        "-s",
        help="Session ID to analyze. Use --list to find available session IDs.",
    )
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="Output in JSON format",
    )
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List recent sessions",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of sessions to list (default: 10)",
    )

    args = parser.parse_args()

    # Parse log files
    entries = parse_log_entries()
    if not entries:
        print("No log entries found.", file=sys.stderr)
        return 1

    # List mode
    if args.list:
        sessions = list_recent_sessions(entries, args.limit)
        if args.json:
            print(json.dumps(sessions, indent=2, ensure_ascii=False))
        else:
            print_sessions_list(sessions)
        return 0

    # Get session ID
    # Issue #2317: CLAUDE_SESSION_ID環境変数を廃止。
    # Use --list to find session IDs, then use --session to specify one.
    session_id = args.session
    if not session_id:
        print(
            "No session ID provided.",
            file=sys.stderr,
        )
        print(
            "Use --list to see recent sessions, then --session <ID> to view details.",
            file=sys.stderr,
        )
        return 1

    # Filter entries
    session_entries = get_session_entries(entries, session_id)
    if not session_entries:
        print(f"No entries found for session: {session_id}", file=sys.stderr)
        return 1

    # Generate and print summary
    summary = generate_summary(session_entries, session_id)
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print_summary(summary)

    return 0


if __name__ == "__main__":
    sys.exit(main())
