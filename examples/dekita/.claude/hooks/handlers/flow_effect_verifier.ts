#!/usr/bin/env bun
/**
 * 未完了フローがある場合にセッション終了をブロックする。
 *
 * Why:
 *   開発ワークフロー（worktree作成→実装→レビュー→マージ）のステップを
 *   スキップすると品質問題が発生する。セッション終了時にチェックし、
 *   未完了フローがあればブロックして完了を促す。
 *
 * What:
 *   - セッション内のアクティブなフローを取得
 *   - クローズ済みIssueや期限切れフローを除外
 *   - blocking_on_session_end=Trueのフローで未完了ステップがあればブロック
 *   - フロー進捗サマリーとワークフロー検証結果を表示
 *
 * State:
 *   reads: .claude/state/flow-progress.jsonl
 *   reads: .claude/state/flow/state-{session}.json
 *
 * Remarks:
 *   - 24時間以上経過したフローは自動的に期限切れ
 *   - クローズ済みIssueのフローはブロック対象外
 *
 * Changelog:
 *   - silenvx/dekita#1283: フロー有効期限機能追加
 *   - silenvx/dekita#1316: クローズ済みIssueフロー除外
 *   - silenvx/dekita#2478: セッション固有状態ファイル対応
 *   - silenvx/dekita#2494: 複数ワークフローのフェーズ集約
 *   - silenvx/dekita#3157: TypeScriptに移行
 */

import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { basename, join } from "node:path";
import { FLOW_LOG_DIR } from "../lib/common";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { type FlowStatus, getIncompleteFlows } from "../lib/flow";
import { canSkipStep, getAllPhases, getFlowDefinition } from "../lib/flow_definitions";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { createContext, parseHookInput } from "../lib/session";
import { WorkflowVerifier } from "../lib/workflow_verifier";

const HOOK_NAME = "flow-effect-verifier";

// フローの有効期限（時間）
const FLOW_EXPIRY_HOURS = 24;

// ステータス優先度
const _STATUS_PRIORITY: Record<string, number> = {
  completed: 3,
  complete: 3,
  in_progress: 2,
  partial: 2,
  pending: 1,
  not_started: 0,
};

// =============================================================================
// Types
// =============================================================================

interface SessionState {
  session_id: string;
  active_workflow: string | null;
  workflows: Record<string, WorkflowData>;
  global: {
    hooks_fired_total: number;
    session_start_time: string;
  };
}

interface WorkflowData {
  branch?: string;
  current_phase?: string;
  phases?: Record<string, PhaseData>;
  phase_start_time?: string;
}

interface PhaseData {
  status: string;
  iterations: number;
  loop_reasons?: string[];
  source?: string;
  pr_number?: number;
  pr_url?: string;
  merged_at?: string;
}

// =============================================================================
// Helper Functions
// =============================================================================

function getStateFile(sessionId: string): string {
  // Sanitize session_id to prevent path traversal
  const safeSessionId = basename(sessionId);
  return join(FLOW_LOG_DIR, `state-${safeSessionId}.json`);
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

function findMostProgressedWorkflow(state: SessionState): [string | null, WorkflowData] {
  const workflows = state.workflows;
  if (Object.keys(workflows).length === 0) {
    return [null, {}];
  }

  const allPhases = getAllPhases();
  const phaseOrder = new Map(allPhases.map((p) => [p.id, p.order]));

  // Prefer active_workflow if set and exists
  const active = state.active_workflow;
  if (active && workflows[active]) {
    return [active, workflows[active]];
  }

  // Find workflow with highest phase order
  let bestWorkflow: string | null = null;
  let bestOrder = -1;
  let bestData: WorkflowData = {};

  for (const [wfName, wfData] of Object.entries(workflows)) {
    const currentPhase = wfData.current_phase ?? "";
    const order = phaseOrder.get(currentPhase) ?? -1;
    if (order > bestOrder) {
      bestOrder = order;
      bestWorkflow = wfName;
      bestData = wfData;
    }
  }

  return [bestWorkflow, bestData];
}

function isIssueClosed(issueNumber: number): boolean {
  try {
    const result = execSync(`gh issue view ${issueNumber} --json state -q .state`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM,
    });
    return result.trim() === "CLOSED";
  } catch {
    return false;
  }
}

