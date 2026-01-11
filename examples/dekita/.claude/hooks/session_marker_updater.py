#!/usr/bin/env python3
"""セッション開始時にworktree内のセッションマーカーを更新。

Why:
    既存worktree内でセッションを開始した場合、マーカーが古いセッションIDのまま
    だとlocked-worktree-guardの自己セッションバイパスが機能しない。
    現在のセッションIDで更新する必要がある。

What:
    - セッション開始時（SessionStart）に発火
    - CWDがworktree内かどうか確認
    - .claude-sessionファイルを現在のセッションIDで上書き

State:
    - writes: .worktrees/*/.claude-session

Remarks:
    - 非ブロック型（情報書き込みのみ）
    - worktree-creation-markerは新規作成時、本フックは既存worktreeでのセッション開始時
    - session-marker-refreshと連携してマーカーを維持

Changelog:
    - silenvx/dekita#1431: フック追加（既存worktreeでのセッション開始対策）
    - silenvx/dekita#2545: HookContextパターン移行
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# 共通モジュール
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.constants import SESSION_MARKER_FILE
from lib.execution import log_hook_execution
from lib.session import HookContext, create_hook_context, parse_hook_input


def get_worktree_root(cwd: Path) -> Path | None:
    """Get the worktree root directory if CWD is inside a worktree.

    Args:
        cwd: Current working directory

    Returns:
        Worktree root path if inside a worktree, None otherwise
    """
    # Check if .worktrees/ is in the path (supports both Unix and Windows paths)
    cwd_str = str(cwd)
    match = re.search(r"(.*?[/\\]\.worktrees[/\\][^/\\]+)", cwd_str)
    if match:
        return Path(match.group(1))
    return None


def write_session_marker(ctx: HookContext, worktree_path: Path) -> bool:
    """Write current session ID to worktree marker file.

    Args:
        ctx: HookContext for session information.
        worktree_path: Path to worktree directory

    Returns:
        True if marker was written successfully, False otherwise
    """
    try:
        marker_path = worktree_path / SESSION_MARKER_FILE
        session_id = ctx.get_session_id()
        marker_path.write_text(session_id)
        return True
    except OSError:
        return False


def main():
    """SessionStart hook to update session marker in worktree."""
    try:
        # SessionStartフックからの入力を解析（session_id取得のため）
        input_data = parse_hook_input()
        ctx = create_hook_context(input_data)

        cwd = Path(os.getcwd())
        worktree_root = get_worktree_root(cwd)

        if worktree_root is None:
            # Not in a worktree, nothing to do
            log_hook_execution("session-marker-updater", "success", "Not in worktree")
            print(json.dumps({"continue": True}))
            sys.exit(0)

        # Update session marker
        session_id = ctx.get_session_id()
        if write_session_marker(ctx, worktree_root):
            log_hook_execution(
                "session-marker-updater",
                "success",
                f"Updated marker in {worktree_root.name} with session {session_id[:8]}...",
            )
            print(json.dumps({"continue": True}))
        else:
            log_hook_execution(
                "session-marker-updater",
                "warning",
                f"Failed to write marker in {worktree_root.name}",
            )
            print(json.dumps({"continue": True}))  # Don't block session start

        sys.exit(0)

    except Exception:
        log_hook_execution(
            "session-marker-updater",
            "error",
            "An unexpected error occurred while updating the session marker.",
        )
        # Don't block session start on errors
        print(json.dumps({"continue": True}))
        sys.exit(0)


if __name__ == "__main__":
    main()
