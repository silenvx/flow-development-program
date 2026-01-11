#!/usr/bin/env python3
"""セッション終了時に引き継ぎメモを生成。

Why:
    Claude Codeはセッション間で記憶を保持しない。作業状態、
    未対応タスク、教訓を記録して次のセッションに引き継ぐ。

What:
    - セッション終了時（Stop）に発火
    - Git状態、worktree、オープンPRを収集
    - セッションサマリー（ブロック回数等）を抽出
    - ブロック理由から教訓を自動生成
    - .claude/handoff/にセッションIDベースで保存

State:
    - reads: .claude/logs/execution/hook-execution-*.jsonl
    - writes: .claude/handoff/{session_id}.json

Remarks:
    - 非ブロック型（情報保存のみ）
    - session-handoff-readerが読み込み、本フックが生成
    - 古いファイルは10個まで保持（自動クリーンアップ）

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#1333: ブロック理由からの教訓抽出
    - silenvx/dekita#2545: HookContextパターン移行
"""

from __future__ import annotations

import json

# 共通モジュール
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from common import EXECUTION_LOG_DIR
from lib.constants import TIMEOUT_HEAVY, TIMEOUT_LIGHT
from lib.execution import log_hook_execution
from lib.git import get_current_branch
from lib.logging import read_session_log_entries
from lib.session import create_hook_context, parse_hook_input

# 引き継ぎメモの保存先
HANDOFF_DIR = HOOKS_DIR.parent / "handoff"

# 保持するハンドオフファイルの最大数
MAX_HANDOFF_FILES = 10


def get_git_status() -> dict[str, Any]:
    """Git状態を取得"""
    status = {
        "branch": get_current_branch(),
        "uncommitted_changes": 0,
        "untracked_files": 0,
    }

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            lines = [line for line in result.stdout.strip().split("\n") if line]
            status["uncommitted_changes"] = len(
                [line for line in lines if not line.startswith("??")]
            )
            status["untracked_files"] = len([line for line in lines if line.startswith("??")])
    except Exception:
        pass  # Best effort - git command may fail

    return status


def get_active_worktrees() -> list[dict[str, str]]:
    """アクティブなworktreeを取得"""
    worktrees = []
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            current_worktree = {}
            for line in result.stdout.strip().split("\n"):
                if line.startswith("worktree "):
                    if current_worktree:
                        worktrees.append(current_worktree)
                    current_worktree = {"path": line[9:]}
                elif line.startswith("branch "):
                    current_worktree["branch"] = line[7:].replace("refs/heads/", "")
                elif line.startswith("locked"):
                    # Handle both "locked" and "locked <reason>" formats
                    current_worktree["locked"] = True
            if current_worktree:
                worktrees.append(current_worktree)
    except Exception:
        pass  # Best effort - git command may fail

    return worktrees


def get_open_prs() -> list[dict[str, Any]]:
    """オープンなPRを取得"""
    prs = []
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--author",
                "@me",
                "--state",
                "open",
                "--json",
                "number,title,headRefName,url",
                "--limit",
                "5",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )
        if result.returncode == 0:
            prs = json.loads(result.stdout)
    except Exception:
        pass  # Best effort - gh command may fail

    return prs


def get_session_summary(session_id: str) -> dict[str, Any]:
    """セッションのサマリーを取得"""
    summary = {
        "hook_executions": 0,
        "blocks": 0,
        "block_reasons": [],
    }

    # Read from session-specific log file
    entries = read_session_log_entries(EXECUTION_LOG_DIR, "hook-execution", session_id)

    for entry in entries:
        summary["hook_executions"] += 1
        if entry.get("decision") == "block":
            summary["blocks"] += 1
            if reason := entry.get("reason"):
                # 重複を避けつつ最大5件まで
                if reason not in summary["block_reasons"] and len(summary["block_reasons"]) < 5:
                    summary["block_reasons"].append(reason[:100])

    return summary


def extract_lessons_learned(block_reasons: list[str]) -> list[str]:
    """ブロック理由から教訓を抽出する。

    Issue #1333: フックブロックのパターンから学習ポイントを生成。

    Args:
        block_reasons: フックによるブロック理由のリスト

    Returns:
        抽出された教訓のリスト（重複なし）
    """
    if not block_reasons:
        return []

    lessons: list[str] = []
    seen_patterns: set[str] = set()

    # ブロック理由からパターンを検出して教訓化
    # キーはパターン（英語・日本語両対応）、値は教訓
    # 注意: より具体的なパターンを先に定義（codex → review, worktree → lock）
    lesson_patterns = {
        "codex": "pushの前にcodex reviewを実行してレビューを受ける",
        "worktree": "worktreeの操作には注意が必要（パス確認、ロック状態の確認）",
        "merge": "マージ前にレビュースレッドの解決とCI通過を確認する",
        "マージ": "マージ前にレビュースレッドの解決とCI通過を確認する",
        "push": "pushの前にcodex reviewを実行する",
        "main": "mainブランチでの直接編集は避け、worktreeで作業する",
        "edit": "編集前にファイルの存在と権限を確認する",
        "branch": "ブランチ操作の前に現在の状態を確認する",
        "lock": "ロックされたworktreeは他セッションが作業中の可能性がある",
        "ロック": "ロックされたworktreeは他セッションが作業中の可能性がある",
        "review": "レビューコメントには署名を付けて返信する",
        "レビュー": "レビューコメントには署名を付けて返信する",
    }

    for reason in block_reasons:
        reason_lower = reason.lower()
        for pattern, lesson in lesson_patterns.items():
            if pattern in reason_lower and lesson not in seen_patterns:
                lessons.append(lesson)
                seen_patterns.add(lesson)
                break  # 1つのブロック理由から1つの教訓のみ

    return lessons[:5]  # 最大5件まで


