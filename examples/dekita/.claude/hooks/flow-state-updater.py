#!/usr/bin/env python3
# hook-pattern-check: skip (event["tool_result"]はログキーへの書き込みであり、input_dataからの読み取りではない)
"""開発ワークフローのフェーズ遷移を追跡する。

Why:
    ワークフロー（session_start→実装→PR→マージ→cleanup）のフェーズを
    追跡し、スキップや逸脱を検出する。データ分析でボトルネックの特定や
    改善ポイントの発見に活用する。

What:
    - 全ツール使用イベントでフェーズ遷移を判定
    - ループ（CI失敗→実装に戻る等）の検出と記録
    - 重大違反（merge後cleanup未実施など）をブロック
    - 軽微な違反は警告のみ

State:
    writes: .claude/state/flow/state-{session}.json
    writes: .claude/state/flow/events-{session}.jsonl

Remarks:
    - フェーズ遷移はPHASE_TRIGGERSのパターンマッチで検出
    - セッション固有のステートファイルで他セッションとの干渉を防止

Changelog:
    - silenvx/dekita#720: フック追加
    - silenvx/dekita#1309: 必須フェーズ遷移の検証追加
    - silenvx/dekita#1631: 外部PR検出機能追加
    - silenvx/dekita#1690: Critical違反のブロック機能追加
    - silenvx/dekita#2567: マージ済みPRの自動検出追加
"""

import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Issue #769: Added extract_input_context for hook_type inference
# Issue #1690: Added make_block_result for critical violation blocking
# Issue #1842: Added get_tool_result for standardized PostToolUse result extraction
# Issue #1352: Import shared constants to avoid duplication
# Issue #1690: Added CRITICAL_VIOLATIONS for blocking logic
# Issue #1728: Added REQUIRED_PHASE_TRANSITIONS, BLOCKING_PHASE_TRANSITIONS
from common import FLOW_LOG_DIR
from flow_constants import (
    BLOCKING_PHASE_TRANSITIONS,
    CRITICAL_VIOLATIONS,
    OPTIONAL_PHASES,
    REQUIRED_PHASE_TRANSITIONS,
)
from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.input_context import extract_input_context
from lib.results import make_block_result
from lib.session import create_hook_context, parse_hook_input


def get_state_file(session_id: str) -> Path:
    """Get state file path for a specific session.

    Issue #734: Separate state files per session to prevent cross-session interference.
    """
    return FLOW_LOG_DIR / f"state-{session_id}.json"


# Phase definitions
PHASES = [
    "session_start",
    "pre_check",
    "worktree_create",
    "implementation",
    "pre_commit_check",
    "local_ai_review",
    "pr_create",
    "issue_work",
    "ci_review",
    "merge",
    "cleanup",
    "production_check",
    "session_end",
]

# Phase transition triggers
PHASE_TRIGGERS = {
    "session_start": {
        "enter": {"hook_type": "SessionStart"},
        "exit_next": "pre_check",
    },
    "pre_check": {
        "enter": {"tools": ["Read", "Grep", "Glob"]},
        "exit_pattern": r"git worktree add",
        "exit_next": "worktree_create",
    },
    "worktree_create": {
        "enter_pattern": r"git worktree add",
        "exit_pattern": r"git worktree add.*succeeded|Preparing worktree",
        "exit_next": "implementation",
    },
    "implementation": {
        "enter": {"tools": ["Edit", "Write"]},
        "exit_pattern": r"git commit",
        "exit_next": "pre_commit_check",
        # Issue #2153: CI失敗やレビューコメント時に実装に戻るフェーズを拡大
        # 以前はci_reviewのみだったが、他のフェーズからもループが発生する
        "loop_from": ["ci_review", "pr_create", "local_ai_review", "pre_commit_check", "merge"],
    },
    "pre_commit_check": {
        "enter_pattern": r"git add|git commit",
        # Pattern matches git commit success output: [branch abc123] message
        # Avoid overly broad \[.*\] which matches any bracketed output
        "exit_pattern": r"git commit.*succeeded|\[[\w/-]+\s+[a-f0-9]{7,}\]",
        "exit_next": "local_ai_review",
    },
    "local_ai_review": {
        "enter_pattern": r"codex review",
        "exit_next": "pr_create",
    },
    "pr_create": {
        "enter_pattern": r"gh pr create",
        "exit_pattern": r"github\.com.*pull",
        "exit_next": "ci_review",
    },
    "issue_work": {
        "enter_pattern": r"gh issue (create|edit|comment)",
    },
    "ci_review": {
        # Issue #1678: Removed "gh pr create" from enter_pattern
        # PR creation should enter pr_create first, then transition to ci_review via exit_pattern
        # Only git push should directly enter ci_review (for existing PRs)
        "enter_pattern": r"git push",
        # Issue #1784: Changed from "gh pr merge" to match on success only
        # This prevents entering merge phase when merge command is blocked/fails
        # Pattern matches specific success phrases from gh pr merge output:
        # - "✔ Merged pull request" or "Merged pull request"
        # - "has been merged" (as standalone phrase)
        # - "successfully merged" with word boundary to avoid "unsuccessfully merged"
        # Note: "merged into" alone is too broad and matches failure messages
        # like "cannot be merged into" or "failed to be merged into"
        "exit_pattern": r"✔?\s*Merged pull request|\bhas been merged\b|\bsuccessfully merged\b",
        "exit_next": "merge",
    },
    "merge": {
        # Issue #1784: Removed enter_pattern to prevent premature phase transition
        # Merge phase is now entered only via ci_review.exit_pattern (on success)
        # Previously: enter_pattern: r"gh pr merge"
        "exit_pattern": r"Merged|merged",
        "exit_next": "cleanup",
    },
    "cleanup": {
        "enter_pattern": r"git worktree remove|git branch -d",
        "exit_next": "session_end",
    },
    "session_end": {
        "enter": {"hook_type": "Stop"},
    },
}

