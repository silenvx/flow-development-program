#!/usr/bin/env python3
"""フロー有効性トラッキング機能を提供する。

Why:
    ワークフローの進捗を追跡し、フローの完了状況を可視化するため。

What:
    - start_flow(): フローインスタンス開始
    - complete_flow_step(): ステップ完了記録
    - complete_flow(): フロー完了記録
    - get_flow_status(): フロー状態取得
    - get_incomplete_flows(): 未完了フロー一覧取得

State:
    - writes: .claude/logs/flows/flow-progress-{session}.jsonl

Remarks:
    - session_idは呼び出し元から渡される（HookContextパターン）
    - 重複フロー防止のためコンテキストマッチング
    - completion_stepでフロー完了判定をカスタマイズ可能

Changelog:
    - silenvx/dekita#617: フロー有効性トラッキング追加
    - silenvx/dekita#1158: セッション開始時刻対応
    - silenvx/dekita#1159: フロー完了追跡改善
    - silenvx/dekita#1758: common.pyから分離
    - silenvx/dekita#1840: セッション固有ファイル形式に変更
    - silenvx/dekita#2545: HookContextパターンに移行
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.git import get_current_branch
from lib.logging import log_to_session_file
from lib.timestamp import get_local_timestamp


def _get_flow_progress_file(flow_log_dir: Path, session_id: str) -> Path:
    """Get session-specific flow progress log file path.

    Issue #1840: Flow progress logs are now separated by session ID.

    Args:
        flow_log_dir: Directory containing flow logs.
        session_id: Claude session identifier.

    Returns:
        Path to the session-specific flow progress file.
    """
    return flow_log_dir / f"flow-progress-{session_id}.jsonl"


def _generate_flow_instance_id(session_id: str | None = None) -> str:
    """Generate a unique flow instance ID.

    Issue #2496: Added session_id parameter to avoid global state.
    Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。

    Args:
        session_id: Session ID to include in the flow instance ID.
                   If None, "unknown" is used.

    Returns:
        A unique identifier combining timestamp and session ID.
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    sid = session_id[:8] if session_id else "unknown"
    return f"{timestamp}-{sid}"


def load_flow_definitions() -> dict[str, Any]:
    """Load flow definitions from Python module.

    Returns:
        Dict of flow definitions in legacy JSON-compatible format.
        Each flow is converted via to_dict() for backward compatibility.
    """
    try:
        from flow_definitions import get_all_flow_definitions

        definitions = get_all_flow_definitions()
        # Convert to dict format for backward compatibility
        return {flow_id: flow.to_dict() for flow_id, flow in definitions.items()}
    except ImportError as e:
        sys.stderr.write(f"[flow] Warning: Failed to import flow_definitions: {e}\n")
        return {}


def _parse_flow_progress_log(
    flow_log_dir: Path,
    session_id: str | None = None,
) -> tuple[dict[str, dict], dict[str, list[str]], dict[str, dict[str, int]], set[str]]:
    """Parse flow progress log and return flow data.

    Issue #1840: Now reads from session-specific files. If session_id is provided,
    reads only that session's file. Otherwise reads from current session.

    Args:
        flow_log_dir: Directory containing flow logs.
        session_id: Session ID to read from (defaults to current session).

    Returns:
        Tuple of (flow_instances, completed_steps, step_counts, completed_flows).
    """
    flow_instances: dict[str, dict] = {}
    completed_steps: dict[str, list[str]] = {}
    step_counts: dict[str, dict[str, int]] = {}
    completed_flows: set[str] = set()

    # Issue #1840: Use session-specific file
    # Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。
    effective_session_id = session_id if session_id else "unknown"
    flow_progress_log = _get_flow_progress_file(flow_log_dir, effective_session_id)

    if not flow_progress_log.exists():
        return flow_instances, completed_steps, step_counts, completed_flows

    try:
        with open(flow_progress_log, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())

                    instance_id = entry.get("flow_instance_id")
                    if not instance_id:
                        continue

                    if entry.get("event") == "flow_started":
                        flow_instances[instance_id] = entry
                        completed_steps[instance_id] = []
                        step_counts[instance_id] = {}
                    elif entry.get("event") == "step_completed":
                        step_id = entry.get("step_id")
                        if instance_id in flow_instances and step_id:
                            # Track count for all completions
                            step_counts[instance_id][step_id] = (
                                step_counts[instance_id].get(step_id, 0) + 1
                            )
                            # Keep unique list for completion checking
                            if step_id not in completed_steps[instance_id]:
                                completed_steps[instance_id].append(step_id)
                    elif entry.get("event") == "flow_completed":
                        # Track flows that have been explicitly marked complete
                        completed_flows.add(instance_id)
                except json.JSONDecodeError:
                    continue
    except OSError:
        # Return empty data if log file can't be read (file permissions, etc.)
        pass

    return flow_instances, completed_steps, step_counts, completed_flows


