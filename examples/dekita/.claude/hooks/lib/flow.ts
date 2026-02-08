/**
 * フロー有効性トラッキング機能を提供する。
 *
 * Why:
 *   ワークフローの進捗を追跡し、フローの完了状況を可視化するため。
 *
 * What:
 *   - startFlow(): フローインスタンス開始
 *   - completeFlowStep(): ステップ完了記録
 *   - completeFlow(): フロー完了記録
 *   - getFlowStatus(): フロー状態取得
 *   - getIncompleteFlows(): 未完了フロー一覧取得
 *
 * State:
 *   - writes: .claude/logs/flows/flow-progress-{session}.jsonl
 *
 * Remarks:
 *   - session_idは呼び出し元から渡される（HookContextパターン）
 *   - 重複フロー防止のためコンテキストマッチング
 *   - completion_stepでフロー完了判定をカスタマイズ可能
 *
 * Changelog:
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { basename, join } from "node:path";
import { logToSessionFile } from "./logging";
import { getLocalTimestamp } from "./timestamp";

// =============================================================================
// Types
// =============================================================================

export interface FlowStep {
  id: string;
  name: string;
  description?: string;
  condition?: string;
  phase?: string;
}

export interface FlowDefinition {
  name: string;
  description?: string;
  steps: FlowStep[];
  completion_step?: string;
}

export interface FlowStatus {
  flow_id: string;
  flow_name: string;
  flow_instance_id: string;
  expected_steps: string[];
  completed_steps: string[];
  pending_steps: string[];
  step_counts: Record<string, number>;
  is_complete: boolean;
  has_flow_completed: boolean;
  context: Record<string, unknown>;
  started_at?: string;
}

interface FlowStartedEntry {
  event: "flow_started";
  flow_id: string;
  flow_instance_id: string;
  flow_name: string;
  expected_steps: string[];
  context: Record<string, unknown>;
  timestamp?: string;
}

interface StepCompletedEntry {
  event: "step_completed";
  flow_instance_id: string;
  step_id: string;
}

interface FlowCompletedEntry {
  event: "flow_completed";
  flow_instance_id: string;
}

type FlowLogEntry = FlowStartedEntry | StepCompletedEntry | FlowCompletedEntry;

// =============================================================================
// Flow Definitions Registry
// =============================================================================

const flowDefinitions: Map<string, FlowDefinition> = new Map();

/**
 * Register a flow definition.
 *
 * @param flowId - Unique identifier for the flow
 * @param definition - The flow definition
 */
export function registerFlowDefinition(flowId: string, definition: FlowDefinition): void {
  flowDefinitions.set(flowId, definition);
}

/**
 * Get a flow definition by ID.
 *
 * @param flowId - The flow ID
 * @returns The flow definition, or undefined if not found
 */
export function getFlowDefinition(flowId: string): FlowDefinition | undefined {
  return flowDefinitions.get(flowId);
}

/**
 * Get all registered flow definitions.
 *
 * @returns Map of flow ID to flow definition
 */
export function getAllFlowDefinitions(): Map<string, FlowDefinition> {
  return flowDefinitions;
}

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Get session-specific flow progress log file path.
 *
 * @param flowLogDir - Directory containing flow logs.
 * @param sessionId - Claude session identifier.
 * @returns Path to the session-specific flow progress file.
 */
function getFlowProgressFile(flowLogDir: string, sessionId: string): string {
  const safeSessionId = basename(sessionId);
  return join(flowLogDir, `flow-progress-${safeSessionId}.jsonl`);
}

/**
 * Generate a unique flow instance ID.
 *
 * @param sessionId - Session ID to include in the flow instance ID.
 * @returns A unique identifier combining timestamp, random component, and session ID.
 */
function generateFlowInstanceId(sessionId?: string): string {
  const now = new Date();
  const timestamp = now.toISOString().replace(/[-:T]/g, "").replace(/\..+/, "");
  const dateStr = `${timestamp.slice(0, 8)}-${timestamp.slice(8)}`;
  const ms = now.getMilliseconds().toString().padStart(3, "0");
  // Add random component to ensure uniqueness even within same millisecond
  const rand = Math.random().toString(36).slice(2, 8);
  const sid = sessionId?.slice(0, 8) ?? "unknown";
  return `${dateStr}-${ms}-${rand}-${sid}`;
}

/**
 * Parse flow progress log file and return flow data.
 *
 * @param flowLogDir - Directory containing flow logs.
 * @param sessionId - Session ID to read from.
 * @returns Parsed flow data
 */
