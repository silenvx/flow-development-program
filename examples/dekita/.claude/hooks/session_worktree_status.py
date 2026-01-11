#!/usr/bin/env python3
"""セッション開始時に既存worktreeの状況を確認し警告する。

Why:
    別セッションが作業中のworktreeに介入すると、競合やコンフリクトが発生する。
    セッション開始時に既存worktreeの状況を把握することで、問題を未然に防ぐ。

What:
    - CWDがworktree内かどうかをチェック
    - 既存worktreeの一覧を取得（git worktree list）
    - 各worktreeの.claude-sessionマーカーを確認
    - 別セッションIDのマーカーがある場合、警告表示
    - 直近1時間以内のコミットがあるworktreeも警告
    - fork-sessionの場合、祖先セッションのworktreeへの介入を禁止警告

State:
    reads: .worktrees/*/.claude-session
    reads: .claude/logs/flow/session-created-issues-*.json

Remarks:
    - ブロックせず警告のみ（実際のブロックはworktree-session-guard.pyが担当）
    - fork-session-collaboration-advisorは提案、これは警告

Changelog:
    - silenvx/dekita#1383: CWDがworktree内かどうかの検出追加
    - silenvx/dekita#1416: フック追加
    - silenvx/dekita#2466: fork-sessionの祖先worktree介入警告追加
    - silenvx/dekita#2475: fork-sessionで自作Issue許可
"""

import json
import os
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from lib.constants import (
    RECENT_COMMIT_THRESHOLD_SECONDS,
    SESSION_GAP_THRESHOLD,
    SESSION_MARKER_FILE,
    TIMEOUT_LIGHT,
    TIMEOUT_MEDIUM,
)
from lib.execution import log_hook_execution
from lib.session import (
    create_hook_context,
    get_session_ancestry,
    is_fork_session,
    parse_hook_input,
)


def _get_session_log_dir() -> Path:
    """Get the directory for session-specific log files.

    Uses .claude/logs/flow/ for consistency with other session logs.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    return Path(project_dir) / ".claude" / "logs" / "flow"


def load_session_created_issues(session_id: str) -> list[int]:
    """Load list of issue numbers created in this session.

    Issue #2475: Used to identify self-created Issues in fork-sessions.

    Args:
        session_id: The Claude session ID.

    Returns:
        List of issue numbers created in this session.
    """
    # Sanitize session_id to prevent path traversal (e.g., "../../etc/passwd")
    safe_session_id = Path(session_id).name
    issues_file = _get_session_log_dir() / f"session-created-issues-{safe_session_id}.json"
    if issues_file.exists():
        try:
            data = json.loads(issues_file.read_text())
            # Guard against non-dict JSON (e.g., array or string)
            if isinstance(data, dict):
                issues = data.get("issues", [])
                # Type validation: must be list of ints
                if isinstance(issues, list) and all(isinstance(n, int) for n in issues):
                    return issues
        except (json.JSONDecodeError, OSError):
            pass  # Best effort - corrupted data is ignored
    return []


# Worktrees directory name
WORKTREES_DIR = ".worktrees"


def get_cwd_worktree_info() -> tuple[str, Path] | None:
    """Check if current working directory is inside a worktree.

    Returns:
        Tuple of (worktree_name, main_repo_path) if CWD is in a worktree,
        None otherwise.
    """
    try:
        cwd = Path.cwd()
    except OSError:
        # CWD access error (deleted directory, etc.)
        return None

    # Find .worktrees in path
    parts = cwd.parts
    for i, part in enumerate(parts):
        if part == WORKTREES_DIR:
            # Next part after .worktrees is the worktree name
            if i + 1 < len(parts):
                worktree_name = parts[i + 1]
                # Main repo path is everything before .worktrees
                main_repo_path = Path(*parts[:i])
                return (worktree_name, main_repo_path)
    return None


class WorktreeInfo(TypedDict):
    """Information about a worktree."""

    path: Path
    locked: bool


def get_worktrees_info() -> list[WorktreeInfo]:
    """Get list of worktree directories and their lock status from git.

    This function makes a single git call to get both worktree paths and
    lock status, avoiding redundant subprocess calls.

    Returns:
        List of WorktreeInfo dictionaries containing 'path' (Path) and 'locked' (bool).
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        worktrees_info: list[WorktreeInfo] = []
        # Split by double newline to get blocks for each worktree
        blocks = result.stdout.strip().split("\n\n")
        for block in blocks:
            lines = block.splitlines()
            if not lines or not lines[0].startswith("worktree "):
                continue

            path = Path(lines[0][9:].strip())

            # Only include worktrees in .worktrees directory
            if WORKTREES_DIR not in path.parts:
                continue

            # Check if locked (line starts with "locked" or "locked <reason>")
            is_locked = any(line.startswith("locked") for line in lines)
            worktrees_info.append({"path": path, "locked": is_locked})

        return worktrees_info
    except (subprocess.TimeoutExpired, OSError):
        # Git command failures are treated as "no worktrees"
        # to fail-open and not block session start unnecessarily
        return []


