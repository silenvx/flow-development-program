#!/usr/bin/env python3
"""ワークフロー追跡を検証し、レポートを生成する。

Why:
    フェーズ追跡が正確に機能しているか検証し、ワークフロー設計の
    問題点を発見する。セッション終了時にサマリーを表示することで、
    改善ポイントを認識できる。

What:
    - Level 1: 追跡精度の検証（フェーズ遷移、ループ検出）
    - Level 2: フロー設計レビュー（効率性、順序、粒度、カバレッジ）
    - スキップされたフェーズの理由推定
    - 全セッション統計の集計

State:
    reads: .claude/state/flow/state-{session}.json
    reads: .claude/state/flow/events-{session}.jsonl
    writes: .claude/state/flow/verification-report.json

Remarks:
    - Stop hookとして発動（セッション終了時）
    - ブロックせず検証レポートのみ表示

Changelog:
    - silenvx/dekita#720: フック追加
    - silenvx/dekita#1359: 依存関係ベースのフェーズ順序チェックに変更
    - silenvx/dekita#1627: スキップ理由の推定機能追加
    - silenvx/dekita#1690: 違反サマリー表示追加
"""

import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Issue #1352: Import shared constants to avoid duplication
# Issue #1359: Import PHASE_DEPENDENCIES for flexible order checking
# Issue #1690: Import CRITICAL_VIOLATIONS for violation summary
from common import FLOW_LOG_DIR
from flow_constants import CRITICAL_VIOLATIONS, OPTIONAL_PHASES, PHASE_DEPENDENCIES
from lib.execution import log_hook_execution
from lib.session import create_hook_context, parse_hook_input

# Constants
REPORT_FILE = FLOW_LOG_DIR / "verification-report.json"


def get_events_file(session_id: str) -> Path:
    """Get events file path for a specific session.

    Issue #1831: Separate events files per session.
    """
    return FLOW_LOG_DIR / f"events-{session_id}.jsonl"


def get_state_file(session_id: str) -> Path:
    """Get state file path for a specific session.

    Issue #734: Separate state files per session.
    """
    return FLOW_LOG_DIR / f"state-{session_id}.json"


def ensure_log_dir():
    """Ensure flow log directory exists.

    Issue #1723: Create log directory automatically at startup
    to prevent failures when reading/writing flow state files.
    """
    FLOW_LOG_DIR.mkdir(parents=True, exist_ok=True)


# Expected phase order
# Issue #1234: issue_work is typically done before worktree_create (checking/assigning issues)
EXPECTED_PHASE_ORDER = [
    "session_start",
    "pre_check",
    "issue_work",  # Issue confirmation before worktree creation
    "worktree_create",
    "implementation",
    "pre_commit_check",
    "local_ai_review",
    "pr_create",
    "ci_review",
    "merge",
    "cleanup",
    "production_check",
    "session_end",
]

# Issue #1352: OPTIONAL_PHASES is now imported from flow_constants.py

# Critical phases that should not be skipped (Issue #1309)
# These require warnings when skipped
CRITICAL_PHASES = {
    "pre_commit_check",  # Code verification before commit
    "cleanup",  # Resource cleanup after merge
}


