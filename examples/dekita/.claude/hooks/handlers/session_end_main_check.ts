#!/usr/bin/env bun
/**
 * Stop hook to verify main branch is up-to-date at session end.
 *
 * - 責務: セッション終了時にmainブランチが最新か確認し、遅れていれば自動pull
 * - 重複なし: pr-merge-pull-reminderは自動pull、これは最終確認+自動pull
 * - 非ブロック型: 警告と自動pullのみ（ブロックしない）
 * - Issue #1103: 遅れている場合は自動でpullするよう変更
 */

import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { getDefaultBranch } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { getRepoRoot } from "../lib/repo";
import { approveAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";

const HOOK_NAME = "session_end_main_check";

interface MainBehindResult {
  isBehind: boolean;
  count: number;
  error: string | null;
  defaultBranch: string;
}

interface UpdateMainResult {
  success: boolean;
  message: string;
}

/**
 * Check if local main branch is behind origin/main.
 */
async function isMainBehind(repoRoot: string): Promise<MainBehindResult> {
  // Get the default branch name dynamically (only once, reused for all operations)
  const defaultBranch = (await getDefaultBranch(repoRoot)) || "main";

  try {
    const originDefaultBranch = `origin/${defaultBranch}`;

    // Fetch origin default branch (network operation, use longer timeout)
    const fetchResult = await asyncSpawn("git", ["fetch", "origin", defaultBranch], {
      cwd: repoRoot,
      timeout: TIMEOUT_MEDIUM * 1000,
    });

    if (fetchResult.exitCode !== 0) {
      return {
        isBehind: false,
        count: 0,
        error: `git fetch failed: ${fetchResult.stderr.trim()}`,
        defaultBranch,
      };
    }

    // Check if local default branch is behind origin
    // Use 'main..origin/main' instead of 'HEAD..origin/main'
    // to correctly check the main branch regardless of current branch
    const result = await asyncSpawn(
      "git",
      ["rev-list", `${defaultBranch}..${originDefaultBranch}`, "--count"],
      {
        cwd: repoRoot,
        timeout: TIMEOUT_LIGHT * 1000,
      },
    );

    if (result.exitCode === 0) {
      const count = Number.parseInt(result.stdout.trim(), 10);
      return { isBehind: count > 0, count, error: null, defaultBranch };
    }
    return {
      isBehind: false,
      count: 0,
      error: `git rev-list failed: ${result.stderr.trim()}`,
      defaultBranch,
    };
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    return { isBehind: false, count: 0, error, defaultBranch };
  }
}

/**
 * Get the current branch name.
 */
async function getCurrentBranch(repoRoot: string): Promise<string | null> {
  try {
    const result = await asyncSpawn("git", ["branch", "--show-current"], {
      cwd: repoRoot,
      timeout: TIMEOUT_LIGHT * 1000,
    });
    if (result.exitCode === 0) {
      return result.stdout.trim() || null;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Update local default branch ref to match origin.
 *
 * Uses 'git fetch origin <defaultBranch>:<defaultBranch>' to update the local
 * default branch ref without affecting the current checked-out branch.
 *
 * @param repoRoot - The repository root path.
 * @param defaultBranch - The default branch name (e.g., "main", "master").
 */
async function updateMainRef(repoRoot: string, defaultBranch: string): Promise<UpdateMainResult> {
  try {
    const result = await asyncSpawn(
      "git",
      ["fetch", "origin", `${defaultBranch}:${defaultBranch}`],
      {
        cwd: repoRoot,
        timeout: TIMEOUT_MEDIUM * 1000,
      },
    );

    if (result.exitCode === 0) {
      return { success: true, message: `${defaultBranch} updated to origin/${defaultBranch}` };
    }

    // Handle case where default branch is currently checked out
    if (result.stderr.includes("refusing to fetch into branch")) {
      // Fall back to git pull when on default branch
      const pullResult = await asyncSpawn("git", ["pull", "origin", defaultBranch], {
        cwd: repoRoot,
        timeout: TIMEOUT_MEDIUM * 1000,
      });

      if (pullResult.exitCode === 0) {
        return { success: true, message: pullResult.stdout.trim() };
      }
      return { success: false, message: pullResult.stderr.trim() };
    }

    return { success: false, message: result.stderr.trim() };
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    return { success: false, message: error };
  }
}

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};
  let sessionId: string | undefined;

  try {
    const input = await parseHookInput();
    sessionId = input.session_id;
    // Prevent infinite loops
    if (input.stop_hook_active) {
      await logHookExecution(HOOK_NAME, "approve", "stop_hook_active", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    const repoRoot = await getRepoRoot();
    if (!repoRoot) {
      await logHookExecution(HOOK_NAME, "approve", "repo root not found", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Check if main is behind (also returns the defaultBranch to avoid redundant git calls)
    const { isBehind, count, error, defaultBranch } = await isMainBehind(repoRoot);

    if (error) {
      // Log error but don't block - just warn
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `check failed: ${formatError(error)}`,
        undefined,
        {
          sessionId,
        },
      );
      console.log(JSON.stringify(result));
      return;
    }

    if (isBehind) {
      const currentBranch = await getCurrentBranch(repoRoot);

      // 自動でdefault branch refを更新
      const { success: pullSuccess, message: pullMessage } = await updateMainRef(
        repoRoot,
        defaultBranch,
      );

      let message: string;
      if (pullSuccess) {
        message = `[${HOOK_NAME}] ✅ ${defaultBranch}ブランチを自動更新しました (${count}コミット)`;
        await logHookExecution(
          HOOK_NAME,
          "approve",
          `auto-pulled ${count} commits`,
          {
            behind_count: count,
            current_branch: currentBranch,
            pull_success: true,
          },
          { sessionId },
        );
      } else {
        message = `[${HOOK_NAME}] ⚠️ ${defaultBranch}ブランチの自動更新に失敗しました\nエラー: ${pullMessage}\n手動で実行してください:\n  cd ${repoRoot}\n  git pull origin ${defaultBranch}`;
        await logHookExecution(
          HOOK_NAME,
          "approve",
          `auto-pull failed: ${pullMessage}`,
          {
            behind_count: count,
            current_branch: currentBranch,
            pull_success: false,
            error: pullMessage,
          },
          { sessionId },
        );
      }

      if (currentBranch !== defaultBranch) {
        message += `\n(現在のブランチ: ${currentBranch})`;
      }

      result.systemMessage = message;
    } else {
      await logHookExecution(HOOK_NAME, "approve", "default branch is up-to-date", undefined, {
        sessionId,
      });
    }
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", `Error: ${formatError(error)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    approveAndExit(HOOK_NAME);
  });
}
