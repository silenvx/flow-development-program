/**
 * Worktree management for ci-monitor.
 *
 * Why:
 *   Handle git worktree detection and cleanup operations.
 *   Supports cleanup after successful PR merge.
 *
 * What:
 *   - getWorktreeInfo(): Get worktree information if in a worktree
 *   - cleanupWorktreeAfterMerge(): Cleanup worktree after successful merge
 *
 * Remarks:
 *   - Migrated from Python ci_monitor/worktree.py (Issue #3261)
 *   - Uses asyncSpawn for git command execution
 *
 * Changelog:
 *   - silenvx/dekita#3261: TypeScript migration from Python
 */

import { basename, resolve, sep } from "node:path";
import { asyncSpawn } from "../../hooks/lib/spawn";

// =============================================================================
// Types
// =============================================================================

/** Optional log function signature */
export type LogFn = (
  message: string,
  jsonMode: boolean,
  data: Record<string, unknown> | null,
) => void;

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Check if branch name contains exact worktree name as a segment.
 *
 * Examples:
 *   - "fix/issue-1366-cleanup" matches "issue-1366" -> true
 *   - "fix/issue-13669" matches "issue-1366" -> false
 *   - "feature-1234" matches "123" -> false
 *
 * @param branch - Branch name to check
 * @param wtName - Worktree name to match
 * @returns True if the branch contains the exact worktree name as a segment
 */
function isExactWorktreeMatch(branch: string, wtName: string): boolean {
  // Check for exact segment match (surrounded by non-alphanumeric or at boundaries)
  // Escape special regex characters in wtName
  const escaped = wtName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // Match only if wtName is a complete segment (not part of a larger number/word)
  const pattern = new RegExp(`(^|[^a-zA-Z0-9])${escaped}([^a-zA-Z0-9]|$)`);
  return pattern.test(branch);
}

// =============================================================================
// Worktree Functions
// =============================================================================

/**
 * Get worktree information if current directory is inside a worktree.
 *
 * @returns Tuple of [mainRepoPath, worktreePath] if in worktree, [null, null] otherwise
 */
export async function getWorktreeInfo(): Promise<[string | null, string | null]> {
  try {
    // Get current working directory
    const cwd = process.cwd();

    // Get git worktree list
    const result = await asyncSpawn("git", ["worktree", "list", "--porcelain"], {
      timeout: 10000,
    });

    if (!result.success || !result.stdout) {
      return [null, null];
    }

    // Parse worktree list
    // Format: worktree /path\nHEAD abc123\nbranch refs/heads/xxx\n\n
    const worktrees: string[] = [];
    let mainRepo: string | null = null;

    for (const line of result.stdout.trim().split("\n")) {
      if (line.startsWith("worktree ")) {
        const path = line.slice(9); // Remove "worktree " prefix
        if (mainRepo === null) {
          mainRepo = path; // First entry is main repo
        } else {
          worktrees.push(path);
        }
      }
    }

    if (mainRepo === null) {
      return [null, null];
    }

    // Check if cwd is inside a worktree (not main repo)
    // Use resolve to normalize paths (Bun.realPathSync is not available in all contexts)
    const cwdResolved = resolve(cwd);
    for (const wtPath of worktrees) {
      const wtResolved = resolve(wtPath);
      if (cwdResolved === wtResolved || cwdResolved.startsWith(wtResolved + sep)) {
        return [mainRepo, wtPath];
      }
    }

    return [null, null];
  } catch {
    return [null, null];
  }
}

/**
 * Cleanup worktree after successful merge.
 *
 * Issue #1366: Prevent worktree from remaining after cleanup phase is skipped.
 *
 * Note: Issue #1478 - This function is no longer called from --merge command
 * because subprocess deletion makes parent process's cwd invalid. However, it
 * is kept for potential use by hooks (e.g., worktree-auto-cleanup.py) that run
 * in the correct process context.
 *
 * @param wtPath - Path to the worktree to remove
 * @param mainRepo - Path to the main repository
 * @param jsonMode - If true, log() outputs to stderr instead of stdout
 * @param logFn - Optional logging function
 * @returns True if cleanup succeeded, false otherwise
 */
export async function cleanupWorktreeAfterMerge(
  wtPath: string,
  mainRepo: string,
  jsonMode = false,
  logFn?: LogFn,
): Promise<boolean> {
  const log = (message: string): void => {
    if (logFn) {
      logFn(message, jsonMode, null);
    } else if (jsonMode) {
      // In JSON mode, use stderr for non-JSON messages to avoid polluting stdout
      console.error(message);
    } else {
      console.log(message);
    }
  };

  const cleanupBranch = async (wtName: string): Promise<void> => {
    try {
      // Get branches with exact issue number match (e.g., issue-1366)
      const branchResult = await asyncSpawn("git", ["branch", "--list"], {
        timeout: 10000,
      });

      if (branchResult.success && branchResult.stdout?.trim()) {
        for (const line of branchResult.stdout.trim().split("\n")) {
          const branch = line.trim().replace(/^\* /, "");
          // Exact match: branch must contain the full wtName as a segment
          if (branch && isExactWorktreeMatch(branch, wtName)) {
            // -d is safe: only deletes fully merged branches
            const delResult = await asyncSpawn("git", ["branch", "-d", branch], {
              timeout: 10000,
            });
            if (delResult.success) {
              log(`🗑️ ブランチ ${branch} を削除しました`);
            }
          }
        }
      }
    } catch (error) {
      // Branch cleanup is best-effort - log error but don't fail
      log(`⚠️ ブランチ削除中にエラー: ${error}`);
    }
  };

  try {
    // If cwd is inside worktree, move to main repo first
    const cwd = process.cwd();
    const cwdResolved = resolve(cwd);
    const wtResolved = resolve(wtPath);

    if (cwdResolved === wtResolved || cwdResolved.startsWith(wtResolved + sep)) {
      process.chdir(mainRepo);
      log(`🔄 Moved to main repo: ${mainRepo}`);
    }

    // Unlock worktree first (ignore errors - may not be locked)
    await asyncSpawn("git", ["worktree", "unlock", wtPath], { timeout: 10000 });

    // Try to remove worktree
    const result = await asyncSpawn("git", ["worktree", "remove", wtPath], {
      timeout: 30000,
    });

    const wtName = basename(wtPath);

    if (result.success) {
      log(`✅ Worktree ${wtPath} を削除しました`);
      await cleanupBranch(wtName);
      return true;
    }

    // Check if there are uncommitted changes before force removal
    // Force removal is only safe after successful merge (all changes committed)
    const errorMsg = result.stderr?.trim() || "";
    log(`⚠️ 通常削除失敗、強制削除を試行: ${errorMsg}`);

    const forceResult = await asyncSpawn("git", ["worktree", "remove", "-f", wtPath], {
      timeout: 30000,
    });

    if (forceResult.success) {
      log(`✅ Worktree ${wtPath} を強制削除しました`);
      await cleanupBranch(wtName);
      return true;
    }

    log(`❌ Worktree削除失敗: ${forceResult.stderr?.trim()}`);
    return false;
  } catch (error) {
    if (error instanceof Error && error.message.includes("timeout")) {
      log("❌ Worktree削除がタイムアウトしました");
    } else {
      log(`❌ Worktree削除エラー: ${error}`);
    }
    return false;
  }
}
