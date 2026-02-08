/**
 * ログレベル分離とエラーコンテキスト管理を提供する。
 *
 * Why:
 *   ログの可視化改善とデバッグ支援のため、レベル別ログ出力と
 *   エラー発生時のコンテキスト（前後の操作）キャプチャが必要。
 *
 * What:
 *   - getLogLevel(): 決定値からログレベル判定
 *   - logToLevelFile(): レベル別ファイルへのログ出力
 *   - logToSessionFile(): セッション固有ファイルへのログ出力
 *   - ErrorContextManager: エラー前後のコンテキスト管理
 *   - cleanupOldContextFiles(): 古いコンテキストファイル削除
 *
 * State:
 *   - writes: .claude/logs/execution/hook-errors.log
 *   - writes: .claude/logs/execution/hook-warnings.log
 *   - writes: .claude/logs/execution/hook-debug.log（HOOK_DEBUG_LOG=1時）
 *   - writes: .claude/logs/execution/error-context/error-context-*.jsonl
 *
 * Remarks:
 *   - リングバッファでエラー前N件の操作を保持
 *   - ファイルロック不要（Bunのappend操作はアトミック）
 *   - DEBUGログはHOOK_DEBUG_LOG=1環境変数で有効化
 *
 * Changelog:
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { createReadStream, createWriteStream } from "node:fs";
import { appendFile, mkdir, readFile, readdir, stat, unlink } from "node:fs/promises";
import { basename, join } from "node:path";
import { pipeline } from "node:stream/promises";
import { createGzip } from "node:zlib";
import { EXECUTION_LOG_DIR } from "./common";
import {
  DEBUG_LOG_FILE,
  ERROR_CONTEXT_AFTER_SIZE,
  ERROR_CONTEXT_BUFFER_SIZE,
  ERROR_CONTEXT_DIR,
  ERROR_CONTEXT_RETENTION_DAYS,
  ERROR_LOG_FILE,
  LOG_LEVEL_DEBUG_DECISIONS,
  LOG_LEVEL_ERROR_DECISIONS,
  LOG_LEVEL_WARN_DECISIONS,
  WARN_LOG_FILE,
} from "./constants";
import { getLocalTimestamp } from "./timestamp";

// =============================================================================
// Log Levels
// =============================================================================

export const LOG_LEVEL_ERROR = "ERROR";
export const LOG_LEVEL_WARN = "WARN";
export const LOG_LEVEL_INFO = "INFO";
export const LOG_LEVEL_DEBUG = "DEBUG";

export type LogLevel =
  | typeof LOG_LEVEL_ERROR
  | typeof LOG_LEVEL_WARN
  | typeof LOG_LEVEL_INFO
  | typeof LOG_LEVEL_DEBUG;

/**
 * Determine log level from hook decision value.
 *
 * @param decision - The decision value from a hook (e.g., "block", "approve")
 * @returns Log level string: "ERROR", "WARN", "INFO", or "DEBUG"
 */
export function getLogLevel(decision: string): LogLevel {
  if (LOG_LEVEL_ERROR_DECISIONS.has(decision)) {
    return LOG_LEVEL_ERROR;
  }
  if (LOG_LEVEL_WARN_DECISIONS.has(decision)) {
    return LOG_LEVEL_WARN;
  }
  if (LOG_LEVEL_DEBUG_DECISIONS.has(decision)) {
    return LOG_LEVEL_DEBUG;
  }
  return LOG_LEVEL_INFO;
}

// =============================================================================
// Level-specific Logging
// =============================================================================

/**
 * Write log entry to level-specific file.
 *
 * @param logDir - Directory for log files (typically EXECUTION_LOG_DIR)
 * @param entry - Log entry object to write
 * @param level - Log level ("ERROR", "WARN", "DEBUG")
 */
export async function logToLevelFile(
  logDir: string,
  entry: Record<string, unknown>,
  level: LogLevel,
): Promise<void> {
  let logFile: string;

  if (level === LOG_LEVEL_ERROR) {
    logFile = join(logDir, ERROR_LOG_FILE);
  } else if (level === LOG_LEVEL_WARN) {
    logFile = join(logDir, WARN_LOG_FILE);
  } else {
    // DEBUG level - only write if env var is set
    if (process.env.HOOK_DEBUG_LOG !== "1") {
      return;
    }
    logFile = join(logDir, DEBUG_LOG_FILE);
  }

  try {
    await mkdir(logDir, { recursive: true });
    await appendFile(logFile, `${JSON.stringify(entry)}\n`, "utf-8");
  } catch (error) {
    // Skip logging if file operations fail, but print to stderr for diagnosis
    console.error(`[logging] Failed to write to log file ${logFile}:`, error);
  }
}

