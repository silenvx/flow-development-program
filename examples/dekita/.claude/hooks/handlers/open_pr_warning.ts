#!/usr/bin/env bun
/**
 * セッション開始時にオープンPRと関連worktreeを表示し介入を防止する。
 *
 * Why:
 *   別セッションが作業中のPR/Issueに介入するとコンフリクトや
 *   重複作業が発生する。オープンPRを表示し介入を防止する。
 *
 * What:
 *   - オープンPRの一覧を取得
 *   - 各PRに関連するworktreeを特定
 *   - ロックされたworktree（PRなし）も検出
 *   - セッション開始時に警告メッセージを表示
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、判断はエージェントに委ねる）
 *   - SessionStartで発火
 *   - session-handoff-readerは前回セッション引き継ぎ（補完関係）
 *   - active-worktree-checkはPreToolUseでの確認（タイミング違い）
 *   - Python版: open_pr_warning.py
 *
 * Changelog:
 *   - silenvx/dekita#673: フック追加
 *   - silenvx/dekita#1095: PRに関連しないロックworktreeも表示
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { checkAndMarkSessionAction, parseHookInput } from "../lib/session";

const HOOK_NAME = "open-pr-warning";

export interface PrInfo {
  number: number;
  title: string;
  headRefName: string;
  author: { login: string };
}

export interface WorktreeInfo {
  path: string;
  branch?: string;
  locked?: string;
}

export interface PrWorktreeMatch {
  number: number;
  title: string;
  branch: string;
  author: string;
  worktree: WorktreeInfo | null;
}

/**
 * Get open PRs from GitHub.
 *
 * @returns Tuple of [prs_list, error_message].
 */
