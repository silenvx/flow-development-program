/**
 * Flow関連の共通定数モジュール。
 *
 * Why:
 *   フロー関連の定数が複数ファイルで重複定義されていると、
 *   変更時の不整合が発生する。共通定数を一箇所にまとめる。
 *
 * What:
 *   - OPTIONAL_PHASES: スキップ可能なフェーズ
 *   - PHASE_DEPENDENCIES: フェーズ間の依存関係
 *   - REQUIRED_PHASE_TRANSITIONS: 必須フェーズ遷移
 *   - BLOCKING_PHASE_TRANSITIONS: ブロック対象の遷移
 *
 * Remarks:
 *   - 定数定義のみ（フックではない）
 *   - flow-state-updater.ts、flow-verifier.tsから共有
 *   - Python版: flow_constants.py からの移行
 *
 * Changelog:
 *   - silenvx/dekita#1352: 重複定義解消のため作成
 *   - silenvx/dekita#1359: PHASE_DEPENDENCIES追加
 *   - silenvx/dekita#1690: BLOCKING_PHASE_TRANSITIONS追加
 *   - silenvx/dekita#1728: REQUIRED_PHASE_TRANSITIONS追加
 *   - silenvx/dekita#3142: TypeScriptに移植
 */

// =============================================================================
// Phase Constants
// =============================================================================

/**
 * Optional phases that can be skipped without violating order.
 * Used by: flow-state-updater.ts, flow-verifier.ts
 */
export const OPTIONAL_PHASES = new Set([
  "worktree_create", // When working on main
  "local_ai_review", // When skipping local review
  "issue_work", // Not always needed
  "production_check", // Not always needed
]);

/**
 * Phase dependencies for flexible order checking.
 * Key = phase, Value = set of phases that should appear before (at least one)
 * Empty set = no strict dependency
 *
 * Issue #1359: Phase dependencies for flexible order checking
 * Issue #1257: Relaxed implementation dependencies
 * - pre_commit_check, pr_create no longer require implementation
 * - Reason: Session resume, doc-only changes, small fixes may skip implementation
 *
 * Used by: flow-verifier.ts
 */
export const PHASE_DEPENDENCIES: Record<string, Set<string>> = {
  session_start: new Set(), // No dependency
  pre_check: new Set(), // Can happen anytime early
  issue_work: new Set(), // Can happen anytime
  worktree_create: new Set(["session_start"]), // After session starts
  implementation: new Set(), // Can start implementation anytime
  pre_commit_check: new Set(), // No strict dependency (session resume, doc changes)
  local_ai_review: new Set(), // Optional, no strict requirement
  pr_create: new Set(), // No strict dependency (session resume, doc changes)
  ci_review: new Set(["pr_create"]), // After PR creation
  merge: new Set(["pr_create"]), // After PR creation (CI implied)
  cleanup: new Set(), // Can happen anytime (not just after merge)
  production_check: new Set(), // Optional, no strict requirement
  session_end: new Set(), // No strict dependency
};

/**
 * Required phase transitions (all phases that have a mandatory next step).
 * Key = from_phase, Value = required_next_phase
 * - Violations are logged as warnings
 * - Only transitions in BLOCKING_PHASE_TRANSITIONS are blocked
 *
 * Issue #1728: Required phase transitions
 * Moved from flow-state-updater.py for centralized management
 */
export const REQUIRED_PHASE_TRANSITIONS: Record<string, string> = {
  session_start: "pre_check", // Must check before implementation
  implementation: "pre_commit_check", // Must verify before commit
  merge: "cleanup", // Must cleanup after merge
};

/**
 * Blocking phase transitions.
 * Subset of REQUIRED_PHASE_TRANSITIONS that should BLOCK (not just warn)
 * Key = from_phase, Value = required_next_phase
 *
 * Issue #1690, #1716, #1728
 */
export const BLOCKING_PHASE_TRANSITIONS: Record<string, string> = {
  merge: "cleanup", // Must cleanup after merge to avoid orphaned worktrees/branches
};

/**
 * Allowed loopbacks that bypass BLOCKING_PHASE_TRANSITIONS.
 * These are legitimate workflow patterns that should not be blocked.
 * Key = [from_phase, to_phase]
 *
 * Issue #1739
 */
export const ALLOWED_LOOPBACKS = new Set<`${string},${string}`>([
  // Rebase after BEHIND state requires returning to ci_review for CI re-run
  "merge,ci_review",
  // Issue #2153: CI failure or review comments after merge require implementation loop
  "merge,implementation",
]);

/**
 * Helper to check if a transition is in ALLOWED_LOOPBACKS.
 */
export function isAllowedLoopback(fromPhase: string, toPhase: string): boolean {
  return ALLOWED_LOOPBACKS.has(`${fromPhase},${toPhase}` as `${string},${string}`);
}

/**
 * ALL_PHASES dynamically generated from PHASE_DEPENDENCIES.
 * This ensures consistency - adding a phase to PHASE_DEPENDENCIES automatically
 * includes it in ALL_PHASES.
 *
 * Issue #1728
 */
export const ALL_PHASES = new Set(Object.keys(PHASE_DEPENDENCIES));

// =============================================================================
// Critical Violations
// =============================================================================

/**
 * Generate CRITICAL_VIOLATIONS from BLOCKING_PHASE_TRANSITIONS.
 *
 * For each phase that requires a specific next phase (blocking),
 * block all other transitions.
 *
 * Issue #1728: Changed from REQUIRED_NEXT_PHASES to BLOCKING_PHASE_TRANSITIONS
 * Issue #1739: ALLOWED_LOOPBACKS are excluded from violations.
 * These represent legitimate workflow patterns like rebase after BEHIND.
 */
function generateCriticalViolations(): Map<string, string> {
  const violations = new Map<string, string>();

  for (const [fromPhase, requiredNext] of Object.entries(BLOCKING_PHASE_TRANSITIONS)) {
    for (const toPhase of ALL_PHASES) {
      if (toPhase !== requiredNext && toPhase !== fromPhase) {
        // Issue #1739: Skip allowed loopbacks
        if (isAllowedLoopback(fromPhase, toPhase)) {
          continue;
        }
        const key = `${fromPhase},${toPhase}`;
        const message =
          `${requiredNext} skipped - must complete ${requiredNext} ` +
          `before transitioning to ${toPhase}`;
        violations.set(key, message);
      }
    }
  }

  return violations;
}

/**
 * Critical violations map.
 * Key format: "from_phase,to_phase"
 * Value: violation message
 */
export const CRITICAL_VIOLATIONS = generateCriticalViolations();

/**
 * Check if a transition is a critical violation.
 *
 * @param fromPhase - The phase transitioning from
 * @param toPhase - The phase transitioning to
 * @returns Violation message if violation, undefined otherwise
 */
export function getCriticalViolation(fromPhase: string, toPhase: string): string | undefined {
  return CRITICAL_VIOLATIONS.get(`${fromPhase},${toPhase}`);
}