// =============================================================================
// Error Context Manager
// =============================================================================

interface PendingCapture {
  timestamp: string;
  errorEntry: Record<string, unknown>;
  beforeEntries: Record<string, unknown>[];
  afterEntries: Record<string, unknown>[];
  logDir: string;
}

/**
 * Manages error context capture using a ring buffer.
 *
 * Maintains a ring buffer of recent log entries per session.
 * When an error (block) occurs, captures the buffer contents plus
 * subsequent operations to provide context for debugging.
 */
export class ErrorContextManager {
  private buffers: Map<string, Record<string, unknown>[]> = new Map();
  private pendingCaptures: Map<string, PendingCapture> = new Map();

  /**
   * Add a log entry to the session's ring buffer.
   *
   * @param sessionId - Claude session identifier
   * @param entry - Log entry object
   */
  addEntry(sessionId: string, entry: Record<string, unknown>): void {
    if (!sessionId) {
      return;
    }

    // Initialize buffer for new sessions
    if (!this.buffers.has(sessionId)) {
      this.buffers.set(sessionId, []);
    }

    const buffer = this.buffers.get(sessionId)!;
    buffer.push({ ...entry });

    // Keep only the most recent entries (ring buffer)
    if (buffer.length > ERROR_CONTEXT_BUFFER_SIZE) {
      buffer.shift();
    }

    // Check if we're capturing after-error context
    const pending = this.pendingCaptures.get(sessionId);
    if (pending) {
      pending.afterEntries.push({ ...entry });

      // Save context if we've captured enough after-entries
      if (pending.afterEntries.length >= ERROR_CONTEXT_AFTER_SIZE) {
        void this.savePendingContext(sessionId);
      }
    }
  }

  /**
   * Handle error occurrence and start capturing context.
   *
   * @param sessionId - Claude session identifier
   * @param errorEntry - The error log entry
   * @param logDir - Directory for error context files
   * @returns Path to the context file if saved immediately, undefined if pending
   */
  onError(sessionId: string, errorEntry: Record<string, unknown>, logDir: string): void {
    if (!sessionId) {
      return;
    }

    // Get the before-context from ring buffer
    // Note: addEntry was called before onError, so the error entry
    // is already in the buffer. Exclude it to avoid duplication.
    const buffer = this.buffers.get(sessionId) ?? [];
    const beforeEntries = buffer.length > 0 ? buffer.slice(0, -1) : [];

    // Store pending capture info
    this.pendingCaptures.set(sessionId, {
      timestamp: (errorEntry.timestamp as string) ?? new Date().toISOString(),
      errorEntry: { ...errorEntry },
      beforeEntries,
      afterEntries: [],
      logDir,
    });
  }

  /**
   * Save pending error context to file.
   */
  private async savePendingContext(sessionId: string): Promise<string | undefined> {
    const pending = this.pendingCaptures.get(sessionId);
    if (!pending) {
      return undefined;
    }

    this.pendingCaptures.delete(sessionId);
    return this.saveContext(
      pending.logDir,
      sessionId,
      pending.errorEntry,
      pending.beforeEntries,
      pending.afterEntries,
    );
  }

