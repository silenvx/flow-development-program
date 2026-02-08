#!/usr/bin/env bun
/**
 * 開発ワークフローのフェーズ遷移を追跡する。
 *
 * Why:
 *   ワークフロー（session_start→実装→PR→マージ→cleanup）のフェーズを
 *   追跡し、スキップや逸脱を検出する。データ分析でボトルネックの特定や
 *   改善ポイントの発見に活用する。
 *
 * What:
 *   - 全ツール使用イベントでフェーズ遷移を判定
 *   - ループ（CI失敗→実装に戻る等）の検出と記録
 *   - 重大違反（merge後cleanup未実施など）をブロック
 *   - 軽微な違反は警告のみ
 *
 * State:
 *   writes: .claude/state/flow/state-{session}.json
 *   writes: .claude/state/flow/events-{session}.jsonl
 *
 * Remarks:
 *   - フェーズ遷移はPHASE_TRIGGERSのパターンマッチで検出
 *   - セッション固有のステートファイルで他セッションとの干渉を防止
 *
 * Changelog:
 *   - silenvx/dekita#720: フック追加
 *   - silenvx/dekita#1309: 必須フェーズ遷移の検証追加
 *   - silenvx/dekita#1631: 外部PR検出機能追加
 *   - silenvx/dekita#1690: Critical違反のブロック機能追加
 *   - silenvx/dekita#2567: マージ済みPRの自動検出追加
 *   - silenvx/dekita#3157: TypeScriptに移行
 */

import { execSync } from "node:child_process";
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { basename, join } from "node:path";
import { FLOW_LOG_DIR } from "../lib/common";
import {
  ALLOWED_LOOPBACKS,
  BLOCKING_PHASE_TRANSITIONS,
  OPTIONAL_PHASES,
  REQUIRED_PHASE_TRANSITIONS,
  getCriticalViolation,
} from "../lib/flow_constants";
import { formatError } from "../lib/format_error";
import { extractInputContext, getToolResult } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { createContext, parseHookInput } from "../lib/session";

// =============================================================================
// Constants
// =============================================================================

const HOOK_NAME = "flow-state-updater";

// Phase definitions
const PHASES = [
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
] as const;

type Phase = (typeof PHASES)[number];

// Phase transition triggers
interface PhaseTrigger {
  enter?: { hook_type?: string; tools?: string[] };
  enter_pattern?: string;
  exit_pattern?: string;
  exit_next?: Phase;
  loop_from?: Phase[];
}

const PHASE_TRIGGERS: Record<string, PhaseTrigger> = {
  session_start: {
    enter: { hook_type: "SessionStart" },
    exit_next: "pre_check",
  },
  pre_check: {
    enter: { tools: ["Read", "Grep", "Glob"] },
    exit_pattern: "git worktree add",
    exit_next: "worktree_create",
  },
  worktree_create: {
    enter_pattern: "git worktree add",
    exit_pattern: "git worktree add.*succeeded|Preparing worktree",
    exit_next: "implementation",
  },
  implementation: {
    enter: { tools: ["Edit", "Write"] },
    exit_pattern: "git commit",
    exit_next: "pre_commit_check",
    // Issue #2153: CI失敗やレビューコメント時に実装に戻るフェーズを拡大
    loop_from: ["ci_review", "pr_create", "local_ai_review", "pre_commit_check", "merge"],
  },
  pre_commit_check: {
    enter_pattern: "git add|git commit",
    // Pattern matches git commit success output: [branch abc123] message
    exit_pattern: "git commit.*succeeded|\\[[\\w/-]+\\s+[a-f0-9]{7,}\\]",
    exit_next: "local_ai_review",
  },
  local_ai_review: {
    enter_pattern: "codex review",
    exit_next: "pr_create",
  },
  pr_create: {
    enter_pattern: "gh pr create",
    exit_pattern: "github\\.com.*pull",
    exit_next: "ci_review",
  },
  issue_work: {
    enter_pattern: "gh issue (create|edit|comment)",
  },
  ci_review: {
    // Issue #1678: Only git push should directly enter ci_review
    enter_pattern: "git push",
    // Issue #1784: Match on success only
    exit_pattern: "✔?\\s*Merged pull request|\\bhas been merged\\b|\\bsuccessfully merged\\b",
    exit_next: "merge",
  },
  merge: {
    // Issue #1784: Merge phase is entered only via ci_review.exit_pattern
    exit_pattern: "Merged|merged",
    exit_next: "cleanup",
  },
  cleanup: {
    enter_pattern: "git worktree remove|git branch -d",
    exit_next: "session_end",
  },
  session_end: {
    enter: { hook_type: "Stop" },
  },
};