def infer_skip_reason(phase: str, workflow_id: str, phases_seen: set[str]) -> dict:
    """Infer the reason why a phase was skipped.

    Issue #1627: Provides context for why phases were skipped to help
    identify issues and improve workflow understanding.

    Args:
        phase: The phase that was skipped
        workflow_id: The workflow identifier (e.g., "issue-123", "main")
        phases_seen: Set of phases that were actually executed

    Returns:
        dict with:
        - phase: The skipped phase name
        - status: "skipped"
        - reason: Human-readable explanation
        - context: Additional context (workflow_id, related phases, etc.)
    """
    context: dict = {"workflow_id": workflow_id}
    reason = "Unknown reason"

    # Determine skip reason based on phase and context
    if phase == "pre_check":
        if "worktree_create" in phases_seen or "implementation" in phases_seen:
            reason = "Started work directly without pre-check exploration"
        elif workflow_id == "main":
            reason = "Working on main branch (no issue context)"
        else:
            reason = "Skipped initial codebase exploration"

    elif phase == "worktree_create":
        if workflow_id == "main":
            reason = "Working on main branch (no worktree needed)"
        elif "implementation" in phases_seen:
            reason = "Worktree may have been created in previous session"
        else:
            reason = "Worktree creation not detected"

    elif phase == "implementation":
        if "pre_commit_check" in phases_seen:
            reason = "Documentation or config change only (no code implementation)"
        elif "pr_create" in phases_seen:
            reason = "Implementation done in previous session"
        else:
            reason = "No implementation detected"

    elif phase == "pre_commit_check":
        if "pr_create" in phases_seen or "ci_review" in phases_seen:
            reason = "Commit verification may have been done in previous session"
        else:
            reason = "No commit detected in this session"

    elif phase == "local_ai_review":
        if "pr_create" in phases_seen:
            reason = "PR created without local AI review (optional)"
            context["suggestion"] = "Consider running codex review before PR creation"
        else:
            reason = "Local AI review was not run"

    elif phase == "pr_create":
        if workflow_id == "main":
            reason = "Working on main branch (no PR needed)"
        elif "ci_review" in phases_seen or "merge" in phases_seen:
            reason = "PR may have been created by another session"
            context["external_session"] = True
        else:
            reason = "No PR creation detected"

    elif phase == "ci_review":
        if "merge" in phases_seen:
            reason = "CI review may have been done in another session"
            context["external_session"] = True
        elif "pr_create" not in phases_seen:
            reason = "No PR was created in this session"
        else:
            reason = "CI review not detected"

    elif phase == "merge":
        if "cleanup" in phases_seen:
            reason = "Merge may have been done by another session"
            context["external_session"] = True
        elif "pr_create" not in phases_seen and "ci_review" not in phases_seen:
            reason = "No PR workflow in this session"
        else:
            reason = "PR not merged in this session"

    elif phase == "cleanup":
        if "merge" in phases_seen:
            reason = "CRITICAL: Cleanup not done after merge"
            context["critical"] = True
            context["suggestion"] = "Ensure worktree and branch are cleaned up"
        elif "session_end" in phases_seen:
            reason = "Session ended without cleanup phase"
        else:
            reason = "No cleanup detected"

    elif phase == "production_check":
        # production_check is optional
        reason = "Production check was not performed (optional)"

    elif phase == "session_end":
        reason = "Session end not properly recorded"

    return {
        "phase": phase,
        "status": "skipped",
        "reason": reason,
        "context": context,
    }


def load_events(session_id: str) -> list[dict]:
    """Load events for a specific session.

    Issue #1831: Load from session-specific events file.
    """
    events = []
    try:
        events_file = get_events_file(session_id)
        if events_file.exists():
            for line in events_file.read_text().splitlines():
                if line.strip():
                    events.append(json.loads(line))
    except (json.JSONDecodeError, OSError):
        pass  # Best effort - corrupted events file is ignored
    return events


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
    return {}


def load_all_sessions_workflows() -> tuple[dict[str, dict], int]:
    """Load and merge workflows from all session state files.

    Issue #1371: Aggregate workflows across all sessions for comprehensive reporting.

    Returns:
        Tuple of (merged_workflows, session_count):
        - merged_workflows: Dict of workflow_id -> workflow_data, with newer data taking priority
        - session_count: Number of session state files processed
    """
    all_workflows: dict[str, dict] = {}
    session_count = 0

    try:
        state_files = list(FLOW_LOG_DIR.glob("state-*.json"))
    except OSError:
        return {}, 0

    for state_file in state_files:
        try:
            state = json.loads(state_file.read_text())
            session_count += 1

            # Get file mtime as fallback timestamp (ISO format for comparison)
            file_mtime = datetime.fromtimestamp(state_file.stat().st_mtime, tz=UTC).isoformat()

            for workflow_id, workflow_data in state.get("workflows", {}).items():
                if workflow_id not in all_workflows:
                    all_workflows[workflow_id] = workflow_data
                    # Store file mtime for potential later comparisons
                    all_workflows[workflow_id]["_file_mtime"] = file_mtime
                else:
                    # Prefer newer data based on updated_at timestamp,
                    # falling back to file mtime when updated_at is missing
                    existing_updated = all_workflows[workflow_id].get(
                        "updated_at"
                    ) or all_workflows[workflow_id].get("_file_mtime", "")
                    new_updated = workflow_data.get("updated_at") or file_mtime
                    if new_updated > existing_updated:
                        all_workflows[workflow_id] = workflow_data
                        all_workflows[workflow_id]["_file_mtime"] = file_mtime
        except (json.JSONDecodeError, OSError):
            pass  # Skip corrupted state files

    # Clean up temporary _file_mtime keys
    for workflow_data in all_workflows.values():
        workflow_data.pop("_file_mtime", None)

    return all_workflows, session_count