def get_active_flow_for_context(
    flow_log_dir: Path,
    flow_id: str,
    context: dict[str, Any],
    session_id: str | None = None,
) -> str | None:
    """Check if there's already an active (incomplete) flow for the given context.

    Issue #1840: Now reads from session-specific file. Session filtering removed
    as files are already separated by session.

    Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。

    Args:
        flow_log_dir: Directory containing flow logs.
        flow_id: The flow type ID (e.g., "issue-ai-review")
        context: Context dict to match (e.g., {"issue_number": 123})
        session_id: Session ID for file isolation. Uses "unknown" if None.

    Returns:
        Existing flow instance ID if found, None otherwise.
    """
    # Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。
    effective_session_id = session_id if session_id else "unknown"
    flow_progress_log = _get_flow_progress_file(flow_log_dir, effective_session_id)

    if not flow_progress_log.exists():
        return None

    # Track flow instances and their completed steps
    flow_instances: dict[str, dict[str, Any]] = {}
    completed_steps: dict[str, list[str]] = {}
    completed_flows: set[str] = set()  # Issue #1159: Track flow_completed events

    try:
        with open(flow_progress_log, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())

                    instance_id = entry.get("flow_instance_id")
                    if not instance_id:
                        continue

                    if entry.get("event") == "flow_started":
                        flow_instances[instance_id] = entry
                        completed_steps[instance_id] = []
                    elif entry.get("event") == "step_completed":
                        step_id = entry.get("step_id")
                        # Note: step_completed events are only processed if the
                        # corresponding flow_started was already seen. This is
                        # correct because events are appended sequentially and
                        # the log is read in order. Out-of-order events would
                        # indicate log corruption and are safely ignored.
                        if instance_id in flow_instances and step_id:
                            if step_id not in completed_steps[instance_id]:
                                completed_steps[instance_id].append(step_id)
                    elif entry.get("event") == "flow_completed":
                        # Issue #1159: Track explicitly completed flows
                        completed_flows.add(instance_id)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None

    # Find matching active flow
    for instance_id, started_entry in flow_instances.items():
        # Issue #1159: Skip flows that are explicitly completed
        if instance_id in completed_flows:
            continue

        # Check if flow_id matches
        if started_entry.get("flow_id") != flow_id:
            continue

        # Check if context matches
        entry_context = started_entry.get("context", {})
        if entry_context != context:
            continue

        # Check if flow is still incomplete (has pending steps)
        expected = started_entry.get("expected_steps", [])
        completed = completed_steps.get(instance_id, [])
        pending = [s for s in expected if s not in completed]

        if pending:  # Still has pending steps = active flow
            return instance_id

    return None


