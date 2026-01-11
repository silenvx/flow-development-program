#!/usr/bin/env python3
"""カレントディレクトリの存在を確認し、削除されていればセッションをブロックする。

Why:
    worktree削除などでカレントディレクトリが消失すると、
    以降のファイル操作が予期せず失敗し、データ損失につながる可能性がある。

What:
    - カレントディレクトリの存在を確認
    - 存在しない場合はセッションをブロック
    - Claude Code再起動の案内を表示

Remarks:
    - ブロック型フック（cwd消失時はブロック）
    - PreToolUseで発火（全ツール対象）
    - stop_hook_active時は無限ループ防止のためスキップ
    - worktree削除後のセッション継続防止が主な用途

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import os
import sys

from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input


def check_cwd_exists() -> tuple[bool, str]:
    """
    Check if the current working directory exists.
    Returns (exists, error_message).
    """
    try:
        cwd = os.getcwd()
        return True, cwd
    except OSError as e:
        return False, str(e)


def generate_handoff_message() -> str:
    """
    Generate a handoff message for the next agent when cwd is lost.
    """
    return """
## カレントディレクトリ消失を検知

**問題**: カレントディレクトリが存在しません。

**推定原因**: ディレクトリが削除された、ファイルシステムがアンマウントされた、権限が変更された、または worktree 内から `git worktree remove` を実行した等、様々な理由が考えられます。

**対応が必要**:
1. Claude Codeを再起動してください
2. オリジナルディレクトリまたは有効な作業ディレクトリから作業を再開してください

**次のエージェントへの引き継ぎ**:
- 前のエージェントはカレントディレクトリが存在しない状態で処理を継続しようとしました
- これは worktree の削除やディレクトリの消失など、複数の原因が考えられます
- ディレクトリや worktree の状態を確認し、必要に応じてクリーンアップや再作成を行ってください
"""


def main():
    """
    Entry point for the cwd existence check hook.

    Reads a JSON object from stdin with the following format:
        {
            "transcript_path": "<path to transcript file>",
            "stop_hook_active": <bool, optional>
        }

    Outputs a JSON object to stdout with the following format:
        {
            "decision": "approve" | "block",
            "reason": "<explanation>"
        }
    """
    result = None
    try:
        input_json = parse_hook_input()

        # If stop_hook_active is set, approve immediately to avoid infinite retry loops
        if input_json.get("stop_hook_active"):
            result = {
                "ok": True,
                "decision": "approve",
                "reason": "stop_hook_active is set; approving to avoid infinite retry loop.",
            }
        else:
            exists, info = check_cwd_exists()

            if exists:
                result = {
                    "ok": True,
                    "decision": "approve",
                    "reason": f"Current working directory exists: {info}",
                    "systemMessage": "✅ cwd-check: カレントディレクトリ存在確認OK",
                }
            else:
                reason = generate_handoff_message()
                result = make_block_result("cwd-check", reason)
                result["ok"] = False
                result["systemMessage"] = (
                    "⚠️ カレントディレクトリが存在しません。Claude Codeの再起動が必要です。"
                )

    except Exception as e:
        # On error, approve to avoid blocking, but log for debugging
        print(f"[cwd-check] Hook error: {e}", file=sys.stderr)
        result = {"ok": True, "decision": "approve", "reason": f"Hook error: {e}"}

    log_hook_execution("cwd-check", result.get("decision", "approve"), result.get("reason"))
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