# Issue #1728: REQUIRED_PHASE_TRANSITIONS now imported from flow_constants.py
# See flow_constants.py for the full set of required transitions.

# Issue #1352: OPTIONAL_PHASES is now imported from flow_constants.py

# Issue #1631: Phases that require pr_create but may have external PRs
PHASES_REQUIRING_PR = {"ci_review", "merge"}


def check_external_pr_exists(branch: str) -> dict | None:
    """Check if a PR exists for the given branch (created by another session).

    Issue #1631: When entering ci_review/merge without pr_create phase,
    check if a PR was created externally (by another session).

    Args:
        branch: Branch name to check for open PRs.

    Returns:
        Dict with PR info if found, None otherwise.
        Example: {"number": 123, "url": "https://github.com/..."}
    """
    import subprocess

    try:
        # Check for open PRs with this branch as head
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--head", branch, "--json", "number,url"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            prs = json.loads(result.stdout)
            if prs:
                return {"number": prs[0]["number"], "url": prs[0]["url"]}
    except Exception:
        pass  # Best effort - network issues should not break the hook
    return None


def check_merged_pr_for_workflow(workflow: str) -> dict | None:
    """Check if a merged PR exists for the given workflow.

    Issue #2567: When entering cleanup without merge phase recorded,
    check if a PR was merged (e.g., during session compact).
    This allows auto-completion of the merge phase.

    Args:
        workflow: Workflow name (e.g., "issue-123").

    Returns:
        Dict with PR info if merged PR found, None otherwise.
        Example: {"number": 123, "url": "https://github.com/...", "state": "MERGED"}
    """
    import subprocess

    # Extract issue number from workflow name
    match = re.search(r"issue-(\d+)", workflow)
    if not match:
        return None

    issue_number = match.group(1)

    try:
        # Search for merged PRs by branch name pattern (feat/issue-XXX, fix/issue-XXX, etc.)
        # Issue #2577: Use --search with head: prefix for partial matching
        # (--head requires exact match, which fails for branches like fix/issue-123-description)
        branch_prefixes = [
            f"feat/issue-{issue_number}",
            f"fix/issue-{issue_number}",
            f"issue-{issue_number}",
        ]
        for branch_prefix in branch_prefixes:
            result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "merged",
                    "--search",
                    f"head:{branch_prefix}",
                    "--json",
                    "number,url,state,mergedAt",
                    "--limit",
                    "1",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                prs = json.loads(result.stdout)
                if prs:
                    return {
                        "number": prs[0]["number"],
                        "url": prs[0]["url"],
                        "state": prs[0].get("state", "MERGED"),
                        "merged_at": prs[0].get("mergedAt"),
                    }
    except Exception:
        pass  # Best effort - network issues should not break the hook
    return None


