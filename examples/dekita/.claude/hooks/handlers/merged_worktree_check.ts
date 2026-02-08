#!/usr/bin/env bun
/**
 * セッション開始時にマージ済みPRのworktreeを検知して警告する。
 *
 * Why:
 *   PRがマージされた後もworktreeが残っているとディスクを圧迫し、
 *   混乱の原因になる。マージ済みworktreeを検知し削除を促す。
 *
 * What:
 *   - .worktrees/ディレクトリ内のworktreeを列挙
 *   - 各worktreeのブランチに関連するPRがマージ済みか確認
 *   - マージ済みworktreeがあればsystemMessageで警告
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、systemMessageで情報提供）
 *   - PreToolUse:Bashで発火（セッション毎に1回）
 *   - orphan-worktree-check.pyはgit未登録worktreeを検出（責務分離）
 *   - gh pr viewでマージ済み判定（リモートブランチ削除後も動作）
 *   - Python版: merged_worktree_check.py
 *
 * Changelog:
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { getRepoRoot } from "../lib/repo";
import { checkAndUpdateSessionMarker, createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "merged-worktree-check";

interface MergedWorktree {
  name: string;
  branch: string;
  prNumber: number;
  prTitle: string;
}

interface PrInfo {
  number: number;
  title: string;
  mergedAt: string;
}

/**
 * Get the branch name of a worktree.
 */
function getWorktreeBranch(worktreePath: string): string | null {
  try {
    const result = execSync(`git -C "${worktreePath}" branch --show-current`, {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const branch = result.trim();
    return branch || null;
  } catch {
    return null;
  }
}

/**
 * Check if there's a merged PR for the given branch.
 */
function checkPrMerged(branch: string): PrInfo | null {
  try {
    const result = execSync(`gh pr view "${branch}" --json number,title,mergedAt`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    if (result.trim()) {
      const data = JSON.parse(result);
      if (data.mergedAt) {
        return {
          number: data.number,
          title: data.title ?? "",
          mergedAt: data.mergedAt,
        };
      }
    }
  } catch {
    // gh CLI unavailable, timeout, or invalid response - skip
  }
  return null;
}

/**
 * Find worktrees whose PRs have been merged.
 */
function findMergedWorktrees(repoRoot: string): MergedWorktree[] {
  const worktreesDir = join(repoRoot, ".worktrees");
  if (!existsSync(worktreesDir)) {
    return [];
  }

  const merged: MergedWorktree[] = [];

  try {
    const entries = readdirSync(worktreesDir);
    for (const item of entries) {
      const itemPath = join(worktreesDir, item);
      try {
        const stat = statSync(itemPath);
        if (!stat.isDirectory()) {
          continue;
        }

        const branch = getWorktreeBranch(itemPath);
        if (!branch) {
          continue;
        }

        const prInfo = checkPrMerged(branch);
        if (prInfo) {
          merged.push({
            name: item,
            branch,
            prNumber: prInfo.number,
            prTitle: prInfo.title,
          });
        }
      } catch {
        // Skip items we can't process
      }
    }
  } catch {
    // Skip if we can't read the directory
  }

  return merged;
}

/**
 * Escape shell argument for safe use in commands.
 */
export function shellQuote(arg: string): string {
  if (/[^a-zA-Z0-9_\-.:/]/.test(arg)) {
    return `'${arg.replace(/'/g, "'\\''")}'`;
  }
  return arg;
}

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    const ctx = createHookContext(inputData);
    sessionId = ctx.sessionId;

    if (await checkAndUpdateSessionMarker(HOOK_NAME)) {
      const projectDirStr = process.env.CLAUDE_PROJECT_DIR ?? "";
      if (projectDirStr) {
        const repoRoot = getRepoRoot(projectDirStr);

        if (repoRoot) {
          const merged = findMergedWorktrees(repoRoot);

          if (merged.length > 0) {
            const lines = merged.map((m) => `  - .worktrees/${m.name} (PR #${m.prNumber}: MERGED)`);
            const mergedList = lines.join("\n");

            // Generate cleanup commands
            const cleanupCmds = merged.map((m) => {
              const name = shellQuote(`.worktrees/${m.name}`);
              return `git worktree unlock ${name} 2>/dev/null; git worktree remove ${name}`;
            });
            const cleanup = cleanupCmds.join("\n");

            result.systemMessage = `⚠️ **マージ済みPRのworktreeが残っています**:\n${mergedList}\n\n削除コマンド:\n\`\`\`\n${cleanup}\n\`\`\``;
          }
        }
      }
    }
  } catch (error) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  await logHookExecution(HOOK_NAME, result.decision ?? "approve", result.systemMessage, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
