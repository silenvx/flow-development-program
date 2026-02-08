#!/usr/bin/env bun
/**
 * セッション開始時に孤立したworktreeディレクトリを検知して警告する。
 *
 * Why:
 *   .worktrees/にディレクトリが残っているが.git/worktrees/に
 *   エントリがない状態は異常。ディスクを圧迫し混乱の原因となる。
 *
 * What:
 *   - .worktrees/ディレクトリを走査
 *   - 対応する.git/worktrees/エントリの存在を確認
 *   - 孤立worktreeがあればsystemMessageで警告
 *   - 削除コマンドを提示
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、systemMessageで情報提供）
 *   - PreToolUse:Bashで発火（セッション毎に1回）
 *   - merged-worktree-check.pyはマージ済みPR検出（責務分離）
 *   - ファイルロックで競合状態を防止
 *   - Python版: orphan_worktree_check.py
 *
 * Changelog:
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { existsSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { getRepoRoot } from "../lib/repo";
import { checkAndUpdateSessionMarker, createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "orphan-worktree-check";

interface OrphanWorktree {
  prefix: string;
  name: string;
}

/**
 * Find worktree directories that don't have corresponding git entries.
 */
function findOrphanWorktrees(projectDir: string): OrphanWorktree[] {
  // Resolve to repo root (handles both main repo and worktree cases)
  const repoRoot = getRepoRoot(projectDir);
  if (repoRoot === null) {
    return [];
  }

  const worktreePrefixes = [".worktrees"];
  const gitWorktreesDir = join(repoRoot, ".git", "worktrees");

  const orphans: OrphanWorktree[] = [];

  // If .git/worktrees/ doesn't exist, all worktree entries are orphans
  const gitWorktreesExists = existsSync(gitWorktreesDir);

  for (const prefix of worktreePrefixes) {
    const worktreesDir = join(repoRoot, prefix);
    if (!existsSync(worktreesDir)) {
      continue;
    }

    try {
      const entries = readdirSync(worktreesDir);
      for (const item of entries) {
        const itemPath = join(worktreesDir, item);
        try {
          const stat = statSync(itemPath);
          if (stat.isDirectory()) {
            // Check if corresponding entry exists in .git/worktrees/
            if (!gitWorktreesExists) {
              orphans.push({ prefix, name: item });
            } else {
              const gitEntry = join(gitWorktreesDir, item);
              if (!existsSync(gitEntry)) {
                orphans.push({ prefix, name: item });
              }
            }
          }
        } catch {
          // Skip items we can't stat
        }
      }
    } catch {
      // Skip directories we can't read
    }
  }

  return orphans;
}

/**
 * Escape shell argument for safe use in commands.
 */
export function shellQuote(arg: string): string {
  // If the string contains special characters, quote it
  if (/[^a-zA-Z0-9_\-.:/]/.test(arg)) {
    // Use single quotes and escape any single quotes within
    return `'${arg.replace(/'/g, "'\\''")}'`;
  }
  return arg;
}

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};
  let sessionId: string | undefined;

  try {
    // Set session_id for proper logging
    const inputData = await parseHookInput();
    const ctx = createHookContext(inputData);
    sessionId = ctx.sessionId;

    // Atomically check if new session and update marker
    // Returns true only for the first caller when concurrent calls occur
    if (await checkAndUpdateSessionMarker(HOOK_NAME)) {
      // Get project directory from environment
      const projectDirStr = process.env.CLAUDE_PROJECT_DIR ?? "";
      if (projectDirStr) {
        const orphans = findOrphanWorktrees(projectDirStr);

        if (orphans.length > 0) {
          const orphanList = orphans.map(({ prefix, name }) => `  - ${prefix}/${name}`).join("\n");
          const quotedPaths = orphans
            .map(({ prefix, name }) => shellQuote(`${prefix}/${name}`))
            .join(" ");
          result.systemMessage = `⚠️ **孤立したworktreeディレクトリを検出**:\n${orphanList}\n\nこれらは.git/worktrees/に対応するエントリがありません。\n削除コマンド: \`rm -rf ${quotedPaths}\``;
        }
      }
    }
  } catch (error) {
    // Don't block on errors, just skip the check
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  await logHookExecution(HOOK_NAME, result.decision ?? "approve", undefined, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
