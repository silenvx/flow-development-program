#!/usr/bin/env python3
"""sibling fork-session間での同一ファイル編集を警告する。

Why:
    複数のfork-sessionが同一ファイルを編集すると、マージ時に
    コンフリクトが発生する。事前に警告し調整を促す。

What:
    - 編集対象ファイルを取得
    - sibling fork-sessionの変更ファイル一覧を取得
    - 同一ファイルを編集中のsiblingがあれば警告

Remarks:
    - 警告型フック（ブロックしない、systemMessageで警告）
    - PreToolUse:Edit/Writeで発火（fork-sessionのみ）
    - session-worktree-statusは起動時のみ（リアルタイム警告との違い）
    - パス正規化でworktree間の比較を可能に

Changelog:
    - silenvx/dekita#2513: フック追加
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))

from lib.execution import log_hook_execution
from lib.session import is_fork_session, parse_hook_input
from lib.session_graph import get_active_worktree_sessions


def get_target_file(tool_input: dict) -> str | None:
    """Extract target file path from tool input.

    Args:
        tool_input: The tool_input from hook input.

    Returns:
        The file path being edited, or None if not found.
    """
    return tool_input.get("file_path")


def normalize_path(file_path: str) -> str:
    """Normalize file path for comparison.

    Converts absolute paths to relative paths from repo root.
    Handles both worktree paths and main repo paths.

    Args:
        file_path: The file path to normalize.

    Returns:
        Normalized relative path.
    """
    path = Path(file_path)
    parts = path.parts

    # Look for .worktrees pattern (handles worktree paths)
    for i, part in enumerate(parts):
        if part == ".worktrees":
            # Skip .worktrees/<name>/ prefix (i is .worktrees, i+1 is name, i+2+ is path)
            if i + 2 < len(parts):
                return str(Path(*parts[i + 2 :]))
            return str(path)

    # For non-worktree paths, try to find a common repo root indicator
    # Look for common project markers (.git, .claude, src, etc.)
    repo_markers = {".git", ".claude", "src", "frontend", "worker", "shared"}
    for i, part in enumerate(parts):
        if part in repo_markers and i > 0:
            # Return path from this point onwards
            return str(Path(*parts[i:]))

    return str(path)


def find_conflicting_worktrees(
    target_file: str,
    active_sessions: dict,
) -> list[dict]:
    """Find worktrees that have the same file in their changed files.

    Args:
        target_file: The file being edited.
        active_sessions: Dict from get_active_worktree_sessions.

    Returns:
        List of conflicting worktree info dicts.
    """
    conflicts = []
    normalized_target = normalize_path(target_file)

    # Check sibling worktrees for conflicts
    sibling_worktrees = active_sessions.get("sibling", [])
    for info in sibling_worktrees:
        # Check if target file is in this worktree's changed files
        for changed_file in info.changed_files:
            if normalize_path(changed_file) == normalized_target:
                conflicts.append(
                    {
                        "issue_number": info.issue_number,
                        "path": str(info.path),
                        "changed_files": sorted(info.changed_files)[:5],
                        "total_files": len(info.changed_files),
                    }
                )
                break

    return conflicts


def format_warning(target_file: str, conflicts: list[dict]) -> str:
    """Format the conflict warning message.

    Args:
        target_file: The file being edited.
        conflicts: List of conflicting worktree info.

    Returns:
        Formatted warning message.
    """
    lines = ["⚠️ 並行編集の競合可能性:\n"]
    lines.append(f"編集対象: {normalize_path(target_file)}\n")
    lines.append("同一ファイルを編集中のsibling session:")

    for conflict in conflicts:
        issue_str = (
            f"Issue #{conflict['issue_number']}"
            if conflict["issue_number"]
            else conflict["path"].split("/")[-1]
        )
        lines.append(f"  - {issue_str}")
        lines.append(f"    Worktree: {conflict['path']}")

        files = conflict["changed_files"]
        total_files = conflict["total_files"]
        files_str = ", ".join(files[:3])
        if total_files > 3:
            files_str += f" (+{total_files - 3} more)"
        lines.append(f"    変更中: {files_str}")
        lines.append("")

    lines.append("マージ時にコンフリクトが発生する可能性があります。")
    lines.append("Tip: 競合を避けるため、siblingセッションと調整するか、")
    lines.append("     別の独立したIssueに着手することを検討してください。")

    return "\n".join(lines)


def main() -> None:
    """PreToolUse hook for Edit/Write tools.

    Warns when editing a file that sibling fork-sessions are also modifying.
    """
    result = {"decision": "approve"}

    try:
        data = parse_hook_input()
        session_id = data.get("session_id", "")
        source = data.get("source", "")
        transcript_path = data.get("transcript_path")
        tool_input = data.get("tool_input", {})

        # Only run for fork-sessions
        if not is_fork_session(session_id, source, transcript_path):
            log_hook_execution("parallel-edit-conflict-check", "approve", "Not a fork-session")
            print(json.dumps(result))
            return

        # Get target file
        target_file = get_target_file(tool_input)
        if not target_file:
            log_hook_execution("parallel-edit-conflict-check", "approve", "No target file")
            print(json.dumps(result))
            return

        # Get active worktree sessions
        try:
            active_sessions = get_active_worktree_sessions(session_id, transcript_path)
        except Exception:
            # Fail silently - don't block on errors
            log_hook_execution("parallel-edit-conflict-check", "approve", "Error getting sessions")
            print(json.dumps(result))
            return

        # Find conflicts
        conflicts = find_conflicting_worktrees(target_file, active_sessions)

        if conflicts:
            warning = format_warning(target_file, conflicts)
            result["systemMessage"] = warning

            log_hook_execution(
                "parallel-edit-conflict-check",
                "approve",
                f"Warning: {len(conflicts)} conflicting worktree(s)",
                {"target_file": target_file, "conflicts": conflicts},
            )
        else:
            log_hook_execution("parallel-edit-conflict-check", "approve", "No conflicts")

    except Exception as e:
        error_msg = f"Hook error: {e}"
        print(f"[parallel-edit-conflict-check] {error_msg}", file=sys.stderr)
        log_hook_execution("parallel-edit-conflict-check", "approve", error_msg)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
