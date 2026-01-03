#!/usr/bin/env python3
"""フック実行ログの記録とログファイルローテーションを管理する。

Why:
    フック実行の追跡・分析のため、構造化されたログ記録が必要。

What:
    - log_hook_execution(): フック実行をJSONL形式で記録
    - rotate_log_if_needed(): ログファイルのサイズベースローテーション
    - compress_rotated_logs(): ローテート済みログのgzip圧縮

State:
    - writes: .claude/logs/execution/hook-execution-{session}.jsonl
    - writes: .claude/logs/execution/hook-errors.log
    - writes: .claude/logs/execution/hook-warnings.log

Remarks:
    - セッション固有ファイル形式でログを出力
    - エラー/警告はレベル別ファイルにも出力
    - ブロックパターン追跡機能を統合

Changelog:
    - silenvx/dekita#1367: ログレベル分離とエラーコンテキストを追加
    - silenvx/dekita#1758: common.pyから分離
    - silenvx/dekita#1994: セッション固有ファイル形式に変更
    - silenvx/dekita#2456: HookContext対応（session_idパラメータ追加）
    - silenvx/dekita#2496: グローバル状態を完全削除
    - silenvx/dekita#2529: ppidフォールバック完全廃止
"""

import gzip
import os
from pathlib import Path
from typing import Any

from lib.block_patterns import check_block_resolution, record_block
from lib.git import get_current_branch
from lib.logging import (
    LOG_LEVEL_ERROR,
    LOG_LEVEL_INFO,
    LOG_LEVEL_WARN,
    get_error_context_manager,
    get_log_level,
    log_to_level_file,
    log_to_session_file,
)
from lib.timestamp import get_local_timestamp


def _get_project_dir() -> Path:
    """Get project directory from environment or cwd."""
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.cwd()


def _get_execution_log_dir() -> Path:
    """Get execution log directory path."""
    project_dir = _get_project_dir()
    return project_dir / ".claude" / "logs" / "execution"


def _get_hook_execution_log() -> Path:
    """Get hook execution log file path."""
    return _get_execution_log_dir() / "hook-execution.log"


def rotate_log_if_needed(log_file: Path, max_size: int, max_files: int) -> bool:
    """Rotate log file if it reaches or exceeds the maximum size.

    Implements numbered rotation: log -> log.1 -> log.2 -> ... -> log.N
    The oldest file (log.N) is deleted when rotation occurs.

    Note:
        This function handles race conditions gracefully. When multiple
        processes attempt rotation simultaneously, OSError from file
        operations is caught and the function returns False. This is
        acceptable for logging - the worst case is a single rotation
        being skipped, and the next call will retry.

    Args:
        log_file: Path to the log file to rotate.
        max_size: Maximum file size in bytes before rotation (inclusive).
        max_files: Maximum number of rotated files to keep.

    Returns:
        True if rotation was performed, False otherwise.
    """
    try:
        if not log_file.exists():
            return False

        # Check file size
        file_size = log_file.stat().st_size
        if file_size < max_size:
            return False

        # Rotate files: log.5 -> delete, log.4 -> log.5, ..., log -> log.1
        for i in range(max_files, 0, -1):
            rotated = log_file.with_suffix(f".log.{i}")
            if i == max_files:
                # Delete the oldest file
                if rotated.exists():
                    rotated.unlink()
            else:
                # Rename to next number
                next_rotated = log_file.with_suffix(f".log.{i + 1}")
                if rotated.exists():
                    rotated.rename(next_rotated)

        # Rename current log to .log.1
        log_file.rename(log_file.with_suffix(".log.1"))
        return True

    except OSError:
        # Don't fail if rotation fails, just continue logging
        return False


