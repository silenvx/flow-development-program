#!/usr/bin/env bun
/**
 * セッション終了時のworktree自動クリーンアップ。
 *
 * Why:
 *   セッション終了時に、マージ済み/クローズ済みのworktreeを自動的に
 *   クリーンアップし、現在のworktreeのロックを解除する。
 *
 * What:
 *   - 現在のworktreeのロック解除（Issue #1315）
 *   - マージ済み/クローズ済みのworktree削除
 *   - ロック中や現在作業中のworktreeはスキップ
 *
 * Remarks:
 *   - Stop hookで発火
 *   - 現在のworktreeは削除しない（ロック解除のみ）
 *   - Fail-open: エラー時は処理をスキップ
 *   - Python版: session_end_worktree_cleanup.py
 *
 * Changelog:
 *   - silenvx/dekita#778: マージ済みworktree自動クリーンアップ
 *   - silenvx/dekita#1315: セッション終了時のロック自動解除
 *   - silenvx/dekita#2986: TypeScript版に移植
 */

import { execSync } from "node:child_process";
import { resolve } from "node:path";
import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "session-end-worktree-cleanup";

export interface WorktreeInfo {
  path: string;
  branch: string;
  isMain: boolean;
  locked: boolean;
}

/**
 * Parse git worktree list --porcelain output.
 */
export function parseWorktreePorcelainOutput(output: string): WorktreeInfo[] {
  const worktrees: WorktreeInfo[] = [];
  let current: Partial<WorktreeInfo> = {};

  for (const line of output.trim().split("\n")) {
    if (line.startsWith("worktree ")) {
      if (current.path) {
        worktrees.push({
          path: current.path,
          branch: current.branch ?? "unknown",
          isMain: false,
          locked: current.locked ?? false,
        });
      }
      current = { path: line.slice(9) };
    } else if (line.startsWith("branch refs/heads/")) {
      current.branch = line.slice(18);
    } else if (line.startsWith("locked")) {
      // Handle both "locked" and "locked <reason>" formats
      current.locked = true;
    }
  }

  if (current.path) {
    worktrees.push({
      path: current.path,
      branch: current.branch ?? "unknown",
      isMain: false,
      locked: current.locked ?? false,
    });
  }

  // Mark first as main
  for (let i = 0; i < worktrees.length; i++) {
    worktrees[i].isMain = i === 0;
  }

  return worktrees;
}

/**
 * Check if cwd is inside a worktree.
 */
export function isInsideWorktree(cwd: string, worktreePath: string): boolean {
  const cwdResolved = resolve(cwd);
  const wtPath = resolve(worktreePath);
  return cwdResolved.startsWith(`${wtPath}/`) || cwdResolved === wtPath;
}

/**
 * Find the worktree containing the given cwd.
 */
export function findWorktreeForCwd(
  cwd: string | null,
  worktrees: WorktreeInfo[],
): WorktreeInfo | null {
  if (!cwd) {
    return null;
  }

  for (const wt of worktrees) {
    if (wt.isMain) {
      continue;
    }
    if (isInsideWorktree(cwd, wt.path)) {
      return wt;
    }
  }

  return null;
}

/**
 * Get information about all worktrees.
 */