  /**
   * Save error context to a file.
   *
   * @param logDir - Base log directory
   * @param sessionId - Claude session identifier
   * @param errorEntry - The error log entry
   * @param beforeEntries - Log entries before the error
   * @param afterEntries - Log entries after the error
   * @returns Path to the saved context file, or undefined if save failed
   */
  async saveContext(
    logDir: string,
    sessionId: string,
    errorEntry: Record<string, unknown>,
    beforeEntries: Record<string, unknown>[],
    afterEntries: Record<string, unknown>[],
  ): Promise<string | undefined> {
    const contextDir = join(logDir, ERROR_CONTEXT_DIR);

    try {
      await mkdir(contextDir, { recursive: true });
    } catch {
      return undefined;
    }

    // Generate filename with timestamp
    const timestamp = (errorEntry.timestamp as string) ?? new Date().toISOString();
    // Convert ISO timestamp to filename-safe format
    const safeTimestamp = timestamp.replace(/:/g, "-").replace(/\+/g, "_");
    const filename = `error-context-${safeTimestamp}.jsonl`;
    const contextFile = join(contextDir, filename);

    try {
      const lines: string[] = [];

      // Write context metadata
      const metadata = {
        type: "metadata",
        session_id: sessionId,
        timestamp,
        hook: errorEntry.hook ?? "unknown",
        before_count: beforeEntries.length,
        after_count: afterEntries.length,
      };
      lines.push(JSON.stringify(metadata));

      // Write before context
      lines.push(JSON.stringify({ type: "context_before", entries: beforeEntries }));

      // Write the error entry
      lines.push(JSON.stringify({ type: "error", entry: errorEntry }));

      // Write after context
      lines.push(JSON.stringify({ type: "context_after", entries: afterEntries }));

      await appendFile(contextFile, `${lines.join("\n")}\n`, "utf-8");
      return contextFile;
    } catch {
      return undefined;
    }
  }

  /**
   * Flush any pending error context for a session.
   *
   * Called when session ends to ensure partial after-context is saved.
   *
   * @param sessionId - Claude session identifier
   * @returns Path to the saved context file, or undefined if nothing pending
   */
  async flushPending(sessionId: string): Promise<string | undefined> {
    if (this.pendingCaptures.has(sessionId)) {
      return this.savePendingContext(sessionId);
    }
    return undefined;
  }

  /**
   * Clear buffer and pending captures for a session.
   *
   * @param sessionId - Claude session identifier
   */
  clearSession(sessionId: string): void {
    this.buffers.delete(sessionId);
    this.pendingCaptures.delete(sessionId);
  }
}

// =============================================================================
// Cleanup Functions
// =============================================================================

/**
 * Remove error context files older than the retention period.
 *
 * @param logDir - Base log directory containing error-context subdirectory
 * @param maxAgeDays - Maximum age in days (defaults to ERROR_CONTEXT_RETENTION_DAYS)
 * @returns Number of files deleted
 */
export async function cleanupOldContextFiles(
  logDir: string,
  maxAgeDays: number = ERROR_CONTEXT_RETENTION_DAYS,
): Promise<number> {
  const contextDir = join(logDir, ERROR_CONTEXT_DIR);
  const cutoffTime = Date.now() - maxAgeDays * 24 * 60 * 60 * 1000;
  let deletedCount = 0;

  try {
    const files = await readdir(contextDir);

    for (const file of files) {
      if (!file.startsWith("error-context-") || !file.endsWith(".jsonl")) {
        continue;
      }

      const filePath = join(contextDir, file);
      try {
        const fileStat = await stat(filePath);
        if (fileStat.mtimeMs < cutoffTime) {
          await unlink(filePath);
          deletedCount++;
        }
      } catch {
        // ファイルアクセスエラー、スキップ
      }
    }
  } catch {
    // Directory doesn't exist or can't be read
  }

  return deletedCount;
}

// =============================================================================
// Log Compression (Issue #2932)
// =============================================================================

/**
 * Compress rotated log files (.log.1, .log.2, etc.) to gzip format.
 *
 * Scans the given directory for rotated log files and compresses them.
 * Already compressed files (.gz) are skipped.
 *
 * @param logDir - Directory containing rotated log files
 * @returns Number of files successfully compressed
 */
