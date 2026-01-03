#!/usr/bin/env python3
"""セッション終了時のメトリクスを収集する。

Why:
    セッション単位のフック実行統計・成果を記録し、
    改善分析に活用するため。

What:
    - collect_metrics(): セッションメトリクスを収集
    - write_metrics(): メトリクスをログファイルに書き込み

State:
    - reads: .claude/logs/session/*/hook-execution-*.jsonl
    - writes: .claude/logs/metrics/session-metrics.jsonl

Remarks:
    - Stop hookから呼び出される
    - lib.logging.read_session_log_entries()を使用

Changelog:
    - silenvx/dekita#1400: セッションメトリクス収集機能を追加
    - silenvx/dekita#2190: セッション別ログファイル形式に対応
    - silenvx/dekita#2496: get_claude_session_id削除に対応
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ログディレクトリ
SCRIPT_DIR = Path(__file__).parent
HOOKS_DIR = SCRIPT_DIR.parent / "hooks"

# Add hooks directory to path for importing common module
# Issue #2496: get_claude_session_id を削除し、handle_session_id_arg の戻り値を使用
sys.path.insert(0, str(HOOKS_DIR))
from lib.logging import read_session_log_entries
from lib.session import handle_session_id_arg


def _get_main_project_root() -> Path:
    """メインプロジェクトルートを取得

    Issue #2198: --git-common-dir で常にメインリポジトリの.gitを取得。
    worktree内でも常にメインリポジトリのルートを返す。

    パターン:
    - メインリポジトリ内: ".git" (相対パス) → SCRIPT_DIR基準で解決
    - worktree内: "/path/to/main/.git/worktrees/<name>" (絶対パス)
    """
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
            # 相対パスの場合は SCRIPT_DIR を基準に解決する
            if not git_common_dir.is_absolute():
                git_common_dir = (SCRIPT_DIR / git_common_dir).resolve()
            # ケース1: メインリポジトリ内で ".git" を返す場合
            if git_common_dir.name == ".git":
                return git_common_dir.parent
            # ケース2: worktree内で /path/to/main/.git/worktrees/<name> を返す場合
            if (
                git_common_dir.parent.name == "worktrees"
                and git_common_dir.parent.parent.name == ".git"
            ):
                # .git ディレクトリの1つ上がメインプロジェクトルート
                return git_common_dir.parent.parent.parent
            # その他のケースでは従来どおり親ディレクトリをルートとみなす
            return git_common_dir.parent
    except Exception:
        pass  # git コマンド失敗時はフォールバックを使用
    return SCRIPT_DIR.parent.parent.resolve()


PROJECT_ROOT = _get_main_project_root()
LOGS_DIR = PROJECT_ROOT / ".claude" / "logs"
EXECUTION_LOG_DIR = LOGS_DIR / "execution"
METRICS_LOG_DIR = LOGS_DIR / "metrics"
# Issue #2190: HOOK_LOG は削除（セッション別ログファイルに移行）
SESSION_METRICS_LOG = METRICS_LOG_DIR / "session-metrics.log"


def get_fallback_session_id() -> str:
    """Get fallback session ID based on PPID.

    Issue #2496: Simplified to use PPID-based fallback only.
    The session_id should be provided via --session-id argument.
    """
    return f"ppid-{os.getppid()}"


def analyze_session_from_hooks(session_id: str) -> dict[str, Any]:
    """フック実行ログからセッション情報を分析

    Issue #2190: セッション別ログファイル（hook-execution-{session_id}.jsonl）を読む
    """
    session_data: dict[str, Any] = {
        "hook_executions": 0,
        "blocks": 0,
        "approves": 0,
        "hooks_triggered": set(),
        "branches_touched": set(),
        "first_timestamp": None,
        "last_timestamp": None,
        "block_reasons": [],
    }

    # Issue #2190: セッション別ログファイルから読み込み
    entries = read_session_log_entries(EXECUTION_LOG_DIR, "hook-execution", session_id)

    for entry in entries:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])

            session_data["hook_executions"] += 1
            session_data["hooks_triggered"].add(entry.get("hook", "unknown"))

            if entry.get("branch"):
                session_data["branches_touched"].add(entry["branch"])

            if session_data["first_timestamp"] is None or ts < session_data["first_timestamp"]:
                session_data["first_timestamp"] = ts
            if session_data["last_timestamp"] is None or ts > session_data["last_timestamp"]:
                session_data["last_timestamp"] = ts

            decision = entry.get("decision", "approve")
            if decision == "approve":
                session_data["approves"] += 1
            elif decision == "block":
                session_data["blocks"] += 1
                if reason := entry.get("reason"):
                    session_data["block_reasons"].append(reason[:100])  # 理由は100文字まで

        except KeyError:
            continue

    # setをlistに変換
    session_data["hooks_triggered"] = list(session_data["hooks_triggered"])
    session_data["branches_touched"] = list(session_data["branches_touched"])

    return session_data


def analyze_review_threads_from_hooks(session_id: str) -> dict[str, Any]:
    """フック実行ログからレビュースレッド解消情報を分析

    Issue #1419: batch_resolve_threads.py からのレビュースレッド解消情報を収集
    Issue #2190: セッション別ログファイルを読む
    """
    review_data: dict[str, Any] = {
        "resolved_count": 0,
        "batch_resolve_used": False,
    }

    # Issue #2190: セッション別ログファイルから読み込み
    entries = read_session_log_entries(EXECUTION_LOG_DIR, "hook-execution", session_id)

    for entry in entries:
        try:
            hook = entry.get("hook", "")
            details = entry.get("details", {})

            # batch_resolve_threads.py からのログ
            if hook == "batch-resolve-threads":
                if not details.get("dry_run", False):
                    review_data["batch_resolve_used"] = True
                    review_data["resolved_count"] += details.get("resolved_count", 0)

        except KeyError:
            continue

    return review_data


# Issue #1289: Threshold for warning about frequent rebases
REBASE_WARNING_THRESHOLD = 3

# Issue #1409: Efficiency metrics logs
TOOL_EFFICIENCY_LOG = METRICS_LOG_DIR / "tool-efficiency-metrics.log"
REWORK_METRICS_LOG = METRICS_LOG_DIR / "rework-metrics.log"

# Issue #1409: Top N rework files to include in metrics (stored in log)
TOP_REWORK_FILES_LIMIT = 5

# Issue #1409: Top N rework files to show in summary output (for readability)
TOP_REWORK_FILES_SUMMARY_LIMIT = 3


def analyze_efficiency_from_logs(session_id: str) -> dict[str, Any]:
    """効率メトリクスログからセッション情報を分析

    Issue #1409: tool-efficiency-metrics.log と rework-metrics.log を分析し、
    セッション中の効率問題を集計する。

    Args:
        session_id: 分析対象のセッションID

    Returns:
        効率メトリクスの辞書:
        - read_edit_loop_count: Read→Editループ検出回数
        - repeated_search_count: 同一パターン検索回数
        - bash_retry_count: Bash失敗リトライ回数
        - rework_file_count: 高頻度編集ファイル数
        - top_rework_files: 高頻度編集ファイル（上位N件）
    """
    efficiency_data: dict[str, Any] = {
        "read_edit_loop_count": 0,
        "repeated_search_count": 0,
        "bash_retry_count": 0,
        "rework_file_count": 0,
        "top_rework_files": [],
    }

    # tool-efficiency-metrics.log を分析
    if TOOL_EFFICIENCY_LOG.exists():
        try:
            with open(TOOL_EFFICIENCY_LOG, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        if entry.get("session_id") != session_id:
                            continue

                        pattern_name = entry.get("pattern_name", "")
                        if pattern_name == "read_edit_loop":
                            efficiency_data["read_edit_loop_count"] += 1
                        elif pattern_name == "repeated_search":
                            efficiency_data["repeated_search_count"] += 1
                        elif pattern_name == "bash_retry":
                            efficiency_data["bash_retry_count"] += 1
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass  # ログファイル読み込み失敗時は空データを返す

    # rework-metrics.log を分析
    rework_file_counts: dict[str, int] = {}
    if REWORK_METRICS_LOG.exists():
        try:
            with open(REWORK_METRICS_LOG, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        if entry.get("session_id") != session_id:
                            continue

                        file_path = entry.get("file_path", "")
                        if file_path:
                            # フルパスをキーとして使用（同名ファイルの混同を防ぐ）
                            rework_file_counts[file_path] = rework_file_counts.get(file_path, 0) + 1
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass  # ログファイル読み込み失敗時は空データを返す

    # 上位N件の高頻度編集ファイルを抽出
    if rework_file_counts:
        sorted_files = sorted(rework_file_counts.items(), key=lambda x: x[1], reverse=True)
        efficiency_data["rework_file_count"] = len(sorted_files)
        efficiency_data["top_rework_files"] = [f[0] for f in sorted_files[:TOP_REWORK_FILES_LIMIT]]

    return efficiency_data


def analyze_ci_monitoring_from_hooks(session_id: str) -> dict[str, Any]:
    """フック実行ログからCI監視情報を分析

    Issue #1419: リベース回数とCI待機時間を追跡
    Issue #1289: PR単位のリベース回数トラッキングを追加
    Issue #2190: セッション別ログファイルを読む
    """
    ci_data: dict[str, Any] = {
        "rebase_count": 0,
        "ci_wait_minutes": 0,
        "pr_rebases": {},  # Issue #1289: PR番号 -> リベース回数
    }

    # PRごとの最新のmonitor_complete情報を追跡
    pr_wait_seconds: dict[str, int] = {}

    # Issue #2190: セッション別ログファイルから読み込み
    entries = read_session_log_entries(EXECUTION_LOG_DIR, "hook-execution", session_id)

    for entry in entries:
        try:
            hook = entry.get("hook", "")
            details = entry.get("details", {})

            if hook != "ci-monitor":
                continue

            action = details.get("action", "")
            # pr_number が None または欠損の場合は空文字列に変換し、
            # 下の if チェックで除外する
            raw_pr_number = details.get("pr_number")
            pr_number = str(raw_pr_number) if raw_pr_number is not None else ""

            # リベースイベント
            if action == "rebase" and details.get("result") == "success":
                ci_data["rebase_count"] += 1
                # Issue #1289: PR単位でカウント
                if pr_number:
                    ci_data["pr_rebases"][pr_number] = ci_data["pr_rebases"].get(pr_number, 0) + 1

            # モニター完了イベント（CI待機時間）
            # ci-monitor.pyは成功時はtotal_wait_seconds、その他はelapsed_secondsを使用
            if action == "monitor_complete" and pr_number:
                wait_seconds = details.get("total_wait_seconds", details.get("elapsed_seconds", 0))
                # PRごとに最新のmonitor_complete時間を記録
                pr_wait_seconds[pr_number] = wait_seconds

        except KeyError:
            continue

    # PRごとのモニター情報を集計
    total_wait_seconds = sum(pr_wait_seconds.values())
    ci_data["ci_wait_minutes"] = int(total_wait_seconds / 60)

    # Issue #1289: リベース回数が閾値を超えたPRに警告
    pr_rebase_warnings = []
    for pr, count in ci_data["pr_rebases"].items():
        if count >= REBASE_WARNING_THRESHOLD:
            pr_rebase_warnings.append(f"PR #{pr}: {count}回のリベース（merge queue検討を推奨）")
    if pr_rebase_warnings:
        ci_data["pr_rebase_warnings"] = pr_rebase_warnings

    return ci_data


def collect_git_stats() -> dict[str, Any]:
    """Gitの統計情報を収集"""
    stats = {}

    try:
        # 現在のブランチ
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=PROJECT_ROOT,
        )
        if result.returncode == 0:
            stats["current_branch"] = result.stdout.strip()

        # 未コミットの変更数
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=PROJECT_ROOT,
        )
        if result.returncode == 0:
            changes = [line for line in result.stdout.strip().split("\n") if line]
            stats["uncommitted_changes"] = len(changes)

        # worktree数
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=PROJECT_ROOT,
        )
        if result.returncode == 0:
            worktrees = [
                line for line in result.stdout.strip().split("\n") if line.startswith("worktree")
            ]
            stats["worktree_count"] = len(worktrees)

    except Exception:
        pass  # git統計取得失敗時はデフォルト値を返す

    return stats


def collect_pr_stats_for_session() -> dict[str, Any]:
    """このセッションで作成/マージしたPRを収集"""
    stats = {"prs_created": 0, "prs_merged": 0}

    try:
        # 今日作成されたPR
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--author",
                "@me",
                "--limit",
                "10",
                "--json",
                "number,state,createdAt,mergedAt",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=PROJECT_ROOT,
        )

        if result.returncode == 0:
            prs = json.loads(result.stdout)
            today = datetime.now(UTC).date()

            for pr in prs:
                created = datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00"))
                if created.date() == today:
                    stats["prs_created"] += 1
                    if pr.get("mergedAt"):
                        stats["prs_merged"] += 1

    except Exception:
        pass  # PR統計取得失敗時はデフォルト値を返す

    return stats


def is_session_end_recorded(session_id: str) -> bool:
    """セッションIDが既にsession_endとして記録済みかチェック

    Issue #1281: 同一session_idに対してsession_endが重複記録されることを防止

    Args:
        session_id: チェック対象のセッションID

    Returns:
        True: 既にsession_endとして記録済み
        False: 未記録
    """
    if not SESSION_METRICS_LOG.exists():
        return False

    try:
        with open(SESSION_METRICS_LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("session_id") == session_id and entry.get("type") == "session_end":
                        return True
                except json.JSONDecodeError:
                    continue
    except OSError:
        return False

    return False


def record_session_metrics(metrics: dict[str, Any]) -> None:
    """セッションメトリクスを記録"""
    METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with open(SESSION_METRICS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(metrics, ensure_ascii=False, default=str) + "\n")
    except OSError as e:
        print(f"Warning: Failed to write session metrics: {e}", file=sys.stderr)


def main():
    # Issue #2317: 引数ベースのsession_id伝播に対応
    import argparse

    parser = argparse.ArgumentParser(description="Collect session metrics")
    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Claude session ID for log tracking",
    )
    args = parser.parse_args()

    # Issue #2496: handle_session_id_arg の戻り値を使用
    validated_session_id = handle_session_id_arg(args.session_id)
    session_id = validated_session_id or get_fallback_session_id()

    # Issue #1281: 重複session_endの防止
    # 既にsession_endが記録済みの場合はsession_snapshotとして記録
    already_recorded = is_session_end_recorded(session_id)
    record_type = "session_snapshot" if already_recorded else "session_end"

    # セッション情報を収集
    session_data = analyze_session_from_hooks(session_id)
    git_stats = collect_git_stats()
    pr_stats = collect_pr_stats_for_session()
    # Issue #1419: レビュースレッド解消とCI監視の情報を収集
    review_threads_data = analyze_review_threads_from_hooks(session_id)
    ci_monitoring_data = analyze_ci_monitoring_from_hooks(session_id)
    # Issue #1409: 効率メトリクスを収集
    efficiency_data = analyze_efficiency_from_logs(session_id)

    # メトリクスを構築
    metrics = {
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": session_id,
        "type": record_type,
        # セッション統計
        "hook_executions": session_data.get("hook_executions", 0),
        "blocks": session_data.get("blocks", 0),
        "approves": session_data.get("approves", 0),
        "hooks_triggered": session_data.get("hooks_triggered", []),
        "branches_touched": session_data.get("branches_touched", []),
        "block_reasons": session_data.get("block_reasons", []),
        # 時間情報
        "session_start": session_data.get("first_timestamp"),
        "session_end": session_data.get("last_timestamp"),
        # Git統計
        "git": git_stats,
        # PR統計
        "pr": pr_stats,
        # Issue #1419: レビュースレッド解消統計
        "review_threads": review_threads_data,
        # Issue #1419: CI監視統計
        "ci_monitoring": ci_monitoring_data,
        # Issue #1409: 効率メトリクス
        "efficiency": efficiency_data,
    }

    # 記録
    record_session_metrics(metrics)

    # サマリー出力
    duration_str = "N/A"
    if session_data.get("first_timestamp") and session_data.get("last_timestamp"):
        duration = (
            session_data["last_timestamp"] - session_data["first_timestamp"]
        ).total_seconds()
        hours = int(duration // 3600)
        minutes = int((duration % 3600) // 60)
        duration_str = f"{hours}h {minutes}m"

    # Issue #1281: タイプに応じてメッセージを変更
    type_label = "Snapshot" if record_type == "session_snapshot" else "End"
    print(f"Session Metrics Recorded ({type_label}):")
    print(f"  Session ID: {session_id}")
    print(f"  Duration: {duration_str}")
    print(f"  Hook executions: {metrics['hook_executions']}")
    print(f"  Blocks: {metrics['blocks']}")
    print(f"  PRs created today: {pr_stats.get('prs_created', 0)}")
    # Issue #1419: 新しいメトリクスをサマリーに追加
    if review_threads_data.get("resolved_count", 0) > 0:
        print(f"  Resolved threads: {review_threads_data['resolved_count']}")
    if ci_monitoring_data.get("rebase_count", 0) > 0:
        print(f"  Rebases: {ci_monitoring_data['rebase_count']}")
    if ci_monitoring_data.get("ci_wait_minutes", 0) > 0:
        print(f"  CI wait time: {ci_monitoring_data['ci_wait_minutes']}m")
    # Issue #1409: 効率メトリクスをサマリーに追加
    total_inefficiency = (
        efficiency_data.get("read_edit_loop_count", 0)
        + efficiency_data.get("repeated_search_count", 0)
        + efficiency_data.get("bash_retry_count", 0)
    )
    if total_inefficiency > 0:
        print(f"  Inefficiency events: {total_inefficiency}")
        if efficiency_data.get("read_edit_loop_count", 0) > 0:
            print(f"    - Read→Edit loops: {efficiency_data['read_edit_loop_count']}")
        if efficiency_data.get("repeated_search_count", 0) > 0:
            print(f"    - Repeated searches: {efficiency_data['repeated_search_count']}")
        if efficiency_data.get("bash_retry_count", 0) > 0:
            print(f"    - Bash retries: {efficiency_data['bash_retry_count']}")
    if efficiency_data.get("rework_file_count", 0) > 0:
        print(f"  Rework files: {efficiency_data['rework_file_count']}")
        if efficiency_data.get("top_rework_files"):
            # サマリー表示はファイル名のみ（rework-tracker.pyと一貫性を保つ）
            # メトリクスにはフルパスで保存（同名ファイル区別のため）
            top_files = ", ".join(
                Path(p).name
                for p in efficiency_data["top_rework_files"][:TOP_REWORK_FILES_SUMMARY_LIMIT]
            )
            print(f"    Top: {top_files}")


if __name__ == "__main__":
    main()
