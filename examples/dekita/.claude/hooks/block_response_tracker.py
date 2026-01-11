#!/usr/bin/env python3
"""ブロック後にテキストのみ応答（ツール呼び出しなし）を検知し警告する。

Why:
    AGENTS.mdでは「ブロックは代替アクションを実行せよ」と定めている。
    ブロック後にツール呼び出しがない場合、エージェントループが停止している。

What:
    - セッション終了時にブロックパターンを分析
    - ブロック後にツール呼び出しがないケースを検出
    - 警告メッセージを表示（ブロックはしない）

State:
    - reads: .claude/logs/metrics/block-patterns-{session}.jsonl
    - reads: .claude/logs/execution/hook-execution-{session}.jsonl

Remarks:
    - 分析型フック（ブロックしない、パターン分析のみ）
    - セッション終了時（Stop）に発火
    - SessionStartなど非ツールフックは除外

Changelog:
    - silenvx/dekita#1967: P1改善
    - silenvx/dekita#1973: 非ツールフックの除外
    - silenvx/dekita#2282: セッションID検証によるパストラバーサル対策
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.execution import log_hook_execution
from lib.logging import read_session_log_entries
from lib.session import create_hook_context, parse_hook_input
from lib.session_validation import is_safe_session_id

HOOK_NAME = "block-response-tracker"

# Time window to check for tool calls after a block (seconds)
RESPONSE_CHECK_WINDOW_SECONDS = 120

# Minimum blocks without recovery to trigger warning
MIN_UNRECOVERED_BLOCKS_FOR_WARNING = 1


def get_metrics_log_dir() -> Path:
    """Get metrics log directory."""
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    return project_dir / ".claude" / "logs" / "metrics"


def get_execution_log_dir() -> Path:
    """Get execution log directory."""
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    return project_dir / ".claude" / "logs" / "execution"


def load_block_patterns(session_id: str) -> list[dict]:
    """Load block patterns for this session.

    Returns list of block events from the session's block-patterns log.
    """
    # Validate session_id to prevent path traversal (Issue #2282)
    if not is_safe_session_id(session_id):
        return []

    log_file = get_metrics_log_dir() / f"block-patterns-{session_id}.jsonl"

    if not log_file.exists():
        return []

    blocks = []
    try:
        with log_file.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "block":
                        blocks.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        # File may not exist if no blocks occurred; safe to ignore
        pass

    return blocks


def load_recovery_events(session_id: str) -> set[str]:
    """Load block IDs that have recovery events.

    Returns set of block_ids that were resolved or had recovery actions.
    """
    # Validate session_id to prevent path traversal (Issue #2282)
    if not is_safe_session_id(session_id):
        return set()

    log_file = get_metrics_log_dir() / f"block-patterns-{session_id}.jsonl"

    if not log_file.exists():
        return set()

    recovered_ids = set()
    try:
        with log_file.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entry_type = entry.get("type", "")
                    if entry_type in ("block_resolved", "block_recovery", "block_expired"):
                        block_id = entry.get("block_id")
                        if block_id:
                            recovered_ids.add(block_id)
                except json.JSONDecodeError:
                    continue
    except OSError:
        # File may not exist if no blocks occurred; safe to ignore
        pass

    return recovered_ids


# Hooks that are not tool calls (phase transitions, state management, etc.)
# These should be excluded when counting tool calls after a block.
NON_TOOL_HOOKS = frozenset(
    {
        "flow-state-updater",
        "block-response-tracker",
        "flow-verifier",
        "session-start",
        "session-end",
    }
)


def load_tool_calls_from_execution_log(session_id: str) -> list[dict]:
    """Load tool calls from session-specific hook-execution log.

    Returns list of tool call events with timestamps.
    Excludes non-tool hooks (phase transitions, state management, etc.).
    """
    # Read from session-specific log file
    entries = read_session_log_entries(get_execution_log_dir(), "hook-execution", session_id)

    tool_calls = []
    for entry in entries:
        # Include tool call approvals (not blocks)
        if entry.get("decision") == "approve":
            # Exclude non-tool hooks (Issue #1973)
            hook_name = entry.get("hook", "")
            if hook_name not in NON_TOOL_HOOKS:
                tool_calls.append(entry)

    return tool_calls


def parse_timestamp(ts_str: str) -> datetime | None:
    """Parse timestamp string to datetime."""
    # Try multiple formats
    formats = [
        "%Y-%m-%dT%H:%M:%S.%f%z",  # ISO format with timezone
        "%Y-%m-%dT%H:%M:%S%z",  # ISO format without microseconds
        "%Y-%m-%d %H:%M:%S",  # Simple format
    ]

    for fmt in formats:
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue

    return None


def has_tool_call_after_block(
    block: dict, tool_calls: list[dict], window_seconds: int = RESPONSE_CHECK_WINDOW_SECONDS
) -> bool:
    """Check if there was a tool call after the block within the window.

    Args:
        block: The block event dict
        tool_calls: List of tool call events
        window_seconds: Time window to check (seconds)

    Returns:
        True if a tool call was made after the block within the window.
    """
    block_ts_str = block.get("timestamp", "")
    block_ts = parse_timestamp(block_ts_str)

    if not block_ts:
        # Can't parse timestamp, assume tool calls happened
        return True

    for call in tool_calls:
        call_ts_str = call.get("timestamp", "")
        call_ts = parse_timestamp(call_ts_str)

        if not call_ts:
            continue

        # Check if call is after the block
        # Make timestamps comparable by removing timezone if one is naive
        try:
            # 正規化用のローカル変数を用意し、すべてのケースで一貫した比較を行う
            norm_block_ts = block_ts
            norm_call_ts = call_ts

            if norm_block_ts.tzinfo is None and norm_call_ts.tzinfo is not None:
                # block_ts が naive の場合、call_ts の tzinfo を落として両方 naive にする
                norm_call_ts = norm_call_ts.replace(tzinfo=None)
            elif norm_block_ts.tzinfo is not None and norm_call_ts.tzinfo is None:
                # call_ts が naive の場合、block_ts の tzinfo を落として両方 naive にする
                norm_block_ts = norm_block_ts.replace(tzinfo=None)

            if norm_call_ts > norm_block_ts:
                elapsed = (norm_call_ts - norm_block_ts).total_seconds()
                if elapsed <= window_seconds:
                    return True
        except TypeError:
            # Timezone comparison issue, assume tool calls happened
            return True

    return False


def analyze_block_responses(session_id: str) -> dict:
    """Analyze block response patterns for the session.

    Returns analysis results with:
    - total_blocks: Total number of blocks
    - recovered_blocks: Number of blocks with recovery events
    - unrecovered_blocks: List of blocks without recovery
    - text_only_blocks: Blocks that appear to have no subsequent tool calls
    """
    blocks = load_block_patterns(session_id)
    recovered_ids = load_recovery_events(session_id)
    tool_calls = load_tool_calls_from_execution_log(session_id)

    unrecovered = []
    text_only = []

    for block in blocks:
        block_id = block.get("block_id")

        # Check if this block was recovered
        if block_id in recovered_ids:
            continue

        unrecovered.append(block)

        # Check if there were tool calls after this block
        if not has_tool_call_after_block(block, tool_calls):
            text_only.append(block)

    return {
        "total_blocks": len(blocks),
        "recovered_blocks": len(recovered_ids),
        "unrecovered_blocks": unrecovered,
        "text_only_blocks": text_only,
    }


def format_warning_message(analysis: dict) -> str | None:
    """Format warning message based on analysis results.

    Returns warning message string or None if no warning needed.
    """
    text_only = analysis.get("text_only_blocks", [])
    unrecovered = analysis.get("unrecovered_blocks", [])

    if len(unrecovered) < MIN_UNRECOVERED_BLOCKS_FOR_WARNING:
        return None

    lines = ["[block-response-tracker] ブロック後の行動分析結果:"]
    lines.append("")

    if text_only:
        lines.append(f"⚠️ **{len(text_only)}件のブロック**後にツール呼び出しがありませんでした。")
        lines.append("")
        lines.append("AGENTS.mdより:")
        lines.append(
            "> ブロックは「やり方を変えろ」という指示。停止ではなく、代替アクションを実行する。"
        )
        lines.append("")

        for block in text_only[:3]:  # Show max 3
            hook = block.get("hook", "unknown")
            preview = (block.get("command_preview") or "N/A")[:50]
            lines.append(f"  - {hook}: `{preview}...`")

        if len(text_only) > 3:
            lines.append(f"  - ...他 {len(text_only) - 3} 件")

    elif unrecovered:
        lines.append(f"📊 {len(unrecovered)}件のブロックが未解決のままです（セッション終了時点）。")
        lines.append("")
        lines.append("これは正常な場合もありますが、代替アクションを検討してください。")

    return "\n".join(lines)


def main() -> None:
    """Main entry point for the hook."""
    # Parse input and create hook context
    input_data = parse_hook_input()
    ctx = create_hook_context(input_data)

    # Get session ID
    session_id = ctx.get_session_id()

    if not session_id:
        log_hook_execution(HOOK_NAME, "approve", "No session ID available")
        print(json.dumps({"continue": True}))
        return

    # Analyze block response patterns
    analysis = analyze_block_responses(session_id)

    # Log analysis results
    log_hook_execution(
        HOOK_NAME,
        "approve",
        f"Blocks: {analysis['total_blocks']}, Recovered: {analysis['recovered_blocks']}, "
        f"Unrecovered: {len(analysis['unrecovered_blocks'])}, Text-only: {len(analysis['text_only_blocks'])}",
        {
            "total_blocks": analysis["total_blocks"],
            "recovered_blocks": analysis["recovered_blocks"],
            "unrecovered_count": len(analysis["unrecovered_blocks"]),
            "text_only_count": len(analysis["text_only_blocks"]),
        },
    )

    # Format warning if needed
    warning = format_warning_message(analysis)

    if warning:
        print(json.dumps({"continue": True, "message": warning}))
    else:
        print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
