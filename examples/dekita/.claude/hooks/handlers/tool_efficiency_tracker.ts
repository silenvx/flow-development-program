#!/usr/bin/env bun
/**
 * ツール呼び出しパターンを追跡し非効率なパターンを検出。
 *
 * Why:
 *   同じファイルの繰り返し読み書きや、同じ検索パターンの重複実行は非効率。
 *   これらのパターンを検出して警告することで、作業効率を向上させる。
 *
 * What:
 *   - 全ツール実行後（PostToolUse）に発火
 *   - ツール呼び出し履歴をセッション単位で記録
 *   - 非効率パターンを検出して警告（Read→Edit繰り返し、検索重複等）
 *   - 検出結果をメトリクスログに記録
 *
 * State:
 *   - reads/writes: /tmp/claude-hooks/tool-sequence.json（呼び出し履歴）
 *   - writes: .claude/logs/metrics/tool-efficiency-metrics.log
 *
 * Remarks:
 *   - 非ブロック型（警告のみ）
 *   - 10分ウィンドウ内のパターンを検出
 *   - セッション変更時に履歴リセット
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#1630: 高頻度Rework検出追加
 *   - silenvx/dekita#2607: HookContextパターン移行
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { METRICS_LOG_DIR } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { createHookContext, getToolResult, parseHookInput } from "../lib/session";

const HOOK_NAME = "tool-efficiency-tracker";

// Time window for pattern detection (minutes)
export const PATTERN_WINDOW_MINUTES = 10;

// Tracking file location (session-specific to avoid collisions)
const TRACKING_DIR = join(tmpdir(), "claude-hooks");

/**
 * Get session-specific tracking file path.
 * Using session ID in filename prevents race conditions between concurrent sessions.
 */
function getToolTrackingFile(sessionId: string): string {
  const shortId = sessionId.slice(0, 8);
  return join(TRACKING_DIR, `tool-sequence-${shortId}.json`);
}

// Persistent log for analysis
const projectDir = process.env.CLAUDE_PROJECT_DIR ?? process.cwd();
const TOOL_EFFICIENCY_LOG = join(projectDir, METRICS_LOG_DIR, "tool-efficiency-metrics.log");

// Maximum number of tool calls to keep in history
const MAX_HISTORY_SIZE = 50;

export interface CallRecord {
  timestamp: string;
  tool: string;
  target: string | null;
  success: boolean;
}

interface ToolHistory {
  calls: CallRecord[];
  session_id: string | null;
}

export interface PatternResult {
  pattern: string;
  [key: string]: unknown;
}

/**
 * Load tool call history from session-specific file.
 */
function loadToolHistory(trackingFile: string): ToolHistory {
  if (existsSync(trackingFile)) {
    try {
      return JSON.parse(readFileSync(trackingFile, "utf-8"));
    } catch {
      // Best effort - corrupted tracking data is ignored
    }
  }
  return { calls: [], session_id: null };
}

/**
 * Save tool call history to session-specific file.
 */
function saveToolHistory(trackingFile: string, data: ToolHistory): void {
  try {
    mkdirSync(TRACKING_DIR, { recursive: true });
    writeFileSync(trackingFile, JSON.stringify(data, null, 2));
  } catch {
    // Silently ignore write errors
  }
}

/**
 * Log efficiency event for later analysis.
 */
function logEfficiencyEvent(
  patternName: string,
  description: string,
  details: object,
  sessionId: string,
): void {
  try {
    const logDir = join(projectDir, METRICS_LOG_DIR);
    mkdirSync(logDir, { recursive: true });

    const entry = {
      timestamp: new Date().toISOString(),
      session_id: sessionId,
      type: "inefficiency_detected",
      pattern_name: patternName,
      description,
      details,
    };
    appendFileSync(TOOL_EFFICIENCY_LOG, `${JSON.stringify(entry)}\n`);
  } catch {
    // ログ書き込み失敗はサイレントに無視
  }
}

/**
 * Extract the target (file/pattern) from tool input.
 */
export function extractTarget(toolName: string, toolInput: Record<string, unknown>): string | null {
  if (toolName === "Read" || toolName === "Edit" || toolName === "Write") {
    return (toolInput.file_path as string) ?? null;
  }
  if (toolName === "Glob") {
    return (toolInput.pattern as string) ?? null;
  }
  if (toolName === "Grep") {
    return (toolInput.pattern as string) ?? null;
  }
  if (toolName === "Bash") {
    const command = (toolInput.command as string) ?? "";
    return command.slice(0, 100); // First 100 chars
  }
  return null;
}

