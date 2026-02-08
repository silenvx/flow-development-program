#!/usr/bin/env bun
/**
 * ブロック後にテキストのみ応答（ツール呼び出しなし）を検知し警告する。
 *
 * Why:
 *   AGENTS.mdでは「ブロックは代替アクションを実行せよ」と定めている。
 *   ブロック後にツール呼び出しがない場合、エージェントループが停止している。
 *
 * What:
 *   - セッション終了時にブロックパターンを分析
 *   - ブロック後にツール呼び出しがないケースを検出
 *   - 警告メッセージを表示（ブロックしない）
 *
 * State:
 *   - reads: .claude/logs/metrics/block-patterns-{session}.jsonl
 *   - reads: .claude/logs/execution/hook-execution-{session}.jsonl
 *
 * Remarks:
 *   - 分析型フック（ブロックしない、パターン分析のみ）
 *   - セッション終了時（Stop）に発火
 *   - SessionStartなど非ツールフックは除外
 *
 * Changelog:
 *   - silenvx/dekita#1967: P1改善
 *   - silenvx/dekita#1973: 非ツールフックの除外
 *   - silenvx/dekita#2282: セッションID検証によるパストラバーサル対策
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { EXECUTION_LOG_DIR, METRICS_LOG_DIR } from "../lib/common";
import { logHookExecution, readSessionLogEntries } from "../lib/logging";
import { createContext, isSafeSessionId, parseHookInput } from "../lib/session";

const HOOK_NAME = "block-response-tracker";

// Time window to check for tool calls after a block (seconds)
const RESPONSE_CHECK_WINDOW_SECONDS = 120;

// Minimum blocks without recovery to trigger warning
const MIN_UNRECOVERED_BLOCKS_FOR_WARNING = 1;

// Hooks that are not tool calls (phase transitions, state management, etc.)
// These should be excluded when counting tool calls after a block.
const NON_TOOL_HOOKS = new Set([
  "flow-state-updater",
  "block-response-tracker",
  "flow-verifier",
  "session-start",
  "session-end",
]);

export interface BlockEntry {
  type: string;
  block_id?: string;
  timestamp?: string;
  hook?: string;
  command_preview?: string;
}

export interface LogEntry extends Record<string, unknown> {
  decision?: string;
  hook?: string;
  timestamp?: string;
}

export interface AnalysisResult {
  total_blocks: number;
  recovered_blocks: number;
  unrecovered_blocks: BlockEntry[];
  text_only_blocks: BlockEntry[];
}

/**
 * Get metrics log directory
 */
function getMetricsLogDir(): string {
  // METRICS_LOG_DIR is already an absolute path from lib/common
  return METRICS_LOG_DIR;
}

/**
 * Get execution log directory
 */
function getExecutionLogDir(): string {
  // EXECUTION_LOG_DIR is already an absolute path from lib/common
  return EXECUTION_LOG_DIR;
}

/**
 * Load block patterns for this session
 */
function loadBlockPatterns(sessionId: string): BlockEntry[] {
  // Validate session_id to prevent path traversal (Issue #2282)
  if (!isSafeSessionId(sessionId)) {
    return [];
  }

  const logFile = join(getMetricsLogDir(), `block-patterns-${sessionId}.jsonl`);

  if (!existsSync(logFile)) {
    return [];
  }

  const blocks: BlockEntry[] = [];

  try {
    const content = readFileSync(logFile, "utf-8");
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      try {
        const entry = JSON.parse(trimmed) as BlockEntry;
        if (entry.type === "block") {
          blocks.push(entry);
        }
      } catch (_e) {
        // Skip invalid JSON lines
        console.error(`[${HOOK_NAME}] Failed to parse line in block patterns log: "${trimmed}"`);
      }
    }
  } catch {
    // File may not exist if no blocks occurred; safe to ignore (ENOENT is expected)
  }

  return blocks;
}

/**
 * Load block IDs that have recovery events
 */
function loadRecoveryEvents(sessionId: string): Set<string> {
  // Validate session_id to prevent path traversal (Issue #2282)
  if (!isSafeSessionId(sessionId)) {
    return new Set();
  }

  const logFile = join(getMetricsLogDir(), `block-patterns-${sessionId}.jsonl`);

  if (!existsSync(logFile)) {
    return new Set();
  }

  const recoveredIds = new Set<string>();

  try {
    const content = readFileSync(logFile, "utf-8");
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      try {
        const entry = JSON.parse(trimmed) as BlockEntry;
        const entryType = entry.type ?? "";
        if (["block_resolved", "block_recovery", "block_expired"].includes(entryType)) {
          const blockId = entry.block_id;
          if (blockId) {
            recoveredIds.add(blockId);
          }
        }
      } catch (_e) {
        // Skip invalid JSON lines
        console.error(`[${HOOK_NAME}] Failed to parse line in recovery events log: "${trimmed}"`);
      }
    }
  } catch {
    // File may not exist if no blocks occurred; safe to ignore (ENOENT is expected)
  }

  return recoveredIds;
}

/**
 * Type guard to check if entry has LogEntry properties
 */
function isLogEntry(entry: Record<string, unknown>): entry is LogEntry {
  return typeof entry.decision === "string" || entry.decision === undefined;
}

/**
 * Load tool calls from session-specific hook-execution log
 */
