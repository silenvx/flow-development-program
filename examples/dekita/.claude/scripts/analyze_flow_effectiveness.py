#!/usr/bin/env python3
"""開発フローの効果を評価・分析する。

Why:
    フック・ワークフローの改善点を特定するため、
    メトリクスを自動収集し改善提案を生成する機能が必要。

What:
    - report: 全体レポート生成
    - weekly: 週次サマリー生成
    - check: 問題検出（CI用）
    - create-issues: 改善Issue自動作成

State:
    - reads: .claude/logs/session/*.jsonl（セッションログ）
    - reads: .claude/logs/metrics/*.log（メトリクス）

Remarks:
    - worktree内でも本体のログを参照する
    - lib.loggingモジュールを使用してログを読み込む

Changelog:
    - silenvx/dekita#1500: フロー効果分析機能を追加
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# プロジェクトルート（worktree内でも本体を参照）
SCRIPT_DIR = Path(__file__).parent

# Add hooks directory to path for lib imports
HOOKS_DIR = SCRIPT_DIR.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
from lib.logging import read_all_session_log_entries


def _get_main_project_root() -> Path:
    """メインプロジェクトルートを取得（worktreeではなく本体）"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=SCRIPT_DIR,
        )
        if result.returncode == 0:
            git_common_dir = Path(result.stdout.strip())
            # 相対パスの場合はSCRIPT_DIRを基準に解決
            if not git_common_dir.is_absolute():
                git_common_dir = (SCRIPT_DIR / git_common_dir).resolve()
            # .git ディレクトリの親がプロジェクトルート
            if git_common_dir.name == ".git":
                return git_common_dir.parent
            # worktreeの場合、common dirは本体の.gitを指す
            return git_common_dir.parent
    except Exception:
        pass  # git コマンド失敗時はフォールバックを使用
    # フォールバック: スクリプトディレクトリから推測
    return SCRIPT_DIR.parent.parent


PROJECT_ROOT = _get_main_project_root()
LOGS_DIR = PROJECT_ROOT / ".claude" / "logs"
EXECUTION_LOG_DIR = LOGS_DIR / "execution"
METRICS_LOG_DIR = LOGS_DIR / "metrics"

# ログファイル（セッション別ログはread_all_session_log_entries()で読み込み）
SESSION_METRICS_LOG = METRICS_LOG_DIR / "session-metrics.log"
PR_METRICS_LOG = METRICS_LOG_DIR / "pr-metrics.log"
IMPROVEMENT_LOG = METRICS_LOG_DIR / "improvement-tracking.log"

# 新規メトリクスログファイル
REWORK_LOG = METRICS_LOG_DIR / "rework-metrics.log"
CI_RECOVERY_LOG = METRICS_LOG_DIR / "ci-recovery-metrics.log"
TOOL_EFFICIENCY_LOG = METRICS_LOG_DIR / "tool-efficiency-metrics.log"

# 評価閾値
THRESHOLDS = {
    "block_rate_min": 0.005,  # ブロック率が低すぎる（0.5%未満）
    "block_rate_max": 0.20,  # ブロック率が高すぎる（20%超）
    "false_positive_max": 0.10,  # 誤検知率上限
    "session_duration_warn": 4 * 60 * 60,  # セッション4時間超は警告
    "pr_cycle_time_warn": 24 * 60 * 60,  # PRサイクルタイム24時間超
    # 新規メトリクス閾値
    "rework_events_warn": 10,  # 手戻りイベント10件以上で警告
    "ci_recovery_time_warn": 30 * 60,  # CI復旧時間30分超で警告
    "tool_inefficiency_warn": 5,  # 非効率パターン5回以上で警告
}


@dataclass
class HookStats:
    """フック実行統計"""

    name: str
    total: int = 0
    approves: int = 0
    blocks: int = 0
    block_reasons: Counter = field(default_factory=Counter)

    @property
    def block_rate(self) -> float:
        return self.blocks / self.total if self.total > 0 else 0.0


@dataclass
class SessionStats:
    """セッション統計"""

    session_id: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    hook_executions: int = 0
    blocks: int = 0
    branches: set = field(default_factory=set)

    @property
    def duration_seconds(self) -> float | None:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None


@dataclass
class PRStats:
    """PRライフサイクル統計"""

    pr_number: int
    created_at: datetime | None = None
    merged_at: datetime | None = None
    review_count: int = 0
    ci_runs: int = 0
    ci_failures: int = 0

    @property
    def cycle_time_seconds(self) -> float | None:
        if self.created_at and self.merged_at:
            return (self.merged_at - self.created_at).total_seconds()
        return None


