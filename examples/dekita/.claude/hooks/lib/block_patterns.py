#!/usr/bin/env python3
"""ブロック→成功パターンの追跡と分析を行う。

Why:
    フックの有効性を分析し、ブロック後の解決パターンから
    学習するためにblock→success追跡が必要。

What:
    - record_block(): ブロックイベントを記録
    - check_block_resolution(): 成功時に先行ブロックと照合
    - _check_recovery_action(): 代替アクション（回復）を検出

State:
    - writes: $TMPDIR/claude-hooks/recent-blocks-{session}.json
    - writes: .claude/logs/metrics/block-patterns-{session}.jsonl

Remarks:
    - プロセス間でファイルベース永続化（各フックは別プロセス）
    - 60秒以内の解決をblock_resolved、超過をblock_expiredとして記録
    - 5分経過したエントリは自動クリーンアップ

Changelog:
    - silenvx/dekita#1361: ブロックパターン追跡を追加
    - silenvx/dekita#1640: リトライ回数・回復アクション追跡を拡張
    - silenvx/dekita#1758: common.pyから分離
    - silenvx/dekita#1840: セッション固有ファイル形式に変更
    - silenvx/dekita#2496: session_idパラメータ追加でグローバル状態削除
    - silenvx/dekita#2529: ppidフォールバック完全廃止
"""

import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.logging import log_to_session_file
from lib.timestamp import get_local_timestamp


def _get_session_id_with_fallback(session_id: str | None) -> str | None:
    """Get session ID or None if not provided.

    Issue #2529: ppidフォールバック完全廃止、Noneを返す。

    Args:
        session_id: Session ID from caller, or None.

    Returns:
        The session ID or None.
    """
    return session_id


# Block pattern tracking constants
BLOCK_RESOLUTION_WINDOW_SECONDS = 60
BLOCK_CLEANUP_WINDOW_SECONDS = 300  # 5 minutes - cleanup old entries from session file
COMMAND_SIMILARITY_PREFIX_LENGTH = 30  # Issue #1640: prefix length for command similarity check


def _get_session_dir() -> Path:
    """Get session directory for temporary state files."""
    import tempfile

    return Path(os.environ.get("TMPDIR", tempfile.gettempdir())) / "claude-hooks"


def _get_metrics_log_dir(project_dir: Path | None = None) -> Path:
    """Get metrics log directory.

    Args:
        project_dir: Optional project directory. If None, uses CLAUDE_PROJECT_DIR.

    Returns:
        Path to metrics log directory.
    """
    if project_dir is None:
        project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    return project_dir / ".claude" / "logs" / "metrics"


def _get_recent_blocks_file(session_id: str | None = None) -> Path:
    """Get session-specific file path for recent blocks.

    Issue #1361: Store blocks in session-scoped file to persist across
    hook invocations (each hook runs as separate process).
    Issue #2496: Added session_id parameter to avoid global state.

    Args:
        session_id: Session ID from caller, or None to use fallback.

    Returns:
        Path to session-specific recent blocks file.
    """
    session_dir = _get_session_dir()
    sid = _get_session_id_with_fallback(session_id)
    # Sanitize session_id for safe use in filename (remove special chars)
    safe_session_id = re.sub(r"[^a-zA-Z0-9_-]", "_", sid) if sid else "unknown"
    return session_dir / f"recent-blocks-{safe_session_id}.json"