function getWorktreesInfo(): WorktreeInfo[] {
  try {
    const result = execSync("git worktree list --porcelain", {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    return parseWorktreePorcelainOutput(result);
  } catch {
    return [];
  }
}

/**
 * Unlock the worktree that contains the current working directory.
 *
 * Issue #1315: When a session ends (e.g., context overflow), unlock the
 * worktree so the next session can work with it (e.g., run ci-monitor).
 */
function unlockCurrentWorktree(cwd: string | null, worktrees: WorktreeInfo[]): string | null {
  if (!cwd) {
    return null;
  }

  const cwdResolved = resolve(cwd);

  for (const wt of worktrees) {
    if (wt.isMain) {
      continue;
    }

    const wtPath = resolve(wt.path);

    // Check if cwd is inside this worktree
    if (!cwdResolved.startsWith(`${wtPath}/`) && cwdResolved !== wtPath) {
      continue;
    }

    // Found the worktree containing cwd
    if (!wt.locked) {
      return null;
    }

    // Unlock this worktree
    try {
      execSync(`git worktree unlock "${wtPath}"`, {
        timeout: TIMEOUT_LIGHT * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      });
      const name = wtPath.split("/").pop() ?? wtPath;
      return `🔓 ${name} のロックを解除しました`;
    } catch {
      const name = wtPath.split("/").pop() ?? wtPath;
      return `⚠️ ${name} のロック解除に失敗しました`;
    }
  }

  return null;
}

/**
 * Get PR state for a branch (MERGED, CLOSED, OPEN, or null).
 */
function getPrState(branch: string): string | null {
  try {
    const result = execSync(
      `gh pr list --state all --head "${branch}" --json state --jq '.[0].state // empty'`,
      {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    return result.trim() || null;
  } catch {
    return null;
  }
}

/**
 * Remove a worktree (unlock first if needed).
 */
function cleanupWorktree(worktreePath: string): { success: boolean; message: string } {
  const name = worktreePath.split("/").pop() ?? worktreePath;

  try {
    // Unlock first (ignore errors)
    try {
      execSync(`git worktree unlock "${worktreePath}"`, {
        timeout: TIMEOUT_LIGHT * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch {
      // Ignore unlock errors
    }

    // Try normal remove
    try {
      execSync(`git worktree remove "${worktreePath}"`, {
        timeout: TIMEOUT_MEDIUM * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      });
      return { success: true, message: `✅ ${name} 削除完了` };
    } catch {
      // Try force remove
      try {
        execSync(`git worktree remove -f "${worktreePath}"`, {
          timeout: TIMEOUT_MEDIUM * 1000,
          stdio: ["pipe", "pipe", "pipe"],
        });
        return { success: true, message: `✅ ${name} 強制削除完了` };
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        return { success: false, message: `⚠️ ${name} 削除失敗: ${msg}` };
      }
    }
  } catch {
    return { success: false, message: `⚠️ ${name} 削除タイムアウト` };
  }
}

async function main(): Promise<void> {
  const result: {
    ok: boolean;
    decision?: string;
    systemMessage?: string;
    reason?: string;
  } = { ok: true };

  const cleaned: string[] = [];
  const skipped: string[] = [];
  let unlockedMsg: string | null = null;
  let sessionId: string | undefined;

  try {
    const inputJson = await parseHookInput();
    sessionId = inputJson.session_id;

    // Exit early on invalid/empty input
    if (!inputJson || Object.keys(inputJson).length === 0) {
      await logHookExecution(HOOK_NAME, "approve", "empty_input", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Prevent infinite loops
    if (inputJson.stop_hook_active) {
      await logHookExecution(HOOK_NAME, "approve", "stop_hook_active", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Get current directory
    let cwd: string | null = null;
    try {
      cwd = process.cwd();
    } catch {
      cwd = null;
    }

    const worktrees = getWorktreesInfo();

    // Issue #1315: Unlock the current worktree before cleanup
    unlockedMsg = unlockCurrentWorktree(cwd, worktrees);

    const cwdResolved = cwd ? resolve(cwd) : null;

    for (const wt of worktrees) {
      if (wt.isMain) {
        continue;
      }

      const wtPath = resolve(wt.path);
      const name = wtPath.split("/").pop() ?? wtPath;

      // Skip if cwd is inside this worktree
      if (cwdResolved) {
        if (cwdResolved.startsWith(`${wtPath}/`) || cwdResolved === wtPath) {
          skipped.push(`${name} (cwd内)`);
          continue;
        }
      }

      // Skip locked worktrees
      if (wt.locked) {
        skipped.push(`${name} (ロック中)`);
        continue;
      }

      // Check PR state
      const prState = getPrState(wt.branch);
      if (prState !== "MERGED" && prState !== "CLOSED") {
        continue;
      }

      // Clean up
      const { success } = cleanupWorktree(wtPath);
      if (success) {
        cleaned.push(name);
      } else {
        skipped.push(`${name} (削除失敗)`);
      }
    }

    // Build message
    if (unlockedMsg || cleaned.length > 0 || skipped.length > 0) {
      const parts: string[] = [];
      if (unlockedMsg) {
        parts.push(unlockedMsg);
      }
      if (cleaned.length > 0) {
        parts.push(`🧹 クリーンアップ完了: ${cleaned.join(", ")}`);
      }
      if (skipped.length > 0) {
        parts.push(`スキップ: ${skipped.join(", ")}`);
      }
      result.systemMessage = parts.join("\n");
      result.reason = `Unlocked: ${unlockedMsg ? 1 : 0}, cleaned: ${cleaned.length}, skipped: ${skipped.length}`;
    } else {
      result.reason = "No worktrees to clean up or unlock";
    }
  } catch (error) {
    result.reason = `Error: ${formatError(error)}`;
  }

  await logHookExecution(HOOK_NAME, result.decision ?? "approve", result.reason, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