class SessionMarker(TypedDict, total=False):
    """Session marker data from worktree marker file."""

    session_id: str
    created_at: str  # ISO format timestamp


def read_session_marker(worktree_path: Path) -> SessionMarker | None:
    """Read session marker from worktree marker file.

    Expects JSON format:
    {
        "session_id": "...",
        "created_at": "2025-12-30T09:30:00+00:00"
    }

    Args:
        worktree_path: Path to worktree directory

    Returns:
        SessionMarker dict if marker exists and is valid JSON, None otherwise.
    """
    marker_path = worktree_path / SESSION_MARKER_FILE
    try:
        if marker_path.exists():
            content = marker_path.read_text().strip()
            data = json.loads(content)
            return {
                "session_id": data.get("session_id", ""),
                "created_at": data.get("created_at", ""),
            }
    except (OSError, json.JSONDecodeError):
        # File access errors or invalid JSON are treated as "no marker"
        # to fail-open and not block session start unnecessarily
        pass
    return None


def get_marker_age_seconds(marker: SessionMarker) -> int | None:
    """Get age of marker in seconds from created_at timestamp.

    Args:
        marker: Session marker dict

    Returns:
        Age in seconds if created_at exists and is valid, None otherwise.
    """
    created_at = marker.get("created_at", "")
    if not created_at:
        return None
    try:
        created_time = datetime.fromisoformat(created_at)
        now = datetime.now(UTC)
        # Ensure both are timezone-aware for comparison
        if created_time.tzinfo is None:
            created_time = created_time.replace(tzinfo=UTC)
        return int((now - created_time).total_seconds())
    except (ValueError, TypeError):
        return None


def get_recent_commit_time(worktree_path: Path) -> int | None:
    """Get seconds since the most recent commit in the worktree.

    Args:
        worktree_path: Path to worktree directory

    Returns:
        Seconds since last commit, or None if unable to determine.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree_path), "log", "-1", "--format=%ct"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0 and result.stdout.strip():
            commit_time = int(result.stdout.strip())
            return int(time.time()) - commit_time
    except (subprocess.TimeoutExpired, OSError, ValueError):
        # Git command failures or timeouts are treated as "unable to determine"
        # to fail-open and not block session start unnecessarily
        pass
    return None


def has_uncommitted_changes(worktree_path: Path) -> bool:
    """Check if worktree has uncommitted changes.

    Note: This function is named differently from lib/git.check_uncommitted_changes
    to avoid name collision. This version uses fail-open policy and returns bool only,
    while lib/git version uses fail-close and returns tuple[bool, int].

    Args:
        worktree_path: Path to worktree directory

    Returns:
        True if there are uncommitted changes, False otherwise (including on error).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree_path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        # Fail-open: treat errors as "no uncommitted changes"
        return False