function getOpenPrs(): [PrInfo[], string | null] {
  try {
    const result = execSync("gh pr list --state open --json number,title,headRefName,author", {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const prs = JSON.parse(result) as PrInfo[];
    return [prs, null];
  } catch (error) {
    if (error instanceof Error) {
      if (error.message.includes("ETIMEDOUT") || error.message.includes("timed out")) {
        return [[], "gh pr list timed out"];
      }
      return [[], `gh pr list failed: ${error.message}`];
    }
    return [[], "Unknown error fetching PRs"];
  }
}

/**
 * Get worktree list.
 */
function getWorktrees(): WorktreeInfo[] {
  try {
    const result = execSync("git worktree list --porcelain", {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    const output = result.trim();
    if (!output) {
      return [];
    }

    const worktrees: WorktreeInfo[] = [];
    let current: WorktreeInfo | null = null;

    for (const line of output.split("\n")) {
      if (line.startsWith("worktree ")) {
        if (current) {
          worktrees.push(current);
        }
        current = { path: line.slice(9) };
      } else if (line.startsWith("branch ") && current) {
        current.branch = line.slice(7);
      } else if ((line === "locked" || line.startsWith("locked ")) && current) {
        current.locked = "true";
      }
    }

    if (current) {
      worktrees.push(current);
    }

    return worktrees;
  } catch {
    return [];
  }
}

/**
 * Extract issue number from branch name.
 */
export function extractIssueNumber(branchName: string): number | null {
  // Pattern: issue-123, feat/issue-123-xxx, fix/issue-123-yyy
  const match = branchName.match(/issue-(\d+)/i);
  if (match) {
    return Number.parseInt(match[1], 10);
  }
  return null;
}

/**
 * Match PRs to worktrees.
 */
export function matchPrToWorktree(prs: PrInfo[], worktrees: WorktreeInfo[]): PrWorktreeMatch[] {
  const result: PrWorktreeMatch[] = [];

  for (const pr of prs) {
    const prBranch = pr.headRefName || "";
    const prIssue = extractIssueNumber(prBranch);

    // Find matching worktree
    let matchedWorktree: WorktreeInfo | null = null;
    for (const wt of worktrees) {
      const wtBranch = wt.branch || "";
      const wtPath = wt.path || "";

      // Normalize worktree branch (strip refs/heads/ prefix)
      let normalizedWtBranch = wtBranch;
      if (wtBranch.startsWith("refs/heads/")) {
        normalizedWtBranch = wtBranch.slice("refs/heads/".length);
      }

      // Branch name exact match
      if (normalizedWtBranch === prBranch) {
        matchedWorktree = wt;
        break;
      }

      // Worktree path contains issue number
      if (prIssue !== null) {
        const wtIssue = extractIssueNumber(wtPath);
        if (wtIssue === prIssue) {
          matchedWorktree = wt;
          break;
        }
      }
    }

    result.push({
      number: pr.number,
      title: pr.title || "",
      branch: prBranch,
      author: pr.author?.login || "unknown",
      worktree: matchedWorktree,
    });
  }

  return result;
}

/**
 * Get locked worktrees that are not matched to any PR.
 *
 * Issue #1095: PRに関連しないworktreeも競合リスクとして表示
 */
export function getUnmatchedLockedWorktrees(
  worktrees: WorktreeInfo[],
  prWorktreeMap: PrWorktreeMatch[],
): WorktreeInfo[] {
  // Collect matched worktree paths
  const matchedPaths = new Set<string>();
  for (const item of prWorktreeMap) {
    if (item.worktree) {
      matchedPaths.add(item.worktree.path);
    }
  }

  // Find locked worktrees that are not matched to any PR
  const unmatchedLocked: WorktreeInfo[] = [];
  for (const wt of worktrees) {
    if (wt.locked === "true" && !matchedPaths.has(wt.path)) {
      // Exclude main repository (only include paths with /.worktrees/)
      if (wt.path.includes("/.worktrees/")) {
        unmatchedLocked.push(wt);
      }
    }
  }

  return unmatchedLocked;
}

/**
 * Format warning message in Markdown.
 */
export function formatWarningMessage(
  prWorktreeMap: PrWorktreeMatch[],
  unmatchedLockedWorktrees: WorktreeInfo[],
): string {
  if (prWorktreeMap.length === 0 && unmatchedLockedWorktrees.length === 0) {
    return "";
  }

  const lines: string[] = [];

  // Open PR section
  if (prWorktreeMap.length > 0) {
    lines.push("⚠️ **オープンPRが存在します** (介入禁止)");
    lines.push("");
    lines.push("以下のPRは別セッションが担当している可能性があります。");
    lines.push("これらのIssue/PRには一切触れないでください。");
    lines.push("");

    for (const item of prWorktreeMap) {
      lines.push(`- **PR #${item.number}**: ${item.title}`);
      lines.push(`  - ブランチ: \`${item.branch}\``);
      lines.push(`  - 作成者: ${item.author}`);

      if (item.worktree) {
        const lockStatus = item.worktree.locked === "true" ? " 🔒 ロック中" : "";
        lines.push(`  - worktree: \`${item.worktree.path}\`${lockStatus}`);
      }

      lines.push("");
    }
  }

  // Locked worktrees (no PR) section
  if (unmatchedLockedWorktrees.length > 0) {
    lines.push("🔒 **ロックされたworktree** (PRなし)");
    lines.push("");
    lines.push("以下のworktreeは別セッションが作業中の可能性があります。");
    lines.push("");

    for (const wt of unmatchedLockedWorktrees) {
      let branch = wt.branch || "";
      if (branch.startsWith("refs/heads/")) {
        branch = branch.slice("refs/heads/".length);
      }
      lines.push(`- \`${wt.path}\``);
      if (branch) {
        lines.push(`  - ブランチ: \`${branch}\``);
      }
      lines.push("");
    }
  }

  lines.push("---");
  lines.push("新しいタスクを始める場合は、上記以外のIssueを選んでください。");

  return lines.join("\n");
}

async function main(): Promise<void> {
  // Set session_id for proper logging
  const input = await parseHookInput();
  const sessionId = input.session_id;

  // Run only once per session (if session ID is available)
  if (sessionId && !checkAndMarkSessionAction(sessionId, HOOK_NAME)) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  const result: { continue: boolean; message?: string } = { continue: true };

  try {
    const [prs, prError] = getOpenPrs();
    const worktrees = getWorktrees();

    if (prError) {
      // Failed to fetch PRs - show warning
      const warningMsg = `⚠️ **オープンPRの確認に失敗しました**\n\nエラー: ${prError}\n\nオープンPRが存在する可能性があります。\n新しいタスクを始める前に、手動で確認してください:\n\`\`\`\ngh pr list --state open\n\`\`\``;
      result.message = warningMsg;
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "Failed to fetch PRs",
        {
          error: prError,
        },
        { sessionId },
      );
    } else {
      const prWorktreeMap = prs.length > 0 ? matchPrToWorktree(prs, worktrees) : [];
      // Issue #1095: PRに関連しないロックされたworktreeも検出
      const unmatchedLocked = getUnmatchedLockedWorktrees(worktrees, prWorktreeMap);

      const message = formatWarningMessage(prWorktreeMap, unmatchedLocked);

      if (message) {
        result.message = message;
      }

      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Found ${prs.length} open PRs, ${unmatchedLocked.length} locked worktrees without PR`,
        {
          open_pr_count: prs.length,
          worktree_count: worktrees.length,
          matched_count: prWorktreeMap.filter((item) => item.worktree !== null).length,
          unmatched_locked_count: unmatchedLocked.length,
        },
        { sessionId },
      );
    }
  } catch (error) {
    // エラーがあっても継続
    await logHookExecution(
      HOOK_NAME,
      "approve",
      "Error checking open PRs",
      { error: formatError(error) },
      { sessionId },
    );
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
