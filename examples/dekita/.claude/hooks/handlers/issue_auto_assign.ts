#!/usr/bin/env bun
/**
 * worktree作成時にブランチ名からIssue番号を抽出し自動アサイン・競合チェック。
 *
 * Why:
 *   複数セッションが同じIssueに着手すると作業の重複・競合が発生する。
 *   worktree作成時点でIssueの状態を確認し、競合を事前に防止する。
 *
 * What:
 *   - git worktree addコマンドからブランチ名/パスを解析
 *   - ブランチ名からIssue番号を抽出（issue-123, fix/123-desc等）
 *   - 以下をブロック: クローズ済み、重複worktree、リモートブランチ存在、
 *     オープンPR存在、他者アサイン済み
 *   - 未アサインなら自動で@meにアサイン
 *   - 最近マージされたPRがあれば警告
 *
 * Remarks:
 *   - ブロック型フック（競合防止のため厳格）
 *   - 自分のみアサイン済みは許可（作業継続）
 *   - worktree-creation-markerはセッション追跡、本フックは競合防止
 *   - Python版: issue_auto_assign.py
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#1453: 最近マージされたPR警告を追加
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { TIMEOUT_HEAVY, TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "issue-auto-assign";

/**
 * Extract issue number from branch name.
 *
 * Patterns:
 * - #123
 * - issue-123, issue_123
 * - /123- or /123_ (after slash, like fix/123-description)
 * - -123- or _123_ (embedded, like feature-123-name)
 * - -123 or _123 (at end, like feature-123)
 */
export function extractIssueNumber(branchName: string): number | null {
  const patterns = [
    /#(\d+)/, // #123
    /issue[_-](\d+)/i, // issue-123, issue_123
    /\/(\d+)[-_]/, // /123-description
    /[-_](\d+)[-_]/, // feature-123-name
    /[-_](\d+)$/, // feature-123 (at end)
  ];

  for (const pattern of patterns) {
    const match = branchName.match(pattern);
    if (match) {
      return Number.parseInt(match[1], 10);
    }
  }

  return null;
}

/**
 * Get list of existing worktree branches.
 *
 * @returns List of [worktree_path, branch_name] tuples.
 */
