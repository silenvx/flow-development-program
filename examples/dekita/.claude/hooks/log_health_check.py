#!/usr/bin/env python3
"""セッション終了時にログの健全性を自動検証する。

Why:
    ログ記録の問題（権限エラー、ディスク不足、セッションID不一致）を
    早期検出することで、メトリクス収集の信頼性を向上させる。

What:
    - ログディレクトリ/ファイルの書き込み権限を確認
    - ディスク容量の閾値チェック
    - ログファイルの鮮度（更新日時）チェック
    - メトリクスとログエントリ数の整合性検証

State:
    - reads: .claude/logs/execution/hook-execution-{session}.jsonl
    - reads: .claude/logs/metrics/session-metrics.log

Remarks:
    - 警告型フック（ブロックしない、systemMessageで警告）
    - Stopで発火
    - session_metrics_collector.pyはメトリクス収集（責務分離）
    - 閾値: 最低100MB空き容量、10分以内の更新、5回以上のフック実行

Changelog:
    - silenvx/dekita#1251: フック追加
    - silenvx/dekita#1455: ログファイル鮮度チェック追加
    - silenvx/dekita#1456: 書き込み権限・ディスク容量チェック追加
    - silenvx/dekita#2068: セッション毎ファイル形式に対応
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from common import EXECUTION_LOG_DIR, METRICS_LOG_DIR
from lib.execution import log_hook_execution
from lib.logging import get_session_log_file
from lib.session import create_hook_context, parse_hook_input

# 閾値定義（Issue #1251で定義）
THRESHOLD_MIN_HOOK_EXECUTIONS = 5  # これ未満で警告
THRESHOLD_MAX_HOOK_EXECUTIONS = 500  # これ以上で情報出力
THRESHOLD_METRICS_LOG_DISCREPANCY_RATIO = 0.5  # メトリクスとログの乖離率閾値
# Issue #1455: ログファイル鮮度チェックの閾値
THRESHOLD_LOG_FRESHNESS_MINUTES = 10  # これを超えると警告

# Issue #1456: 書き込みエラー検出の閾値
THRESHOLD_MIN_DISK_SPACE_MB = 100  # 最低必要なディスク容量（MB）


def get_session_metrics(session_id: str) -> dict[str, Any] | None:
    """セッションメトリクスログから当該セッションのメトリクスを取得。

    Args:
        session_id: 検索するセッションID

    Returns:
        メトリクスdict、見つからない場合はNone
    """
    metrics_file = METRICS_LOG_DIR / "session-metrics.log"
    if not metrics_file.exists():
        return None

    try:
        # メモリ効率のため、1行ずつ読み込んで最後に見つかったエントリを保持
        found_entry = None
        with open(metrics_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("session_id") == session_id:
                        found_entry = entry  # 最後に見つかったエントリを保持
                except json.JSONDecodeError:
                    continue
        return found_entry
    except OSError:
        return None


def check_log_freshness(
    log_file: Path, threshold_minutes: int = THRESHOLD_LOG_FRESHNESS_MINUTES
) -> tuple[bool, float | None]:
    """ログファイルの鮮度をチェック。

    Issue #1455: ログファイルの更新日時チェック機能を追加。

    Args:
        log_file: チェック対象のログファイルパス
        threshold_minutes: 古いと判断する閾値（分）

    Returns:
        (is_fresh, age_minutes) - is_freshはファイルが新鮮かどうか、
        age_minutesはファイルの経過時間（分）。ファイルが存在しない場合はNone。
    """
    if not log_file.exists():
        return False, None

    try:
        mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
        age = datetime.now() - mtime
        age_minutes = age.total_seconds() / 60
        is_fresh = age < timedelta(minutes=threshold_minutes)
        return is_fresh, round(age_minutes, 1)
    except OSError:
        return False, None


def count_hook_executions_in_log(session_id: str) -> int:
    """セッション固有のhook-executionログからエントリ数をカウント。

    Issue #2068: セッション毎ファイル形式に対応。
    ファイル形式: hook-execution-{session_id}.jsonl

    Args:
        session_id: 検索するセッションID

    Returns:
        エントリ数
    """
    if not session_id:
        return 0

    session_log_file = get_session_log_file(EXECUTION_LOG_DIR, "hook-execution", session_id)
    if not session_log_file.exists():
        return 0

    count = 0
    try:
        with open(session_log_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    _ = json.loads(line)  # validate JSON and discard result
                    count += 1
                except json.JSONDecodeError:
                    continue
        return count
    except OSError:
        return 0


def check_log_writable(log_path: Path) -> tuple[bool, str | None]:
    """ログファイルまたはディレクトリが書き込み可能か確認する。

    Issue #1456: ログファイルの書き込みエラー検出機能

    Args:
        log_path: チェック対象のログファイルまたはディレクトリのパス

    Returns:
        (is_writable, error_message) - 書き込み可能ならTrue、エラーがあればメッセージを返す
    """
    try:
        # ディレクトリの場合（POSIX: 書き込み+実行権限が必要）
        if log_path.is_dir():
            if not os.access(log_path, os.W_OK | os.X_OK):
                return False, f"ディレクトリへの書き込み/実行権限がありません: {log_path}"
            return True, None

        # ファイルの場合
        if log_path.exists():
            if not os.access(log_path, os.W_OK):
                return False, f"ファイルへの書き込み権限がありません: {log_path}"
            return True, None

        # ファイルが存在しない場合、親ディレクトリをチェック（POSIX: 書き込み+実行権限が必要）
        parent_dir = log_path.parent
        if not parent_dir.exists():
            return False, f"ログディレクトリが存在しません: {parent_dir}"
        if not os.access(parent_dir, os.W_OK | os.X_OK):
            return False, f"ログディレクトリへの書き込み/実行権限がありません: {parent_dir}"

        return True, None
    except OSError as e:
        return False, f"書き込み権限チェック中にエラー: {e}"


def check_disk_space(log_path: Path) -> tuple[bool, str | None, int]:
    """ログディレクトリのディスク容量を確認する。

    Issue #1456: ディスク容量不足の検出

    Args:
        log_path: チェック対象のログファイルまたはディレクトリのパス

    Returns:
        (is_sufficient, error_message, free_mb) - 容量が十分ならTrue、空き容量（MB）を返す
    """
    try:
        # ファイルの場合は親ディレクトリをチェック
        check_path = log_path if log_path.is_dir() else log_path.parent

        # ディレクトリが存在しない場合、存在する親を探す
        while not check_path.exists() and check_path != check_path.parent:
            check_path = check_path.parent

        if not check_path.exists():
            return False, "ディスク容量チェック対象のパスが存在しません", 0

        usage = shutil.disk_usage(check_path)
        free_mb = usage.free // (1024 * 1024)

        if free_mb < THRESHOLD_MIN_DISK_SPACE_MB:
            return (
                False,
                f"ディスク容量が不足しています: 空き {free_mb}MB (閾値: {THRESHOLD_MIN_DISK_SPACE_MB}MB)",
                free_mb,
            )

        return True, None, free_mb
    except OSError as e:
        return False, f"ディスク容量チェック中にエラー: {e}", 0


def check_log_health(session_id: str) -> list[dict[str, Any]]:
    """ログの健全性をチェックし、問題を検出。

    Issue #2068: セッション毎ファイル形式に対応。

    Args:
        session_id: チェック対象のセッションID

    Returns:
        検出された問題のリスト（level, message, detailsを含む）
    """
    issues: list[dict[str, Any]] = []

    # Issue #1456: 書き込みエラー検出
    # 0. ログディレクトリとファイルの書き込み権限チェック
    # Issue #2068: セッション毎ファイル形式に対応
    log_paths_to_check: list[Path] = [
        EXECUTION_LOG_DIR,
        METRICS_LOG_DIR,
        METRICS_LOG_DIR / "session-metrics.log",
    ]

    # セッション固有のログファイルも権限チェック対象に追加
    # これにより、ファイルレベルの権限問題も検出可能
    if session_id:
        session_log_file = get_session_log_file(EXECUTION_LOG_DIR, "hook-execution", session_id)
        if session_log_file.exists():
            log_paths_to_check.append(session_log_file)

    for log_path in log_paths_to_check:
        is_writable, error_msg = check_log_writable(log_path)
        if not is_writable:
            issues.append(
                {
                    "level": "ERROR",
                    "message": "ログ書き込み権限エラー",
                    "details": {
                        "path": str(log_path),
                        "error": error_msg,
                        "possible_cause": "パーミッション設定を確認してください",
                    },
                }
            )

    # 0b. ディスク容量チェック
    is_sufficient, disk_error, free_mb = check_disk_space(METRICS_LOG_DIR)
    if not is_sufficient:
        issues.append(
            {
                "level": "WARNING",
                "message": "ディスク容量警告",
                "details": {
                    "path": str(METRICS_LOG_DIR),
                    "free_mb": free_mb,
                    "threshold_mb": THRESHOLD_MIN_DISK_SPACE_MB,
                    "error": disk_error,
                    "possible_cause": "不要なファイルを削除してディスク容量を確保してください",
                },
            }
        )

    # Issue #1455 + #2068: ログファイルの鮮度チェック（セッション固有ファイル対応）
    metrics_log_file = METRICS_LOG_DIR / "session-metrics.log"

    # セッション固有のhook-executionログの鮮度チェック
    if session_id:
        session_log_file = get_session_log_file(EXECUTION_LOG_DIR, "hook-execution", session_id)
        hook_log_fresh, hook_log_age = check_log_freshness(session_log_file)
        if hook_log_age is not None and not hook_log_fresh:
            issues.append(
                {
                    "level": "WARNING",
                    "message": f"セッションログの更新が古いです: {hook_log_age}分前",
                    "details": {
                        "log_file": str(session_log_file),
                        "age_minutes": hook_log_age,
                        "threshold_minutes": THRESHOLD_LOG_FRESHNESS_MINUTES,
                        "possible_cause": "フックログ記録が停止している可能性",
                    },
                }
            )

    # session-metrics.logの鮮度チェック
    metrics_log_fresh, metrics_log_age = check_log_freshness(metrics_log_file)
    if metrics_log_age is not None and not metrics_log_fresh:
        issues.append(
            {
                "level": "WARNING",
                "message": f"session-metrics.logの更新が古いです: {metrics_log_age}分前",
                "details": {
                    "log_file": str(metrics_log_file),
                    "age_minutes": metrics_log_age,
                    "threshold_minutes": THRESHOLD_LOG_FRESHNESS_MINUTES,
                    "possible_cause": "メトリクス収集が停止している可能性",
                },
            }
        )

    # 1. セッション固有ログのエントリ確認
    log_entry_count = count_hook_executions_in_log(session_id)

    # 2. session-metrics.logのメトリクス確認
    metrics = get_session_metrics(session_id)

    # 3. 異常検知
    if metrics:
        hook_executions = metrics.get("hook_executions", 0)
        blocks = metrics.get("blocks", 0)
        approves = metrics.get("approves", 0)

        # メトリクス全てゼロの検出（session_id不一致の可能性）
        if hook_executions == 0 and blocks == 0 and approves == 0:
            issues.append(
                {
                    "level": "ERROR",
                    "message": "セッションメトリクスが全てゼロです",
                    "details": {
                        "session_id": session_id,
                        "possible_cause": "session_id不一致の可能性があります",
                        "reference": "Issue #1232",
                    },
                }
            )
        # フック実行回数が少なすぎる
        elif hook_executions < THRESHOLD_MIN_HOOK_EXECUTIONS:
            issues.append(
                {
                    "level": "WARNING",
                    "message": f"フック実行回数が少なすぎます: {hook_executions}回",
                    "details": {
                        "session_id": session_id,
                        "threshold": THRESHOLD_MIN_HOOK_EXECUTIONS,
                        "possible_cause": "セッションが短すぎる、またはログ記録の問題",
                    },
                }
            )
        # フック実行回数が多い（情報のみ）
        elif hook_executions > THRESHOLD_MAX_HOOK_EXECUTIONS:
            issues.append(
                {
                    "level": "INFO",
                    "message": f"長時間セッション: フック実行回数 {hook_executions}回",
                    "details": {
                        "session_id": session_id,
                        "threshold": THRESHOLD_MAX_HOOK_EXECUTIONS,
                    },
                }
            )

        # メトリクスとログエントリ数の整合性チェック
        if log_entry_count > 0 and hook_executions > 0:
            ratio = abs(hook_executions - log_entry_count) / max(hook_executions, log_entry_count)
            if ratio > THRESHOLD_METRICS_LOG_DISCREPANCY_RATIO:
                issues.append(
                    {
                        "level": "WARNING",
                        "message": "メトリクスとログエントリ数に大きな乖離があります",
                        "details": {
                            "session_id": session_id,
                            "metrics_hook_executions": hook_executions,
                            "log_entry_count": log_entry_count,
                            "discrepancy_ratio": round(ratio, 2),
                            "threshold": THRESHOLD_METRICS_LOG_DISCREPANCY_RATIO,
                            "possible_cause": "ログ記録の重複または欠落の可能性",
                        },
                    }
                )
    else:
        # メトリクスが見つからない場合
        # まだ収集されていない可能性があるため、log_entry_countでフォールバック
        if log_entry_count == 0:
            issues.append(
                {
                    "level": "WARNING",
                    "message": "セッションログにエントリがありません",
                    "details": {
                        "session_id": session_id,
                        "possible_cause": "ログ記録が正常に動作していない可能性",
                    },
                }
            )
        elif log_entry_count < THRESHOLD_MIN_HOOK_EXECUTIONS:
            issues.append(
                {
                    "level": "WARNING",
                    "message": f"フック実行回数が少なすぎます（ログ直接カウント）: {log_entry_count}回",
                    "details": {
                        "session_id": session_id,
                        "threshold": THRESHOLD_MIN_HOOK_EXECUTIONS,
                        "note": "session-metrics.logにエントリなし、セッションログから直接カウント",
                    },
                }
            )

    return issues


def format_health_report(issues: list[dict[str, Any]]) -> str:
    """健全性レポートをフォーマット。

    Args:
        issues: 検出された問題のリスト

    Returns:
        フォーマットされたレポート文字列
    """
    if not issues:
        return ""

    lines = ["\n[log_health_check] ログ健全性レポート:"]

    for issue in issues:
        level = issue.get("level", "INFO")
        message = issue.get("message", "")
        details = issue.get("details", {}) or {}

        prefix = "❌" if level == "ERROR" else "⚠️" if level == "WARNING" else "ℹ️"
        lines.append(f"  {prefix} [{level}] {message}")

        # 代表的なキーは従来どおり個別に出力
        if details.get("possible_cause"):
            lines.append(f"      原因: {details['possible_cause']}")
        if details.get("reference"):
            lines.append(f"      参照: {details['reference']}")

        # 上記以外の details のキーも汎用的に出力する
        for key, value in details.items():
            if key in ("possible_cause", "reference", "session_id"):
                continue
            if value is None or value == "":
                continue
            if isinstance(value, (dict, list)):
                formatted_value = json.dumps(value, ensure_ascii=False)
            else:
                formatted_value = str(value)
            lines.append(f"      {key}: {formatted_value}")

    return "\n".join(lines)


def main() -> None:
    """Main entry point for the hook."""
    hook_input = parse_hook_input()

    ctx = create_hook_context(hook_input)

    # Prevent infinite loops in Stop hooks
    if hook_input.get("stop_hook_active"):
        print(json.dumps({"decision": "approve"}))
        return

    session_id = hook_input.get("session_id") or ctx.get_session_id()

    # 健全性チェック実行
    issues = check_log_health(session_id)

    # ログに記録
    has_errors = any(issue.get("level") == "ERROR" for issue in issues)
    has_warnings = any(issue.get("level") == "WARNING" for issue in issues)

    log_hook_execution(
        "log_health_check",
        "approve",
        f"Health check completed: {len(issues)} issue(s) found",
        {
            "issues_count": len(issues),
            "has_errors": has_errors,
            "has_warnings": has_warnings,
            "issues": issues,
        },
    )

    # レポート生成
    report = format_health_report(issues)

    # 常にapprove（ブロックしない）、問題があればsystemMessageで通知
    result: dict[str, Any] = {"decision": "approve"}
    if report:
        result["systemMessage"] = report

    print(json.dumps(result))


if __name__ == "__main__":
    main()
