#!/usr/bin/env bun
/**
 * セッション開始時に作業中（未マージ）のworktree一覧を表示する。
 *
 * Why:
 *   複数セッション間で同じIssueへの重複着手を防止するため、
 *   既存の作業状況を把握する必要がある。
 *
 * What:
 *   - 作業中のworktree（PRがOPEN/未作成）を検出
 *   - ブランチ名、PR状態、最終コミット情報を表示
 *   - 情報提供のみ（ブロックしない）
 *
 * Remarks:
 *   - 情報提供型フック（ブロックしない、systemMessageで通知）
 *   - worktree-session-guardはブロック、本フックは情報提供
 *   - session-worktree-statusは現在のworktree、本フックは全worktree
 *   - Python版: active_worktree_check.py
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
import { checkAndUpdateSessionMarker, parseHookInput } from "../lib/session";

const HOOK_NAME = "active-worktree-check";

interface ActiveWorktree {
  name: string;
  branch: string;
  prNumber: number | null;
  prState: string | null;
  lastCommit: string | null;
}

interface PrInfo {
  number: number;
  title: string;
  state: string;
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
 * Get the last commit info of a worktree.
 */
function getWorktreeLastCommit(worktreePath: string): string | null {
  try {
    const result = execSync(`git -C "${worktreePath}" log -1 --format="%h %s"`, {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const commit = result.trim();
    return commit ? commit.slice(0, 60) : null;
  } catch {
    return null;
  }
}

/**
 * Check the PR status for the given branch.
 */
function checkPrStatus(branch: string): PrInfo | null {
  try {
    const result = execSync(
      `gh pr list --state all --head "${branch}" --json number,title,state --limit 1`,
      {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    if (result.trim()) {
      const prs = JSON.parse(result);
      if (prs.length > 0) {
        return prs[0];
      }
    }
  } catch {
    // gh CLI unavailable, timeout, or invalid response - skip
  }
  return null;
}

/**
 * Find worktrees that are actively being worked on (not merged).
 */
function findActiveWorktrees(repoRoot: string): ActiveWorktree[] {
  const worktreesDir = join(repoRoot, ".worktrees");
  if (!existsSync(worktreesDir)) {
    return [];
  }

  const active: ActiveWorktree[] = [];

  try {
    const entries = readdirSync(worktreesDir).sort();
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

        const prInfo = checkPrStatus(branch);

        // Skip merged PRs (handled by merged-worktree-check)
        if (prInfo && prInfo.state === "MERGED") {
          continue;
        }

        const lastCommit = getWorktreeLastCommit(itemPath);

        active.push({
          name: item,
          branch,
          prNumber: prInfo?.number ?? null,
          prState: prInfo?.state ?? null,
          lastCommit,
        });
      } catch {
        // Skip items we can't process
      }
    }
  } catch {
    // Skip if we can't read the directory
  }

  return active;
}

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};
  let sessionId: string | undefined;

  try {
    const hookInput = await parseHookInput();
    sessionId = hookInput.session_id;

    if (await checkAndUpdateSessionMarker(HOOK_NAME)) {
      const projectDirStr = process.env.CLAUDE_PROJECT_DIR ?? "";
      if (projectDirStr) {
        const repoRoot = getRepoRoot(projectDirStr);

        if (repoRoot) {
          const active = findActiveWorktrees(repoRoot);

          if (active.length > 0) {
            // PR状態の日本語マッピング
            const stateJa: Record<string, string> = {
              OPEN: "レビュー中",
              CLOSED: "クローズ",
            };

            const lines = active.map((w) => {
              let prStatus: string;
              if (w.prNumber !== null) {
                const stateDisplay = w.prState ? (stateJa[w.prState] ?? w.prState) : "";
                prStatus = `PR #${w.prNumber}: ${stateDisplay}`;
              } else {
                prStatus = "PRなし";
              }
              const commitInfo = w.lastCommit ? ` - ${w.lastCommit}` : "";
              return `  - .worktrees/${w.name} (branch: ${w.branch}, ${prStatus})${commitInfo}`;
            });

            const activeList = lines.join("\n");

            result.systemMessage = `📋 **作業中のworktreeがあります**:
${activeList}

重複着手を避けるため、既存のworktreeを確認してください。`;
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
