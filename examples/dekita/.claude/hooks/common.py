#!/usr/bin/env python3
"""Claude Codeフック共通のディレクトリ定数とラッパー関数。

Why:
    CLAUDE_PROJECT_DIR環境変数に依存するディレクトリ定数と、
    デフォルト引数を提供するラッパー関数を一箇所にまとめる。

What:
    - PROJECT_DIR, STATE_DIR, EXECUTION_LOG_DIR等のディレクトリ定数
    - check_and_update_session_marker等のラッパー関数

Remarks:
    - 他のユーティリティはlib/から直接インポートすること
    - Issue #2014でre-export削除。全コードはlib/から直接インポート
    - Issue #2505: worktree内でもメインリポジトリパスを返す

Changelog:
    - silenvx/dekita#2014: re-export削除
    - silenvx/dekita#2505: worktreeからメインリポジトリ解決
    - silenvx/dekita#2509: パス検証によるセキュリティ強化
"""

import os
from pathlib import Path
from typing import Any

# =============================================================================
# Directory Constants
# These are defined here because they depend on CLAUDE_PROJECT_DIR
# and are used by many modules.
# =============================================================================


def _get_main_repo_from_worktree(cwd: Path) -> Path | None:
    """Get main repository path from a worktree.

    Issue #2505: Worktrees store their git info in a `.git` file (not directory)
    containing a path like `gitdir: /path/to/main/.git/worktrees/xxx`.

    The gitdir can be either absolute or relative (default for `git worktree add`):
    - Absolute: `gitdir: /path/to/main/.git/worktrees/xxx`
    - Relative: `gitdir: ../.git/worktrees/xxx`

    Args:
        cwd: Current working directory to check.

    Returns:
        Path to main repository, or None if not a worktree.
    """
    git_file = cwd / ".git"
    # Worktrees have .git as a file, not a directory
    if not git_file.is_file():
        return None

    try:
        content = git_file.read_text().strip()
        # Expected format: "gitdir: /path/to/main/.git/worktrees/name"
        # or relative: "gitdir: ../.git/worktrees/name"
        if not content.startswith("gitdir:"):
            return None

        gitdir = content.split(":", 1)[1].strip()
        gitdir_path = Path(gitdir)

        # Handle relative paths: resolve against the worktree directory
        if not gitdir_path.is_absolute():
            gitdir_path = (cwd / gitdir_path).resolve()

        # Navigate from ".git/worktrees/xxx" to main repo root
        # Expected structure: /main/repo/.git/worktrees/issue-123
        if gitdir_path.parent.name == "worktrees" and gitdir_path.parent.parent.name == ".git":
            # Issue #2509: Validate the resolved path to prevent path traversal attacks
            # First verify that gitdir_path itself exists (prevents arbitrary path construction)
            if not gitdir_path.exists():
                return None

            # Verify that the .git/worktrees directory actually exists on disk
            # Note: If worktrees_dir exists, parent .git directory is guaranteed to exist
            worktrees_dir = gitdir_path.parent
            if not worktrees_dir.is_dir():
                return None

            return gitdir_path.parent.parent.parent

    except (OSError, ValueError):
        pass  # Best effort - file read may fail

    return None


def _get_project_dir() -> Path:
    """Get project directory from environment or cwd.

    Issue #2505: When running in a worktree, returns the main repository path
    instead of the worktree path. This ensures all session logs are stored
    in the main repository's .claude/logs/ directory, preventing log loss
    when worktrees are deleted.
    """
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        env_path = Path(env_dir)
        # Even if CLAUDE_PROJECT_DIR is set to a worktree, resolve to main repo
        main_repo = _get_main_repo_from_worktree(env_path)
        if main_repo:
            return main_repo
        return env_path

    cwd = Path.cwd()
    # Check if we're in a worktree and resolve to main repo
    main_repo = _get_main_repo_from_worktree(cwd)
    if main_repo:
        return main_repo

    return cwd


_PROJECT_DIR = _get_project_dir()

# Persistent log directory for post-session analysis
# Stored in project-local .claude/logs/ for Claude Code to analyze later
LOG_DIR = _PROJECT_DIR / ".claude" / "logs"

# Subdirectories for organized logging
EXECUTION_LOG_DIR = LOG_DIR / "execution"  # Hook execution, git operations
METRICS_LOG_DIR = LOG_DIR / "metrics"  # PR metrics, session metrics, etc.
# Review/test completion markers (.done files)
# Marker file specification (Issue #813):
# - Filename: Uses SANITIZED branch name (e.g., "codex-review-feat-issue-123.done")
# - Content: Uses ORIGINAL branch name (e.g., "feat/issue-123:abc1234")
# This is intentional: filenames must be filesystem-safe, but content preserves
# the actual branch name for accurate identification and logging.
MARKERS_LOG_DIR = LOG_DIR / "markers"

# Session-only directory for temporary state (markers, locks)
# Cleared on reboot, which is appropriate for session-scoped data
SESSION_DIR = Path(os.environ.get("TMPDIR", "/tmp")) / "claude-hooks"