# Issue #1369: Phases where Read/Grep/Glob should NOT trigger pre_check transition
# During active work, reading code is normal and shouldn't reset to pre_check
ACTIVE_WORK_PHASES = {
    "implementation",  # Reading code during implementation is normal
    "pre_commit_check",  # Reading code during commit prep is normal
    "local_ai_review",  # Reading during review is normal
    "pr_create",  # Reading code during PR creation is normal
    "issue_work",  # Reading code during issue work is normal
    "ci_review",  # Reading during CI review is normal
    "merge",  # Reading during merge is normal
    "cleanup",  # Reading during cleanup is normal
}

# Issue #1363: Phases after successful merge where ci_review should NOT be re-entered
# Only cleanup and session_end are truly post-merge phases.
# The "merge" phase itself is excluded because:
# - A failed merge attempt (e.g., branch behind) leaves workflow in "merge" phase
# - Subsequent git push to update the PR should return to ci_review for new CI run
POST_MERGE_PHASES = {
    "cleanup",  # Cleaning up after successful merge
    "session_end",  # Session ending
}


def is_valid_phase_transition(current_phase: str, new_phase: str) -> tuple[bool, str | None]:
    """Check if phase transition is valid according to required order (Issue #1309).

    Returns:
        (is_valid, violation_reason) - if not valid, reason explains why.

    Issue #1739: ALLOWED_LOOPBACKS are treated as valid but still return a violation
    message for logging purposes.

    Issue #1874: Transition to session_start is always valid regardless of current phase.
    This prevents noise from session continuations where previous session ended in a
    different phase.
    """
    from flow_constants import ALLOWED_LOOPBACKS

    # Loop back to same phase is always valid
    if current_phase == new_phase:
        return True, None

    # Issue #1874: Transition to session_start is always valid
    # New session can start from any previous phase state
    if new_phase == "session_start":
        return True, None

    # Check required transitions
    if current_phase in REQUIRED_PHASE_TRANSITIONS:
        required_next = REQUIRED_PHASE_TRANSITIONS[current_phase]
        # If target is the required next phase, it's valid
        if new_phase == required_next:
            return True, None
        # Issue #1739: If target is an allowed loopback, allow but log violation
        # This supports rebase workflows where merge -> ci_review is legitimate
        if (current_phase, new_phase) in ALLOWED_LOOPBACKS:
            return True, (
                f"Phase '{current_phase}' must transition to '{required_next}' before '{new_phase}'"
            )
        # If target is an optional phase, allow but log violation (Issue #1345)
        # This prevents optional phases from silently bypassing required transitions
        if new_phase in OPTIONAL_PHASES:
            return True, (
                f"Required phase '{required_next}' bypassed by optional phase '{new_phase}'"
            )
        # Otherwise, it's a violation
        return False, (
            f"Phase '{current_phase}' must transition to '{required_next}' before '{new_phase}'"
        )

    return True, None


# Loop triggers - patterns that indicate returning to a previous phase
LOOP_TRIGGERS = {
    "ci_failed": [r"CI failed", r"check failed", r"workflow failed", r"Build failed"],
    "review_comment": [r"copilot", r"codex", r"review comment", r"comment\(s\)"],
    "lint_error": [r"lint", r"ruff", r"biome", r"eslint", r"Lint error"],
    "test_failed": [r"test failed", r"FAILED", r"AssertionError"],
    "type_error": [r"typecheck", r"TypeError", r"type error"],
    "merge_conflict": [r"conflict", r"CONFLICT"],
}


def infer_tool_result(hook_input: dict) -> str | None:
    """Infer tool execution result from PostToolUse hook input.

    Issue #769: Adds tool_result field for better log analysis.
    Issue #1842: Uses get_tool_result() for standardized extraction.

    Returns:
        "success", "failure", "blocked", or None if not PostToolUse.
    """
    # Issue #1842: Use standardized helper for tool result extraction
    tool_output = get_tool_result(hook_input)
    if tool_output is None:
        # Not a PostToolUse hook
        return None

    output_str = str(tool_output)

    # Check for blocked patterns
    if "Hook PreToolUse:" in output_str and "denied" in output_str:
        return "blocked"

    # Check for failure patterns
    failure_patterns = [
        "error:",
        "Error:",
        "ERROR:",
        "failed",
        "Failed",
        "FAILED",
        "Exit code 1",
        "fatal:",
        "Fatal:",
    ]
    for pattern in failure_patterns:
        if pattern in output_str:
            return "failure"

    return "success"


def ensure_log_dir():
    """Ensure flow log directory exists.

    Issue #1723: Create log directory automatically at startup
    to prevent failures when reading/writing flow state files.
    """
    FLOW_LOG_DIR.mkdir(parents=True, exist_ok=True)


