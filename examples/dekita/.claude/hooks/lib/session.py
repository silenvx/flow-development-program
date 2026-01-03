#!/usr/bin/env python3
"""セッション識別・追跡機能を提供する。

Why:
    セッション単位でログをグループ化し、状態を追跡するために
    セッションID管理機能が必要。

What:
    - HookContext: 依存性注入パターンによるセッション情報管理
    - parse_hook_input(): stdinからフック入力をパース
    - is_fork_session(): fork-session検出
    - check_and_update_session_marker(): セッションマーカー機構

State:
    - reads: ~/.claude/projects/*/*.jsonl（transcript）
    - writes: .claude/logs/session/*.marker

Remarks:
    - fork-session検出はtranscriptファイル名・内容を使用
    - セッションマーカーはファイルロックで競合防止
    - ppidフォールバックは完全廃止（Issue #2529）

Changelog:
    - silenvx/dekita#759: parse_hook_input()追加
    - silenvx/dekita#1758: common.pyから分離
    - silenvx/dekita#2308: transcript-based fork検出
    - silenvx/dekita#2413: HookContextクラス追加
    - silenvx/dekita#2496: グローバル状態を完全削除
    - silenvx/dekita#2529: ppidフォールバック完全廃止
"""

import hashlib
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.constants import SESSION_GAP_THRESHOLD


def _debug_log(message: str) -> None:
    """Output debug message to stderr if CLAUDE_DEBUG=1.

    Issue #779: DEBUGログ出力でsession_id取得元を可視化。
    """
    if os.environ.get("CLAUDE_DEBUG") == "1":
        print(message, file=sys.stderr)


@dataclass
class HookContext:
    """Context object for hook execution with dependency injection.

    Issue #2413: Replaces global _HOOK_SESSION_ID with explicit context passing.
    This improves testability and thread-safety by avoiding global state.

    Attributes:
        session_id: The Claude Code session ID from hook JSON input.

    Example:
        >>> hook_input = parse_hook_input()
        >>> ctx = create_hook_context(hook_input)
        >>> session_id = ctx.get_session_id()  # Use in your hook logic
    """

    session_id: str | None = None

    def get_session_id(self) -> str | None:
        """Get session ID or None if not set.

        Issue #2529: ppidフォールバック完全廃止
        session_idがセットされていない場合はNoneを返す。

        Returns:
            The session ID if set, otherwise None.
        """
        if self.session_id:
            truncated = self.session_id[:16]
            suffix = "..." if len(self.session_id) > 16 else ""
            _debug_log(f"[session_id] source=hook_input, value={truncated}{suffix}")
            return self.session_id
        _debug_log("[session_id] source=None, session_id not provided in hook input")
        return None


def create_hook_context(hook_input: dict[str, Any]) -> HookContext:
    """Create HookContext from hook JSON input.

    Issue #2413: Factory function for creating HookContext instances.

    Args:
        hook_input: Parsed hook JSON input dictionary.

    Returns:
        HookContext with session_id from input if available.

    Example:
        >>> hook_input = {"session_id": "abc-123", "tool_name": "Bash"}
        >>> ctx = create_hook_context(hook_input)
        >>> ctx.session_id
        'abc-123'
    """
    return HookContext(session_id=hook_input.get("session_id"))


def parse_hook_input() -> dict[str, Any]:
    """Parse hook JSON input from stdin.

    This is the preferred way to read hook input. It:
    1. Reads and parses JSON from stdin
    2. Returns the parsed hook input dict

    After calling this function, use create_hook_context() to create
    a HookContext for accessing session_id and other context-dependent
    functionality.

    Issue #759: Standard hook input parsing.
    Issue #2496: Removed global state side effects. Use HookContext instead.

    Returns:
        Parsed hook input as a dictionary. Empty dict on parse error.

    Example:
        >>> def main():
        ...     hook_input = parse_hook_input()
        ...     ctx = create_hook_context(hook_input)
        ...     session_id = ctx.get_session_id()
        ...     # ... rest of hook logic
    """
    try:
        hook_input = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return {}

    return hook_input


