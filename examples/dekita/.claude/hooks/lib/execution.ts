/**
 * Hook execution logging.
 *
 * Why:
 *   Track hook execution for analysis and debugging.
 *   Records hook decisions (approve/block) with context.
 *
 * What:
 *   - logHookExecution(): Log hook execution to session-specific files
 *
 * Remarks:
 *   - Simplified TypeScript version of Python lib/execution.py
 *   - Issue #3261: Created for ci_monitor TypeScript migration
 *
 * Changelog:
 *   - silenvx/dekita#3261: Initial TypeScript implementation
 */

import { execSync } from "node:child_process";
import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { basename, resolve } from "node:path";
import { getCiMonitorSessionId } from "./session";
import { asyncSpawn } from "./spawn";

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Get project directory from environment or git root.
 * Falls back to cwd if not in a git repo.
 */
function getProjectDir(): string {
  const envDir = process.env.CLAUDE_PROJECT_DIR;
  if (envDir) {
    return envDir;
  }
  try {
    return execSync("git rev-parse --show-toplevel", { encoding: "utf-8" }).trim();
  } catch {
    return process.cwd();
  }
}

/**
 * Get execution log directory path.
 */
function getExecutionLogDir(): string {
  const projectDir = getProjectDir();
  return resolve(projectDir, ".claude", "logs", "execution");
}

/**
 * Get local timestamp in ISO format.
 */
function getLocalTimestamp(): string {
  return new Date().toISOString();
}

/**
 * Get current git branch name.
 */
async function getCurrentBranch(): Promise<string | null> {
  const result = await asyncSpawn("git", ["rev-parse", "--abbrev-ref", "HEAD"], {
    timeout: 5000,
  });
  if (result.success && result.stdout) {
    return result.stdout.trim() || null;
  }
  return null;
}

// =============================================================================
// Main Functions
// =============================================================================

/**
 * Log hook execution to centralized log files (synchronous version).
 *
 * Records hook execution in JSON Lines format for later analysis.
 * Use logHookExecutionAsync when branch context is needed.
 *
 * Note: Renamed from logHookExecution to avoid name collision with
 * logging.ts's logHookExecution (async implementation) (Issue #3284).
 *
 * @param hookName - Name of the hook (e.g., "merge-check", "cwd-check")
 * @param decision - "approve" or "block"
 * @param reason - Reason for the decision (especially for blocks)
 * @param details - Additional details (tool_name, command, etc.)
 * @param durationMs - Hook execution time in milliseconds (optional)
 */
export function logHookExecutionSync(
  hookName: string,
  decision: string,
  reason?: string | null,
  details?: Record<string, unknown> | null,
  durationMs?: number | null,
): void {
  const executionLogDir = getExecutionLogDir();
  const sessionId = getCiMonitorSessionId();

  try {
    if (!existsSync(executionLogDir)) {
      mkdirSync(executionLogDir, { recursive: true });
    }
  } catch {
    // Skip logging if directory can't be created
    return;
  }

  // Build log entry
  const logEntry: Record<string, unknown> = {
    timestamp: getLocalTimestamp(),
    session_id: sessionId,
    hook: hookName,
    decision,
  };

  // Note: Branch context would require async, skipping for simplicity
  // A future enhancement could make this function async

  if (reason) {
    logEntry.reason = reason;
  }
  if (details) {
    logEntry.details = details;
  }
  if (durationMs !== undefined && durationMs !== null) {
    logEntry.duration_ms = durationMs;
  }

  // Write to session-specific file if session_id is available
  if (sessionId) {
    const safeSessionId = basename(sessionId);
    const logFile = resolve(executionLogDir, `hook-execution-${safeSessionId}.jsonl`);
    try {
      appendFileSync(logFile, `${JSON.stringify(logEntry)}\n`);
    } catch {
      // Ignore write errors
    }
  }
}

/**
 * Async version of logHookExecution that includes branch context.
 */
export async function logHookExecutionAsync(
  hookName: string,
  decision: string,
  reason?: string | null,
  details?: Record<string, unknown> | null,
  durationMs?: number | null,
): Promise<void> {
  const executionLogDir = getExecutionLogDir();
  const sessionId = getCiMonitorSessionId();

  try {
    if (!existsSync(executionLogDir)) {
      mkdirSync(executionLogDir, { recursive: true });
    }
  } catch {
    return;
  }

  const logEntry: Record<string, unknown> = {
    timestamp: getLocalTimestamp(),
    session_id: sessionId,
    hook: hookName,
    decision,
  };

  // Add branch context
  const branch = await getCurrentBranch();
  if (branch) {
    logEntry.branch = branch;
  }

  if (reason) {
    logEntry.reason = reason;
  }
  if (details) {
    logEntry.details = details;
  }
  if (durationMs !== undefined && durationMs !== null) {
    logEntry.duration_ms = durationMs;
  }

  if (sessionId) {
    const safeSessionId = basename(sessionId);
    const logFile = resolve(executionLogDir, `hook-execution-${safeSessionId}.jsonl`);
    try {
      appendFileSync(logFile, `${JSON.stringify(logEntry)}\n`);
    } catch {
      // Ignore write errors
    }
  }
}