export async function compressRotatedLogs(logDir: string): Promise<number> {
  let compressedCount = 0;

  try {
    const files = await readdir(logDir);

    // Find all rotated log files (*.log.1, *.log.2, etc.)
    // Pattern: *.log.[0-9]+
    const rotatedFilePattern = /\.log\.\d+$/;

    for (const file of files) {
      // Skip already compressed files
      if (file.endsWith(".gz")) {
        continue;
      }

      // Skip if not a rotated log file
      if (!rotatedFilePattern.test(file)) {
        continue;
      }

      const logFile = join(logDir, file);
      const gzFile = `${logFile}.gz`;

      // Skip if already compressed
      try {
        await stat(gzFile);
        // gz exists - clean up original if it still exists (e.g., from a previous crashed run)
        try {
          await unlink(logFile);
        } catch {
          // Original already deleted or inaccessible
        }
        continue;
      } catch {
        // gzFile doesn't exist, proceed with compression
      }

      try {
        // Stream-based compression to avoid loading entire file into memory
        // This matches Python implementation's chunk-based approach (64KB chunks)
        await pipeline(createReadStream(logFile), createGzip(), createWriteStream(gzFile));

        // Remove original after successful compression
        await unlink(logFile);

        compressedCount++;
      } catch (e) {
        console.error(`Failed to compress ${logFile}:`, e);
        // Clean up partial .gz file on error
        try {
          await unlink(gzFile);
        } catch {
          // Ignore cleanup failure
        }
      }
    }
  } catch (e) {
    console.error(`Failed to read directory ${logDir}:`, e);
    // Directory access error - return what we have
  }

  return compressedCount;
}

// =============================================================================
// Global Instance
// =============================================================================

let errorContextManager: ErrorContextManager | undefined;

/**
 * Get the global error context manager instance.
 *
 * @returns The singleton ErrorContextManager instance
 */
export function getErrorContextManager(): ErrorContextManager {
  if (!errorContextManager) {
    errorContextManager = new ErrorContextManager();
  }
  return errorContextManager;
}

// =============================================================================
// Session Log Functions (Issue #1840)
// =============================================================================

/**
 * Get the path for a session-specific log file.
 *
 * @param logDir - Base directory for log files
 * @param logName - Base name of the log (e.g., "flow-progress", "api-operations")
 * @param sessionId - Claude session identifier
 * @returns Path to the session-specific log file
 *
 * @example
 * ```ts
 * getSessionLogFile(".claude/logs/flows", "flow-progress", "abc123")
 * // => ".claude/logs/flows/flow-progress-abc123.jsonl"
 * ```
 */
export function getSessionLogFile(logDir: string, logName: string, sessionId: string): string {
  // Sanitize sessionId to prevent path traversal attacks
  const safeSessionId = basename(sessionId);
  return join(logDir, `${logName}-${safeSessionId}.jsonl`);
}

/**
 * Write a log entry to a session-specific file.
 *
 * Creates the log directory if it doesn't exist.
 *
 * @param logDir - Base directory for log files
 * @param logName - Base name of the log (e.g., "flow-progress", "api-operations")
 * @param sessionId - Claude session identifier
 * @param entry - Log entry object to write (will be JSON-serialized)
 * @returns True if write succeeded, false otherwise
 *
 * @example
 * ```ts
 * await logToSessionFile(
 *   ".claude/logs/flows",
 *   "flow-progress",
 *   "abc123",
 *   { event: "phase_complete", phase: "implementation" }
 * )
 * ```
 */
export async function logToSessionFile(
  logDir: string,
  logName: string,
  sessionId: string,
  entry: Record<string, unknown>,
): Promise<boolean> {
  if (!sessionId) {
    return false;
  }

  const logFile = getSessionLogFile(logDir, logName, sessionId);

  try {
    await mkdir(logDir, { recursive: true });

    // Create a copy to avoid mutating caller's object
    const writeEntry = { ...entry };
    if (!("timestamp" in writeEntry)) {
      writeEntry.timestamp = new Date().toISOString();
    }

    await appendFile(logFile, `${JSON.stringify(writeEntry)}\n`, "utf-8");
    return true;
  } catch {
    return false;
  }
}

/**
 * Read all entries from a session-specific log file.
 *
 * @param logDir - Base directory for log files
 * @param logName - Base name of the log
 * @param sessionId - Claude session identifier
 * @returns List of log entries (empty list if file doesn't exist or on error)
 */
export async function readSessionLogEntries(
  logDir: string,
  logName: string,
  sessionId: string,
): Promise<Record<string, unknown>[]> {
  const logFile = getSessionLogFile(logDir, logName, sessionId);
  const entries: Record<string, unknown>[] = [];

  try {
    const content = await readFile(logFile, "utf-8");
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (trimmed) {
        try {
          entries.push(JSON.parse(trimmed));
        } catch {
          // 無効なJSON行、スキップ
        }
      }
    }
  } catch {
    // File doesn't exist or can't be read
  }

  return entries;
}

