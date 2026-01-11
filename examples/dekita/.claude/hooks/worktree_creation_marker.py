#!/usr/bin/env python3
"""worktree作成時にセッションIDをマーカーファイルとして記録する。

Why:
    worktreeの所有者（作成セッション）を記録することで、
    別セッションによる誤介入を防止できる。

What:
    - git worktree addコマンドの成功を検出
    - 作成されたworktreeにセッションIDを.claude-sessionとして記録
    - worktree-session-guard.pyがこのマーカーを参照

State:
    writes: .worktrees/*/.claude-session

Remarks:
    - ブロックせず情報記録のみ
    - worktree-session-guard.pyと連携（マーカー作成→マーカー検証）
    - JSON形式でsession_idとcreated_atを記録

Changelog:
    - silenvx/dekita#1396: フック追加
    - silenvx/dekita#1842: get_tool_result()ヘルパー使用
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from lib import extract_worktree_add_path
from lib.constants import SESSION_MARKER_FILE
from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.results import make_approve_result
from lib.session import HookContext, create_hook_context, parse_hook_input


def write_session_marker(ctx: HookContext, worktree_path: Path) -> bool:
    """Write current session ID and timestamp to worktree marker file.

    The marker is written in JSON format:
    {
        "session_id": "...",
        "created_at": "2025-12-30T09:30:00+00:00"
    }

    For backward compatibility, the marker can be read as either:
    - JSON (new format with timestamp)
    - Plain text (old format, session ID only)

    Args:
        ctx: HookContext for session information.
        worktree_path: Path to worktree directory

    Returns:
        True if marker was written successfully, False otherwise
    """
    try:
        marker_path = worktree_path / SESSION_MARKER_FILE
        session_id = ctx.get_session_id()
        marker_data = {
            "session_id": session_id,
            "created_at": datetime.now(UTC).isoformat(),
        }
        # Write atomically: write to temp file then rename
        # Note: with_suffix() doesn't work for dotfiles like .claude-session
        # because the entire name is considered the suffix
        temp_path = marker_path.with_name(marker_path.name + ".tmp")
        temp_path.write_text(json.dumps(marker_data))
        temp_path.rename(marker_path)
        return True
    except OSError:
        return False


def main():
    """PostToolUse:Bash hook to record session marker after worktree creation."""
    try:
        data = parse_hook_input()

        ctx = create_hook_context(data)
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})

        # Issue #1842: Use standardized helper for tool result extraction
        # Ensure we have a dict for .get() calls (tool_result can be a string)
        raw_result = get_tool_result(data)
        tool_result = raw_result if isinstance(raw_result, dict) else {}

        # Only process Bash commands
        if tool_name != "Bash":
            result = make_approve_result("worktree-creation-marker", "Not Bash command")
            print(json.dumps(result))
            sys.exit(0)

        command = tool_input.get("command", "")

        # Only process git worktree add commands
        if "worktree add" not in command:
            result = make_approve_result("worktree-creation-marker", "Not worktree add")
            print(json.dumps(result))
            sys.exit(0)

        # Check if command succeeded
        # PostToolUse provides tool_result with stdout/stderr/exit_code
        # Default to 0 (success) if exit_code not provided - consistent with other hooks
        # Issue #1461: Previous default of -1 caused marker creation to be skipped
        exit_code = tool_result.get("exit_code", tool_result.get("exitCode", 0))
        if exit_code != 0:
            msg = f"worktree add failed with exit code {exit_code}"
            log_hook_execution("worktree-creation-marker", "skip", msg)
            result = make_approve_result("worktree-creation-marker", msg)
            print(json.dumps(result))
            sys.exit(0)

        # Extract worktree path from command
        worktree_path_str = extract_worktree_add_path(command)
        if not worktree_path_str:
            msg = "Could not extract worktree path from command"
            log_hook_execution("worktree-creation-marker", "skip", msg)
            result = make_approve_result("worktree-creation-marker", msg)
            print(json.dumps(result))
            sys.exit(0)

        # Resolve to absolute path
        worktree_path = Path(worktree_path_str)
        if not worktree_path.is_absolute():
            # Try to resolve relative to project dir or cwd
            import os

            project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
            if project_dir:
                worktree_path = Path(project_dir) / worktree_path
            else:
                worktree_path = Path.cwd() / worktree_path

        worktree_path = worktree_path.resolve()

        # Check if worktree exists
        if not worktree_path.exists():
            msg = f"Worktree path does not exist: {worktree_path}"
            log_hook_execution("worktree-creation-marker", "skip", msg)
            result = make_approve_result("worktree-creation-marker", msg)
            print(json.dumps(result))
            sys.exit(0)

        # Write session marker
        if write_session_marker(ctx, worktree_path):
            session_id = ctx.get_session_id()
            msg = f"Recorded session marker in {worktree_path.name}: {session_id[:16]}..."
            log_hook_execution("worktree-creation-marker", "approve", msg)
            result = make_approve_result("worktree-creation-marker", msg)
        else:
            msg = f"Failed to write session marker to {worktree_path.name}"
            log_hook_execution("worktree-creation-marker", "approve", msg)
            result = make_approve_result("worktree-creation-marker", msg)

        # Always exit with success - this is just a marker, not a gate
        print(json.dumps(result))
        sys.exit(0)

    except Exception as e:
        # Fail open - don't affect the operation
        error_msg = f"Hook error: {e}"
        print(f"[worktree-creation-marker] {error_msg}", file=sys.stderr)
        result = make_approve_result("worktree-creation-marker", error_msg)
        print(json.dumps(result))
        sys.exit(0)


if __name__ == "__main__":
    main()
