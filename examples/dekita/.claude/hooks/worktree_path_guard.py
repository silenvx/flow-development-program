#!/usr/bin/env python3
"""worktree作成先が.worktrees/内かを検証。

Why:
    worktreeがファイルシステム全体に散らばると管理が困難になる。
    .worktrees/に集約することで一覧確認が容易になり、他エージェントとの競合も避けられる。

What:
    - git worktree addコマンド実行前（PreToolUse:Bash）に発火
    - パス引数を抽出して.worktrees/配下かを検証
    - 絶対パスや..を使った迂回パスもブロック
    - 正しい使い方を提示

Remarks:
    - ブロック型フック（.worktrees/外への作成はブロック）
    - worktree-main-freshness-checkはmain最新確認、本フックはパス検証

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import os.path
import sys
from pathlib import Path

from lib import extract_worktree_add_path
from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input


def is_valid_worktree_path(path: str) -> bool:
    """Check if the worktree path is under .worktrees/.

    Args:
        path: The path argument from git worktree add

    Returns:
        True if path is under .worktrees/, False otherwise

    Note:
        This function handles path traversal attacks like `.worktrees/../foo`
        by normalizing the path before checking.
    """
    # Reject absolute paths immediately
    if Path(path).is_absolute():
        return False

    # Normalize the path to resolve .. and .
    # Use os.path.normpath to handle path traversal
    normalized = os.path.normpath(path)

    # After normalization, check if it still starts with .worktrees/
    # normpath will collapse .worktrees/../foo to just foo
    parts = Path(normalized).parts
    if parts and parts[0] == ".worktrees" and len(parts) >= 2:
        # Must have at least .worktrees/something
        return True

    return False


def main():
    """
    PreToolUse hook for Bash commands.

    Blocks `git worktree add` commands that don't target `.worktrees/` directory.
    """
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        worktree_path = extract_worktree_add_path(command)

        if worktree_path is not None and not is_valid_worktree_path(worktree_path):
            reason = (
                f"worktreeは `.worktrees/` ディレクトリ内に作成してください。\n\n"
                f"**検出されたパス:** `{worktree_path}`\n\n"
                f"**正しい使い方:**\n"
                f"```bash\n"
                f"# Issue番号を使った命名規則\n"
                f"git worktree add .worktrees/issue-123 feature/issue-123-description\n\n"
                f"# または任意の名前\n"
                f"git worktree add .worktrees/my-feature feature/my-feature\n"
                f"```\n\n"
                f"**理由:**\n"
                f"- worktreeを一箇所に集約することで管理が容易になります\n"
                f"- `git worktree list`で一覧を確認しやすくなります\n"
                f"- 他のエージェントとの競合を避けられます"
            )
            result = make_block_result("worktree-path-guard", reason)
            log_hook_execution("worktree-path-guard", "block", f"invalid path: {worktree_path}")
            print(json.dumps(result))
            sys.exit(0)

        result = make_approve_result("worktree-path-guard")

    except Exception as e:
        print(f"[worktree-path-guard] Hook error: {e}", file=sys.stderr)
        result = make_approve_result("worktree-path-guard", f"Hook error: {e}")

    log_hook_execution("worktree-path-guard", result.get("decision", "approve"))
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