function filterClosedIssueFlows(flows: FlowStatus[]): FlowStatus[] {
  // Collect unique issue numbers
  const issueNumbers = new Set<number>();
  for (const flow of flows) {
    const issueNumber = flow.context?.issue_number;
    if (typeof issueNumber === "number") {
      issueNumbers.add(issueNumber);
    }
  }

  if (issueNumbers.size === 0) {
    return [...flows];
  }

  // Check issue states (sequential for simplicity in TS)
  const closedIssues = new Set<number>();
  for (const issueNum of issueNumbers) {
    if (isIssueClosed(issueNum)) {
      closedIssues.add(issueNum);
    }
  }

  // Filter flows
  return flows.filter((flow) => {
    const issueNumber = flow.context?.issue_number;
    if (typeof issueNumber === "number" && closedIssues.has(issueNumber)) {
      return false;
    }
    return true;
  });
}

function filterExpiredFlows(flows: FlowStatus[]): FlowStatus[] {
  const now = Date.now();
  const maxAgeMs = FLOW_EXPIRY_HOURS * 60 * 60 * 1000;

  return flows.filter((flow) => {
    const startedAt = flow.started_at;
    if (!startedAt) {
      return true; // No timestamp - preserve (conservative)
    }

    try {
      const flowStart = new Date(startedAt);
      if (Number.isNaN(flowStart.getTime())) {
        return true; // Invalid timestamp - preserve (conservative)
      }
      const ageMs = now - flowStart.getTime();
      return ageMs < maxAgeMs;
    } catch {
      return true; // Invalid timestamp - preserve (conservative)
    }
  });
}

function getRequiredPendingSteps(flow: FlowStatus): string[] {
  const flowId = flow.flow_id;
  const context = flow.context ?? {};
  const pending = flow.pending_steps ?? [];

  if (!flowId) {
    return pending;
  }

  const requiredPending: string[] = [];
  for (const stepId of pending) {
    if (!canSkipStep(flowId, stepId, context)) {
      requiredPending.push(stepId);
    }
  }

  return requiredPending;
}

function formatFlowSummary(flows: FlowStatus[]): string {
  if (flows.length === 0) {
    return "";
  }

  const lines: string[] = ["[flow-summary] セッション中のフロー進捗:"];

  for (const flow of flows) {
    const flowId = flow.flow_id ?? "unknown";
    const flowName = flow.flow_name ?? flowId;
    const context = flow.context ?? {};
    const completed = flow.completed_steps ?? [];
    const pending = flow.pending_steps ?? [];
    const stepCounts = flow.step_counts ?? {};

    // Format context
    let contextStr = "";
    const issueNum = context.issue_number;
    if (issueNum) {
      contextStr = ` (Issue #${issueNum})`;
    } else if (Object.keys(context).length > 0) {
      const contextParts = Object.entries(context).map(([k, v]) => `${k}: ${v}`);
      contextStr = ` (${contextParts.join(", ")})`;
    }

    const flowDef = getFlowDefinition(flowId);

    const stepLines: string[] = [];

    if (flowDef) {
      // Group steps by phase
      const phases: Map<string, Array<[string, string, boolean, number, boolean]>> = new Map();
      const phaseOrder: string[] = [];

      const sortedSteps = [...flowDef.steps].sort((a, b) => a.order - b.order);
      for (const step of sortedSteps) {
        const phase = step.phase ?? "default";
        if (!phases.has(phase)) {
          phases.set(phase, []);
          phaseOrder.push(phase);
        }

        const isCompleted = completed.includes(step.id);
        const isPending = pending.includes(step.id);
        const count = isCompleted ? (stepCounts[step.id] ?? 1) : 0;
        phases.get(phase)!.push([step.id, step.name, isCompleted, count, isPending]);
      }

      // Determine current phase
      let currentPhase: string | null = null;
      for (const phase of phaseOrder) {
        const phaseSteps = phases.get(phase)!;
        if (phaseSteps.some((s) => s[4])) {
          // s[4] = isPending
          currentPhase = phase;
          break;
        }
      }

      // Phase name mapping
      const phaseNames: Record<string, string> = {
        setup: "準備",
        implementation: "実装",
        review: "レビュー",
        complete: "完了",
        default: "ステップ",
      };

      // Render each phase
      for (const phase of phaseOrder) {
        const phaseSteps = phases.get(phase)!;
        const allComplete = !phaseSteps.some((s) => s[4]);
        const phaseDisplay = phaseNames[phase] ?? phase;

        if (allComplete) {
          stepLines.push(`[${phaseDisplay}] ✅ 完了`);
        } else if (phase === currentPhase) {
          stepLines.push(`[${phaseDisplay}]`);
          for (const [stepId, stepName, isCompleted, count, isPending] of phaseSteps) {
            if (!isCompleted && !isPending) {
              continue;
            }
            if (isCompleted) {
              const countStr = count > 1 ? ` (${count}回)` : "";
              stepLines.push(`  ✅ ${stepName}${countStr}`);
            } else if (pending.length > 0 && stepId === pending[0]) {
              stepLines.push(`  ⏳ ${stepName} ← 次のステップ`);
            } else {
              stepLines.push(`  ⬜ ${stepName}`);
            }
          }
        } else {
          stepLines.push(`[${phaseDisplay}] ⬜`);
        }
      }
    } else {
      // Fallback: flat list
      for (const stepId of completed) {
        const count = stepCounts[stepId] ?? 1;
        const countStr = count > 1 ? ` (${count}回)` : "";
        stepLines.push(`✅ ${stepId}${countStr}`);
      }

      for (let i = 0; i < pending.length; i++) {
        const stepId = pending[i];
        if (i === 0) {
          stepLines.push(`⏳ ${stepId} ← 次のステップ`);
        } else {
          stepLines.push(`⬜ ${stepId}`);
        }
      }
    }

    // Calculate box width
    const maxContentLen = Math.max(...stepLines.map((l) => l.length), 0);
    const boxWidth = Math.min(60, Math.max(30, maxContentLen + 4));

    lines.push(`\n📋 ${flowName}${contextStr}`);
    lines.push(`┌${"─".repeat(boxWidth)}┐`);
    for (const stepLine of stepLines) {
      const padded = stepLine.padEnd(boxWidth - 2);
      lines.push(`│ ${padded} │`);
    }
    lines.push(`└${"─".repeat(boxWidth)}┘`);
  }

  return lines.join("\n");
}

