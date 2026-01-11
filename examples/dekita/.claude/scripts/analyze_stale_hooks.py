#!/usr/bin/env python3
"""フックの有効性を分析し陳腐化候補を特定する。

Why:
    不要なフックを特定・削除するため、
    承認率・ブロック頻度・経過時間を分析する。

What:
    - get_hook_files(): フックファイル一覧を取得
    - get_file_created_at(): git logから作成日を取得
    - analyze_effectiveness(): 有効性スコアを計算
    - identify_stale(): 陳腐化候補を特定

State:
    - reads: .claude/hooks/*.py (hook files)
    - reads: .claude/logs/execution/hook-execution-*.jsonl

Remarks:
    - 95%以上の承認率 → 陳腐化候補
    - 4週間以上ブロックなし → 陳腐化候補
    - 90日以上経過 → レビュー推奨
    - metadata.json廃止に伴い、git logから作成日を取得

Changelog:
    - silenvx/dekita#1400: 陳腐化フック分析機能を追加
    - silenvx/dekita#2762: metadata.json依存を削除、git logベースに移行
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add hooks directory to path for imports
SCRIPTS_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPTS_DIR.parent.parent
HOOKS_DIR = PROJECT_DIR / ".claude" / "hooks"
LOGS_DIR = PROJECT_DIR / ".claude" / "logs" / "execution"

sys.path.insert(0, str(HOOKS_DIR))

from lib.logging import read_all_session_log_entries

# Stale detection thresholds
APPROVE_RATE_THRESHOLD = 0.95  # 95%+ approve rate = potentially stale
MIN_TRIGGERS_FOR_ANALYSIS = 10  # Need at least 10 triggers for meaningful analysis
CONSECUTIVE_WEEKS_THRESHOLD = 2  # High approve rate for 2+ weeks = stale candidate
NO_BLOCK_WEEKS_THRESHOLD = 4  # No blocks for 4+ weeks = stale candidate
AGE_DAYS_THRESHOLD = 90  # Hooks older than 90 days need review

# Utility files to exclude from analysis
EXCLUDE_FILES = {
    "common",
    "check_utils",
    "command_parser",
    "guard_rules",
    "__init__",
}


def get_hook_files() -> list[Path]:
    """Get list of hook Python files."""
    files = []
    for py_file in HOOKS_DIR.glob("*.py"):
        if py_file.stem in EXCLUDE_FILES:
            continue
        files.append(py_file)
    return sorted(files)


def get_file_created_at(file_path: Path) -> datetime | None:
    """Get file creation date from git log."""
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--diff-filter=A",
                "--format=%aI",
                "--",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_DIR,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Get the first (oldest) commit that added this file
            lines = result.stdout.strip().split("\n")
            if lines:
                return datetime.fromisoformat(lines[-1])
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, ValueError):
        # git log errors or date parse failures: treat as unknown creation date
        pass
    return None


def load_execution_logs(weeks: int = 4) -> list[dict]:
    """Load hook execution logs for the specified number of weeks."""
    logs = []
    cutoff = datetime.now(UTC) - timedelta(weeks=weeks)

    # Read from session-specific log files
    entries = read_all_session_log_entries(LOGS_DIR, "hook-execution")

    for entry in entries:
        ts_str = entry.get("timestamp", "")
        if ts_str:
            try:
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                ts = datetime.fromisoformat(ts_str)
                if ts >= cutoff:
                    entry["_parsed_timestamp"] = ts
                    iso = ts.isocalendar()
                    entry["_week"] = (iso[0], iso[1])  # (year, week) for proper ordering
                    logs.append(entry)
            except ValueError:
                continue

    return logs


def calculate_weekly_stats(logs: list[dict]) -> dict[str, dict[tuple[int, int], dict]]:
    """Calculate per-hook, per-week statistics."""
    stats: dict[str, dict[tuple[int, int], dict]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "total": 0,
                "approve": 0,
                "block": 0,
                "advice": 0,
            }
        )
    )

    for entry in logs:
        hook = entry.get("hook", "unknown")
        week = entry.get("_week", (0, 0))  # (year, week) tuple
        decision = entry.get("decision", "approve")

        stats[hook][week]["total"] += 1
        if decision == "approve":
            stats[hook][week]["approve"] += 1
        elif decision == "block":
            stats[hook][week]["block"] += 1
        elif decision == "advice":
            stats[hook][week]["advice"] += 1

    return stats


def analyze_stale_candidates(
    hook_files: list[Path],
    weekly_stats: dict[str, dict[tuple[int, int], dict]],
) -> list[dict]:
    """Identify stale candidate hooks based on analysis criteria."""
    candidates = []
    today = datetime.now(UTC).date()

    for hook_file in hook_files:
        hook_name = hook_file.stem
        reasons = []
        severity = "low"

        # Check age from git log
        created_at = get_file_created_at(hook_file)
        if created_at:
            age_days = (today - created_at.date()).days
            if age_days > AGE_DAYS_THRESHOLD:
                reasons.append(f"Created {age_days} days ago (>{AGE_DAYS_THRESHOLD} days)")

        # Check execution statistics
        hook_stats = weekly_stats.get(hook_name, {})
        if hook_stats:
            weeks_data = sorted(hook_stats.items(), reverse=True)

            # Calculate overall stats
            total_triggers = sum(w["total"] for _, w in weeks_data)
            total_approves = sum(w["approve"] for _, w in weeks_data)

            if total_triggers >= MIN_TRIGGERS_FOR_ANALYSIS:
                approve_rate = total_approves / total_triggers if total_triggers > 0 else 0

                # Check high approve rate
                if approve_rate >= APPROVE_RATE_THRESHOLD:
                    # Check if consecutive weeks have high approve rate
                    high_approve_weeks = 0
                    for _, week_stats in weeks_data[:CONSECUTIVE_WEEKS_THRESHOLD]:
                        if week_stats["total"] > 0:
                            week_approve_rate = week_stats["approve"] / week_stats["total"]
                            if week_approve_rate >= APPROVE_RATE_THRESHOLD:
                                high_approve_weeks += 1

                    if high_approve_weeks >= CONSECUTIVE_WEEKS_THRESHOLD:
                        reasons.append(
                            f"High approve rate ({approve_rate:.1%}) for {high_approve_weeks}+ consecutive weeks"
                        )
                        severity = "high"

                # Check no blocks for extended period
                weeks_without_block = 0
                for _, week_stats in weeks_data:
                    if week_stats["block"] == 0:
                        weeks_without_block += 1
                    else:
                        break

                if weeks_without_block >= NO_BLOCK_WEEKS_THRESHOLD:
                    reasons.append(f"No blocks for {weeks_without_block}+ weeks")
                    if severity != "high":
                        severity = "medium"
        else:
            # No execution data - hook may not be registered in settings.json
            reasons.append("No execution data found (hook may not be registered)")

        if reasons:
            candidates.append(
                {
                    "hook": hook_name,
                    "path": str(hook_file.relative_to(PROJECT_DIR)),
                    "severity": severity,
                    "reasons": reasons,
                    "created_at": created_at.isoformat() if created_at else None,
                    "stats": {
                        "total_triggers": sum(w["total"] for w in hook_stats.values())
                        if hook_stats
                        else 0,
                        "total_blocks": sum(w["block"] for w in hook_stats.values())
                        if hook_stats
                        else 0,
                    },
                }
            )

    # Sort by severity
    severity_order = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda x: severity_order.get(x["severity"], 3))

    return candidates


def format_markdown(candidates: list[dict], all_stats: dict) -> str:
    """Format analysis results as markdown."""
    lines = [
        "# Hook Stale Analysis Report",
        f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    if not candidates:
        lines.append("No stale candidates identified.")
        return "\n".join(lines)

    # Summary
    high_severity = len([c for c in candidates if c["severity"] == "high"])
    medium_severity = len([c for c in candidates if c["severity"] == "medium"])
    low_severity = len([c for c in candidates if c["severity"] == "low"])

    lines.extend(
        [
            "## Summary",
            f"- **High severity**: {high_severity}",
            f"- **Medium severity**: {medium_severity}",
            f"- **Low severity**: {low_severity}",
            "",
            "## Stale Candidates",
            "",
        ]
    )

    for candidate in candidates:
        severity_emoji = {
            "high": "🔴",  # red circle
            "medium": "🟠",  # orange circle
            "low": "🟡",  # yellow circle
        }.get(candidate["severity"], "⚪")  # white circle

        lines.extend(
            [
                f"### {severity_emoji} {candidate['hook']}",
                f"**Path**: {candidate['path']}",
                f"**Triggers**: {candidate['stats']['total_triggers']} | **Blocks**: {candidate['stats']['total_blocks']}",
            ]
        )
        if candidate.get("created_at"):
            lines.append(f"**Created**: {candidate['created_at'][:10]}")
        lines.append("")
        lines.append("**Reasons**:")
        for reason in candidate["reasons"]:
            lines.append(f"- {reason}")

        lines.extend(
            [
                "",
                "**Recommended Actions**:",
            ]
        )

        if candidate["severity"] == "high":
            lines.append("- [ ] Review and consider disabling or removing")
            lines.append("- [ ] Evaluate if the hook's purpose is still relevant")
        elif candidate["severity"] == "medium":
            lines.append("- [ ] Consider adjusting detection criteria")
        else:
            lines.append("- [ ] Schedule review for next sprint")

        lines.append("")

    return "\n".join(lines)


def format_json(candidates: list[dict], all_stats: dict) -> str:
    """Format analysis results as JSON."""
    output = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_candidates": len(candidates),
            "high_severity": len([c for c in candidates if c["severity"] == "high"]),
            "medium_severity": len([c for c in candidates if c["severity"] == "medium"]),
            "low_severity": len([c for c in candidates if c["severity"] == "low"]),
        },
        "candidates": candidates,
    }
    return json.dumps(output, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze hook effectiveness and identify stale candidates"
    )
    parser.add_argument(
        "--output", choices=["json", "markdown"], default="markdown", help="Output format"
    )
    parser.add_argument("--weeks", type=int, default=4, help="Number of weeks to analyze")
    args = parser.parse_args()

    # Load data
    hook_files = get_hook_files()
    logs = load_execution_logs(args.weeks)

    # Analyze
    weekly_stats = calculate_weekly_stats(logs)
    candidates = analyze_stale_candidates(hook_files, weekly_stats)

    # Output
    if args.output == "json":
        print(format_json(candidates, weekly_stats))
    else:
        print(format_markdown(candidates, weekly_stats))


if __name__ == "__main__":
    main()
