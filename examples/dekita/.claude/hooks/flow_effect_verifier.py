#!/usr/bin/env python3
"""未完了フローがある場合にセッション終了をブロックする。

Why:
    開発ワークフロー（worktree作成→実装→レビュー→マージ）のステップを
    スキップすると品質問題が発生する。セッション終了時にチェックし、
    未完了フローがあればブロックして完了を促す。

What:
    - セッション内のアクティブなフローを取得
    - クローズ済みIssueや期限切れフローを除外
    - blocking_on_session_end=Trueのフローで未完了ステップがあればブロック
    - フロー進捗サマリーとワークフロー検証結果を表示

State:
    reads: .claude/state/flow-progress.jsonl
    reads: .claude/state/flow/state-{session}.json

Remarks:
    - 24時間以上経過したフローは自動的に期限切れ
    - クローズ済みIssueのフローはブロック対象外

Changelog:
    - silenvx/dekita#1283: フロー有効期限機能追加
    - silenvx/dekita#1316: クローズ済みIssueフロー除外
    - silenvx/dekita#2478: セッション固有状態ファイル対応
    - silenvx/dekita#2494: 複数ワークフローのフェーズ集約
"""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from common import FLOW_LOG_DIR, get_incomplete_flows
from flow_definitions import can_skip_step, get_all_phases, get_flow_definition
from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import create_hook_context, parse_hook_input
from workflow_verifier import WorkflowVerifier

# フローの有効期限（時間）
# 古いフローはセッション終了をブロックしない (Issue #1283)
FLOW_EXPIRY_HOURS = 24

# ステータス優先度 (Issue #2494): 数値が大きいほど良いステータス
_STATUS_PRIORITY = {
    "completed": 3,
    "complete": 3,
    "in_progress": 2,
    "partial": 2,
    "pending": 1,
    "not_started": 0,
}


def get_state_file(session_id: str):
    """Get state file path for a specific session.

    Issue #734: Separate state files per session to prevent cross-session interference.
    Issue #2478: Sanitize session_id to prevent path traversal attacks.
    """
    from pathlib import Path as PathlibPath

    safe_session_id = PathlibPath(session_id).name
    return FLOW_LOG_DIR / f"state-{safe_session_id}.json"


def load_state(session_id: str) -> dict:
    """Load current state from session-specific state file.

    Issue #734: Each session has its own state file.
    """
    state_file = get_state_file(session_id)
    try:
        if state_file.exists():
            return json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        pass  # Best effort - corrupted state file is ignored

    # Initial state for new session
    return {
        "session_id": session_id,
        "active_workflow": None,
        "workflows": {},
        "global": {
            "hooks_fired_total": 0,
            "session_start_time": datetime.now(UTC).isoformat(),
        },
    }


def find_most_progressed_workflow(state: dict) -> tuple[str | None, dict]:
    """Find the most progressed workflow from state.

    Returns the workflow with the highest phase order or active_workflow.
    If no workflows exist, returns (None, {}).

    Issue #2478: Used to display correct workflow phases.
    """
    workflows = state.get("workflows", {})
    if not workflows:
        return None, {}

    # Get all phases for order lookup
    all_phases = get_all_phases()
    phase_order = {p.id: p.order for p in all_phases}

    # If active_workflow is set and exists, prefer it
    active = state.get("active_workflow")
    if active and active in workflows:
        return active, workflows[active]

    # Otherwise find workflow with highest phase order
    best_workflow = None
    best_order = -1
    best_data: dict = {}

    for wf_name, wf_data in workflows.items():
        current_phase = wf_data.get("current_phase", "")
        order = phase_order.get(current_phase, -1)
        if order > best_order:
            best_order = order
            best_workflow = wf_name
            best_data = wf_data

    return best_workflow, best_data