@dataclass
class ReworkStats:
    """手戻り統計

    Note: 手戻り「率」は全編集数が不明のため計算しない。
    代わりに手戻りイベント数と平均編集数を報告する。
    """

    rework_events: int = 0
    total_rework_edits: int = 0  # 手戻りイベント内の編集数合計
    files_with_rework: set = field(default_factory=set)

    @property
    def avg_edits_per_rework(self) -> float:
        """手戻りあたりの平均編集数"""
        return self.total_rework_edits / self.rework_events if self.rework_events > 0 else 0.0


@dataclass
class CIRecoveryStats:
    """CI復旧統計"""

    total_failures: int = 0
    total_recoveries: int = 0
    recovery_times: list = field(default_factory=list)

    @property
    def avg_recovery_seconds(self) -> float | None:
        if self.recovery_times:
            return sum(self.recovery_times) / len(self.recovery_times)
        return None


@dataclass
class ToolEfficiencyStats:
    """ツール効率統計"""

    total_inefficiencies: int = 0
    patterns: Counter = field(default_factory=Counter)
    affected_files: set = field(default_factory=set)


@dataclass
class GitOperationsStats:
    """Git操作統計"""

    update_branch_count: int = 0
    conflict_count: int = 0
    rebase_count: int = 0
    merge_count: int = 0
    update_branch_success: int = 0


@dataclass
class AnalysisReport:
    """分析レポート"""

    generated_at: datetime
    period_start: datetime | None
    period_end: datetime | None
    hook_stats: dict[str, HookStats]
    session_stats: dict[str, SessionStats]
    pr_stats: dict[int, PRStats]
    git_operations_stats: GitOperationsStats
    issues: list[dict[str, Any]]
    recommendations: list[str]
    # 新規メトリクス
    rework_stats: ReworkStats | None = None
    ci_recovery_stats: CIRecoveryStats | None = None
    tool_efficiency_stats: ToolEfficiencyStats | None = None


def load_hook_logs(since: datetime | None = None) -> list[dict[str, Any]]:
    """フック実行ログを読み込む（全セッション）"""
    entries = read_all_session_log_entries(EXECUTION_LOG_DIR, "hook-execution")

    if not since:
        return entries

    # Filter by timestamp
    filtered = []
    for entry in entries:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts >= since:
                filtered.append(entry)
        except (KeyError, ValueError):
            continue
    return filtered


