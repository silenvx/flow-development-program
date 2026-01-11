#!/usr/bin/env python3
"""継続セッションを検出し、前セッションのメトリクス記録と開発フローリマインダーを表示する。

Why:
    コンテキスト継続（context resumption）時はStop hookが発火しないため、
    前セッションのメトリクスが失われる。また開発フローの意識がリセットされ
    手順スキップによる連続ブロックが発生する。

What:
    - handoff-state.jsonの更新時刻で継続セッションを判定
    - 未記録の前セッションメトリクスを収集・記録
    - 開発フローチェックリストを表示

State:
    - reads: .claude/state/handoff-state.json
    - reads: .claude/logs/metrics/session-metrics.log
    - reads: .claude/logs/execution/hook-execution-{session}.jsonl
    - writes: .claude/logs/metrics/session-metrics.log

Remarks:
    - 情報注入型フック（ブロックしない、systemMessageで情報表示）
    - SessionStartで発火
    - collect_session_metrics.pyスクリプトを呼び出してメトリクス収集
    - 継続判定の時間窓は5分（CONTINUATION_WINDOW_MINUTES）
    - 1回の継続で最大3セッション分のメトリクスを収集

Changelog:
    - silenvx/dekita#1433: 継続セッションメトリクス記録
    - silenvx/dekita#2006: 開発フローリマインダー追加
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

# 共通モジュール
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.constants import TIMEOUT_HEAVY
from lib.execution import log_hook_execution
from lib.logging import read_all_session_log_entries
from lib.session import create_hook_context, parse_hook_input

SCRIPT_DIR = HOOKS_DIR.parent / "scripts"
LOGS_DIR = HOOKS_DIR.parent / "logs"
METRICS_LOG_DIR = LOGS_DIR / "metrics"
SESSION_METRICS_LOG = METRICS_LOG_DIR / "session-metrics.log"
EXECUTION_LOG_DIR = LOGS_DIR / "execution"

# セッション継続判定の時間窓（分）
# Claude Codeのcontext resumptionは通常5分以内に発生する
CONTINUATION_WINDOW_MINUTES = 5

# 1回の継続セッションで収集する最大セッション数
# メトリクス収集は重い処理のため、SessionStart時の遅延を抑えるために制限
MAX_SESSIONS_TO_COLLECT = 3


class HandoffSummary(TypedDict, total=False):
    """ハンドオフサマリー情報の型定義。

    Note:
        total=False を指定しているのは、handoff-state.json に保存される
        previous_work_status / previous_next_action などの情報が、
        状況によっては存在しない場合があるため（すべてのキーを
        オプショナル扱いにする）。
    """

    previous_work_status: str
    previous_next_action: str
    previous_block_count: int
    previous_block_reasons: list[str]
    pending_tasks_count: int
    open_prs_count: int


def is_continuation_session() -> bool:
    """継続セッションかどうかを判定

    Claude Codeはcontext window overflow時に自動的にセッションを継続する。
    この場合、handoff summaryから継続されるため、特定の検出が必要。

    判定基準:
    handoff-state.jsonが存在し、最近（CONTINUATION_WINDOW_MINUTES分以内）に
    更新されている場合、前セッションからの継続と判断する。
    """
    try:
        handoff_state = HOOKS_DIR.parent / "state" / "handoff-state.json"
        if handoff_state.exists():
            mtime = datetime.fromtimestamp(handoff_state.stat().st_mtime, tz=UTC)
            now = datetime.now(UTC)
            age_minutes = (now - mtime).total_seconds() / 60
            if age_minutes < CONTINUATION_WINDOW_MINUTES:
                return True
    except (FileNotFoundError, OSError):
        pass  # ファイルアクセスエラーは無視（継続セッションではないと判断）

    return False


def get_handoff_summary(session_id: str | None = None) -> HandoffSummary:
    """ハンドオフファイルからサマリー情報を取得

    Issue #1273: 継続セッション時のコンテキスト引き継ぎログ

    Args:
        session_id: 取得対象のセッションID。指定時はそのセッションの
                    ハンドオフファイルを優先的に読み込む。
                    未指定時は最新のハンドオフファイルを使用。
    """
    handoff_dir = HOOKS_DIR.parent / "handoff"
    if not handoff_dir.exists():
        return {}

    try:
        handoff_file = None

        # セッションID指定時は対応するファイルを優先
        if session_id:
            specific_file = handoff_dir / f"{session_id}.json"
            if specific_file.exists():
                handoff_file = specific_file

        # フォールバック: 最新のハンドオフファイルを取得
        if handoff_file is None:
            handoff_files = sorted(
                handoff_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True
            )
            if not handoff_files:
                return {}
            handoff_file = handoff_files[0]

        with open(handoff_file, encoding="utf-8") as f:
            handoff_data = json.load(f)

        # サマリー情報を抽出（None値は除外）
        session_summary = handoff_data.get("session_summary", {})
        result = {
            "previous_work_status": handoff_data.get("work_status"),
            "previous_next_action": handoff_data.get("next_action"),
            "previous_block_count": session_summary.get("blocks", 0),
            "previous_block_reasons": session_summary.get("block_reasons", [])[:3],
            "pending_tasks_count": len(handoff_data.get("pending_tasks", [])),
            "open_prs_count": len(handoff_data.get("open_prs", [])),
        }
        # None値を除外
        return {k: v for k, v in result.items() if v is not None}
    except (OSError, json.JSONDecodeError):
        return {}


def get_recorded_session_ids() -> set[str]:
    """session-metrics.logに記録済みのメトリクスセッションIDを取得

    継続マーカー（type: session_continuation）はメトリクスではないため除外する。
    これにより、連続継続（A→B→C）でセッションBのメトリクスが失われることを防ぐ。
    """
    recorded = set()
    if not SESSION_METRICS_LOG.exists():
        return recorded

    try:
        with open(SESSION_METRICS_LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    # 継続マーカーはメトリクスではないので除外
                    if entry.get("type") == "session_continuation":
                        continue
                    if sid := entry.get("session_id"):
                        recorded.add(sid)
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, OSError):
        pass  # ログファイル読み込みエラーは無視（空セットを返す）

    return recorded


def get_last_recorded_session_id() -> str | None:
    """session-metrics.logから最後に記録されたメトリクスのセッションIDを取得

    継続マーカー（type: session_continuation）はメトリクスではないため除外する。
    """
    if not SESSION_METRICS_LOG.exists():
        return None

    try:
        # メモリ効率のため、全行読み込みではなく1行ずつ走査して最後を保持
        last_metrics_sid: str | None = None
        with open(SESSION_METRICS_LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    # 継続マーカーはメトリクスではないのでスキップ
                    if entry.get("type") == "session_continuation":
                        continue
                    if sid := entry.get("session_id"):
                        last_metrics_sid = sid
                except json.JSONDecodeError:
                    continue
        return last_metrics_sid
    except (FileNotFoundError, OSError):
        pass  # ログファイル読み込みエラーは無視（Noneを返す）

    return None


def get_session_ids_from_hook_log(hours: int = 24) -> list[str]:
    """セッション別hook-execution logから過去N時間のセッションIDを最新順で取得

    Returns:
        最新のセッションから順にソートされたセッションIDのリスト
    """
    # Read from all session-specific log files
    entries = read_all_session_log_entries(EXECUTION_LOG_DIR, "hook-execution")

    session_last_seen: dict[str, float] = {}
    cutoff = datetime.now(UTC).timestamp() - (hours * 3600)

    for entry in entries:
        try:
            ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
            if ts >= cutoff:
                if sid := entry.get("session_id"):
                    # 最新のタイムスタンプを記録
                    if sid not in session_last_seen or ts > session_last_seen[sid]:
                        session_last_seen[sid] = ts
        except (KeyError, ValueError):
            continue

    # 最新順にソートして返す
    return sorted(session_last_seen.keys(), key=lambda x: session_last_seen[x], reverse=True)


def collect_metrics_for_session(session_id: str) -> bool:
    """指定されたセッションIDのメトリクスを収集"""
    collect_script = SCRIPT_DIR / "collect_session_metrics.py"
    if not collect_script.exists():
        return False

    try:
        # Issue #2317: 環境変数ではなくコマンドライン引数でsession_idを渡す
        result = subprocess.run(
            ["python3", str(collect_script), "--session-id", session_id],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False  # サブプロセス実行エラーは無視（失敗として扱う）


def record_continuation_marker(current_session_id: str, previous_session_id: str | None) -> None:
    """継続セッションのマーカーをメトリクスログに記録"""
    METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)

    marker = {
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": current_session_id,
        "type": "session_continuation",
        "previous_session_id": previous_session_id,
    }

    try:
        with open(SESSION_METRICS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(marker, ensure_ascii=False) + "\n")
    except OSError:
        pass  # ファイル書き込みエラーは無視（メトリクス記録は任意）


def build_development_flow_reminder(handoff_summary: HandoffSummary) -> str:
    """開発フローリマインダーメッセージを構築

    Issue #2006: セッション継続時に開発フローの意識がリセットされる問題に対応。
    チェックリストを表示して、手順スキップを防ぐ。
    """
    # 前セッションの作業状態を取得
    work_status = handoff_summary.get("previous_work_status", "不明")
    next_action = handoff_summary.get("previous_next_action", "")
    pending_tasks = handoff_summary.get("pending_tasks_count", 0)
    open_prs = handoff_summary.get("open_prs_count", 0)

    lines = [
        "📋 **セッション継続 - 開発フローチェックリスト**",
        "",
        f"前セッションの状態: {work_status}",
    ]

    if next_action:
        lines.append(f"次のアクション: {next_action}")

    if pending_tasks > 0 or open_prs > 0:
        lines.append("")
        if pending_tasks > 0:
            lines.append(f"- 保留タスク: {pending_tasks}件")
        if open_prs > 0:
            lines.append(f"- オープンPR: {open_prs}件")

    lines.extend(
        [
            "",
            "**作業開始前に確認**:",
            "- [ ] Issue作成前に調査・探索を実施したか",
            "- [ ] Worktree作成前にプランを作成したか",
            "- [ ] Push前にCodexレビューを実施したか",
            "",
            "💡 各ステップのスキップは個別フックがブロックします。",
        ]
    )

    return "\n".join(lines)


def main():
    """継続セッション検出とメトリクス記録"""
    # SessionStartフックからの入力を解析（session_id取得のため）
    input_data = parse_hook_input()
    ctx = create_hook_context(input_data)

    current_session_id = ctx.get_session_id()
    is_continuation = is_continuation_session()

    if not is_continuation:
        # 通常のセッション開始 - 何もしない
        log_hook_execution(
            "continuation-session-metrics",
            "approve",
            "Normal session start",
            {"is_continuation": False},
        )
        print(json.dumps({"continue": True}))
        return

    # 継続セッション検出
    recorded_sessions = get_recorded_session_ids()
    recent_sessions = get_session_ids_from_hook_log(hours=24)

    # 未記録のセッションを特定（現在のセッションと記録済みセッションを除外）
    unrecorded_sessions = []
    for sid in recent_sessions:
        if sid != current_session_id and sid not in recorded_sessions:
            unrecorded_sessions.append(sid)

    # 未記録セッションのメトリクスを収集
    recorded_count = 0
    collected_sessions: list[str] = []
    for sid in unrecorded_sessions[:MAX_SESSIONS_TO_COLLECT]:
        if collect_metrics_for_session(sid):
            recorded_count += 1
            collected_sessions.append(sid)

    # 継続マーカーを記録
    # previous_session_idは収集後に決定（Codex CLI review指摘: 収集前の値だとチェーンが不正確）
    # 優先順位: 1. 今回収集した最新セッション, 2. 既存の最新記録済みセッション
    if collected_sessions:
        # 収集したセッションのうち最初のもの（= 最新）を前セッションとする
        previous_session_id = collected_sessions[0]
    else:
        # 収集がなければ既存の最新記録済みセッションを使用
        previous_session_id = get_last_recorded_session_id()

    record_continuation_marker(current_session_id, previous_session_id)

    # Issue #1273: ハンドオフサマリーを取得してログに記録
    # previous_session_idを渡して、正確なセッションのハンドオフを取得
    handoff_summary = get_handoff_summary(previous_session_id)

    log_details = {
        "is_continuation": True,
        "previous_session_id": previous_session_id,
        "unrecorded_sessions": len(unrecorded_sessions),
        "recorded_count": recorded_count,
    }

    # ハンドオフサマリーがあれば追加
    if handoff_summary:
        log_details["handoff_summary"] = handoff_summary

    log_hook_execution(
        "continuation-session-metrics",
        "approve",
        f"Continuation session detected, recorded {recorded_count} previous sessions",
        log_details,
    )

    # Issue #2006: 継続セッション時に開発フローリマインダーを表示
    # これにより、手順スキップによる連続ブロックを防ぐ
    reminder_message = build_development_flow_reminder(handoff_summary)
    print(json.dumps({"continue": True, "message": reminder_message}))


if __name__ == "__main__":
    main()
