#!/usr/bin/env bun
/**
 * 探索深度（Read/Glob/Grep使用回数）を追跡する。
 *
 * Why:
 *   十分なコード探索が行われていれば、明示的なWeb検索がなくても
 *   十分な調査が行われたと見なせる。探索深度を記録することで、
 *   research-requirement-check.pyがバイパス判断に利用できる。
 *
 * What:
 *   - Read/Glob/Grepツールの使用を検出
 *   - セッションごとにカウントを記録
 *   - 合計探索回数を更新
 *
 * State:
 *   - writes: .claude/state/session/exploration-depth-{session}.json
 *
 * Remarks:
 *   - 記録型フック（ブロックしない、カウント記録）
 *   - PostToolUse:Read/Glob/Grepで発火
 *   - research-requirement-check.pyが探索深度をバイパス判断に利用
 *   - セッションごとに独立したカウント管理
 *
 * Changelog:
 *   - silenvx/dekita#2545: HookContextパターンに移行
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { SESSION_DIR } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "exploration-tracker";

export interface ExplorationData {
  counts: {
    Read: number;
    Glob: number;
    Grep: number;
  };
  session_start: string;
  total?: number;
  last_updated?: string;
}

/**
 * Get session-specific exploration file path.
 */
export function getExplorationFile(sessionId: string): string {
  return `${SESSION_DIR}/exploration-depth-${sessionId || "unknown"}.json`;
}

/**
 * Load existing exploration data or create empty structure.
 */
export function loadExplorationData(sessionId: string): ExplorationData {
  const explorationFile = getExplorationFile(sessionId);
  try {
    if (existsSync(explorationFile)) {
      const content = readFileSync(explorationFile, "utf-8");
      return JSON.parse(content);
    }
  } catch {
    // Corrupt or unreadable file - start fresh with empty structure
  }
  return {
    counts: { Read: 0, Glob: 0, Grep: 0 },
    session_start: new Date().toISOString(),
  };
}

/**
 * Save exploration data atomically.
 */
export function saveExplorationData(sessionId: string, data: ExplorationData): void {
  const explorationFile = getExplorationFile(sessionId);
  mkdirSync(SESSION_DIR, { recursive: true });
  const tempFile = `${explorationFile}.tmp`;
  try {
    writeFileSync(tempFile, JSON.stringify(data, null, 2));
    renameSync(tempFile, explorationFile);
  } catch {
    // Fail silently - tracking is non-critical
  }
}

/**
 * Increment exploration count for a tool and return updated stats.
 */
export function incrementExploration(sessionId: string, toolName: string): ExplorationData {
  const data = loadExplorationData(sessionId);
  if (toolName in data.counts) {
    data.counts[toolName as keyof typeof data.counts]++;
  }
  data.total = Object.values(data.counts).reduce((a, b) => a + b, 0);
  data.last_updated = new Date().toISOString();
  saveExplorationData(sessionId, data);
  return data;
}

async function main(): Promise<void> {
  let sessionId = "unknown";

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id || "unknown";
    const toolName = inputData.tool_name || "";

    // Only track exploration tools
    if (toolName === "Read" || toolName === "Glob" || toolName === "Grep") {
      const _stats = incrementExploration(sessionId, toolName);
      logHookExecution(HOOK_NAME, "approve", `Recorded ${toolName} exploration`);
    }
  } catch (error) {
    // Invalid input - continue without tracking
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
  }

  // Always continue (PostToolUse hook)
  console.log(JSON.stringify({ continue: true }));
}

if (import.meta.main) {
  main();
}
