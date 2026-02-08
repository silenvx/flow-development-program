#!/usr/bin/env bun
/**
 * PRマージ成功後にworktreeを自動削除。
 *
 * Why:
 *   PRマージ後にworktreeが残るとディスク容量を消費し、管理が煩雑になる。
 *   マージ成功時に自動削除することで、worktree蓄積を防ぐ。
 *
 * What:
 *   - gh pr merge成功後（PostToolUse:Bash）に発火
 *   - コマンドまたは出力からPR番号を抽出
 *   - 対応するブランチのworktreeを検索
 *   - ロック解除後にworktreeを削除
 *
 * Remarks:
 *   - 自動化型フック（マージ成功後に即座に実行）
 *   - cwdがworktree内の場合はスキップ（削除不可）
 *   - merged-worktree-checkはセッション開始時、本フックはマージ直後
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#778: コマンド出力からPR番号抽出追加
 *   - silenvx/dekita#803: cwd確認追加
 *   - silenvx/dekita#1470: exit_codeデフォルト値修正
 *   - silenvx/dekita#2607: HookContextパターン移行
 *   - silenvx/dekita#3161: TypeScript移行
 */

import { existsSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { checkCwdInsidePath } from "../lib/cwd";
import { formatError } from "../lib/format_error";
import { getToolResult } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { getRepoRoot } from "../lib/repo";
import { createHookContext, parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";

const HOOK_NAME = "worktree-auto-cleanup";

/**
 * Get the head branch name for a PR.
 *
 * @param prNumber - The PR number.
 * @param repoRoot - The repository root path.
 * @returns The head branch name, or null if not found.
 */
async function getPrBranch(prNumber: number, repoRoot: string): Promise<string | null> {
  try {
    const result = await asyncSpawn(
      "gh",
      ["pr", "view", String(prNumber), "--json", "headRefName", "-q", ".headRefName"],
      { timeout: TIMEOUT_MEDIUM * 1000, cwd: repoRoot },
    );

    if (result.success && result.stdout.trim()) {
      return result.stdout.trim();
    }
  } catch {
    // gh unavailable or timed out - treat as no branch info
  }
  return null;
}

/**
 * Find worktree path by branch name.
 *
 * @param repoRoot - The repository root path.
 * @param branch - The branch name to search for.
 * @returns The worktree path if found, or null.
 */
async function findWorktreeByBranch(repoRoot: string, branch: string): Promise<string | null> {
  const worktreesDir = join(repoRoot, ".worktrees");
  if (!existsSync(worktreesDir)) {
    return null;
  }

  try {
    const items = readdirSync(worktreesDir);
    for (const item of items) {
      const itemPath = join(worktreesDir, item);
      const stat = statSync(itemPath);
      if (!stat.isDirectory()) {
        continue;
      }

      try {
        const result = await asyncSpawn("git", ["-C", itemPath, "branch", "--show-current"], {
          timeout: TIMEOUT_LIGHT * 1000,
        });

        if (result.success && result.stdout.trim() === branch) {
          return itemPath;
        }
      } catch {
        // Gitコマンド失敗、スキップ
      }
    }
  } catch {
    // Directory read error
  }

  return null;
}

/**
 * Remove a worktree (unlock first if needed).
 *
 * @param repoRoot - The repository root path.
 * @param worktreePath - The full path to the worktree.
 * @returns Tuple of [success, message].
 */
async function removeWorktree(repoRoot: string, worktreePath: string): Promise<[boolean, string]> {
  const worktreeName = worktreePath.split("/").pop() ?? "";
  const relPath = `.worktrees/${worktreeName}`;

  try {
    // Try to unlock first (ignore errors if not locked)
    await asyncSpawn("git", ["-C", repoRoot, "worktree", "unlock", relPath], {
      timeout: TIMEOUT_LIGHT * 1000,
    });

    // Remove the worktree
    let result = await asyncSpawn("git", ["-C", repoRoot, "worktree", "remove", relPath], {
      timeout: TIMEOUT_MEDIUM * 1000,
    });

    if (result.success) {
      return [true, `✅ worktree '${worktreeName}' を削除しました`];
    }

    // Try force remove
    result = await asyncSpawn("git", ["-C", repoRoot, "worktree", "remove", "-f", relPath], {
      timeout: TIMEOUT_MEDIUM * 1000,
    });

    if (result.success) {
      return [true, `✅ worktree '${worktreeName}' を強制削除しました`];
    }

    return [false, `⚠️ worktree削除失敗: ${result.stderr.trim()}`];
  } catch (e) {
    if (e instanceof Error && e.message.includes("timeout")) {
      return [false, "⚠️ worktree削除がタイムアウトしました"];
    }
    return [false, `⚠️ worktree削除エラー: ${formatError(e)}`];
  }
}

/**
 * Extract PR number from gh pr merge output.
 *
 * Issue #778: When `gh pr merge` succeeds, it outputs:
 *   ✓ Merged pull request #123 (Title)
 * or similar. Parse the PR number from this output.
 *
 * This is more reliable than using cwd-dependent gh commands
 * since hooks run from CLAUDE_PROJECT_DIR, not the worktree.
 *
 * @param stdout - The stdout from gh pr merge command.
 * @returns The PR number if found, or null.
 */
function extractPrNumberFromOutput(stdout: string): number | null {
  // Match patterns like:
  // ✓ Merged pull request #123
  // ✓ Squashed and merged pull request #123
  // ✓ Rebased and merged pull request #123
  const match = stdout.match(/(?:Merged|merged)\s+pull\s+request\s+#(\d+)/);
  if (match) {
    return Number.parseInt(match[1], 10);
  }
  return null;
}

/**
 * Print continue result and log skip reason.
 */
function printContinueAndLogSkip(reason: string, sessionId?: string | null): void {
  logHookExecution(HOOK_NAME, "approve", reason, undefined, {
    sessionId: sessionId ?? undefined,
  });
  console.log(JSON.stringify({ continue: true }));
}

async function main(): Promise<void> {
  const result: Record<string, unknown> = { continue: true };

  try {
    const inputData = await parseHookInput();
    // Issue #2607: Create context for session_id logging
    const ctx = createHookContext(inputData);
    const toolInput = inputData.tool_input ?? {};
    const toolResult = (getToolResult(inputData as Record<string, unknown>) ?? {}) as Record<
      string,
      unknown
    >;

    const command = typeof toolInput.command === "string" ? toolInput.command : "";
    // Default to 0 (success) if exit_code not provided
    // Issue #1470: Previous default of -1 caused cleanup to be skipped for successful commands
    const exitCodeValue = toolResult.exit_code ?? toolResult.exitCode ?? 0;
    const exitCode = typeof exitCodeValue === "number" ? exitCodeValue : 0;

    // Check if this is a real gh pr merge command (with or without PR number)
    // Use regex to avoid false positives like `echo "gh pr merge"`
    // Pattern matches: `gh pr merge`, `cd dir && gh pr merge`, `false || gh pr merge`, etc.
    // But NOT: `echo "gh pr merge"`, `# gh pr merge`, etc.
    if (!command.match(/(?:^|&&\s*|;\s*|\|\|\s*)gh pr merge\b/) || exitCode !== 0) {
      printContinueAndLogSkip("not a gh pr merge or failed", ctx.sessionId);
      return;
    }

    // Get repository root first (needed for PR lookup)
    const repoRoot = getRepoRoot();
    if (!repoRoot) {
      printContinueAndLogSkip("repo root not found", ctx.sessionId);
      return;
    }

    // Try to extract PR number from command or output
    // Method 1: From command args (e.g., `gh pr merge 123`, `gh pr merge #123`)
    let prNumber: number | null = null;
    const prMatch = command.match(/gh pr merge\s+(?:--?\S+\s+)*#?(\d+)/);
    if (prMatch) {
      prNumber = Number.parseInt(prMatch[1], 10);
    } else {
      // Method 2: From merge output (e.g., "✓ Merged pull request #123")
      // This handles `gh pr merge --squash` which merges current branch's PR
      const stdout = typeof toolResult.stdout === "string" ? toolResult.stdout : "";
      prNumber = extractPrNumberFromOutput(stdout);
      if (!prNumber) {
        printContinueAndLogSkip("PR number not found", ctx.sessionId);
        return;
      }
    }

    // Get PR branch name
    const branch = await getPrBranch(prNumber, repoRoot);
    if (!branch) {
      printContinueAndLogSkip(`branch not found for PR#${prNumber}`, ctx.sessionId);
      return;
    }

    // Find corresponding worktree
    const worktreePath = await findWorktreeByBranch(repoRoot, branch);
    if (!worktreePath) {
      // No worktree found - that's fine, maybe it was created differently
      printContinueAndLogSkip(`worktree not found for branch ${branch}`, ctx.sessionId);
      return;
    }

    // Issue #803: Check if cwd is inside the worktree before attempting deletion
    // subprocess calls bypass PreToolUse hooks, so we must check here
    // Pass command to detect cd-prefixed patterns like "cd <worktree> && gh pr merge"
    if (checkCwdInsidePath(worktreePath, command)) {
      const worktreeName = worktreePath.split("/").pop() ?? "";
      result.systemMessage = `⚠️ worktree '${worktreeName}' の自動削除をスキップしました。\n\n現在の作業ディレクトリ (cwd) が削除対象のworktree内にあります。\n手動で削除するには:\n1. cd ${repoRoot}\n2. git worktree remove .worktrees/${worktreeName}`;
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `cwdがworktree内のため削除スキップ: ${worktreeName}`,
        undefined,
        { sessionId: ctx.sessionId ?? undefined },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Remove the worktree
    const [, message] = await removeWorktree(repoRoot, worktreePath);
    result.systemMessage = message;
  } catch (e) {
    // Don't block on errors
    result.systemMessage = `⚠️ worktree自動削除中にエラー: ${formatError(e)}`;
  }

  await logHookExecution(
    HOOK_NAME,
    "approve",
    typeof result.systemMessage === "string" ? result.systemMessage : undefined,
  );
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    // Hooks should not block on internal errors - output continue response
    console.log(JSON.stringify({ continue: true }));
    process.exit(0);
  });
}
