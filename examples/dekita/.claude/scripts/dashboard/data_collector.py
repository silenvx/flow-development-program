#!/usr/bin/env python3
"""
ダッシュボードデータ収集モジュール

各種ログファイルからダッシュボード表示用のデータを収集する。

Issue #1367: 開発フローログの可視化
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _parse_timestamp(ts_str: str) -> datetime:
    """タイムスタンプ文字列をタイムゾーンアウェアなdatetimeに変換

    Args:
        ts_str: ISO 8601形式のタイムスタンプ文字列

    Returns:
        タイムゾーンアウェアなdatetime（タイムゾーンなしの場合はローカルとみなす）
    """
    ts = datetime.fromisoformat(ts_str)
    if ts.tzinfo is None:
        # タイムゾーンなしの場合はローカルタイムゾーンとみなしUTCに変換
        ts = ts.astimezone(UTC)
    return ts


class DashboardDataCollector:
    """ダッシュボード用データ収集クラス"""

    def __init__(self, logs_dir: Path | None = None):
        """初期化

        Args:
            logs_dir: ログディレクトリのパス（None の場合は自動検出）
        """
        if logs_dir is None:
            logs_dir = self._detect_logs_dir()
        self.logs_dir = logs_dir
        self.execution_dir = logs_dir / "execution"
        self.metrics_dir = logs_dir / "metrics"
        self.flow_dir = logs_dir / "flow"

    def _detect_logs_dir(self) -> Path:
        """プロジェクトのログディレクトリを自動検出

        worktree環境でも正しくメインリポジトリのログディレクトリを検出する。
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                git_common_dir = Path(result.stdout.strip()).resolve()
                # worktree環境: .git/worktrees/<name> -> メインリポジトリを検出
                if "worktrees" in git_common_dir.parts:
                    parts = git_common_dir.parts
                    worktrees_idx = parts.index("worktrees")
                    if worktrees_idx >= 2:
                        project_root = Path(*parts[: worktrees_idx - 1])
                        return project_root / ".claude" / "logs"
                # 通常リポジトリ: .git -> parent がルート
                if git_common_dir.name == ".git":
                    return git_common_dir.parent / ".claude" / "logs"
        except Exception:
            # Git command failed or timeout; fall back to relative path
            pass
        return Path(__file__).parent.parent.parent / "logs"

    def get_api_success_rate_trend(self, days: int = 7) -> list[dict[str, Any]]:
        """API成功率のトレンドを取得

        Args:
            days: 集計する日数

        Returns:
            日別の成功率データのリスト
        """
        api_log = self.execution_dir / "api-operations.jsonl"
        if not api_log.exists():
            return []

        cutoff = datetime.now(UTC) - timedelta(days=days)
        daily_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"success": 0, "total": 0})

        with open(api_log, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    ts_str = entry.get("timestamp")
                    if not ts_str:
                        continue

                    ts = _parse_timestamp(ts_str)
                    if ts < cutoff:
                        continue

                    date_key = ts.strftime("%Y-%m-%d")
                    daily_stats[date_key]["total"] += 1
                    if entry.get("success", True):
                        daily_stats[date_key]["success"] += 1

                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        result = []
        for date_key in sorted(daily_stats.keys()):
            stats = daily_stats[date_key]
            rate = (stats["success"] / stats["total"] * 100) if stats["total"] > 0 else 100.0
            result.append(
                {
                    "date": date_key,
                    "success_rate": round(rate, 1),
                    "total": stats["total"],
                    "success": stats["success"],
                }
            )

        return result

    def get_block_rate_trend(self, days: int = 7) -> list[dict[str, Any]]:
        """ブロック率のトレンドを取得

        Args:
            days: 集計する日数

        Returns:
            日別のブロック率データのリスト
        """
        hook_log = self.execution_dir / "hook-execution.log"
        if not hook_log.exists():
            return []

        cutoff = datetime.now(UTC) - timedelta(days=days)
        daily_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"blocks": 0, "total": 0})

        with open(hook_log, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    ts_str = entry.get("timestamp")
                    if not ts_str:
                        continue

                    ts = _parse_timestamp(ts_str)
                    if ts < cutoff:
                        continue

                    date_key = ts.strftime("%Y-%m-%d")
                    daily_stats[date_key]["total"] += 1
                    if entry.get("decision") == "block":
                        daily_stats[date_key]["blocks"] += 1

                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        result = []
        for date_key in sorted(daily_stats.keys()):
            stats = daily_stats[date_key]
            rate = (stats["blocks"] / stats["total"] * 100) if stats["total"] > 0 else 0.0
            result.append(
                {
                    "date": date_key,
                    "block_rate": round(rate, 1),
                    "total": stats["total"],
                    "blocks": stats["blocks"],
                }
            )

        return result

    def get_rework_events_trend(self, days: int = 7) -> list[dict[str, Any]]:
        """手戻りイベントのトレンドを取得

        Args:
            days: 集計する日数

        Returns:
            日別の手戻りイベント数のリスト
        """
        rework_log = self.metrics_dir / "rework-metrics.log"
        if not rework_log.exists():
            return []

        cutoff = datetime.now(UTC) - timedelta(days=days)
        daily_counts: Counter[str] = Counter()

        with open(rework_log, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("type") != "rework_detected":
                        continue

                    ts_str = entry.get("timestamp")
                    if not ts_str:
                        continue

                    ts = _parse_timestamp(ts_str)
                    if ts < cutoff:
                        continue

                    date_key = ts.strftime("%Y-%m-%d")
                    daily_counts[date_key] += 1

                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        # 日付順にソート
        result = []
        for date_key in sorted(daily_counts.keys()):
            result.append(
                {
                    "date": date_key,
                    "count": daily_counts[date_key],
                }
            )

        return result

    def get_phase_durations(self, days: int = 7) -> dict[str, float]:
        """フェーズごとの平均滞在時間を取得

        Args:
            days: 集計する日数

        Returns:
            フェーズ名 -> 平均滞在時間（分）のマッピング
        """
        events_log = self.flow_dir / "events.jsonl"
        if not events_log.exists():
            return {}

        cutoff = datetime.now(UTC) - timedelta(days=days)
        phase_durations: dict[str, list[float]] = defaultdict(list)

        # セッションごとにフェーズ遷移を追跡
        session_phases: dict[str, list[tuple[str, datetime]]] = defaultdict(list)

        with open(events_log, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    ts_str = entry.get("ts")
                    if not ts_str:
                        continue

                    ts = _parse_timestamp(ts_str)
                    if ts < cutoff:
                        continue

                    session_id = entry.get("session_id", "")
                    phase = entry.get("current_phase") or entry.get("new_phase")
                    if phase:
                        session_phases[session_id].append((phase, ts))

                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        # 各セッションのフェーズ滞在時間を計算
        for _session_id, phases in session_phases.items():
            phases.sort(key=lambda x: x[1])
            for i in range(len(phases) - 1):
                phase_name = phases[i][0]
                duration = (phases[i + 1][1] - phases[i][1]).total_seconds() / 60
                if 0 < duration < 120:  # 2時間以上は異常値として除外
                    phase_durations[phase_name].append(duration)

        # 平均を計算
        result = {}
        for phase, durations in phase_durations.items():
            if durations:
                result[phase] = round(sum(durations) / len(durations), 1)

        return result

    def get_ci_failures(self, days: int = 7, limit: int = 10) -> list[dict[str, Any]]:
        """最近のCI失敗を取得

        Args:
            days: 集計する日数
            limit: 取得する最大件数

        Returns:
            CI失敗の詳細リスト
        """
        api_log = self.execution_dir / "api-operations.jsonl"
        if not api_log.exists():
            return []

        cutoff = datetime.now(UTC) - timedelta(days=days)
        failures = []

        with open(api_log, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("success", True):
                        continue

                    ts_str = entry.get("timestamp")
                    if not ts_str:
                        continue

                    ts = _parse_timestamp(ts_str)
                    if ts < cutoff:
                        continue

                    # CIやテスト関連のコマンドをフィルタ
                    command = entry.get("command", "")
                    operation = entry.get("operation", "")
                    if any(kw in command.lower() for kw in ["test", "ci", "lint", "build"]):
                        failures.append(
                            {
                                "timestamp": ts_str,
                                "command": command[:100],
                                "operation": operation,
                                "exit_code": entry.get("exit_code"),
                            }
                        )

                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        # 新しい順にソートして上位を返す
        failures.sort(key=lambda x: x["timestamp"], reverse=True)
        return failures[:limit]

    def get_summary_kpis(self, days: int = 7) -> dict[str, Any]:
        """サマリーKPIを取得

        Args:
            days: 集計する日数

        Returns:
            各種KPIのマッピング
        """
        api_trend = self.get_api_success_rate_trend(days)
        block_trend = self.get_block_rate_trend(days)
        rework_trend = self.get_rework_events_trend(days)

        # 平均を計算
        api_success_rate = 100.0
        if api_trend:
            total_success = sum(d["success"] for d in api_trend)
            total_requests = sum(d["total"] for d in api_trend)
            if total_requests > 0:
                api_success_rate = round(total_success / total_requests * 100, 1)

        block_rate = 0.0
        if block_trend:
            total_blocks = sum(d["blocks"] for d in block_trend)
            total_hooks = sum(d["total"] for d in block_trend)
            if total_hooks > 0:
                block_rate = round(total_blocks / total_hooks * 100, 1)

        total_reworks = sum(d["count"] for d in rework_trend) if rework_trend else 0

        return {
            "api_success_rate": api_success_rate,
            "block_rate": block_rate,
            "total_reworks": total_reworks,
            "days": days,
        }