def get_session_id() -> str:
    """Get a project-scoped identifier based on project directory.

    Returns a stable identifier that is unique per project directory.
    Use this for project-level tracking that persists across conversations.

    Note:
        This is different from HookContext.get_session_id() which tracks
        individual conversation sessions - useful for grouping actions
        within a single Claude Code session.

    Returns:
        A 16-character hex string derived from SHA-256 hash of project directory.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    return hashlib.sha256(project_dir.encode("utf-8")).hexdigest()[:16]


def check_and_update_session_marker(marker_name: str, session_dir: Path) -> bool:
    """Atomically check if this is a new session and update the marker.

    This function provides a unified session marker mechanism for hooks that
    need to perform an action once per session (e.g., showing a checklist,
    checking for orphan worktrees).

    Uses file locking to prevent race conditions when multiple tool calls
    occur simultaneously at session start.

    Issue #558: セッションマーカー機構を common.py に統合

    Args:
        marker_name: Unique name for this marker (e.g., "task-start-checklist",
                    "orphan-worktree-check"). Creates separate marker and lock
                    files for each name.
        session_dir: Directory to store session markers.

    Returns:
        True if this is a new session (marker was updated).
        False if within an existing session or on error.

    Example::

        if check_and_update_session_marker("my-hook", SESSION_DIR):
            # First call in session - show message
            result["systemMessage"] = "Welcome to new session!"
    """
    import fcntl

    session_dir.mkdir(parents=True, exist_ok=True)
    marker_file = session_dir / f"{marker_name}.marker"
    lock_file = session_dir / f"{marker_name}.lock"

    # Use file locking to ensure atomicity
    # Lock is automatically released when the file is closed
    try:
        with lock_file.open("w") as f:
            # Acquire exclusive lock (blocks if another process holds it)
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)

            # Check if new session
            is_new = True
            if marker_file.exists():
                try:
                    last_check = float(marker_file.read_text().strip())
                    current_time = time.time()
                    is_new = (current_time - last_check) > SESSION_GAP_THRESHOLD
                except (ValueError, OSError):
                    pass  # Treat as new session

            if is_new:
                # Update marker atomically
                marker_file.write_text(str(time.time()))

            return is_new
    except OSError:
        # On lock error, skip the check to avoid blocking
        return False


def get_session_marker_dir() -> Path:
    """Get the directory for session marker files.

    Returns the path to the session marker directory, creating it if needed.
    Uses CLAUDE_PROJECT_DIR if set, otherwise falls back to cwd.

    Returns:
        Path to the session marker directory.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    session_dir = Path(project_dir) / ".claude" / "logs" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def is_valid_session_id(session_id: str) -> bool:
    """Validate that session_id is in UUID format.

    Issue #2301: Defensive programming to validate session_id.
    Claude Code provides session_id in standard UUID format.

    Uses Python's uuid module for robust validation instead of regex.

    Args:
        session_id: The session ID to validate.

    Returns:
        True if session_id matches UUID format, False otherwise.
    """
    try:
        # uuid.UUID() is permissive, so we compare the parsed UUID's string form
        # with the original lowercased string to enforce standard hyphenated format.
        return str(uuid.UUID(session_id)) == session_id.lower()
    except (ValueError, TypeError):
        return False


def handle_session_id_arg(session_id: str | None) -> str | None:
    """Validate session ID from command line argument.

    Common helper for scripts that accept --session-id argument.
    Validates the session ID format and returns it if valid.

    Issue #2326: Extracted from collect-session-metrics.py,
    session-report-generator.py, and analyze-fork-tree.py.
    Issue #2496: No longer sets global state. Returns validated session_id instead.

    Args:
        session_id: The session ID from command line argument, or None.

    Returns:
        The validated session_id if valid, None otherwise.

    Usage::

        parser.add_argument("--session-id", type=str, default=None)
        args = parser.parse_args()
        validated_id = handle_session_id_arg(args.session_id)
        ctx = create_hook_context({"session_id": validated_id})
    """
    if session_id:
        if not is_valid_session_id(session_id):
            print(f"Warning: Invalid session ID format: {session_id}", file=sys.stderr)
            return None
        return session_id
    return None


def _is_valid_transcript_path(transcript_file: Path) -> bool:
    """Validate that transcript_file is in an allowed location.

    Allowed locations:
    1. Within CLAUDE_PROJECT_DIR (for tests and local files)
    2. Within ~/.claude/projects/ (Claude Code's transcript storage)

    Issue #2333: Fixed fork-session detection by allowing ~/.claude paths.
    Claude Code stores transcripts in ~/.claude/projects/<project-hash>/
    which is outside CLAUDE_PROJECT_DIR, causing security check to always
    reject legitimate transcript files.

    Issue #2336: Changed parameter from str to Path to avoid redundant
    resolve() calls. Callers should pass already-resolved Path objects.

    Args:
        transcript_file: Resolved Path object to validate.

    Returns:
        True if path is in an allowed location, False otherwise.
    """
    try:
        # Allow paths within CLAUDE_PROJECT_DIR
        project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())).resolve()
        if transcript_file.is_relative_to(project_dir):
            return True

        # Allow paths within ~/.claude/projects/ (Claude Code's transcript storage)
        # Note: is_relative_to() works correctly even if the directory doesn't exist
        claude_projects_dir = Path.home() / ".claude" / "projects"
        if transcript_file.is_relative_to(claude_projects_dir):
            return True

        _debug_log(
            f"[session] Rejecting transcript_path outside allowed locations: {transcript_file}"
        )
        return False
    except (OSError, ValueError):
        return False


