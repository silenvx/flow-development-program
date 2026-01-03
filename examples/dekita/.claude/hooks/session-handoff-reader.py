#!/usr/bin/env python3
"""セッション開始時に前回の引き継ぎメモを読み込み表示。

Why:
    Claude Codeはセッション間で記憶を保持しない。前回の作業状態、
    未対応タスク、教訓を引き継ぐことで、継続性を確保する。

What:
    - セッション開始時（SessionStart）に発火
    - .claude/handoff/配下の有効なメモを読み込み
    - 自セッションと他セッションのメモを区別して表示
    - Git状態、オープンPR、ロック中worktreeも表示

State:
    - reads: .claude/handoff/*.json

Remarks:
    - 非ブロック型（情報表示のみ）
    - session-handoff-writerが生成、本フックが読み込み
    - メモの有効期間は24時間

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#1333: 教訓抽出機能を追加
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# 共通モジュール
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.execution import log_hook_execution
from lib.session import create_hook_context, parse_hook_input

# 引き継ぎメモの保存先
HANDOFF_DIR = HOOKS_DIR.parent / "handoff"

# 引き継ぎメモの有効期間（24時間以内の場合のみ表示）
HANDOFF_VALIDITY_HOURS = 24


def load_all_handoff_memos() -> list[dict[str, Any]]:
    """全ての有効なハンドオフメモを読み込み（時刻順）"""
    memos = []

    if not HANDOFF_DIR.exists():
        return memos

    for handoff_file in HANDOFF_DIR.glob("*.json"):
        try:
            with open(handoff_file, encoding="utf-8") as f:
                memo = json.load(f)
                if is_memo_valid(memo):
                    memos.append(memo)
        except (OSError, json.JSONDecodeError):
            continue

    # 生成時刻で降順ソート（最新が先頭）
    memos.sort(key=lambda m: m.get("generated_at", ""), reverse=True)

    return memos


def is_memo_valid(memo: dict[str, Any]) -> bool:
    """引き継ぎメモが有効期間内かチェック"""
    generated_at = memo.get("generated_at")
    if not generated_at:
        return False

    try:
        generated_time = datetime.fromisoformat(generated_at)
        now = datetime.now(UTC)
        age_hours = (now - generated_time).total_seconds() / 3600
        return age_hours < HANDOFF_VALIDITY_HOURS
    except (ValueError, TypeError):
        return False


def format_age(generated_at: str) -> str:
    """生成時刻から経過時間を文字列化"""
    try:
        generated_time = datetime.fromisoformat(generated_at)
        age_minutes = int((datetime.now(UTC) - generated_time).total_seconds() / 60)
        if age_minutes < 1:
            return "たった今"
        elif age_minutes < 60:
            return f"{age_minutes}分前"
        else:
            return f"{age_minutes // 60}時間前"
    except (ValueError, TypeError):
        return "不明"


def format_handoff_message(memos: list[dict[str, Any]], current_session_id: str) -> str:
    """複数のハンドオフメモを読みやすい形式にフォーマット"""
    if not memos:
        return ""

    lines = ["📝 **セッション引き継ぎ情報**", ""]

    # 自分のセッションのメモを優先して表示
    own_session_memos = [m for m in memos if m.get("session_id") == current_session_id]
    other_session_memos = [m for m in memos if m.get("session_id") != current_session_id]

    # 自分のセッションのメモがあればそれを使う、なければ最新のメモ
    if own_session_memos:
        latest = own_session_memos[0]
        is_own_session = True
    else:
        latest = memos[0]
        is_own_session = False

    session_label = "前回のセッション" if is_own_session else "別セッション"

    lines.append(
        f"**{session_label}からの引き継ぎ** ({format_age(latest.get('generated_at', ''))})"
    )
    lines.append("")

    # 作業状態
    work_status = latest.get("work_status", "不明")
    lines.append(f"**状態**: {work_status}")

    # 次のアクション
    next_action = latest.get("next_action", "")
    if next_action:
        lines.append(f"**次にすべきこと**: {next_action}")

    # 未対応タスク（あれば）
    pending_tasks = latest.get("pending_tasks", [])
    if pending_tasks:
        lines.append("")
        lines.append("**⚠️ 未対応タスク**:")
        for task in pending_tasks[:5]:
            lines.append(f"  - {task}")

    # 教訓・学び（あれば）
    lessons = latest.get("lessons_learned", [])
    if lessons:
        lines.append("")
        lines.append("**💡 前回の教訓**:")
        for lesson in lessons[:3]:
            lines.append(f"  - {lesson}")

    lines.append("")

    # Git状態
    git = latest.get("git", {})
    if git:
        branch = git.get("branch", "不明")
        uncommitted = git.get("uncommitted_changes", 0)
        untracked = git.get("untracked_files", 0)

        lines.append("**Git状態**:")
        lines.append(f"  - ブランチ: `{branch}`")
        if uncommitted > 0:
            lines.append(f"  - 未コミットの変更: {uncommitted}件 ⚠️")
        if untracked > 0:
            lines.append(f"  - 未追跡ファイル: {untracked}件")

    # オープンPR
    open_prs = latest.get("open_prs", [])
    if open_prs:
        lines.append("")
        lines.append("**オープンPR**:")
        for pr in open_prs[:3]:
            lines.append(
                f"  - #{pr.get('number')}: {pr.get('title', '')} (`{pr.get('branch', '')}`)"
            )

    # アクティブworktree
    worktrees = latest.get("worktrees", [])
    active_worktrees = [wt for wt in worktrees if wt.get("locked")]
    if active_worktrees:
        lines.append("")
        lines.append("**ロック中のworktree** (別セッションが作業中かも):")
        for wt in active_worktrees[:3]:
            lines.append(f"  - `{wt.get('branch', '?')}` @ {wt.get('path', '?')}")

    # セッションサマリー
    summary = latest.get("session_summary", {})
    if summary.get("blocks", 0) > 0:
        lines.append("")
        lines.append(f"**前回のセッション**: {summary.get('blocks', 0)}回ブロックされました")
        block_reasons = summary.get("block_reasons", [])
        if block_reasons:
            lines.append("  最近のブロック理由:")
            for reason in block_reasons[:2]:
                truncated = reason[:60]
                suffix = "..." if len(reason) > 60 else ""
                lines.append(f"    - {truncated}{suffix}")

    # 他セッションからのメモがある場合
    if other_session_memos:
        lines.append("")
        lines.append("---")
        lines.append(f"_他に{len(other_session_memos)}件の並列セッションの引き継ぎがあります_")

        # 重要なタスクや教訓があれば表示
        for memo in other_session_memos[:2]:
            pending = memo.get("pending_tasks", [])
            lessons = memo.get("lessons_learned", [])
            if pending or lessons:
                age = format_age(memo.get("generated_at", ""))
                lines.append(f"  ({age}):")
                for task in pending[:2]:
                    lines.append(f"    - ⚠️ {task}")
                for lesson in lessons[:1]:
                    lines.append(f"    - 💡 {lesson}")

    return "\n".join(lines)


def main():
    """SessionStart hookのエントリーポイント"""
    result = {"continue": True}

    try:
        # 現在のセッションIDを取得
        hook_input = parse_hook_input()

        ctx = create_hook_context(hook_input)
        current_session_id = hook_input.get("session_id") or ctx.get_session_id()

        # 全ての有効なメモを読み込み
        memos = load_all_handoff_memos()

        if memos:
            message = format_handoff_message(memos, current_session_id)
            if message:
                result["message"] = message

            log_hook_execution(
                "session-handoff-reader",
                "approve",
                "Handoff memos displayed",
                {
                    "memo_count": len(memos),
                    "latest_work_status": memos[0].get("work_status") if memos else None,
                    "has_pending_tasks": any(m.get("pending_tasks") for m in memos),
                    "has_lessons": any(m.get("lessons_learned") for m in memos),
                },
            )
        else:
            log_hook_execution("session-handoff-reader", "approve", "No valid handoff memos found")

    except Exception as e:
        # エラーがあっても継続
        log_hook_execution("session-handoff-reader", "approve", f"Error loading handoff memos: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