def main():
    """SessionStart hook to warn about existing worktrees."""
    try:
        # Parse input and set session ID
        input_data = parse_hook_input()
        ctx = create_hook_context(input_data)
        current_session = ctx.get_session_id()

        # Issue #2466: Detect fork-session and get ancestor sessions
        source = input_data.get("source", "")
        transcript_path = input_data.get("transcript_path")
        is_fork = is_fork_session(current_session, source, transcript_path)
        ancestor_sessions: list[str] = []
        if is_fork and transcript_path:
            ancestor_sessions = get_session_ancestry(transcript_path)
            # Remove current session from ancestors if present
            ancestor_sessions = [s for s in ancestor_sessions if s != current_session]

        warnings: list[str] = []
        fork_session_warnings: list[str] = []  # Issue #2466: Separate list for fork warnings
        cwd_warning_prefix = ""

        # Check if CWD is inside a worktree (Issue #1383)
        cwd_worktree_info = get_cwd_worktree_info()
        if cwd_worktree_info:
            cwd_worktree_name, main_repo_path = cwd_worktree_info
            # Use shlex.quote to properly escape path for shell (handles quotes, spaces, etc.)
            quoted_path = shlex.quote(str(main_repo_path))
            cwd_warning_prefix = (
                f"**CWDがworktree内です: {cwd_worktree_name}**\n\n"
                "セッション継続後もCWDがworktree内のままになっています。\n"
                "worktree削除がブロックされる可能性があるため、mainリポジトリに移動してください:\n\n"
                f"```\ncd {quoted_path}\n```\n\n"
            )
            log_hook_execution(
                "session-worktree-status", "warn", f"CWD is inside worktree: {cwd_worktree_name}"
            )

        # Get list of worktrees with lock status (single git call)
        worktrees_info = get_worktrees_info()
        if not worktrees_info and not cwd_warning_prefix:
            log_hook_execution("session-worktree-status", "skip", "No worktrees found")
            print(json.dumps({"continue": True}))
            return

        # Check each worktree for issues
        for info in worktrees_info:
            worktree_path = info["path"]
            worktree_name = worktree_path.name
            issues: list[str] = []

            # Check session marker
            marker = read_session_marker(worktree_path)
            if marker:
                marker_session = marker.get("session_id", "")
                marker_age = get_marker_age_seconds(marker)

                if marker_session and marker_session != current_session:
                    # Different session ID
                    session_display = (
                        f"{marker_session[:16]}..." if len(marker_session) > 16 else marker_session
                    )

                    # Issue #2466: Check if this is an ancestor session's worktree
                    if is_fork and marker_session in ancestor_sessions:
                        # This is a fork-session trying to access parent session's worktree
                        fork_session_warnings.append(
                            f"- **{worktree_name}**: 元セッション（fork元）のworktree"
                        )
                        log_hook_execution(
                            "session-worktree-status",
                            "warn",
                            f"Fork-session detected ancestor worktree: {worktree_name}",
                        )
                    else:
                        issues.append(f"別セッション: {session_display}")
                elif marker_session == current_session and marker_age is not None:
                    # Same session ID but check if marker is stale (context continuation case)
                    # If marker is older than SESSION_GAP_THRESHOLD, warn about potential
                    # context continuation from a different "instance" of the same session
                    if marker_age > SESSION_GAP_THRESHOLD:
                        minutes = marker_age // 60
                        issues.append(f"古いセッションマーカー: {minutes}分前")

            # Check if locked (already retrieved in get_worktrees_info)
            if info["locked"]:
                issues.append("ロック中")

            # Check uncommitted changes
            if has_uncommitted_changes(worktree_path):
                issues.append("未コミット変更あり")

            # Check recent commits
            seconds_since_commit = get_recent_commit_time(worktree_path)
            if seconds_since_commit is not None:
                if seconds_since_commit < RECENT_COMMIT_THRESHOLD_SECONDS:
                    minutes = seconds_since_commit // 60
                    # Use "1分未満" for commits less than 1 minute ago
                    if minutes < 1:
                        issues.append("直近1分未満にコミット")
                    else:
                        issues.append(f"直近{minutes}分前にコミット")

            if issues:
                warnings.append(f"- **{worktree_name}**: {', '.join(issues)}")

        # Build final warning message
        if warnings or cwd_warning_prefix or fork_session_warnings:
            warning_parts = ["[session-worktree-status]"]

            # Add CWD warning if present
            if cwd_warning_prefix:
                warning_parts.append(cwd_warning_prefix)

            # Issue #2466: Add fork-session warning (after CWD warning if present)
            # Issue #2470: 自分で作成したIssueは例外
            # Issue #2475: 自作Issueリストを表示
            if fork_session_warnings:
                # Load self-created Issues for this session
                self_created_issues = load_session_created_issues(current_session)
                if self_created_issues:
                    issue_list = ", ".join(f"#{n}" for n in self_created_issues)
                    self_created_note = (
                        f"\n**このセッションで作成したIssue**: {issue_list}\n"
                        "これらのIssueへの作業は**警告なしで許可**されています。\n"
                    )
                else:
                    self_created_note = (
                        "\n**このセッションで作成したIssue**: "
                        "（このセッション中に作成したIssueはまだありません）\n"
                        "今後このセッションで新しく作成したIssueへの作業は"
                        "**警告なしで許可**されます。\n"
                    )

                warning_parts.append(
                    "\n".join(
                        [
                            "⚠️ **fork-session検出**: 元セッション（fork元）のworktreeがあります:",
                            "",
                            *fork_session_warnings,
                            "",
                            "**これらのworktreeへの介入は禁止です。**",
                            "元セッションがまだ作業中の可能性があります。",
                            self_created_note,
                            "その場合は、元セッションのworktreeとは**別の新しいworktree**を作成してください。",
                            "",
                        ]
                    )
                )

            # Add worktree warnings if present
            if warnings:
                warning_parts.append(
                    "以下のworktreeに注意が必要です:\n\n" + "\n".join(warnings) + "\n\n"
                    "これらのworktreeに関連するIssueは作業中の可能性があります。\n"
                    "AGENTS.mdのルールに従い、以下を確認してください:\n"
                    "- worktreeがロック中 or 未コミット変更あり → 作業開始しない\n"
                    "- 直近1時間以内のコミット → ユーザーに確認\n"
                    "- 別セッションマーカー → 引き継がず他のIssueへ\n"
                    "- 古いセッションマーカー → コンテキスト継続の可能性。ユーザーに確認\n"
                )

            warning_msg = " ".join(warning_parts[:2])
            if len(warning_parts) > 2:
                warning_msg += "\n\n" + "\n\n".join(warning_parts[2:])

            log_hook_execution(
                "session-worktree-status",
                "warn",
                f"Found {len(warnings)} worktrees requiring attention, "
                f"fork-session warnings: {len(fork_session_warnings)}, "
                f"CWD in worktree: {bool(cwd_warning_prefix)}",
            )
            print(json.dumps({"continue": True, "systemMessage": warning_msg}))
        else:
            log_hook_execution(
                "session-worktree-status",
                "approve",
                f"Checked {len(worktrees_info)} worktrees, all clear",
            )
            print(json.dumps({"continue": True}))

    except Exception as e:
        # Fail open - don't block on errors
        error_msg = f"Hook error: {e}"
        print(f"[session-worktree-status] {error_msg}", file=sys.stderr)
        print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
