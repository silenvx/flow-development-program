#!/usr/bin/env bun
/**
 * 外部APIコマンドの開始時刻を記録する（api-operation-loggerと連携）。
 *
 * Why:
 *   APIコマンドの実行時間を計測するため、開始時刻を記録しておく必要がある。
 *   PostToolUseフックで終了時刻と比較して実行時間を算出する。
 *
 * What:
 *   - gh, git, npmコマンドを検出
 *   - 開始時刻を一時ファイルに記録
 *   - 古いタイミングファイルをクリーンアップ
 *
 * When:
 *   - PreToolUse（Bashコマンド実行前）
 *
 * State:
 *   - writes: /tmp/claude-hooks/api-timing/{session}-{tool_use_id}.json
 *
 * Remarks:
 *   - 記録型フック（ブロックしない、開始時刻の記録のみ）
 *   - api-operation-loggerと連携（本フックがPre、loggerがPost）
 *   - 1時間以上経過した古いファイルは自動クリーンアップ
 *   - Python版: api_operation_timer.py
 *
 * Changelog:
 *   - silenvx/dekita#1176: コマンドハッシュ+タイムスタンプでの一意性確保
 *   - silenvx/dekita#2986: TypeScript版に移植
 */

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "api-operation-timer";

// Directory for temporary timing files (cross-platform)
const TIMING_DIR = join(tmpdir(), "claude-hooks", "api-timing");

// Target subcommands for each command type
export const TARGET_SUBCOMMANDS: Record<string, Set<string>> = {
  gh: new Set(["pr", "issue", "api", "run", "auth"]),
  git: new Set(["push", "pull", "commit", "worktree", "checkout", "switch", "merge", "rebase"]),
  npm: new Set(["run", "install", "test", "build", "i", "add", "t"]),
  pnpm: new Set(["run", "install", "test", "build", "i", "add", "t"]),
};

/**
 * Check if a command is a target command for timing.
 */
export function isTargetCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }

  // Check for gh commands
  const ghMatch = command.match(/(?:\bgh\s+|\/gh\s+)(\S+)/);
  if (ghMatch) {
    const subcommand = ghMatch[1].replace(/^--.*$/, ""); // Skip flags
    if (TARGET_SUBCOMMANDS.gh.has(subcommand)) {
      return true;
    }
    // Check if next word after flags is a target subcommand
    const fullMatch = command.match(/(?:\bgh\s+|\/gh\s+)(?:--\S+\s+)*(\S+)/);
    if (fullMatch && TARGET_SUBCOMMANDS.gh.has(fullMatch[1])) {
      return true;
    }
  }

  // Check for git commands
  const gitMatch = command.match(/(?:\bgit\s+|\/git\s+)(\S+)/);
  if (gitMatch) {
    const subcommand = gitMatch[1];
    if (TARGET_SUBCOMMANDS.git.has(subcommand)) {
      return true;
    }
  }

  // Check for npm/pnpm commands
  const npmMatch = command.match(/(?:\b(?:npm|pnpm)\s+|\/(?:npm|pnpm)\s+)(\S+)/);
  if (npmMatch) {
    const subcommand = npmMatch[1];
    if (TARGET_SUBCOMMANDS.npm.has(subcommand)) {
      return true;
    }
  }

  return false;
}

/**
 * Extract tool_use_id from hook input.
 */
function getToolUseId(hookInput: Record<string, unknown>): string | undefined {
  const toolUseId = hookInput.tool_use_id;
  if (typeof toolUseId === "string") {
    return toolUseId;
  }
  return undefined;
}

/**
 * Get session_id from hook input.
 */
export function getSessionId(hookInput: Record<string, unknown>): string | undefined {
  const sessionId = hookInput.session_id;
  if (typeof sessionId === "string") {
    return sessionId;
  }
  return undefined;
}

/**
 * Save the start time for an API operation.
 */
function saveStartTime(sessionId: string, toolUseId: string | undefined, command: string): void {
  try {
    mkdirSync(TIMING_DIR, { recursive: true });
  } catch {
    // Ignore mkdir errors
    return;
  }

  // Sanitize sessionId and toolUseId to prevent path traversal attacks
  const safeSessionId = basename(sessionId);
  const safeToolUseId = toolUseId ? basename(toolUseId) : undefined;

  let filename: string;
  if (safeToolUseId) {
    filename = `${safeSessionId}-${safeToolUseId}.json`;
  } else {
    // Fallback: use command hash + timestamp for uniqueness (Issue #1176)
    const cmdHash = createHash("md5").update(command).digest("hex").slice(0, 8);
    const timestamp = new Date().toISOString().replace(/[-:T.Z]/g, "");
    filename = `${safeSessionId}-cmd-${cmdHash}-${timestamp}.json`;
  }

  const timingFile = join(TIMING_DIR, filename);
  const timingData = {
    start_time: new Date().toISOString(),
    session_id: safeSessionId,
    command_preview: command.slice(0, 200),
  };

  try {
    writeFileSync(timingFile, JSON.stringify(timingData));
  } catch {
    // Ignore write errors - timing is best-effort
  }
}

/**
 * Remove timing files older than 1 hour.
 */
function cleanupOldTimingFiles(): void {
  if (!existsSync(TIMING_DIR)) {
    return;
  }

  const now = Date.now();
  const maxAgeMs = 3600 * 1000; // 1 hour

  try {
    const files = readdirSync(TIMING_DIR);
    for (const file of files) {
      if (!file.endsWith(".json")) {
        continue;
      }
      try {
        const filePath = join(TIMING_DIR, file);
        const stats = statSync(filePath);
        const age = now - stats.mtimeMs;
        if (age > maxAgeMs) {
          unlinkSync(filePath);
        }
      } catch {
        // Best-effort cleanup: ignore individual file stat/unlink failures
      }
    }
  } catch {
    // Best-effort cleanup: ignore directory read failures
  }
}

async function main(): Promise<void> {
  const result = {};

  try {
    const hookInput = await parseHookInput();

    if (!hookInput || Object.keys(hookInput).length === 0) {
      console.log(JSON.stringify(result));
      return;
    }

    const toolName = hookInput.tool_name;

    // Only process Bash tool calls
    if (toolName !== "Bash") {
      console.log(JSON.stringify(result));
      return;
    }

    const toolInput = (hookInput.tool_input as Record<string, unknown>) ?? {};
    const command = (toolInput.command as string) ?? "";

    // Check if this is a target command (gh, git, npm)
    if (!isTargetCommand(command)) {
      console.log(JSON.stringify(result));
      return;
    }

    // Get session and tool IDs
    const sessionId = getSessionId(hookInput);
    const toolUseId = getToolUseId(hookInput);

    // Periodically cleanup old timing files (sessionID不要なので早期リターン前に実行)
    cleanupOldTimingFiles();

    // セッションIDがない場合はタイミング記録をスキップ
    if (!sessionId) {
      console.log(JSON.stringify(result));
      return;
    }

    // Save start time
    saveStartTime(sessionId, toolUseId, command);

    // Log the timing start
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Started timing for: ${command.slice(0, 100)}`,
      undefined,
      { sessionId },
    );
  } catch {
    // Best effort - timing is not critical
  }

  // Always approve - this hook only records timing
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