def load_session_metrics(since: datetime | None = None) -> list[dict[str, Any]]:
    """セッションメトリクスを読み込む"""
    if not SESSION_METRICS_LOG.exists():
        return []

    logs = []
    with open(SESSION_METRICS_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                if since:
                    ts = datetime.fromisoformat(entry["timestamp"])
                    if ts < since:
                        continue
                logs.append(entry)
            except (json.JSONDecodeError, KeyError):
                continue
    return logs


def load_pr_metrics(since: datetime | None = None) -> list[dict[str, Any]]:
    """PRメトリクスを読み込む"""
    if not PR_METRICS_LOG.exists():
        return []

    logs = []
    with open(PR_METRICS_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                if since:
                    ts = datetime.fromisoformat(entry["timestamp"])
                    if ts < since:
                        continue
                logs.append(entry)
            except (json.JSONDecodeError, KeyError):
                continue
    return logs


def load_rework_metrics(since: datetime | None = None) -> list[dict[str, Any]]:
    """手戻りメトリクスを読み込む"""
    if not REWORK_LOG.exists():
        return []

    logs = []
    with open(REWORK_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                if since:
                    ts = datetime.fromisoformat(entry["timestamp"])
                    if ts < since:
                        continue
                logs.append(entry)
            except (json.JSONDecodeError, KeyError):
                continue
    return logs


def load_ci_recovery_metrics(since: datetime | None = None) -> list[dict[str, Any]]:
    """CI復旧メトリクスを読み込む"""
    if not CI_RECOVERY_LOG.exists():
        return []

    logs = []
    with open(CI_RECOVERY_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                if since:
                    ts = datetime.fromisoformat(entry["timestamp"])
                    if ts < since:
                        continue
                logs.append(entry)
            except (json.JSONDecodeError, KeyError):
                continue
    return logs


def load_tool_efficiency_metrics(since: datetime | None = None) -> list[dict[str, Any]]:
    """ツール効率メトリクスを読み込む"""
    if not TOOL_EFFICIENCY_LOG.exists():
        return []

    logs = []
    with open(TOOL_EFFICIENCY_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                if since:
                    ts = datetime.fromisoformat(entry["timestamp"])
                    if ts < since:
                        continue
                logs.append(entry)
            except (json.JSONDecodeError, KeyError):
                continue
    return logs


def load_git_operations_logs(since: datetime | None = None) -> list[dict[str, Any]]:
    """Git操作ログを読み込む（全セッション）"""
    entries = read_all_session_log_entries(EXECUTION_LOG_DIR, "git-operations")

    if not since:
        return entries

    # Filter by timestamp
    filtered = []
    for entry in entries:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts >= since:
                filtered.append(entry)
        except (KeyError, ValueError):
            continue
    return filtered


def analyze_git_operations(logs: list[dict[str, Any]]) -> GitOperationsStats:
    """Git操作を分析"""
    stats = GitOperationsStats()

    for entry in logs:
        operation = entry.get("operation", "")
        success = entry.get("success", False)

        if operation == "update_branch":
            stats.update_branch_count += 1
            if success:
                stats.update_branch_success += 1
        elif operation == "conflict":
            stats.conflict_count += 1
        elif operation == "rebase":
            stats.rebase_count += 1
        elif operation == "merge":
            stats.merge_count += 1

    return stats


def analyze_hooks(logs: list[dict[str, Any]]) -> dict[str, HookStats]:
    """フック実行を分析"""
    stats: dict[str, HookStats] = {}

    for entry in logs:
        hook = entry.get("hook", "unknown")
        decision = entry.get("decision", "approve")

        if hook not in stats:
            stats[hook] = HookStats(name=hook)

        stats[hook].total += 1
        if decision == "approve":
            stats[hook].approves += 1
        elif decision == "block":
            stats[hook].blocks += 1
            reason = entry.get("reason", "unknown")
            stats[hook].block_reasons[reason] += 1

    return dict(stats)


def analyze_sessions(logs: list[dict[str, Any]]) -> dict[str, SessionStats]:
    """セッションを分析"""
    stats: dict[str, SessionStats] = {}

    for entry in logs:
        session_id = entry.get("session_id", "unknown")
        ts = datetime.fromisoformat(entry["timestamp"])

        if session_id not in stats:
            stats[session_id] = SessionStats(session_id=session_id)

        session = stats[session_id]
        session.hook_executions += 1

        if session.start_time is None or ts < session.start_time:
            session.start_time = ts
        if session.end_time is None or ts > session.end_time:
            session.end_time = ts

        if entry.get("decision") == "block":
            session.blocks += 1

        if branch := entry.get("branch"):
            session.branches.add(branch)

    return stats


def fetch_github_pr_stats(days: int = 30) -> dict[int, PRStats]:
    """GitHub APIからPR統計を取得"""
    stats: dict[int, PRStats] = {}

    try:
        # マージ済みPR一覧を取得
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "merged",
                "--limit",
                "50",
                "--json",
                "number,createdAt,mergedAt,reviews",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=PROJECT_ROOT,
        )

        if result.returncode == 0:
            prs = json.loads(result.stdout)
            cutoff = datetime.now(UTC) - timedelta(days=days)

            for pr in prs:
                created = datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00"))

                # マージ日でフィルタリング（長期PRも週次レポートに含める）
                merged = None
                if pr.get("mergedAt"):
                    merged = datetime.fromisoformat(pr["mergedAt"].replace("Z", "+00:00"))
                    if merged < cutoff:
                        continue
                else:
                    # 未マージPRは作成日でフィルタリング
                    if created < cutoff:
                        continue

                stats[pr["number"]] = PRStats(
                    pr_number=pr["number"],
                    created_at=created,
                    merged_at=merged,
                    review_count=len(pr.get("reviews", [])),
                )

        # 各PRのCIステータスを取得
        for pr_number in list(stats.keys())[:10]:  # 最新10件のみ
            try:
                result = subprocess.run(
                    [
                        "gh",
                        "pr",
                        "checks",
                        str(pr_number),
                        "--json",
                        "name,state",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=PROJECT_ROOT,
                )
                if result.returncode == 0:
                    checks = json.loads(result.stdout)
                    stats[pr_number].ci_runs = len(checks)
                    stats[pr_number].ci_failures = sum(
                        1 for c in checks if c.get("state") == "FAILURE"
                    )
            except Exception:
                pass  # CI情報取得失敗は無視（PR基本情報は既に取得済み）

    except Exception as e:
        print(f"Warning: GitHub API error: {e}", file=sys.stderr)

    return stats


def analyze_rework(logs: list[dict[str, Any]]) -> ReworkStats:
    """手戻りメトリクスを分析"""
    stats = ReworkStats()

    for entry in logs:
        if entry.get("type") == "rework_detected":
            stats.rework_events += 1
            stats.total_rework_edits += entry.get("edit_count", 0)
            if file_path := entry.get("file_path"):
                stats.files_with_rework.add(file_path)

    return stats


def analyze_ci_recovery(logs: list[dict[str, Any]]) -> CIRecoveryStats:
    """CI復旧メトリクスを分析"""
    stats = CIRecoveryStats()

    for entry in logs:
        if entry.get("type") == "ci_failure":
            stats.total_failures += 1
        elif entry.get("type") == "ci_recovery":
            stats.total_recoveries += 1
            if recovery_time := entry.get("recovery_seconds"):
                stats.recovery_times.append(recovery_time)

    return stats


def analyze_tool_efficiency(logs: list[dict[str, Any]]) -> ToolEfficiencyStats:
    """ツール効率メトリクスを分析"""
    stats = ToolEfficiencyStats()

    for entry in logs:
        if entry.get("type") == "inefficiency_detected":
            stats.total_inefficiencies += 1
            if pattern := entry.get("pattern_name"):
                stats.patterns[pattern] += 1
            details = entry.get("details", {})
            if file_path := details.get("file"):
                stats.affected_files.add(file_path)

    return stats


def detect_issues(
    hook_stats: dict[str, HookStats],
    session_stats: dict[str, SessionStats],
    pr_stats: dict[int, PRStats],
    rework_stats: ReworkStats | None = None,
    ci_recovery_stats: CIRecoveryStats | None = None,
    tool_efficiency_stats: ToolEfficiencyStats | None = None,
) -> list[dict[str, Any]]:
    """問題を検出"""
    issues: list[dict[str, Any]] = []

    # 1. 全体ブロック率のチェック
    total_executions = sum(s.total for s in hook_stats.values())
    total_blocks = sum(s.blocks for s in hook_stats.values())
    overall_block_rate = total_blocks / total_executions if total_executions > 0 else 0

    # ログがない場合はスキップ（新規インストール時などの誤検知を防止）
    if total_executions > 0 and overall_block_rate < THRESHOLDS["block_rate_min"]:
        issues.append(
            {
                "type": "low_block_rate",
                "severity": "warning",
                "message": f"ブロック率が低すぎます ({overall_block_rate:.2%})",
                "detail": "フックが十分に機能していない可能性があります",
                "recommendation": "フックの検出ロジックを見直すか、新しいチェックを追加してください",
            }
        )
    elif overall_block_rate > THRESHOLDS["block_rate_max"]:
        issues.append(
            {
                "type": "high_block_rate",
                "severity": "warning",
                "message": f"ブロック率が高すぎます ({overall_block_rate:.2%})",
                "detail": "フックが過剰に厳しいか、誤検知が多い可能性があります",
                "recommendation": "ブロック理由を分析し、不要なブロックを削減してください",
            }
        )

    # 2. 特定フックのブロック率チェック
    for name, stats in hook_stats.items():
        if stats.total < 10:  # サンプル数が少ない場合はスキップ
            continue
        if stats.block_rate > 0.30:
            issues.append(
                {
                    "type": "hook_high_block_rate",
                    "severity": "info",
                    "message": f"フック '{name}' のブロック率が高い ({stats.block_rate:.2%})",
                    "detail": f"主なブロック理由: {stats.block_reasons.most_common(3)}",
                    "recommendation": "このフックの条件を緩和するか、より具体的にする検討を",
                }
            )

    # 3. 長時間セッションのチェック
    for session_id, session in session_stats.items():
        duration = session.duration_seconds
        if duration and duration > THRESHOLDS["session_duration_warn"]:
            hours = duration / 3600
            issues.append(
                {
                    "type": "long_session",
                    "severity": "info",
                    "message": f"セッション {session_id} が長時間 ({hours:.1f}時間)",
                    "detail": f"ブロック数: {session.blocks}, ブランチ: {session.branches}",
                    "recommendation": "タスクの分割や効率化を検討してください",
                }
            )

    # 4. PRサイクルタイムのチェック
    for pr_number, pr in pr_stats.items():
        cycle_time = pr.cycle_time_seconds
        if cycle_time and cycle_time > THRESHOLDS["pr_cycle_time_warn"]:
            hours = cycle_time / 3600
            issues.append(
                {
                    "type": "long_pr_cycle",
                    "severity": "info",
                    "message": f"PR #{pr_number} のサイクルタイムが長い ({hours:.1f}時間)",
                    "detail": f"レビュー数: {pr.review_count}, CI失敗: {pr.ci_failures}",
                    "recommendation": "PRを小さく分割するか、レビュープロセスを改善してください",
                }
            )

    # 5. 手戻りイベント数のチェック
    if rework_stats and rework_stats.rework_events >= THRESHOLDS["rework_events_warn"]:
        issues.append(
            {
                "type": "high_rework_events",
                "severity": "warning",
                "message": f"手戻りイベントが多い ({rework_stats.rework_events}件)",
                "detail": f"影響ファイル: {len(rework_stats.files_with_rework)}件, "
                f"平均編集数/手戻り: {rework_stats.avg_edits_per_rework:.1f}",
                "recommendation": "編集前にファイル内容を十分に確認し、計画的な編集を心がけてください",
            }
        )

    # 6. CI復旧時間のチェック
    if ci_recovery_stats and ci_recovery_stats.avg_recovery_seconds:
        avg_recovery = ci_recovery_stats.avg_recovery_seconds
        if avg_recovery > THRESHOLDS["ci_recovery_time_warn"]:
            minutes = avg_recovery / 60
            issues.append(
                {
                    "type": "slow_ci_recovery",
                    "severity": "info",
                    "message": f"CI復旧時間が長い (平均 {minutes:.1f}分)",
                    "detail": f"CI失敗: {ci_recovery_stats.total_failures}件, "
                    f"復旧: {ci_recovery_stats.total_recoveries}件",
                    "recommendation": "ローカルでのテスト実行を強化するか、CI失敗原因の分析を検討してください",
                }
            )

    # 7. ツール非効率パターンのチェック
    if tool_efficiency_stats:
        if tool_efficiency_stats.total_inefficiencies >= THRESHOLDS["tool_inefficiency_warn"]:
            top_patterns = tool_efficiency_stats.patterns.most_common(3)
            issues.append(
                {
                    "type": "tool_inefficiency",
                    "severity": "info",
                    "message": f"ツール使用の非効率パターンが多い ({tool_efficiency_stats.total_inefficiencies}件)",
                    "detail": f"主なパターン: {', '.join(f'{p[0]} ({p[1]}件)' for p in top_patterns)}",
                    "recommendation": "Task toolでの探索や、事前調査を活用して効率化を検討してください",
                }
            )

    return issues


def generate_recommendations(
    hook_stats: dict[str, HookStats],
    session_stats: dict[str, SessionStats],
    pr_stats: dict[int, PRStats],
    issues: list[dict[str, Any]],
    rework_stats: ReworkStats | None = None,
    ci_recovery_stats: CIRecoveryStats | None = None,
    tool_efficiency_stats: ToolEfficiencyStats | None = None,
) -> list[str]:
    """改善提案を生成"""
    recommendations: list[str] = []

    # ブロック理由の分析
    all_block_reasons: Counter = Counter()
    for stats in hook_stats.values():
        all_block_reasons.update(stats.block_reasons)

    if all_block_reasons:
        top_reasons = all_block_reasons.most_common(5)
        recommendations.append(
            f"最も多いブロック理由: {', '.join(f'{r[0]} ({r[1]}件)' for r in top_reasons)}"
        )

    # フック追加の提案
    low_coverage_areas = []
    hook_names = set(hook_stats.keys())

    expected_hooks = {
        "type-check": "型チェック",
        "test-check": "テスト実行",
        "security-check": "セキュリティ",
    }

    for hook, desc in expected_hooks.items():
        if not any(hook in name for name in hook_names):
            low_coverage_areas.append(desc)

    if low_coverage_areas:
        recommendations.append(f"カバレッジ不足の領域: {', '.join(low_coverage_areas)}")

    # PRメトリクスからの提案
    if pr_stats:
        avg_cycle_time = sum(p.cycle_time_seconds or 0 for p in pr_stats.values()) / len(pr_stats)
        if avg_cycle_time > 0:
            recommendations.append(f"平均PRサイクルタイム: {avg_cycle_time / 3600:.1f}時間")

        total_ci_failures = sum(p.ci_failures for p in pr_stats.values())
        if total_ci_failures > 5:
            recommendations.append(
                f"CI失敗が多い ({total_ci_failures}件): ローカルチェックの強化を検討"
            )

    # 新規メトリクスからの提案
    if rework_stats and rework_stats.rework_events > 0:
        recommendations.append(
            f"手戻りイベント: {rework_stats.rework_events}件 "
            f"(平均 {rework_stats.avg_edits_per_rework:.1f} 編集/手戻り)"
        )

    if ci_recovery_stats and ci_recovery_stats.avg_recovery_seconds:
        avg_min = ci_recovery_stats.avg_recovery_seconds / 60
        recommendations.append(f"平均CI復旧時間: {avg_min:.1f}分")

    if tool_efficiency_stats and tool_efficiency_stats.total_inefficiencies > 0:
        top_pattern = tool_efficiency_stats.patterns.most_common(1)
        if top_pattern:
            recommendations.append(
                f"最も多い非効率パターン: {top_pattern[0][0]} ({top_pattern[0][1]}件)"
            )

    return recommendations


def generate_report(
    period_days: int | None = None,
) -> AnalysisReport:
    """分析レポートを生成"""
    since = None
    if period_days:
        since = datetime.now(UTC) - timedelta(days=period_days)

    # データ読み込み
    hook_logs = load_hook_logs(since)
    git_ops_logs = load_git_operations_logs(since)
    # セッションメトリクスログは収集のみ（将来の拡張用に読み込みテスト）
    _ = load_session_metrics(since)

    # 新規メトリクスログ読み込み
    rework_logs = load_rework_metrics(since)
    ci_recovery_logs = load_ci_recovery_metrics(since)
    tool_efficiency_logs = load_tool_efficiency_metrics(since)

    # 分析実行
    hook_stats = analyze_hooks(hook_logs)
    session_stats = analyze_sessions(hook_logs)  # フックログからセッション情報も取得
    pr_stats = fetch_github_pr_stats(period_days or 30)
    git_operations_stats = analyze_git_operations(git_ops_logs)

    # 新規メトリクス分析
    rework_stats = analyze_rework(rework_logs)
    ci_recovery_stats = analyze_ci_recovery(ci_recovery_logs)
    tool_efficiency_stats = analyze_tool_efficiency(tool_efficiency_logs)

    # 問題検出
    issues = detect_issues(
        hook_stats,
        session_stats,
        pr_stats,
        rework_stats,
        ci_recovery_stats,
        tool_efficiency_stats,
    )

    # 改善提案生成
    recommendations = generate_recommendations(
        hook_stats,
        session_stats,
        pr_stats,
        issues,
        rework_stats,
        ci_recovery_stats,
        tool_efficiency_stats,
    )

    return AnalysisReport(
        generated_at=datetime.now(UTC),
        period_start=since,
        period_end=datetime.now(UTC),
        hook_stats=hook_stats,
        session_stats=session_stats,
        pr_stats=pr_stats,
        git_operations_stats=git_operations_stats,
        issues=issues,
        recommendations=recommendations,
        rework_stats=rework_stats,
        ci_recovery_stats=ci_recovery_stats,
        tool_efficiency_stats=tool_efficiency_stats,
    )


def format_report_markdown(report: AnalysisReport) -> str:
    """レポートをMarkdown形式で出力"""
    lines: list[str] = []

    lines.append("# 開発フロー評価レポート")
    lines.append("")
    lines.append(f"生成日時: {report.generated_at.isoformat()}")
    if report.period_start:
        lines.append(
            f"分析期間: {report.period_start.date()} ~ {report.period_end.date() if report.period_end else 'now'}"
        )
    lines.append("")

    # サマリー
    lines.append("## サマリー")
    lines.append("")
    total_executions = sum(s.total for s in report.hook_stats.values())
    total_blocks = sum(s.blocks for s in report.hook_stats.values())
    block_rate = total_blocks / total_executions if total_executions > 0 else 0
    lines.append(f"- フック実行総数: {total_executions}")
    lines.append(f"- ブロック数: {total_blocks} ({block_rate:.2%})")
    lines.append(f"- セッション数: {len(report.session_stats)}")
    lines.append(f"- 分析PR数: {len(report.pr_stats)}")
    lines.append("")

    # フック統計
    lines.append("## フック実行統計")
    lines.append("")
    lines.append("| フック名 | 実行数 | ブロック数 | ブロック率 |")
    lines.append("|----------|--------|-----------|-----------|")
    for name, stats in sorted(report.hook_stats.items(), key=lambda x: x[1].total, reverse=True):
        lines.append(f"| {name} | {stats.total} | {stats.blocks} | {stats.block_rate:.2%} |")
    lines.append("")

    # 検出された問題
    if report.issues:
        lines.append("## 検出された問題")
        lines.append("")
        for issue in report.issues:
            severity_emoji = {"warning": "⚠️", "info": "ℹ️", "error": "❌"}.get(
                issue["severity"], "•"
            )
            lines.append(f"### {severity_emoji} {issue['message']}")
            lines.append("")
            lines.append(f"- 詳細: {issue['detail']}")
            lines.append(f"- 推奨: {issue['recommendation']}")
            lines.append("")

    # 改善提案
    if report.recommendations:
        lines.append("## 改善提案")
        lines.append("")
        for rec in report.recommendations:
            lines.append(f"- {rec}")
        lines.append("")

    # PRメトリクス
    if report.pr_stats:
        lines.append("## PRメトリクス（最新）")
        lines.append("")
        lines.append("| PR# | サイクルタイム | レビュー数 | CI失敗 |")
        lines.append("|-----|--------------|-----------|--------|")
        for pr_number, pr in sorted(report.pr_stats.items(), key=lambda x: x[0], reverse=True)[:10]:
            cycle_time = pr.cycle_time_seconds
            cycle_str = f"{cycle_time / 3600:.1f}h" if cycle_time else "N/A"
            lines.append(f"| #{pr_number} | {cycle_str} | {pr.review_count} | {pr.ci_failures} |")
        lines.append("")

    # 効率性メトリクス
    has_efficiency_data = (
        (report.rework_stats and report.rework_stats.rework_events > 0)
        or (report.ci_recovery_stats and report.ci_recovery_stats.total_failures > 0)
        or (report.tool_efficiency_stats and report.tool_efficiency_stats.total_inefficiencies > 0)
    )

    if has_efficiency_data:
        lines.append("## 効率性メトリクス")
        lines.append("")

        # 手戻り統計
        if report.rework_stats and report.rework_stats.rework_events > 0:
            lines.append("### 手戻り (Rework)")
            lines.append("")
            lines.append(f"- 手戻りイベント: {report.rework_stats.rework_events}件")
            lines.append(f"- 手戻り内編集数合計: {report.rework_stats.total_rework_edits}")
            lines.append(f"- 平均編集数/手戻り: {report.rework_stats.avg_edits_per_rework:.1f}")
            lines.append(f"- 影響ファイル数: {len(report.rework_stats.files_with_rework)}")
            lines.append("")

        # CI復旧統計
        if report.ci_recovery_stats and report.ci_recovery_stats.total_failures > 0:
            lines.append("### CI復旧時間")
            lines.append("")
            lines.append(f"- CI失敗: {report.ci_recovery_stats.total_failures}件")
            lines.append(f"- 復旧完了: {report.ci_recovery_stats.total_recoveries}件")
            if report.ci_recovery_stats.avg_recovery_seconds:
                avg_min = report.ci_recovery_stats.avg_recovery_seconds / 60
                lines.append(f"- 平均復旧時間: {avg_min:.1f}分")
            lines.append("")

        # ツール効率統計
        if report.tool_efficiency_stats and report.tool_efficiency_stats.total_inefficiencies > 0:
            lines.append("### ツール使用効率")
            lines.append("")
            lines.append(
                f"- 非効率パターン検出: {report.tool_efficiency_stats.total_inefficiencies}件"
            )
            if report.tool_efficiency_stats.patterns:
                lines.append("- パターン別:")
                for pattern, count in report.tool_efficiency_stats.patterns.most_common(5):
                    lines.append(f"  - {pattern}: {count}件")
            lines.append("")

    # Git操作統計
    git_ops = report.git_operations_stats
    total_git_ops = (
        git_ops.update_branch_count
        + git_ops.conflict_count
        + git_ops.rebase_count
        + git_ops.merge_count
    )
    if total_git_ops > 0:
        lines.append("## Git操作統計")
        lines.append("")
        lines.append("| 操作 | 回数 | 成功率 |")
        lines.append("|------|------|--------|")
        if git_ops.update_branch_count > 0:
            success_rate = git_ops.update_branch_success / git_ops.update_branch_count * 100
            lines.append(f"| Update Branch | {git_ops.update_branch_count} | {success_rate:.0f}% |")
        if git_ops.conflict_count > 0:
            lines.append(f"| Conflict | {git_ops.conflict_count} | - |")
        if git_ops.rebase_count > 0:
            lines.append(f"| Rebase | {git_ops.rebase_count} | - |")
        if git_ops.merge_count > 0:
            lines.append(f"| Merge | {git_ops.merge_count} | - |")
        lines.append("")

    return "\n".join(lines)


def create_improvement_issues(report: AnalysisReport) -> list[str]:
    """重要な問題についてGitHub Issueを作成"""
    created_issues: list[str] = []

    for issue in report.issues:
        if issue["severity"] not in ("warning", "error"):
            continue

        title = f"[Flow Analysis] {issue['message']}"
        body = f"""## 自動検出された問題

**タイプ**: {issue["type"]}
**重要度**: {issue["severity"]}

### 詳細
{issue["detail"]}

### 推奨アクション
{issue["recommendation"]}

---
このIssueは `analyze-flow-effectiveness.py` によって自動生成されました。
"""

        try:
            result = subprocess.run(
                [
                    "gh",
                    "issue",
                    "create",
                    "--title",
                    title,
                    "--body",
                    body,
                    "--label",
                    "automation,flow-analysis",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=PROJECT_ROOT,
            )
            if result.returncode == 0:
                created_issues.append(result.stdout.strip())
        except Exception as e:
            print(f"Warning: Failed to create issue: {e}", file=sys.stderr)

    return created_issues


def check_for_problems(report: AnalysisReport) -> int:
    """問題があれば非ゼロを返す（CI用）"""
    critical_issues = [i for i in report.issues if i["severity"] in ("warning", "error")]
    if critical_issues:
        print("検出された問題:")
        for issue in critical_issues:
            print(f"  - [{issue['severity']}] {issue['message']}")
        return 1
    print("重大な問題は検出されませんでした")
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "report":
        # 全体レポート（全期間）
        report = generate_report()
        print(format_report_markdown(report))

    elif command == "weekly":
        # 週次レポート
        report = generate_report(period_days=7)
        print(format_report_markdown(report))

    elif command == "check":
        # 問題チェック（CI用）
        report = generate_report(period_days=7)
        sys.exit(check_for_problems(report))

    elif command == "create-issues":
        # 改善Issue作成
        report = generate_report(period_days=7)
        issues = create_improvement_issues(report)
        if issues:
            print(f"作成されたIssue: {len(issues)}件")
            for url in issues:
                print(f"  - {url}")
        else:
            print("Issueは作成されませんでした")

    elif command == "json":
        # JSON出力（プログラム連携用）
        report = generate_report(period_days=int(sys.argv[2]) if len(sys.argv) > 2 else None)
        output: dict[str, Any] = {
            "generated_at": report.generated_at.isoformat(),
            "hook_stats": {
                name: {
                    "total": s.total,
                    "approves": s.approves,
                    "blocks": s.blocks,
                    "block_rate": s.block_rate,
                }
                for name, s in report.hook_stats.items()
            },
            "git_operations_stats": {
                "update_branch_count": report.git_operations_stats.update_branch_count,
                "conflict_count": report.git_operations_stats.conflict_count,
                "rebase_count": report.git_operations_stats.rebase_count,
                "merge_count": report.git_operations_stats.merge_count,
                "update_branch_success": report.git_operations_stats.update_branch_success,
            },
            "issues": report.issues,
            "recommendations": report.recommendations,
        }

        # 効率性メトリクスを追加
        if report.rework_stats:
            output["rework_stats"] = {
                "rework_events": report.rework_stats.rework_events,
                "total_rework_edits": report.rework_stats.total_rework_edits,
                "avg_edits_per_rework": report.rework_stats.avg_edits_per_rework,
                "files_with_rework": len(report.rework_stats.files_with_rework),
            }

        if report.ci_recovery_stats:
            output["ci_recovery_stats"] = {
                "total_failures": report.ci_recovery_stats.total_failures,
                "total_recoveries": report.ci_recovery_stats.total_recoveries,
                "avg_recovery_seconds": report.ci_recovery_stats.avg_recovery_seconds,
            }

        if report.tool_efficiency_stats:
            output["tool_efficiency_stats"] = {
                "total_inefficiencies": report.tool_efficiency_stats.total_inefficiencies,
                "patterns": dict(report.tool_efficiency_stats.patterns),
                "affected_files": len(report.tool_efficiency_stats.affected_files),
            }

        print(json.dumps(output, ensure_ascii=False, indent=2))

    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
