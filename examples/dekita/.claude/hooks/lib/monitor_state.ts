/**
 * Monitor state management for ci-monitor.
 *
 * Why:
 *   Support background execution of ci-monitor by persisting state to disk.
 *   This allows status checking and result retrieval while monitoring runs.
 *
 * What:
 *   - saveMonitorState(): Save monitor state to file
 *   - loadMonitorState(): Load saved monitor state
 *   - clearMonitorState(): Clear state file after completion
 *   - getStateFilePath(): Get state file path for a PR
 *
 * Remarks:
 *   - State files are stored in main repo's .claude/state directory
 *   - Uses atomic write (temp file + rename) to prevent partial reads
 *   - Migrated from Python ci_monitor/state.py (Issue #3261)
 *
 * Changelog:
 *   - silenvx/dekita#3261: TypeScript migration from Python
 */

import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { formatError } from "./format_error";
import { asyncSpawn } from "./spawn";

// =============================================================================
// Configuration
// =============================================================================

/** Directory for state files relative to repo root */
const STATE_FILE_DIR = ".claude/state";

/** Prefix for state file names */
const STATE_FILE_PREFIX = "ci-monitor-";

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Get the main repository path (works from worktree too).
 *
 * @returns Path to the main repository root
 */
async function getMainRepoPath(): Promise<string> {
  // Try to get main repo via worktree list
  const worktreeResult = await asyncSpawn("git", ["worktree", "list", "--porcelain"], {
    timeout: 10000,
  });

  if (worktreeResult.success && worktreeResult.stdout) {
    const lines = worktreeResult.stdout.trim().split("\n");
    for (const line of lines) {
      if (line.startsWith("worktree ")) {
        return line.slice(9); // First entry is main repo
      }
    }
  }

  // Fall back to current directory's git root
  const revParseResult = await asyncSpawn("git", ["rev-parse", "--show-toplevel"], {
    timeout: 10000,
  });

  if (revParseResult.success && revParseResult.stdout) {
    return revParseResult.stdout.trim();
  }

  // Fall back to current working directory
  return process.cwd();
}

/**
 * Check if a file exists.
 *
 * @param path - File path to check
 * @returns True if file exists, false otherwise
 */
async function fileExists(path: string): Promise<boolean> {
  try {
    await readFile(path);
    return true;
  } catch {
    return false;
  }
}

// =============================================================================
// State Management Functions
// =============================================================================

/**
 * Get the state file path for a PR.
 *
 * State files are stored in the main repository's .claude/state directory,
 * not in worktrees, to allow status checking from any location.
 *
 * @param prNumber - The PR number
 * @returns Path to the state file
 * @throws Error if prNumber contains invalid characters
 */
export async function getStateFilePath(prNumber: string): Promise<string> {
  // Validate PR number (alphanumeric only)
  if (!/^[a-zA-Z0-9]+$/.test(prNumber)) {
    throw new Error(`Invalid pr_number specified: ${prNumber}`);
  }

  const baseDir = await getMainRepoPath();
  return join(baseDir, STATE_FILE_DIR, `${STATE_FILE_PREFIX}${prNumber}.json`);
}

/**
 * Monitor state data structure.
 */
export interface MonitorState {
  /** Timestamp of last update (ISO format) */
  updated_at: string;
  /** PR number being monitored */
  pr_number: string;
  /** Current monitoring status */
  status?: string;
  /** Current phase of monitoring */
  phase?: string;
  /** Number of rebase attempts */
  rebase_count?: number;
  /** CI check status */
  ci_status?: string;
  /** Review status */
  review_status?: string;
  /** Any additional state data */
  [key: string]: unknown;
}

/**
 * Save monitor state to file for background execution support.
 *
 * Uses atomic write (temp file + rename) to prevent partial reads during status checks.
 *
 * @param prNumber - The PR number being monitored
 * @param state - Dictionary containing current monitor state (not mutated)
 * @returns True if save succeeded, false otherwise
 */
export async function saveMonitorState(
  prNumber: string,
  state: Record<string, unknown>,
): Promise<boolean> {
  let tempFile: string | null = null;

  try {
    const stateFile = await getStateFilePath(prNumber);
    const stateDir = dirname(stateFile);

    // Ensure directory exists
    await mkdir(stateDir, { recursive: true });

    // Prepare state to save
    const stateToSave: MonitorState = {
      ...state,
      updated_at: new Date().toISOString(),
      pr_number: prNumber,
    };

    // Atomic write: write to temp file, then rename
    tempFile = `${stateFile}.tmp`;
    await writeFile(tempFile, JSON.stringify(stateToSave, null, 2), "utf-8");
    await rename(tempFile, stateFile);

    return true;
  } catch (error) {
    if (error instanceof Error && error.message.includes("Invalid pr_number")) {
      console.error(`Warning: Invalid PR number: ${error.message}`);
    } else {
      console.error(`Warning: Failed to save state: ${formatError(error)}`);
    }

    // Cleanup temp file on error
    if (tempFile) {
      try {
        await unlink(tempFile);
      } catch {
        // Best-effort cleanup - ignore failures
      }
    }

    return false;
  }
}

/**
 * Load saved monitor state from file.
 *
 * @param prNumber - The PR number to load state for
 * @returns State dictionary if found, null otherwise
 */
export async function loadMonitorState(prNumber: string): Promise<MonitorState | null> {
  try {
    const stateFile = await getStateFilePath(prNumber);

    if (!(await fileExists(stateFile))) {
      return null;
    }

    const content = await readFile(stateFile, "utf-8");
    return JSON.parse(content) as MonitorState;
  } catch (error) {
    if (error instanceof Error && error.message.includes("Invalid pr_number")) {
      console.error(`Warning: Invalid PR number: ${error.message}`);
    } else {
      console.error(`Warning: Failed to load state: ${formatError(error)}`);
    }
    return null;
  }
}

/**
 * Clear saved monitor state file.
 *
 * Called when monitoring completes to clean up.
 *
 * @param prNumber - The PR number to clear state for
 * @returns True if cleared or didn't exist, false on error
 */
export async function clearMonitorState(prNumber: string): Promise<boolean> {
  try {
    const stateFile = await getStateFilePath(prNumber);

    if (await fileExists(stateFile)) {
      await unlink(stateFile);
    }

    return true;
  } catch (error) {
    if (error instanceof Error && error.message.includes("Invalid pr_number")) {
      console.error(`Warning: Invalid PR number: ${error.message}`);
    } else {
      console.error(`Warning: Failed to clear state: ${formatError(error)}`);
    }
    return false;
  }
}