async function parseFlowProgressLog(
  flowLogDir: string,
  sessionId: string,
): Promise<{
  flowInstances: Map<string, FlowStartedEntry>;
  completedSteps: Map<string, string[]>;
  stepCounts: Map<string, Record<string, number>>;
  completedFlows: Set<string>;
}> {
  const flowInstances = new Map<string, FlowStartedEntry>();
  const completedSteps = new Map<string, string[]>();
  const stepCounts = new Map<string, Record<string, number>>();
  const completedFlows = new Set<string>();

  const effectiveSessionId = sessionId || "unknown";
  const flowProgressLog = getFlowProgressFile(flowLogDir, effectiveSessionId);

  if (!existsSync(flowProgressLog)) {
    return { flowInstances, completedSteps, stepCounts, completedFlows };
  }

  try {
    const content = await readFile(flowProgressLog, "utf-8");
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      try {
        const entry = JSON.parse(trimmed) as FlowLogEntry;
        const instanceId = entry.flow_instance_id;
        if (!instanceId) continue;

        if (entry.event === "flow_started") {
          flowInstances.set(instanceId, entry);
          completedSteps.set(instanceId, []);
          stepCounts.set(instanceId, {});
        } else if (entry.event === "step_completed") {
          const stepId = entry.step_id;
          if (flowInstances.has(instanceId) && stepId) {
            // Track count for all completions
            const counts = stepCounts.get(instanceId) ?? {};
            counts[stepId] = (counts[stepId] ?? 0) + 1;
            stepCounts.set(instanceId, counts);

            // Keep unique list for completion checking
            const steps = completedSteps.get(instanceId) ?? [];
            if (!steps.includes(stepId)) {
              steps.push(stepId);
              completedSteps.set(instanceId, steps);
            }
          }
        } else if (entry.event === "flow_completed") {
          completedFlows.add(instanceId);
        }
      } catch {
        // 無効なJSON行、スキップ
      }
    }
  } catch {
    // Return empty data if log file can't be read
  }

  return { flowInstances, completedSteps, stepCounts, completedFlows };
}

// =============================================================================
// Main Functions
// =============================================================================

/**
 * Check if there's already an active (incomplete) flow for the given context.
 *
 * @param flowLogDir - Directory containing flow logs.
 * @param flowId - The flow type ID (e.g., "issue-ai-review")
 * @param context - Context dict to match (e.g., {"issue_number": 123})
 * @param sessionId - Session ID for file isolation.
 * @returns Existing flow instance ID if found, undefined otherwise.
 */
export async function getActiveFlowForContext(
  flowLogDir: string,
  flowId: string,
  context: Record<string, unknown>,
  sessionId?: string,
): Promise<string | undefined> {
  const effectiveSessionId = sessionId ?? "unknown";
  const { flowInstances, completedSteps, completedFlows } = await parseFlowProgressLog(
    flowLogDir,
    effectiveSessionId,
  );

  for (const [instanceId, startedEntry] of flowInstances) {
    // Skip flows that are explicitly completed
    if (completedFlows.has(instanceId)) continue;

    // Check if flow_id matches
    if (startedEntry.flow_id !== flowId) continue;

    // Check if context matches
    const entryContext = startedEntry.context ?? {};
    if (JSON.stringify(entryContext) !== JSON.stringify(context)) continue;

    // Check if flow is still incomplete (has pending steps)
    const expected = startedEntry.expected_steps ?? [];
    const completed = completedSteps.get(instanceId) ?? [];
    const pending = expected.filter((s) => !completed.includes(s));

    if (pending.length > 0) {
      return instanceId;
    }
  }

  return undefined;
}

/**
 * Start a new flow instance and return instance ID.
 *
 * Creates an entry in the flow progress log with status "started".
 * If an active (incomplete) flow already exists for the same flow_id and context,
 * returns the existing instance ID instead of creating a new one.
 *
 * @param flowLogDir - Directory for flow log files.
 * @param flowId - The flow type ID (e.g., "issue-ai-review")
 * @param context - Optional context dict (e.g., {"issue_number": 123})
 * @param sessionId - Session ID for file isolation.
 * @returns Flow instance ID (new or existing), or undefined on error.
 */
