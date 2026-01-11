#!/usr/bin/env python3
"""別セッションが作業中のworktreeへの誤介入を防止する。

Why:
    別セッションが作業中のworktreeを編集すると、競合やコンフリクトが発生し、
    両セッションの作業が無駄になる。セッションマーカーで所有権を確認し、
    別セッションのworktreeへの編集をブロックする。

What:
    - Edit対象ファイルが.worktrees/配下かチェック
    - 該当worktreeの.claude-sessionマーカーを読む
    - 現在のセッションIDと比較
    - 不一致ならブロック（別セッションが作業中）
    - 一致またはマーカーなしなら許可

State:
    reads: .worktrees/*/.claude-session

Remarks:
    - worktree-creation-marker.pyがマーカー作成、本フックがマーカー検証
    - session-worktree-status.pyは警告、本フックはブロック

Changelog:
    - silenvx/dekita#1396: フック追加
"""

import json
import sys
from pathlib import Path

from lib.constants import CONTINUATION_HINT, SESSION_MARKER_FILE
from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import (
    create_hook_context,
    get_session_ancestry,
    parse_hook_input,
)


def get_worktree_from_path(file_path: str) -> Path | None:
    """Extract worktree directory from file path.

    Args:
        file_path: Absolute path to a file

    Returns:
        Path to worktree directory if file is inside .worktrees/, None otherwise
    """
    path = Path(file_path)

    # Look for .worktrees in path parts
    parts = path.parts
    for i, part in enumerate(parts):
        if part == ".worktrees" and i + 1 < len(parts):
            # Found .worktrees, next part is the worktree name
            worktree_path = Path(*parts[: i + 2])
            return worktree_path

    return None


def read_session_marker(worktree_path: Path) -> str | None:
    """Read session ID from worktree marker file.

    Expects JSON format:
    {
        "session_id": "...",
        "created_at": "2025-12-30T09:30:00+00:00"
    }

    Args:
        worktree_path: Path to worktree directory

    Returns:
        Session ID if marker exists and is valid JSON, None otherwise
    """
    marker_path = worktree_path / SESSION_MARKER_FILE
    try:
        if marker_path.exists():
            content = marker_path.read_text().strip()
            data = json.loads(content)
            return data.get("session_id", "")
    except (OSError, json.JSONDecodeError):
        # File access errors or invalid JSON are treated as "no marker"
        # to fail-open and not block operations unnecessarily
        pass
    return None


def main():
    """PreToolUse:Edit hook to prevent editing files in other sessions' worktrees."""
    try:
        data = parse_hook_input()

        ctx = create_hook_context(data)
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})

        # Only check Edit and Write operations
        if tool_name not in ("Edit", "Write"):
            log_hook_execution("worktree-session-guard", "skip", f"Not Edit/Write: {tool_name}")
            result = make_approve_result("worktree-session-guard", f"Not Edit/Write: {tool_name}")
            print(json.dumps(result))
            sys.exit(0)

        file_path = tool_input.get("file_path", "")
        if not file_path:
            log_hook_execution("worktree-session-guard", "skip", "No file_path in tool_input")
            result = make_approve_result("worktree-session-guard", "No file_path in tool_input")
            print(json.dumps(result))
            sys.exit(0)

        # Check if file is inside a worktree
        worktree_path = get_worktree_from_path(file_path)
        if not worktree_path:
            # Not in a worktree, allow
            log_hook_execution("worktree-session-guard", "approve", "File not in worktree")
            result = make_approve_result("worktree-session-guard", "File not in worktree")
            print(json.dumps(result))
            sys.exit(0)

        # Read session marker
        marker_session = read_session_marker(worktree_path)
        if not marker_session:
            # No marker = legacy worktree or new session hasn't written marker yet
            # Allow but log
            msg = f"No session marker in {worktree_path.name}"
            log_hook_execution("worktree-session-guard", "approve", msg)
            result = make_approve_result("worktree-session-guard", msg)
            print(json.dumps(result))
            sys.exit(0)

        # Get current session ID
        current_session = ctx.get_session_id()

        # Compare sessions
        if marker_session == current_session:
            # Same session, allow
            msg = f"Same session for {worktree_path.name}"
            log_hook_execution("worktree-session-guard", "approve", msg)
            result = make_approve_result("worktree-session-guard", msg)
            print(json.dumps(result))
            sys.exit(0)

        # Issue #2331: Check if marker session is an ancestor (fork-session support)
        # In fork-sessions, child sessions should be able to access worktrees created
        # by their parent sessions. However, sibling sessions should NOT access each
        # other's worktrees. We check if marker_session appears BEFORE current_session
        # in the ancestry list to ensure it's a true ancestor, not a sibling.
        transcript_path = data.get("transcript_path")
        if transcript_path:
            ancestry = get_session_ancestry(transcript_path)
            if marker_session in ancestry and current_session in ancestry:
                try:
                    marker_index = ancestry.index(marker_session)
                    current_index = ancestry.index(current_session)
                    if marker_index < current_index:
                        # Marker session appears before current session = ancestor
                        msg = f"Ancestor session worktree for {worktree_path.name}"
                        log_hook_execution("worktree-session-guard", "approve", msg)
                        result = make_approve_result("worktree-session-guard", msg)
                        print(json.dumps(result))
                        sys.exit(0)
                except ValueError:
                    # Session not found in ancestry, treat as non-ancestor
                    pass

        # Different session - block!
        reason = (
            f"このworktree ({worktree_path.name}) は別のセッションが作業中です。\n\n"
            f"マーカーセッション: {marker_session[:16]}...\n"
            f"現在のセッション: {current_session[:16]}...\n\n"
            "別セッションの作業を引き継がないでください。\n"
            "このIssueをスキップして、次のIssue（worktreeがないもの）に進んでください。\n\n"
            "確認コマンド:\n"
            "```bash\n"
            "git worktree list\n"
            f"```{CONTINUATION_HINT}"
        )
        log_hook_execution(
            "worktree-session-guard",
            "block",
            f"Different session for {worktree_path.name}: marker={marker_session[:16]}, current={current_session[:16]}",
        )
        result = make_block_result("worktree-session-guard", reason, ctx)
        print(json.dumps(result))
        sys.exit(0)

    except Exception as e:
        # Fail open - don't block on errors
        error_msg = f"Hook error: {e}"
        print(f"[worktree-session-guard] {error_msg}", file=sys.stderr)
        result = make_approve_result("worktree-session-guard", error_msg)
        print(json.dumps(result))
        sys.exit(0)


if __name__ == "__main__":
    main()