def rebuild_state_from_events(events: list[dict], target_session_id: str = "") -> dict:
    """Rebuild state from events (for recovery/verification).

    Issue #777: Simplified to use session_id only. Claude Code's session_id from
    hook JSON input is unique per conversation.

    Args:
        events: List of events from events.jsonl
        target_session_id: If provided, only rebuild from events with this session_id.
                          If empty, uses the most recent session_id found in events.
    """
    state = {
        "session_id": "",
        "active_workflow": None,
        "workflows": {},
        "global": {"hooks_fired_total": 0},
    }

    # Determine session_id to use (most recent if not specified)
    if not target_session_id and events:
        # Find the most recent session_id (last unique one in events)
        for event in reversed(events):
            if event.get("session_id"):
                target_session_id = event["session_id"]
                break

    # Filter events to target session
    if target_session_id:
        events = [e for e in events if e.get("session_id") == target_session_id]

    for event in events:
        session_id = event.get("session_id", "")
        workflow = event.get("workflow", "unknown")

        if not state["session_id"]:
            state["session_id"] = session_id

        if workflow not in state["workflows"]:
            state["workflows"][workflow] = {
                "current_phase": "session_start",
                "phases": {},
            }

        state["global"]["hooks_fired_total"] += 1
        state["active_workflow"] = workflow

        if event.get("event") == "phase_transition":
            new_phase = event.get("new_phase")
            if new_phase:
                wf = state["workflows"][workflow]
                old_phase = wf.get("current_phase")

                # Mark old phase complete
                if old_phase and old_phase != new_phase:
                    if old_phase not in wf["phases"]:
                        wf["phases"][old_phase] = {"status": "completed", "iterations": 1}
                    else:
                        wf["phases"][old_phase]["status"] = "completed"

                # Update new phase
                if new_phase not in wf["phases"]:
                    wf["phases"][new_phase] = {"status": "in_progress", "iterations": 1}
                else:
                    wf["phases"][new_phase]["iterations"] += 1
                    if event.get("loop_reason"):
                        if "loop_reasons" not in wf["phases"][new_phase]:
                            wf["phases"][new_phase]["loop_reasons"] = []
                        wf["phases"][new_phase]["loop_reasons"].append(event["loop_reason"])

                wf["current_phase"] = new_phase

    return state


