#!/usr/bin/env bun
/**
 * 外部APIコマンド（gh, git, npm）の実行詳細をログ記録する。
 *
 * Why:
 *   API操作の実行時間やエラー率を分析することで、ワークフローの
 *   ボトルネックや障害パターンを特定できる。
 *
 * What:
 *   - コマンドタイプと操作種別を記録
 *   - 実行時間（ms）を計測
 *   - 終了コードと成功/失敗を記録
 *   - レート制限エラーを検出・フラグ付け
 *
 * State:
 *   - writes: .claude/logs/execution/api-operations-{session}.jsonl
 *
 * Remarks:
 *   - ログ記録型フック（ブロックしない、記録のみ）
 *   - api-operation-timerと連携（開始時刻を記録）
 *   - gh/git/npmコマンドを対象
 *
 * Changelog:
 *   - silenvx/dekita#1269: APIエラーとフォールバックのトレーサビリティ
 *   - silenvx/dekita#1564: レート制限検出の改善
 *   - silenvx/dekita#1581: URL除去によるパターンマッチング改善
 *   - silenvx/dekita#1840: セッション別ファイル出力
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, statSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { EXECUTION_LOG_DIR } from "../lib/common";
import { formatError } from "../lib/format_error";
import { getCurrentBranch } from "../lib/git";
import { logHookExecution, logToSessionFile } from "../lib/logging";
import { createHookContext, getToolResult, parseHookInput } from "../lib/session";

const HOOK_NAME = "api-operation-logger";

// Directory for temporary timing files (cross-platform)
const TIMING_DIR = join(tmpdir(), "claude-hooks", "api-timing");

// Rate limit detection patterns (case-insensitive)
const RATE_LIMIT_PATTERNS = [
  "rate_limited",
  "rate limit exceeded",
  "secondary rate limit",
  "abuse detection",
  "too many requests",
];

// Target command patterns
const TARGET_COMMAND_PATTERNS = [/^gh\s+/, /^git\s+/, /^npm\s+/];

interface ParsedCommand {
  type: string;
  operation: string;
  [key: string]: unknown;
}

/**
 * Remove URLs from a line to allow rate limit pattern matching.
 */
function removeUrlsFromLine(line: string): string {
  return line.replace(/https?:\/\/\S+/gi, "");
}

/**
 * Detect if the output indicates a rate limit error.
 */
function detectRateLimit(stdout: string, stderr: string): boolean {
  const combined = stdout + stderr;

  for (const line of combined.split("\n")) {
    const lineWithoutUrls = removeUrlsFromLine(line);
    const lineLower = lineWithoutUrls.toLowerCase();

    if (RATE_LIMIT_PATTERNS.some((pattern) => lineLower.includes(pattern))) {
      return true;
    }
  }

  return false;
}

/**
 * Truncate stderr to max bytes using UTF-8 encoding.
 */
function truncateStderrBytes(stderr: string, maxBytes = 1000): string {
  const encoded = new TextEncoder().encode(stderr);
  if (encoded.length <= maxBytes) {
    return stderr;
  }
  return new TextDecoder("utf-8", { fatal: false }).decode(encoded.slice(0, maxBytes));
}

/**
 * Check if the command is a target command (gh, git, npm).
 */
function isTargetCommand(command: string): boolean {
  return TARGET_COMMAND_PATTERNS.some((pattern) => pattern.test(command));
}

/**
 * gh CLI global flags that take an argument.
 * These flags appear before the subcommand and should be skipped.
 * Example: gh -R owner/repo pr create → operation should be "pr create"
 */
const GH_GLOBAL_FLAGS_WITH_ARG = new Set([
  "-R",
  "--repo",
  "--hostname",
  "--config",
  "-E",
  "--enterprise",
]);

/**
 * gh CLI global flags that do not take an argument.
 */
const GH_GLOBAL_FLAGS_NO_ARG = new Set(["--help", "--version"]);

/**
 * Skip gh global flags and return the index of the first subcommand.
 * @param parts - Split command parts (e.g., ["gh", "-R", "owner/repo", "pr", "create"])
 * @returns Index of the first subcommand (e.g., 3 for "pr")
 */
function skipGhGlobalFlags(parts: string[]): number {
  let i = 1; // Start after "gh"
  while (i < parts.length) {
    const part = parts[i];
    if (GH_GLOBAL_FLAGS_WITH_ARG.has(part)) {
      // Skip flag and its argument (if argument exists)
      if (i + 1 < parts.length) {
        i += 2;
      } else {
        // Flag at end without argument - treat as end
        i += 1;
        break;
      }
    } else if (GH_GLOBAL_FLAGS_NO_ARG.has(part)) {
      // Skip flag only
      i += 1;
    } else if (part.startsWith("-")) {
      // Unknown flag - could be global or subcommand flag
      // If it looks like --flag=value, skip it
      if (part.includes("=")) {
        i += 1;
      } else {
        // Unknown flag without =, assume it's part of subcommand
        break;
      }
    } else {
      // Not a flag, this is the subcommand
      break;
    }
  }
  return i;
}

