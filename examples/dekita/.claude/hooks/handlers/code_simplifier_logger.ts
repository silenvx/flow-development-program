#!/usr/bin/env bun
/**
 * code-simplifier実行をログ記録する
 *
 * Why:
 *   code_simplifier_check.tsがPR作成前にcode-simplifier実行済みかを確認するため、
 *   実行時にブランチ・コミット情報を記録しておく必要がある。
 *
 * What:
 *   - Skillツールで /simplifying-code の実行を検出
 *   - ブランチ名、コミットハッシュを記録
 *   - main/masterブランチでは記録しない
 *
 * State:
 *   - writes: .claude/logs/markers/code-simplifier-{branch}.done
 *
 * Remarks:
 *   - 記録型フック（ブロックしない、マーカーファイル書き込み）
 *   - PostToolUse:.*で発火（全ツール対象、フック内でフィルタリング）
 *   - code_simplifier_check.tsと連携（マーカーファイル参照元）
 *
 * Changelog:
 *   - silenvx/dekita#3499: Task agentを廃止、simplifying-code Skillのみに対応
 *   - silenvx/dekita#3124: PostToolUse:.*に移動（matcher:"Task"がPostToolUseで機能しない問題を回避）
 *   - silenvx/dekita#3090: Task agent対応を追加
 *   - silenvx/dekita#3006: 初期実装
 */

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { formatError } from "../lib/format_error";
import { getCurrentBranch, getHeadCommitFull } from "../lib/git";
import { getMarkersDir } from "../lib/markers";
import { approveAndExit } from "../lib/results";
import { getToolResult, parseHookInput } from "../lib/session";
import { sanitizeBranchName } from "../lib/strings";

/**
 * Log code-simplifier execution to marker file
 */
export function logSimplifierExecution(branch: string, commit: string | null): void {
  const markersDir = getMarkersDir();
  if (!existsSync(markersDir)) {
    mkdirSync(markersDir, { recursive: true });
  }

  const safeBranch = sanitizeBranchName(branch);
  const logFile = `${markersDir}/code-simplifier-${safeBranch}.done`;

  const content = commit ? `${branch}:${commit}` : branch;
  writeFileSync(logFile, content);
}

/**
 * Check if Skill tool input is for simplifying-code
 */
export function isCodeSimplifierSkill(toolInput: Record<string, unknown>): boolean {
  const skill = toolInput?.skill as string | undefined;
  if (!skill) return false;

  return skill === "simplifying-code";
}

/**
 * Check if this is a simplifying-code Skill execution
 */
export function isCodeSimplifierExecution(
  toolName: string,
  toolInput: Record<string, unknown>,
): boolean {
  return toolName === "Skill" && isCodeSimplifierSkill(toolInput);
}

/**
 * Check if tool execution was successful (no error patterns in result)
 */
export function isToolResultSuccess(toolResult: unknown): boolean {
  if (!toolResult) return false;

  const resultText = typeof toolResult === "string" ? toolResult : JSON.stringify(toolResult);

  // Error patterns that indicate failure
  const errorPatterns = [
    /Unknown skill:/i,
    /Agent type ['"]?[^'"]+['"]? not found/i,
    /^Error:/i, // Error at string start
    /"error":\s*(?!null|false|""|'')/i, // JSON error key with actual error value
    /failed to/i,
  ];

  return !errorPatterns.some((pattern) => pattern.test(resultText));
}

/**
 * Debug log helper - always log to stderr for debugging
 */
function debugLog(message: string): void {
  if (process.env.CLAUDE_DEBUG === "1") {
    console.error(`[code-simplifier-logger] ${message}`);
  }
}

/**
 * メイン処理
 */
async function main(): Promise<void> {
  try {
    const input = await parseHookInput();
    const toolName = input.tool_name ?? "";
    const toolInput = input.tool_input as Record<string, unknown> | undefined;

    debugLog(`tool_name: ${toolName}`);
    debugLog(`tool_input: ${JSON.stringify(toolInput)?.slice(0, 1000)}`);

    if (!toolInput || !isCodeSimplifierExecution(toolName, toolInput)) {
      debugLog(
        `Not a code-simplifier execution. toolName=${toolName}, isExecution=${toolInput ? isCodeSimplifierExecution(toolName, toolInput) : "no toolInput"}`,
      );
      approveAndExit("code-simplifier-logger");
      return;
    }

    debugLog("Detected code-simplifier execution");

    // Check if tool execution was successful before logging
    const toolResult = getToolResult(input);
    debugLog(`toolResult: ${JSON.stringify(toolResult)?.slice(0, 500)}`);

    if (!isToolResultSuccess(toolResult)) {
      // Don't mark as executed if tool failed
      debugLog("Tool result indicates failure, not logging marker");
      approveAndExit("code-simplifier-logger");
      return;
    }

    debugLog("Tool result indicates success");

    const branch = await getCurrentBranch();
    debugLog(`Current branch: ${branch}`);

    if (branch && branch !== "main" && branch !== "master") {
      const commit = await getHeadCommitFull();
      debugLog(`Logging simplifier execution for branch=${branch}, commit=${commit}`);
      logSimplifierExecution(branch, commit);
      debugLog("Marker file created successfully");
    } else {
      debugLog(`Skipping marker creation: branch=${branch} is main/master or null`);
    }

    approveAndExit("code-simplifier-logger");
  } catch (error) {
    console.error(`[code-simplifier-logger] Hook error: ${formatError(error)}`);
    approveAndExit("code-simplifier-logger");
  }
}

// 実行（テスト時はスキップ）
if (import.meta.main) {
  main();
}