def compress_rotated_logs(log_dir: Path) -> int:
    """Compress rotated log files (.log.1, .log.2, etc.) to gzip format.

    Scans the given directory for rotated log files and compresses them.
    Already compressed files (.gz) are skipped.

    Args:
        log_dir: Directory containing rotated log files.

    Returns:
        Number of files successfully compressed.
    """
    compressed_count = 0

    try:
        if not log_dir.exists():
            return 0

        # Find all rotated log files (*.log.1, *.log.2, etc.)
        # Use [0-9]* to match both single and multi-digit rotation numbers
        for log_file in log_dir.glob("*.log.[0-9]*"):
            # Skip already compressed files
            if log_file.suffix == ".gz":
                continue
            gz_file = log_file.with_suffix(log_file.suffix + ".gz")

            # Skip if already compressed
            if gz_file.exists():
                continue

            try:
                # Stream compress to avoid loading entire file into memory
                with log_file.open("rb") as f_in, gzip.open(gz_file, "wb") as f_out:
                    while chunk := f_in.read(65536):  # 64KB chunks
                        f_out.write(chunk)

                # Remove original after successful compression
                log_file.unlink()
                compressed_count += 1

            except OSError:
                # Clean up partial .gz file on error to avoid orphaned files
                try:
                    gz_file.unlink(missing_ok=True)
                except OSError:
                    pass  # Ignore cleanup failure - partial .gz may remain
                continue

    except OSError:
        # Directory access error - return what we have
        pass

    return compressed_count


def log_hook_execution(
    hook_name: str,
    decision: str,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    *,
    execution_log_dir: Path | None = None,
    session_id: str | None = None,
) -> None:
    """Log hook execution to centralized log files.

    Records hook execution in JSON Lines format for later analysis.
    This helps track:
    - Which hooks are being triggered
    - How often hooks block vs approve
    - Common block reasons
    - Session and branch context for grouping
    - Execution time for performance analysis (Issue #1282)

    Issue #1367: Extended to support log level separation and error context.
    - Writes to main hook-execution.log (all levels, backward compatible)
    - Writes to level-specific files (hook-errors.log, hook-warnings.log)
    - Captures error context with ring buffer for debugging

    Issue #2456: HookContext対応（段階的移行）
    - session_idパラメータを追加（オプショナル）
    Issue #2496: グローバル状態を完全削除
    - session_idがNoneの場合はPPIDベースのフォールバックを使用

    Args:
        hook_name: Name of the hook (e.g., "merge-check", "cwd-check")
        decision: "approve" or "block"
        reason: Reason for the decision (especially for blocks)
        details: Additional details (tool_name, command, etc.)
        duration_ms: Hook execution time in milliseconds (optional)
        execution_log_dir: Optional log directory (for testing). Uses default if None.
        session_id: Session ID from HookContext. If None, session-specific logging is skipped.
    """
    if execution_log_dir is None:
        execution_log_dir = _get_execution_log_dir()

    # Issue #2529: ppidフォールバック完全廃止
    # session_idがNoneの場合、セッション固有のログをスキップ（警告なし）
    # 警告はmake_block_result()のctx=Noneチェックで行う

    try:
        execution_log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Skip logging if directory can't be created (e.g., in tests with mock paths)
        return

    # Build log entry with context for analysis
    log_entry: dict[str, Any] = {
        "timestamp": get_local_timestamp(),
        "session_id": session_id,
        "hook": hook_name,
        "decision": decision,
    }

    # Add branch context if available (useful for grouping by feature work)
    branch = get_current_branch()
    if branch:
        log_entry["branch"] = branch

    if reason:
        log_entry["reason"] = reason
    if details:
        log_entry["details"] = details
    if duration_ms is not None:
        log_entry["duration_ms"] = duration_ms

    # Determine log level from decision (Issue #1367)
    log_level = get_log_level(decision)

    # Issue #1994: Write to session-specific file instead of single rotated file
    # This eliminates the need for log rotation and makes per-session analysis easier
    if session_id:
        log_to_session_file(execution_log_dir, "hook-execution", session_id, log_entry)

    # Write to level-specific log files (Issue #1367)
    if log_level in (LOG_LEVEL_ERROR, LOG_LEVEL_WARN):
        log_to_level_file(execution_log_dir, log_entry, log_level)
    elif log_level not in (LOG_LEVEL_INFO,):
        # DEBUG level (controlled by env var in log_to_level_file)
        log_to_level_file(execution_log_dir, log_entry, log_level)

    # Error context management (Issue #1367)
    if session_id:
        error_context_manager = get_error_context_manager()
        error_context_manager.add_entry(session_id, log_entry)

        # Trigger error context capture on block
        if log_level == LOG_LEVEL_ERROR:
            error_context_manager.on_error(session_id, log_entry, execution_log_dir)

    # Block pattern tracking (Issue #1361)
    # Track block→success patterns for learning and analysis
    metrics_log_dir = execution_log_dir.parent / "metrics"
    if decision == "block":
        record_block(hook_name, reason, details, session_id, metrics_log_dir)
    elif decision == "approve":
        check_block_resolution(hook_name, details, session_id, metrics_log_dir)
