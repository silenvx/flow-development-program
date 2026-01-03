#!/usr/bin/env python3
"""
ダッシュボードHTML生成モジュール

Chart.jsを使用して静的なHTMLダッシュボードを生成する。

Issue #1367: 開発フローログの可視化
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from typing import Any


def generate_dashboard_html(
    kpis: dict[str, Any],
    api_trend: list[dict[str, Any]],
    block_trend: list[dict[str, Any]],
    rework_trend: list[dict[str, Any]],
    phase_durations: dict[str, float],
    ci_failures: list[dict[str, Any]],
) -> str:
    """ダッシュボードHTMLを生成

    Args:
        kpis: サマリーKPI
        api_trend: API成功率トレンド
        block_trend: ブロック率トレンド
        rework_trend: 手戻りイベントトレンド
        phase_durations: フェーズ滞在時間
        ci_failures: CI失敗リスト

    Returns:
        HTML文字列
    """
    # Chart.js用のデータを生成
    api_labels = json.dumps([d["date"] for d in api_trend])
    api_data = json.dumps([d["success_rate"] for d in api_trend])

    block_labels = json.dumps([d["date"] for d in block_trend])
    block_data = json.dumps([d["block_rate"] for d in block_trend])

    rework_labels = json.dumps([d["date"] for d in rework_trend])
    rework_data = json.dumps([d["count"] for d in rework_trend])

    phase_labels = json.dumps(list(phase_durations.keys()))
    phase_data = json.dumps(list(phase_durations.values()))

    # CI失敗テーブルのHTML
    ci_failures_rows = ""
    for failure in ci_failures:
        # Escape user-controllable data to prevent XSS
        safe_timestamp = html.escape(failure["timestamp"][:19])
        safe_command = html.escape(failure["command"][:50])
        safe_exit_code = html.escape(str(failure.get("exit_code", "N/A")))
        ci_failures_rows += f"""
            <tr>
                <td class="px-4 py-2 text-sm">{safe_timestamp}</td>
                <td class="px-4 py-2 text-sm font-mono">{safe_command}...</td>
                <td class="px-4 py-2 text-sm">{safe_exit_code}</td>
            </tr>
        """

    if not ci_failures_rows:
        ci_failures_rows = """
            <tr>
                <td colspan="3" class="px-4 py-8 text-center text-gray-500">
                    No CI failures in the selected period
                </td>
            </tr>
        """

    # KPIカードの色分け
    api_color = (
        "green"
        if kpis["api_success_rate"] >= 95
        else "yellow"
        if kpis["api_success_rate"] >= 80
        else "red"
    )
    block_color = (
        "green" if kpis["block_rate"] <= 5 else "yellow" if kpis["block_rate"] <= 15 else "red"
    )

    # Use UTC for consistent timezone handling
    generated_time = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Development Flow Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body class="bg-gray-100 min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <!-- Header -->
        <div class="mb-8">
            <h1 class="text-3xl font-bold text-gray-800">Development Flow Dashboard</h1>
            <p class="text-gray-600">Generated: {generated_time} | Period: Last {kpis["days"]} days</p>
        </div>

        <!-- KPI Cards -->
        <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
            <div class="bg-white rounded-lg shadow p-6">
                <div class="text-sm text-gray-500 mb-1">API Success Rate</div>
                <div class="text-3xl font-bold text-{api_color}-600">{kpis["api_success_rate"]}%</div>
            </div>
            <div class="bg-white rounded-lg shadow p-6">
                <div class="text-sm text-gray-500 mb-1">Block Rate</div>
                <div class="text-3xl font-bold text-{block_color}-600">{kpis["block_rate"]}%</div>
            </div>
            <div class="bg-white rounded-lg shadow p-6">
                <div class="text-sm text-gray-500 mb-1">Total Reworks</div>
                <div class="text-3xl font-bold text-gray-800">{kpis["total_reworks"]}</div>
            </div>
            <div class="bg-white rounded-lg shadow p-6">
                <div class="text-sm text-gray-500 mb-1">Days Analyzed</div>
                <div class="text-3xl font-bold text-gray-800">{kpis["days"]}</div>
            </div>
        </div>

        <!-- Charts Row 1 -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
            <div class="bg-white rounded-lg shadow p-6">
                <h2 class="text-lg font-semibold mb-4">API Success Rate Trend</h2>
                <canvas id="apiChart"></canvas>
            </div>
            <div class="bg-white rounded-lg shadow p-6">
                <h2 class="text-lg font-semibold mb-4">Block Rate Trend</h2>
                <canvas id="blockChart"></canvas>
            </div>
        </div>

        <!-- Charts Row 2 -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
            <div class="bg-white rounded-lg shadow p-6">
                <h2 class="text-lg font-semibold mb-4">Rework Events (Daily)</h2>
                <canvas id="reworkChart"></canvas>
            </div>
            <div class="bg-white rounded-lg shadow p-6">
                <h2 class="text-lg font-semibold mb-4">Average Phase Duration (minutes)</h2>
                <canvas id="phaseChart"></canvas>
            </div>
        </div>

        <!-- CI Failures Table -->
        <div class="bg-white rounded-lg shadow p-6">
            <h2 class="text-lg font-semibold mb-4">Recent CI Failures</h2>
            <div class="overflow-x-auto">
                <table class="min-w-full">
                    <thead>
                        <tr class="border-b">
                            <th class="px-4 py-2 text-left text-sm font-medium text-gray-500">Timestamp</th>
                            <th class="px-4 py-2 text-left text-sm font-medium text-gray-500">Command</th>
                            <th class="px-4 py-2 text-left text-sm font-medium text-gray-500">Exit Code</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y">
                        {ci_failures_rows}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        // API Success Rate Chart
        new Chart(document.getElementById('apiChart'), {{
            type: 'line',
            data: {{
                labels: {api_labels},
                datasets: [{{
                    label: 'Success Rate (%)',
                    data: {api_data},
                    borderColor: 'rgb(34, 197, 94)',
                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                    fill: true,
                    tension: 0.3
                }}]
            }},
            options: {{
                responsive: true,
                scales: {{
                    y: {{
                        min: 0,
                        max: 100
                    }}
                }}
            }}
        }});

        // Block Rate Chart
        new Chart(document.getElementById('blockChart'), {{
            type: 'line',
            data: {{
                labels: {block_labels},
                datasets: [{{
                    label: 'Block Rate (%)',
                    data: {block_data},
                    borderColor: 'rgb(239, 68, 68)',
                    backgroundColor: 'rgba(239, 68, 68, 0.1)',
                    fill: true,
                    tension: 0.3
                }}]
            }},
            options: {{
                responsive: true,
                scales: {{
                    y: {{
                        min: 0
                    }}
                }}
            }}
        }});

        // Rework Events Chart
        new Chart(document.getElementById('reworkChart'), {{
            type: 'bar',
            data: {{
                labels: {rework_labels},
                datasets: [{{
                    label: 'Rework Events',
                    data: {rework_data},
                    backgroundColor: 'rgba(59, 130, 246, 0.7)'
                }}]
            }},
            options: {{
                responsive: true,
                scales: {{
                    y: {{
                        beginAtZero: true
                    }}
                }}
            }}
        }});

        // Phase Duration Chart
        new Chart(document.getElementById('phaseChart'), {{
            type: 'bar',
            data: {{
                labels: {phase_labels},
                datasets: [{{
                    label: 'Duration (min)',
                    data: {phase_data},
                    backgroundColor: 'rgba(139, 92, 246, 0.7)'
                }}]
            }},
            options: {{
                responsive: true,
                indexAxis: 'y',
                scales: {{
                    x: {{
                        beginAtZero: true
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""