/**
 * Detect Read → Edit → Read → Edit pattern on same file.
 */
export function detectReadEditLoop(calls: CallRecord[]): PatternResult | null {
  if (calls.length < 4) {
    return null;
  }

  // Look at last 6 calls
  const recent = calls.slice(-6);

  // Find Read-Edit pairs on the same file
  const fileEditCounts: Map<string, number> = new Map();

  for (let i = 0; i < recent.length; i++) {
    const call = recent[i];
    if (call.tool === "Edit" && call.target) {
      const target = call.target;
      // Check if preceded by Read on same file
      for (let j = Math.max(0, i - 2); j < i; j++) {
        if (recent[j].tool === "Read" && recent[j].target === target) {
          fileEditCounts.set(target, (fileEditCounts.get(target) ?? 0) + 1);
          break;
        }
      }
    }
  }

  // Report if any file had 2+ Read-Edit cycles
  for (const [filePath, count] of fileEditCounts) {
    if (count >= 2) {
      return {
        pattern: "read_edit_loop",
        file: filePath,
        cycles: count,
      };
    }
  }

  return null;
}

/**
 * Detect repeated Glob/Grep with similar patterns.
 */
export function detectRepeatedSearch(calls: CallRecord[]): PatternResult | null {
  const recent = calls.slice(-10);

  const searchPatterns: Map<string, number> = new Map();

  for (const call of recent) {
    if ((call.tool === "Glob" || call.tool === "Grep") && call.target) {
      const pattern = call.target.toLowerCase();
      searchPatterns.set(pattern, (searchPatterns.get(pattern) ?? 0) + 1);
    }
  }

  // Report if any pattern was searched 3+ times
  for (const [pattern, count] of searchPatterns) {
    if (count >= 3) {
      return {
        pattern: "repeated_search",
        search_pattern: pattern,
        count,
      };
    }
  }

  return null;
}

/**
 * Detect repeated Bash command failures.
 */
export function detectBashRetry(calls: CallRecord[]): PatternResult | null {
  const bashCalls = calls.slice(-10).filter((c) => c.tool === "Bash");

  if (bashCalls.length < 3) {
    return null;
  }

  const failures = bashCalls.filter((c) => !c.success);
  if (failures.length >= 3) {
    return {
      pattern: "bash_retry",
      failure_count: failures.length,
      commands: failures.slice(-3).map((c) => (c.target ?? "").slice(0, 50)),
    };
  }

  return null;
}

/**
 * Detect high-frequency rework on the same file.
 */
