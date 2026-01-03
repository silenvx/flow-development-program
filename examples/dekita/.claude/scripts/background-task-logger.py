#!/usr/bin/env python3
"""バックグラウンドタスクのログを永続化する。

Why:
    コンテキスト要約時にバックグラウンドタスクの出力が失われる問題を解決し、
    セッション終了後もログを確認可能にするため。

What:
    - log_background_event(): イベントをファイルに記録
    - list_events(): イベント一覧を表示
    - filter_by_session(): セッション別にフィルタリング

State:
    - writes: .claude/logs/background-tasks/events.jsonl

Remarks:
    - ログローテーション: 10MB、最大5ファイル
    - --session-id, --task, --since でフィルタリング可能
    - SRP: バックグラウンドタスクのログ記録と表示のみを担当

Changelog:
    - silenvx/dekita#1422: バックグラウンドタスクログ永続化機能を追加
    - silenvx/dekita#2496: os.getppid()ベースのフォールバックに対応
"""

from __future__ import annotations

import argparse
import json

# Issue #2496: Use os.getppid() for PPID-based fallback
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ログディレクトリの設定
LOGS_DIR = Path(__file__).parent.parent / "logs"
BACKGROUND_LOGS_DIR = LOGS_DIR / "background-tasks"
LOG_FILE = BACKGROUND_LOGS_DIR / "events.jsonl"

# ログローテーション設定
MAX_LOG_SIZE_MB = 10
MAX_LOG_FILES = 5

JST = timezone(timedelta(hours=9))


def ensure_log_dir() -> None:
    """ログディレクトリを作成する。"""
    BACKGROUND_LOGS_DIR.mkdir(parents=True, exist_ok=True)


def rotate_logs_if_needed() -> None:
    """ログファイルが大きくなりすぎたらローテーションする。"""
    if not LOG_FILE.exists():
        return

    size_mb = LOG_FILE.stat().st_size / (1024 * 1024)
    if size_mb < MAX_LOG_SIZE_MB:
        return

    # ローテーション: events.jsonl -> events.jsonl.1 -> ... -> events.jsonl.5
    for i in range(MAX_LOG_FILES, 0, -1):
        old_file = LOG_FILE.with_suffix(f".jsonl.{i}")
        if old_file.exists():
            if i == MAX_LOG_FILES:
                old_file.unlink()  # 最古のファイルは削除
            else:
                new_file = LOG_FILE.with_suffix(f".jsonl.{i + 1}")
                old_file.rename(new_file)

    # 現在のファイルを.1に移動
    LOG_FILE.rename(LOG_FILE.with_suffix(".jsonl.1"))


def get_session_id() -> str:
    """現在のセッションIDを取得する。

    Issue #2317: CLAUDE_SESSION_ID環境変数を廃止し引数ベースに統一。
    Issue #2496: Use PPID-based fallback.
    """
    return f"ppid-{os.getppid()}"