# Flow progress log directory
# Note: Flow logs are now written to session-specific files (flow-progress-{session_id}.jsonl)
FLOW_LOG_DIR = LOG_DIR / "flow"

# Note: Review quality logs are now written to session-specific files.
# Use lib.logging.read_all_session_log_entries() for cross-session analysis.

# =============================================================================
# Wrapper Functions
# These wrap lib/ functions to provide default directory arguments.
# =============================================================================


def check_and_update_session_marker(marker_name: str, session_dir: Path | None = None) -> bool:
    """Atomically check if this is a new session and update the marker.

    Wrapper for lib.session.check_and_update_session_marker().
    Uses SESSION_DIR as default if session_dir is not provided.

    Args:
        marker_name: Unique name for this marker (e.g., "task-start-checklist")
        session_dir: Directory to store session markers. Defaults to SESSION_DIR.

    Returns:
        True if this is a new session, False otherwise.
    """
    from lib.session import check_and_update_session_marker as _check_and_update_session_marker

    if session_dir is None:
        session_dir = SESSION_DIR
    return _check_and_update_session_marker(marker_name, session_dir)


def get_session_start_time(session_id: str, flow_log_dir: Path | None = None) -> Any:
    """Get the session start time from flow state.

    Wrapper for lib.session.get_session_start_time().
    Uses FLOW_LOG_DIR as default if flow_log_dir is not provided.

    Issue #2496: Added session_id parameter (required).

    Args:
        session_id: The session ID to look up.
        flow_log_dir: Directory containing flow logs. Defaults to FLOW_LOG_DIR.

    Returns:
        Session start time as datetime with timezone, or None if not available.
    """
    from lib.session import get_session_start_time as _get_session_start_time

    if flow_log_dir is None:
        flow_log_dir = FLOW_LOG_DIR
    # Note: lib.session.get_session_start_time() expects (flow_log_dir, session_id) order,
    # but this wrapper exposes (session_id, flow_log_dir) for caller convenience.
    # The argument order is explicitly swapped here when delegating to the library function.
    return _get_session_start_time(flow_log_dir, session_id)


def get_active_flow_for_context(
    flow_id: str,
    context: dict[str, Any],
    session_id: str | None = None,
) -> str | None:
    """Check if there's already an active flow for the given context.

    Wrapper for lib.flow.get_active_flow_for_context().

    Args:
        flow_id: The flow type ID (e.g., "issue-ai-review")
        context: Context dict to match (e.g., {"issue_number": 123})
        session_id: Optional session ID for log file isolation

    Returns:
        Existing flow instance ID if found, None otherwise.
    """
    from lib.flow import get_active_flow_for_context as _get_active_flow

    return _get_active_flow(FLOW_LOG_DIR, flow_id, context, session_id)


