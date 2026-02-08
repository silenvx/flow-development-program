#!/usr/bin/env bun
/**
 * ワークフロー追跡を検証し、レポートを生成する。
 *
 * Why:
 *   フェーズ追跡が正確に機能しているか検証し、ワークフロー設計の
 *   問題点を発見する。セッション終了時にサマリーを表示することで、
 *   改善ポイントを認識できる。
 *
 * What:
 *   - Level 1: 追跡精度の検証（フェーズ遷移、ループ検出）
 *   - Level 2: フロー設計レビュー（効率性、順序、粒度、カバレッジ）
 *   - スキップされたフェーズの理由推定
 *   - 全セッション統計の集計
 *
 * State:
 *   reads: .claude/state/flow/state-{session}.json
 *   reads: .claude/state/flow/events-{session}.jsonl
 *   writes: .claude/state/flow/verification-report.json
 *
 * Remarks:
 *   - Stop hookとして発動（セッション終了時）
 *   - ブロックせず検証レポートのみ表示
 *
 * Changelog:
 *   - silenvx/dekita#720: フック追加
 *   - silenvx/dekita#1359: 依存関係ベースのフェーズ順序チェックに変更
 *   - silenvx/dekita#1627: スキップ理由の推定機能追加
 *   - silenvx/dekita#1690: 違反サマリー表示追加
 *   - silenvx/dekita#3157: TypeScriptに移行
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";
import { FLOW_LOG_DIR } from "../lib/common";
import { OPTIONAL_PHASES, PHASE_DEPENDENCIES, getCriticalViolation } from "../lib/flow_constants";
import { logHookExecution } from "../lib/logging";
import { createContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "flow-verifier";

// =============================================================================
// Helper Functions
// =============================================================================

function getEventsFile(sessionId: string): string {
  const safeSessionId = basename(sessionId);
  return join(FLOW_LOG_DIR, `events-${safeSessionId}.jsonl`);
}

function getStateFile(sessionId: string): string {
  const safeSessionId = basename(sessionId);
  return join(FLOW_LOG_DIR, `state-${safeSessionId}.json`);
}

function getReportFile(): string {
  return join(FLOW_LOG_DIR, "verification-report.json");
}

function ensureLogDir(): void {
  const flowLogDir = FLOW_LOG_DIR;
  if (!existsSync(flowLogDir)) {
    mkdirSync(flowLogDir, { recursive: true });
  }
}

// Expected phase order
const EXPECTED_PHASE_ORDER = [
  "session_start",
  "pre_check",
  "issue_work",
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
];

// Critical phases that should not be skipped
const _CRITICAL_PHASES = new Set(["pre_commit_check", "cleanup"]);

// =============================================================================
// Types
// =============================================================================

interface PhaseInfo {
  status: string;
  iterations: number;
  loop_reasons?: string[];
}

interface WorkflowData {
  current_phase?: string;
  phases?: Record<string, PhaseInfo>;
}

interface SessionState {
  session_id?: string;
  active_workflow?: string | null;
  workflows?: Record<string, WorkflowData>;
  global?: {
    hooks_fired_total?: number;
    session_start_time?: string;
  };
}

interface Event {
  session_id?: string;
  workflow?: string;
  event?: string;
  new_phase?: string;
  loop_reason?: string;
  violation_reason?: string;
  current_phase?: string;
}

interface SkipInfo {
  phase: string;
  status: string;
  reason: string;
  context: Record<string, unknown>;
}

// =============================================================================
// Data Loading
// =============================================================================

function loadEvents(sessionId: string): Event[] {
  const events: Event[] = [];
  try {
    const eventsFile = getEventsFile(sessionId);
    if (existsSync(eventsFile)) {
      const content = readFileSync(eventsFile, "utf-8");
      for (const line of content.split("\n")) {
        if (line.trim()) {
          events.push(JSON.parse(line));
        }
      }
    }
  } catch {
    // Best effort
  }
  return events;
}

function loadState(sessionId: string): SessionState {
  const stateFile = getStateFile(sessionId);
  try {
    if (existsSync(stateFile)) {
      return JSON.parse(readFileSync(stateFile, "utf-8"));
    }
  } catch {
    // Best effort
  }
  return {};
}

function loadAllSessionsWorkflows(): [Record<string, WorkflowData>, number] {
  const allWorkflows: Record<string, WorkflowData & { _file_mtime?: string }> = {};
  let sessionCount = 0;

  try {
    const flowLogDir = FLOW_LOG_DIR;
    if (!existsSync(flowLogDir)) {
      return [{}, 0];
    }

    const files = readdirSync(flowLogDir).filter(
      (f) => f.startsWith("state-") && f.endsWith(".json"),
    );

    for (const file of files) {
      const filePath = join(flowLogDir, file);
      try {
        const state: SessionState = JSON.parse(readFileSync(filePath, "utf-8"));
        sessionCount++;

        const fileMtime = new Date(statSync(filePath).mtimeMs).toISOString();

        for (const [workflowId, workflowData] of Object.entries(state.workflows ?? {})) {
          if (!allWorkflows[workflowId]) {
            allWorkflows[workflowId] = { ...workflowData, _file_mtime: fileMtime };
          } else {
            const existingUpdated =
              (allWorkflows[workflowId] as Record<string, unknown>).updated_at ??
              allWorkflows[workflowId]._file_mtime ??
              "";
            const newUpdated = (workflowData as Record<string, unknown>).updated_at ?? fileMtime;
            if (newUpdated > existingUpdated) {
              allWorkflows[workflowId] = { ...workflowData, _file_mtime: fileMtime };
            }
          }
        }
      } catch {
        // Skip corrupted files
      }
    }

    // Clean up _file_mtime
    for (const workflowData of Object.values(allWorkflows)) {
      workflowData._file_mtime = undefined;
    }
  } catch {
    return [{}, 0];
  }

  return [allWorkflows, sessionCount];
}

function rebuildStateFromEvents(events: Event[], initialSessionId = ""): SessionState {
  const state: SessionState = {
    session_id: "",
    active_workflow: null,
    workflows: {},
    global: { hooks_fired_total: 0 },
  };

  // Determine session_id
  let targetSessionId = initialSessionId;
  if (!targetSessionId && events.length > 0) {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].session_id) {
        targetSessionId = events[i].session_id!;
        break;
      }
    }
  }

  // Filter events
  const filteredEvents = targetSessionId
    ? events.filter((e) => e.session_id === targetSessionId)
    : events;

  for (const event of filteredEvents) {
    const sessionId = event.session_id ?? "";
    const workflow = event.workflow ?? "unknown";

    if (!state.session_id) {
      state.session_id = sessionId;
    }

    if (!state.workflows![workflow]) {
      state.workflows![workflow] = {
        current_phase: "session_start",
        phases: {},
      };
    }

    state.global!.hooks_fired_total!++;
    state.active_workflow = workflow;

    if (event.event === "phase_transition") {
      const newPhase = event.new_phase;
      if (newPhase) {
        const wf = state.workflows![workflow];
        const oldPhase = wf.current_phase;

        // Mark old phase complete
        if (oldPhase && oldPhase !== newPhase) {
          if (!wf.phases![oldPhase]) {
            wf.phases![oldPhase] = { status: "completed", iterations: 1 };
          } else {
            wf.phases![oldPhase].status = "completed";
          }
        }

        // Update new phase
        if (!wf.phases![newPhase]) {
          wf.phases![newPhase] = { status: "in_progress", iterations: 1 };
        } else {
          wf.phases![newPhase].iterations++;
          if (event.loop_reason) {
            if (!wf.phases![newPhase].loop_reasons) {
              wf.phases![newPhase].loop_reasons = [];
            }
            wf.phases![newPhase].loop_reasons!.push(event.loop_reason);
          }
        }

        wf.current_phase = newPhase;
      }
    }
  }

  return state;
}

// =============================================================================
// Skip Reason Inference
// =============================================================================

function inferSkipReason(phase: string, workflowId: string, phasesSeen: Set<string>): SkipInfo {
  const context: Record<string, unknown> = { workflow_id: workflowId };
  let reason = "Unknown reason";

  if (phase === "pre_check") {
    if (phasesSeen.has("worktree_create") || phasesSeen.has("implementation")) {
      reason = "Started work directly without pre-check exploration";
    } else if (workflowId === "main") {
      reason = "Working on main branch (no issue context)";
    } else {
      reason = "Skipped initial codebase exploration";
    }
  } else if (phase === "worktree_create") {
    if (workflowId === "main") {
      reason = "Working on main branch (no worktree needed)";
    } else if (phasesSeen.has("implementation")) {
      reason = "Worktree may have been created in previous session";
    } else {
      reason = "Worktree creation not detected";
    }
  } else if (phase === "implementation") {
    if (phasesSeen.has("pre_commit_check")) {
      reason = "Documentation or config change only (no code implementation)";
    } else if (phasesSeen.has("pr_create")) {
      reason = "Implementation done in previous session";
    } else {
      reason = "No implementation detected";
    }
  } else if (phase === "pre_commit_check") {
    if (phasesSeen.has("pr_create") || phasesSeen.has("ci_review")) {
      reason = "Commit verification may have been done in previous session";
    } else {
      reason = "No commit detected in this session";
    }
  } else if (phase === "local_ai_review") {
    if (phasesSeen.has("pr_create")) {
      reason = "PR created without local AI review (optional)";
      context.suggestion = "Consider running codex review before PR creation";
    } else {
      reason = "Local AI review was not run";
    }
  } else if (phase === "pr_create") {
    if (workflowId === "main") {
      reason = "Working on main branch (no PR needed)";
    } else if (phasesSeen.has("ci_review") || phasesSeen.has("merge")) {
      reason = "PR may have been created by another session";
      context.external_session = true;
    } else {
      reason = "No PR creation detected";
    }
  } else if (phase === "ci_review") {
    if (phasesSeen.has("merge")) {
      reason = "CI review may have been done in another session";
      context.external_session = true;
    } else if (!phasesSeen.has("pr_create")) {
      reason = "No PR was created in this session";
    } else {
      reason = "CI review not detected";
    }
  } else if (phase === "merge") {
    if (phasesSeen.has("cleanup")) {
      reason = "Merge may have been done by another session";
      context.external_session = true;
    } else if (!phasesSeen.has("pr_create") && !phasesSeen.has("ci_review")) {
      reason = "No PR workflow in this session";
    } else {
      reason = "PR not merged in this session";
    }
  } else if (phase === "cleanup") {
    if (phasesSeen.has("merge")) {
      reason = "CRITICAL: Cleanup not done after merge";
      context.critical = true;
      context.suggestion = "Ensure worktree and branch are cleaned up";
    } else if (phasesSeen.has("session_end")) {
      reason = "Session ended without cleanup phase";
    } else {
      reason = "No cleanup detected";
    }
  } else if (phase === "production_check") {
    reason = "Production check was not performed (optional)";
  } else if (phase === "session_end") {
    reason = "Session end not properly recorded";
  }

  return { phase, status: "skipped", reason, context };
}

// =============================================================================
// Verification Functions
// =============================================================================

function verifyTrackingAccuracy(events: Event[], state: SessionState) {
  const report = {
    phase_transitions: { correct: 0, total: 0, issues: [] as string[] },
    loop_detection: { correct: 0, total: 0, issues: [] as string[] },
    undetected_events: [] as string[],
    skipped_phases: [] as SkipInfo[],
    false_positives: [] as string[],
  };

  for (const [workflowId, workflow] of Object.entries(state.workflows ?? {})) {
    const phasesSeen = new Set(Object.keys(workflow.phases ?? {}));
    report.phase_transitions.total += phasesSeen.size;

    for (const phase of phasesSeen) {
      const requiredDeps = PHASE_DEPENDENCIES[phase];

      if (requiredDeps && requiredDeps.size > 0) {
        const depsSatisfied = [...requiredDeps].some((dep) => phasesSeen.has(dep));
        if (depsSatisfied) {
          report.phase_transitions.correct++;
        } else {
          const missing = [...requiredDeps].sort().join(", ");
          report.phase_transitions.issues.push(
            `Phase '${phase}' missing required predecessor(s) (${missing}) in ${workflowId}`,
          );
        }
      } else {
        report.phase_transitions.correct++;
      }
    }
  }

  // Count loops
  const loopEvents = events.filter((e) => e.loop_reason);
  report.loop_detection.total = loopEvents.length;
  report.loop_detection.correct = loopEvents.length;

  // Check skipped phases
  for (const [workflowId, workflow] of Object.entries(state.workflows ?? {})) {
    const phasesSeen = new Set(Object.keys(workflow.phases ?? {}));
    for (const phase of EXPECTED_PHASE_ORDER) {
      if (!phasesSeen.has(phase) && !OPTIONAL_PHASES.has(phase)) {
        report.undetected_events.push(`Phase '${phase}' was skipped in ${workflowId}`);
        report.skipped_phases.push(inferSkipReason(phase, workflowId, phasesSeen));
      }
    }
  }

  return report;
}

function reviewFlowDesign(_events: Event[], state: SessionState) {
  const report = {
    efficiency: { issues: [] as string[], suggestions: [] as string[] },
    order: { issues: [] as string[], suggestions: [] as string[] },
    granularity: { issues: [] as string[], suggestions: [] as string[] },
    coverage: { issues: [] as string[], suggestions: [] as string[] },
    divergence: { issues: [] as string[], suggestions: [] as string[] },
  };

  for (const [workflowId, workflow] of Object.entries(state.workflows ?? {})) {
    const phases = workflow.phases ?? {};

    // Efficiency: Check for excessive loops
    for (const [phase, info] of Object.entries(phases)) {
      const iterations = info.iterations ?? 1;
      if (iterations > 3) {
        report.efficiency.issues.push(
          `Phase '${phase}' had ${iterations} iterations in ${workflowId}`,
        );
        const loopReasons = info.loop_reasons ?? [];
        const reasonCounts = new Map<string, number>();
        for (const reason of loopReasons) {
          reasonCounts.set(reason, (reasonCounts.get(reason) ?? 0) + 1);
        }
        let maxReason = "";
        let maxCount = 0;
        for (const [reason, count] of reasonCounts) {
          if (count > maxCount) {
            maxCount = count;
            maxReason = reason;
          }
        }
        if (maxReason) {
          report.efficiency.suggestions.push(
            `Consider preventing '${maxReason}' - caused ${maxCount} loops`,
          );
        }
      }
    }

    // Coverage: Check for cleanup after merge
    if (phases.merge && !phases.cleanup) {
      report.coverage.issues.push(
        `CRITICAL: 'cleanup' was skipped in ${workflowId} - worktree/branch cleanup may be incomplete`,
      );
      const suggestion =
        "cleanup is critical - ensure worktrees and branches are cleaned up after merge";
      if (!report.coverage.suggestions.includes(suggestion)) {
        report.coverage.suggestions.push(suggestion);
      }
    }

    if (phases.pr_create && !phases.local_ai_review) {
      const suggestion = "Consider running local AI review before creating PR";
      if (!report.coverage.suggestions.includes(suggestion)) {
        report.coverage.suggestions.push(suggestion);
      }
    }

    // Granularity
    const implIterations = phases.implementation?.iterations ?? 1;
    if (implIterations > 2) {
      const suggestion = "Consider splitting 'implementation' into 'initial' and 'revision' phases";
      if (!report.granularity.suggestions.includes(suggestion)) {
        report.granularity.suggestions.push(suggestion);
      }
    }
  }

  return report;
}

function calculateCompletionMetrics(state: SessionState) {
  const workflows = state.workflows ?? {};
  const totalWorkflows = Object.keys(workflows).length;
  let completedWorkflows = 0;
  let totalCriticalIssues = 0;

  for (const workflow of Object.values(workflows)) {
    const phases = workflow.phases ?? {};

    if (phases.cleanup && phases.session_end) {
      completedWorkflows++;
    }

    if (phases.merge && !phases.cleanup) {
      totalCriticalIssues++;
    }
  }

  return {
    total_workflows: totalWorkflows,
    completed_workflows: completedWorkflows,
    completion_rate: totalWorkflows > 0 ? completedWorkflows / totalWorkflows : 0,
    total_critical_issues: totalCriticalIssues,
  };
}

function countSessionViolations(events: Event[]) {
  const violations: {
    critical: number;
    warning: number;
    patterns: Record<string, number>;
    details: Array<{ type: string; pattern: string; reason: string; critical_reason?: string }>;
  } = {
    critical: 0,
    warning: 0,
    patterns: {},
    details: [],
  };

  for (const event of events) {
    const violationReason = event.violation_reason;
    if (!violationReason) {
      continue;
    }

    const currentPhase = event.current_phase ?? "";
    const newPhase = event.new_phase ?? "";
    const pattern = `${currentPhase}->${newPhase}`;

    violations.patterns[pattern] = (violations.patterns[pattern] ?? 0) + 1;

    const criticalReason = getCriticalViolation(currentPhase, newPhase);
    if (criticalReason) {
      violations.critical++;
      violations.details.push({
        type: "critical",
        pattern,
        reason: violationReason,
        critical_reason: criticalReason,
      });
    } else {
      violations.warning++;
      violations.details.push({
        type: "warning",
        pattern,
        reason: violationReason,
      });
    }
  }

  return violations;
}

// =============================================================================
// Report Generation
// =============================================================================

function generateReport(
  events: Event[],
  state: SessionState,
  aggregatedWorkflows: Record<string, WorkflowData> | null = null,
  sessionCount = 0,
) {
  const accuracy = verifyTrackingAccuracy(events, state);
  const design = reviewFlowDesign(events, state);
  const completion = calculateCompletionMetrics(state);
  const violations = countSessionViolations(events);

  let totalPhases = 0;
  let passedPhases = 0;
  let totalLoops = 0;

  for (const workflow of Object.values(state.workflows ?? {})) {
    const phases = workflow.phases ?? {};
    totalPhases += Object.keys(phases).length;
    passedPhases += Object.values(phases).filter((p) => p.status === "completed").length;
    totalLoops += Object.values(phases).reduce((acc, p) => acc + (p.iterations ?? 1) - 1, 0);
  }

  const report: Record<string, unknown> = {
    timestamp: new Date().toISOString(),
    session_id: state.session_id ?? "",
    summary: {
      total_phases: totalPhases,
      passed_phases: passedPhases,
      skipped_phases: accuracy.undetected_events.length,
      total_loops: totalLoops,
      hooks_fired: state.global?.hooks_fired_total ?? 0,
      completion_rate: completion.completion_rate,
      total_critical_issues: completion.total_critical_issues,
      violations_critical: violations.critical,
      violations_warning: violations.warning,
    },
    level1_accuracy: accuracy,
    level2_design: design,
    completion,
    violations,
    workflows: state.workflows ?? {},
  };

  if (aggregatedWorkflows !== null) {
    const aggregatedState = { workflows: aggregatedWorkflows };
    const aggregatedCompletion = calculateCompletionMetrics(aggregatedState);
    report.aggregated = {
      total_sessions: sessionCount,
      total_workflows: Object.keys(aggregatedWorkflows).length,
      completion_rate: aggregatedCompletion.completion_rate,
      total_critical_issues: aggregatedCompletion.total_critical_issues,
      completed_workflows: aggregatedCompletion.completed_workflows,
    };
  }

  return report;
}

function formatReportText(report: Record<string, unknown>): string {
  const lines: string[] = ["", "━━━ フロー検証レポート ━━━", ""];

  const summary = (report.summary ?? {}) as Record<string, unknown>;
  lines.push("📊 サマリー");
  lines.push(`  総フェーズ: ${summary.total_phases ?? 0}`);
  lines.push(`  完了: ${summary.passed_phases ?? 0}`);
  lines.push(`  スキップ: ${summary.skipped_phases ?? 0}`);
  lines.push(`  総ループ: ${summary.total_loops ?? 0}`);
  lines.push(`  フック発動: ${summary.hooks_fired ?? 0}`);

  const completionRate = (summary.completion_rate as number) ?? 0;
  const criticalIssues = (summary.total_critical_issues as number) ?? 0;
  lines.push(`  完了率: ${Math.round(completionRate * 100)}%`);
  if (criticalIssues > 0) {
    lines.push(`  ⚠️ Critical問題: ${criticalIssues}件`);
  }

  // Violation summary
  const violationsCritical = (summary.violations_critical as number) ?? 0;
  const violationsWarning = (summary.violations_warning as number) ?? 0;
  if (violationsCritical > 0 || violationsWarning > 0) {
    lines.push("");
    lines.push("⚠️ フェーズ遷移違反");
    if (violationsCritical > 0) {
      lines.push(`  Critical: ${violationsCritical}件（ブロック対象）`);
    }
    if (violationsWarning > 0) {
      lines.push(`  Warning: ${violationsWarning}件（警告のみ）`);
    }

    const violations = (report.violations ?? {}) as Record<string, unknown>;
    const patterns = (violations.patterns ?? {}) as Record<string, number>;
    if (Object.keys(patterns).length > 0) {
      lines.push("  パターン別:");
      const sortedPatterns = Object.entries(patterns)
        .sort(([, a], [, b]) => b - a)
        .slice(0, 5);
      for (const [pattern, count] of sortedPatterns) {
        lines.push(`    - ${pattern}: ${count}回`);
      }
    }
  }

  lines.push("");

  // Level 1
  const accuracy = (report.level1_accuracy ?? {}) as Record<string, unknown>;
  const trans = (accuracy.phase_transitions ?? {}) as Record<string, unknown>;
  lines.push("📈 追跡精度 (Level 1)");
  lines.push(`  フェーズ遷移: ${trans.correct ?? 0}/${trans.total ?? 0}`);

  // Skipped phases
  const skippedPhases = (accuracy.skipped_phases ?? []) as SkipInfo[];
  if (skippedPhases.length > 0) {
    lines.push("  ⚠️ スキップされたフェーズ:");
    for (const skipInfo of skippedPhases.slice(0, 5)) {
      const { phase, reason, context } = skipInfo;
      const workflowId = context.workflow_id ?? "unknown";

      if (context.critical) {
        lines.push(`    - ⚠️ CRITICAL: ${phase} (${workflowId})`);
      } else {
        lines.push(`    - ${phase} (${workflowId})`);
      }
      lines.push(`      理由: ${reason}`);

      if (context.suggestion) {
        lines.push(`      💡 ${context.suggestion}`);
      }
    }
  }
  lines.push("");

  // Level 2
  const design = (report.level2_design ?? {}) as Record<
    string,
    { issues?: string[]; suggestions?: string[] }
  >;
  const hasIssues = Object.values(design).some((cat) => (cat.issues?.length ?? 0) > 0);
  const hasSuggestions = Object.values(design).some((cat) => (cat.suggestions?.length ?? 0) > 0);

  if (hasIssues || hasSuggestions) {
    lines.push("🤔 フロー設計レビュー (Level 2)");
    for (const [category, data] of Object.entries(design)) {
      for (const issue of data.issues ?? []) {
        lines.push(`  ⚠️ [${category}] ${issue}`);
      }
      for (const suggestion of data.suggestions ?? []) {
        lines.push(`  💡 [${category}] ${suggestion}`);
      }
    }
    lines.push("");
  }

  // Aggregated stats
  const aggregated = report.aggregated as Record<string, unknown> | undefined;
  if (aggregated) {
    lines.push("🌐 全セッション統計");
    lines.push(`  総セッション数: ${aggregated.total_sessions ?? 0}`);
    lines.push(`  総ワークフロー数: ${aggregated.total_workflows ?? 0}`);
    const aggRate = (aggregated.completion_rate as number) ?? 0;
    lines.push(`  全体完了率: ${Math.round(aggRate * 100)}%`);
    const aggCritical = (aggregated.total_critical_issues as number) ?? 0;
    if (aggCritical > 0) {
      lines.push(`  ⚠️ Critical問題（全体）: ${aggCritical}件`);
    }
    lines.push("");
  }

  return lines.join("\n");
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  try {
    ensureLogDir();
  } catch {
    // Best effort
  }

  const hookInput = await parseHookInput();
  const ctx = createContext(hookInput);

  // Skip if Stop hook is already active
  if (hookInput.stop_hook_active) {
    console.log(JSON.stringify({}));
    return;
  }

  // Get session ID
  const sessionId =
    ctx.sessionId ??
    `${new Date()
      .toISOString()
      .replace(/[-:T.]/g, "")
      .slice(0, 15)}-${process.pid}`;

  // Load data
  const events = loadEvents(sessionId);
  let state = loadState(sessionId);

  // Rebuild state from events if state file is missing/empty
  if (!state.workflows || Object.keys(state.workflows).length === 0) {
    state = rebuildStateFromEvents(events, sessionId);
  }

  // Load aggregated workflows
  const [aggregatedWorkflows, sessionCount] = loadAllSessionsWorkflows();

  // Generate report
  const report = generateReport(events, state, aggregatedWorkflows, sessionCount);

  // Save report
  try {
    writeFileSync(getReportFile(), JSON.stringify(report, null, 2));
  } catch {
    // Best effort
  }

  // Format report
  const reportText = formatReportText(report);

  await logHookExecution(
    HOOK_NAME,
    "approve",
    "Flow verification completed",
    {
      total_phases: (report.summary as Record<string, unknown>).total_phases,
      total_loops: (report.summary as Record<string, unknown>).total_loops,
    },
    { sessionId },
  );

  // Output report as system message if there are issues
  const result: Record<string, unknown> = {};

  const level1Accuracy = (report.level1_accuracy ?? {}) as Record<string, unknown>;
  const hasLevel1Issues =
    ((level1Accuracy.phase_transitions as Record<string, unknown>)?.issues as unknown[])?.length >
      0 || (level1Accuracy.undetected_events as unknown[])?.length > 0;

  const level2Design = (report.level2_design ?? {}) as Record<
    string,
    { issues?: string[]; suggestions?: string[] }
  >;
  const hasLevel2Issues = Object.values(level2Design).some(
    (cat) => (cat.issues?.length ?? 0) > 0 || (cat.suggestions?.length ?? 0) > 0,
  );

  if (hasLevel1Issues || hasLevel2Issues) {
    result.systemMessage = reportText;
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e: unknown) => {
    console.error(`[${HOOK_NAME}] Unexpected error:`, e);
    console.log(JSON.stringify({}));
    process.exit(0);
  });
}
