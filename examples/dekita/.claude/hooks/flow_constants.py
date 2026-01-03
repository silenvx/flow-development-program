#!/usr/bin/env python3
"""Flow関連の共通定数モジュール。

Why:
    フロー関連の定数が複数ファイルで重複定義されていると、
    変更時の不整合が発生する。共通定数を一箇所にまとめる。

What:
    - OPTIONAL_PHASES: スキップ可能なフェーズ
    - PHASE_DEPENDENCIES: フェーズ間の依存関係
    - REQUIRED_PHASE_TRANSITIONS: 必須フェーズ遷移
    - BLOCKING_PHASE_TRANSITIONS: ブロック対象の遷移

Remarks:
    - 定数定義のみ（フックではない）
    - flow-state-updater.py、flow-verifier.pyから共有

Changelog:
    - silenvx/dekita#1352: 重複定義解消のため作成
    - silenvx/dekita#1359: PHASE_DEPENDENCIES追加
    - silenvx/dekita#1690: BLOCKING_PHASE_TRANSITIONS追加
    - silenvx/dekita#1728: REQUIRED_PHASE_TRANSITIONS追加
"""

from __future__ import annotations

# Optional phases that can be skipped without violating order
# Used by: flow-state-updater.py, flow-verifier.py
OPTIONAL_PHASES = {
    "worktree_create",  # When working on main
    "local_ai_review",  # When skipping local review
    "issue_work",  # Not always needed
    "production_check",  # Not always needed
}

# Issue #1359: Phase dependencies for flexible order checking
# Key = phase, Value = set of phases that should appear before (at least one)
# Empty set = no strict dependency
# Used by: flow-verifier.py
#
# Issue #1257: Relaxed implementation dependencies
# - pre_commit_check, pr_create no longer require implementation
# - Reason: Session resume, doc-only changes, small fixes may skip implementation
PHASE_DEPENDENCIES: dict[str, set[str]] = {
    "session_start": set(),  # No dependency
    "pre_check": set(),  # Can happen anytime early
    "issue_work": set(),  # Can happen anytime
    "worktree_create": {"session_start"},  # After session starts
    "implementation": set(),  # Can start implementation anytime
    "pre_commit_check": set(),  # No strict dependency (session resume, doc changes)
    "local_ai_review": set(),  # Optional, no strict requirement
    "pr_create": set(),  # No strict dependency (session resume, doc changes)
    "ci_review": {"pr_create"},  # After PR creation
    "merge": {"pr_create"},  # After PR creation (CI implied)
    "cleanup": set(),  # Can happen anytime (not just after merge)
    "production_check": set(),  # Optional, no strict requirement
    "session_end": set(),  # No strict dependency
}

# Issue #1728: Required phase transitions (all phases that have a mandatory next step)
# Key = from_phase, Value = required_next_phase
# - Violations are logged as warnings
# - Only transitions in BLOCKING_PHASE_TRANSITIONS are blocked
# Moved from flow-state-updater.py for centralized management
REQUIRED_PHASE_TRANSITIONS: dict[str, str] = {
    "session_start": "pre_check",  # Must check before implementation
    "implementation": "pre_commit_check",  # Must verify before commit
    "merge": "cleanup",  # Must cleanup after merge
}

# Issue #1690, #1716, #1728: Blocking phase transitions
# Subset of REQUIRED_PHASE_TRANSITIONS that should BLOCK (not just warn)
# Key = from_phase, Value = required_next_phase
BLOCKING_PHASE_TRANSITIONS: dict[str, str] = {
    "merge": "cleanup",  # Must cleanup after merge to avoid orphaned worktrees/branches
}

# Issue #1739: Allowed loopbacks that bypass BLOCKING_PHASE_TRANSITIONS
# These are legitimate workflow patterns that should not be blocked
# Key = (from_phase, to_phase)
ALLOWED_LOOPBACKS: set[tuple[str, str]] = {
    # Rebase after BEHIND state requires returning to ci_review for CI re-run
    ("merge", "ci_review"),
    # Issue #2153: CI failure or review comments after merge require implementation loop
    ("merge", "implementation"),
}

# Issue #1728: ALL_PHASES dynamically generated from PHASE_DEPENDENCIES
# This ensures consistency - adding a phase to PHASE_DEPENDENCIES automatically
# includes it in ALL_PHASES
ALL_PHASES: set[str] = set(PHASE_DEPENDENCIES.keys())


def _generate_critical_violations() -> dict[tuple[str, str], str]:
    """Generate CRITICAL_VIOLATIONS from BLOCKING_PHASE_TRANSITIONS.

    For each phase that requires a specific next phase (blocking),
    block all other transitions.

    Issue #1728: Changed from REQUIRED_NEXT_PHASES to BLOCKING_PHASE_TRANSITIONS
    Issue #1739: ALLOWED_LOOPBACKS are excluded from violations.
    These represent legitimate workflow patterns like rebase after BEHIND.
    """
    violations: dict[tuple[str, str], str] = {}

    for from_phase, required_next in BLOCKING_PHASE_TRANSITIONS.items():
        for to_phase in ALL_PHASES:
            if to_phase != required_next and to_phase != from_phase:
                # Issue #1739: Skip allowed loopbacks
                if (from_phase, to_phase) in ALLOWED_LOOPBACKS:
                    continue
                violations[(from_phase, to_phase)] = (
                    f"{required_next} skipped - must complete {required_next} "
                    f"before transitioning to {to_phase}"
                )

    return violations


CRITICAL_VIOLATIONS: dict[tuple[str, str], str] = _generate_critical_violations()