def get_current_workflow(hook_input: dict | None = None) -> str:
    """Detect current workflow from worktree or branch.

    Issue #1365: When a cleanup command targets a specific worktree,
    detect the workflow from the command target, not from cwd.
    This fixes false "cleanup skipped" warnings when running cleanup
    from the main repository directory.

    Args:
        hook_input: Hook から渡される入力辞書。`tool_input` キーに `command`
            が含まれる場合、そこからワークフロー情報を抽出します。None の場合は、
            現在の作業ディレクトリ (cwd) に基づく検出にフォールバックします。

    Returns:
        検出されたワークフロー名（例: ``"issue-123"``）または ``"unknown"``。
    """
    # Issue #1365: Check if this is a cleanup command targeting a specific worktree
    # Example: git worktree remove /path/to/.worktrees/issue-123
    if hook_input:
        command = hook_input.get("tool_input", {}).get("command", "")
        # Match worktree remove command with absolute or relative path to .worktrees/
        # - Use .*? (non-greedy) to minimize backtracking when .worktrees is not found
        # - Allow optional flags like -f/--force between 'remove' and the path
        # - Handle paths with spaces (e.g., /Users/John Doe/.worktrees/)
        # - Handle Windows-style backslash paths
        worktree_match = re.search(
            r"git\s+worktree\s+remove\s+(?:--?\w+\s+)*.*?\.worktrees[/\\]([^/\\\s\"']+)", command
        )
        if worktree_match:
            return worktree_match.group(1)

    cwd = os.getcwd()

    # From worktree path
    if "/.worktrees/" in cwd:
        match = re.search(r"\.worktrees/([^/]+)", cwd)
        if match:
            return match.group(1)

    # From branch name
    try:
        import subprocess

        result = subprocess.run(
            ["git", "branch", "--show-current"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            if match := re.search(r"issue-(\d+)", branch):
                return f"issue-{match.group(1)}"
            if branch in ["main", "master"]:
                return "main"
            return branch
    except Exception:
        pass  # Best effort - git command may fail

    return "unknown"


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


def save_state(state: dict, session_id: str):
    """Save state to session-specific state file.

    Issue #734: Each session has its own state file.
    """
    try:
        ensure_log_dir()
        state_file = get_state_file(session_id)
        state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    except OSError:
        pass  # Best effort - state save may fail


def log_event(event: dict):
    """Append event to session-specific events file.

    Issue #1831: Changed from single events.jsonl to per-session files
    (events-{session_id}.jsonl) to prevent file growth and enable
    session-level analysis.
    """
    try:
        ensure_log_dir()
        event["ts"] = datetime.now(UTC).isoformat()

        # Issue #1831: Use session-specific events file
        session_id = event.get("session_id", "unknown")
        events_file = get_events_file(session_id)

        with events_file.open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass  # Best effort - event logging may fail


# Issue #1280: Cleanup configuration
CLEANUP_MAX_AGE_HOURS = 24  # Delete state files older than 24 hours
CLEANUP_FREQUENCY = 10  # Run cleanup every N hook executions


def get_events_file(session_id: str) -> Path:
    """Get events file path for a specific session.

    Issue #1831: Separate events files per session to prevent file growth
    and enable session-level analysis.
    """
    return FLOW_LOG_DIR / f"events-{session_id}.jsonl"


def cleanup_old_session_files() -> int:
    """Delete session files older than CLEANUP_MAX_AGE_HOURS.

    Issue #1280: Prevents state file accumulation by periodically removing
    old session state files that are no longer needed.

    Issue #1831: Also deletes old events-*.jsonl files.

    Returns:
        Number of files successfully deleted. Returns 0 if the directory
        doesn't exist or if an error occurs during cleanup.
    """
    try:
        if not FLOW_LOG_DIR.exists():
            return 0

        now = datetime.now(UTC)
        max_age = timedelta(hours=CLEANUP_MAX_AGE_HOURS)
        deleted_count = 0

        # Issue #1831: Clean up both state and events files
        patterns = ["state-*.json", "events-*.jsonl"]
        for pattern in patterns:
            for session_file in FLOW_LOG_DIR.glob(pattern):
                try:
                    # Check file modification time (use UTC for consistency)
                    mtime = datetime.fromtimestamp(session_file.stat().st_mtime, tz=UTC)
                    if now - mtime > max_age:
                        session_file.unlink()
                        deleted_count += 1
                except FileNotFoundError:
                    # TOCTOU: File was deleted by another process between glob and unlink
                    pass
                except OSError:
                    # Other I/O errors (permission denied, etc.)
                    pass

        return deleted_count
    except OSError:
        return 0


def cleanup_session_state(session_id: str) -> bool:
    """Delete state file for a specific session.

    Issue #1280: Called when session ends to clean up immediately.

    Returns:
        True if file was deleted, False otherwise.
    """
    try:
        state_file = get_state_file(session_id)
        if state_file.exists():
            state_file.unlink()
            return True
    except OSError:
        pass  # File may have been deleted by another process
    return False


def detect_phase_transition(
    current_phase: str, hook_input: dict, state: dict
) -> tuple[str | None, str | None, str | None, str | None]:
    """Detect if a phase transition should occur.

    Issue #769: Now also returns transition_reason for better debugging.
    Issue #1309: Added violation_reason for order violation detection.

    Returns:
        (new_phase, loop_reason, transition_reason, violation_reason)
        or (None, None, None, None) if no transition.
        - new_phase: The target phase to transition to
        - loop_reason: Why we're looping back (e.g., "ci_failed", "review_comment")
        - transition_reason: What triggered the transition (e.g., "exit_pattern: gh pr merge")
        - violation_reason: If order was violated (Issue #1309)
    """
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    tool_output = hook_input.get("tool_output", "")
    # Issue #769: Infer hook_type if not present
    hook_type = hook_input.get("hook_type", "")
    if not hook_type:
        context = extract_input_context(hook_input)
        hook_type = context.get("hook_type", "")

    def check_and_return(
        new_phase: str | None, loop_reason: str | None, transition_reason: str | None
    ) -> tuple[str | None, str | None, str | None, str | None]:
        """Helper to check order violation before returning transition."""
        if new_phase:
            is_valid, violation = is_valid_phase_transition(current_phase, new_phase)
            return (new_phase, loop_reason, transition_reason, violation)
        return (None, None, None, None)

    # Check for loop triggers first
    output_str = str(tool_output)
    for reason, patterns in LOOP_TRIGGERS.items():
        for pattern in patterns:
            if re.search(pattern, output_str, re.IGNORECASE):
                # Check if any phase has loop_from containing current_phase
                for target_phase, config in PHASE_TRIGGERS.items():
                    if "loop_from" in config and current_phase in config["loop_from"]:
                        return check_and_return(target_phase, reason, f"loop_trigger: {pattern}")

    # Check for phase exit
    trigger = PHASE_TRIGGERS.get(current_phase, {})
    if "exit_pattern" in trigger:
        command = tool_input.get("command", "")
        if re.search(trigger["exit_pattern"], command + output_str, re.IGNORECASE):
            return check_and_return(
                trigger.get("exit_next"), None, f"exit_pattern: {trigger['exit_pattern']}"
            )

    # Check for phase enter
    for phase, config in PHASE_TRIGGERS.items():
        if phase == current_phase:
            continue

        # Check hook type trigger
        if "enter" in config and "hook_type" in config["enter"]:
            if hook_type == config["enter"]["hook_type"]:
                return check_and_return(phase, None, f"hook_type: {hook_type}")

        # Check tool trigger
        if "enter" in config and "tools" in config["enter"]:
            if tool_name in config["enter"]["tools"]:
                # Issue #1369: Skip pre_check trigger during active work phases
                # Reading/searching code during implementation is normal behavior
                if phase == "pre_check" and current_phase in ACTIVE_WORK_PHASES:
                    continue
                return check_and_return(phase, None, f"tool: {tool_name}")

        # Check pattern trigger
        if "enter_pattern" in config:
            # Issue #1363: Skip ci_review trigger from post-merge phases
            # After merge, git push (for worktree sync etc.) should not re-enter ci_review
            if phase == "ci_review" and current_phase in POST_MERGE_PHASES:
                continue
            command = tool_input.get("command", "")
            if re.search(config["enter_pattern"], command, re.IGNORECASE):
                return check_and_return(phase, None, f"enter_pattern: {config['enter_pattern']}")

    return (None, None, None, None)


def get_current_branch() -> str | None:
    """Get the current git branch name.

    Issue #1631: Used for external PR detection.

    Returns:
        Branch name or None if detection fails.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass  # git command may fail in non-git directories or detached HEAD state
    return None


def update_workflow_state(
    state: dict, workflow: str, new_phase: str, loop_reason: str | None
) -> tuple[dict | None, dict | None]:
    """Update workflow state with new phase.

    Issue #1631: Also detects external PRs and auto-completes pr_create phase.
    Issue #1642: Also tracks phase_start_time to calculate duration_seconds on exit.
    Issue #2567: Also detects merged PRs and auto-completes merge phase when
                 entering cleanup without merge recorded (e.g., after session compact).

    Returns:
        Tuple of (external_pr, auto_detected_merge):
        - external_pr: PR info if external PR detected, None otherwise.
        - auto_detected_merge: Merged PR info if merge phase was auto-completed, None otherwise.
    """
    now = datetime.now(UTC)

    if workflow not in state["workflows"]:
        state["workflows"][workflow] = {
            "branch": "",
            "current_phase": new_phase,
            "phases": {},
            "phase_start_time": now.isoformat(),  # Issue #1642
        }

    wf = state["workflows"][workflow]
    old_phase = wf.get("current_phase")

    # Issue #1631: Check for external PR when entering ci_review/merge without pr_create
    external_pr = None
    if new_phase in PHASES_REQUIRING_PR and "pr_create" not in wf.get("phases", {}):
        branch = get_current_branch()
        if branch:
            external_pr = check_external_pr_exists(branch)
            if external_pr:
                # Ensure phases dict exists before assignment
                if "phases" not in wf:
                    wf["phases"] = {}
                # Auto-complete pr_create phase with external source marker
                wf["phases"]["pr_create"] = {
                    "status": "completed",
                    "iterations": 1,
                    "source": "external",
                    "pr_number": external_pr.get("number"),
                    "pr_url": external_pr.get("url"),
                }

    # Issue #2567: Auto-complete merge phase when entering cleanup without merge recorded
    # This can happen after session compact where the merge output wasn't captured
    auto_detected_merge = None
    if new_phase == "cleanup" and "merge" not in wf.get("phases", {}):
        merged_pr = check_merged_pr_for_workflow(workflow)
        if merged_pr:
            # Ensure phases dict exists before assignment
            if "phases" not in wf:
                wf["phases"] = {}
            # Auto-complete merge phase with auto-detected marker
            wf["phases"]["merge"] = {
                "status": "completed",
                "iterations": 1,
                "source": "auto_detected",
                "pr_number": merged_pr.get("number"),
                "pr_url": merged_pr.get("url"),
                "merged_at": merged_pr.get("merged_at"),
            }
            auto_detected_merge = merged_pr

    # Update phase status
    if old_phase and old_phase != new_phase:
        if old_phase not in wf["phases"]:
            wf["phases"][old_phase] = {"status": "completed", "iterations": 1}
        else:
            wf["phases"][old_phase]["status"] = "completed"

    # Handle new phase
    if new_phase not in wf["phases"]:
        wf["phases"][new_phase] = {"status": "in_progress", "iterations": 1}
    else:
        if loop_reason:
            wf["phases"][new_phase]["iterations"] += 1
            if "loop_reasons" not in wf["phases"][new_phase]:
                wf["phases"][new_phase]["loop_reasons"] = []
            wf["phases"][new_phase]["loop_reasons"].append(loop_reason)
        wf["phases"][new_phase]["status"] = "in_progress"

    wf["current_phase"] = new_phase
    # Issue #1642: Update phase start time for new phase
    wf["phase_start_time"] = now.isoformat()
    state["active_workflow"] = workflow

    return external_pr, auto_detected_merge


def main():
    """Main hook logic."""
    # Issue #1723: 起動時にログディレクトリの存在を保証
    # Note: save_state() と log_event() も内部で ensure_log_dir() を呼び出すため、
    # ここでの呼び出しは主に読み込み操作 (load_state()) が書き込み操作より先に
    # 実行される場合の事前準備として機能する。
    try:
        ensure_log_dir()
    except OSError:
        pass  # Best effort - if log dir creation fails, continue without logging

    # Read hook input
    hook_input = parse_hook_input()

    ctx = create_hook_context(hook_input)

    # Issue #1680: Infer hook_type early to distinguish Stop hooks from recursive calls
    hook_type = hook_input.get("hook_type", "") or extract_input_context(hook_input).get(
        "hook_type", ""
    )

    # Skip recursive tool calls during Stop hook to prevent infinite loop
    # But allow Stop hooks to proceed - they need to record session_end phase
    if hook_input.get("stop_hook_active") and hook_type != "Stop":
        print(json.dumps({"decision": "approve"}))
        return

    # Get session ID first - this determines which state file to use
    # Issue #734: Each session has its own state file
    # Issue #777: Claude Code provides unique session_id per conversation via hook input
    session_id = (
        ctx.get_session_id() or f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    )

    # Get current workflow and state
    # Issue #1365: Pass hook_input to detect workflow from cleanup command target
    workflow = get_current_workflow(hook_input)
    state = load_state(session_id)

    # Get current phase, initializing workflow with session_start if new
    current_phase = "session_start"
    is_new_workflow = workflow not in state.get("workflows", {})
    if is_new_workflow:
        # Initialize new workflow with session_start phase
        # Issue #1642: Include phase_start_time for duration tracking
        state["workflows"][workflow] = {
            "branch": "",
            "current_phase": "session_start",
            "phases": {"session_start": {"status": "in_progress", "iterations": 1}},
            "phase_start_time": datetime.now(UTC).isoformat(),
        }
        state["active_workflow"] = workflow
    else:
        current_phase = state["workflows"][workflow].get("current_phase", "session_start")

    # Detect phase transition
    # Issue #769: Now also returns transition_reason
    # Issue #1309: Now also returns violation_reason for order violations
    new_phase, loop_reason, transition_reason, violation_reason = detect_phase_transition(
        current_phase, hook_input, state
    )

    # Issue #769: hook_type already inferred at function start (Issue #1680)

    # Issue #769: Infer tool execution result (for PostToolUse hooks)
    tool_result = infer_tool_result(hook_input)

    # Log event
    # Issue #769: Include hook_type (inferred), tool_result, and transition_reason
    event = {
        "session_id": state.get("session_id", ""),
        "workflow": workflow,
        "event": "hook_fired",
        "hook_type": hook_type,
        "tool_name": hook_input.get("tool_name", ""),
        "current_phase": current_phase,
    }

    # Issue #769: Add tool_result only if present (PostToolUse only)
    if tool_result:
        event["tool_result"] = tool_result

    if new_phase and new_phase != current_phase:
        event["event"] = "phase_transition"
        event["new_phase"] = new_phase
        if loop_reason:
            event["loop_reason"] = loop_reason
        # Issue #769: Add transition_reason to understand why phase changed
        if transition_reason:
            event["transition_reason"] = transition_reason
        # Issue #1309: Add violation_reason if order was violated
        if violation_reason:
            event["violation_reason"] = violation_reason
        # Issue #1642: Calculate duration_seconds for the exiting phase
        if workflow in state.get("workflows", {}):
            wf = state["workflows"][workflow]
            phase_start = wf.get("phase_start_time")
            if phase_start:
                try:
                    start_time = datetime.fromisoformat(phase_start)
                    now = datetime.now(UTC)
                    duration = (now - start_time).total_seconds()
                    event["duration_seconds"] = round(duration, 2)
                except (ValueError, TypeError):
                    pass  # Invalid timestamp format, skip duration

    log_event(event)

    # Issue #1690: Check for critical violations BEFORE updating state
    # This prevents the state from being stuck in an invalid phase after blocking
    # SKIP_FLOW_VIOLATION_CHECK=1 bypasses all violation checks
    if os.environ.get("SKIP_FLOW_VIOLATION_CHECK") != "1":
        if violation_reason and new_phase:
            violation_key = (current_phase, new_phase)

            if violation_key in CRITICAL_VIOLATIONS:
                # Block critical violations WITHOUT updating state
                # This allows the user to perform the required cleanup from current_phase
                critical_reason = CRITICAL_VIOLATIONS[violation_key]
                # Issue #1728: Use BLOCKING_PHASE_TRANSITIONS for blocking violations
                required_phase = BLOCKING_PHASE_TRANSITIONS.get(current_phase, "cleanup")

                # Save state WITHOUT the invalid transition
                state["global"]["hooks_fired_total"] = (
                    state["global"].get("hooks_fired_total", 0) + 1
                )
                save_state(state, session_id)

                result = make_block_result(
                    "flow-state-updater",
                    f"Critical workflow violation: {violation_reason}\n\n"
                    f"Reason: {critical_reason}\n\n"
                    f"Please complete the '{required_phase}' phase before proceeding.",
                    ctx,
                )
                log_hook_execution(
                    "flow-state-updater",
                    "block",
                    f"Critical violation: {violation_key}",
                    {"violation_reason": violation_reason, "critical_reason": critical_reason},
                )
                print(json.dumps(result))
                return

    # Always increment hook count
    state["global"]["hooks_fired_total"] = state["global"].get("hooks_fired_total", 0) + 1

    # Issue #1280: Periodic cleanup of old state files
    # Run cleanup every CLEANUP_FREQUENCY hook executions to minimize I/O
    if state["global"]["hooks_fired_total"] % CLEANUP_FREQUENCY == 0:
        cleanup_old_session_files()

    # Update state if phase changed
    if new_phase and new_phase != current_phase:
        external_pr, auto_detected_merge = update_workflow_state(
            state, workflow, new_phase, loop_reason
        )

        # Issue #1631: Log external PR detection
        if external_pr:
            log_event(
                {
                    "session_id": state.get("session_id", ""),
                    "workflow": workflow,
                    "event": "external_pr_detected",
                    "phase": new_phase,
                    "pr_number": external_pr.get("number"),
                    "pr_url": external_pr.get("url"),
                }
            )

        # Issue #2567: Log auto-detected merge phase
        if auto_detected_merge:
            log_event(
                {
                    "session_id": state.get("session_id", ""),
                    "workflow": workflow,
                    "event": "merge_phase_auto_detected",
                    "phase": new_phase,
                    "pr_number": auto_detected_merge.get("number"),
                    "pr_url": auto_detected_merge.get("url"),
                    "merged_at": auto_detected_merge.get("merged_at"),
                }
            )

    # Issue #1665: Always save state, even at session_end
    # This fixes the bug where flow-verifier.py couldn't read session_end phase
    # because the state file was deleted before it could run.
    #
    # Background:
    # - Multiple Stop hooks run sequentially (flow-state-updater → flow-verifier)
    # - If we delete the state file here, flow-verifier can't read the final state
    # - State file cleanup is handled by age-based cleanup (CLEANUP_MAX_AGE_HOURS)
    save_state(state, session_id)

    # Issue #1280 + #1665: Trigger cleanup at session_end
    # Since no hooks fire after session_end, we must trigger cleanup here
    # to prevent stale state files from accumulating indefinitely.
    # This runs age-based cleanup which removes files older than 24h.
    effective_phase = new_phase if new_phase else current_phase
    if effective_phase == "session_end":
        cleanup_old_session_files()

    # Issue #1690: Warn for non-critical violations (critical ones already blocked above)
    # When SKIP_FLOW_VIOLATION_CHECK=1, critical violations also reach here
    if violation_reason and new_phase:
        violation_key = (current_phase, new_phase)
        is_critical = violation_key in CRITICAL_VIOLATIONS
        skip_enabled = os.environ.get("SKIP_FLOW_VIOLATION_CHECK") == "1"

        if skip_enabled and is_critical:
            # Critical violation bypassed by SKIP_FLOW_VIOLATION_CHECK
            critical_reason = CRITICAL_VIOLATIONS[violation_key]
            message = (
                f"[flow-state-updater] BYPASS MODE: Critical violation bypassed\n"
                f"Violation: {violation_reason}\n"
                f"Normally would be BLOCKED: {critical_reason}\n"
                "Set SKIP_FLOW_VIOLATION_CHECK= to re-enable blocking."
            )
            log_action = "bypass"
            log_detail = f"Critical violation bypassed: {violation_key}"
        else:
            # Non-critical violation - Issue #1967: Enhanced realtime warning
            # Get expected next phase for better guidance
            expected_next = REQUIRED_PHASE_TRANSITIONS.get(current_phase, "N/A")
            message = (
                f"[flow-state-updater] ⚠️ フロー逸脱を検出:\n\n"
                f"{violation_reason}\n\n"
                f"  現在フェーズ: {current_phase}\n"
                f"  遷移先: {new_phase}\n"
                f"  推奨フェーズ: {expected_next}\n\n"
                "💡 AGENTS.mdの開発フローを確認してください。\n"
                "（ブロックではありません。分析用に記録されます）"
            )
            log_action = "warn"
            log_detail = f"Non-critical violation: {current_phase} -> {new_phase}"

        result = {
            "decision": "approve",
            "systemMessage": message,
        }
        log_hook_execution(
            "flow-state-updater",
            log_action,
            log_detail,
            {"violation_reason": violation_reason, "is_critical": is_critical},
        )
        print(json.dumps(result))
        return

    # No violation - approve normally
    print(json.dumps({"decision": "approve"}))


if __name__ == "__main__":
    main()