def generate_handoff_memo(session_id: str | None) -> dict[str, Any]:
    """引き継ぎメモを生成

    Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。

    Args:
        session_id: Claude Codeのセッション識別子。Noneの場合は"unknown"として扱う。
    """
    now = datetime.now(UTC)

    # Normalize session_id to string (handle None case)
    effective_session_id = session_id if session_id else "unknown"

    git_status = get_git_status()
    worktrees = get_active_worktrees()
    open_prs = get_open_prs()
    session_summary = get_session_summary(effective_session_id)

    # 作業状態の推測
    work_status = "不明"
    next_action = "前回の作業を確認してください"

    if git_status["uncommitted_changes"] > 0:
        work_status = "作業途中（未コミットの変更あり）"
        next_action = "未コミットの変更を確認し、コミットまたは破棄してください"
    elif open_prs:
        work_status = f"PR作業中（{len(open_prs)}件のオープンPR）"
        next_action = "オープンPRのレビュー状態を確認してください"
    elif git_status["branch"] and git_status["branch"] != "main":
        work_status = f"フィーチャーブランチ '{git_status['branch']}' で作業中"
        next_action = "ブランチの作業を完了するか、mainに戻るか判断してください"
    else:
        work_status = "待機状態"
        next_action = "新しいタスクを開始できます"

    memo = {
        "generated_at": now.isoformat(),
        "session_id": session_id,
        "work_status": work_status,
        "next_action": next_action,
        "git": git_status,
        "worktrees": worktrees,
        "open_prs": [
            {
                "number": pr.get("number"),
                "title": pr.get("title", ""),
                "branch": pr.get("headRefName", ""),
            }
            for pr in open_prs
        ],
        "session_summary": session_summary,
        # 次セッションへの引き継ぎタスク（明示的に設定される場合用）
        "pending_tasks": [],
        # 教訓・学び（ブロック理由から自動抽出）
        "lessons_learned": extract_lessons_learned(session_summary.get("block_reasons", [])),
    }

    return memo


def cleanup_old_handoffs() -> None:
    """古いハンドオフファイルを削除（最新MAX_HANDOFF_FILES個を保持）"""
    try:
        if not HANDOFF_DIR.exists():
            return

        # セッションIDベースのファイルを取得（*.json）
        handoff_files = sorted(
            HANDOFF_DIR.glob("*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        # 最新N個以外を削除
        for old_file in handoff_files[MAX_HANDOFF_FILES:]:
            try:
                old_file.unlink()
            except FileNotFoundError:
                # 並列セッションで既に削除された場合は無視
                pass
    except OSError:
        # ファイルシステムエラーは致命的ではないため継続
        pass


def save_handoff_memo(memo: dict[str, Any]) -> bool:
    """引き継ぎメモをセッションIDベースのファイルに保存"""
    session_id = memo.get("session_id", "unknown")

    try:
        HANDOFF_DIR.mkdir(parents=True, exist_ok=True)

        # セッションIDベースのファイル名
        handoff_file = HANDOFF_DIR / f"{session_id}.json"
        with open(handoff_file, "w", encoding="utf-8") as f:
            json.dump(memo, f, ensure_ascii=False, indent=2)

        # 古いファイルをクリーンアップ
        cleanup_old_handoffs()

        return True
    except OSError:
        return False


def main():
    """Generate and save session handoff memo on session end."""
    # Stop hookはstdinからJSON入力を受け取る
    hook_input = parse_hook_input()

    # Stop hookが既にアクティブな場合は即座にapprove
    if hook_input.get("stop_hook_active"):
        print(json.dumps({"decision": "approve"}))
        return

    # Issue #2545: HookContextパターンでsession_idを取得
    ctx = create_hook_context(hook_input)
    session_id = ctx.get_session_id()

    # 引き継ぎメモ生成・保存
    memo = generate_handoff_memo(session_id)
    success = save_handoff_memo(memo)

    log_hook_execution(
        "session-handoff-writer",
        "approve",
        f"Handoff memo {'saved' if success else 'save failed'}",
        {
            "work_status": memo.get("work_status"),
            "next_action": memo.get("next_action"),
        },
    )

    # 保存の成否に関わらずapprove
    print(json.dumps({"decision": "approve"}))


if __name__ == "__main__":
    main()