// Phases requiring PR
const PHASES_REQUIRING_PR = new Set(["ci_review", "merge"]);

// Issue #1369: Phases where Read/Grep/Glob should NOT trigger pre_check transition
const ACTIVE_WORK_PHASES = new Set([
  "implementation",
  "pre_commit_check",
  "local_ai_review",
  "pr_create",
  "issue_work",
  "ci_review",
  "merge",
  "cleanup",
]);

// Issue #1363: Phases after successful merge
const POST_MERGE_PHASES = new Set(["cleanup", "session_end"]);

// Loop triggers
const LOOP_TRIGGERS: Record<string, string[]> = {
  ci_failed: ["CI failed", "check failed", "workflow failed", "Build failed"],
  review_comment: ["copilot", "codex", "review comment", "comment\\(s\\)"],
  lint_error: ["lint", "ruff", "biome", "eslint", "Lint error"],
  test_failed: ["test failed", "FAILED", "AssertionError"],
  type_error: ["typecheck", "TypeError", "type error"],
  merge_conflict: ["conflict", "CONFLICT"],
};

// Cleanup configuration
const CLEANUP_MAX_AGE_HOURS = 24;
const CLEANUP_FREQUENCY = 10;

// =============================================================================
// Types
// =============================================================================

interface WorkflowState {
  branch: string;
  current_phase: string;
  phases: Record<
    string,
    {
      status: string;
      iterations: number;
      loop_reasons?: string[];
      source?: string;
      pr_number?: number;
      pr_url?: string;
      merged_at?: string;
    }
  >;
  phase_start_time: string;
}

interface SessionState {
  session_id: string;
  active_workflow: string | null;
  workflows: Record<string, WorkflowState>;
  global: {
    hooks_fired_total: number;
    session_start_time: string;
  };
}

interface ExternalPR {
  number: number;
  url: string;
}

interface MergedPR {
  number: number;
  url: string;
  state: string;
  merged_at?: string;
}

// =============================================================================
// File Path Helpers
// =============================================================================

function getStateFile(sessionId: string): string {
  const safeSessionId = basename(sessionId);
  return join(FLOW_LOG_DIR, `state-${safeSessionId}.json`);
}

function getEventsFile(sessionId: string): string {
  const safeSessionId = basename(sessionId);
  return join(FLOW_LOG_DIR, `events-${safeSessionId}.jsonl`);
}

// =============================================================================
// State Management
// =============================================================================

function ensureLogDir(): void {
  const flowLogDir = FLOW_LOG_DIR;
  if (!existsSync(flowLogDir)) {
    mkdirSync(flowLogDir, { recursive: true });
  }
}

function loadState(sessionId: string): SessionState {
  const stateFile = getStateFile(sessionId);
  try {
    if (existsSync(stateFile)) {
      return JSON.parse(readFileSync(stateFile, "utf-8"));
    }
  } catch {
    // Corrupted state file is ignored
  }

  // Initial state for new session
  return {
    session_id: sessionId,
    active_workflow: null,
    workflows: {},
    global: {
      hooks_fired_total: 0,
      session_start_time: new Date().toISOString(),
    },
  };
}

