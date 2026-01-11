#!/usr/bin/env python3
"""レビュー品質メトリクスを分析する。

Why:
    AIレビュワー（Copilot/Codex）の有効性を評価し、
    カテゴリ別の妥当性を分析するため。

What:
    - summary: 全体統計を表示
    - --by-reviewer: レビュワー別統計
    - --by-category: カテゴリ別統計
    - --detail: 全レコード詳細表示

State:
    - reads: .claude/logs/metrics/review-quality-*.jsonl

Remarks:
    - --since/--until で日付フィルタリング可能
    - --json でJSON形式出力
    - lib.loggingモジュールを使用

Changelog:
    - silenvx/dekita#1800: レビュー品質分析機能を追加
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

# Import from hooks common module
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
from common import METRICS_LOG_DIR
from lib.logging import read_all_session_log_entries


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Analyze review quality metrics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--by-reviewer",
        action="store_true",
        help="Group statistics by reviewer type",
    )
    parser.add_argument(
        "--by-category",
        action="store_true",
        help="Group statistics by category",
    )
    parser.add_argument(
        "--since",
        help="Filter records since date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--until",
        help="Filter records until date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output as JSON",
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="Show all individual records",
    )

    return parser.parse_args()


def load_records() -> list[dict[str, Any]]:
    """Load all records from session-specific log files.

    Issue #2194: Migrated to read from session-specific files instead of global file.
    """
    return read_all_session_log_entries(METRICS_LOG_DIR, "review-quality")


def merge_records(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Merge records by comment_id, keeping the latest resolution.

    Returns a dict keyed by (pr_number, comment_id) with merged data.
    """
    merged: dict[str, dict[str, Any]] = {}

    for record in records:
        pr_number = record.get("pr_number", "unknown")
        comment_id = record.get("comment_id", "unknown")
        key = f"{pr_number}:{comment_id}"

        if key not in merged:
            merged[key] = record.copy()
        else:
            # Merge: response records override initial records
            if record.get("record_type") == "response":
                merged[key].update(record)
            else:
                # Initial record: only update if we don't have a response yet
                if merged[key].get("record_type") != "response":
                    merged[key].update(record)

    return merged


def filter_records(
    records: list[dict[str, Any]],
    since: str | None,
    until: str | None,
) -> list[dict[str, Any]]:
    """Filter records by date range."""
    filtered = []

    for record in records:
        timestamp = record.get("timestamp", "")
        if not timestamp:
            continue

        try:
            record_date = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            continue

        if since:
            since_date = datetime.fromisoformat(since + "T00:00:00+00:00")
            if record_date < since_date:
                continue

        if until:
            until_date = datetime.fromisoformat(until + "T23:59:59+00:00")
            if record_date > until_date:
                continue

        filtered.append(record)

    return filtered