/**
 * Parse the command to extract type and operation.
 */
function parseCommand(command: string): ParsedCommand | null {
  const trimmed = command.trim();

  // gh commands
  if (/^gh\s+/.test(trimmed)) {
    const parts = trimmed.split(/\s+/);
    if (parts.length >= 2) {
      const subcommandIndex = skipGhGlobalFlags(parts);
      if (subcommandIndex < parts.length) {
        // Extract up to 2 parts for operation (e.g., "pr create", "issue view")
        const operation = parts.slice(subcommandIndex, subcommandIndex + 2).join(" ");
        return { type: "gh", operation };
      }
    }
    return { type: "gh", operation: "unknown" };
  }

  // git commands
  if (/^git\s+/.test(trimmed)) {
    const parts = trimmed.split(/\s+/);
    if (parts.length >= 2) {
      return { type: "git", operation: parts[1] };
    }
    return { type: "git", operation: "unknown" };
  }

  // npm commands
  if (/^npm\s+/.test(trimmed)) {
    const parts = trimmed.split(/\s+/);
    if (parts.length >= 2) {
      return { type: "npm", operation: parts[1] };
    }
    return { type: "npm", operation: "unknown" };
  }

  return null;
}

/**
 * Extract structured result data from command output.
 *
 * This extracts key information like PR URLs, issue numbers, and merge results
 * from command output for analysis purposes.
 */
function extractResultFromOutput(
  parsed: ParsedCommand,
  stdout: string,
  stderr = "",
): Record<string, unknown> | null {
  const result: Record<string, unknown> = {};
  const combined = `${stdout}\n${stderr}`;

  if (!parsed) {
    return null;
  }

  const cmdType = parsed.type;
  const operation = (parsed.operation as string) ?? "";

  if (cmdType === "gh") {
    // Extract GitHub URLs
    const urlMatch = combined.match(/https:\/\/github\.com\/[^\s]+\/(pull|issues?)\/(\d+)/);
    if (urlMatch) {
      result.url = urlMatch[0];
      result.number = Number.parseInt(urlMatch[2], 10);
      result.resource_type = urlMatch[1].includes("pull") ? "pr" : "issue";
    }

    // Extract PR/Issue number from create output
    if (operation.includes("pr create") || operation.includes("issue create")) {
      const numMatch = combined.match(/#(\d+)/);
      if (numMatch && !result.number) {
        result.number = Number.parseInt(numMatch[1], 10);
      }
    }

    // Extract merge result
    if (operation.includes("pr merge")) {
      const lowered = combined.toLowerCase();
      if (lowered.includes("was merged")) {
        result.merged = true;
      } else if (lowered.includes("already merged")) {
        result.already_merged = true;
      }
    }
  }

  return Object.keys(result).length > 0 ? result : null;
}

/**
 * Get tool_use_id from hook input.
 */
function getToolUseId(hookInput: Record<string, unknown>): string | undefined {
  return hookInput.tool_use_id as string | undefined;
}

/**
 * Load the start time for an API operation.
 */
function loadStartTime(
  sessionId: string,
  toolUseId: string | undefined,
  command: string,
): Date | null {
  if (!existsSync(TIMING_DIR)) {
    return null;
  }

  let timingFile: string | null = null;

  // Sanitize sessionId and toolUseId to prevent path traversal attacks
  const safeSessionId = basename(sessionId);
  const safeToolUseId = toolUseId ? basename(toolUseId) : undefined;

  // Try tool_use_id first (exact match)
  if (safeToolUseId) {
    const candidate = join(TIMING_DIR, `${safeSessionId}-${safeToolUseId}.json`);
    if (existsSync(candidate)) {
      timingFile = candidate;
    }
  } else {
    // Fallback: use command hash with glob pattern
    const cmdHash = createHash("md5").update(command).digest("hex").slice(0, 8);
    const pattern = `${safeSessionId}-cmd-${cmdHash}-`;

    try {
      const files = readdirSync(TIMING_DIR);
      const matchingFiles: string[] = [];
      const now = Date.now();
      const STALE_THRESHOLD = 24 * 60 * 60 * 1000; // 24 hours

      for (const file of files) {
        // Opportunistic cleanup of stale files
        try {
          const filePath = join(TIMING_DIR, file);
          if (now - statSync(filePath).mtimeMs > STALE_THRESHOLD) {
            unlinkSync(filePath);
            continue;
          }
        } catch {
          // Ignore stat/unlink errors
        }

        if (file.startsWith(pattern) && file.endsWith(".json")) {
          matchingFiles.push(join(TIMING_DIR, file));
        }
      }

      // Also check for legacy format without timestamp
      const legacyFile = join(TIMING_DIR, `${safeSessionId}-cmd-${cmdHash}.json`);
      if (existsSync(legacyFile)) {
        matchingFiles.push(legacyFile);
      }

      if (matchingFiles.length > 0) {
        // Use the most recently created file
        timingFile = matchingFiles.reduce((a, b) => {
          const statA = statSync(a);
          const statB = statSync(b);
          return statA.mtimeMs > statB.mtimeMs ? a : b;
        });
      }
    } catch {
      // Ignore directory read errors
    }
  }

  if (!timingFile || !existsSync(timingFile)) {
    return null;
  }

  try {
    const content = readFileSync(timingFile, "utf-8");
    const timingData = JSON.parse(content);

    const startTimeStr = timingData.start_time;
    if (startTimeStr) {
      const startTime = new Date(startTimeStr);
      // Cleanup timing file
      try {
        unlinkSync(timingFile);
      } catch {
        // Best-effort cleanup
      }
      return startTime;
    }
  } catch {
    // Timing file may be corrupted or deleted
  }

  return null;
}

/**
 * Calculate duration in milliseconds from start time to now.
 */
function calculateDurationMs(startTime: Date | null): number | null {
  if (!startTime) {
    return null;
  }

  const now = new Date();
  return now.getTime() - startTime.getTime();
}

/**
 * Log an API operation to the operations log file.
 */
async function logApiOperation(
  commandType: string,
  operation: string,
  command: string,
  durationMs: number | null,
  exitCode: number,
  success: boolean,
  parsed: ParsedCommand,
  result: Record<string, unknown> | null,
  sessionId: string,
  branch: string | null,
  stderr: string | null,
  rateLimitDetected: boolean,
): Promise<void> {
  const logEntry: Record<string, unknown> = {
    timestamp: new Date().toISOString(),
    session_id: sessionId,
    type: commandType,
    operation,
    command: command.slice(0, 500),
    exit_code: exitCode,
    success,
  };

  if (durationMs !== null) {
    logEntry.duration_ms = durationMs;
  }

  // Include parsed info but exclude redundant fields
  const parsedInfo: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(parsed)) {
    if (k !== "type" && k !== "operation") {
      parsedInfo[k] = v;
    }
  }
  if (Object.keys(parsedInfo).length > 0) {
    logEntry.parsed = parsedInfo;
  }

  // Include extracted result data (URLs, PR numbers, etc.)
  if (result) {
    logEntry.result = result;
  }

  if (branch) {
    logEntry.branch = branch;
  }

  if (!success && stderr) {
    logEntry.error = truncateStderrBytes(stderr);
  }

  if (rateLimitDetected) {
    logEntry.rate_limit_detected = true;
  }

  // EXECUTION_LOG_DIR is already an absolute path from lib/common
  await logToSessionFile(EXECUTION_LOG_DIR, "api-operations", sessionId, logEntry);
}

