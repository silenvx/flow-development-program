#!/usr/bin/env python3
"""振り返りスキル実行時にセッションログを自動集計して提供。

Why:
    振り返り時に手動でログを確認するのは手間がかかり、見落としが発生する。
    自動集計することで、客観的データに基づく振り返りを促進する。

What:
    - Skill(reflect)ツール呼び出しを検出
    - セッションのブロック回数をhook-errors.logから集計
    - フロー状態（現在フェーズ等）をstate-{session_id}.jsonから取得
    - recurring-problem-block検出情報を取得
    - systemMessageとしてClaudeに提供

State:
    - reads: .claude/logs/flow/hook-errors.log
    - reads: .claude/logs/flow/state-{session_id}.json

Remarks:
    - 非ブロック型（情報提供のみ）
    - PreToolUse:Skill フック

Changelog:
    - silenvx/dekita#1851: フック追加（振り返り時のログ確認漏れ防止）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))

from common import EXECUTION_LOG_DIR, FLOW_LOG_DIR
from lib.execution import log_hook_execution
from lib.session import create_hook_context, parse_hook_input


def get_block_summary(session_id: str) -> dict:
    """Get block count summary for the session.

    Args:
        session_id: The Claude session ID.

    Returns:
        Dict with block_count and blocks_by_hook.
    """
    errors_log = EXECUTION_LOG_DIR / "hook-errors.log"
    if not errors_log.exists():
        return {"block_count": 0, "blocks_by_hook": {}}

    blocks_by_hook: dict[str, int] = {}
    try:
        with errors_log.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("session_id") == session_id:
                        hook = entry.get("hook", "unknown")
                        blocks_by_hook[hook] = blocks_by_hook.get(hook, 0) + 1
                except json.JSONDecodeError:
                    continue
    except OSError:
        return {"block_count": 0, "blocks_by_hook": {}}

    total = sum(blocks_by_hook.values())
    return {"block_count": total, "blocks_by_hook": blocks_by_hook}


def get_flow_status(session_id: str) -> dict:
    """Get flow status for the session.

    Args:
        session_id: The Claude session ID.

    Returns:
        Dict with flow status information.
    """
    state_file = FLOW_LOG_DIR / f"state-{session_id}.json"
    if not state_file.exists():
        return {"status": "no_state_file"}

    try:
        state = json.loads(state_file.read_text())
        workflows = state.get("workflows", {})
        main_workflow = workflows.get("main", {})
        current_phase = main_workflow.get("current_phase", "unknown")
        return {
            "status": "found",
            "current_phase": current_phase,
            "phase_history_count": len(main_workflow.get("phase_history", [])),
        }
    except (json.JSONDecodeError, OSError):
        return {"status": "error"}


def check_recurring_problems(session_id: str) -> list[str]:
    """Check for recurring problems detected in this session.

    Args:
        session_id: The Claude session ID.

    Returns:
        List of recurring problem sources.
    """
    errors_log = EXECUTION_LOG_DIR / "hook-errors.log"
    if not errors_log.exists():
        return []

    recurring = set()
    try:
        with errors_log.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if (
                        entry.get("session_id") == session_id
                        and entry.get("hook") == "recurring-problem-block"
                    ):
                        details = entry.get("details", {})
                        for problem in details.get("blocking_problems", []):
                            recurring.add(problem.get("source", "unknown"))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []

    return list(recurring)


def format_log_summary(
    block_summary: dict, flow_status: dict, recurring_problems: list[str]
) -> str:
    """Format log summary for display.

    Args:
        block_summary: Block count and breakdown.
        flow_status: Flow state information.
        recurring_problems: List of recurring problem sources.

    Returns:
        Formatted summary string.
    """
    lines = ["📊 **セッションログ自動集計**"]
    lines.append("")

    # Block summary
    total = block_summary["block_count"]
    if total > 0:
        lines.append(f"**ブロック**: {total}件")
        # Top 5 hooks by block count
        by_hook = block_summary["blocks_by_hook"]
        sorted_hooks = sorted(by_hook.items(), key=lambda x: -x[1])[:5]
        hook_summary = ", ".join(f"{h}: {c}" for h, c in sorted_hooks)
        lines.append(f"  - {hook_summary}")
    else:
        lines.append("**ブロック**: 0件")

    # Recurring problems
    if recurring_problems:
        lines.append(f"**recurring-problem-block検出**: {', '.join(recurring_problems)}")

    # Flow status
    if flow_status["status"] == "found":
        phase = flow_status.get("current_phase", "unknown")
        lines.append(f"**現在フェーズ**: {phase}")

    lines.append("")
    lines.append("💡 上記データを振り返りに活用してください。")

    return "\n".join(lines)


def main():
    """PreToolUse:Skill hook for reflection log collection."""
    result = {"continue": True}

    try:
        input_data = parse_hook_input()

        ctx = create_hook_context(input_data)
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Only process Skill tool
        if tool_name != "Skill":
            print(json.dumps(result))
            return

        # Check if this is a reflect skill
        skill_name = tool_input.get("skill", "")
        if skill_name not in ("reflect", "reflection"):
            print(json.dumps(result))
            return

        # Get session ID
        session_id = ctx.get_session_id()
        if not session_id:
            log_hook_execution("reflection-log-collector", "approve", "No session ID available")
            print(json.dumps(result))
            return

        # Collect log data
        block_summary = get_block_summary(session_id)
        flow_status = get_flow_status(session_id)
        recurring_problems = check_recurring_problems(session_id)

        # Format and add to systemMessage
        summary = format_log_summary(block_summary, flow_status, recurring_problems)
        result["systemMessage"] = summary

        log_hook_execution(
            "reflection-log-collector",
            "approve",
            f"Collected logs: {block_summary['block_count']} blocks",
        )

    except Exception as e:
        log_hook_execution("reflection-log-collector", "approve", f"Error: {e}")
        print(f"[reflection-log-collector] Error: {e}", file=sys.stderr)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
