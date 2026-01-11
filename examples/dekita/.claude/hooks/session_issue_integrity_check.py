#!/usr/bin/env python3
"""セッション別Issue追跡データの整合性を検証。

Why:
    issue-creation-trackerで記録したIssueが正しくファイルに保存されているか
    検証する必要がある。データ損失を早期に検出し、警告する。

What:
    - セッション開始時（SessionStart）に発火
    - 過去5セッションのログとファイルを照合
    - 実行ログに記録されたIssueがファイルにも存在するか確認
    - 不一致があればsystemMessageで警告

State:
    - reads: .claude/logs/execution/hook-execution-*.jsonl
    - reads: .claude/logs/flow/session-created-issues-*.json
    - reads: .claude/logs/flow/state-*.json

Remarks:
    - 非ブロック型（警告のみ）
    - Issue #2003の修正適用前のデータで不整合が発生する可能性

Changelog:
    - silenvx/dekita#2004: フック追加（整合性検証）
"""

import json
import os
import re
import sys
from pathlib import Path

from lib.execution import log_hook_execution
from lib.logging import read_session_log_entries
from lib.session import HookContext, create_hook_context, parse_hook_input


def _get_log_dir() -> Path:
    """Get the execution log directory."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    return Path(project_dir) / ".claude" / "logs" / "execution"


def _get_flow_dir() -> Path:
    """Get the flow log directory."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    return Path(project_dir) / ".claude" / "logs" / "flow"


def get_issues_from_execution_log(session_id: str) -> set[int]:
    """Extract issue numbers recorded for a session from execution log.

    Args:
        session_id: The Claude session ID to check.

    Returns:
        Set of issue numbers found in the execution log for this session.
    """
    issues: set[int] = set()

    # Pattern to extract issue number from reason field
    # e.g., "Recorded P2 issue #1971 - implement after current task"
    issue_pattern = re.compile(r"Recorded (?:P\d+ )?issue #(\d+)")

    # Read from session-specific log file
    entries = read_session_log_entries(_get_log_dir(), "hook-execution", session_id)

    for entry in entries:
        # Check if this is an issue-creation-tracker entry
        if entry.get("hook") == "issue-creation-tracker" and entry.get("decision") == "approve":
            reason = entry.get("reason", "")
            match = issue_pattern.search(reason)
            if match:
                issues.add(int(match.group(1)))

    return issues


def get_issues_from_session_file(session_id: str) -> set[int]:
    """Load issues from session-specific file.

    Args:
        session_id: The Claude session ID.

    Returns:
        Set of issue numbers from the session file.
    """
    issues_file = _get_flow_dir() / f"session-created-issues-{session_id}.json"

    if not issues_file.exists():
        return set()

    try:
        data = json.loads(issues_file.read_text())
        return set(data.get("issues", []))
    except (json.JSONDecodeError, OSError) as e:
        print(
            f"[session-issue-integrity-check] Warning: Failed to read session issues file {issues_file}: {e}",
            file=sys.stderr,
        )
        return set()


def get_recent_session_ids(limit: int = 5) -> list[str]:
    """Get recent session IDs from state files.

    Args:
        limit: Maximum number of sessions to return.

    Returns:
        List of session IDs, most recent first.
    """
    flow_dir = _get_flow_dir()
    if not flow_dir.exists():
        return []

    # Find state files and sort by modification time
    # Handle TOCTOU race condition where files may be deleted during iteration
    state_files_with_mtime: list[tuple[float, Path]] = []
    for path in flow_dir.glob("state-*.json"):
        try:
            mtime = path.stat().st_mtime
        except (FileNotFoundError, OSError):
            # 他プロセスによる削除やアクセス不能なファイルはスキップする
            continue
        state_files_with_mtime.append((mtime, path))

    state_files = [p for _, p in sorted(state_files_with_mtime, key=lambda x: x[0], reverse=True)]

    session_ids = []
    for state_file in state_files[:limit]:
        # Extract session ID from filename: state-{session_id}.json
        name = state_file.stem  # state-{session_id}
        if name.startswith("state-"):
            session_ids.append(name[6:])  # Remove "state-" prefix

    return session_ids


def verify_integrity(ctx: HookContext) -> list[str]:
    """Verify integrity of recent sessions' issue tracking.

    Args:
        ctx: HookContext for session information.

    Returns:
        List of warning messages for any integrity issues found.
    """
    warnings: list[str] = []
    current_session_id = ctx.get_session_id()

    # Check recent sessions (excluding current)
    for session_id in get_recent_session_ids(limit=5):
        if session_id == current_session_id:
            continue

        # Get issues from both sources
        log_issues = get_issues_from_execution_log(session_id)
        file_issues = get_issues_from_session_file(session_id)

        # Check for missing issues (logged but not in file)
        missing = log_issues - file_issues
        if missing:
            warnings.append(
                f"Session {session_id[:8]}...: Issues logged but missing from file: {sorted(missing)}"
            )

        # Check for extra issues (in file but not logged) - less critical
        extra = file_issues - log_issues
        if extra:
            # This is informational, not a warning
            pass

    return warnings


def main():
    """SessionStart hook to verify issue tracking integrity."""
    result = {"continue": True}

    try:
        input_data = parse_hook_input()

        ctx = create_hook_context(input_data)
        hook_type = input_data.get("hook_type", "")

        # Only run on SessionStart
        if hook_type != "SessionStart":
            print(json.dumps(result))
            return

        # Verify integrity of recent sessions
        warnings = verify_integrity(ctx)

        if warnings:
            warning_text = "\n".join(f"  - {w}" for w in warnings)
            system_message = (
                f"⚠️ Issue追跡データの整合性問題を検出しました:\n\n"
                f"{warning_text}\n\n"
                f"Issue #2003 の修正が適用される前のデータかもしれません。"
            )
            result["systemMessage"] = system_message
            log_hook_execution(
                "session-issue-integrity-check",
                "warn",
                f"Integrity issues found: {len(warnings)} sessions affected",
            )
        else:
            log_hook_execution(
                "session-issue-integrity-check", "approve", "No integrity issues found"
            )

    except Exception as e:
        print(f"[session-issue-integrity-check] Error: {e}", file=sys.stderr)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