function saveState(state: SessionState, sessionId: string): void {
  try {
    ensureLogDir();
    const stateFile = getStateFile(sessionId);
    writeFileSync(stateFile, JSON.stringify(state, null, 2));
  } catch {
    // Best effort
  }
}

function logEvent(event: Record<string, unknown>): void {
  try {
    ensureLogDir();
    const eventWithTs = { ...event, ts: new Date().toISOString() };
    const sessionId = (event.session_id as string) ?? "unknown";
    const eventsFile = getEventsFile(sessionId);
    appendFileSync(eventsFile, `${JSON.stringify(eventWithTs)}\n`);
  } catch {
    // Best effort
  }
}

// =============================================================================
// Cleanup
// =============================================================================

function cleanupOldSessionFiles(): number {
  try {
    const flowLogDir = FLOW_LOG_DIR;
    if (!existsSync(flowLogDir)) {
      return 0;
    }

    const now = Date.now();
    const maxAgeMs = CLEANUP_MAX_AGE_HOURS * 60 * 60 * 1000;
    let deletedCount = 0;

    const files = readdirSync(flowLogDir) as string[];

    for (const file of files) {
      if (!file.startsWith("state-") && !file.startsWith("events-")) {
        continue;
      }

      const filePath = join(flowLogDir, file);
      try {
        const stat = statSync(filePath);
        if (now - stat.mtimeMs > maxAgeMs) {
          unlinkSync(filePath);
          deletedCount++;
        }
      } catch {
        // File may have been deleted
      }
    }

    return deletedCount;
  } catch {
    return 0;
  }
}

// =============================================================================
// External PR Detection
// =============================================================================

function checkExternalPrExists(branch: string): ExternalPR | null {
  try {
    const result = execSync(`gh pr list --state open --head "${branch}" --json number,url`, {
      encoding: "utf-8",
      timeout: 10000,
    });

    const prs = JSON.parse(result.trim() || "[]");
    if (prs.length > 0) {
      return { number: prs[0].number, url: prs[0].url };
    }
  } catch {
    // Best effort
  }
  return null;
}

function checkMergedPrForWorkflow(workflow: string): MergedPR | null {
  const match = workflow.match(/issue-(\d+)/);
  if (!match) {
    return null;
  }

  const issueNumber = match[1];
  const branchPrefixes = [
    `feat/issue-${issueNumber}`,
    `fix/issue-${issueNumber}`,
    `issue-${issueNumber}`,
  ];

  for (const branchPrefix of branchPrefixes) {
    try {
      const result = execSync(
        `gh pr list --state merged --search "head:${branchPrefix}" --json number,url,state,mergedAt --limit 1`,
        { encoding: "utf-8", timeout: 10000 },
      );

      const prs = JSON.parse(result.trim() || "[]");
      if (prs.length > 0) {
        return {
          number: prs[0].number,
          url: prs[0].url,
          state: prs[0].state ?? "MERGED",
          merged_at: prs[0].mergedAt,
        };
      }
    } catch {
      // Best effort
    }
  }

  return null;
}

function getCurrentBranch(): string | null {
  try {
    const result = execSync("git branch --show-current", {
      encoding: "utf-8",
      timeout: 5000,
    });
    return result.trim() || null;
  } catch {
    return null;
  }
}

// =============================================================================
// Workflow Detection
// =============================================================================

