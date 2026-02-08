#!/usr/bin/env bun
/**
 * 振り返りスキル実行時にセッションログを自動集計して提供。
 *
 * Why:
 *   振り返り時に手動でログを確認するのは手間がかかり、見落としが発生する。
 *   自動集計することで、客観的データに基づく振り返りを促進する。
 *
 * What:
 *   - Skill(reflect)ツール呼び出しを検出
 *   - セッションのブロック回数をhook-errors.logから集計
 *   - フロー状態（現在フェーズ等）をstate-{session_id}.jsonから取得
 *   - recurring-problem-block検出情報を取得
 *   - systemMessageとしてClaudeに提供
 *
 * Remarks:
 *   - 非ブロック型（情報提供のみ）
 *   - PreToolUse:Skill フック
 *   - Python版: reflection_log_collector.py
 *
 * Changelog:
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { existsSync, readFileSync } from "node:fs";
import { basename, join } from "node:path";
import { EXECUTION_LOG_DIR, FLOW_LOG_DIR } from "../lib/common";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "reflection-log-collector";

export interface BlockSummary {
  block_count: number;
  blocks_by_hook: Record<string, number>;
}

export interface FlowStatus {
  status: string;
  current_phase?: string;
  phase_history_count?: number;
}

interface LogEntry {
  session_id?: string;
  hook?: string;
  details?: {
    blocking_problems?: Array<{ source?: string }>;
  };
}

/**
 * Get block count summary for the session.
 */
function getBlockSummary(sessionId: string): BlockSummary {
  // EXECUTION_LOG_DIR is already an absolute path from lib/common
  const errorsLog = join(EXECUTION_LOG_DIR, "hook-errors.log");

  if (!existsSync(errorsLog)) {
    return { block_count: 0, blocks_by_hook: {} };
  }

  const blocksByHook: Record<string, number> = {};

  try {
    const content = readFileSync(errorsLog, "utf-8");
    for (const line of content.split("\n")) {
      const trimmedLine = line.trim();
      if (!trimmedLine) continue;

      try {
        const entry: LogEntry = JSON.parse(trimmedLine);
        if (entry.session_id === sessionId) {
          const hook = entry.hook ?? "unknown";
          blocksByHook[hook] = (blocksByHook[hook] ?? 0) + 1;
        }
      } catch {
        // 無効なJSON行、スキップ
      }
    }
  } catch {
    return { block_count: 0, blocks_by_hook: {} };
  }

  const total = Object.values(blocksByHook).reduce((sum, count) => sum + count, 0);
  return { block_count: total, blocks_by_hook: blocksByHook };
}

/**
 * Get flow status for the session.
 */
function getFlowStatus(sessionId: string): FlowStatus {
  // FLOW_LOG_DIR is already an absolute path from lib/common
  const safeSessionId = basename(sessionId);
  const stateFile = join(FLOW_LOG_DIR, `state-${safeSessionId}.json`);

  if (!existsSync(stateFile)) {
    return { status: "no_state_file" };
  }

  try {
    const content = readFileSync(stateFile, "utf-8");
    const state = JSON.parse(content);
    const workflows = state.workflows ?? {};
    const mainWorkflow = workflows.main ?? {};
    const currentPhase = mainWorkflow.current_phase ?? "unknown";

    return {
      status: "found",
      current_phase: currentPhase,
      phase_history_count: (mainWorkflow.phase_history ?? []).length,
    };
  } catch {
    return { status: "error" };
  }
}

/**
 * Check for recurring problems detected in this session.
 */
function checkRecurringProblems(sessionId: string): string[] {
  // EXECUTION_LOG_DIR is already an absolute path from lib/common
  const errorsLog = join(EXECUTION_LOG_DIR, "hook-errors.log");

  if (!existsSync(errorsLog)) {
    return [];
  }

  const recurring = new Set<string>();

  try {
    const content = readFileSync(errorsLog, "utf-8");
    for (const line of content.split("\n")) {
      const trimmedLine = line.trim();
      if (!trimmedLine) continue;

      try {
        const entry: LogEntry = JSON.parse(trimmedLine);
        if (entry.session_id === sessionId && entry.hook === "recurring-problem-block") {
          const details = entry.details ?? {};
          for (const problem of details.blocking_problems ?? []) {
            recurring.add(problem.source ?? "unknown");
          }
        }
      } catch {
        // 無効なJSON行、スキップ
      }
    }
  } catch {
    return [];
  }

  return Array.from(recurring);
}

/**
 * Format log summary for display.
 */
export function formatLogSummary(
  blockSummary: BlockSummary,
  flowStatus: FlowStatus,
  recurringProblems: string[],
): string {
  const lines: string[] = ["📊 **セッションログ自動集計**", ""];

  // Block summary
  const total = blockSummary.block_count;
  if (total > 0) {
    lines.push(`**ブロック**: ${total}件`);
    // Top 5 hooks by block count
    const sortedHooks = Object.entries(blockSummary.blocks_by_hook)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5);
    const hookSummary = sortedHooks.map(([h, c]) => `${h}: ${c}`).join(", ");
    lines.push(`  - ${hookSummary}`);
  } else {
    lines.push("**ブロック**: 0件");
  }

  // Recurring problems
  if (recurringProblems.length > 0) {
    lines.push(`**recurring-problem-block検出**: ${recurringProblems.join(", ")}`);
  }

  // Flow status
  if (flowStatus.status === "found") {
    const phase = flowStatus.current_phase ?? "unknown";
    lines.push(`**現在フェーズ**: ${phase}`);
  }

  lines.push("");
  lines.push("💡 上記データを振り返りに活用してください。");

  return lines.join("\n");
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    const ctx = createHookContext(inputData);
    sessionId = ctx.sessionId;
    const toolName = inputData.tool_name ?? "";
    const toolInput = (inputData.tool_input as Record<string, unknown>) ?? {};

    // Only process Skill tool
    if (toolName !== "Skill") {
      console.log(JSON.stringify(result));
      return;
    }

    // Check if this is the reflecting-sessions skill
    const skillName = (toolInput.skill as string) ?? "";
    if (skillName !== "reflecting-sessions") {
      console.log(JSON.stringify(result));
      return;
    }

    // Get session ID (already extracted via createHookContext)
    if (!sessionId) {
      await logHookExecution(HOOK_NAME, "approve", "No session ID available", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Collect log data
    const blockSummary = getBlockSummary(sessionId);
    const flowStatus = getFlowStatus(sessionId);
    const recurringProblems = checkRecurringProblems(sessionId);

    // Format and add to systemMessage
    const summary = formatLogSummary(blockSummary, flowStatus, recurringProblems);
    result.systemMessage = summary;

    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Collected logs: ${blockSummary.block_count} blocks`,
      undefined,
      { sessionId },
    );
  } catch (error) {
    await logHookExecution(HOOK_NAME, "approve", `Error: ${formatError(error)}`, undefined, {
      sessionId,
    });
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