def get_parent_session_id(transcript_path: str | None) -> str | None:
    """Get the parent session ID from transcript file.

    In a fork-session, the first user message in the transcript contains
    the original (parent) session ID, while subsequent messages have the
    new session ID. This function extracts the parent session ID by finding
    the first user message with a valid (non-None) sessionId.

    Issue #2308: Transcript-based fork detection for parallel session support.

    Note:
        This function is deprecated for fork-session detection because its
        logic of checking only the first entry's sessionId is unreliable.
        Use `has_different_session_ids()` instead, which provides a more
        robust detection method by checking for multiple distinct sessionIds
        in the transcript.

    Args:
        transcript_path: Path to the transcript JSONL file. Must be within
            CLAUDE_PROJECT_DIR or ~/.claude/projects/ for security.

    Returns:
        The parent session ID if found, None otherwise.
    """
    if not transcript_path:
        return None

    try:
        transcript_file = Path(transcript_path).resolve()

        # Security: Validate transcript_path is in an allowed location
        if not _is_valid_transcript_path(transcript_file):
            return None

        if not transcript_file.exists():
            return None

        with transcript_file.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    # Find first user message with valid (non-None) sessionId
                    if entry.get("type") == "user":
                        session_id = entry.get("sessionId")
                        if session_id is not None:
                            return session_id
                except json.JSONDecodeError:
                    continue

    except OSError:
        # File access errors (permission denied, file not found, etc.)
        # are handled gracefully by returning None
        pass

    return None


def has_different_session_ids(transcript_path: str | None, current_session_id: str) -> bool:
    """Check if the transcript contains entries with different session IDs.

    Fork-sessions have a distinctive pattern: they contain entries from
    parent sessions with different sessionIds. When you fork a session,
    the parent session's conversation history is included, and those
    entries retain their original sessionIds.

    Issue #2328: Used for fork-session detection. This is the definitive
    way to detect fork-sessions, as summary entries can also appear in
    non-fork sessions due to context compression.

    Args:
        transcript_path: Path to the transcript JSONL file. Must be within
            CLAUDE_PROJECT_DIR or ~/.claude/projects/ for security.
        current_session_id: The current session's ID to compare against.

    Returns:
        True if entries with different sessionIds (not null) are found,
        False otherwise.
    """
    if not transcript_path or not current_session_id:
        return False

    try:
        transcript_file = Path(transcript_path).resolve()

        # Security: Validate transcript_path is in an allowed location
        if not _is_valid_transcript_path(transcript_file):
            return False

        if not transcript_file.exists():
            return False

        with transcript_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    session_id = entry.get("sessionId")
                    # Check if there's a different sessionId (not null and not current)
                    if session_id is not None and session_id != current_session_id:
                        return True
                except json.JSONDecodeError:
                    continue

    except OSError:
        _debug_log(
            f"[session] OSError while reading transcript for fork detection: {transcript_path}"
        )

    return False


def extract_session_id_from_transcript_path(transcript_path: str | None) -> str | None:
    """Extract session ID from transcript file path.

    Claude Code stores transcripts with the session ID as the filename:
    ~/.claude/projects/<project-hash>/<session-id>.jsonl

    Issue #2342: Used for fork-session detection. When Claude Code forks
    a session, it creates a new session ID but the hook input still
    contains the parent session's ID. By comparing the transcript filename
    with the hook's session_id, we can detect fork-sessions.

    Args:
        transcript_path: Path to the transcript JSONL file.

    Returns:
        The session ID extracted from the filename, or None if invalid.
    """
    if not transcript_path:
        return None

    try:
        path = Path(transcript_path)
        # Filename without extension is the session ID
        filename = path.stem
        # Validate it looks like a UUID
        if is_valid_session_id(filename):
            return filename
        return None
    except (OSError, ValueError):
        return None