function getCurrentWorkflow(hookInput: Record<string, unknown> | null): string {
  // Issue #1365: Check if cleanup command targets specific worktree
  if (hookInput) {
    const toolInput = hookInput.tool_input as Record<string, unknown> | undefined;
    const command = typeof toolInput?.command === "string" ? toolInput.command : "";

    const worktreeMatch = command.match(
      /git\s+worktree\s+remove\s+(?:--?\w+\s+)*.*?\.worktrees[/\\]([^/\\\s"']+)/,
    );
    if (worktreeMatch) {
      return worktreeMatch[1];
    }
  }

  const cwd = process.cwd();

  // From worktree path
  if (cwd.includes("/.worktrees/")) {
    const match = cwd.match(/\.worktrees\/([^/]+)/);
    if (match) {
      return match[1];
    }
  }

  // From branch name
  try {
    const branch = execSync("git branch --show-current", {
      encoding: "utf-8",
      timeout: 5000,
    }).trim();

    const issueMatch = branch.match(/issue-(\d+)/);
    if (issueMatch) {
      return `issue-${issueMatch[1]}`;
    }
    if (branch === "main" || branch === "master") {
      return "main";
    }
    return branch;
  } catch {
    // Best effort
  }

  return "unknown";
}

// =============================================================================
// Tool Result Inference
// =============================================================================

function inferToolResult(hookInput: Record<string, unknown>): string | null {
  const toolOutput = getToolResult(hookInput);
  // null または undefined (PreToolUse等でtool_resultがない場合) はnullを返す
  if (toolOutput == null) {
    return null;
  }

  // オブジェクトの場合はJSON化して文字列化（String()だと"[object Object]"になる）
  const outputStr =
    typeof toolOutput === "object" ? JSON.stringify(toolOutput) : String(toolOutput);

  // Check for blocked patterns
  if (outputStr.includes("Hook PreToolUse:") && outputStr.includes("denied")) {
    return "blocked";
  }

  // Check for failure patterns
  const failurePatterns = [
    "error:",
    "Error:",
    "ERROR:",
    "failed",
    "Failed",
    "FAILED",
    "Exit code 1",
    "fatal:",
    "Fatal:",
  ];
  for (const pattern of failurePatterns) {
    if (outputStr.includes(pattern)) {
      return "failure";
    }
  }

  return "success";
}

// =============================================================================
// Phase Transition Validation
// =============================================================================

function isValidPhaseTransition(currentPhase: string, newPhase: string): [boolean, string | null] {
  // Loop back to same phase is always valid
  if (currentPhase === newPhase) {
    return [true, null];
  }

  // Issue #1874: Transition to session_start is always valid
  if (newPhase === "session_start") {
    return [true, null];
  }

  // Check required transitions
  const requiredNext = REQUIRED_PHASE_TRANSITIONS[currentPhase];
  if (requiredNext) {
    if (newPhase === requiredNext) {
      return [true, null];
    }

    // Issue #1739: Allowed loopbacks
    if (ALLOWED_LOOPBACKS.has(`${currentPhase},${newPhase}`)) {
      return [
        true,
        `Phase '${currentPhase}' must transition to '${requiredNext}' before '${newPhase}'`,
      ];
    }

    // Issue #1345: Optional phases
    if (OPTIONAL_PHASES.has(newPhase)) {
      return [true, `Required phase '${requiredNext}' bypassed by optional phase '${newPhase}'`];
    }

    return [
      false,
      `Phase '${currentPhase}' must transition to '${requiredNext}' before '${newPhase}'`,
    ];
  }

  return [true, null];
}

// =============================================================================
// Phase Transition Detection
// =============================================================================

function detectPhaseTransition(
  currentPhase: string,
  hookInput: Record<string, unknown>,
  _state: SessionState,
): [string | null, string | null, string | null, string | null] {
  const toolName = (hookInput.tool_name as string) ?? "";
  const toolInput = (hookInput.tool_input as Record<string, unknown>) ?? {};
  // getToolResultを使用してtool_result/tool_response/tool_outputを統一的に取得
  const toolOutputRaw = getToolResult(hookInput);
  const toolOutput = toolOutputRaw ?? "";

  // Infer hook_type if not present
  let hookType = (hookInput.hook_type as string) ?? "";
  if (!hookType) {
    const context = extractInputContext(hookInput);
    hookType = context.hook_type ?? "";
  }

  const checkAndReturn = (
    newPhase: string | null,
    loopReason: string | null,
    transitionReason: string | null,
  ): [string | null, string | null, string | null, string | null] => {
    if (newPhase) {
      const [_isValid, violation] = isValidPhaseTransition(currentPhase, newPhase);
      return [newPhase, loopReason, transitionReason, violation];
    }
    return [null, null, null, null];
  };

  // Check for loop triggers first
  // オブジェクトの場合はJSON化して文字列化（String()だと"[object Object]"になる）
  const outputStr =
    typeof toolOutput === "object" ? JSON.stringify(toolOutput) : String(toolOutput);
  for (const [reason, patterns] of Object.entries(LOOP_TRIGGERS)) {
    for (const pattern of patterns) {
      if (new RegExp(pattern, "i").test(outputStr)) {
        // Check if any phase has loop_from containing currentPhase
        for (const [targetPhase, config] of Object.entries(PHASE_TRIGGERS)) {
          if (config.loop_from && (config.loop_from as string[]).includes(currentPhase)) {
            return checkAndReturn(targetPhase, reason, `loop_trigger: ${pattern}`);
          }
        }
      }
    }
  }

  // Check for phase exit
  const trigger = PHASE_TRIGGERS[currentPhase];
  if (trigger?.exit_pattern) {
    const command = typeof toolInput.command === "string" ? toolInput.command : "";
    if (new RegExp(trigger.exit_pattern, "i").test(command + outputStr)) {
      return checkAndReturn(
        trigger.exit_next ?? null,
        null,
        `exit_pattern: ${trigger.exit_pattern}`,
      );
    }
  }

  // Check for phase enter
  for (const [phase, config] of Object.entries(PHASE_TRIGGERS)) {
    if (phase === currentPhase) {
      continue;
    }

    // Check hook type trigger
    if (config.enter?.hook_type && hookType === config.enter.hook_type) {
      return checkAndReturn(phase, null, `hook_type: ${hookType}`);
    }

    // Check tool trigger
    if (config.enter?.tools?.includes(toolName)) {
      // Issue #1369: Skip pre_check trigger during active work phases
      if (phase === "pre_check" && ACTIVE_WORK_PHASES.has(currentPhase)) {
        continue;
      }
      return checkAndReturn(phase, null, `tool: ${toolName}`);
    }

    // Check pattern trigger
    if (config.enter_pattern) {
      // Issue #1363: Skip ci_review trigger from post-merge phases
      if (phase === "ci_review" && POST_MERGE_PHASES.has(currentPhase)) {
        continue;
      }
      const command = typeof toolInput.command === "string" ? toolInput.command : "";
      if (new RegExp(config.enter_pattern, "i").test(command)) {
        return checkAndReturn(phase, null, `enter_pattern: ${config.enter_pattern}`);
      }
    }
  }

  return [null, null, null, null];
}

// =============================================================================
// Workflow State Update
// =============================================================================

function updateWorkflowState(
  state: SessionState,
  workflow: string,
  newPhase: string,
  loopReason: string | null,
): [ExternalPR | null, MergedPR | null] {
  const now = new Date();

  if (!state.workflows[workflow]) {
    state.workflows[workflow] = {
      branch: "",
      current_phase: newPhase,
      phases: {},
      phase_start_time: now.toISOString(),
    };
  }

  const wf = state.workflows[workflow];
  const oldPhase = wf.current_phase;

  // Issue #1631: Check for external PR
  let externalPr: ExternalPR | null = null;
  if (PHASES_REQUIRING_PR.has(newPhase) && !wf.phases.pr_create) {
    const branch = getCurrentBranch();
    if (branch) {
      externalPr = checkExternalPrExists(branch);
      if (externalPr) {
        wf.phases.pr_create = {
          status: "completed",
          iterations: 1,
          source: "external",
          pr_number: externalPr.number,
          pr_url: externalPr.url,
        };
      }
    }
  }

  // Issue #2567: Auto-complete merge phase
  let autoDetectedMerge: MergedPR | null = null;
  if (newPhase === "cleanup" && !wf.phases.merge) {
    const mergedPr = checkMergedPrForWorkflow(workflow);
    if (mergedPr) {
      wf.phases.merge = {
        status: "completed",
        iterations: 1,
        source: "auto_detected",
        pr_number: mergedPr.number,
        pr_url: mergedPr.url,
        merged_at: mergedPr.merged_at,
      };
      autoDetectedMerge = mergedPr;
    }
  }

  // Update phase status
  if (oldPhase && oldPhase !== newPhase) {
    if (!wf.phases[oldPhase]) {
      wf.phases[oldPhase] = { status: "completed", iterations: 1 };
    } else {
      wf.phases[oldPhase].status = "completed";
    }
  }

  // Handle new phase
  if (!wf.phases[newPhase]) {
    wf.phases[newPhase] = { status: "in_progress", iterations: 1 };
  } else {
    if (loopReason) {
      wf.phases[newPhase].iterations += 1;
      if (!wf.phases[newPhase].loop_reasons) {
        wf.phases[newPhase].loop_reasons = [];
      }
      wf.phases[newPhase].loop_reasons!.push(loopReason);
    }
    wf.phases[newPhase].status = "in_progress";
  }

  wf.current_phase = newPhase;
  wf.phase_start_time = now.toISOString();
  state.active_workflow = workflow;

  return [externalPr, autoDetectedMerge];
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  // Ensure log directory exists
  try {
    ensureLogDir();
  } catch {
    // Best effort
  }

  // Read hook input
  const hookInput = await parseHookInput();
  const ctx = createContext(hookInput);

  // Infer hook_type early
  let hookType = (hookInput.hook_type as string) ?? "";
  if (!hookType) {
    hookType = extractInputContext(hookInput).hook_type ?? "";
  }

  // Skip recursive tool calls during Stop hook
  if (hookInput.stop_hook_active && hookType !== "Stop") {
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

  // Get current workflow and state
  const workflow = getCurrentWorkflow(hookInput);
  const state = loadState(sessionId);

  // Get current phase
  let currentPhase = "session_start";
  const isNewWorkflow = !(workflow in state.workflows);
  if (isNewWorkflow) {
    state.workflows[workflow] = {
      branch: "",
      current_phase: "session_start",
      phases: { session_start: { status: "in_progress", iterations: 1 } },
      phase_start_time: new Date().toISOString(),
    };
    state.active_workflow = workflow;
  } else {
    currentPhase = state.workflows[workflow].current_phase ?? "session_start";
  }

  // Detect phase transition
  const [newPhase, loopReason, transitionReason, violationReason] = detectPhaseTransition(
    currentPhase,
    hookInput,
    state,
  );

  // Infer tool execution result
  const toolResult = inferToolResult(hookInput);

  // Log event
  const event: Record<string, unknown> = {
    session_id: state.session_id,
    workflow,
    event: "hook_fired",
    hook_type: hookType,
    tool_name: hookInput.tool_name ?? "",
    current_phase: currentPhase,
  };

  if (toolResult) {
    event.tool_result = toolResult;
  }

  if (newPhase && newPhase !== currentPhase) {
    event.event = "phase_transition";
    event.new_phase = newPhase;
    if (loopReason) {
      event.loop_reason = loopReason;
    }
    if (transitionReason) {
      event.transition_reason = transitionReason;
    }
    if (violationReason) {
      event.violation_reason = violationReason;
    }

    // Calculate duration
    if (state.workflows[workflow]) {
      const phaseStart = state.workflows[workflow].phase_start_time;
      if (phaseStart) {
        try {
          const startTime = new Date(phaseStart);
          const now = new Date();
          const duration = (now.getTime() - startTime.getTime()) / 1000;
          event.duration_seconds = Math.round(duration * 100) / 100;
        } catch {
          // Invalid timestamp
        }
      }
    }
  }

  logEvent(event);

  // Check for critical violations
  if (process.env.SKIP_FLOW_VIOLATION_CHECK !== "1") {
    if (violationReason && newPhase) {
      const criticalReason = getCriticalViolation(currentPhase, newPhase);

      if (criticalReason) {
        const requiredPhase = BLOCKING_PHASE_TRANSITIONS[currentPhase] ?? "cleanup";

        // Save state WITHOUT the invalid transition
        state.global.hooks_fired_total = (state.global.hooks_fired_total ?? 0) + 1;
        saveState(state, sessionId);

        const result = makeBlockResult(
          HOOK_NAME,
          `Critical workflow violation: ${violationReason}\n\n` +
            `Reason: ${criticalReason}\n\n` +
            `Please complete the '${requiredPhase}' phase before proceeding.`,
          ctx,
        );

        await logHookExecution(
          HOOK_NAME,
          "block",
          `Critical workflow violation: ${currentPhase} -> ${newPhase}`,
          {
            violation_reason: violationReason,
            critical_reason: criticalReason,
          },
          { sessionId },
        );

        console.log(JSON.stringify(result));
        return;
      }
    }
  }

  // Increment hook count
  state.global.hooks_fired_total = (state.global.hooks_fired_total ?? 0) + 1;

  // Periodic cleanup
  if (state.global.hooks_fired_total % CLEANUP_FREQUENCY === 0) {
    cleanupOldSessionFiles();
  }

  // Update state if phase changed
  if (newPhase && newPhase !== currentPhase) {
    const [externalPr, autoDetectedMerge] = updateWorkflowState(
      state,
      workflow,
      newPhase,
      loopReason,
    );

    if (externalPr) {
      logEvent({
        session_id: state.session_id,
        workflow,
        event: "external_pr_detected",
        phase: newPhase,
        pr_number: externalPr.number,
        pr_url: externalPr.url,
      });
    }

    if (autoDetectedMerge) {
      logEvent({
        session_id: state.session_id,
        workflow,
        event: "merge_phase_auto_detected",
        phase: newPhase,
        pr_number: autoDetectedMerge.number,
        pr_url: autoDetectedMerge.url,
        merged_at: autoDetectedMerge.merged_at,
      });
    }
  }

  // Save state
  saveState(state, sessionId);

  // Trigger cleanup at session_end
  const effectivePhase = newPhase ?? currentPhase;
  if (effectivePhase === "session_end") {
    cleanupOldSessionFiles();
  }

  // Warn for non-critical violations
  if (violationReason && newPhase) {
    const criticalReasonForWarn = getCriticalViolation(currentPhase, newPhase);
    const isCritical = criticalReasonForWarn !== undefined;
    const skipEnabled = process.env.SKIP_FLOW_VIOLATION_CHECK === "1";

    let message: string;
    let logAction: string;
    let logDetail: string;

    if (skipEnabled && isCritical) {
      message = `[${HOOK_NAME}] BYPASS MODE: Critical violation bypassed\nViolation: ${violationReason}\nNormally would be BLOCKED: ${criticalReasonForWarn}\nSet SKIP_FLOW_VIOLATION_CHECK= to re-enable blocking.`;
      logAction = "bypass";
      logDetail = `Critical violation bypassed: ${currentPhase} -> ${newPhase}`;
    } else {
      const expectedNext = REQUIRED_PHASE_TRANSITIONS[currentPhase] ?? "N/A";
      message = `[${HOOK_NAME}] ⚠️ フロー逸脱を検出:\n\n${violationReason}\n\n  現在フェーズ: ${currentPhase}\n  遷移先: ${newPhase}\n  推奨フェーズ: ${expectedNext}\n\n💡 AGENTS.mdの開発フローを確認してください。\n（ブロックではありません。分析用に記録されます）`;
      logAction = "warn";
      logDetail = `Non-critical violation: ${currentPhase} -> ${newPhase}`;
    }

    await logHookExecution(
      HOOK_NAME,
      logAction,
      logDetail,
      {
        violation_reason: violationReason,
        is_critical: isCritical,
      },
      { sessionId },
    );

    console.log(
      JSON.stringify({
        systemMessage: message,
      }),
    );
    return;
  }

  // No violation - approve normally
  console.log(JSON.stringify({}));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({}));
  });
}
