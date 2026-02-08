#!/usr/bin/env bun
/**
 * セッション終了時にマージ/クローズ済みPRのworktreeクリーンアップを提案。
 *
 * Why:
 *   PRがマージ/クローズされた後もworktreeが残ると管理が煩雑になる。
 *   セッション終了時に提案することで、不要なworktreeの蓄積を防ぐ。
 *
 * What:
 *   - セッション終了時（Stop）に発火
 *   - cwdがworktree内かを確認
 *   - 関連PRの状態（MERGED/CLOSED）をチェック
 *   - マージ/クローズ済みならクリーンアップ手順を提案
 *
 * Remarks:
 *   - 提案型フック（ブロックせず、systemMessageで案内）
 *   - ロック中のworktreeはスキップ（別セッション使用中の可能性）
 *   - worktree-auto-cleanupはマージ直後、本フックはセッション終了時
 *
 * Changelog:
 *   - silenvx/dekita#739: フック追加
 *   - silenvx/dekita#3161: TypeScript移行
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { TIMEOUT_LIGHT } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";

const HOOK_NAME = "worktree-cleanup-suggester";

interface WorktreeInfo {
  path: string;
  branch: string;
  isMain: boolean;
  mainRepo: string;
}

/**
 * Get information about the current worktree if we're inside one.
 *
 * @param cwdOverride - Optional cwd override from session input (Issue #3263)
 * @returns WorktreeInfo if inside a worktree, null otherwise.
 */
async function getCurrentWorktreeInfo(cwdOverride?: string | null): Promise<WorktreeInfo | null> {
  try {
    // Get the current directory (prefer session cwd if provided)
    const cwd = cwdOverride ?? process.cwd();

    // Get the list of worktrees
    const result = await asyncSpawn("git", ["worktree", "list", "--porcelain"], {
      timeout: TIMEOUT_LIGHT * 1000,
    });

    if (!result.success) {
      return null;
    }

    const worktrees: Array<{ path: string; branch?: string; head?: string }> = [];
    let currentWorktree: { path: string; branch?: string; head?: string } | null = null;

    for (const line of result.stdout.trim().split("\n")) {
      if (line.startsWith("worktree ")) {
        if (currentWorktree) {
          worktrees.push(currentWorktree);
        }
        currentWorktree = { path: line.slice(9) };
      } else if (line.startsWith("branch refs/heads/")) {
        if (currentWorktree) {
          currentWorktree.branch = line.slice(18);
        }
      } else if (line.startsWith("HEAD ")) {
        if (currentWorktree) {
          currentWorktree.head = line.slice(5);
        }
      }
    }
    if (currentWorktree) {
      worktrees.push(currentWorktree);
    }

    if (worktrees.length === 0) {
      return null;
    }

    // The first worktree is always the main one
    const mainWorktreePath = resolve(worktrees[0].path);

    // Find the MOST SPECIFIC worktree that contains cwd
    // (worktrees can be nested inside the main repo)
    const cwdResolved = resolve(cwd);
    let bestMatch: (typeof worktrees)[0] | null = null;
    let bestMatchLen = -1;
    let bestMatchIndex = -1;

    for (let i = 0; i < worktrees.length; i++) {
      const wt = worktrees[i];
      const wtPath = resolve(wt.path);

      // Check if cwd starts with wtPath (cwd is inside this worktree)
      if (cwdResolved.startsWith(`${wtPath}/`) || cwdResolved === wtPath) {
        const pathLen = wtPath.length;
        if (pathLen > bestMatchLen) {
          bestMatch = wt;
          bestMatchLen = pathLen;
          bestMatchIndex = i;
        }
      }
    }

    if (bestMatch) {
      return {
        path: resolve(bestMatch.path),
        branch: bestMatch.branch ?? "unknown",
        isMain: bestMatchIndex === 0,
        mainRepo: mainWorktreePath,
      };
    }

    return null;
  } catch {
    return null;
  }
}

/**
 * Get the PR state for a given branch.
 *
 * @param branch - The branch name.
 * @returns "MERGED", "CLOSED", "OPEN", or null if no PR found.
 */