def calculate_stats(merged: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Calculate overall statistics."""
    total = len(merged)
    with_resolution = [r for r in merged.values() if r.get("resolution")]

    resolution_counts = defaultdict(int)
    validity_counts = defaultdict(int)

    for record in with_resolution:
        resolution_counts[record.get("resolution", "unknown")] += 1
        validity_counts[record.get("validity", "unknown")] += 1

    return {
        "total_comments": total,
        "with_resolution": len(with_resolution),
        "pending_resolution": total - len(with_resolution),
        "resolution_breakdown": dict(resolution_counts),
        "validity_breakdown": dict(validity_counts),
    }


def calculate_by_reviewer(merged: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Calculate statistics grouped by reviewer."""
    by_reviewer: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for record in merged.values():
        reviewer = record.get("reviewer", "unknown")
        by_reviewer[reviewer].append(record)

    stats = {}
    for reviewer, records in by_reviewer.items():
        with_resolution = [r for r in records if r.get("resolution")]
        accepted = sum(1 for r in with_resolution if r.get("resolution") == "accepted")
        valid = sum(1 for r in with_resolution if r.get("validity") == "valid")
        invalid = sum(1 for r in with_resolution if r.get("validity") == "invalid")
        partial = sum(1 for r in with_resolution if r.get("validity") == "partially_valid")

        total_with_res = len(with_resolution) or 1  # Avoid division by zero
        stats[reviewer] = {
            "total": len(records),
            "with_resolution": len(with_resolution),
            "accepted": accepted,
            "acceptance_rate": round(accepted / total_with_res * 100, 1),
            "valid": valid,
            "invalid": invalid,
            "partially_valid": partial,
            "validity_rate": round(valid / total_with_res * 100, 1),
        }

    return stats


def calculate_by_category(merged: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Calculate statistics grouped by category."""
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for record in merged.values():
        category = record.get("category", "other")
        by_category[category].append(record)

    stats = {}
    for category, records in by_category.items():
        with_resolution = [r for r in records if r.get("resolution")]
        accepted = sum(1 for r in with_resolution if r.get("resolution") == "accepted")
        valid = sum(1 for r in with_resolution if r.get("validity") == "valid")

        total_with_res = len(with_resolution) or 1  # Avoid division by zero
        stats[category] = {
            "total": len(records),
            "with_resolution": len(with_resolution),
            "accepted": accepted,
            "acceptance_rate": round(accepted / total_with_res * 100, 1),
            "valid": valid,
            "validity_rate": round(valid / total_with_res * 100, 1),
        }

    return stats


def print_summary(stats: dict[str, Any]) -> None:
    """Print summary statistics."""
    print("=" * 50)
    print("レビュー品質分析")
    print("=" * 50)
    print()
    print(f"総コメント数: {stats['total_comments']}")
    print(f"  - 対応記録あり: {stats['with_resolution']}")
    print(f"  - 対応待ち: {stats['pending_resolution']}")
    print()

    if stats["resolution_breakdown"]:
        print("【対応内訳】")
        for resolution, count in sorted(stats["resolution_breakdown"].items()):
            print(f"  {resolution}: {count}")
        print()

    if stats["validity_breakdown"]:
        print("【妥当性内訳】")
        for validity, count in sorted(stats["validity_breakdown"].items()):
            print(f"  {validity}: {count}")


def print_by_reviewer(stats: dict[str, dict[str, Any]]) -> None:
    """Print statistics grouped by reviewer."""
    print()
    print("【レビュアー別採用率】")
    for reviewer, data in sorted(stats.items()):
        if data["with_resolution"] > 0:
            print(
                f"  {reviewer:12s}: {data['acceptance_rate']:5.1f}% "
                f"({data['accepted']}/{data['with_resolution']}) - "
                f"valid: {data['validity_rate']:.0f}%, "
                f"invalid: {round(data['invalid'] / (data['with_resolution'] or 1) * 100):.0f}%, "
                f"partial: {round(data['partially_valid'] / (data['with_resolution'] or 1) * 100):.0f}%"
            )
        else:
            print(f"  {reviewer:12s}: 対応記録なし ({data['total']} 件)")


def print_by_category(stats: dict[str, dict[str, Any]]) -> None:
    """Print statistics grouped by category."""
    print()
    print("【カテゴリ別妥当性】")
    for category, data in sorted(stats.items()):
        if data["with_resolution"] > 0:
            print(
                f"  {category:12s}: {data['validity_rate']:5.1f}% "
                f"({data['valid']}/{data['with_resolution']})"
            )
        else:
            print(f"  {category:12s}: 対応記録なし ({data['total']} 件)")


def print_detail(merged: dict[str, dict[str, Any]]) -> None:
    """Print all individual records."""
    print()
    print("【詳細レコード】")
    for _, record in sorted(merged.items()):
        pr = record.get("pr_number", "?")
        cid = record.get("comment_id", "?")
        reviewer = record.get("reviewer", "?")
        category = record.get("category", "?")
        resolution = record.get("resolution", "-")
        validity = record.get("validity", "-")
        preview = record.get("body_preview", "")[:50]

        print(f"  PR#{pr} [{cid}] {reviewer}/{category}")
        print(f"    Resolution: {resolution}, Validity: {validity}")
        if preview:
            print(f"    Preview: {preview}...")
        print()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Load and filter records
    records = load_records()
    if not records:
        if args.json_output:
            print(json.dumps({"error": "No records found"}))
        else:
            print("レビュー品質ログが見つかりません。")
            print(f"ログディレクトリ: {METRICS_LOG_DIR}")
        return 0

    # Filter by date
    if args.since or args.until:
        records = filter_records(records, args.since, args.until)
        if not records:
            if args.json_output:
                print(json.dumps({"error": "No records found in date range"}))
            else:
                print("指定された期間にレコードが見つかりません。")
            return 0

    # Merge records
    merged = merge_records(records)

    # Calculate statistics
    overall_stats = calculate_stats(merged)
    reviewer_stats = calculate_by_reviewer(merged)
    category_stats = calculate_by_category(merged)

    # Output
    if args.json_output:
        output = {
            "overall": overall_stats,
            "by_reviewer": reviewer_stats,
            "by_category": category_stats,
        }
        if args.detail:
            output["records"] = list(merged.values())
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print_summary(overall_stats)

        if args.by_reviewer or (not args.by_category and not args.detail):
            print_by_reviewer(reviewer_stats)

        if args.by_category or (not args.by_reviewer and not args.detail):
            print_by_category(category_stats)

        if args.detail:
            print_detail(merged)

    return 0


if __name__ == "__main__":
    sys.exit(main())