def verify_tracking_accuracy(events: list[dict], state: dict) -> dict:
    """Level 1: Verify tracking accuracy.

    Issue #1359: Changed from strict linear order checking to dependency-based checking.
    Only reports violations when a required dependency is missing, not for flexible ordering.

    Issue #1627: Added skipped_phases with inferred reasons for each skip.
    """
    report = {
        "phase_transitions": {"correct": 0, "total": 0, "issues": []},
        "loop_detection": {"correct": 0, "total": 0, "issues": []},
        "undetected_events": [],
        "skipped_phases": [],  # Issue #1627: Detailed skip information
        "false_positives": [],
    }

    # Issue #1359: Use dependency-based checking instead of strict linear order
    # Design decision (Issue #1531):
    # - Uses set-based dependency check, not order-based
    # - Reason: flow-state.json only records phase existence, not execution timestamps
    # - If both required phases exist, the dependency is considered satisfied
    # - For event-level order verification, use flow-events.jsonl with timestamps
    for workflow_id, workflow in state.get("workflows", {}).items():
        phases_seen = set(workflow.get("phases", {}).keys())
        report["phase_transitions"]["total"] += len(phases_seen)

        for phase in phases_seen:
            # Check if this phase has required dependencies
            required_deps = PHASE_DEPENDENCIES.get(phase, set())

            if required_deps:
                # At least one dependency must be present
                deps_satisfied = any(dep in phases_seen for dep in required_deps)
                if deps_satisfied:
                    report["phase_transitions"]["correct"] += 1
                else:
                    # Dependency violation - this is a real issue
                    missing = ", ".join(sorted(required_deps))
                    report["phase_transitions"]["issues"].append(
                        f"Phase '{phase}' missing required predecessor(s) ({missing}) in {workflow_id}"
                    )
            else:
                # No dependencies, always correct
                report["phase_transitions"]["correct"] += 1

    # Count loops
    loop_events = [e for e in events if e.get("loop_reason")]
    report["loop_detection"]["total"] = len(loop_events)
    report["loop_detection"]["correct"] = len(loop_events)  # Assume all detected loops are correct

    # Check for skipped phases (not errors, just notes)
    # Issue #1627: Now includes inferred reasons for each skip
    for workflow_id, workflow in state.get("workflows", {}).items():
        phases_seen = set(workflow.get("phases", {}).keys())
        for phase in EXPECTED_PHASE_ORDER:
            if phase not in phases_seen and phase not in OPTIONAL_PHASES:
                # Legacy format for backward compatibility
                report["undetected_events"].append(f"Phase '{phase}' was skipped in {workflow_id}")
                # Issue #1627: Add detailed skip information
                skip_info = infer_skip_reason(phase, workflow_id, phases_seen)
                report["skipped_phases"].append(skip_info)

    return report


def review_flow_design(events: list[dict], state: dict) -> dict:
    """Level 2: Review flow design effectiveness."""
    report = {
        "efficiency": {"issues": [], "suggestions": []},
        "order": {"issues": [], "suggestions": []},
        "granularity": {"issues": [], "suggestions": []},
        "coverage": {"issues": [], "suggestions": []},
        "divergence": {"issues": [], "suggestions": []},
    }

    for workflow_id, workflow in state.get("workflows", {}).items():
        phases = workflow.get("phases", {})

        # Efficiency: Check for excessive loops
        for phase, info in phases.items():
            iterations = info.get("iterations", 1)
            if iterations > 3:
                report["efficiency"]["issues"].append(
                    f"Phase '{phase}' had {iterations} iterations in {workflow_id}"
                )
                loop_reasons = info.get("loop_reasons", [])
                reason_counts = Counter(loop_reasons)
                for reason, count in reason_counts.most_common(1):
                    report["efficiency"]["suggestions"].append(
                        f"Consider preventing '{reason}' - caused {count} loops"
                    )

        # Coverage: Check for missing common phases
        # Issue #1309: Critical phase skip detection with CRITICAL prefix
        # Issue #1257: Removed implementation → pre_commit_check check
        # (session resume, doc changes may have pre_commit_check without implementation)

        # Issue #1309: Check for cleanup skip after merge
        if "merge" in phases and "cleanup" not in phases:
            report["coverage"]["issues"].append(
                f"CRITICAL: 'cleanup' was skipped in {workflow_id} - worktree/branch cleanup may be incomplete"
            )
            # Avoid duplicate suggestions
            suggestion = (
                "cleanup is critical - ensure worktrees and branches are cleaned up after merge"
            )
            if suggestion not in report["coverage"]["suggestions"]:
                report["coverage"]["suggestions"].append(suggestion)

        if "pr_create" in phases and "local_ai_review" not in phases:
            # Avoid duplicate suggestions
            suggestion = "Consider running local AI review before creating PR"
            if suggestion not in report["coverage"]["suggestions"]:
                report["coverage"]["suggestions"].append(suggestion)

        # Granularity: Check if phases are too coarse
        impl_iterations = phases.get("implementation", {}).get("iterations", 1)
        if impl_iterations > 2:
            # Avoid duplicate suggestions
            suggestion = "Consider splitting 'implementation' into 'initial' and 'revision' phases"
            if suggestion not in report["granularity"]["suggestions"]:
                report["granularity"]["suggestions"].append(suggestion)

    return report