# Maximum lines to scan in transcript for parent session ID detection.
# Fork transcripts contain parent sessionId entries from conversation history,
# which typically appear in the first few lines of the file.
_MAX_LINES_TO_SCAN_FOR_FORK = 50


def get_fork_transcript_session_id(
    transcript_path: str | None, parent_session_id: str
) -> str | None:
    """Find a fork transcript that contains the parent session ID.

    Issue #2344: Claude Code passes parent session's transcript_path to hooks
    during fork-session, but creates a new transcript file for the fork.
    This function finds the first transcript file that contains the parent
    session ID in its content (indicating a fork relationship).

    Note: This function returns the first matching fork found (sorted by
    modification time, newest first). If multiple forks exist from the same
    parent, only one will be detected, but this is sufficient for boolean
    fork detection.

    Args:
        transcript_path: Path to any transcript file (used to find the directory).
        parent_session_id: The session ID from hook input (potential parent).

    Returns:
        Session ID of a fork transcript (different from parent), or None.
    """
    if not transcript_path or not parent_session_id:
        return None

    try:
        transcript_file = Path(transcript_path).resolve()

        # Security validation: ensure path is within allowed directories
        if not _is_valid_transcript_path(transcript_file):
            _debug_log(
                f"[session] get_fork_transcript_session_id: "
                f"invalid transcript path: {transcript_path}"
            )
            return None

        transcript_dir = transcript_file.parent
        if not transcript_dir.exists():
            _debug_log(
                f"[session] get_fork_transcript_session_id: "
                f"directory does not exist: {transcript_dir}"
            )
            return None

        # Find transcript files, sorted by modification time (newest first)
        # to prioritize the most recent fork
        jsonl_files = sorted(
            transcript_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for jsonl_file in jsonl_files:
            file_session_id = jsonl_file.stem
            # Skip if not a valid UUID or same as parent
            if not is_valid_session_id(file_session_id):
                continue
            if file_session_id == parent_session_id:
                continue

            # Check if this file contains the parent session ID in sessionId field
            try:
                with jsonl_file.open("r", encoding="utf-8") as f:
                    for i, line in enumerate(f):
                        if i >= _MAX_LINES_TO_SCAN_FOR_FORK:
                            break
                        # Parse JSON to check sessionId field explicitly
                        # to avoid false positives from substring matches
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(entry, dict) and entry.get("sessionId") == parent_session_id:
                            # This file contains parent's sessionId → it's a fork
                            return file_session_id
            except OSError as e:
                _debug_log(
                    f"[session] get_fork_transcript_session_id: error reading {jsonl_file}: {e}"
                )
                continue

        return None
    except OSError as e:
        _debug_log(f"[session] get_fork_transcript_session_id: error: {e}")
        return None


def is_fork_session(
    current_session_id: str,
    source: str,
    transcript_path: str | None = None,
) -> bool:
    """Detect if this is a fork-session (new session_id with conversation history).

    A fork-session occurs when --fork-session flag is used, creating a new
    session_id while preserving conversation history from a parent session.

    Detection logic:
    1. source="compact" → Always False (context compression, not a fork)
    2. source="resume" → Multiple detection methods:
       a. Compare hook's session_id with transcript filename
       b. Find transcript files that contain parent session ID in content
       c. Check transcript content for different sessionIds
    3. Otherwise → False

    Issue #2308: Uses transcript-based detection for parallel session support.
    Issue #2316: Removed file-based fallback (last-session-id.txt) due to
    unreliability with parallel sessions.
    Issue #2328: Changed detection to check for different sessionIds in
    transcript. Summary entries are not reliable indicators because they
    can also appear in non-fork sessions due to context compression.
    Issue #2342: Added transcript filename comparison. Claude Code passes
    the parent session's ID to hooks during fork-session, but the transcript
    filename contains the parent's ID. If hook's session_id differs from
    transcript filename, it's a fork-session.
    Issue #2344: Added content-based fork detection. Claude Code passes
    parent session's transcript_path AND session_id to hooks during fork.
    Find transcript files that contain the parent session ID to detect fork.

    Args:
        current_session_id: The current Claude session ID.
        source: The session source from hook input ("resume", "compact", etc.)
        transcript_path: Path to the transcript JSONL file (required for detection).

    Returns:
        True if this is a fork-session (transcript contains entries with
        different sessionIds from parent sessions), False otherwise.
    """
    # source="compact" is context compression, not a fork
    if source == "compact":
        return False

    # Fork-session detection for source="resume"
    if source == "resume":
        # Validate transcript path before any detection logic
        # This prevents false positives from invalid/non-existent paths
        if transcript_path:
            transcript_file = Path(transcript_path).resolve()
            if not _is_valid_transcript_path(transcript_file) or not transcript_file.exists():
                _debug_log(
                    f"[session] Skipping fork detection: invalid or non-existent transcript: "
                    f"{transcript_path}"
                )
                return False

        # Primary detection: Compare hook's session_id with transcript filename
        # In fork-session, hook receives new session_id but transcript file
        # is named after the parent session_id
        transcript_session_id = extract_session_id_from_transcript_path(transcript_path)
        if transcript_session_id and transcript_session_id != current_session_id:
            _debug_log(
                f"[session] Fork detected: hook_id={current_session_id[:8]}... "
                f"!= transcript_id={transcript_session_id[:8]}..."
            )
            return True

        # Issue #2344: Secondary detection using fork transcript file
        # Claude Code may pass parent's transcript_path AND session_id to hooks.
        # Find transcript files that contain the parent session ID to detect fork.
        fork_session_id = get_fork_transcript_session_id(transcript_path, current_session_id)
        if fork_session_id:
            _debug_log(
                f"[session] Fork detected via content: parent_id={current_session_id[:8]}... "
                f"found in fork_id={fork_session_id[:8]}..."
            )
            return True

        # Fallback: Check if transcript contains entries with different sessionIds
        # This handles cases where the primary detection might miss
        return has_different_session_ids(transcript_path, current_session_id)

    return False


def get_session_ancestry(transcript_path: str | None) -> list[str]:
    """Return all distinct session IDs found in a transcript in order of appearance.

    Fork sessions may include history from parent sessions or sibling sessions
    in their transcripts. Therefore, a transcript can contain multiple distinct
    sessionIds, including not just parent-child relationships but also sibling
    relationships. This function collects all unique sessionIds from the
    transcript in the order they first appear.

    Issue #2331: Used to understand the structure of forked session groups.
    The returned list represents the order of sessionIds as they appear in
    the transcript, but does NOT guarantee that it represents a strict
    "ancestry chain" (pure parent-child lineage). Use index comparison to
    determine if a sessionId is an ancestor of the current session.

    Args:
        transcript_path: Path to the transcript JSONL file. Must be within
            CLAUDE_PROJECT_DIR or ~/.claude/projects/ for security.

    Returns:
        List of distinct session IDs in order of first appearance (empty list
        on error). Example: ["session-id-A", "session-id-B", "session-id-C"]
    """
    if not transcript_path:
        return []

    try:
        transcript_file = Path(transcript_path).resolve()

        # Security: Validate transcript_path is in an allowed location
        if not _is_valid_transcript_path(transcript_file):
            return []

        if not transcript_file.exists():
            return []

        seen_session_ids: set[str] = set()
        ordered_session_ids: list[str] = []

        with transcript_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    session_id = entry.get("sessionId")
                    # Only include valid (non-null, non-empty) sessionIds
                    if session_id and session_id not in seen_session_ids:
                        seen_session_ids.add(session_id)
                        ordered_session_ids.append(session_id)
                except json.JSONDecodeError:
                    continue

        return ordered_session_ids

    except (OSError, ValueError):
        # ValueError can be raised by is_relative_to() on Windows with different drives
        _debug_log(f"[session] Error while reading transcript for ancestry: {transcript_path}")
        return []


def get_session_start_time(flow_log_dir: Path, session_id: str) -> Any:
    """Get the session start time from flow state.

    Returns the session start time recorded by flow-state-updater.py,
    which is stored in the session-specific state file.

    Issue #1158: Used for outcome-based session evaluation to filter
    GitHub artifacts (PRs, Issues) created during this session.
    Issue #2496: Added session_id parameter instead of using global state.

    Args:
        flow_log_dir: Directory containing flow logs.
        session_id: The session ID to look up.

    Returns:
        Session start time as datetime with timezone, or None if not available.
    """
    from lib.timestamp import parse_iso_timestamp

    # Security: Validate session_id to prevent path traversal attacks
    # Issue #2529: ppidフォールバック完全廃止、UUIDフォーマットのみ受け入れ
    if not is_valid_session_id(session_id):
        return None

    state_file = flow_log_dir / f"state-{session_id}.json"

    try:
        if not state_file.exists():
            return None

        with open(state_file, encoding="utf-8") as f:
            state = json.load(f)

        start_time_str = state.get("global", {}).get("session_start_time")
        if not start_time_str:
            return None

        return parse_iso_timestamp(start_time_str)
    except (OSError, json.JSONDecodeError):
        return None
