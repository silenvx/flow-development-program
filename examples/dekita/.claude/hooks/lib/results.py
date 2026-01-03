#!/usr/bin/env python3
"""フック結果（block/approve）の生成ユーティリティを提供する。

Why:
    フック結果の形式を統一し、ブロック時のメッセージ表示・
    ログ記録・連続ブロック検出を一元化する。

What:
    - make_block_result(): ブロック結果生成（ログ自動記録、連続ブロック警告）
    - make_approve_result(): 承認結果生成
    - print_continue_and_log_skip(): 早期リターン用ヘルパー（PreToolUse用）
    - print_approve_and_log_skip(): 早期リターン用ヘルパー（Stop用）
    - check_skip_env(): SKIP_*環境変数チェック

Remarks:
    - make_block_result()はブロックを自動的にログ記録
    - 60秒以内に同一フックで2回以上ブロック時は警告追加
    - CONTINUATION_HINTでブロック後の継続を促進

Changelog:
    - silenvx/dekita#725: CONTINUATION_HINT追加
    - silenvx/dekita#938: stderrへのブロック理由出力
    - silenvx/dekita#1260: SKIP環境変数ログ記録
    - silenvx/dekita#1279: systemMessageフィールド追加
    - silenvx/dekita#1607: print_xxx_and_log_skip()追加
    - silenvx/dekita#1758: common.pyから分離
    - silenvx/dekita#2023: ブロック自動ログ記録
    - silenvx/dekita#2401: 連続ブロック検出と警告
    - silenvx/dekita#2456: HookContext対応
    - silenvx/dekita#2529: ppidフォールバック廃止
    - silenvx/dekita#2604: ctx引数追加
"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lib.constants import CONTINUATION_HINT
from lib.strings import is_skip_env_enabled

if TYPE_CHECKING:
    from lib.session import HookContext

# Repeated block detection settings
REPEATED_BLOCK_WINDOW_SECONDS = 60  # Look back window for recent blocks
REPEATED_BLOCK_THRESHOLD = 2  # Number of previous blocks to trigger warning


def _get_execution_log_dir() -> Path:
    """Get execution log directory path."""
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        return Path(env_dir) / ".claude" / "logs" / "execution"
    return Path.cwd() / ".claude" / "logs" / "execution"


def _count_recent_blocks(hook_name: str, session_id: str | None) -> int:
    """Count recent blocks from the same hook within the time window.

    Issue #2401: Detect repeated blocks to warn about not reading messages.

    Args:
        hook_name: Name of the hook to count blocks for
        session_id: Claude session identifier

    Returns:
        Number of blocks from the same hook within REPEATED_BLOCK_WINDOW_SECONDS
    """
    if not session_id:
        return 0

    try:
        # Import here to avoid circular dependency
        from lib.logging import read_session_log_entries

        log_dir = _get_execution_log_dir()
        entries = read_session_log_entries(log_dir, "hook-execution", session_id)

        if not entries:
            return 0

        # Get current time for age calculation
        now = datetime.now(UTC)
        count = 0

        for entry in entries:
            # Only count blocks from the same hook
            if entry.get("hook") != hook_name:
                continue
            if entry.get("decision") != "block":
                continue

            # Parse timestamp and check if within window
            timestamp_str = entry.get("timestamp", "")
            if not timestamp_str:
                continue

            try:
                # Handle both timezone-aware and naive timestamps
                entry_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=UTC)

                age_seconds = (now - entry_time).total_seconds()
                if age_seconds <= REPEATED_BLOCK_WINDOW_SECONDS:
                    count += 1
            except (ValueError, TypeError):
                continue

        return count

    except Exception:
        # Fail silently - don't break hook execution for logging errors
        return 0


def make_block_result(
    hook_name: str,
    reason: str,
    ctx: "HookContext | None" = None,
) -> dict[str, str]:
    """Create a block result with hook name prefix and continuation hint.

    This ensures the hook name is always visible in block messages,
    making it clear which hook caused the block.

    Also includes a continuation hint to remind Claude to continue with
    tool calls after a block, preventing premature session termination.
    Issue #725: ブロック後のテキストのみ応答による処理停止を防止

    Issue #938: ブロック時に詳細情報をstderrに出力し、ユーザーに可視化
    - Claude Codeは`reason`フィールドをユーザーに表示しない
    - stderrに出力することで、ターミナル上でブロック理由が確認できる
    - 理由の最初の行のみを出力（長いメッセージは省略）

    Issue #1279: systemMessageフィールドでユーザーにメッセージを表示
    - reasonフィールドはClaudeに表示されるが、ユーザーには表示されない
    - systemMessageフィールドを追加することでユーザーにも確実に表示される

    Issue #2023: ブロックをセッションログに自動記録
    - 全てのブロックがセッション毎のログファイルに記録される
    - 各フックで個別にlog_hook_execution()を呼ぶ必要がなくなる

    Issue #2401: 連続ブロック検出と警告強化
    - 同一フックで60秒以内に2回以上ブロックされた場合、警告を追加
    - ブロックメッセージを読まずに再試行する問題を防止

    Issue #2456: HookContext対応（段階的移行）
    - ctxパラメータを追加（オプショナル）
    - 段階的にhookを移行し、最終的にctxを必須化

    Issue #2529: ppidフォールバック完全廃止
    - ctxがNoneの場合はsession_id=Noneとなり、セッション固有のログはスキップ

    Args:
        hook_name: Name of the hook (e.g., "merge-check", "ci-wait-check")
        reason: The block reason message
        ctx: HookContext instance (optional, for gradual migration)

    Returns:
        A dict with the following keys:
        - "decision": "block"
        - "reason": Hook-prefixed reason with continuation hint (for Claude)
        - "systemMessage": Short summary for user display

        Note: Unlike make_approve_result, this includes a "reason" key.
    """
    # 遅延インポートで循環参照を回避

    from lib.execution import log_hook_execution

    # Issue #2529: ppidフォールバック完全廃止
    # ctxがNoneの場合はsession_id=Noneとなり、セッション固有のログはスキップ
    # 警告は出力しない（テストへの影響回避、移行完了後は不要）
    session_id = ctx.get_session_id() if ctx is not None else None
    recent_block_count = _count_recent_blocks(hook_name, session_id)

    # Issue #2023: ブロックを自動的にセッションログに記録
    # Note: This must be called AFTER _count_recent_blocks to avoid off-by-one
    # Issue #2456: session_idを渡して正しいセッションログに記録
    log_hook_execution(hook_name, "block", reason, session_id=session_id)

    # Build enhanced warning if repeated blocks detected
    repeated_warning = ""
    repeated_warning_short = ""
    if recent_block_count >= REPEATED_BLOCK_THRESHOLD:
        # Include current block in the displayed count
        total_block_count = recent_block_count + 1
        repeated_warning = (
            f"\n\n⚠️ 【警告】このフックで{total_block_count}回連続ブロック中！\n"
            "上記のメッセージを必ず読み、指示に従ってください。\n"
            "同じ操作を繰り返しても解決しません。\n"
            "AGENTS.md: 「同一フックで3回以上ブロックされることは禁止」"
        )
        repeated_warning_short = f" (⚠️ {total_block_count}回連続ブロック - メッセージを読んで!)"

    # ブロック理由の要約を作成（ユーザーに表示される）
    # 最初の行のみ抽出し、長すぎる場合は省略
    first_line = reason.split("\n")[0].strip() if reason else ""
    if not first_line:
        first_line = "ブロックされました"
    elif len(first_line) > 100:
        first_line = first_line[:97] + "..."

    # stderrへの出力はフォールバックとして維持（Issue #938）
    # systemMessageが無視される環境でもユーザーがブロック理由を確認できる
    print(f"❌ {hook_name}: {first_line}{repeated_warning_short}", file=sys.stderr)

    return {
        "decision": "block",
        "reason": f"[{hook_name}] {reason}{repeated_warning}{CONTINUATION_HINT}",
        "systemMessage": f"❌ {hook_name}: {first_line}{repeated_warning_short}",
    }


def make_approve_result(hook_name: str, message: str | None = None) -> dict[str, str]:
    """Create an approve result with optional systemMessage.

    Args:
        hook_name: Name of the hook (e.g., "merge-check", "ci-wait-check")
        message: Optional message to include in systemMessage.
                 If None, a simple "OK" message is used.

    Returns:
        A dict with the following keys:
        - "decision": "approve"
        - "systemMessage": Status message for user display

        Note: Unlike make_block_result, this does NOT include a "reason" key.
    """
    if message:
        return {
            "decision": "approve",
            "systemMessage": f"✅ {hook_name}: {message}",
        }
    return {
        "decision": "approve",
        "systemMessage": f"✅ {hook_name}: OK",
    }


def print_continue_and_log_skip(
    hook_name: str,
    reason: str,
    log_hook_execution_func: Any = None,
    ctx: "HookContext | None" = None,
) -> None:
    """Print continue result and log skip event for early returns.

    Issue #1607: Unified helper for fail-open early returns.
    Issue #2604: Added ctx parameter for session_id logging.

    Use this for early returns when the hook should allow the operation
    but we want to log the reason for debugging purposes.

    After calling this, the hook should return immediately.

    Args:
        hook_name: Name of the hook (e.g., "worktree-removal-check")
        reason: Reason for skipping (will be logged)
        log_hook_execution_func: Optional log function. If None, imports from lib.execution.
        ctx: HookContext instance (optional, for session_id logging)

    Example:
        if not some_condition:
            print_continue_and_log_skip("my-hook", "condition not met", ctx=ctx)
            return
    """
    if log_hook_execution_func is None:
        from lib.execution import log_hook_execution

        log_hook_execution_func = log_hook_execution

    # Issue #2604: Pass session_id to log_hook_execution
    session_id = ctx.get_session_id() if ctx is not None else None
    log_hook_execution_func(hook_name, "skip", reason, session_id=session_id)
    print(json.dumps({"continue": True}))


def print_approve_and_log_skip(
    hook_name: str,
    reason: str,
    log_hook_execution_func: Any = None,
    ctx: "HookContext | None" = None,
) -> None:
    """Print approve result and log skip event for Stop hook early returns.

    Issue #1607: Unified helper for fail-open early returns in Stop hooks.
    Issue #2604: Added ctx parameter for session_id logging.

    Stop hooks must return {"decision": "approve"} (not {"continue": True}).
    Use this for early returns in Stop hooks when the hook should allow
    but we want to log the reason for debugging purposes.

    After calling this, the hook should return immediately.

    Args:
        hook_name: Name of the hook (e.g., "session-end-main-check")
        reason: Reason for skipping (will be logged)
        log_hook_execution_func: Optional log function. If None, imports from lib.execution.
        ctx: HookContext instance (optional, for session_id logging)

    Example:
        if stop_hook_active:
            print_approve_and_log_skip("my-stop-hook", "stop_hook_active", ctx=ctx)
            return
    """
    if log_hook_execution_func is None:
        from lib.execution import log_hook_execution

        log_hook_execution_func = log_hook_execution

    # Issue #2604: Pass session_id to log_hook_execution
    session_id = ctx.get_session_id() if ctx is not None else None
    log_hook_execution_func(hook_name, "skip", reason, session_id=session_id)
    print(json.dumps({"decision": "approve"}))


def check_skip_env(
    hook_name: str,
    env_var: str,
    details: dict[str, Any] | None = None,
    log_hook_execution_func: Any = None,
) -> bool:
    """Check if skip environment variable is set and log if so.

    This function:
    1. Checks if the specified SKIP_* environment variable is enabled
    2. If enabled, logs the skip event to hook-execution.log
    3. Returns True if skip is enabled, False otherwise

    Issue #1260: Record SKIP environment variable usage in logs.

    Args:
        hook_name: Name of the hook (e.g., "planning-enforcement")
        env_var: The environment variable name (e.g., "SKIP_PLAN")
        details: Optional additional details to include in log (e.g., input_context)
        log_hook_execution_func: Optional log function. If None, imports from lib.execution.

    Returns:
        True if skip env is enabled, False otherwise.

    Example::

        if check_skip_env("planning-enforcement", "SKIP_PLAN"):
            return {"decision": "approve"}  # Skip the hook

        # With additional context:
        if check_skip_env("my-hook", "SKIP_MY_HOOK", {"command": cmd}):
            return {"decision": "approve"}
    """
    if log_hook_execution_func is None:
        from lib.execution import log_hook_execution

        log_hook_execution_func = log_hook_execution

    value = os.environ.get(env_var)
    if is_skip_env_enabled(value):
        log_details = {"env_var": env_var, "value": value}
        if details:
            log_details.update(details)
        log_hook_execution_func(
            hook_name=hook_name,
            decision="skip_by_env",
            reason=f"{env_var}={value}",
            details=log_details,
        )
        return True
    return False