def start_flow(
    flow_id: str,
    context: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> str | None:
    """Start a new flow instance.

    Wrapper for lib.flow.start_flow().

    Args:
        flow_id: The flow type ID (e.g., "issue-ai-review")
        context: Optional context dict (e.g., {"issue_number": 123})
        session_id: Optional session ID for log file isolation

    Returns:
        Flow instance ID, or None on error.
    """
    from lib.flow import start_flow as _start_flow

    return _start_flow(FLOW_LOG_DIR, flow_id, context, session_id)


def complete_flow(
    flow_instance_id: str,
    flow_id: str | None = None,
    session_id: str | None = None,
) -> bool:
    """Mark a flow as completed.

    Wrapper for lib.flow.complete_flow().

    Args:
        flow_instance_id: The flow instance ID from start_flow()
        flow_id: Optional flow ID for the log entry
        session_id: Optional session ID for log file isolation

    Returns:
        True if recorded successfully, False on error.
    """
    from lib.flow import complete_flow as _complete_flow

    return _complete_flow(FLOW_LOG_DIR, flow_instance_id, flow_id, session_id)


def complete_flow_step(
    flow_instance_id: str,
    step_id: str,
    flow_id: str | None = None,
    session_id: str | None = None,
) -> bool:
    """Mark a flow step as completed.

    Wrapper for lib.flow.complete_flow_step().

    Args:
        flow_instance_id: The flow instance ID from start_flow()
        step_id: The step ID to mark as completed
        flow_id: Optional flow type ID for logging
        session_id: Optional session ID for log file isolation

    Returns:
        True if recorded successfully, False on error.
    """
    from lib.flow import complete_flow_step as _complete_flow_step

    return _complete_flow_step(FLOW_LOG_DIR, flow_instance_id, step_id, flow_id, session_id)


def get_flow_status(
    flow_instance_id: str,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """Get the current status of a flow instance.

    Wrapper for lib.flow.get_flow_status().

    Args:
        flow_instance_id: The flow instance ID
        session_id: Optional session ID for log file isolation

    Returns:
        Dict with flow status, or None if not found.
    """
    from lib.flow import get_flow_status as _get_flow_status

    return _get_flow_status(FLOW_LOG_DIR, flow_instance_id, session_id)


def get_incomplete_flows(session_id: str | None = None) -> list[dict[str, Any]]:
    """Get all incomplete flows in the current session.

    Wrapper for lib.flow.get_incomplete_flows().

    Args:
        session_id: Optional session ID for log file isolation

    Returns:
        List of incomplete flow status dicts.
    """
    from lib.flow import get_incomplete_flows as _get_incomplete_flows

    return _get_incomplete_flows(FLOW_LOG_DIR, session_id)


def check_flow_completion(
    flow_instance_id: str,
    session_id: str | None = None,
) -> bool:
    """Check if a flow is complete.

    Wrapper for lib.flow.check_flow_completion().

    Args:
        flow_instance_id: The flow instance ID
        session_id: Optional session ID for log file isolation

    Returns:
        True if all expected steps are completed, False otherwise.
    """
    from lib.flow import check_flow_completion as _check_flow_completion

    return _check_flow_completion(FLOW_LOG_DIR, flow_instance_id, session_id)


def get_research_activity_file() -> Path:
    """Get session-specific research activity file path.

    Wrapper for lib.research.get_research_activity_file().

    Returns:
        Path to the research activity file for the current session.
    """
    from lib.research import get_research_activity_file as _get_research_activity_file

    return _get_research_activity_file(SESSION_DIR)


def get_exploration_file() -> Path:
    """Get session-specific exploration file path.

    Wrapper for lib.research.get_exploration_file().

    Returns:
        Path to the exploration depth file for the current session.
    """
    from lib.research import get_exploration_file as _get_exploration_file

    return _get_exploration_file(SESSION_DIR)


def check_research_done() -> bool:
    """Check if any research was done in this session.

    Wrapper for lib.research.check_research_done().

    Returns:
        True if WebSearch or WebFetch was used, False otherwise.
    """
    from lib.research import check_research_done as _check_research_done

    return _check_research_done(SESSION_DIR)


def get_research_summary() -> dict:
    """Get summary of research activities in session.

    Wrapper for lib.research.get_research_summary().

    Returns:
        dict with count, tools_used, has_research.
    """
    from lib.research import get_research_summary as _get_research_summary

    return _get_research_summary(SESSION_DIR)


def get_exploration_depth() -> dict:
    """Get current exploration depth stats.

    Wrapper for lib.research.get_exploration_depth().

    Returns:
        dict with counts, total, sufficient.
    """
    from lib.research import get_exploration_depth as _get_exploration_depth

    return _get_exploration_depth(SESSION_DIR)


def log_review_comment(
    pr_number: int | str,
    comment_id: int | str,
    reviewer: str,
    category: str | None = None,
    file_path: str | None = None,
    line_number: int | None = None,
    body_preview: str | None = None,
    resolution: str | None = None,
    validity: str | None = None,
    issue_created: int | None = None,
    reason: str | None = None,
    *,
    metrics_log_dir: Path | None = None,
    session_id: str | None = None,
) -> None:
    """Log a review comment to the review quality log.

    Wrapper for lib.review.log_review_comment().
    Uses METRICS_LOG_DIR as default if metrics_log_dir is not provided.

    Args:
        pr_number: PR number
        comment_id: Comment ID
        reviewer: Reviewer name
        category: Comment category (optional)
        file_path: File path (optional)
        line_number: Line number (optional)
        body_preview: Body preview (optional)
        resolution: Resolution status (optional)
        validity: Validity status (optional)
        issue_created: Issue number if created (optional)
        reason: Reason for resolution (optional)
        metrics_log_dir: Directory for metrics logs. Defaults to METRICS_LOG_DIR.
        session_id: Session ID (uses PPID fallback if None). Issue #2496.
    """
    from lib.review import log_review_comment as _log_review_comment

    if metrics_log_dir is None:
        metrics_log_dir = METRICS_LOG_DIR
    _log_review_comment(
        metrics_log_dir=metrics_log_dir,
        pr_number=pr_number,
        comment_id=comment_id,
        reviewer=reviewer,
        category=category,
        file_path=file_path,
        line_number=line_number,
        body_preview=body_preview,
        resolution=resolution,
        validity=validity,
        issue_created=issue_created,
        reason=reason,
        session_id=session_id,
    )


# =============================================================================
# Public API
# =============================================================================
__all__ = [
    # Directory constants
    "_PROJECT_DIR",
    "EXECUTION_LOG_DIR",
    "FLOW_LOG_DIR",
    "LOG_DIR",
    "MARKERS_LOG_DIR",
    "METRICS_LOG_DIR",
    "SESSION_DIR",
    # Wrapper functions
    "check_and_update_session_marker",
    "check_flow_completion",
    "check_research_done",
    "complete_flow",
    "complete_flow_step",
    "get_active_flow_for_context",
    "get_exploration_depth",
    "get_exploration_file",
    "get_flow_status",
    "get_incomplete_flows",
    "get_research_activity_file",
    "get_research_summary",
    "get_session_start_time",
    "log_review_comment",
    "start_flow",
]