export function detectHighFrequencyRework(calls: CallRecord[], now: Date): PatternResult | null {
  // Filter to 5-minute window
  const window5min = new Date(now.getTime() - 5 * 60 * 1000);
  const recent5min = calls.filter((c) => new Date(c.timestamp) > window5min);

  // Filter to Edit calls with targets
  const editCalls = recent5min.filter((c) => c.tool === "Edit" && c.target);

  if (editCalls.length < 3) {
    return null;
  }

  // Count edits per file
  const fileEditCounts: Map<string, number> = new Map();
  for (const call of editCalls) {
    const target = call.target!;
    fileEditCounts.set(target, (fileEditCounts.get(target) ?? 0) + 1);
  }

  // Find files with 3+ edits
  for (const [filePath, count] of fileEditCounts) {
    if (count >= 3) {
      return {
        pattern: "high_frequency_rework",
        file: filePath,
        edit_count: count,
      };
    }
  }

  return null;
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    const hookInput = await parseHookInput();
    const ctx = createHookContext(hookInput);
    sessionId = ctx.sessionId ?? undefined;
    const toolName = hookInput.tool_name ?? "";
    const toolInput = (hookInput.tool_input ?? {}) as Record<string, unknown>;
    const rawResult = getToolResult(hookInput);
    const toolResult =
      typeof rawResult === "object" && rawResult ? (rawResult as Record<string, unknown>) : {};

    // Skip if no tool name
    if (!toolName) {
      await logHookExecution(HOOK_NAME, "approve", "no tool name", {}, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    const now = new Date();
    const currentSession = ctx.sessionId ?? "unknown";
    const trackingFile = getToolTrackingFile(currentSession);

    // Load history from session-specific file
    let history = loadToolHistory(trackingFile);

    // Reset if session changed (shouldn't happen with session-specific files, but kept for safety)
    if (history.session_id !== currentSession) {
      history = { calls: [], session_id: currentSession };
    }

    // Determine success (for Bash, check exit code, error, and blocked fields)
    let success = true;
    if (toolName === "Bash") {
      const exitCode = typeof toolResult.exit_code === "number" ? toolResult.exit_code : 0;
      // Check exit code, error, and blocked fields (e.g., timeout, system errors, or pre-hook blocks)
      success = exitCode === 0 && !toolResult.error && !toolResult.blocked;
    }

    // Create call record
    const callRecord: CallRecord = {
      timestamp: now.toISOString(),
      tool: toolName,
      target: extractTarget(toolName, toolInput),
      success,
    };

    // Add to history
    history.calls.push(callRecord);

    // Trim history to max size
    if (history.calls.length > MAX_HISTORY_SIZE) {
      history.calls = history.calls.slice(-MAX_HISTORY_SIZE);
    }

    // Save updated history
    saveToolHistory(trackingFile, history);

    // Filter to recent calls within window
    const windowStart = new Date(now.getTime() - PATTERN_WINDOW_MINUTES * 60 * 1000);
    const recentCalls = history.calls.filter((c) => new Date(c.timestamp) > windowStart);

    // Detect patterns
    const patternsDetected: PatternResult[] = [];

    const readEdit = detectReadEditLoop(recentCalls);
    if (readEdit) {
      patternsDetected.push(readEdit);
    }

    const repeated = detectRepeatedSearch(recentCalls);
    if (repeated) {
      patternsDetected.push(repeated);
    }

    const bashRetry = detectBashRetry(recentCalls);
    if (bashRetry) {
      patternsDetected.push(bashRetry);
    }

    const rework = detectHighFrequencyRework(recentCalls, now);
    if (rework) {
      patternsDetected.push(rework);
    }

    // Log and report patterns
    if (patternsDetected.length > 0) {
      for (const pattern of patternsDetected) {
        const patternName = pattern.pattern;
        if (patternName === "read_edit_loop") {
          logEfficiencyEvent(
            patternName,
            `ファイル ${pattern.file} で Read→Edit が ${pattern.cycles} 回繰り返し`,
            pattern,
            currentSession,
          );
        } else if (patternName === "repeated_search") {
          logEfficiencyEvent(
            patternName,
            `パターン '${pattern.search_pattern}' を ${pattern.count} 回検索`,
            pattern,
            currentSession,
          );
        } else if (patternName === "bash_retry") {
          logEfficiencyEvent(
            patternName,
            `Bashコマンドが ${pattern.failure_count} 回失敗`,
            pattern,
            currentSession,
          );
        } else if (patternName === "high_frequency_rework") {
          logEfficiencyEvent(
            patternName,
            `ファイル ${pattern.file} を ${pattern.edit_count} 回編集（高頻度Rework）`,
            pattern,
            currentSession,
          );
        }
      }

      // Show message for first pattern only
      const first = patternsDetected[0];
      if (first.pattern === "read_edit_loop") {
        result.systemMessage = `📊 効率性: ${basename(first.file as string)} の Read→Edit が ${first.cycles} 回繰り返し。\n事前調査で編集内容を確定させると効率的です。`;
      } else if (first.pattern === "repeated_search") {
        result.systemMessage = `📊 効率性: 同じパターンを ${first.count} 回検索。\n検索結果を活用するか、Task toolで探索すると効率的です。`;
      } else if (first.pattern === "bash_retry") {
        result.systemMessage = `📊 効率性: Bashコマンドが ${first.failure_count} 回失敗。\nアプローチの見直しを検討してください。`;
      } else if (first.pattern === "high_frequency_rework") {
        result.systemMessage = `📊 効率性: ${basename(first.file as string)} を ${first.edit_count} 回編集（高頻度Rework）。\n編集前に変更内容を確定させると効率的です。`;
      }

      // Output to stderr for immediate feedback
      if (result.systemMessage) {
        console.error(`[${HOOK_NAME}] ${result.systemMessage}`);
      }
    }
  } catch (error) {
    // フック実行の失敗でClaude Codeをブロックしない
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  await logHookExecution(HOOK_NAME, "approve", "tool_tracked", {}, { sessionId });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e: unknown) => {
    console.error(`[${HOOK_NAME}] Unexpected error:`, e);
    console.log(JSON.stringify({ continue: true }));
    process.exit(0);
  });
}
