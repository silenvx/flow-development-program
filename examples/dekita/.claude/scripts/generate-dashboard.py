#!/usr/bin/env python3
"""開発フローログからHTMLダッシュボードを生成する。

Why:
    フック実行統計・セッションメトリクスを可視化し、
    改善ポイントを視覚的に把握するため。

What:
    - collect_data(): ダッシュボードデータを収集
    - generate_html(): HTMLダッシュボードを生成

State:
    - reads: .claude/logs/session/*/*.jsonl
    - writes: .claude/logs/dashboard.html

Remarks:
    - --days N で集計期間を指定（デフォルト: 7日）
    - --open で生成後にブラウザで開く

Changelog:
    - silenvx/dekita#1367: ダッシュボード生成機能を追加
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

# Add scripts directory to path
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from dashboard.data_collector import DashboardDataCollector
from dashboard.html_generator import generate_dashboard_html


def main() -> None:
    """メイン処理"""
    parser = argparse.ArgumentParser(
        description="Generate development flow dashboard",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to analyze (default: 7)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file path (default: .claude/logs/dashboard.html)",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open dashboard in browser after generation",
    )
    args = parser.parse_args()

    # データ収集
    print(f"Collecting data for the last {args.days} days...")
    collector = DashboardDataCollector()

    kpis = collector.get_summary_kpis(args.days)
    api_trend = collector.get_api_success_rate_trend(args.days)
    block_trend = collector.get_block_rate_trend(args.days)
    rework_trend = collector.get_rework_events_trend(args.days)
    phase_durations = collector.get_phase_durations(args.days)
    ci_failures = collector.get_ci_failures(args.days)

    print(f"  API Success Rate: {kpis['api_success_rate']}%")
    print(f"  Block Rate: {kpis['block_rate']}%")
    print(f"  Total Reworks: {kpis['total_reworks']}")

    # HTML生成
    print("Generating dashboard HTML...")
    html = generate_dashboard_html(
        kpis=kpis,
        api_trend=api_trend,
        block_trend=block_trend,
        rework_trend=rework_trend,
        phase_durations=phase_durations,
        ci_failures=ci_failures,
    )

    # 出力
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = collector.logs_dir / "dashboard.html"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard saved to: {output_path}")

    # ブラウザで開く (cross-platform using webbrowser module)
    if args.open:
        try:
            # Convert to file:// URL for cross-platform compatibility
            file_url = output_path.resolve().as_uri()
            webbrowser.open(file_url)
            print("Opened in browser")
        except Exception as e:
            print(f"Failed to open browser: {e}")


if __name__ == "__main__":
    main()
