#!/usr/bin/env python3
"""セッション終了時に未完了TODOを検出して警告。

Why:
    未完了のTODOがIssue化されないままセッションが終了すると、
    タスクが忘れられる。セッション終了時に警告することで対応漏れを防ぐ。

What:
    - セッション終了時（Stop）に発火
    - transcriptからTodoWriteツール呼び出しを解析
    - 未完了かつIssue参照（#xxx）のないTODOを抽出
    - 該当TODOがあれば警告メッセージを表示

Remarks:
    - 非ブロック型（警告のみ、セッション終了は許可）
    - Issue参照があるTODOはスキップ（Issue化済みと判断）
    - 5件まで表示し、残りは件数のみ表示

Changelog:
    - silenvx/dekita#1909: フック追加
    - silenvx/dekita#1914: パストラバーサル攻撃対策追加
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.execution import log_hook_execution
from lib.path_validation import is_safe_transcript_path
from lib.session import parse_hook_input

HOOK_NAME = "session-todo-check"

# Issue参照パターン
ISSUE_REFERENCE_PATTERN = re.compile(r"#(\d+)")


def extract_latest_todos(transcript_path: str) -> list[dict] | None:
    """transcriptから最新のTodoWriteツール呼び出しを抽出.

    Args:
        transcript_path: トランスクリプトファイルのパス

    Returns:
        最新のTODOリスト（dict形式）、なければNone
    """
    try:
        with open(transcript_path, encoding="utf-8") as f:
            content = f.read()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return None

    # TodoWriteツールの呼び出しを検索（最新のものを取得）
    latest_todos = None

    # JSONLフォーマット（1行1JSON）
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("role") == "assistant":
                content_blocks = obj.get("content", [])
                if isinstance(content_blocks, list):
                    for block in content_blocks:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "tool_use"
                            and block.get("name") == "TodoWrite"
                        ):
                            tool_input = block.get("input", {})
                            todos = tool_input.get("todos", [])
                            if todos:
                                latest_todos = todos
        except json.JSONDecodeError:
            continue

    # JSON配列フォーマットの場合
    if latest_todos is None:
        try:
            data = json.loads(content)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("role") == "assistant":
                        content_blocks = item.get("content", [])
                        if isinstance(content_blocks, list):
                            for block in content_blocks:
                                if (
                                    isinstance(block, dict)
                                    and block.get("type") == "tool_use"
                                    and block.get("name") == "TodoWrite"
                                ):
                                    tool_input = block.get("input", {})
                                    todos = tool_input.get("todos", [])
                                    if todos:
                                        latest_todos = todos
        except json.JSONDecodeError:
            pass  # JSON配列フォーマットでない場合は無視（JSONLで既に処理済み）

    return latest_todos


def find_incomplete_todos_without_issue(todos: list[dict]) -> list[dict]:
    """未完了かつIssue参照のないTODOを抽出.

    Args:
        todos: TODOリスト

    Returns:
        未完了かつIssue参照のないTODOのリスト
    """
    incomplete = []
    for todo in todos:
        status = todo.get("status", "")
        content = todo.get("content", "")

        # completed以外は未完了扱い
        if status == "completed":
            continue

        # Issue参照があるかチェック
        if ISSUE_REFERENCE_PATTERN.search(content):
            continue

        incomplete.append(todo)

    return incomplete


def main() -> None:
    """フックのエントリポイント."""
    hook_input = parse_hook_input()

    # Stop hookはtranscript_pathをトップレベルで受け取る
    transcript_path = hook_input.get("transcript_path", "")

    if not transcript_path:
        log_hook_execution(HOOK_NAME, "approve", "No transcript path")
        print(json.dumps({"continue": True}))
        return

    # セキュリティ: パストラバーサル攻撃を防止 (Issue #1914)
    if not is_safe_transcript_path(transcript_path):
        log_hook_execution(HOOK_NAME, "approve", f"Invalid transcript path: {transcript_path}")
        print(json.dumps({"continue": True}))
        return

    # TODOを抽出
    todos = extract_latest_todos(transcript_path)

    if not todos:
        log_hook_execution(HOOK_NAME, "approve", "No todos found")
        print(json.dumps({"continue": True}))
        return

    # 未完了かつIssue参照のないTODOを抽出
    incomplete = find_incomplete_todos_without_issue(todos)

    if not incomplete:
        log_hook_execution(HOOK_NAME, "approve", "All todos completed or have issue refs")
        print(json.dumps({"continue": True}))
        return

    # 警告メッセージを生成
    todo_list = "\n".join(
        f"  - [{todo.get('status', 'unknown')}] {todo.get('content', '(no content)')}"
        for todo in incomplete[:5]
    )
    if len(incomplete) > 5:
        todo_list += f"\n  ... 他 {len(incomplete) - 5} 件"

    warning_lines = [
        "⚠️ 未完了のTODOがあります:",
        "",
        todo_list,
        "",
        "未完了タスクがある場合:",
        "  1. 対応するIssueを作成 (`gh issue create`)",
        "  2. または、TODOの内容にIssue番号を含める (例: `#1234の実装`)",
        "",
        "Issue化しておかないと、セッション終了後に忘れられる可能性があります。",
    ]
    warning_msg = "\n".join(warning_lines)

    log_hook_execution(
        HOOK_NAME,
        "warn",
        f"Incomplete todos without issue ref: {len(incomplete)}",
    )
    print(
        json.dumps(
            {
                "continue": True,
                "message": warning_msg,
            }
        )
    )


if __name__ == "__main__":
    main()