async function getPrStateForBranch(branch: string): Promise<string | null> {
  try {
    const result = await asyncSpawn(
      "gh",
      [
        "pr",
        "list",
        "--state",
        "all",
        "--head",
        branch,
        "--json",
        "state",
        "--jq",
        ".[0].state // empty",
      ],
      { timeout: TIMEOUT_LIGHT * 1000 },
    );

    if (result.success && result.stdout.trim()) {
      return result.stdout.trim();
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Check if a worktree is locked.
 *
 * @param worktreePath - The path to the worktree.
 * @returns True if locked, False otherwise.
 */
function checkWorktreeLocked(worktreePath: string): boolean {
  try {
    const gitFile = resolve(worktreePath, ".git");
    if (!existsSync(gitFile)) {
      return false;
    }

    // Read the .git file to get the gitdir path
    const gitDirContent = readFileSync(gitFile, "utf-8").trim();
    if (!gitDirContent.startsWith("gitdir: ")) {
      return false;
    }

    let gitdirPath = gitDirContent.slice(8);

    // If gitdir is relative, resolve it from the .git file's parent
    if (!isAbsolute(gitdirPath)) {
      gitdirPath = resolve(dirname(gitFile), gitdirPath);
    }
    gitdirPath = resolve(gitdirPath);

    const lockPath = resolve(gitdirPath, "locked");
    return existsSync(lockPath);
  } catch {
    return false;
  }
}

/**
 * Escape a string for shell safety.
 */
function shellQuote(s: string): string {
  // If the string contains no special characters, return as-is
  if (/^[a-zA-Z0-9._\-/]+$/.test(s)) {
    return s;
  }
  // Otherwise, wrap in single quotes and escape any existing single quotes
  return `'${s.replace(/'/g, "'\\''")}'`;
}

/**
 * Generate a cleanup suggestion message.
 *
 * @param worktreeInfo - The worktree information.
 * @param prState - The PR state (MERGED or CLOSED).
 * @returns The cleanup suggestion message.
 */
function generateCleanupSuggestion(worktreeInfo: WorktreeInfo, prState: string): string {
  const worktreePath = worktreeInfo.path;
  const mainRepo = worktreeInfo.mainRepo;
  const worktreeName = worktreePath.split("/").pop() ?? "";

  // Quote paths for shell safety (handles spaces and special characters)
  const quotedMainRepo = shellQuote(mainRepo);
  const quotedWorktreePath = shellQuote(worktreePath);

  // Build the cleanup command (one command per line for clarity)
  // Issue #3161: Don't include unlock - this function is only called when unlocked
  const cleanupCmd = `cd ${quotedMainRepo}\ngit worktree remove ${quotedWorktreePath}`;

  return `
## 🧹 Worktreeクリーンアップの提案

現在 worktree **${worktreeName}** 内で作業中ですが、関連PRは **${prState}** です。

### クリーンアップ手順

以下のコマンドでworktreeを削除できます:

\`\`\`bash
${cleanupCmd}
\`\`\`

または、全てのマージ済みworktreeを一括削除:

\`\`\`bash
cd ${quotedMainRepo} && ./scripts/cleanup-worktrees.sh --force
\`\`\`

💡 **ヒント**: 上記コマンドを実行してworktreeをクリーンアップしてください。
`;
}

async function main(): Promise<void> {
  let result: Record<string, unknown> | null = null;
  let sessionId: string | undefined;

  try {
    const inputJson = await parseHookInput();
    sessionId = inputJson.session_id;

    // If stop_hook_active is set, approve immediately to avoid infinite retry loops
    if (inputJson.stop_hook_active) {
      result = {
        ok: true,

        reason: "stop_hook_active is set; approving to avoid infinite retry loop.",
      };
      await logHookExecution(
        HOOK_NAME,
        result.decision as string,
        result.reason as string,
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Check if we're inside a worktree (prefer session cwd if provided)
    const worktreeInfo = await getCurrentWorktreeInfo(inputJson.cwd ?? null);

    if (!worktreeInfo) {
      // Not in a worktree, nothing to suggest
      result = {
        ok: true,

        reason: "Not inside a worktree.",
      };
    } else if (worktreeInfo.isMain) {
      // Inside main repo, nothing to suggest
      result = {
        ok: true,

        reason: "Inside main repository, no cleanup needed.",
      };
    } else {
      // Inside a worktree, check PR state
      const branch = worktreeInfo.branch;
      const prState = await getPrStateForBranch(branch);

      if (prState === "MERGED" || prState === "CLOSED") {
        // Check if locked
        const isLocked = checkWorktreeLocked(worktreeInfo.path);

        if (isLocked) {
          result = {
            ok: true,

            reason: "Worktree is locked (another session may be using it).",
            systemMessage: `ℹ️ worktree-cleanup: ${worktreeInfo.path} はロック中のため、クリーンアップをスキップします。`,
          };
        } else {
          const suggestion = generateCleanupSuggestion(worktreeInfo, prState);
          result = {
            ok: true,

            reason: `PR is ${prState}, cleanup suggested.`,
            systemMessage: suggestion,
          };
        }
      } else if (prState === "OPEN") {
        result = {
          ok: true,

          reason: "PR is still open, no cleanup needed.",
        };
      } else {
        // No PR found or error
        result = {
          ok: true,

          reason: `No PR found for branch ${branch}.`,
        };
      }
    }
  } catch (e) {
    // On error, approve to avoid blocking, but log for debugging
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(e)}`);
    result = { ok: true, reason: `Hook error: ${formatError(e)}` };
  }

  await logHookExecution(
    HOOK_NAME,
    (result?.decision as string) ?? "approve",
    result?.reason as string | undefined,
    undefined,
    { sessionId },
  );
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({ ok: true }));
  });
}