export async function startFlow(
  flowLogDir: string,
  flowId: string,
  context?: Record<string, unknown>,
  sessionId?: string,
): Promise<string | undefined> {
  // Check for existing active flow with same context
  if (context && Object.keys(context).length > 0) {
    const existingId = await getActiveFlowForContext(flowLogDir, flowId, context, sessionId);
    if (existingId) {
      return existingId;
    }
  }

  const flowDef = flowDefinitions.get(flowId);
  if (!flowDef) {
    console.error(`[flow] Warning: Unknown flow_id '${flowId}'`);
    return undefined;
  }

  const instanceId = generateFlowInstanceId(sessionId);

  // Extract step IDs
  const expectedSteps = flowDef.steps.map((s) => s.id).filter(Boolean);

  // Skip if session_id is not provided
  if (!sessionId) {
    console.error("[flow] Warning: session_id not provided, skipping flow log");
    return instanceId;
  }

  const entry: Record<string, unknown> = {
    timestamp: getLocalTimestamp(),
    session_id: sessionId,
    event: "flow_started",
    flow_id: flowId,
    flow_instance_id: instanceId,
    flow_name: flowDef.name,
    expected_steps: expectedSteps,
    context: context ?? {},
  };

  const success = await logToSessionFile(flowLogDir, "flow-progress", sessionId, entry);

  if (!success) {
    console.error("[flow] Warning: Failed to write flow log");
    return undefined;
  }

  return instanceId;
}

/**
 * Mark a flow as completed.
 *
 * @param flowLogDir - Directory for flow log files.
 * @param flowInstanceId - The flow instance ID from startFlow()
 * @param flowId - Optional flow ID for the log entry
 * @param sessionId - Session ID for file isolation.
 * @returns True if recorded successfully, false on error.
 */
export async function completeFlow(
  flowLogDir: string,
  flowInstanceId: string,
  flowId?: string,
  sessionId?: string,
): Promise<boolean> {
  if (!sessionId) {
    console.error("[flow] Warning: session_id not provided, skipping flow completion log");
    return true; // Consider success to not block caller
  }

  const entry: Record<string, unknown> = {
    timestamp: getLocalTimestamp(),
    session_id: sessionId,
    event: "flow_completed",
    flow_instance_id: flowInstanceId,
  };

  if (flowId) {
    entry.flow_id = flowId;
  }

  return logToSessionFile(flowLogDir, "flow-progress", sessionId, entry);
}

/**
 * Check if a flow is complete and record flow_completed event if so.
 */
async function checkAndCompleteFlow(
  flowLogDir: string,
  flowInstanceId: string,
  sessionId?: string,
): Promise<boolean> {
  const status = await getFlowStatus(flowLogDir, flowInstanceId, sessionId);
  if (!status) {
    return false;
  }

  // Skip if flow_completed event already exists
  if (status.has_flow_completed) {
    return false;
  }

  const flowDef = status.flow_id ? flowDefinitions.get(status.flow_id) : undefined;
  let isComplete = false;

  // Check completion via completion_step (takes priority)
  if (flowDef?.completion_step) {
    if (status.completed_steps.includes(flowDef.completion_step)) {
      isComplete = true;
    }
  }

  // Fall back to all steps completed
  if (!isComplete) {
    isComplete = status.pending_steps.length === 0;
  }

  if (isComplete) {
    return completeFlow(flowLogDir, flowInstanceId, status.flow_id, sessionId);
  }

  return false;
}

/**
 * Mark a flow step as completed.
 *
 * @param flowLogDir - Directory for flow log files.
 * @param flowInstanceId - The flow instance ID from startFlow()
 * @param stepId - The step ID to mark as completed
 * @param flowId - Optional flow type ID for logging
 * @param sessionId - Session ID for file isolation.
 * @returns True if recorded successfully, false on error.
 */
export async function completeFlowStep(
  flowLogDir: string,
  flowInstanceId: string,
  stepId: string,
  flowId?: string,
  sessionId?: string,
): Promise<boolean> {
  if (!sessionId) {
    console.error("[flow] Warning: session_id not provided, skipping step completion log");
    return true; // Consider success to not block caller
  }

  const entry: Record<string, unknown> = {
    timestamp: getLocalTimestamp(),
    session_id: sessionId,
    event: "step_completed",
    flow_instance_id: flowInstanceId,
    step_id: stepId,
    flow_id: flowId,
  };

  const success = await logToSessionFile(flowLogDir, "flow-progress", sessionId, entry);

  if (!success) {
    return false;
  }

  // Check if flow is now complete and record flow_completed event
  await checkAndCompleteFlow(flowLogDir, flowInstanceId, sessionId);

  return true;
}

/**
 * Get the current status of a flow instance.
 *
 * @param flowLogDir - Directory containing flow logs.
 * @param flowInstanceId - The flow instance ID
 * @param sessionId - Session ID for file isolation.
 * @returns Flow status, or undefined if not found.
 */