def calculate_completion_metrics(state: dict) -> dict:
    """Calculate workflow completion metrics (Issue #1309).

    Returns:
        dict containing:
        - total_workflows: Total number of workflows tracked
        - completed_workflows: Workflows with both 'cleanup' and 'session_end' phases
        - completion_rate: Ratio of completed to total workflows (0.0-1.0)
        - total_critical_issues: Total count of critical phase violations
          (e.g., merge without cleanup)
    """
    workflows = state.get("workflows", {})
    total_workflows = len(workflows)
    completed_workflows = 0
    total_critical_issues = 0

    for workflow in workflows.values():
        phases = workflow.get("phases", {})

        # Workflow complete: has both cleanup and session_end
        if "cleanup" in phases and "session_end" in phases:
            completed_workflows += 1

        # Issue #1257: Removed implementation → pre_commit_check check
        # (session resume, doc changes may have pre_commit_check without implementation)

        # Critical issue: merge without cleanup
        if "merge" in phases and "cleanup" not in phases:
            total_critical_issues += 1

    return {
        "total_workflows": total_workflows,
        "completed_workflows": completed_workflows,
        "completion_rate": completed_workflows / total_workflows if total_workflows > 0 else 0.0,
        "total_critical_issues": total_critical_issues,
    }


def count_session_violations(events: list[dict]) -> dict:
    """Count phase transition violations in session events (Issue #1690).

    Args:
        events: List of events from events.jsonl

    Returns:
        dict containing:
        - critical: Count of critical violations (blocking level)
        - warning: Count of non-critical violations (warning level)
        - patterns: Dict of violation pattern -> count (e.g., {"merge->session_end": 2})
        - details: List of violation details for reporting
    """
    violations = {
        "critical": 0,
        "warning": 0,
        "patterns": {},
        "details": [],
    }

    for event in events:
        violation_reason = event.get("violation_reason")
        if not violation_reason:
            continue

        current_phase = event.get("current_phase", "")
        new_phase = event.get("new_phase", "")
        pattern = f"{current_phase}->{new_phase}"

        violations["patterns"][pattern] = violations["patterns"].get(pattern, 0) + 1

        # Check if this is a critical violation
        violation_key = (current_phase, new_phase)
        if violation_key in CRITICAL_VIOLATIONS:
            violations["critical"] += 1
            violations["details"].append(
                {
                    "type": "critical",
                    "pattern": pattern,
                    "reason": violation_reason,
                    "critical_reason": CRITICAL_VIOLATIONS[violation_key],
                }
            )
        else:
            violations["warning"] += 1
            violations["details"].append(
                {
                    "type": "warning",
                    "pattern": pattern,
                    "reason": violation_reason,
                }
            )

    return violations