def _load_recent_blocks(session_id: str | None = None) -> dict[str, dict]:
    """Load recent blocks from session file.

    Also performs cleanup of expired entries (older than 5 minutes)
    to prevent unbounded file growth.

    Issue #2496: Added session_id parameter to avoid global state.

    Args:
        session_id: Session ID from caller, or None to use fallback.

    Returns:
        Dict mapping command_hash to block info.
    """
    blocks_file = _get_recent_blocks_file(session_id)
    try:
        if blocks_file.exists():
            with blocks_file.open(encoding="utf-8") as f:
                blocks = json.load(f)
            # Cleanup: remove entries older than BLOCK_CLEANUP_WINDOW_SECONDS
            current_time = time.time()
            max_age = BLOCK_CLEANUP_WINDOW_SECONDS
            cleaned = {
                k: v for k, v in blocks.items() if current_time - v.get("timestamp", 0) < max_age
            }
            # Save cleaned data if entries were removed
            if len(cleaned) < len(blocks):
                _save_recent_blocks(cleaned, session_id)
            return cleaned
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        # Silently ignore file read errors - session file may be corrupted or missing
        # Issue #2204: UnicodeDecodeErrorも追加（破損ファイル対策）
        pass
    return {}


def _save_recent_blocks(blocks: dict[str, dict], session_id: str | None = None) -> None:
    """Save recent blocks to session file.

    Issue #2496: Added session_id parameter to avoid global state.

    Args:
        blocks: Dict mapping command_hash to block info.
        session_id: Session ID from caller, or None to use fallback.
    """
    blocks_file = _get_recent_blocks_file(session_id)
    try:
        session_dir = _get_session_dir()
        session_dir.mkdir(parents=True, exist_ok=True)
        with blocks_file.open("w", encoding="utf-8") as f:
            json.dump(blocks, f, ensure_ascii=False)
    except OSError:
        pass  # Don't fail if save fails


