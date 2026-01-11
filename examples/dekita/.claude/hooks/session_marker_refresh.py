#!/usr/bin/env python3
"""worktree内のセッションマーカーのmtimeを定期更新。

Why:
    長時間セッション（30分以上）でマーカーのmtimeが古くなると、
    worktree-removal-checkが「古いセッション」と判断してしまう。
    定期的にmtimeを更新してセッション活性を示す。

What:
    - PostToolUse時に発火
    - CWDがworktree内かどうか確認
    - マーカーのmtimeが10分以上古ければtouchで更新
    - 更新が不要なら何もしない（パフォーマンス最適化）

State:
    - writes: .worktrees/*/.claude-session（mtimeのみ更新）

Remarks:
    - 非ブロック型（マーカーのtouchのみ）
    - session-marker-updaterはSessionStart時、本フックはPostToolUse
    - 10分間隔でのみ更新（REFRESH_INTERVAL）

Changelog:
    - silenvx/dekita#1572: フック追加（長時間セッション対策）
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

# 共通モジュール
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.constants import SESSION_MARKER_FILE
from lib.cwd import get_effective_cwd
from lib.execution import log_hook_execution
from lib.session import parse_hook_input

# Refresh interval in seconds (10 minutes)
REFRESH_INTERVAL = 600


def get_worktree_root() -> Path | None:
    """Get the worktree root directory if effective CWD is inside a worktree.

    Uses get_effective_cwd() to handle cases where the hook process runs from
    the project root but the session has cd'd into a worktree.

    Uses the same regex pattern as session-marker-updater.py for consistency.

    Returns:
        Worktree root path if inside a worktree, None otherwise
    """
    cwd = get_effective_cwd()
    cwd_str = str(cwd)

    # Use regex to match .worktrees pattern (same as session-marker-updater.py)
    # This handles edge cases like /path/.worktrees.backup/.worktrees/issue-123
    match = re.search(r"(.*?[/\\]\.worktrees[/\\][^/\\]+)", cwd_str)
    if match:
        return Path(match.group(1))
    return None


def needs_refresh(marker_path: Path) -> bool:
    """Check if marker needs to be refreshed.

    Args:
        marker_path: Path to the session marker file

    Returns:
        True if marker is older than REFRESH_INTERVAL, False otherwise
    """
    if not marker_path.exists():
        return False

    try:
        mtime = marker_path.stat().st_mtime
        age = time.time() - mtime
        return age > REFRESH_INTERVAL
    except OSError:
        return False


def refresh_marker(marker_path: Path) -> bool:
    """Refresh the marker file's mtime.

    Args:
        marker_path: Path to the session marker file

    Returns:
        True if marker was refreshed successfully, False otherwise
    """
    try:
        # Touch the file to update mtime
        marker_path.touch()
        return True
    except OSError:
        return False


def main():
    """PostToolUse hook to periodically refresh session marker."""
    result = {"continue": True}

    try:
        # Parse hook input for session_id (required for log_hook_execution)
        parse_hook_input()

        worktree_root = get_worktree_root()

        if worktree_root is None:
            # Not in a worktree, nothing to do
            print(json.dumps(result))
            sys.exit(0)

        marker_path = worktree_root / SESSION_MARKER_FILE

        if not marker_path.exists():
            # No marker file, nothing to do
            print(json.dumps(result))
            sys.exit(0)

        if needs_refresh(marker_path):
            if refresh_marker(marker_path):
                log_hook_execution(
                    "session-marker-refresh",
                    "success",
                    f"Refreshed marker in {worktree_root.name}",
                )
            else:
                log_hook_execution(
                    "session-marker-refresh",
                    "warning",
                    f"Failed to refresh marker in {worktree_root.name}",
                )

        print(json.dumps(result))
        sys.exit(0)

    except Exception as e:
        log_hook_execution(
            "session-marker-refresh",
            "error",
            f"Unexpected error: {e}",
        )
        # Don't block on errors
        print(json.dumps(result))
        sys.exit(0)


if __name__ == "__main__":
    main()
