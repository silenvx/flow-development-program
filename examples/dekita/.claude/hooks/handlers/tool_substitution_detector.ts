#!/usr/bin/env bun
/**
 * パッケージマネージャの実行とツール代替パターンを追跡。
 *
 * Why:
 *   ツールが失敗した際に、原因調査せずに別ツールに切り替えると根本問題が
 *   解決されない。パターンを記録して振り返りで分析できるようにする。
 *
 * What:
 *   - パッケージマネージャコマンド実行後（PostToolUse:Bash）に発火
 *   - uvx/npm/pip/brew/cargo等のコマンドを検出
 *   - 実行結果（成功/失敗）をセッション別ログに記録
 *   - 振り返り時に代替パターンを分析可能に
 *
 * State:
 *   - writes: .claude/logs/metrics/tool-substitution-*.jsonl
 *
 * Remarks:
 *   - 非ブロック型（記録のみ、振り返りで分析）
 *   - 検出対象はTOOL_PATTERNSで定義
 *
 * Changelog:
 *   - silenvx/dekita#1887: フック追加
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { METRICS_LOG_DIR } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logToSessionFile } from "../lib/logging";
import { makeApproveResult } from "../lib/results";
import { getToolResult, parseHookInput } from "../lib/session";

const HOOK_NAME = "tool-substitution-detector";

// Tool name patterns to track
export const TOOL_PATTERNS: Record<string, RegExp> = {
  uvx: /\buvx\s+(\S+)/,
  uv: /\buv\s+(?:pip\s+install|add)\s+(\S+)/,
  npm: /\bnpm\s+(?:install|i|add)\s+(\S+)/,
  pip: /\bpip\s+install\s+(\S+)/,
  brew: /\bbrew\s+install\s+(\S+)/,
  cargo: /\bcargo\s+(?:install|add)\s+(\S+)/,
  go: /\bgo\s+(?:install|get)\s+(\S+)/,
};

/**
 * Extract tool manager and package name from command.
 */
export function extractToolInfo(
  command: string,
): { toolManager: string; packageName: string } | null {
  for (const [toolManager, pattern] of Object.entries(TOOL_PATTERNS)) {
    const match = pattern.exec(command);
    if (match) {
      // Clean up package name (remove version specifiers)
      const packageName = match[1].replace(/[@=<>].*/, "");
      return { toolManager, packageName };
    }
  }
  return null;
}

async function main(): Promise<void> {
  const data = await parseHookInput();

  // Only process Bash PostToolUse
  if (data.tool_name !== "Bash") {
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  const rawResult = getToolResult(data);
  const toolResult =
    rawResult && typeof rawResult === "object" && !Array.isArray(rawResult)
      ? (rawResult as Record<string, unknown>)
      : null;
  const toolInput = data.tool_input || {};
  const command = (toolInput as { command?: string }).command || "";
  const sessionId = data.session_id || process.env.CLAUDE_SESSION_ID || "unknown";

  // Extract tool info from command
  const info = extractToolInfo(command);

  if (!info) {
    // Not a tool installation command
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  const { toolManager, packageName } = info;

  // Check if command failed
  const stderr = String(toolResult?.stderr ?? "");
  const stdout = String(toolResult?.stdout ?? "");

  // Detect failure patterns
  let isFailure = false;
  let failureReason = "";

  // Check exit_code field directly (most reliable)
  const rawExitCode = toolResult?.exit_code;
  const exitCode = typeof rawExitCode === "number" ? rawExitCode : undefined;
  if (exitCode !== undefined && exitCode !== 0) {
    isFailure = true;
    failureReason = `non-zero exit code: ${exitCode}`;
  } else if (/error|failed/i.test(stderr)) {
    isFailure = true;
    failureReason = "stderr contains error";
  } else if (/not found|no such/i.test(stderr)) {
    isFailure = true;
    failureReason = "package/command not found";
  } else if (/error/i.test(stdout) && /not found/i.test(stdout)) {
    isFailure = true;
    failureReason = "stdout contains error";
  }

  // Log tool execution
  const logEntry = {
    timestamp: new Date().toISOString(),
    session_id: sessionId,
    type: "tool_execution",
    tool_manager: toolManager,
    package: packageName,
    command_preview: command.slice(0, 100),
    is_failure: isFailure,
    failure_reason: isFailure ? failureReason : null,
  };

  await logToSessionFile(METRICS_LOG_DIR, "tool-substitution", sessionId, logEntry);

  console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
}

if (import.meta.main) {
  main().catch((e) => {
    // Ensure hook returns approve response even on unexpected errors
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(e)}`);
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
  });
}