def generate_report(
    events: list[dict],
    state: dict,
    aggregated_workflows: dict[str, dict] | None = None,
    session_count: int = 0,
) -> dict:
    """Generate full verification report.

    Args:
        events: List of events for the current session
        state: Current session state
        aggregated_workflows: Optional merged workflows from all sessions (Issue #1371)
        session_count: Number of sessions included in aggregated data
    """
    accuracy = verify_tracking_accuracy(events, state)
    design = review_flow_design(events, state)
    # Issue #1309: Add completion metrics
    completion = calculate_completion_metrics(state)
    # Issue #1690: Add violation summary
    violations = count_session_violations(events)

    # Calculate summary stats
    total_phases = 0
    passed_phases = 0
    total_loops = 0

    for workflow in state.get("workflows", {}).values():
        phases = workflow.get("phases", {})
        total_phases += len(phases)
        passed_phases += sum(1 for p in phases.values() if p.get("status") == "completed")
        total_loops += sum(p.get("iterations", 1) - 1 for p in phases.values())

    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": state.get("session_id", ""),
        "summary": {
            "total_phases": total_phases,
            "passed_phases": passed_phases,
            "skipped_phases": len(accuracy.get("undetected_events", [])),
            "total_loops": total_loops,
            "hooks_fired": state.get("global", {}).get("hooks_fired_total", 0),
            # Issue #1309: Add completion metrics to summary
            "completion_rate": completion["completion_rate"],
            "total_critical_issues": completion["total_critical_issues"],
            # Issue #1690: Add violation counts to summary
            "violations_critical": violations["critical"],
            "violations_warning": violations["warning"],
        },
        "level1_accuracy": accuracy,
        "level2_design": design,
        # Issue #1309: Add completion section
        "completion": completion,
        # Issue #1690: Add violations section
        "violations": violations,
        "workflows": state.get("workflows", {}),
    }

    # Issue #1371: Add aggregated section with all-sessions data
    if aggregated_workflows is not None:
        aggregated_state = {"workflows": aggregated_workflows}
        aggregated_completion = calculate_completion_metrics(aggregated_state)
        report["aggregated"] = {
            "total_sessions": session_count,
            "total_workflows": len(aggregated_workflows),
            "completion_rate": aggregated_completion["completion_rate"],
            "total_critical_issues": aggregated_completion["total_critical_issues"],
            "completed_workflows": aggregated_completion["completed_workflows"],
        }

    return report


def format_report_text(report: dict) -> str:
    """Format report as human-readable text."""
    lines = ["", "━━━ フロー検証レポート ━━━", ""]

    summary = report.get("summary", {})
    lines.append("📊 サマリー")
    lines.append(f"  総フェーズ: {summary.get('total_phases', 0)}")
    lines.append(f"  完了: {summary.get('passed_phases', 0)}")
    lines.append(f"  スキップ: {summary.get('skipped_phases', 0)}")
    lines.append(f"  総ループ: {summary.get('total_loops', 0)}")
    lines.append(f"  フック発動: {summary.get('hooks_fired', 0)}")
    # Issue #1309: Display completion rate and critical issues
    completion_rate = summary.get("completion_rate", 0)
    critical_issues = summary.get("total_critical_issues", 0)
    lines.append(f"  完了率: {completion_rate:.0%}")
    if critical_issues > 0:
        lines.append(f"  ⚠️ Critical問題: {critical_issues}件")

    # Issue #1690: Display violation summary
    violations_critical = summary.get("violations_critical", 0)
    violations_warning = summary.get("violations_warning", 0)
    if violations_critical > 0 or violations_warning > 0:
        lines.append("")
        lines.append("⚠️ フェーズ遷移違反")
        if violations_critical > 0:
            lines.append(f"  Critical: {violations_critical}件（ブロック対象）")
        if violations_warning > 0:
            lines.append(f"  Warning: {violations_warning}件（警告のみ）")

        # Show violation patterns
        violations = report.get("violations", {})
        patterns = violations.get("patterns", {})
        if patterns:
            lines.append("  パターン別:")
            for pattern, count in sorted(patterns.items(), key=lambda x: -x[1])[:5]:
                lines.append(f"    - {pattern}: {count}回")

    lines.append("")

    # Level 1
    accuracy = report.get("level1_accuracy", {})
    trans = accuracy.get("phase_transitions", {})
    lines.append("📈 追跡精度 (Level 1)")
    lines.append(f"  フェーズ遷移: {trans.get('correct', 0)}/{trans.get('total', 0)}")

    # Issue #1627: Display skipped phases with reasons
    skipped_phases = accuracy.get("skipped_phases", [])
    if skipped_phases:
        lines.append("  ⚠️ スキップされたフェーズ:")
        for skip_info in skipped_phases[:5]:  # Limit to 5 for readability
            phase = skip_info.get("phase", "unknown")
            reason = skip_info.get("reason", "Unknown reason")
            context = skip_info.get("context", {})
            workflow_id = context.get("workflow_id", "unknown")

            # Mark critical skips
            if context.get("critical"):
                lines.append(f"    - ⚠️ CRITICAL: {phase} ({workflow_id})")
            else:
                lines.append(f"    - {phase} ({workflow_id})")
            lines.append(f"      理由: {reason}")

            # Show suggestion if available
            if context.get("suggestion"):
                lines.append(f"      💡 {context['suggestion']}")
    lines.append("")

    # Level 2
    design = report.get("level2_design", {})
    has_issues = any(design[cat].get("issues") for cat in design)
    has_suggestions = any(design[cat].get("suggestions") for cat in design)

    if has_issues or has_suggestions:
        lines.append("🤔 フロー設計レビュー (Level 2)")
        for category, data in design.items():
            if data.get("issues"):
                for issue in data["issues"]:
                    lines.append(f"  ⚠️ [{category}] {issue}")
            if data.get("suggestions"):
                for suggestion in data["suggestions"]:
                    lines.append(f"  💡 [{category}] {suggestion}")
        lines.append("")

    # Issue #1371: Display aggregated stats from all sessions
    aggregated = report.get("aggregated")
    if aggregated:
        lines.append("🌐 全セッション統計")
        lines.append(f"  総セッション数: {aggregated.get('total_sessions', 0)}")
        lines.append(f"  総ワークフロー数: {aggregated.get('total_workflows', 0)}")
        agg_rate = aggregated.get("completion_rate", 0)
        lines.append(f"  全体完了率: {agg_rate:.0%}")
        agg_critical = aggregated.get("total_critical_issues", 0)
        if agg_critical > 0:
            lines.append(f"  ⚠️ Critical問題（全体）: {agg_critical}件")
        lines.append("")

    return "\n".join(lines)


