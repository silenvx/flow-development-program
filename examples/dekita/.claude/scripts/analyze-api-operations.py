#!/usr/bin/env python3
"""API操作ログの分析ツールを提供する。

Why:
    開発ワークフローの洞察を得るため、gh/git APIの
    操作履歴を分析する機能が必要。

What:
    - timeline: セッション単位の操作タイムライン表示
    - pr-lifecycle: PR単位のライフサイクル分析
    - issue-lifecycle: Issue単位のライフサイクル分析
    - errors: エラー発生パターンの分析
    - duration-stats: 操作時間の統計
    - summary: 全体サマリー生成

State:
    - reads: .claude/logs/execution/api-operations.jsonl

Remarks:
    - --since オプションで期間指定（例: 2h, 3d, 1w）
    - --session-id でセッションフィルタリング可能

Changelog:
    - silenvx/dekita#1820: API操作ログ分析機能を追加
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Log file location
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent.parent
API_OPERATIONS_LOG = PROJECT_DIR / ".claude" / "logs" / "execution" / "api-operations.jsonl"


def parse_duration(duration_str: str) -> timedelta:
    """Parse duration string like '2h', '3d', '1w' into timedelta."""
    match = re.match(r"(\d+)\s*(h|d|w|m)?", duration_str.lower())
    if not match:
        raise ValueError(f"Invalid duration: {duration_str}")

    value = int(match.group(1))
    unit = match.group(2) or "d"

    if unit == "h":
        return timedelta(hours=value)
    elif unit == "d":
        return timedelta(days=value)
    elif unit == "w":
        return timedelta(weeks=value)
    elif unit == "m":
        return timedelta(minutes=value)
    else:
        return timedelta(days=value)


def load_operations(since: timedelta | None = None) -> list[dict[str, Any]]:
    """Load operations from the log file."""
    if not API_OPERATIONS_LOG.exists():
        return []

    operations = []
    cutoff = datetime.now(UTC) - since if since else None

    try:
        with open(API_OPERATIONS_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if cutoff:
                        ts = datetime.fromisoformat(entry.get("timestamp", ""))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=UTC)
                        if ts < cutoff:
                            continue
                    operations.append(entry)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return []

    return operations


def format_duration(ms: int | None) -> str:
    """Format duration in milliseconds to human-readable string."""
    if ms is None:
        return "N/A"
    if ms < 1000:
        return f"{ms}ms"
    elif ms < 60000:
        return f"{ms / 1000:.1f}s"
    else:
        return f"{ms / 60000:.1f}m"


def cmd_timeline(args: argparse.Namespace) -> None:
    """Show timeline of operations."""
    since = parse_duration(args.since) if args.since else timedelta(days=1)
    operations = load_operations(since)

    if args.session_id:
        operations = [op for op in operations if op.get("session_id") == args.session_id]

    if not operations:
        print("No operations found.")
        return

    print(f"\n{'Timestamp':<25} {'Type':<6} {'Operation':<20} {'Duration':<10} {'Status':<8}")
    print("-" * 75)

    for op in operations:
        ts = op.get("timestamp", "")[:19].replace("T", " ")
        op_type = op.get("type", "?")[:5]
        operation = op.get("operation", "?")[:19]
        duration = format_duration(op.get("duration_ms"))
        status = "✓" if op.get("success") else "✗"

        print(f"{ts:<25} {op_type:<6} {operation:<20} {duration:<10} {status:<8}")

    print(f"\nTotal: {len(operations)} operations")


def cmd_pr_lifecycle(args: argparse.Namespace) -> None:
    """Show PR lifecycle events."""
    operations = load_operations()

    pr_number = args.pr
    pr_ops = []

    for op in operations:
        # Check parsed.pr_number
        parsed_pr = op.get("parsed", {}).get("pr_number")
        result_pr = op.get("result", {}).get("number")
        result_type = op.get("result", {}).get("resource_type")

        if (
            parsed_pr == pr_number
            or (result_pr == pr_number and result_type == "pr")
            or f"pr/{pr_number}" in op.get("command", "").lower()
            or f"pull/{pr_number}" in op.get("result", {}).get("url", "")
        ):
            pr_ops.append(op)

    if not pr_ops:
        print(f"No operations found for PR #{pr_number}")
        return

    print(f"\n=== PR #{pr_number} Lifecycle ===\n")
    print(f"{'Timestamp':<25} {'Operation':<25} {'Duration':<10} {'Status'}")
    print("-" * 75)

    for op in pr_ops:
        ts = op.get("timestamp", "")[:19].replace("T", " ")
        operation = op.get("operation", "?")
        duration = format_duration(op.get("duration_ms"))
        status = "✓" if op.get("success") else "✗"

        print(f"{ts:<25} {operation:<25} {duration:<10} {status}")

    # Calculate total time
    if len(pr_ops) >= 2:
        first_ts = datetime.fromisoformat(pr_ops[0].get("timestamp", ""))
        last_ts = datetime.fromisoformat(pr_ops[-1].get("timestamp", ""))
        total_time = last_ts - first_ts
        print(f"\nTotal lifecycle time: {total_time}")


def cmd_issue_lifecycle(args: argparse.Namespace) -> None:
    """Show Issue lifecycle events."""
    operations = load_operations()

    issue_number = args.issue
    issue_ops = []

    for op in operations:
        # Check parsed.issue_number
        parsed_issue = op.get("parsed", {}).get("issue_number")
        result_issue = op.get("result", {}).get("number")
        result_type = op.get("result", {}).get("resource_type")

        if (
            parsed_issue == issue_number
            or (result_issue == issue_number and result_type == "issue")
            or f"issue/{issue_number}" in op.get("command", "").lower()
            or f"issues/{issue_number}" in op.get("result", {}).get("url", "")
        ):
            issue_ops.append(op)

    if not issue_ops:
        print(f"No operations found for Issue #{issue_number}")
        return

    print(f"\n=== Issue #{issue_number} Lifecycle ===\n")
    print(f"{'Timestamp':<25} {'Operation':<25} {'Duration':<10} {'Status'}")
    print("-" * 75)

    for op in issue_ops:
        ts = op.get("timestamp", "")[:19].replace("T", " ")
        operation = op.get("operation", "?")
        duration = format_duration(op.get("duration_ms"))
        status = "✓" if op.get("success") else "✗"

        print(f"{ts:<25} {operation:<25} {duration:<10} {status}")


def cmd_errors(args: argparse.Namespace) -> None:
    """Show failed operations."""
    since = parse_duration(args.since) if args.since else timedelta(days=7)
    operations = load_operations(since)

    errors = [op for op in operations if not op.get("success")]

    if not errors:
        print("No failed operations found.")
        return

    print("\n=== Failed Operations ===\n")
    print(f"{'Timestamp':<25} {'Type':<6} {'Operation':<20} {'Exit Code'}")
    print("-" * 60)

    for op in errors:
        ts = op.get("timestamp", "")[:19].replace("T", " ")
        op_type = op.get("type", "?")[:5]
        operation = op.get("operation", "?")[:19]
        exit_code = op.get("exit_code", "?")

        print(f"{ts:<25} {op_type:<6} {operation:<20} {exit_code}")

    # Group by operation type
    error_by_op: dict[str, int] = defaultdict(int)
    for op in errors:
        error_by_op[op.get("operation", "unknown")] += 1

    print("\n=== Error Summary by Operation ===")
    for operation, count in sorted(error_by_op.items(), key=lambda x: -x[1]):
        print(f"  {operation}: {count}")

    print(f"\nTotal: {len(errors)} failed operations")


def cmd_duration_stats(args: argparse.Namespace) -> None:
    """Show duration statistics."""
    since = parse_duration(args.since) if args.since else timedelta(days=7)
    operations = load_operations(since)

    # Group by operation
    durations: dict[str, list[int]] = defaultdict(list)

    for op in operations:
        duration = op.get("duration_ms")
        if duration is not None:
            key = f"{op.get('type', '?')}:{op.get('operation', '?')}"
            durations[key].append(duration)

    if not durations:
        print("No duration data available.")
        return

    print("\n=== Duration Statistics ===\n")
    print(f"{'Operation':<35} {'Count':<8} {'Avg':<10} {'Min':<10} {'Max':<10}")
    print("-" * 80)

    # Sort by average duration (slowest first)
    stats = []
    for op, times in durations.items():
        avg = sum(times) / len(times)
        stats.append((op, len(times), avg, min(times), max(times)))

    for op, count, avg, min_time, max_time in sorted(stats, key=lambda x: -x[2]):
        print(
            f"{op:<35} {count:<8} {format_duration(int(avg)):<10} "
            f"{format_duration(min_time):<10} {format_duration(max_time):<10}"
        )


def cmd_summary(args: argparse.Namespace) -> None:
    """Show summary of operations."""
    since = parse_duration(args.since) if args.since else timedelta(days=1)
    operations = load_operations(since)

    if not operations:
        print("No operations found.")
        return

    # Count by type
    by_type: dict[str, int] = defaultdict(int)
    by_operation: dict[str, int] = defaultdict(int)
    success_count = 0
    total_duration = 0
    duration_count = 0

    for op in operations:
        by_type[op.get("type", "unknown")] += 1
        by_operation[op.get("operation", "unknown")] += 1
        if op.get("success"):
            success_count += 1
        if op.get("duration_ms"):
            total_duration += op.get("duration_ms", 0)
            duration_count += 1

    print("\n=== API Operations Summary ===\n")
    print(f"Total operations: {len(operations)}")
    print(
        f"Success rate: {success_count}/{len(operations)} ({100 * success_count / len(operations):.1f}%)"
    )
    if duration_count > 0:
        print(f"Average duration: {format_duration(int(total_duration / duration_count))}")

    print("\n--- By Type ---")
    for op_type, count in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {op_type}: {count}")

    print("\n--- Top Operations ---")
    for operation, count in sorted(by_operation.items(), key=lambda x: -x[1])[:10]:
        print(f"  {operation}: {count}")

    # Show unique sessions
    sessions = set(op.get("session_id") for op in operations if op.get("session_id"))
    print("\n--- Sessions ---")
    print(f"  Unique sessions: {len(sessions)}")


def cmd_rate_limit_events(args: argparse.Namespace) -> None:
    """Show rate limit events recorded in api-operations.jsonl.

    Displays events logged by ci-monitor.py when GitHub API rate limits are encountered.
    """
    since = parse_duration(args.since) if args.since else timedelta(hours=1)
    operations = load_operations(since)

    # Filter rate_limit events
    events = [op for op in operations if op.get("type") == "rate_limit"]

    if not events:
        print(f"No rate limit events found in the last {args.since or '1h'}.")
        return

    print(f"\n=== Rate Limit Events (last {args.since or '1h'}) ===\n")
    print(f"{'Timestamp':<25} {'Operation':<20} {'Remaining/Limit':<15} {'Reset At'}")
    print("-" * 80)

    for event in events:
        ts = event.get("timestamp", "")[:19].replace("T", " ")
        operation = event.get("operation", "unknown")
        details = event.get("details", {})

        remaining = details.get("remaining", "?")
        limit = details.get("limit", "?")
        ratio = f"{remaining}/{limit}"

        # Format reset time
        reset_at = details.get("reset_at", "")
        if reset_at:
            # Extract time portion (HH:MM)
            reset_time = reset_at[11:16] if len(reset_at) >= 16 else reset_at
        else:
            reset_time = "?"

        print(f"{ts:<25} {operation:<20} {ratio:<15} {reset_time}")

    # Summary by event type
    event_type_counts: dict[str, int] = defaultdict(int)
    for event in events:
        event_type_counts[event.get("operation", "unknown")] += 1

    print("\n--- Summary by Event Type ---")
    for op_type, count in sorted(event_type_counts.items(), key=lambda x: -x[1]):
        print(f"  {op_type}: {count}")

    print(f"\nTotal: {len(events)} events")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze API operations log for development workflow insights."
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # timeline
    timeline_parser = subparsers.add_parser("timeline", help="Show timeline of operations")
    timeline_parser.add_argument("--session-id", help="Filter by session ID")
    timeline_parser.add_argument("--since", default="1d", help="Time range (e.g., 2h, 3d, 1w)")

    # pr-lifecycle
    pr_parser = subparsers.add_parser("pr-lifecycle", help="Show PR lifecycle events")
    pr_parser.add_argument("--pr", type=int, required=True, help="PR number")

    # issue-lifecycle
    issue_parser = subparsers.add_parser("issue-lifecycle", help="Show Issue lifecycle events")
    issue_parser.add_argument("--issue", type=int, required=True, help="Issue number")

    # errors
    errors_parser = subparsers.add_parser("errors", help="Show failed operations")
    errors_parser.add_argument("--since", default="7d", help="Time range (e.g., 2h, 3d, 1w)")

    # duration-stats
    duration_parser = subparsers.add_parser("duration-stats", help="Show duration statistics")
    duration_parser.add_argument("--since", default="7d", help="Time range (e.g., 2h, 3d, 1w)")

    # summary
    summary_parser = subparsers.add_parser("summary", help="Show summary of operations")
    summary_parser.add_argument("--since", default="1d", help="Time range (e.g., 2h, 3d, 1w)")

    # rate-limit-events (Issue #1523)
    rate_limit_parser = subparsers.add_parser("rate-limit-events", help="Show rate limit events")
    rate_limit_parser.add_argument("--since", default="1h", help="Time range (e.g., 1h, 6h, 1d)")

    args = parser.parse_args()

    if args.command == "timeline":
        cmd_timeline(args)
    elif args.command == "pr-lifecycle":
        cmd_pr_lifecycle(args)
    elif args.command == "issue-lifecycle":
        cmd_issue_lifecycle(args)
    elif args.command == "errors":
        cmd_errors(args)
    elif args.command == "duration-stats":
        cmd_duration_stats(args)
    elif args.command == "summary":
        cmd_summary(args)
    elif args.command == "rate-limit-events":
        cmd_rate_limit_events(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
