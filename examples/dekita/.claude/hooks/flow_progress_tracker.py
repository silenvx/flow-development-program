#!/usr/bin/env python3
"""フローステップの完了を追跡する。

Why:
    開発ワークフローの各ステップ（worktree作成、コミット、PR作成等）が
    実際に実行されたことを記録する必要がある。コマンドパターンマッチで
    自動的にステップ完了を検出し、flow-effect-verifierが判断に使用する。

What:
    - Bashコマンドの実行を監視
    - flow_definitions.pyのパターンとマッチング
    - マッチした場合はステップを完了としてマーク
    - ステップ順序の妥当性を検証（警告のみ）

State:
    writes: .claude/state/flow-progress.jsonl

Remarks:
    - 終了コードが0のコマンドのみ対象
    - 順序違反は警告するが、ステップは完了としてマーク（回復可能）
"""

import json

from common import complete_flow_step, get_incomplete_flows
from flow_definitions import get_flow_definition, validate_step_order
from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.session import parse_hook_input


def get_active_flows() -> list[dict]:
    """Get active (incomplete) flows in current session with flow definitions.

    Uses get_incomplete_flows() from common.py and adds flow_definition objects
    for context-aware pattern matching.

    Returns:
        List of flow entries with their context, completed steps, and flow definitions.
    """
    incomplete = get_incomplete_flows()

    active: list[dict] = []
    for flow in incomplete:
        flow_id = flow.get("flow_id")
        flow_def = get_flow_definition(flow_id)
        if not flow_def:
            continue

        active.append(
            {
                "flow_instance_id": flow.get("flow_instance_id"),
                "flow_id": flow_id,
                "flow_definition": flow_def,
                "context": flow.get("context", {}),
                "pending_steps": flow.get("pending_steps", []),
                "completed_steps": flow.get("completed_steps", []),
            }
        )

    return active


def main() -> None:
    """Main entry point for the hook."""
    input_data = parse_hook_input()

    # Only process Bash tool
    tool_name = input_data.get("tool_name")
    if tool_name != "Bash":
        return

    # Get the command that was executed
    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")
    if not command:
        return

    # Only mark steps complete if command succeeded (exit_code == 0)
    tool_result = get_tool_result(input_data) or {}
    exit_code = tool_result.get("exit_code", 0)
    if exit_code != 0:
        return  # Don't mark steps complete for failed commands

    # Get active flows
    active_flows = get_active_flows()
    if not active_flows:
        return

    # Check each active flow for matching steps (with context-aware matching)
    matched_steps: list[tuple[str, str, str]] = []  # (instance_id, step_id, step_name)
    order_warnings: list[str] = []

    for flow in active_flows:
        instance_id = flow["flow_instance_id"]
        flow_id = flow["flow_id"]
        flow_def = flow["flow_definition"]
        context = flow["context"]
        completed = flow["completed_steps"]

        for step_id in flow["pending_steps"]:
            # Use context-aware pattern matching from flow_definitions.py
            if flow_def.matches_step(step_id, command, context):
                # Validate step order
                is_valid, error_msg = validate_step_order(flow_id, completed, step_id)

                if not is_valid:
                    order_warnings.append(f"順序警告: {error_msg}")
                    # Still mark the step as complete, but warn about order
                    # This allows recovery from out-of-order execution

                step = flow_def.get_step(step_id)
                step_name = step.name if step else step_id
                matched_steps.append((instance_id, step_id, step_name))
                complete_flow_step(instance_id, step_id, flow_id)
                # Keep local completed list in sync for subsequent validations
                completed.append(step_id)

    # Output notification if steps were completed
    if matched_steps or order_warnings:
        messages = []
        if matched_steps:
            step_names = [name for _, _, name in matched_steps]
            messages.append(f"ステップ完了: {', '.join(step_names)}")
            # Log step completions
            log_hook_execution(
                "flow-progress-tracker",
                "approve",
                f"Steps completed: {', '.join(step_names)}",
                {
                    "matched_steps": [
                        {"instance_id": inst, "step_id": sid, "step_name": name}
                        for inst, sid, name in matched_steps
                    ]
                },
            )
        messages.extend(order_warnings)

        result = {
            "continue": True,
            "systemMessage": "[flow-progress-tracker] " + " | ".join(messages),
        }
        print(json.dumps(result))


if __name__ == "__main__":
    main()