function getExistingWorktreeBranches(): Array<[string, string]> {
  try {
    const result = execSync("git worktree list --porcelain", {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    const worktrees: Array<[string, string]> = [];
    let currentPath: string | null = null;
    let currentBranch: string | null = null;

    for (const line of result.split("\n")) {
      if (line.startsWith("worktree ")) {
        // Save previous worktree if it had a branch
        if (currentPath && currentBranch) {
          worktrees.push([currentPath, currentBranch]);
        }
        currentPath = line.slice(9); // Remove "worktree " prefix
        currentBranch = null; // Reset for new entry
      } else if (line.startsWith("branch refs/heads/")) {
        currentBranch = line.slice(18); // Remove "branch refs/heads/" prefix
      }
    }

    // Don't forget the last worktree
    if (currentPath && currentBranch) {
      worktrees.push([currentPath, currentBranch]);
    }

    return worktrees;
  } catch {
    // Fail open: return empty list on error to avoid blocking
    return [];
  }
}

/**
 * Extract issue number from worktree path.
 */
export function extractIssueFromPath(path: string | null): number | null {
  if (!path) {
    return null;
  }

  // Extract the worktree name from path
  for (const prefix of [".worktrees/", "worktrees/"]) {
    if (path.includes(prefix)) {
      const worktreeName = path.split(prefix).pop() || "";
      return extractIssueNumber(worktreeName);
    }
  }

  // Try the path directly
  return extractIssueNumber(path);
}

/**
 * Check if another worktree already exists for the same issue.
 */
function findDuplicateIssueWorktree(
  issueNumber: number,
  newBranch: string | null,
  newPath: string | null,
): [string, string] | null {
  const worktrees = getExistingWorktreeBranches();

  for (const [path, branch] of worktrees) {
    // Skip if same branch name or path
    if (branch === newBranch) {
      continue;
    }
    if (newPath && path.endsWith(newPath.replace(/^\./, ""))) {
      continue;
    }

    // Check if this worktree's branch references the same issue
    let existingIssue = extractIssueNumber(branch);
    // Also check path if branch didn't have issue number
    if (existingIssue === null) {
      existingIssue = extractIssueFromPath(path);
    }
    if (existingIssue === issueNumber) {
      return [path, branch];
    }
  }

  return null;
}

/**
 * Check if a remote branch already exists for the same issue.
 */
function findRemoteBranchForIssue(issueNumber: number, newBranch: string | null): string | null {
  try {
    // Fetch latest remote branches (quiet mode, prune deleted, origin only)
    execSync("git fetch --quiet --prune origin", {
      timeout: TIMEOUT_HEAVY * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    // Get all remote branches
    const result = execSync("git branch -r", {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    for (const line of result.trim().split("\n")) {
      const branch = line.trim();
      if (!branch || branch.includes("->")) {
        // Skip HEAD pointer
        continue;
      }

      // Remove remote prefix for comparison
      const localName = branch.includes("/") ? branch.split("/").slice(1).join("/") : branch;
      if (localName === newBranch) {
        continue;
      }

      // Check if this branch references the same issue
      const existingIssue = extractIssueNumber(branch);
      if (existingIssue === issueNumber) {
        return branch;
      }
    }
  } catch {
    // Fail open: return null on error to avoid blocking
  }
  return null;
}

interface PrInfo {
  number: number;
  title: string;
  url: string;
  body?: string;
  headRefName?: string;
  mergedAt?: string;
}

/**
 * Check if an open PR already exists that references this issue.
 */
function findOpenPrForIssue(issueNumber: number): PrInfo | null {
  try {
    const result = execSync("gh pr list --state open --json number,title,url,body,headRefName", {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    const prs = JSON.parse(result) as PrInfo[];
    for (const pr of prs) {
      // Check if PR body contains "Closes #N" or "Fixes #N"
      const body = pr.body || "";
      const regex = new RegExp(`(?:closes|fixes|resolves)\\s*#?${issueNumber}\\b`, "i");
      if (regex.test(body)) {
        return { number: pr.number, title: pr.title, url: pr.url };
      }

      // Also check branch name
      const branch = pr.headRefName || "";
      const branchIssue = extractIssueNumber(branch);
      if (branchIssue === issueNumber) {
        return { number: pr.number, title: pr.title, url: pr.url };
      }
    }
  } catch {
    // Fail open: return null on error to avoid blocking
  }
  return null;
}

/**
 * Check if a PR referencing this issue was merged recently.
 */
function findRecentlyMergedPrForIssue(issueNumber: number, hours = 24): PrInfo | null {
  try {
    const result = execSync(
      "gh pr list --state merged --json number,title,url,body,headRefName,mergedAt --limit 50",
      {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );

    const prs = JSON.parse(result) as PrInfo[];
    const threshold = new Date(Date.now() - hours * 60 * 60 * 1000);

    for (const pr of prs) {
      // Parse merge time
      const mergedAtStr = pr.mergedAt || "";
      if (!mergedAtStr) {
        continue;
      }

      const mergedAt = new Date(mergedAtStr);
      if (mergedAt < threshold) {
        continue;
      }

      // Check if PR body contains "Closes #N" or "Fixes #N"
      const body = pr.body || "";
      const regex = new RegExp(`(?:closes|fixes|resolves)\\s*#?${issueNumber}\\b`, "i");
      if (regex.test(body)) {
        return { number: pr.number, title: pr.title, url: pr.url, mergedAt: pr.mergedAt };
      }

      // Also check branch name
      const branch = pr.headRefName || "";
      const branchIssue = extractIssueNumber(branch);
      if (branchIssue === issueNumber) {
        return { number: pr.number, title: pr.title, url: pr.url, mergedAt: pr.mergedAt };
      }
    }
  } catch {
    // Fail open: return null on error to avoid blocking
  }
  return null;
}

interface IssueInfo {
  state: string;
  assignees: Array<{ login: string }>;
}

/**
 * Get issue state and assignees.
 */
function getIssueInfo(issueNumber: number): IssueInfo | null {
  try {
    const result = execSync(`gh issue view ${issueNumber} --json state,assignees`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return JSON.parse(result) as IssueInfo;
  } catch {
    return null;
  }
}

/**
 * Get the current GitHub user login.
 */
function getCurrentUser(): string | null {
  try {
    const result = execSync("gh api user --jq .login", {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return result.trim() || null;
  } catch {
    return null;
  }
}

/**
 * Assign the issue to the current user.
 */
function assignIssue(issueNumber: number): boolean {
  try {
    execSync(`gh issue edit ${issueNumber} --add-assignee @me`, {
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * Parse git worktree add command and extract branch name and path.
 */
export function parseWorktreeAddCommand(command: string): [string | null, string | null] {
  if (!command.includes("git worktree add")) {
    return [null, null];
  }

  let branchName: string | null = null;
  let worktreePath: string | null = null;

  // Look for -b <branch> pattern
  const branchMatch = command.match(/-b\s+([^\s]+)/);
  if (branchMatch) {
    branchName = branchMatch[1];
  }

  // Parse command parts to find positional arguments
  const parts = command.split(/\s+/);

  // Find position of 'add' to start looking for positional args
  const addIdx = parts.indexOf("add");
  if (addIdx === -1) {
    return [branchName, worktreePath];
  }

  // Collect positional arguments (non-option arguments after 'add')
  const positionalArgs: string[] = [];
  let skipNext = false;
  for (const part of parts.slice(addIdx + 1)) {
    if (skipNext) {
      skipNext = false;
      continue;
    }
    if (part.startsWith("-")) {
      // Skip options that take an argument
      if (part === "-b" || part === "--reason") {
        skipNext = true;
      }
      // --lock is a flag without argument, just skip it
      continue;
    }
    positionalArgs.push(part);
  }

  // First positional arg is always the path
  if (positionalArgs.length >= 1) {
    worktreePath = positionalArgs[0];
  }

  // If we have 2 positional args and no -b branch, the second is the branch name
  if (positionalArgs.length >= 2 && !branchName) {
    branchName = positionalArgs[1];
  }

  return [branchName, worktreePath];
}

async function main(): Promise<void> {
  let result: { decision?: string; reason?: string; systemMessage?: string } = {};
  let sessionId: string | undefined;

  try {
    // Read input from stdin
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;
    const toolInput = inputData.tool_input ?? {};
    const command = (toolInput.command as string) ?? "";

    // Only process git worktree add commands
    if (command.includes("git worktree add")) {
      // Extract branch name and path
      const [branchName, worktreePath] = parseWorktreeAddCommand(command);

      // Try to extract issue number from branch name first, then from path
      let issueNumber: number | null = null;
      if (branchName) {
        issueNumber = extractIssueNumber(branchName);
      }
      if (issueNumber === null) {
        issueNumber = extractIssueFromPath(worktreePath);
      }

      if (issueNumber !== null) {
        // First, check issue state (must be done before other checks)
        const issueInfo = getIssueInfo(issueNumber);
        if (issueInfo && issueInfo.state === "CLOSED") {
          const reason = `🚫 Issue #${issueNumber} は既にクローズされています。\nオープンなIssueを選択してください。\n確認: \`gh issue view ${issueNumber}\``;
          result = makeBlockResult(HOOK_NAME, reason);
        } else {
          // Check if another worktree already exists for this issue (BLOCK)
          const duplicate = findDuplicateIssueWorktree(issueNumber, branchName, worktreePath);
          if (duplicate) {
            const [dupPath, dupBranch] = duplicate;
            const reason = `🚫 Issue #${issueNumber} は既に別のworktreeで作業中です！\n   既存worktree: ${dupPath}\n   ブランチ: ${dupBranch}\n\n別のIssueを選択するか、既存worktreeで作業を続けてください。`;
            result = makeBlockResult(HOOK_NAME, reason);
          } else {
            // Check if a remote branch already exists for this issue (BLOCK)
            const remoteBranch = findRemoteBranchForIssue(issueNumber, branchName);
            if (remoteBranch) {
              const reason = `🚫 Issue #${issueNumber} のリモートブランチが既に存在します！\n   リモートブランチ: ${remoteBranch}\n\n既存ブランチで作業するか、別のIssueを選択してください。\n既存ブランチを使う: \`git worktree add .worktrees/issue-${issueNumber} ${remoteBranch}\``;
              result = makeBlockResult(HOOK_NAME, reason);
            } else {
              // Check if an open PR already exists for this issue (BLOCK)
              const existingPr = findOpenPrForIssue(issueNumber);
              if (existingPr) {
                const reason = `🚫 Issue #${issueNumber} を参照するオープンPRが既に存在します！\n   PR #${existingPr.number}: ${existingPr.title}\n   URL: ${existingPr.url}\n\n既存PRをレビュー・マージするか、別のIssueを選択してください。`;
                result = makeBlockResult(HOOK_NAME, reason);
              } else {
                // Check if issue already has assignees (BLOCK to prevent conflicts)
                const assignees =
                  issueInfo?.assignees?.map((a) => a.login).filter((login) => login?.trim()) || [];

                if (assignees.length > 0) {
                  // Get current user to check if self-assigned
                  const currentUser = getCurrentUser();
                  // Block only if there are assignees OTHER than the current user
                  const otherAssignees = currentUser
                    ? assignees.filter((a) => a !== currentUser)
                    : assignees;

                  if (otherAssignees.length > 0) {
                    const reason = `🚫 Issue #${issueNumber} は既にアサイン済み: ${otherAssignees.join(", ")}\nこのIssueは他のセッションで作業中の可能性があります。\n\n別のIssueを選択するか、担当者に確認してください。\n確認: \`gh issue view ${issueNumber}\``;
                    result = makeBlockResult(HOOK_NAME, reason);
                  } else {
                    // Only self-assigned - allow the operation
                    result.systemMessage = `✅ Issue #${issueNumber} は既に自分にアサイン済み（作業継続可能）`;
                  }
                } else {
                  // Auto-assign the issue
                  if (assignIssue(issueNumber)) {
                    result.systemMessage = `✅ Issue #${issueNumber} に自動アサインしました（競合防止）`;
                  } else {
                    result.systemMessage =
                      `⚠️ Issue #${issueNumber} のアサインに失敗しました。` +
                      `手動で実行: \`gh issue edit ${issueNumber} --add-assignee @me\``;
                  }
                }

                // Issue #1453: Check for recently merged PRs (warning only)
                // Note: Skip warning if already blocking (warning is redundant)
                if (result.decision !== "block") {
                  const mergedPr = findRecentlyMergedPrForIssue(issueNumber);
                  if (mergedPr) {
                    const warning = `\n\n⚠️ Issue #${issueNumber} を参照するPRが最近マージされました:\n   PR #${mergedPr.number}: ${mergedPr.title}\n   URL: ${mergedPr.url}\n\n同じ修正が既に適用されている可能性があります。\n確認: \`gh pr view ${mergedPr.number}\``;
                    result.systemMessage = (result.systemMessage || "") + warning;
                  }
                }
              }
            }
          }
        }
      }
    }
  } catch (error) {
    // Don't block on errors
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    result = {};
  }

  // Always log execution for accurate statistics
  await logHookExecution(HOOK_NAME, result.decision ?? "approve", result.reason, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