def _generate_block_id(session_id: str | None = None) -> str:
    """Generate a unique block ID for tracking block→success patterns.

    Issue #1361: Used to correlate block events with their resolutions.
    Issue #2496: Added session_id parameter to avoid global state.
    Issue #2529: session_idがNoneの場合は"unknown"を使用。

    Args:
        session_id: Session ID from caller, or None to use fallback.

    Returns:
        A unique identifier combining timestamp and session ID.
        Format: blk_YYYYMMDD-HHMMSS-ffffff-session_id_8chars
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    fallback_sid = _get_session_id_with_fallback(session_id)
    sid = fallback_sid[:8] if fallback_sid else "unknown"
    return f"blk_{timestamp}-{sid}"


def _compute_command_hash(hook: str, command: str | None) -> str:
    """Compute hash for block-success matching.

    Issue #1361: Creates a hash key from hook name and command prefix
    to match blocks with their corresponding successful retries.

    Args:
        hook: The hook name
        command: The command string (first 50 chars used)

    Returns:
        A 16-character hex hash for matching.
    """
    key = f"{hook}:{command[:50] if command else ''}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _rotate_log_if_needed(log_file: Path, max_size: int, max_files: int) -> bool:
    """Rotate log file if it reaches or exceeds the maximum size.

    Simple rotation implementation for block patterns log.

    Args:
        log_file: Path to the log file to rotate.
        max_size: Maximum file size in bytes before rotation.
        max_files: Maximum number of rotated files to keep.

    Returns:
        True if rotation was performed, False otherwise.
    """
    try:
        if not log_file.exists():
            return False

        file_size = log_file.stat().st_size
        if file_size < max_size:
            return False

        # Rotate files: log.5 -> delete, log.4 -> log.5, ..., log -> log.1
        for i in range(max_files, 0, -1):
            rotated = log_file.with_suffix(f".jsonl.{i}")
            if i == max_files:
                if rotated.exists():
                    rotated.unlink()
            else:
                next_rotated = log_file.with_suffix(f".jsonl.{i + 1}")
                if rotated.exists():
                    rotated.rename(next_rotated)

        log_file.rename(log_file.with_suffix(".jsonl.1"))
        return True

    except OSError:
        return False


def _log_block_pattern(
    entry: dict[str, Any],
    session_id: str | None = None,
    metrics_log_dir: Path | None = None,
) -> None:
    """Log block pattern to metrics file.

    Issue #1361: Records block, block_resolved, and block_expired events.
    Issue #1840: Now writes to session-specific file.
    Issue #2496: Added session_id parameter to avoid global state.

    Args:
        entry: The log entry dict to write.
        session_id: Session ID from caller, or None to use fallback.
        metrics_log_dir: Optional metrics log directory. If None, uses default.
    """
    if metrics_log_dir is None:
        metrics_log_dir = _get_metrics_log_dir()

    sid = _get_session_id_with_fallback(session_id)
    # Issue #1840: Write to session-specific file
    log_to_session_file(metrics_log_dir, "block-patterns", sid, entry)


def record_block(
    hook: str,
    reason: str | None,
    details: dict[str, Any] | None,
    session_id: str | None = None,
    metrics_log_dir: Path | None = None,
) -> None:
    """Record a block event for pattern tracking.

    Issue #1361: Records block events and stores them for later matching
    with successful retries. Uses file-based persistence to work across
    hook invocations (each hook runs as separate process).

    Issue #1640: Tracks retry_count when same command is blocked multiple times.
    Issue #2496: Added session_id parameter to avoid global state.

    Args:
        hook: The hook name that blocked
        reason: The block reason message
        details: Additional details from the hook
        session_id: Session ID from caller, or None to use fallback.
        metrics_log_dir: Optional metrics log directory.
    """
    command = details.get("command") if details else None
    cmd_hash = _compute_command_hash(hook, command)
    block_id = _generate_block_id(session_id)

    # Load existing blocks
    recent_blocks = _load_recent_blocks(session_id)

    # Issue #1640: Track retry count for repeated blocks
    retry_count = 1
    if cmd_hash in recent_blocks:
        existing = recent_blocks[cmd_hash]
        retry_count = existing.get("retry_count", 1) + 1

    # Update block record with retry count
    current_time = time.time()
    recent_blocks[cmd_hash] = {
        "block_id": block_id,
        "hook": hook,
        "timestamp": current_time,
        "command_preview": command[:80] if command else None,
        "retry_count": retry_count,
    }

    # Issue #1640: Also track as "last block" for recovery action detection
    recent_blocks["__last_block__"] = {
        "block_id": block_id,
        "hook": hook,
        "timestamp": current_time,
        "command_preview": command[:80] if command else None,
        "reason": reason[:200] if reason else None,
    }

    _save_recent_blocks(recent_blocks, session_id)

    sid = _get_session_id_with_fallback(session_id)
    _log_block_pattern(
        {
            "type": "block",
            "block_id": block_id,
            "session_id": sid,
            "hook": hook,
            "command_hash": cmd_hash,
            "command_preview": command[:80] if command else None,
            "reason": reason[:200] if reason else None,
            "retry_count": retry_count,
            "timestamp": get_local_timestamp(),
        },
        session_id,
        metrics_log_dir,
    )


def _check_recovery_action(
    hook: str,
    command: str | None,
    recent_blocks: dict[str, dict],
    session_id: str | None = None,
    metrics_log_dir: Path | None = None,
) -> None:
    """Check if this action is a recovery from a recent block.

    Issue #1640: Tracks when a different action is taken after a block,
    which indicates the user switched to an alternative approach.
    Issue #2496: Added session_id parameter to avoid global state.

    Args:
        hook: The hook name that approved
        command: The command that was approved
        recent_blocks: The loaded recent blocks dictionary
        session_id: Session ID from caller, or None to use fallback.
        metrics_log_dir: Optional metrics log directory.
    """
    if "__last_block__" not in recent_blocks:
        return

    last_block = recent_blocks["__last_block__"]
    elapsed = time.time() - last_block["timestamp"]

    # Only track recovery within the window
    if elapsed > BLOCK_RESOLUTION_WINDOW_SECONDS:
        # Remove stale last_block entry
        del recent_blocks["__last_block__"]
        _save_recent_blocks(recent_blocks, session_id)
        return

    # Check if this is a different action (recovery) vs same action (retry)
    blocked_command = last_block.get("command_preview", "") or ""
    blocked_hook = last_block.get("hook", "")
    current_command = command[:80] if command else ""

    # If same hook and commands are similar, it's a retry, not a recovery action
    if blocked_hook == hook:
        # Same hook means it's likely a retry, not a switch to different approach
        return

    # If commands are similar, it's a retry, not a recovery action
    if blocked_command and current_command:
        # Simple similarity check: same prefix = likely same command
        prefix_len = COMMAND_SIMILARITY_PREFIX_LENGTH
        if blocked_command[:prefix_len] == current_command[:prefix_len]:
            return

    # This is a recovery action - log it
    sid = _get_session_id_with_fallback(session_id)
    _log_block_pattern(
        {
            "type": "block_recovery",
            "block_id": last_block["block_id"],
            "session_id": sid,
            "blocked_hook": last_block["hook"],
            "blocked_reason": last_block.get("reason"),
            "recovery": {
                "elapsed_seconds": round(elapsed, 1),
                "recovery_hook": hook,
                "recovery_action": current_command if current_command else None,
            },
            "timestamp": get_local_timestamp(),
        },
        session_id,
        metrics_log_dir,
    )

    # Clear last_block after recording recovery
    del recent_blocks["__last_block__"]
    _save_recent_blocks(recent_blocks, session_id)


def check_block_resolution(
    hook: str,
    details: dict[str, Any] | None,
    session_id: str | None = None,
    metrics_log_dir: Path | None = None,
) -> None:
    """Check if this success resolves a recent block.

    Issue #1361: Matches successful operations with prior blocks
    within a 60-second window. Uses file-based persistence to work across
    hook invocations (each hook runs as separate process).

    Issue #1640: Enhanced to track retry_count and recovery_action.
    Issue #2496: Added session_id parameter to avoid global state.

    Args:
        hook: The hook name that approved
        details: Additional details from the hook
        session_id: Session ID from caller, or None to use fallback.
        metrics_log_dir: Optional metrics log directory.
    """
    command = details.get("command") if details else None
    cmd_hash = _compute_command_hash(hook, command)

    # Load blocks from file
    recent_blocks = _load_recent_blocks(session_id)

    # Issue #1640: Check for recovery action (different command after block)
    _check_recovery_action(hook, command, recent_blocks, session_id, metrics_log_dir)

    if cmd_hash not in recent_blocks:
        return

    block_info = recent_blocks[cmd_hash]
    elapsed = time.time() - block_info["timestamp"]

    # Issue #1640: Get actual retry count from block_info
    retry_count = block_info.get("retry_count", 1)

    sid = _get_session_id_with_fallback(session_id)
    if elapsed <= BLOCK_RESOLUTION_WINDOW_SECONDS:
        _log_block_pattern(
            {
                "type": "block_resolved",
                "block_id": block_info["block_id"],
                "session_id": sid,
                "hook": hook,
                "resolution": {
                    "elapsed_seconds": round(elapsed, 1),
                    "retry_count": retry_count,
                },
                "timestamp": get_local_timestamp(),
            },
            session_id,
            metrics_log_dir,
        )
    else:
        _log_block_pattern(
            {
                "type": "block_expired",
                "block_id": block_info["block_id"],
                "session_id": sid,
                "hook": hook,
                "elapsed_seconds": round(elapsed, 1),
                "timestamp": get_local_timestamp(),
            },
            session_id,
            metrics_log_dir,
        )

    # Remove resolved/expired block
    del recent_blocks[cmd_hash]

    # Issue #1640: Also clear __last_block__ if it corresponds to this block
    # to prevent false recovery logs for subsequent approvals
    if "__last_block__" in recent_blocks:
        last_block = recent_blocks["__last_block__"]
        if last_block.get("block_id") == block_info["block_id"]:
            del recent_blocks["__last_block__"]

    _save_recent_blocks(recent_blocks, session_id)
