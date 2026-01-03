#!/usr/bin/env python3
"""セッション成果データを分析する。

Why:
    セッションの生産性とタスク種別の分布を把握し、
    改善ポイントを特定するため。

What:
    - load_outcomes(): 成果ログを読み込み
    - show_sessions(): セッション一覧を表示
    - show_summary(): 統計サマリーを表示

State:
    - reads: .claude/logs/outcomes/session-outcomes.jsonl

Remarks:
    - --days N で直近N日間にフィルタリング
    - --summary で統計サマリーを表示

Changelog:
    - silenvx/dekita#1158: セッション成果分析機能を追加
"""

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Outcome log file
OUTCOME_LOG_FILE = Path(__file__).parent.parent / "logs" / "outcomes" / "session-outcomes.jsonl"


def load_outcomes(days: int | None = None) -> list[dict]:
    """Load session outcomes from log file.

    Args:
        days: If specified, only load outcomes from the last N days

    Returns:
        List of outcome dicts
    """
    if not OUTCOME_LOG_FILE.exists():
        return []

    cutoff = None
    if days:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

    outcomes = []
    try:
        with open(OUTCOME_LOG_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if cutoff and entry.get("timestamp", "") < cutoff:
                        continue
                    outcomes.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        # File read error - return whatever outcomes we've collected so far
        pass

    return outcomes


def format_session(outcome: dict) -> str:
    """Format a single session outcome for display.

    Args:
        outcome: Session outcome dict

    Returns:
        Formatted string
    """
    lines = []

    # Header with timestamp and task type
    timestamp = outcome.get("timestamp", "")[:19]  # Truncate to seconds
    task_type = outcome.get("task_type", "unknown")
    session_id = outcome.get("session_id", "")[:8]

    lines.append(f"[{timestamp}] {task_type} (session: {session_id})")

    # PRs
    prs_merged = outcome.get("prs_merged", [])
    prs_created = outcome.get("prs_created", [])
    if prs_merged:
        pr_list = ", ".join(f"#{pr['number']}" for pr in prs_merged)
        lines.append(f"  Merged PRs: {pr_list}")
    if prs_created:
        pr_list = ", ".join(f"#{pr['number']}" for pr in prs_created)
        lines.append(f"  Created PRs: {pr_list}")

    # Issues
    issues_created = outcome.get("issues_created", [])
    if issues_created:
        issue_list = ", ".join(f"#{issue['number']}" for issue in issues_created)
        lines.append(f"  Created Issues: {issue_list}")

    # Commits
    commits_count = outcome.get("commits_count", 0)
    if commits_count > 0:
        lines.append(f"  Commits: {commits_count}")

    return "\n".join(lines)


def format_summary(outcomes: list[dict]) -> str:
    """Format summary statistics for outcomes.

    Args:
        outcomes: List of session outcome dicts

    Returns:
        Formatted summary string
    """
    if not outcomes:
        return "No session outcomes found."

    lines = ["Session Outcome Summary", "=" * 40]

    # Task type distribution
    task_types = Counter(o.get("task_type", "unknown") for o in outcomes)
    lines.append("\nTask Type Distribution:")
    for task_type, count in task_types.most_common():
        percentage = count / len(outcomes) * 100
        lines.append(f"  {task_type}: {count} ({percentage:.1f}%)")

    # Totals
    total_prs_merged = sum(len(o.get("prs_merged", [])) for o in outcomes)
    total_prs_created = sum(len(o.get("prs_created", [])) for o in outcomes)
    total_issues_created = sum(len(o.get("issues_created", [])) for o in outcomes)
    total_commits = sum(o.get("commits_count", 0) for o in outcomes)

    lines.append("\nTotals:")
    lines.append(f"  Sessions: {len(outcomes)}")
    lines.append(f"  PRs Merged: {total_prs_merged}")
    lines.append(f"  PRs Created: {total_prs_created}")
    lines.append(f"  Issues Created: {total_issues_created}")
    lines.append(f"  Commits: {total_commits}")

    # Averages
    if len(outcomes) > 0:
        lines.append("\nAverages per Session:")
        lines.append(f"  PRs Merged: {total_prs_merged / len(outcomes):.1f}")
        lines.append(f"  Commits: {total_commits / len(outcomes):.1f}")

    return "\n".join(lines)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze session outcome data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Show sessions from the last N days (default: all)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Show summary statistics instead of individual sessions",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Limit number of sessions to show (default: 10)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    args = parser.parse_args()

    # Load outcomes
    outcomes = load_outcomes(days=args.days)

    if not outcomes:
        print("No session outcomes found.")
        print(f"Outcome log file: {OUTCOME_LOG_FILE}")
        sys.exit(0)

    if args.json:
        # Output as JSON
        if args.summary:
            task_types = Counter(o.get("task_type", "unknown") for o in outcomes)
            summary = {
                "total_sessions": len(outcomes),
                "task_type_distribution": dict(task_types),
                "total_prs_merged": sum(len(o.get("prs_merged", [])) for o in outcomes),
                "total_prs_created": sum(len(o.get("prs_created", [])) for o in outcomes),
                "total_issues_created": sum(len(o.get("issues_created", [])) for o in outcomes),
                "total_commits": sum(o.get("commits_count", 0) for o in outcomes),
            }
            print(json.dumps(summary, indent=2))
        else:
            recent = outcomes[-args.limit :]
            print(json.dumps(recent, indent=2, ensure_ascii=False))
    elif args.summary:
        print(format_summary(outcomes))
    else:
        # Show individual sessions (most recent first)
        recent = outcomes[-args.limit :]
        recent.reverse()

        print(f"Session Outcomes (showing {len(recent)} of {len(outcomes)})")
        print("-" * 50)
        for outcome in recent:
            print(format_session(outcome))
            print()


if __name__ == "__main__":
    main()
