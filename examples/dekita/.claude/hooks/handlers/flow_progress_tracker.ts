#!/usr/bin/env bun
/**
 * フローステップの完了を追跡する。
 *
 * Why:
 *   開発ワークフローの各ステップ（worktree作成、コミット、PR作成等）が
 *   実際に実行されたことを記録する必要がある。コマンドパターンマッチで
 *   自動的にステップ完了を検出し、flow-effect-verifierが判断に使用する。
 *
 * What:
 *   - Bashコマンドの実行を監視
 *   - flow_definitions.tsのパターンとマッチング（コンテキスト対応）
 *   - マッチした場合はステップを完了としてマーク
 *   - ステップ順序の妥当性を検証（警告のみ）
 *
 * State:
 *   writes: .claude/logs/flow/flow-progress-{session_id}.jsonl
 *
 * Remarks:
 *   - 終了コードが0のコマンドのみ対象
 *   - 順序違反は警告するが、ステップは完了としてマーク（回復可能）
 *
 * Changelog:
 *   - silenvx/dekita#3051: TypeScriptに移行
 *   - silenvx/dekita#3157: flow_definitions.ts統合、順序検証追加
 */

import { basename } from "node:path";
import { FLOW_LOG_DIR } from "../lib/common";
import { completeFlowStep, getIncompleteFlows } from "../lib/flow";
import { type FlowDefinition, getFlowDefinition, validateStepOrder } from "../lib/flow_definitions";
import { getExitCode, getToolResult } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { createContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "flow-progress-tracker";

// =============================================================================
// Types
// =============================================================================

interface ActiveFlow {
  flow_instance_id: string;
  flow_id: string;
  flow_definition: FlowDefinition;
  context: Record<string, unknown>;
  pending_steps: string[];
  completed_steps: string[];
}

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Get active (incomplete) flows in current session with flow definitions.
 *
 * Uses getIncompleteFlows() from flow.ts and adds flow_definition objects
 * for context-aware pattern matching.
 *
 * @param sessionId - Session ID for file isolation.
 * @returns List of flow entries with their context, completed steps, and flow definitions.
 */
async function getActiveFlows(sessionId?: string): Promise<ActiveFlow[]> {
  const incomplete = await getIncompleteFlows(FLOW_LOG_DIR, sessionId);

  const active: ActiveFlow[] = [];
  for (const flow of incomplete) {
    const flowId = flow.flow_id;
    const flowDef = getFlowDefinition(flowId);
    if (!flowDef) {
      continue;
    }

    active.push({
      flow_instance_id: flow.flow_instance_id,
      flow_id: flowId,
      flow_definition: flowDef,
      context: flow.context,
      pending_steps: flow.pending_steps,
      completed_steps: [...flow.completed_steps],
    });
  }

  return active;
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = {
    continue: true,
  };

  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    const ctx = createContext(inputData);

    sessionId = ctx.sessionId ?? undefined;
    // Sanitize sessionId early to prevent path traversal in all uses
    const safeSessionId = sessionId ? basename(sessionId) : undefined;

    // Only process Bash tool
    const toolName = inputData.tool_name;
    if (toolName !== "Bash") {
      console.log(JSON.stringify(result));
      return;
    }

    // Get the command that was executed
    const toolInput = inputData.tool_input ?? {};
    const command = typeof toolInput.command === "string" ? toolInput.command : "";
    if (!command) {
      console.log(JSON.stringify(result));
      return;
    }

    // Only process if command succeeded (exit_code == 0)
    // getExitCode defaults to 0 for missing exit_code (success), but we want to
    // treat missing as failure (-1) to be conservative about tracking
    const toolResult = getToolResult(inputData as Record<string, unknown>);
    const exitCode = getExitCode(toolResult, -1);
    if (exitCode !== 0) {
      console.log(JSON.stringify(result));
      return;
    }

    // Get active (incomplete) flows with flow definitions
    const activeFlows = await getActiveFlows(safeSessionId);

    if (activeFlows.length === 0) {
      console.log(JSON.stringify(result));
      return;
    }

    // Check each active flow for matching steps (with context-aware matching)
    const matchedSteps: Array<{ instanceId: string; stepId: string; stepName: string }> = [];
    const orderWarnings: string[] = [];

    for (const flow of activeFlows) {
      const instanceId = flow.flow_instance_id;
      const flowId = flow.flow_id;
      const flowDef = flow.flow_definition;
      const context = flow.context;
      const completed = flow.completed_steps;

      for (const stepId of flow.pending_steps) {
        // Use context-aware pattern matching from flow_definitions.ts
        if (flowDef.matchesStep(stepId, command, context)) {
          // Validate step order
          const [valid, errorMessage] = validateStepOrder(flowId, completed, stepId);

          if (!valid && errorMessage) {
            orderWarnings.push(`順序警告: ${errorMessage}`);
            // Still mark the step as complete, but warn about order
            // This allows recovery from out-of-order execution
          }

          const step = flowDef.getStep(stepId);
          const stepName = step?.name ?? stepId;
          matchedSteps.push({ instanceId, stepId, stepName });

          await completeFlowStep(FLOW_LOG_DIR, instanceId, stepId, flowId, safeSessionId);

          // Keep local completed list in sync for subsequent validations
          completed.push(stepId);
        }
      }
    }

    // Output notification if steps were completed
    if (matchedSteps.length > 0 || orderWarnings.length > 0) {
      const messages: string[] = [];

      if (matchedSteps.length > 0) {
        const stepNames = matchedSteps.map((s) => s.stepName);
        messages.push(`ステップ完了: ${stepNames.join(", ")}`);

        // Log step completions
        await logHookExecution(
          HOOK_NAME,
          "approve",
          `Steps completed: ${stepNames.join(", ")}`,
          {
            matched_steps: matchedSteps,
          },
          { sessionId },
        );
      }

      messages.push(...orderWarnings);
      result.systemMessage = `[flow-progress-tracker] ${messages.join(" | ")}`;
    }
  } catch (error) {
    // Hook execution failure should not block Claude Code
    await logHookExecution(
      HOOK_NAME,
      "approve",
      "Hook execution error (non-blocking)",
      { error: String(error) },
      { sessionId },
    );
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
