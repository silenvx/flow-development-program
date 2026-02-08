/**
 * ブロック→成功パターンの追跡と分析を行う。
 *
 * Why:
 *   フックの有効性を分析し、ブロック後の解決パターンから
 *   学習するためにblock→success追跡が必要。
 *
 * What:
 *   - recordBlock(): ブロックイベントを記録
 *   - checkBlockResolution(): 成功時に先行ブロックと照合
 *   - _checkRecoveryAction(): 代替アクション（回復）を検出
 *
 * State:
 *   - writes: $TMPDIR/claude-hooks/recent-blocks-{session}.json
 *   - writes: .claude/logs/metrics/block-patterns-{session}.jsonl
 *
 * Remarks:
 *   - プロセス間でファイルベース永続化（各フックは別プロセス）
 *   - 60秒以内の解決をblock_resolved、超過をblock_expiredとして記録
 *   - 5分経過したエントリは自動クリーンアップ
 *
 * Changelog:
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { METRICS_LOG_DIR } from "./common";
import { logToSessionFile } from "./logging";
import { getLocalTimestamp } from "./timestamp";

// =============================================================================
// Constants
// =============================================================================

/** Time window in seconds for considering a block as resolved */
export const BLOCK_RESOLUTION_WINDOW_SECONDS = 60;

/** Time window in seconds for cleanup of old entries */
export const BLOCK_CLEANUP_WINDOW_SECONDS = 300; // 5 minutes

/** Prefix length for command similarity check */
export const COMMAND_SIMILARITY_PREFIX_LENGTH = 30;

// =============================================================================
// Types
// =============================================================================

interface BlockInfo {
  block_id: string;
  hook: string;
  timestamp: number;
  command_preview?: string | null;
  retry_count?: number;
  reason?: string | null;
}

/**
 * RecentBlocks dictionary value type.
 * __last_block__ key can be undefined after clearing.
 */
type RecentBlocksValue = BlockInfo | undefined;

interface RecentBlocks {
  [key: string]: RecentBlocksValue;
}

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Get session directory for temporary state files.
 */
function getSessionDir(): string {
  return join(process.env.TMPDIR ?? tmpdir(), "claude-hooks");
}

/**
 * Get metrics log directory.
 * METRICS_LOG_DIR is already an absolute, worktree-aware path from lib/common.
 *
 * @returns Path to metrics log directory.
 */
function getMetricsLogDir(): string {
  return METRICS_LOG_DIR;
}

/**
 * Get session-specific file path for recent blocks.
 *
 * @param sessionId - Session ID, or undefined to use "unknown".
 * @returns Path to session-specific recent blocks file.
 */
function getRecentBlocksFile(sessionId?: string): string {
  const sessionDir = getSessionDir();
  // Sanitize session_id for safe use in filename
  const safeSessionId = sessionId ? sessionId.replace(/[^a-zA-Z0-9_-]/g, "_") : "unknown";
  return join(sessionDir, `recent-blocks-${safeSessionId}.json`);
}

/**
 * Load recent blocks from session file.
 *
 * Also performs cleanup of expired entries (older than 5 minutes).
 *
 * @param sessionId - Session ID, or undefined for fallback.
 * @returns Dict mapping command_hash to block info.
 */
async function loadRecentBlocks(sessionId?: string): Promise<RecentBlocks> {
  const blocksFile = getRecentBlocksFile(sessionId);
  try {
    const content = await readFile(blocksFile, "utf-8");
    const blocks: RecentBlocks = JSON.parse(content);

    // Cleanup: remove entries older than BLOCK_CLEANUP_WINDOW_SECONDS
    const currentTime = Date.now() / 1000;
    const maxAge = BLOCK_CLEANUP_WINDOW_SECONDS;
    const cleaned: RecentBlocks = {};
    let needsSave = false;

    for (const [key, value] of Object.entries(blocks)) {
      if (value && currentTime - (value.timestamp ?? 0) < maxAge) {
        cleaned[key] = value;
      } else {
        needsSave = true;
      }
    }

    // Save cleaned data if entries were removed
    if (needsSave) {
      await saveRecentBlocks(cleaned, sessionId);
    }

    return cleaned;
  } catch {
    // File doesn't exist or can't be read
    return {};
  }
}

/**
 * Save recent blocks to session file.
 *
 * @param blocks - Dict mapping command_hash to block info.
 * @param sessionId - Session ID, or undefined for fallback.
 */