async function loadToolCallsFromExecutionLog(sessionId: string): Promise<LogEntry[]> {
  // Read from session-specific log file
  const entries = await readSessionLogEntries(getExecutionLogDir(), "hook-execution", sessionId);

  const toolCalls: LogEntry[] = [];

  for (const entry of entries) {
    if (!isLogEntry(entry)) continue;

    // Include tool call approvals (not blocks)
    const decision = entry.decision;
    if (decision === "approve") {
      // Exclude non-tool hooks (Issue #1973)
      const hookName = typeof entry.hook === "string" ? entry.hook : "";
      if (!NON_TOOL_HOOKS.has(hookName)) {
        toolCalls.push(entry);
      }
    }
  }

  return toolCalls;
}

/**
 * Parse timestamp string to Date
 */
export function parseTimestamp(tsStr: string): Date | null {
  if (!tsStr) return null;

  // Try parsing as ISO format first
  const date = new Date(tsStr);
  if (!Number.isNaN(date.getTime())) {
    return date;
  }

  return null;
}

/**
 * Check if there was a tool call after the block within the window
 */
export function hasToolCallAfterBlock(
  block: BlockEntry,
  toolCalls: LogEntry[],
  windowSeconds: number = RESPONSE_CHECK_WINDOW_SECONDS,
): boolean {
  const blockTsStr = block.timestamp ?? "";
  const blockTs = parseTimestamp(blockTsStr);

  if (!blockTs) {
    // Can't parse timestamp, assume tool calls happened
    return true;
  }

  for (const call of toolCalls) {
    const callTsStr = call.timestamp ?? "";
    const callTs = parseTimestamp(callTsStr);

    if (!callTs) {
      continue;
    }

    // Check if call is after the block
    if (callTs > blockTs) {
      const elapsed = (callTs.getTime() - blockTs.getTime()) / 1000;
      if (elapsed <= windowSeconds) {
        return true;
      }
    }
  }

  return false;
}

/**
 * Analyze block response patterns for the session
 */
async function analyzeBlockResponses(sessionId: string): Promise<AnalysisResult> {
  const blocks = loadBlockPatterns(sessionId);
  const recoveredIds = loadRecoveryEvents(sessionId);
  const toolCalls = await loadToolCallsFromExecutionLog(sessionId);

  const unrecovered: BlockEntry[] = [];
  const textOnly: BlockEntry[] = [];

  for (const block of blocks) {
    const blockId = block.block_id;

    // Check if this block was recovered
    if (blockId && recoveredIds.has(blockId)) {
      continue;
    }

    unrecovered.push(block);

    // Check if there were tool calls after this block
    if (!hasToolCallAfterBlock(block, toolCalls)) {
      textOnly.push(block);
    }
  }

  return {
    total_blocks: blocks.length,
    recovered_blocks: recoveredIds.size,
    unrecovered_blocks: unrecovered,
    text_only_blocks: textOnly,
  };
}

/**
 * Format warning message based on analysis results
 */
export function formatWarningMessage(analysis: AnalysisResult): string | null {
  const textOnly = analysis.text_only_blocks;
  const unrecovered = analysis.unrecovered_blocks;

  if (unrecovered.length < MIN_UNRECOVERED_BLOCKS_FOR_WARNING) {
    return null;
  }

  const lines: string[] = ["[block-response-tracker] ブロック後の行動分析結果:", ""];

  if (textOnly.length > 0) {
    lines.push(`⚠️ **${textOnly.length}件のブロック**後にツール呼び出しがありませんでした。`);
    lines.push("");
    lines.push("AGENTS.mdより:");
    lines.push(
      "> ブロックは「やり方を変えろ」という指示。停止ではなく、代替アクションを実行する。",
    );
    lines.push("");

    for (const block of textOnly.slice(0, 3)) {
      // Show max 3
      const hook = block.hook ?? "unknown";
      const preview = (block.command_preview ?? "N/A").slice(0, 50);
      lines.push(`  - ${hook}: \`${preview}...\``);
    }

    if (textOnly.length > 3) {
      lines.push(`  - ...他 ${textOnly.length - 3} 件`);
    }
  } else if (unrecovered.length > 0) {
    lines.push(`📊 ${unrecovered.length}件のブロックが未解決のままです（セッション終了時点）。`);
    lines.push("");
    lines.push("これは正常な場合もありますが、代替アクションを検討してください。");
  }

  return lines.join("\n");
}

/**
 * Main entry point
 */
async function main(): Promise<void> {
  let sessionId: string | undefined;

  try {
    // Parse input and create hook context
    const input = await parseHookInput();
    const ctx = createContext(input);

    // Get session ID (convert null to undefined for consistent type)
    sessionId = ctx.sessionId ?? undefined;

    if (!sessionId) {
      await logHookExecution(HOOK_NAME, "approve", "No session ID available", undefined, {
        sessionId: undefined,
      });
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Analyze block response patterns
    const analysis = await analyzeBlockResponses(sessionId);

    // Log analysis results
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Blocks: ${analysis.total_blocks}, Recovered: ${analysis.recovered_blocks}, ` +
        `Unrecovered: ${analysis.unrecovered_blocks.length}, Text-only: ${analysis.text_only_blocks.length}`,
      {
        total_blocks: analysis.total_blocks,
        recovered_blocks: analysis.recovered_blocks,
        unrecovered_count: analysis.unrecovered_blocks.length,
        text_only_count: analysis.text_only_blocks.length,
      },
      { sessionId },
    );

    // Format warning if needed
    const warning = formatWarningMessage(analysis);

    if (warning) {
      console.log(JSON.stringify({ continue: true, message: warning }));
    } else {
      console.log(JSON.stringify({ continue: true }));
    }
  } catch (error) {
    // Fail-open: approve on errors
    const errorMsg = error instanceof Error ? error.message : String(error);
    await logHookExecution(HOOK_NAME, "approve", `Error: ${errorMsg}`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify({ continue: true }));
  }
}

if (import.meta.main) {
  main();
}