async function main(): Promise<void> {
  try {
    const hookInput = await parseHookInput();
    const ctx = createHookContext(hookInput);

    const toolName = hookInput.tool_name ?? "";
    const toolInput = (hookInput.tool_input ?? {}) as Record<string, unknown>;
    const rawResult = getToolResult(hookInput);
    const toolResult = typeof rawResult === "object" && rawResult ? rawResult : {};

    // Only process Bash tool calls
    if (toolName !== "Bash") {
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    const command = (toolInput.command as string) ?? "";

    // Check if this is a target command
    if (!isTargetCommand(command)) {
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Parse the command
    const parsed = parseCommand(command);
    if (!parsed) {
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Get session and timing info
    const sessionId = ctx.sessionId;

    // セッションIDがない場合はログ記録をスキップ
    if (!sessionId) {
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    const toolUseId = getToolUseId(hookInput);
    const startTime = loadStartTime(sessionId, toolUseId, command);
    const durationMs = calculateDurationMs(startTime);

    // Extract result info
    const stdout = ((toolResult as Record<string, unknown>).stdout as string) ?? "";
    const stderr = ((toolResult as Record<string, unknown>).stderr as string) ?? "";
    const exitCode = ((toolResult as Record<string, unknown>).exit_code as number) ?? 0;
    const success = exitCode === 0;

    // Detect rate limit errors (only for failed commands)
    const rateLimitDetected = !success && detectRateLimit(stdout, stderr);

    // Extract result info (e.g., PR URLs, issue numbers)
    // Also extract on failure to capture info like "PR already exists: <URL>"
    const result = extractResultFromOutput(parsed, stdout, stderr);

    // Get branch context
    const branch = await getCurrentBranch();

    // Log the operation
    await logApiOperation(
      parsed.type,
      parsed.operation,
      command,
      durationMs,
      exitCode,
      success,
      parsed,
      result,
      sessionId,
      branch,
      success ? null : stderr,
      rateLimitDetected,
    );

    // Also log to hook execution log for consistency
    const hookDetails: Record<string, unknown> = {
      type: parsed.type,
      operation: parsed.operation,
      success,
      duration_ms: durationMs,
    };
    if (rateLimitDetected) {
      hookDetails.rate_limit_detected = true;
    }
    if (!success) {
      hookDetails.exit_code = exitCode;
    }

    await logHookExecution(HOOK_NAME, "approve", undefined, hookDetails, { sessionId });
  } catch {
    // フック実行の失敗でClaude Codeをブロックしない
  }

  console.log(JSON.stringify({ continue: true }));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    process.exit(1);
  });
}

// Export for testing
export { extractResultFromOutput, parseCommand, skipGhGlobalFlags };