async function saveRecentBlocks(blocks: RecentBlocks, sessionId?: string): Promise<void> {
  const blocksFile = getRecentBlocksFile(sessionId);
  try {
    const sessionDir = getSessionDir();
    await mkdir(sessionDir, { recursive: true });
    await writeFile(blocksFile, JSON.stringify(blocks), "utf-8");
  } catch (error) {
    // Don't fail if save fails, but log the error for debugging
    console.error(`[block_patterns] Failed to save recent blocks for session ${sessionId}:`, error);
  }
}

/**
 * Generate a unique block ID for tracking block→success patterns.
 *
 * @param sessionId - Session ID, or undefined for fallback.
 * @returns A unique identifier combining timestamp and session ID.
 */
function generateBlockId(sessionId?: string): string {
  const now = new Date();
  const timestamp = now.toISOString().replace(/[-:T]/g, "").replace(/\..+/, "");
  const dateStr = `${timestamp.slice(0, 8)}-${timestamp.slice(8)}`;
  const ms = now.getMilliseconds().toString().padStart(6, "0");
  const sid = sessionId?.slice(0, 8) ?? "unknown";
  return `blk_${dateStr}-${ms}-${sid}`;
}

/**
 * Compute hash for block-success matching.
 *
 * @param hook - The hook name
 * @param command - The command string (first 50 chars used)
 * @returns A 16-character hex hash for matching.
 */
function computeCommandHash(hook: string, command?: string | null): string {
  const key = `${hook}:${command?.slice(0, 50) ?? ""}`;
  return createHash("sha256").update(key).digest("hex").slice(0, 16);
}

/**
 * Log block pattern to metrics file.
 *
 * @param entry - The log entry dict to write.
 * @param sessionId - Session ID, or undefined for fallback.
 * @param metricsLogDir - Optional metrics log directory.
 */
async function logBlockPattern(
  entry: Record<string, unknown>,
  sessionId?: string,
  metricsLogDir?: string,
): Promise<void> {
  const logDir = metricsLogDir ?? getMetricsLogDir();
  await logToSessionFile(logDir, "block-patterns", sessionId ?? "unknown", entry);
}

// =============================================================================
// Main Functions
// =============================================================================

/**
 * Check if this action is a recovery from a recent block.
 *
 * Tracks when a different action is taken after a block,
 * which indicates the user switched to an alternative approach.
 *
 * @param hook - The hook name that approved
 * @param command - The command that was approved
 * @param recentBlocks - The loaded recent blocks dictionary
 * @param sessionId - Session ID, or undefined for fallback.
 * @param metricsLogDir - Optional metrics log directory.
 */
async function checkRecoveryAction(
  hook: string,
  command: string | null | undefined,
  recentBlocks: RecentBlocks,
  sessionId?: string,
  metricsLogDir?: string,
): Promise<void> {
  const lastBlock = recentBlocks.__last_block__;
  if (!lastBlock) {
    return;
  }

  const elapsed = Date.now() / 1000 - lastBlock.timestamp;

  // Only track recovery within the window
  if (elapsed > BLOCK_RESOLUTION_WINDOW_SECONDS) {
    // Remove stale last_block entry
    recentBlocks.__last_block__ = undefined;
    await saveRecentBlocks(recentBlocks, sessionId);
    return;
  }

  // Check if this is a different action (recovery) vs same action (retry)
  const blockedCommand = lastBlock.command_preview ?? "";
  const blockedHook = lastBlock.hook ?? "";
  const currentCommand = command?.slice(0, 80) ?? "";

  // If same hook and commands are similar, it's a retry, not a recovery action
  if (blockedHook === hook) {
    // Same hook means it's likely a retry, not a switch to different approach
    return;
  }

  // If commands are similar, it's a retry, not a recovery action
  if (blockedCommand && currentCommand) {
    // Simple similarity check: same prefix = likely same command
    const prefixLen = COMMAND_SIMILARITY_PREFIX_LENGTH;
    if (blockedCommand.slice(0, prefixLen) === currentCommand.slice(0, prefixLen)) {
      return;
    }
  }

  // This is a recovery action - log it
  await logBlockPattern(
    {
      type: "block_recovery",
      block_id: lastBlock.block_id,
      session_id: sessionId ?? null,
      blocked_hook: lastBlock.hook,
      blocked_reason: lastBlock.reason ?? null,
      recovery: {
        elapsed_seconds: Math.round(elapsed * 10) / 10,
        recovery_hook: hook,
        recovery_action: currentCommand || null,
      },
      timestamp: getLocalTimestamp(),
    },
    sessionId,
    metricsLogDir,
  );

  // Clear last_block after recording recovery
  recentBlocks.__last_block__ = undefined;
  await saveRecentBlocks(recentBlocks, sessionId);
}

