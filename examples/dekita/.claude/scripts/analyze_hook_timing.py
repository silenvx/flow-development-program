#!/usr/bin/env python3
"""フック実行時間を分析する。

Why:
    フックのパフォーマンスボトルネックを特定し、
    遅いフックを改善するためのデータが必要。

What:
    - parse_log_entries(): 実行ログをパース
    - analyze_hooks(): フック実行統計を計算
    - show_slow_hooks(): 遅いフックを表示

State:
    - reads: .claude/logs/session/*/hook-execution-*.jsonl

Remarks:
    - --top N で上位N件の遅いフックを表示（デフォルト: 10）
    - --slow MS で「遅い」の閾値を指定（デフォルト: 100ms）
    - --session-id でセッションフィルタリング可能

Changelog:
    - silenvx/dekita#1882: フック実行時間分析機能を追加
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Add hooks directory to path for lib imports
SCRIPT_DIR = Path(__file__).parent
HOOKS_DIR = SCRIPT_DIR.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
from lib.logging import read_all_session_log_entries, read_session_log_entries


def parse_log_entries(log_dir: Path, session_id: str | None = None) -> list[dict]:
    """Parse hook execution log files.

    Args:
        log_dir: Directory containing session-specific log files.
        session_id: Optional session ID to filter by.

    Returns:
        List of log entries.
    """
    if session_id:
        return read_session_log_entries(log_dir, "hook-execution", session_id)
    return read_all_session_log_entries(log_dir, "hook-execution")


def analyze_hooks(
    entries: list[dict],
    session_id: str | None = None,
) -> dict:
    """Analyze hook execution data."""
    # Filter by session if specified
    if session_id:
        entries = [e for e in entries if e.get("session_id") == session_id]

    # Collect statistics per hook
    hook_stats: dict[str, dict] = defaultdict(
        lambda: {
            "count": 0,
            "block_count": 0,
            "durations": [],
            "decisions": defaultdict(int),
        }
    )

    for entry in entries:
        hook_name = entry.get("hook", "unknown")
        decision = entry.get("decision", "unknown")
        duration_ms = entry.get("duration_ms")

        stats = hook_stats[hook_name]
        stats["count"] += 1
        stats["decisions"][decision] += 1

        if decision == "block":
            stats["block_count"] += 1

        if duration_ms is not None:
            stats["durations"].append(duration_ms)

    # Calculate summary statistics
    results = {}
    for hook_name, stats in hook_stats.items():
        durations = stats["durations"]
        result = {
            "hook": hook_name,
            "count": stats["count"],
            "block_count": stats["block_count"],
            "block_rate": (
                round(stats["block_count"] / stats["count"] * 100, 1) if stats["count"] > 0 else 0
            ),
            "has_timing": len(durations) > 0,
        }

        if durations:
            result["timing"] = {
                "min_ms": min(durations),
                "max_ms": max(durations),
                "avg_ms": round(sum(durations) / len(durations), 1),
                "total_ms": sum(durations),
                "samples": len(durations),
            }

        results[hook_name] = result

    return results


def print_report(
    results: dict,
    top_n: int = 10,
    slow_threshold_ms: int = 100,
    output_json: bool = False,
) -> None:
    """Print analysis report."""
    if output_json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    # Sort by execution count
    sorted_by_count = sorted(
        results.values(),
        key=lambda x: x["count"],
        reverse=True,
    )

    # Sort by average duration (if available)
    hooks_with_timing = [r for r in results.values() if r.get("has_timing")]
    sorted_by_duration = sorted(
        hooks_with_timing,
        key=lambda x: x["timing"]["avg_ms"],
        reverse=True,
    )

    print("=" * 60)
    print("フック実行時間分析レポート")
    print("=" * 60)
    print()

    # Summary
    total_hooks = len(results)
    total_executions = sum(r["count"] for r in results.values())
    hooks_with_timing_count = len(hooks_with_timing)

    print(f"総フック数: {total_hooks}")
    print(f"総実行回数: {total_executions}")
    print(f"タイミング記録あり: {hooks_with_timing_count}/{total_hooks}")
    print()

    # Top N by execution count
    print(f"--- 実行回数 Top {top_n} ---")
    print(f"{'フック名':<40} {'回数':>6} {'ブロック率':>8}")
    print("-" * 60)
    for r in sorted_by_count[:top_n]:
        print(f"{r['hook']:<40} {r['count']:>6} {r['block_rate']:>7.1f}%")
    print()

    # Slow hooks (if timing data available)
    if sorted_by_duration:
        print(f"--- 遅いフック Top {top_n} (平均実行時間) ---")
        print(f"{'フック名':<40} {'平均(ms)':>8} {'最大(ms)':>8} {'回数':>6}")
        print("-" * 60)
        for r in sorted_by_duration[:top_n]:
            t = r["timing"]
            print(f"{r['hook']:<40} {t['avg_ms']:>8.1f} {t['max_ms']:>8.1f} {t['samples']:>6}")
        print()

        # Hooks exceeding threshold
        slow_hooks = [r for r in hooks_with_timing if r["timing"]["avg_ms"] > slow_threshold_ms]
        if slow_hooks:
            print(f"⚠️ 警告: 平均{slow_threshold_ms}ms超のフック: {len(slow_hooks)}個")
            for r in sorted(slow_hooks, key=lambda x: x["timing"]["avg_ms"], reverse=True):
                print(f"  - {r['hook']}: {r['timing']['avg_ms']:.1f}ms")
            print()
    else:
        print("📊 タイミングデータがありません。")
        print("   フックでduration_msパラメータを使用すると、実行時間が記録されます。")
        print()

    # High block rate hooks
    high_block_hooks = [r for r in results.values() if r["block_rate"] > 20 and r["count"] >= 5]
    if high_block_hooks:
        print("--- ブロック率が高いフック (>20%, 5回以上実行) ---")
        for r in sorted(high_block_hooks, key=lambda x: x["block_rate"], reverse=True):
            print(f"  - {r['hook']}: {r['block_rate']:.1f}% ({r['block_count']}/{r['count']})")
        print()


def main():
    parser = argparse.ArgumentParser(description="Analyze hook execution timing")
    parser.add_argument("--top", type=int, default=10, help="Show top N results")
    parser.add_argument("--slow", type=int, default=100, help="Slow threshold in milliseconds")
    parser.add_argument("--session-id", type=str, help="Filter by session ID")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    # Find log directory
    project_dir = Path(__file__).parent.parent.parent
    log_dir = project_dir / ".claude" / "logs" / "execution"

    if not log_dir.exists():
        print(f"ログディレクトリが見つかりません: {log_dir}", file=sys.stderr)
        sys.exit(1)

    # Read entries (optionally filtered by session)
    entries = parse_log_entries(log_dir, session_id=args.session_id)
    if not entries:
        print("ログエントリがありません。", file=sys.stderr)
        sys.exit(1)

    # Note: session filtering is now done in parse_log_entries
    results = analyze_hooks(entries, session_id=None)
    print_report(
        results,
        top_n=args.top,
        slow_threshold_ms=args.slow,
        output_json=args.json,
    )


if __name__ == "__main__":
    main()
