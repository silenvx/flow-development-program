#!/usr/bin/env python3
"""ログレベル分離とエラーコンテキスト管理を提供する。

Why:
    ログの可視化改善とデバッグ支援のため、レベル別ログ出力と
    エラー発生時のコンテキスト（前後の操作）キャプチャが必要。

What:
    - get_log_level(): 決定値からログレベル判定
    - log_to_level_file(): レベル別ファイルへのログ出力
    - log_to_session_file(): セッション固有ファイルへのログ出力
    - ErrorContextManager: エラー前後のコンテキスト管理
    - cleanup_old_context_files(): 古いコンテキストファイル削除

State:
    - writes: .claude/logs/execution/hook-errors.log
    - writes: .claude/logs/execution/hook-warnings.log
    - writes: .claude/logs/execution/hook-debug.log（HOOK_DEBUG_LOG=1時）
    - writes: .claude/logs/execution/error-context/error-context-*.jsonl

Remarks:
    - リングバッファでエラー前N件の操作を保持
    - ファイルロックで並行書き込み時の競合防止
    - DEBUGログはHOOK_DEBUG_LOG=1環境変数で有効化

Changelog:
    - silenvx/dekita#1367: ログレベル分離を追加
    - silenvx/dekita#1840: セッションログ関数を追加
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .constants import (
    ERROR_CONTEXT_AFTER_SIZE,
    ERROR_CONTEXT_BUFFER_SIZE,
    ERROR_CONTEXT_DIR,
    ERROR_CONTEXT_RETENTION_DAYS,
    ERROR_LOG_FILE,
    LOG_LEVEL_DEBUG_DECISIONS,
    LOG_LEVEL_ERROR_DECISIONS,
    LOG_LEVEL_WARN_DECISIONS,
    WARN_LOG_FILE,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

# Log levels
LOG_LEVEL_ERROR = "ERROR"
LOG_LEVEL_WARN = "WARN"
LOG_LEVEL_INFO = "INFO"
LOG_LEVEL_DEBUG = "DEBUG"


def get_log_level(decision: str) -> str:
    """Determine log level from hook decision value.

    Args:
        decision: The decision value from a hook (e.g., "block", "approve")

    Returns:
        Log level string: "ERROR", "WARN", "INFO", or "DEBUG"
    """
    if decision in LOG_LEVEL_ERROR_DECISIONS:
        return LOG_LEVEL_ERROR
    if decision in LOG_LEVEL_WARN_DECISIONS:
        return LOG_LEVEL_WARN
    if decision in LOG_LEVEL_DEBUG_DECISIONS:
        return LOG_LEVEL_DEBUG
    return LOG_LEVEL_INFO


def log_to_level_file(
    log_dir: Path,
    entry: dict[str, Any],
    level: str,
) -> None:
    """Write log entry to level-specific file.

    Args:
        log_dir: Directory for log files (typically EXECUTION_LOG_DIR)
        entry: Log entry dictionary to write
        level: Log level ("ERROR", "WARN", "DEBUG")
    """
    if level == LOG_LEVEL_ERROR:
        log_file = log_dir / ERROR_LOG_FILE
    elif level == LOG_LEVEL_WARN:
        log_file = log_dir / WARN_LOG_FILE
    else:
        # DEBUG level - only write if env var is set
        if os.environ.get("HOOK_DEBUG_LOG") != "1":
            return
        from .constants import DEBUG_LOG_FILE

        log_file = log_dir / DEBUG_LOG_FILE

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        # Skip logging if file operations fail
        pass


class ErrorContextManager:
    """Manages error context capture using a ring buffer.

    Maintains a ring buffer of recent log entries per session.
    When an error (block) occurs, captures the buffer contents plus
    subsequent operations to provide context for debugging.
    """

    def __init__(self) -> None:
        """Initialize the error context manager."""
        # Session ID -> deque of recent entries
        self._buffers: dict[str, deque[dict[str, Any]]] = {}
        # Session ID -> pending capture info (timestamp, error_entry, after_entries)
        self._pending_captures: dict[str, dict[str, Any]] = {}

    def add_entry(self, session_id: str, entry: dict[str, Any]) -> None:
        """Add a log entry to the session's ring buffer.

        Args:
            session_id: Claude session identifier
            entry: Log entry dictionary
        """
        if not session_id:
            return

        # Initialize buffer for new sessions
        if session_id not in self._buffers:
            self._buffers[session_id] = deque(maxlen=ERROR_CONTEXT_BUFFER_SIZE)

        self._buffers[session_id].append(entry.copy())

        # Check if we're capturing after-error context
        if session_id in self._pending_captures:
            pending = self._pending_captures[session_id]
            pending["after_entries"].append(entry.copy())

            # Save context if we've captured enough after-entries
            if len(pending["after_entries"]) >= ERROR_CONTEXT_AFTER_SIZE:
                self._save_pending_context(session_id)

    def on_error(
        self,
        session_id: str,
        error_entry: dict[str, Any],
        log_dir: Path,
    ) -> Path | None:
        """Handle error occurrence and start capturing context.

        Args:
            session_id: Claude session identifier
            error_entry: The error log entry
            log_dir: Directory for error context files

        Returns:
            Path to the context file if saved immediately, None if pending
        """
        if not session_id:
            return None

        # Get the before-context from ring buffer
        # Note: add_entry was called before on_error, so the error entry
        # is already in the buffer. Exclude it to avoid duplication.
        buffer = self._buffers.get(session_id, [])
        before_entries = list(buffer)[:-1] if buffer else []

        # Store pending capture info
        self._pending_captures[session_id] = {
            "timestamp": error_entry.get("timestamp", datetime.now(UTC).isoformat()),
            "error_entry": error_entry.copy(),
            "before_entries": before_entries,
            "after_entries": [],
            "log_dir": log_dir,
        }

        return None

    def _save_pending_context(self, session_id: str) -> Path | None:
        """Save pending error context to file.

        Args:
            session_id: Claude session identifier

        Returns:
            Path to the saved context file, or None if save failed
        """
        if session_id not in self._pending_captures:
            return None

        pending = self._pending_captures.pop(session_id)
        return self.save_context(
            log_dir=pending["log_dir"],
            session_id=session_id,
            error_entry=pending["error_entry"],
            before_entries=pending["before_entries"],
            after_entries=pending["after_entries"],
        )

    def save_context(
        self,
        log_dir: Path,
        session_id: str,
        error_entry: dict[str, Any],
        before_entries: Sequence[dict[str, Any]],
        after_entries: Sequence[dict[str, Any]],
    ) -> Path | None:
        """Save error context to a file.

        Args:
            log_dir: Base log directory
            session_id: Claude session identifier
            error_entry: The error log entry
            before_entries: Log entries before the error
            after_entries: Log entries after the error

        Returns:
            Path to the saved context file, or None if save failed
        """
        context_dir = log_dir / ERROR_CONTEXT_DIR
        try:
            context_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None

        # Generate filename with timestamp
        timestamp = error_entry.get("timestamp", datetime.now(UTC).isoformat())
        # Convert ISO timestamp to filename-safe format
        safe_timestamp = timestamp.replace(":", "-").replace("+", "_")
        filename = f"error-context-{safe_timestamp}.jsonl"
        context_file = context_dir / filename

        try:
            with open(context_file, "w") as f:
                # Write context metadata
                metadata = {
                    "type": "metadata",
                    "session_id": session_id,
                    "timestamp": timestamp,
                    "hook": error_entry.get("hook", "unknown"),
                    "before_count": len(before_entries),
                    "after_count": len(after_entries),
                }
                f.write(json.dumps(metadata, ensure_ascii=False) + "\n")

                # Write before context
                f.write(
                    json.dumps(
                        {"type": "context_before", "entries": list(before_entries)},
                        ensure_ascii=False,
                    )
                    + "\n"
                )

                # Write the error entry
                f.write(
                    json.dumps(
                        {"type": "error", "entry": error_entry},
                        ensure_ascii=False,
                    )
                    + "\n"
                )

                # Write after context
                f.write(
                    json.dumps(
                        {"type": "context_after", "entries": list(after_entries)},
                        ensure_ascii=False,
                    )
                    + "\n"
                )

            return context_file
        except OSError:
            return None

    def flush_pending(self, session_id: str) -> Path | None:
        """Flush any pending error context for a session.

        Called when session ends to ensure partial after-context is saved.

        Args:
            session_id: Claude session identifier

        Returns:
            Path to the saved context file, or None if nothing pending
        """
        if session_id in self._pending_captures:
            return self._save_pending_context(session_id)
        return None

    def clear_session(self, session_id: str) -> None:
        """Clear buffer and pending captures for a session.

        Args:
            session_id: Claude session identifier
        """
        self._buffers.pop(session_id, None)
        self._pending_captures.pop(session_id, None)


def cleanup_old_context_files(log_dir: Path, max_age_days: int | None = None) -> int:
    """Remove error context files older than the retention period.

    Args:
        log_dir: Base log directory containing error-context subdirectory
        max_age_days: Maximum age in days (defaults to ERROR_CONTEXT_RETENTION_DAYS)

    Returns:
        Number of files deleted
    """
    if max_age_days is None:
        max_age_days = ERROR_CONTEXT_RETENTION_DAYS

    context_dir = log_dir / ERROR_CONTEXT_DIR
    if not context_dir.exists():
        return 0

    cutoff_time = time.time() - (max_age_days * 24 * 60 * 60)
    deleted_count = 0

    try:
        for file_path in context_dir.glob("error-context-*.jsonl"):
            try:
                if file_path.stat().st_mtime < cutoff_time:
                    file_path.unlink()
                    deleted_count += 1
            except OSError:
                continue
    except OSError:
        # Directory iteration failed (permission denied, removed during iteration, etc.)
        # Cleanup is best-effort; proceed silently without affecting hook execution
        pass

    return deleted_count


# Global error context manager instance
_error_context_manager: ErrorContextManager | None = None


def get_error_context_manager() -> ErrorContextManager:
    """Get the global error context manager instance.

    Returns:
        The singleton ErrorContextManager instance
    """
    global _error_context_manager
    if _error_context_manager is None:
        _error_context_manager = ErrorContextManager()
    return _error_context_manager


# =============================================================================
# Session Log Functions (Issue #1840)
# =============================================================================


def get_session_log_file(log_dir: Path, log_name: str, session_id: str) -> Path:
    """Get the path for a session-specific log file.

    Args:
        log_dir: Base directory for log files
        log_name: Base name of the log (e.g., "flow-progress", "api-operations")
        session_id: Claude session identifier

    Returns:
        Path to the session-specific log file (e.g., "flow-progress-{session_id}.jsonl")

    Example:
        >>> get_session_log_file(Path(".claude/logs/flows"), "flow-progress", "abc123")
        PosixPath('.claude/logs/flows/flow-progress-abc123.jsonl')
    """
    return log_dir / f"{log_name}-{session_id}.jsonl"


def log_to_session_file(
    log_dir: Path,
    log_name: str,
    session_id: str,
    entry: dict[str, Any],
) -> bool:
    """Write a log entry to a session-specific file.

    Creates the log directory if it doesn't exist. Uses file locking to
    prevent race conditions when multiple processes write simultaneously.

    Args:
        log_dir: Base directory for log files
        log_name: Base name of the log (e.g., "flow-progress", "api-operations")
        session_id: Claude session identifier
        entry: Log entry dictionary to write (will be JSON-serialized)

    Returns:
        True if write succeeded, False otherwise

    Example:
        >>> log_to_session_file(
        ...     Path(".claude/logs/flows"),
        ...     "flow-progress",
        ...     "abc123",
        ...     {"event": "phase_complete", "phase": "implementation"}
        ... )
        True
    """
    if not session_id:
        return False

    log_file = get_session_log_file(log_dir, log_name, session_id)

    try:
        log_dir.mkdir(parents=True, exist_ok=True)

        # Create a copy to avoid mutating caller's dict
        write_entry = dict(entry)
        if "timestamp" not in write_entry:
            write_entry["timestamp"] = datetime.now(UTC).isoformat()

        with open(log_file, "a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(write_entry, ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return True
    except OSError:
        return False


def read_session_log_entries(
    log_dir: Path,
    log_name: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """Read all entries from a session-specific log file.

    Args:
        log_dir: Base directory for log files
        log_name: Base name of the log
        session_id: Claude session identifier

    Returns:
        List of log entries (empty list if file doesn't exist or on error)
    """
    log_file = get_session_log_file(log_dir, log_name, session_id)
    entries = []

    try:
        if not log_file.exists():
            return entries

        with open(log_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except (OSError, UnicodeDecodeError):
        # File read failed (permission, corruption, encoding) - return empty list
        pass

    return entries


def read_all_session_log_entries(
    log_dir: Path,
    log_name: str,
) -> list[dict[str, Any]]:
    """Read entries from all session files for a given log name.

    Useful for cross-session analysis (e.g., recurring problem detection).

    Args:
        log_dir: Base directory for log files
        log_name: Base name of the log

    Returns:
        List of all log entries from all session files, sorted by timestamp
    """
    entries = []

    try:
        if not log_dir.exists():
            return entries

        # Glob all session files for this log name
        pattern = f"{log_name}-*.jsonl"
        for log_file in log_dir.glob(pattern):
            try:
                with open(log_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entries.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except (OSError, UnicodeDecodeError):
                continue
    except OSError:
        # Directory access failed - return partial results collected so far
        pass

    # Sort by timestamp if available
    entries.sort(key=lambda e: e.get("timestamp", ""))
    return entries