/**
 * Record a block event for pattern tracking.
 *
 * Records block events and stores them for later matching
 * with successful retries. Uses file-based persistence to work across
 * hook invocations (each hook runs as separate process).
 *
 * @param hook - The hook name that blocked
 * @param reason - The block reason message
 * @param details - Additional details from the hook
 * @param sessionId - Session ID, or undefined for fallback.
 * @param metricsLogDir - Optional metrics log directory.
 */
export async function recordBlock(
  hook: string,
  reason: string | null | undefined,
  details: Record<string, unknown> | null | undefined,
  sessionId?: string,
  metricsLogDir?: string,
): Promise<void> {
  const command = details?.command as string | undefined;
  const cmdHash = computeCommandHash(hook, command);
  const blockId = generateBlockId(sessionId);

  // Load existing blocks
  const recentBlocks = await loadRecentBlocks(sessionId);

  // Track retry count for repeated blocks
  let retryCount = 1;
  const existing = recentBlocks[cmdHash];
  if (existing) {
    retryCount = (existing.retry_count ?? 1) + 1;
  }

  // Update block record with retry count
  const currentTime = Date.now() / 1000;
  recentBlocks[cmdHash] = {
    block_id: blockId,
    hook,
    timestamp: currentTime,
    command_preview: command?.slice(0, 80) ?? null,
    retry_count: retryCount,
  };

  // Also track as "last block" for recovery action detection
  recentBlocks.__last_block__ = {
    block_id: blockId,
    hook,
    timestamp: currentTime,
    command_preview: command?.slice(0, 80) ?? null,
    reason: reason?.slice(0, 200) ?? null,
  };

  await saveRecentBlocks(recentBlocks, sessionId);

  await logBlockPattern(
    {
      type: "block",
      block_id: blockId,
      session_id: sessionId ?? null,
      hook,
      command_hash: cmdHash,
      command_preview: command?.slice(0, 80) ?? null,
      reason: reason?.slice(0, 200) ?? null,
      retry_count: retryCount,
      timestamp: getLocalTimestamp(),
    },
    sessionId,
    metricsLogDir,
  );
}

/**
 * Check if this success resolves a recent block.
 *
 * Matches successful operations with prior blocks
 * within a 60-second window. Uses file-based persistence to work across
 * hook invocations (each hook runs as separate process).
 *
 * @param hook - The hook name that approved
 * @param details - Additional details from the hook
 * @param sessionId - Session ID, or undefined for fallback.
 * @param metricsLogDir - Optional metrics log directory.
 */
export async function checkBlockResolution(
  hook: string,
  details: Record<string, unknown> | null | undefined,
  sessionId?: string,
  metricsLogDir?: string,
): Promise<void> {
  const command = details?.command as string | undefined;
  const cmdHash = computeCommandHash(hook, command);

  // Load blocks from file
  const recentBlocks = await loadRecentBlocks(sessionId);

  // Check for recovery action (different command after block)
  await checkRecoveryAction(hook, command, recentBlocks, sessionId, metricsLogDir);

  const blockInfo = recentBlocks[cmdHash];
  if (!blockInfo) {
    return;
  }

  const elapsed = Date.now() / 1000 - blockInfo.timestamp;

  // Get actual retry count from block_info
  const retryCount = blockInfo.retry_count ?? 1;

  if (elapsed <= BLOCK_RESOLUTION_WINDOW_SECONDS) {
    await logBlockPattern(
      {
        type: "block_resolved",
        block_id: blockInfo.block_id,
        session_id: sessionId ?? null,
        hook,
        resolution: {
          elapsed_seconds: Math.round(elapsed * 10) / 10,
          retry_count: retryCount,
        },
        timestamp: getLocalTimestamp(),
      },
      sessionId,
      metricsLogDir,
    );
  } else {
    await logBlockPattern(
      {
        type: "block_expired",
        block_id: blockInfo.block_id,
        session_id: sessionId ?? null,
        hook,
        elapsed_seconds: Math.round(elapsed * 10) / 10,
        timestamp: getLocalTimestamp(),
      },
      sessionId,
      metricsLogDir,
    );
  }

  // Remove resolved/expired block
  Reflect.deleteProperty(recentBlocks, cmdHash);

  // Also clear __last_block__ if it corresponds to this block
  // to prevent false recovery logs for subsequent approvals
  const lastBlock = recentBlocks.__last_block__;
  if (lastBlock?.block_id === blockInfo.block_id) {
    recentBlocks.__last_block__ = undefined;
  }

  await saveRecentBlocks(recentBlocks, sessionId);
}
