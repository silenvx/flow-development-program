#!/usr/bin/env python3
"""セッション開始時に孤立したworktreeディレクトリを検知して警告する。

Why:
    .worktrees/にディレクトリが残っているが.git/worktrees/に
    エントリがない状態は異常。ディスクを圧迫し混乱の原因となる。

What:
    - .worktrees/ディレクトリを走査
    - 対応する.git/worktrees/エントリの存在を確認
    - 孤立worktreeがあればsystemMessageで警告
    - 削除コマンドを提示

Remarks:
    - 警告型フック（ブロックしない、systemMessageで情報提供）
    - PreToolUse:Bashで発火（セッション毎に1回）
    - merged-worktree-check.pyはマージ済みPR検出（責務分離）
    - ファイルロックで競合状態を防止

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import os
import shlex
import sys
from pathlib import Path

from common import check_and_update_session_marker
from lib.execution import log_hook_execution
from lib.repo import get_repo_root
from lib.session import parse_hook_input


def find_orphan_worktrees(project_dir: Path) -> list[tuple[str, str]]:
    """Find worktree directories that don't have corresponding git entries.

    Resolves project_dir to repo root if running inside a worktree.

    Returns:
        List of tuples (prefix, name) where prefix is ".worktrees"
        and name is the directory name.
    """
    # Resolve to repo root (handles both main repo and worktree cases)
    repo_root = get_repo_root(project_dir)
    if repo_root is None:
        return []

    worktree_prefixes = [".worktrees"]
    git_worktrees_dir = repo_root / ".git" / "worktrees"

    orphans: list[tuple[str, str]] = []

    # If .git/worktrees/ doesn't exist, all worktree entries are orphans
    git_worktrees_exists = git_worktrees_dir.exists()

    for prefix in worktree_prefixes:
        worktrees_dir = repo_root / prefix
        if not worktrees_dir.exists():
            continue

        for item in worktrees_dir.iterdir():
            if item.is_dir():
                # Check if corresponding entry exists in .git/worktrees/
                if not git_worktrees_exists:
                    orphans.append((prefix, item.name))
                else:
                    git_entry = git_worktrees_dir / item.name
                    if not git_entry.exists():
                        orphans.append((prefix, item.name))

    return orphans


def main():
    """
    PreToolUse hook for Bash commands.

    Shows orphan worktree warning on first Bash execution of each session.
    Uses atomic check-and-update to prevent race conditions.
    """
    # Set session_id for proper logging
    parse_hook_input()

    result = {"decision": "approve"}

    try:
        # Atomically check if new session and update marker
        # Returns True only for the first caller when concurrent calls occur
        if check_and_update_session_marker("orphan-worktree-check"):
            # Get project directory from environment
            project_dir_str = os.environ.get("CLAUDE_PROJECT_DIR", "")
            if project_dir_str:
                project_dir = Path(project_dir_str)
                orphans = find_orphan_worktrees(project_dir)

                if orphans:
                    orphan_list = "\n".join(f"  - {prefix}/{name}" for prefix, name in orphans)
                    # Use shlex.quote to safely escape directory names
                    quoted_paths = " ".join(
                        shlex.quote(f"{prefix}/{name}") for prefix, name in orphans
                    )
                    message = (
                        f"⚠️ **孤立したworktreeディレクトリを検出**:\n"
                        f"{orphan_list}\n\n"
                        f"これらは.git/worktrees/に対応するエントリがありません。\n"
                        f"削除コマンド: `rm -rf {quoted_paths}`"
                    )
                    result["systemMessage"] = message

    except Exception as e:
        # Don't block on errors, just skip the check
        print(f"[orphan-worktree-check] Error: {e}", file=sys.stderr)

    log_hook_execution(
        "orphan-worktree-check",
        result.get("decision", "approve"),
        result.get("reason"),
    )
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