def aggregate_workflow_phases(state: dict) -> dict:
    """Aggregate phases from all workflows in the session.

    Issue #2494: When displaying session-end summary, we need to show
    phases completed across ALL workflows, not just the active one.

    For each phase, uses the "best" status across all workflows:
    - completed > in_progress > pending/not_started

    Returns:
        Dict mapping phase_id to {"status": str, "iterations": int}
    """
    workflows = state.get("workflows", {})
    if not workflows:
        return {}

    aggregated: dict[str, dict] = {}

    for _wf_name, wf_data in workflows.items():
        phases = wf_data.get("phases", {})
        for phase_id, phase_data in phases.items():
            status = phase_data.get("status", "not_started")
            iterations = phase_data.get("iterations", 0)

            if phase_id not in aggregated:
                aggregated[phase_id] = {"status": status, "iterations": iterations}
            else:
                # Keep the better status
                current_priority = _STATUS_PRIORITY.get(aggregated[phase_id]["status"], 0)
                new_priority = _STATUS_PRIORITY.get(status, 0)
                if new_priority > current_priority:
                    aggregated[phase_id]["status"] = status
                # Sum iterations
                aggregated[phase_id]["iterations"] += iterations

    return aggregated


def is_issue_closed(issue_number: int) -> bool:
    """Check if a GitHub issue is closed using gh CLI.

    Args:
        issue_number: The GitHub issue number to check.

    Returns:
        True if the issue is closed, False otherwise (including on error).
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                str(issue_number),
                "--json",
                "state",
                "-q",
                ".state",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            return result.stdout.strip() == "CLOSED"
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError, OSError):
        # gh CLI failure, timeout, or missing binary: treat as "open" to avoid false filtering
        pass
    return False


def filter_closed_issue_flows(flows: list[dict]) -> list[dict]:
    """Filter out flows whose associated issues are closed.

    This prevents closed issues from blocking session end (Issue #1316).
    Uses parallel processing for performance optimization (Issue #1320).

    Args:
        flows: List of flow dicts from get_incomplete_flows()

    Returns:
        List of flows excluding those for closed issues (preserving order).
    """
    # Collect unique issue numbers to check
    issue_numbers: set[int] = set()
    for flow in flows:
        issue_number = flow.get("context", {}).get("issue_number")
        if issue_number:
            issue_numbers.add(issue_number)

    if not issue_numbers:
        return flows[:]

    # Check issue states in parallel (max 5 concurrent)
    closed_issues: set[int] = set()
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_issue = {executor.submit(is_issue_closed, num): num for num in issue_numbers}
        for future in as_completed(future_to_issue):
            issue_num = future_to_issue[future]
            try:
                if future.result():
                    closed_issues.add(issue_num)
            except Exception:
                pass  # Treat errors as "open"

    # Filter flows while preserving original order
    open_flows = []
    for flow in flows:
        issue_number = flow.get("context", {}).get("issue_number")
        if issue_number and issue_number in closed_issues:
            continue
        open_flows.append(flow)

    return open_flows


def filter_expired_flows(flows: list[dict]) -> list[dict]:
    """Filter out flows that have expired (older than FLOW_EXPIRY_HOURS).

    This prevents old flows from blocking session end (Issue #1283).
    Flows without a timestamp are preserved (conservative approach).

    Args:
        flows: List of flow dicts from get_incomplete_flows()

    Returns:
        List of flows excluding expired ones (preserving order).
    """
    now = datetime.now(UTC)
    active_flows = []

    for flow in flows:
        started_at = flow.get("started_at")
        if not started_at:
            # No timestamp - preserve flow (conservative)
            active_flows.append(flow)
            continue

        try:
            # Parse ISO format timestamp
            flow_start = datetime.fromisoformat(started_at)
            # Ensure timezone-aware comparison
            if flow_start.tzinfo is None:
                flow_start = flow_start.replace(tzinfo=UTC)

            age_hours = (now - flow_start).total_seconds() / 3600
            if age_hours < FLOW_EXPIRY_HOURS:
                active_flows.append(flow)
            # Expired flows are silently filtered out
        except (ValueError, TypeError):
            # Invalid timestamp format - preserve flow (conservative)
            active_flows.append(flow)

    return active_flows


def get_required_pending_steps(flow: dict) -> list[str]:
    """Filter pending steps to only include required (non-skippable) steps.

    Uses can_skip_step() to evaluate which pending steps can be skipped
    based on their characteristics (required, condition).

    Args:
        flow: Flow dict from get_incomplete_flows()

    Returns:
        List of pending step IDs that cannot be skipped.
    """
    flow_id = flow.get("flow_id")
    context = flow.get("context", {})
    pending = flow.get("pending_steps", [])

    if not flow_id:
        return pending

    required_pending = []
    for step_id in pending:
        if not can_skip_step(flow_id, step_id, context):
            required_pending.append(step_id)

    return required_pending


def format_flow_summary(flows: list[dict]) -> str:
    """Format flow progress summary for display.

    Displays flows with phase-based grouping:
    - Completed phases are collapsed (e.g., "[setup] ✅ 完了")
    - Current/pending phases are expanded to show individual steps

    Args:
        flows: List of incomplete flow dicts from get_incomplete_flows()

    Returns:
        Formatted summary string with visual progress indicators.
    """
    if not flows:
        return ""

    lines = ["[flow-summary] セッション中のフロー進捗:"]

    for flow in flows:
        flow_id = flow.get("flow_id", "unknown")
        flow_name = flow.get("flow_name", flow_id)
        context = flow.get("context", {})
        completed = flow.get("completed_steps", [])
        pending = flow.get("pending_steps", [])
        step_counts = flow.get("step_counts", {})

        # Format context (e.g., "Issue #123")
        context_str = ""
        if issue_num := context.get("issue_number"):
            context_str = f" (Issue #{issue_num})"
        elif context:
            context_parts = [f"{k}: {v}" for k, v in context.items()]
            context_str = f" ({', '.join(context_parts)})"

        # Get flow definition for step names and phases
        flow_def = get_flow_definition(flow_id)

        # Build step lines with phase-based grouping
        step_lines: list[str] = []

        if flow_def:
            # Group steps by phase
            # Tuple: (step_id, step_name, is_completed, count, is_pending)
            phases: dict[str, list[tuple[str, str, bool, int, bool]]] = {}
            phase_order: list[str] = []

            for step in sorted(flow_def.steps, key=lambda s: s.order):
                phase = step.phase or "default"
                if phase not in phases:
                    phases[phase] = []
                    phase_order.append(phase)

                is_completed = step.id in completed
                is_pending = step.id in pending  # Track if step is actually pending
                count = step_counts.get(step.id, 1) if is_completed else 0
                phases[phase].append((step.id, step.name, is_completed, count, is_pending))

            # Determine current phase (first phase with pending steps)
            # Only consider steps that are in the pending list (excludes optional N/A steps)
            current_phase: str | None = None
            for phase in phase_order:
                phase_steps = phases[phase]
                if any(s[4] for s in phase_steps):  # s[4] = is_pending
                    current_phase = phase
                    break

            # Render each phase
            for phase in phase_order:
                phase_steps = phases[phase]
                # Phase is complete if no pending steps in it
                all_complete = not any(s[4] for s in phase_steps)

                phase_names = {
                    "setup": "準備",
                    "implementation": "実装",
                    "review": "レビュー",
                    "complete": "完了",
                    "default": "ステップ",
                }
                phase_display = phase_names.get(phase, phase)

                if all_complete:
                    step_lines.append(f"[{phase_display}] ✅ 完了")
                elif phase == current_phase:
                    step_lines.append(f"[{phase_display}]")
                    for step_id, step_name, is_completed, count, is_pending in phase_steps:
                        # Skip optional steps that are neither completed nor pending
                        if not is_completed and not is_pending:
                            continue
                        if is_completed:
                            count_str = f" ({count}回)" if count > 1 else ""
                            step_lines.append(f"  ✅ {step_name}{count_str}")
                        elif step_id == pending[0] if pending else False:
                            step_lines.append(f"  ⏳ {step_name} ← 次のステップ")
                        else:
                            step_lines.append(f"  ⬜ {step_name}")
                else:
                    step_lines.append(f"[{phase_display}] ⬜")
        else:
            # Fallback: flat list if no flow definition
            for step_id in completed:
                count = step_counts.get(step_id, 1)
                count_str = f" ({count}回)" if count > 1 else ""
                step_lines.append(f"✅ {step_id}{count_str}")

            for i, step_id in enumerate(pending):
                if i == 0:
                    step_lines.append(f"⏳ {step_id} ← 次のステップ")
                else:
                    step_lines.append(f"⬜ {step_id}")

        # Calculate box width based on content (min 30, max 60)
        max_content_len = max((len(line) for line in step_lines), default=0)
        box_width = min(60, max(30, max_content_len + 4))

        lines.append(f"\n📋 {flow_name}{context_str}")
        lines.append("┌" + "─" * box_width + "┐")
        for step_line in step_lines:
            padded = step_line.ljust(box_width - 2)
            lines.append(f"│ {padded} │")
        lines.append("└" + "─" * box_width + "┘")

    return "\n".join(lines)


def format_workflow_verification_summary(
    verifier: WorkflowVerifier,
    verbose: bool = False,
    session_id: str | None = None,
) -> str:
    """Format workflow verification summary for display.

    Shows workflow phase progress based on state file, with hook execution stats.

    Issue #2478: Uses state file phases instead of hook-based estimation.

    Args:
        verifier: WorkflowVerifier instance with loaded executions
        verbose: If True, show all phase details
        session_id: If provided, loads state file for accurate phase display

    Returns:
        Formatted summary string
    """
    summary = verifier.get_summary_dict()

    # Hook stats
    fired = summary.get("fired_hooks", 0)
    unfired = summary.get("unfired_hooks", 0)

    # Issue #2478: Use state file for phases if session_id is provided
    # Issue #2494: Aggregate phases from ALL workflows, not just active one
    workflow_name: str | None = None
    current_phase: str | None = None
    workflow_phases: dict = {}

    if session_id:
        state = load_state(session_id)
        workflow_name, workflow_data = find_most_progressed_workflow(state)
        if workflow_data:
            current_phase = workflow_data.get("current_phase")
            # Issue #2600: Use current workflow's phases only, not aggregated
            # Aggregating all workflows causes misleading display where phases
            # completed in other workflows appear as completed for current workflow
            workflow_phases = workflow_data.get("phases", {})

    # Fallback to verifier if no state data
    if not current_phase:
        current_phase = summary.get("current_phase")

    # Build header with workflow name if available
    workflow_display = f" [{workflow_name}]" if workflow_name else ""
    header = f"📍 {current_phase or 'unknown'}{workflow_display} | 🪝 {fired}/{fired + unfired}"
    lines = [f"\n[workflow-verification] {header}"]

    # Phase status - vertical view (Issue #720/#731)
    phase_icons = {
        "completed": "✅",
        "complete": "✅",
        "in_progress": "⏳",
        "partial": "⏳",
        "pending": "⬜",
        "not_started": "⬜",
        "no_hooks": "➖",
    }

    # Issue #2478: Use state file phases if available
    if workflow_phases:
        lines.append("")
        all_phases = get_all_phases()
        for phase in all_phases:
            phase_state = workflow_phases.get(phase.id, {})
            status = phase_state.get("status", "not_started")
            icon = phase_icons.get(status, "⬜")
            # Mark current phase
            marker = " ←" if phase.id == current_phase else ""
            lines.append(f"  {icon} {phase.name}{marker}")
    else:
        # Fallback to verifier-based phases
        phases = summary.get("phases", [])
        if phases:
            lines.append("")
            for p in phases:
                icon = phase_icons.get(p.get("status", ""), "⬜")
                phase_name = p.get("phase_name", "")
                # Mark current phase
                marker = " ←" if p.get("phase_id") == current_phase else ""
                lines.append(f"  {icon} {phase_name}{marker}")

    # Issues
    issues = summary.get("issues", [])
    if issues:
        lines.append("")
        lines.append("⚠️ 検出された問題:")
        for issue in issues[:5]:  # Limit to 5 issues
            lines.append(f"  - {issue.get('hook', 'unknown')}: {issue.get('message', '')}")
        if len(issues) > 5:
            lines.append(f"  ... 他 {len(issues) - 5} 件")

    return "\n".join(lines)


def main() -> None:
    """Main entry point for the hook."""
    input_data = parse_hook_input()

    # Prevent infinite loops in Stop hooks
    if input_data.get("stop_hook_active"):
        result = {"ok": True, "decision": "approve"}
        print(json.dumps(result))
        return

    # Issue #2478: Get session_id for state file access
    ctx = create_hook_context(input_data)
    session_id = ctx.get_session_id()

    # Initialize workflow verifier for hook execution analysis
    verifier = WorkflowVerifier()
    workflow_summary = format_workflow_verification_summary(verifier, session_id=session_id)

    # Get incomplete flows
    incomplete_flows = get_incomplete_flows()

    # Filter out flows for closed issues (Issue #1316)
    incomplete_flows = filter_closed_issue_flows(incomplete_flows)

    # Filter out expired flows (Issue #1283)
    incomplete_flows = filter_expired_flows(incomplete_flows)

    if not incomplete_flows:
        # No incomplete flows - show workflow verification as info
        log_hook_execution(
            "flow-effect-verifier",
            "approve",
            "No incomplete flows",
        )
        result = {"ok": True, "decision": "approve", "systemMessage": workflow_summary}
        print(json.dumps(result))
        return

    # Generate summary for all incomplete flows (Phase 1 enhancement)
    summary = format_flow_summary(incomplete_flows)
    # Combine flow summary with workflow verification
    summary = f"{summary}\n{workflow_summary}"

    # Filter for blocking flows only (using flow_definitions.py)
    # Phase 4: Only consider required (non-skippable) pending steps
    blocking_flows: list[dict] = []
    for flow in incomplete_flows:
        flow_id = flow.get("flow_id")
        flow_def = get_flow_definition(flow_id)

        if flow_def and flow_def.blocking_on_session_end:
            # Check if there are any required pending steps
            required_pending = get_required_pending_steps(flow)
            if required_pending:
                # Update flow with filtered pending steps for display
                flow_with_required = flow.copy()
                flow_with_required["pending_steps"] = required_pending
                blocking_flows.append(flow_with_required)

    if not blocking_flows:
        # No blocking flows, but show summary as informational message
        log_hook_execution(
            "flow-effect-verifier",
            "approve",
            "No blocking flows (non-blocking incomplete flows exist)",
            {"incomplete_flow_count": len(incomplete_flows)},
        )
        result = {"ok": True, "decision": "approve", "systemMessage": summary}
        print(json.dumps(result))
        return

    # Build block message
    messages: list[str] = []
    for flow in blocking_flows:
        flow_name = flow.get("flow_name", flow.get("flow_id", "unknown"))
        pending = flow.get("pending_steps", [])
        context = flow.get("context", {})

        context_str = ""
        if context:
            # Format context for display (e.g., issue_number: 123)
            context_parts = [f"{k}: {v}" for k, v in context.items()]
            context_str = f" ({', '.join(context_parts)})"

        messages.append(f"- {flow_name}{context_str}: 未完了ステップ {pending}")

    hint = (
        "ヒント: フロー定義で指定されたパターンにマッチするコマンドを"
        "実行すると、ステップが完了としてマークされます。"
    )
    reason = (
        "未完了のフローがあります。以下のフローを完了させてください:\n\n"
        + "\n".join(messages)
        + f"\n\n{hint}\n\n{summary}"
    )

    # Log blocking decision with flow details
    blocking_flow_details = [
        {
            "flow_id": flow.get("flow_id"),
            "flow_name": flow.get("flow_name"),
            "pending_steps": flow.get("pending_steps", []),
            "context": flow.get("context", {}),
        }
        for flow in blocking_flows
    ]
    log_hook_execution(
        "flow-effect-verifier",
        "block",
        f"Blocking session end: {len(blocking_flows)} incomplete flow(s)",
        {"blocking_flows": blocking_flow_details},
    )

    result = make_block_result("flow-effect-verifier", reason)
    result["ok"] = False
    print(json.dumps(result))


if __name__ == "__main__":
    main()
