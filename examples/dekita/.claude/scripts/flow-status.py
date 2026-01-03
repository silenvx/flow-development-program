#!/usr/bin/env python3
"""セッション内のフロー進捗状況を表示する。

Why:
    現在のセッションで進行中・未完了のフローを把握し、
    作業の継続性を確保するため。

What:
    - get_all_flows(): 全フローの進捗を取得
    - get_incomplete_flows(): 未完了フローを取得
    - display_status(): 進捗状況を表示

State:
    - reads: .claude/logs/session/*/flow-progress-*.jsonl

Remarks:
    - --all で全フローを表示
    - --json でJSON形式出力
    - デフォルトは未完了フローのみ表示

Changelog:
    - silenvx/dekita#1500: フロー進捗表示機能を追加
    - silenvx/dekita#2496: get_claude_session_id削除に対応
"""

import argparse
import json
import sys
from pathlib import Path

# Add hooks directory to path for imports
hooks_dir = Path(__file__).parent.parent / "hooks"
if str(hooks_dir) not in sys.path:
    sys.path.insert(0, str(hooks_dir))

import os

from common import (
    _parse_flow_progress_log,
    get_incomplete_flows,
)
from flow_definitions import get_flow_definition


def _get_session_id_fallback() -> str:
    """Get session ID using PPID fallback.

    Issue #2496: Replaces get_claude_session_id().
    """
    return f"ppid-{os.getppid()}"


def get_all_flows() -> list[dict]:
    """Get all flows in the current session (complete and incomplete)."""
    session_id = _get_session_id_fallback()

    # Use shared parsing function from common.py
    # Issue #1159: Now returns 4 values including completed_flows set
    flow_instances, completed_steps, step_counts, completed_flows = _parse_flow_progress_log(
        session_id
    )

    # Build list of all flows
    flows = []
    for instance_id, started_entry in flow_instances.items():
        flow_id = started_entry.get("flow_id")
        expected = started_entry.get("expected_steps", [])
        completed = completed_steps.get(instance_id, [])
        pending = [s for s in expected if s not in completed]

        # Issue #1159: Flow is complete if:
        #   1) flow_completed event exists, OR
        #   2) completion_step is defined and that step is completed, OR
        #   3) all steps are completed (when no completion_step is defined)
        if instance_id in completed_flows:
            is_complete = True
        else:
            is_complete = False
            if flow_id:  # Check flow_id is not None before calling get_flow_definition
                flow_def = get_flow_definition(flow_id)
                if flow_def and flow_def.completion_step:
                    is_complete = flow_def.completion_step in completed
            if not is_complete:
                is_complete = len(pending) == 0

        flows.append(
            {
                "flow_id": started_entry.get("flow_id"),
                "flow_name": started_entry.get("flow_name"),
                "flow_instance_id": instance_id,
                "expected_steps": expected,
                "completed_steps": completed,
                "pending_steps": pending,
                "step_counts": step_counts.get(instance_id, {}),
                "is_complete": is_complete,
                "context": started_entry.get("context", {}),
                "started_at": started_entry.get("timestamp"),
            }
        )

    return flows


def format_flow_display(flow: dict) -> str:
    """Format a single flow for terminal display."""
    flow_id = flow.get("flow_id", "unknown")
    flow_name = flow.get("flow_name", flow_id)
    context = flow.get("context", {})
    completed = flow.get("completed_steps", [])
    pending = flow.get("pending_steps", [])
    step_counts = flow.get("step_counts", {})
    is_complete = flow.get("is_complete", False)

    # Format context
    context_str = ""
    if issue_num := context.get("issue_number"):
        context_str = f" (Issue #{issue_num})"
    elif context:
        context_parts = [f"{k}: {v}" for k, v in context.items()]
        context_str = f" ({', '.join(context_parts)})"

    # Get flow definition for step names
    flow_def = get_flow_definition(flow_id)

    # Build step lines
    step_lines = []

    for step_id in completed:
        step_name = step_id
        if flow_def:
            step = flow_def.get_step(step_id)
            if step:
                step_name = step.name
        count = step_counts.get(step_id, 1)
        count_str = f" ({count}回)" if count > 1 else ""
        step_lines.append(f"✅ {step_name}{count_str}")

    for i, step_id in enumerate(pending):
        step_name = step_id
        if flow_def:
            step = flow_def.get_step(step_id)
            if step:
                step_name = step.name
        if i == 0:
            step_lines.append(f"⏳ {step_name} ← 次のステップ")
        else:
            step_lines.append(f"⬜ {step_name}")

    # Calculate box width
    max_content_len = max((len(line) for line in step_lines), default=0)
    box_width = min(60, max(30, max_content_len + 4))

    # Build output
    status_icon = "✅" if is_complete else "🔄"
    lines = [f"\n{status_icon} 📋 {flow_name}{context_str}"]
    lines.append("┌" + "─" * box_width + "┐")
    for step_line in step_lines:
        padded = step_line.ljust(box_width - 2)
        lines.append(f"│ {padded} │")
    lines.append("└" + "─" * box_width + "┘")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Display flow status")
    parser.add_argument("--all", action="store_true", help="Show all flows")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    args = parser.parse_args()

    if args.all:
        flows = get_all_flows()
    else:
        flows = get_incomplete_flows()

    if args.json:
        print(json.dumps(flows, indent=2, ensure_ascii=False))
        return

    if not flows:
        print("📭 現在のセッションにフローはありません。")
        return

    print("=" * 50)
    print("🔄 フロー進捗状況")
    print("=" * 50)

    for flow in flows:
        print(format_flow_display(flow))

    # Summary
    complete = sum(1 for f in flows if f.get("is_complete", False))
    incomplete = len(flows) - complete
    print("\n" + "=" * 50)
    print(f"📊 サマリー: 完了 {complete} / 進行中 {incomplete} / 合計 {len(flows)}")
    print("=" * 50)


if __name__ == "__main__":
    main()