def start_flow(
    flow_log_dir: Path,
    flow_id: str,
    context: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> str | None:
    """Start a new flow instance and return instance ID.

    Creates an entry in the flow progress log with status "started".
    If an active (incomplete) flow already exists for the same flow_id and context,
    returns the existing instance ID instead of creating a new one.

    Note:
        See complete_flow_step() for concurrency considerations.
        The duplicate check via get_active_flow_for_context() is not atomic,
        but race conditions are unlikely in single-session environments.

    Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。

    Args:
        flow_log_dir: Directory for flow log files.
        flow_id: The flow type ID (e.g., "issue-ai-review")
        context: Optional context dict (e.g., {"issue_number": 123})
        session_id: Session ID for file isolation. Uses "unknown" if None.

    Returns:
        Flow instance ID (new or existing), or None on error.
    """
    # Check for existing active flow with same context to prevent duplicates.
    # Note: Only check when context is truthy (non-None and non-empty).
    # Flows with None or empty context allow duplicates intentionally,
    # as there's no unique identifier to match them.
    if context:
        existing_id = get_active_flow_for_context(flow_log_dir, flow_id, context, session_id)
        if existing_id:
            return existing_id

    definitions = load_flow_definitions()
    if flow_id not in definitions:
        sys.stderr.write(f"[flow] Warning: Unknown flow_id '{flow_id}'\n")
        return None

    flow_def = definitions[flow_id]
    instance_id = _generate_flow_instance_id(session_id)

    try:
        flow_log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        sys.stderr.write(f"[flow] Warning: Failed to create log dir: {e}\n")
        return None

    # Extract step IDs, filtering out steps without an id field
    steps = flow_def.get("steps", [])
    expected_steps = [s.get("id") for s in steps if s.get("id")]
    if len(expected_steps) != len(steps):
        sys.stderr.write(f"[flow] Warning: Some steps in '{flow_id}' are missing 'id' field\n")

    # Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。
    # Skip session-specific logging if session_id is None
    if not session_id:
        sys.stderr.write("[flow] Warning: session_id not provided, skipping flow log\n")
        return instance_id

    entry: dict[str, Any] = {
        "timestamp": get_local_timestamp(),
        "session_id": session_id,
        "event": "flow_started",
        "flow_id": flow_id,
        "flow_instance_id": instance_id,
        "flow_name": flow_def.get("name", flow_id),
        "expected_steps": expected_steps,
        "context": context or {},
    }

    # Add branch context
    branch = get_current_branch()
    if branch:
        entry["branch"] = branch

    # Issue #1840: Write to session-specific file
    # Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。
    if not log_to_session_file(flow_log_dir, "flow-progress", session_id, entry):
        sys.stderr.write("[flow] Warning: Failed to write flow log\n")
        return None

    return instance_id


def complete_flow(
    flow_log_dir: Path,
    flow_instance_id: str,
    flow_id: str | None = None,
    session_id: str | None = None,
) -> bool:
    """Mark a flow as completed.

    Records flow completion in the flow progress log. This is called automatically
    by complete_flow_step() when all required steps are done, but can also be
    called manually if needed.

    Issue #1840: Now writes to session-specific file.
    Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。

    Args:
        flow_log_dir: Directory for flow log files.
        flow_instance_id: The flow instance ID from start_flow()
        flow_id: Optional flow ID for the log entry
        session_id: Session ID for file isolation. Uses "unknown" if None.

    Returns:
        True if recorded successfully, False on error.
    """
    # Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。
    # Skip session-specific logging if session_id is None
    if not session_id:
        sys.stderr.write("[flow] Warning: session_id not provided, skipping flow completion log\n")
        return True  # Consider success to not block caller

    entry: dict[str, Any] = {
        "timestamp": get_local_timestamp(),
        "session_id": session_id,
        "event": "flow_completed",
        "flow_instance_id": flow_instance_id,
    }

    if flow_id:
        entry["flow_id"] = flow_id

    # Issue #1840: Write to session-specific file
    return log_to_session_file(flow_log_dir, "flow-progress", session_id, entry)


def _check_and_complete_flow(
    flow_log_dir: Path,
    flow_instance_id: str,
    session_id: str | None = None,
) -> bool:
    """Check if a flow is complete and record flow_completed event if so.

    Args:
        flow_log_dir: Directory for flow log files.
        flow_instance_id: The flow instance ID
        session_id: Session ID for file isolation.

    Returns:
        True if flow was marked complete, False otherwise.
    """
    status = get_flow_status(flow_log_dir, flow_instance_id, session_id)
    if not status:
        return False

    # Skip if flow_completed event already exists (prevent duplicates)
    if status.get("has_flow_completed"):
        return False

    flow_id = status.get("flow_id")
    completed_steps = status.get("completed_steps", [])
    expected_steps = status.get("expected_steps", [])

    # Import flow definitions to check completion_step
    try:
        from flow_definitions import get_flow_definition
    except ImportError:
        get_flow_definition = None

    is_complete = False

    # Check completion via completion_step (takes priority)
    if get_flow_definition and flow_id:
        flow_def = get_flow_definition(flow_id)
        if flow_def and flow_def.completion_step:
            if flow_def.completion_step in completed_steps:
                is_complete = True

    # Fall back to all steps completed
    if not is_complete:
        pending = [s for s in expected_steps if s not in completed_steps]
        if len(pending) == 0:
            is_complete = True

    if is_complete:
        return complete_flow(flow_log_dir, flow_instance_id, flow_id, session_id)

    return False


def complete_flow_step(
    flow_log_dir: Path,
    flow_instance_id: str,
    step_id: str,
    flow_id: str | None = None,
    session_id: str | None = None,
) -> bool:
    """Mark a flow step as completed.

    Records step completion in the flow progress log.

    Issue #1840: Now writes to session-specific file with file locking.
    Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。

    Args:
        flow_log_dir: Directory for flow log files.
        flow_instance_id: The flow instance ID from start_flow()
        step_id: The step ID to mark as completed
        flow_id: Optional flow type ID for logging (e.g., "issue-work", "pr-review")
        session_id: Session ID for file isolation. Uses "unknown" if None.

    Returns:
        True if recorded successfully, False on error.
    """
    # Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。
    # Skip session-specific logging if session_id is None
    if not session_id:
        sys.stderr.write("[flow] Warning: session_id not provided, skipping step completion log\n")
        return True  # Consider success to not block caller

    entry: dict[str, Any] = {
        "timestamp": get_local_timestamp(),
        "session_id": session_id,
        "event": "step_completed",
        "flow_instance_id": flow_instance_id,
        "step_id": step_id,
        "flow_id": flow_id,
    }

    # Issue #1840: Write to session-specific file (with file locking)
    if not log_to_session_file(flow_log_dir, "flow-progress", session_id, entry):
        return False

    # Check if flow is now complete and record flow_completed event
    _check_and_complete_flow(flow_log_dir, flow_instance_id, session_id)

    return True


def get_flow_status(
    flow_log_dir: Path,
    flow_instance_id: str,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """Get the current status of a flow instance.

    Reads the flow progress log to reconstruct flow status.

    Issue #1159: Now considers flow_completed events when determining completion.
    Issue #1840: Now reads from session-specific file.
    Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。

    Args:
        flow_log_dir: Directory containing flow logs.
        flow_instance_id: The flow instance ID
        session_id: Session ID for file isolation. Uses "unknown" if None.

    Returns:
        Dict with flow status containing keys:
        - flow_id, flow_name, flow_instance_id
        - expected_steps, completed_steps, pending_steps
        - step_counts (dict mapping step_id to completion count)
        - is_complete (bool), has_flow_completed (bool), context, started_at
        Returns None if the flow instance is not found in the log,
        if the log file doesn't exist, or on read errors.
    """
    # Issue #1840: Use session-specific file
    # Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。
    effective_session_id = session_id if session_id else "unknown"

    flow_progress_log = _get_flow_progress_file(flow_log_dir, effective_session_id)

    if not flow_progress_log.exists():
        return None

    flow_started = None
    completed_steps: list[str] = []
    step_counts: dict[str, int] = {}
    has_flow_completed = False

    try:
        with open(flow_progress_log, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("flow_instance_id") == flow_instance_id:
                        if entry.get("event") == "flow_started":
                            flow_started = entry
                        elif entry.get("event") == "step_completed":
                            step_id = entry.get("step_id")
                            if step_id:
                                # Track count for all completions
                                step_counts[step_id] = step_counts.get(step_id, 0) + 1
                                # Keep unique list for completion checking
                                if step_id not in completed_steps:
                                    completed_steps.append(step_id)
                        elif entry.get("event") == "flow_completed":
                            has_flow_completed = True
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None

    if not flow_started:
        return None

    expected_steps = flow_started.get("expected_steps", [])
    pending_steps = [s for s in expected_steps if s not in completed_steps]

    # Flow is complete if:
    #   1) explicit flow_completed event exists, OR
    #   2) completion_step is defined and that step is completed, OR
    #   3) all steps are completed (when no completion_step is defined)
    if has_flow_completed:
        is_complete = True
    else:
        is_complete = False
        flow_id = flow_started.get("flow_id")
        if flow_id:
            try:
                from flow_definitions import get_flow_definition
            except ImportError:
                get_flow_definition = None
            if get_flow_definition:
                flow_def = get_flow_definition(flow_id)
                if flow_def and flow_def.completion_step:
                    is_complete = flow_def.completion_step in completed_steps
        if not is_complete:
            is_complete = len(pending_steps) == 0

    return {
        "flow_id": flow_started.get("flow_id"),
        "flow_name": flow_started.get("flow_name"),
        "flow_instance_id": flow_instance_id,
        "expected_steps": expected_steps,
        "completed_steps": completed_steps,
        "pending_steps": pending_steps,
        "step_counts": step_counts,
        "is_complete": is_complete,
        "has_flow_completed": has_flow_completed,
        "context": flow_started.get("context", {}),
        "started_at": flow_started.get("timestamp"),
    }


def get_incomplete_flows(
    flow_log_dir: Path,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Get all incomplete flows in the current session.

    Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。

    Args:
        flow_log_dir: Directory containing flow logs.
        session_id: Session ID for file isolation. Uses "unknown" if None.

    Returns:
        List of incomplete flow status dicts.
    """
    # Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。
    effective_session_id = session_id if session_id else "unknown"

    # Use shared parsing function with session_id
    flow_instances, completed_steps, step_counts, completed_flows = _parse_flow_progress_log(
        flow_log_dir, effective_session_id
    )

    # Import flow definitions to check completion_step
    try:
        from flow_definitions import get_flow_definition
    except ImportError:
        get_flow_definition = None

    # Build list of incomplete flows
    incomplete: list[dict[str, Any]] = []
    for instance_id, started_entry in flow_instances.items():
        # Skip flows that have explicit flow_completed events
        if instance_id in completed_flows:
            continue

        expected = started_entry.get("expected_steps", [])
        completed = completed_steps.get(instance_id, [])
        pending = [s for s in expected if s not in completed]

        # Check if flow is complete via completion_step
        flow_id = started_entry.get("flow_id")
        is_complete = len(pending) == 0

        if not is_complete and get_flow_definition and flow_id:
            flow_def = get_flow_definition(flow_id)
            if flow_def and flow_def.completion_step:
                # If completion_step is completed, the flow is considered complete
                if flow_def.completion_step in completed:
                    is_complete = True

        if not is_complete:  # Has pending steps = incomplete
            incomplete.append(
                {
                    "flow_id": flow_id,
                    "flow_name": started_entry.get("flow_name"),
                    "flow_instance_id": instance_id,
                    "expected_steps": expected,
                    "completed_steps": completed,
                    "pending_steps": pending,
                    "step_counts": step_counts.get(instance_id, {}),
                    "context": started_entry.get("context", {}),
                    "started_at": started_entry.get("timestamp"),
                }
            )

    return incomplete


def check_flow_completion(
    flow_log_dir: Path,
    flow_instance_id: str,
    session_id: str | None = None,
) -> bool:
    """Check if a flow is complete.

    Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。

    Args:
        flow_log_dir: Directory containing flow logs.
        flow_instance_id: The flow instance ID
        session_id: Session ID for file isolation. Uses "unknown" if None.

    Returns:
        True if all expected steps are completed, False otherwise.
    """
    status = get_flow_status(flow_log_dir, flow_instance_id, session_id)
    if not status:
        return False
    return status.get("is_complete", False)