def log_background_event(
    task_name: str,
    event_type: str,
    details: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> None:
    """バックグラウンドタスクのイベントをログに記録する。

    Args:
        task_name: タスク名（例: "ci-monitor", "codex-review"）
        event_type: イベント種別（例: "CI_PASSED", "REVIEW_COMPLETED"）
        details: 追加の詳細情報
        session_id: セッションID（指定しない場合は自動取得）
    """
    ensure_log_dir()
    rotate_logs_if_needed()

    event = {
        "timestamp": datetime.now(JST).isoformat(),
        "session_id": session_id or get_session_id(),
        "task_name": task_name,
        "event_type": event_type,
        "details": details or {},
    }

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_logs(
    session_id: str | None = None,
    task_name: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """ログを読み込む。

    Args:
        session_id: フィルタするセッションID
        task_name: フィルタするタスク名
        since: この日時以降のログのみ
        limit: 最大件数

    Returns:
        ログイベントのリスト（新しい順）
    """
    if not LOG_FILE.exists():
        return []

    events: list[dict[str, Any]] = []

    # 現在のログファイルと過去のローテーションファイルを読む
    log_files = [LOG_FILE]
    for i in range(1, MAX_LOG_FILES + 1):
        rotated = LOG_FILE.with_suffix(f".jsonl.{i}")
        if rotated.exists():
            log_files.append(rotated)

    for log_file in log_files:
        with open(log_file, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)

                    # フィルタリング
                    if session_id and event.get("session_id") != session_id:
                        continue
                    if task_name and event.get("task_name") != task_name:
                        continue
                    if since:
                        event_time = datetime.fromisoformat(event["timestamp"])
                        if event_time < since:
                            continue

                    events.append(event)
                except json.JSONDecodeError:
                    continue

    # 新しい順にソート
    events.sort(key=lambda x: x["timestamp"], reverse=True)

    return events[:limit]


def print_events(events: list[dict[str, Any]]) -> None:
    """イベントを表示する。"""
    if not events:
        print("ログが見つかりませんでした。")
        return

    for event in events:
        timestamp = event["timestamp"][:19].replace("T", " ")
        task = event["task_name"]
        event_type = event["event_type"]
        session = event["session_id"][:8] if len(event["session_id"]) > 8 else event["session_id"]

        print(f"[{timestamp}] {task}: {event_type} (session: {session})")

        details = event.get("details", {})
        if details:
            for key, value in details.items():
                print(f"    {key}: {value}")


def get_summary() -> dict[str, Any]:
    """ログのサマリーを取得する。"""
    events = read_logs(limit=1000)

    if not events:
        return {"total": 0, "sessions": 0, "by_task": {}, "by_event_type": {}}

    by_task: dict[str, int] = {}
    by_event_type: dict[str, int] = {}
    sessions: set[str] = set()

    for event in events:
        task = event["task_name"]
        event_type = event["event_type"]
        by_task[task] = by_task.get(task, 0) + 1
        by_event_type[event_type] = by_event_type.get(event_type, 0) + 1
        sessions.add(event["session_id"])

    return {
        "total": len(events),
        "sessions": len(sessions),
        "by_task": by_task,
        "by_event_type": by_event_type,
    }


def print_summary() -> None:
    """サマリーを表示する。"""
    summary = get_summary()

    print("=== バックグラウンドタスクログ サマリー ===\n")
    print(f"総イベント数: {summary['total']}")
    print(f"セッション数: {summary['sessions']}")

    if summary["by_task"]:
        print("\n--- タスク別 ---")
        for task, count in sorted(summary["by_task"].items()):
            print(f"  {task}: {count}")

    if summary["by_event_type"]:
        print("\n--- イベント種別 ---")
        for event_type, count in sorted(summary["by_event_type"].items()):
            print(f"  {event_type}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="バックグラウンドタスクのログを表示・管理")
    parser.add_argument("--list", action="store_true", help="最新のログを表示")
    parser.add_argument("--session-id", type=str, help="特定のセッションのログを表示")
    parser.add_argument("--task", type=str, help="特定のタスクのログを表示")
    parser.add_argument("--since", type=str, help="指定日時以降のログを表示 (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=50, help="表示件数（デフォルト: 50）")
    parser.add_argument("--summary", action="store_true", help="サマリーを表示")
    parser.add_argument("--json", action="store_true", help="JSON形式で出力")

    args = parser.parse_args()

    # アクションが指定されていない場合はヘルプを表示
    has_filter = args.session_id or args.task or args.since
    if not args.list and not args.summary and not has_filter:
        parser.print_help()
        sys.exit(0)

    if args.summary:
        if args.json:
            print(json.dumps(get_summary(), ensure_ascii=False, indent=2))
        else:
            print_summary()
        return

    # sinceの解析
    since = None
    if args.since:
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=JST)
        except ValueError:
            print(f"日付形式が不正です: {args.since} (YYYY-MM-DD形式で指定)", file=sys.stderr)
            sys.exit(1)

    events = read_logs(
        session_id=args.session_id,
        task_name=args.task,
        since=since,
        limit=args.limit,
    )

    if args.json:
        print(json.dumps(events, ensure_ascii=False, indent=2))
    else:
        print_events(events)


if __name__ == "__main__":
    main()