function formatWorkflowVerificationSummary(
  verifier: WorkflowVerifier,
  sessionId: string | null = null,
): string {
  const summary = verifier.getSummaryDict();

  const fired = (summary.fired_hooks as number) ?? 0;
  const unfired = (summary.unfired_hooks as number) ?? 0;

  let workflowName: string | null = null;
  let currentPhase: string | null = (summary.current_phase as string) ?? null;
  let workflowPhases: Record<string, PhaseData> = {};

  if (sessionId) {
    const state = loadState(sessionId);
    const [wfName, workflowData] = findMostProgressedWorkflow(state);
    workflowName = wfName;
    if (workflowData) {
      currentPhase = workflowData.current_phase ?? currentPhase;
      workflowPhases = workflowData.phases ?? {};
    }
  }

  const workflowDisplay = workflowName ? ` [${workflowName}]` : "";
  const header = `📍 ${currentPhase ?? "unknown"}${workflowDisplay} | 🪝 ${fired}/${fired + unfired}`;
  const lines: string[] = [`\n[workflow-verification] ${header}`];

  const phaseIcons: Record<string, string> = {
    completed: "✅",
    complete: "✅",
    in_progress: "⏳",
    partial: "⏳",
    pending: "⬜",
    not_started: "⬜",
    no_hooks: "➖",
  };

  if (Object.keys(workflowPhases).length > 0) {
    lines.push("");
    const allPhases = getAllPhases();
    for (const phase of allPhases) {
      const phaseState = workflowPhases[phase.id];
      const status = phaseState?.status ?? "not_started";
      const icon = phaseIcons[status] ?? "⬜";
      const marker = phase.id === currentPhase ? " ←" : "";
      lines.push(`  ${icon} ${phase.name}${marker}`);
    }
  } else {
    const phases =
      (summary.phases as Array<{ phase_id: string; phase_name: string; status: string }>) ?? [];
    if (phases.length > 0) {
      lines.push("");
      for (const p of phases) {
        const icon = phaseIcons[p.status] ?? "⬜";
        const marker = p.phase_id === currentPhase ? " ←" : "";
        lines.push(`  ${icon} ${p.phase_name}${marker}`);
      }
    }
  }

  const issues = (summary.issues as Array<{ hook: string; message: string }>) ?? [];
  if (issues.length > 0) {
    lines.push("");
    lines.push("⚠️ 検出された問題:");
    for (const issue of issues.slice(0, 5)) {
      lines.push(`  - ${issue.hook}: ${issue.message}`);
    }
    if (issues.length > 5) {
      lines.push(`  ... 他 ${issues.length - 5} 件`);
    }
  }

  return lines.join("\n");
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  let inputData: Awaited<ReturnType<typeof parseHookInput>>;
  try {
    inputData = await parseHookInput();
  } catch {
    // Fail open on malformed input (matches Python behavior)
    console.log(JSON.stringify({ ok: true }));
    return;
  }

  // Prevent infinite loops in Stop hooks
  if (inputData.stop_hook_active) {
    console.log(JSON.stringify({ ok: true }));
    return;
  }

  const ctx = createContext(inputData);
  const sessionId = ctx.sessionId;

  // Initialize workflow verifier
  const verifier = await WorkflowVerifier.create(sessionId);
  const workflowSummary = formatWorkflowVerificationSummary(verifier, sessionId);

  // Get incomplete flows
  const flowLogDir = FLOW_LOG_DIR;
  let incompleteFlows = await getIncompleteFlows(flowLogDir, sessionId ?? undefined);

  // Filter out flows for closed issues
  incompleteFlows = filterClosedIssueFlows(incompleteFlows);

  // Filter out expired flows
  incompleteFlows = filterExpiredFlows(incompleteFlows);

  if (incompleteFlows.length === 0) {
    await logHookExecution(HOOK_NAME, "approve", "No incomplete flows", undefined, { sessionId });
    console.log(JSON.stringify({ ok: true, systemMessage: workflowSummary }));
    return;
  }

  // Generate summary
  let summary = formatFlowSummary(incompleteFlows);
  summary = `${summary}\n${workflowSummary}`;

  // Filter for blocking flows
  const blockingFlows: FlowStatus[] = [];
  for (const flow of incompleteFlows) {
    const flowId = flow.flow_id;
    const flowDef = getFlowDefinition(flowId ?? "");

    if (flowDef?.blockingOnSessionEnd) {
      const requiredPending = getRequiredPendingSteps(flow);
      if (requiredPending.length > 0) {
        blockingFlows.push({
          ...flow,
          pending_steps: requiredPending,
        });
      }
    }
  }

  if (blockingFlows.length === 0) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      "No blocking flows (non-blocking incomplete flows exist)",
      {
        incomplete_flow_count: incompleteFlows.length,
      },
      { sessionId },
    );
    console.log(JSON.stringify({ ok: true, systemMessage: summary }));
    return;
  }

  // Build block message
  const messages: string[] = [];
  for (const flow of blockingFlows) {
    const flowName = flow.flow_name ?? flow.flow_id ?? "unknown";
    const pending = flow.pending_steps ?? [];
    const context = flow.context ?? {};

    let contextStr = "";
    if (Object.keys(context).length > 0) {
      const contextParts = Object.entries(context).map(([k, v]) => `${k}: ${v}`);
      contextStr = ` (${contextParts.join(", ")})`;
    }

    messages.push(`- ${flowName}${contextStr}: 未完了ステップ ${JSON.stringify(pending)}`);
  }

  const hint =
    "ヒント: フロー定義で指定されたパターンにマッチするコマンドを実行すると、ステップが完了としてマークされます。";
  const reason = `未完了のフローがあります。以下のフローを完了させてください:\n\n${messages.join("\n")}\n\n${hint}\n\n${summary}`;

  // Log blocking decision
  const blockingFlowDetails = blockingFlows.map((flow) => ({
    flow_id: flow.flow_id,
    flow_name: flow.flow_name,
    pending_steps: flow.pending_steps,
    context: flow.context,
  }));

  await logHookExecution(
    HOOK_NAME,
    "block",
    `Blocking session end: ${blockingFlows.length} incomplete flow(s)`,
    {
      blocking_flows: blockingFlowDetails,
    },
    { sessionId },
  );

  const result = makeBlockResult(HOOK_NAME, reason, ctx);
  console.log(JSON.stringify({ ...result, ok: false }));
}

if (import.meta.main) {
  main().catch((err) => {
    console.error(`[${HOOK_NAME}] Unhandled error:`, err);
    process.exit(1);
  });
}