def main():
    """Main hook logic."""
    # Issue #1723: 起動時にログディレクトリの存在を保証
    # このフックでは load_state()、load_events()、load_all_sessions_workflows() などの
    # 読み込み処理が書き込み処理より先に呼ばれる可能性があるため、起動時に一度
    # ensure_log_dir() を呼び出してログディレクトリの存在を保証しておく。
    try:
        ensure_log_dir()
    except OSError:
        pass  # Best effort - if log dir creation fails, continue without logging

    # Read hook input
    hook_input = parse_hook_input()

    ctx = create_hook_context(hook_input)

    # Skip if Stop hook is already active
    if hook_input.get("stop_hook_active"):
        print(json.dumps({"decision": "approve"}))
        return

    # Get session ID first
    # Issue #734: Each session has its own state file
    # Use same fallback as flow-state-updater.py for consistency
    session_id = (
        ctx.get_session_id() or f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    )

    # Load data
    # Issue #1831: load_events now takes session_id and returns only that session's events
    events = load_events(session_id)
    state = load_state(session_id)

    # Rebuild state from events if state file is missing/empty
    # Issue #777: session_id from hook input is unique per conversation
    if not state.get("workflows"):
        state = rebuild_state_from_events(events, session_id)

    # Issue #1371: Load aggregated workflows from all sessions
    aggregated_workflows, session_count = load_all_sessions_workflows()

    # Generate report
    report = generate_report(events, state, aggregated_workflows, session_count)

    # Save report
    # Note: ensure_log_dir() is already called at main() startup
    try:
        REPORT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    except OSError:
        pass  # Best effort - report save may fail

    # Format and log
    report_text = format_report_text(report)

    log_hook_execution(
        "flow-verifier",
        "approve",
        "Flow verification completed",
        {
            "total_phases": report["summary"]["total_phases"],
            "total_loops": report["summary"]["total_loops"],
        },
    )

    # Output report as system message if there are any issues or suggestions
    result = {"decision": "approve"}
    has_level1_issues = report["level1_accuracy"].get("phase_transitions", {}).get(
        "issues"
    ) or report["level1_accuracy"].get("undetected_events")
    has_level2_issues = any(
        report["level2_design"][cat].get("issues")
        or report["level2_design"][cat].get("suggestions")
        for cat in report["level2_design"]
    )
    if has_level1_issues or has_level2_issues:
        result["systemMessage"] = report_text

    print(json.dumps(result))


if __name__ == "__main__":
    main()