/**
 * Read entries from all session files for a given log name.
 *
 * Useful for cross-session analysis (e.g., recurring problem detection).
 *
 * @param logDir - Base directory for log files
 * @param logName - Base name of the log
 * @returns List of all log entries from all session files, sorted by timestamp
 */
export async function readAllSessionLogEntries(
  logDir: string,
  logName: string,
): Promise<Record<string, unknown>[]> {
  const entries: Record<string, unknown>[] = [];

  try {
    const files = await readdir(logDir);
    const pattern = new RegExp(`^${logName}-.*\\.jsonl$`);

    for (const file of files) {
      if (!pattern.test(file)) {
        continue;
      }

      const filePath = join(logDir, file);
      try {
        const content = await readFile(filePath, "utf-8");
        for (const line of content.split("\n")) {
          const trimmed = line.trim();
          if (trimmed) {
            try {
              entries.push(JSON.parse(trimmed));
            } catch {
              // 無効なJSON行、スキップ
            }
          }
        }
      } catch {
        // ファイル読み取りエラー、スキップ
      }
    }
  } catch {
    // Directory doesn't exist or can't be read
  }

  // Sort by timestamp if available
  entries.sort((a, b) => {
    const aTime = (a.timestamp as string) ?? "";
    const bTime = (b.timestamp as string) ?? "";
    return aTime.localeCompare(bTime);
  });

  return entries;
}

// =============================================================================
// Log Hook Execution (Issue #2874)
// =============================================================================

/**
 * Get execution log directory path.
 * Note: EXECUTION_LOG_DIR from lib/common is already an absolute path,
 * but we keep this function for backward compatibility with options?.executionLogDir
 */
function getExecutionLogDir(): string {
  // EXECUTION_LOG_DIR from lib/common is already absolute (worktree-aware)
  return EXECUTION_LOG_DIR;
}

/**
 * Log hook execution to centralized log files.
 *
 * Records hook execution in JSON Lines format for later analysis.
 * This helps track:
 * - Which hooks are being triggered
 * - How often hooks block vs approve
 * - Common block reasons
 * - Session and branch context for grouping
 * - Execution time for performance analysis
 *
 * @param hookName - Name of the hook (e.g., "merge-check", "cwd-check")
 * @param decision - "approve" or "block"
 * @param reason - Reason for the decision (especially for blocks)
 * @param details - Additional details (tool_name, command, etc.)
 * @param options - Additional options
 */
export async function logHookExecution(
  hookName: string,
  decision: string,
  reason?: string,
  details?: Record<string, unknown>,
  options?: {
    durationMs?: number;
    executionLogDir?: string;
    sessionId?: string;
  },
): Promise<void> {
  const executionLogDir = options?.executionLogDir ?? getExecutionLogDir();
  const sessionId = options?.sessionId;

  try {
    await mkdir(executionLogDir, { recursive: true });
  } catch {
    // Skip logging if directory can't be created
    return;
  }

  // Build log entry with context for analysis
  const logEntry: Record<string, unknown> = {
    timestamp: getLocalTimestamp(),
    session_id: sessionId ?? null,
    hook: hookName,
    decision,
  };

  if (reason) {
    logEntry.reason = reason;
  }
  if (details) {
    logEntry.details = details;
  }
  if (options?.durationMs !== undefined) {
    logEntry.duration_ms = options.durationMs;
  }

  // Determine log level from decision
  const logLevel = getLogLevel(decision);

  // Write to session-specific file
  if (sessionId) {
    await logToSessionFile(executionLogDir, "hook-execution", sessionId, logEntry);
  }

  // Write to level-specific log files
  if (logLevel === LOG_LEVEL_ERROR || logLevel === LOG_LEVEL_WARN) {
    await logToLevelFile(executionLogDir, logEntry, logLevel);
  } else if (logLevel === LOG_LEVEL_DEBUG) {
    await logToLevelFile(executionLogDir, logEntry, logLevel);
  }

  // Error context management
  if (sessionId) {
    const errorContextManager = getErrorContextManager();
    errorContextManager.addEntry(sessionId, logEntry);

    // Trigger error context capture on block
    if (logLevel === LOG_LEVEL_ERROR) {
      errorContextManager.onError(sessionId, logEntry, executionLogDir);
    }
  }
}
