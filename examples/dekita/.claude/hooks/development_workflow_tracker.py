#!/usr/bin/env python3
"""開発ワークフローの開始を追跡する。

Why:
    Issue単位の開発ワークフロー（worktree作成→実装→レビュー→マージ）を
    追跡することで、ステップのスキップを検出し、品質を担保できる。

What:
    - git worktree addコマンドを検出
    - Issue番号を抽出してワークフローを開始
    - worktree_createdステップを完了としてマーク

State:
    writes: .claude/state/flow-progress.jsonl

Remarks:
    - ステップ完了の追跡はflow-progress-tracker.pyが担当

Changelog:
    - silenvx/dekita#2534: コマンドマッチングパターン改善
"""

import json
import re

from common import complete_flow_step, start_flow
from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.results import print_continue_and_log_skip
from lib.session import create_hook_context, parse_hook_input


def extract_issue_number_from_worktree(command: str) -> int | None:
    """Extract issue number from git worktree add command.

    Looks for patterns like:
    - git worktree add ../.worktrees/issue-123
    - git worktree add /path/to/issue-456 -b issue-456

    Args:
        command: The git command string

    Returns:
        Issue number if found, None otherwise.
    """
    # Look for issue-<number> pattern in the command
    match = re.search(r"issue-(\d+)", command, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def main() -> None:
    """Main entry point for the hook."""
    result = {"continue": True}

    input_data = parse_hook_input()
    # Issue #2607: Create context for session_id logging (even if input_data is empty)
    ctx = create_hook_context(input_data or {})
    if not input_data:
        print_continue_and_log_skip("development-workflow-tracker", "no input data", ctx=ctx)
        return

    # Only process Bash tool
    tool_name = input_data.get("tool_name")
    if tool_name != "Bash":
        print_continue_and_log_skip(
            "development-workflow-tracker", f"not Bash tool: {tool_name}", ctx=ctx
        )
        return

    # Get the command that was executed
    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")
    if not command:
        print_continue_and_log_skip("development-workflow-tracker", "no command", ctx=ctx)
        return

    # Only process if command succeeded (exit_code == 0)
    tool_result = get_tool_result(input_data) or {}
    exit_code = tool_result.get("exit_code", 0)
    if exit_code != 0:
        print_continue_and_log_skip(
            "development-workflow-tracker",
            f"command failed: exit_code={exit_code}",
            ctx=ctx,
        )
        return

    # Check for git worktree add command
    # Support both `git worktree add` and `cd /path && git worktree add` patterns
    # Issue #2534: Use (?:^|&&\s*) to match start of line or after &&
    # This avoids false positives from echo/comments while supporting cd prefix
    if re.search(r"(?:^|&&\s*)\s*git\s+worktree\s+add\b", command):
        issue_number = extract_issue_number_from_worktree(command)

        if issue_number:
            # Start development workflow with issue context
            context = {"issue_number": issue_number}
            flow_instance_id = start_flow("development-workflow", context)

            if flow_instance_id:
                # Mark worktree_created step as completed
                complete_flow_step(flow_instance_id, "worktree_created", "development-workflow")

                log_hook_execution(
                    "development-workflow-tracker",
                    "approve",
                    f"Development workflow started for issue #{issue_number}",
                    {"flow_instance_id": flow_instance_id},
                )

                result["systemMessage"] = (
                    f"[development-workflow] Issue #{issue_number} の開発ワークフローを開始しました。"
                )
            else:
                log_hook_execution(
                    "development-workflow-tracker",
                    "approve",
                    f"Failed to start flow for issue #{issue_number}",
                )
        else:
            log_hook_execution(
                "development-workflow-tracker",
                "approve",
                "Worktree created without issue number pattern",
                {"command": command[:100]},
            )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