export async function getFlowStatus(
  flowLogDir: string,
  flowInstanceId: string,
  sessionId?: string,
): Promise<FlowStatus | undefined> {
  const effectiveSessionId = sessionId ?? "unknown";
  const flowProgressLog = getFlowProgressFile(flowLogDir, effectiveSessionId);

  if (!existsSync(flowProgressLog)) {
    return undefined;
  }

  let flowStarted: FlowStartedEntry | undefined;
  const completedSteps: string[] = [];
  const stepCounts: Record<string, number> = {};
  let hasFlowCompleted = false;

  try {
    const content = await readFile(flowProgressLog, "utf-8");
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      try {
        const entry = JSON.parse(trimmed) as FlowLogEntry;
        if (entry.flow_instance_id === flowInstanceId) {
          if (entry.event === "flow_started") {
            flowStarted = entry;
          } else if (entry.event === "step_completed") {
            const stepId = entry.step_id;
            if (stepId) {
              stepCounts[stepId] = (stepCounts[stepId] ?? 0) + 1;
              if (!completedSteps.includes(stepId)) {
                completedSteps.push(stepId);
              }
            }
          } else if (entry.event === "flow_completed") {
            hasFlowCompleted = true;
          }
        }
      } catch {
        // 無効なJSON行、スキップ
      }
    }
  } catch {
    return undefined;
  }

  if (!flowStarted) {
    return undefined;
  }

  const expectedSteps = flowStarted.expected_steps ?? [];
  const pendingSteps = expectedSteps.filter((s) => !completedSteps.includes(s));

  // Determine if flow is complete
  let isComplete = hasFlowCompleted;

  if (!isComplete) {
    const flowDef = flowStarted.flow_id ? flowDefinitions.get(flowStarted.flow_id) : undefined;

    if (flowDef?.completion_step) {
      isComplete = completedSteps.includes(flowDef.completion_step);
    }

    if (!isComplete) {
      isComplete = pendingSteps.length === 0;
    }
  }

  return {
    flow_id: flowStarted.flow_id,
    flow_name: flowStarted.flow_name,
    flow_instance_id: flowInstanceId,
    expected_steps: expectedSteps,
    completed_steps: completedSteps,
    pending_steps: pendingSteps,
    step_counts: stepCounts,
    is_complete: isComplete,
    has_flow_completed: hasFlowCompleted,
    context: flowStarted.context ?? {},
    started_at: flowStarted.timestamp,
  };
}

/**
 * Get all incomplete flows in the current session.
 *
 * @param flowLogDir - Directory containing flow logs.
 * @param sessionId - Session ID for file isolation.
 * @returns List of incomplete flow status objects.
 */
export async function getIncompleteFlows(
  flowLogDir: string,
  sessionId?: string,
): Promise<FlowStatus[]> {
  const effectiveSessionId = sessionId ?? "unknown";
  const { flowInstances, completedSteps, stepCounts, completedFlows } = await parseFlowProgressLog(
    flowLogDir,
    effectiveSessionId,
  );

  const incomplete: FlowStatus[] = [];

  for (const [instanceId, startedEntry] of flowInstances) {
    // Skip flows that have explicit flow_completed events
    if (completedFlows.has(instanceId)) continue;

    const expected = startedEntry.expected_steps ?? [];
    const completed = completedSteps.get(instanceId) ?? [];
    const pending = expected.filter((s) => !completed.includes(s));

    // Check if flow is complete via completion_step
    const flowDef = startedEntry.flow_id ? flowDefinitions.get(startedEntry.flow_id) : undefined;
    let isComplete = pending.length === 0;

    if (!isComplete && flowDef?.completion_step) {
      if (completed.includes(flowDef.completion_step)) {
        isComplete = true;
      }
    }

    if (!isComplete) {
      incomplete.push({
        flow_id: startedEntry.flow_id,
        flow_name: startedEntry.flow_name,
        flow_instance_id: instanceId,
        expected_steps: expected,
        completed_steps: completed,
        pending_steps: pending,
        step_counts: stepCounts.get(instanceId) ?? {},
        is_complete: false,
        has_flow_completed: false,
        context: startedEntry.context ?? {},
        started_at: startedEntry.timestamp,
      });
    }
  }

  return incomplete;
}

/**
 * Check if a flow is complete.
 *
 * @param flowLogDir - Directory containing flow logs.
 * @param flowInstanceId - The flow instance ID
 * @param sessionId - Session ID for file isolation.
 * @returns True if all expected steps are completed, false otherwise.
 */
export async function checkFlowCompletion(
  flowLogDir: string,
  flowInstanceId: string,
  sessionId?: string,
): Promise<boolean> {
  const status = await getFlowStatus(flowLogDir, flowInstanceId, sessionId);
  if (!status) {
    return false;
  }
  return status.is_complete;
}
