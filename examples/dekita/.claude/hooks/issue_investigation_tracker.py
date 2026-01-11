#!/usr/bin/env python3
"""gh issue view実行時に別セッションの調査を検知し警告する。

Why:
    worktree/PR作成前の調査フェーズでも並行セッションの競合が発生する。
    Issue閲覧時点で調査開始を記録し、別セッションとの重複を早期検知する。

What:
    - gh issue viewコマンドを検出しIssue番号を抽出
    - Issueコメントから他セッションの調査開始マーカーを検索
    - 別セッションが1時間以内に調査中なら警告
    - 自身の調査開始をコメントとして記録（重複防止）

State:
    - writes: GitHub Issueコメント（🔍 調査開始マーカー）

Remarks:
    - 非ブロック型（警告のみ）
    - issue-auto-assignはworktree作成時の競合防止、本フックは調査フェーズの検知

Changelog:
    - silenvx/dekita#1830: フック追加
"""

import json
import re
import subprocess
from datetime import UTC, datetime, timedelta

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.session import create_hook_context, parse_hook_input

# 調査中と判定する時間（1時間以内）
ACTIVE_INVESTIGATION_HOURS = 1

# 調査開始コメントのパターン
INVESTIGATION_PATTERN = re.compile(r"🔍 調査開始 \(session: ([a-zA-Z0-9-]+)\)")

# gh issue view コマンドのパターン
GH_ISSUE_VIEW_PATTERN = re.compile(r"\bgh\s+issue\s+view\s+#?(\d+)")


def get_issue_comments(issue_number: int) -> list[dict] | None:
    """Issueのコメントを取得"""
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                str(issue_number),
                "--json",
                "comments",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("comments", [])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass  # Best effort - gh command may fail, return None to indicate failure
    return None


def find_active_investigation(comments: list[dict], current_session: str) -> dict | None:
    """活動中の調査セッションを検索

    Returns:
        活動中の別セッション情報。自分のセッションまたは活動なしの場合はNone。
    """
    now = datetime.now(UTC)
    threshold = now - timedelta(hours=ACTIVE_INVESTIGATION_HOURS)

    for comment in reversed(comments):  # 新しいコメントから検索
        body = comment.get("body", "")
        match = INVESTIGATION_PATTERN.search(body)
        if not match:
            continue

        session_id = match.group(1)

        # 自分のセッションなら無視
        if session_id == current_session:
            continue

        # タイムスタンプ確認
        created_at_str = comment.get("createdAt", "")
        if created_at_str:
            try:
                # ISO 8601形式をパース
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                if created_at > threshold:
                    return {
                        "session_id": session_id,
                        "created_at": created_at_str,
                        "author": comment.get("author", {}).get("login", "unknown"),
                    }
            except ValueError:
                pass  # Skip comment with invalid timestamp format

    return None


def has_recent_own_comment(comments: list[dict], current_session: str) -> bool:
    """自分のセッションからの最近のコメントがあるかチェック

    重複コメント防止用。1時間以内の自分のコメントがあればTrueを返す。
    """
    now = datetime.now(UTC)
    threshold = now - timedelta(hours=ACTIVE_INVESTIGATION_HOURS)

    for comment in reversed(comments):
        body = comment.get("body", "")
        match = INVESTIGATION_PATTERN.search(body)
        if not match:
            continue

        session_id = match.group(1)
        if session_id != current_session:
            continue

        # タイムスタンプ確認
        created_at_str = comment.get("createdAt", "")
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                if created_at > threshold:
                    return True
            except ValueError:
                pass  # Skip comment with invalid timestamp format

    return False


def add_investigation_comment(issue_number: int, session_id: str) -> bool:
    """調査開始コメントを追加"""
    comment_body = f"🔍 調査開始 (session: {session_id})"
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "comment",
                str(issue_number),
                "--body",
                comment_body,
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def main():
    """PreToolUse:Bash hook for gh issue view commands.

    Detects when another session is investigating the same issue
    and warns the user.
    """
    result = {"decision": "approve"}

    # parse_hook_input は JSONDecodeError を送出せず、エラー時には空 dict を返す
    data = parse_hook_input()

    ctx = create_hook_context(data)
    if not data:
        log_hook_execution("issue-investigation-tracker", "approve", None)
        print(json.dumps(result))
        return

    # Bashツールのみを対象
    tool_name = data.get("tool_name", "")
    if tool_name != "Bash":
        log_hook_execution("issue-investigation-tracker", "approve", None)
        print(json.dumps(result))
        return

    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "")

    # gh issue view コマンドかチェック
    match = GH_ISSUE_VIEW_PATTERN.search(command)
    if not match:
        log_hook_execution("issue-investigation-tracker", "approve", None)
        print(json.dumps(result))
        return

    issue_number = int(match.group(1))
    current_session = ctx.get_session_id()

    # 既存コメントを取得
    comments = get_issue_comments(issue_number)
    if comments is None:
        # コメント取得失敗時は警告なしで続行
        log_hook_execution("issue-investigation-tracker", "approve", "comments_fetch_failed")
        print(json.dumps(result))
        return

    # 活動中の別セッションを検索
    active_investigation = find_active_investigation(comments, current_session)

    if active_investigation:
        # 別セッションが調査中 - 警告
        other_session = active_investigation["session_id"]
        author = active_investigation["author"]
        created_at = active_investigation["created_at"]

        warning = (
            f"⚠️ **別セッションが調査中**: Issue #{issue_number}\n\n"
            f"- セッション: `{other_session}`\n"
            f"- 開始者: @{author}\n"
            f"- 開始時刻: {created_at}\n\n"
            "同じIssueに取り組むと競合する可能性があります。\n"
            "別のIssueに取り組むか、調査のみに留めることを検討してください。"
        )

        result = {
            "decision": "approve",
            "systemMessage": f"[issue-investigation-tracker] {warning}",
        }
        log_hook_execution(
            "issue-investigation-tracker", "approve", f"other_session_active:{other_session}"
        )
    else:
        # 重複コメント防止: 自分のセッションからの最近のコメントがあればスキップ
        if has_recent_own_comment(comments, current_session):
            log_hook_execution(
                "issue-investigation-tracker", "approve", f"already_commented:{issue_number}"
            )
        elif add_investigation_comment(issue_number, current_session):
            log_hook_execution(
                "issue-investigation-tracker", "approve", f"investigation_started:{issue_number}"
            )
        else:
            log_hook_execution("issue-investigation-tracker", "approve", "comment_add_failed")

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
