#!/usr/bin/env bun
/**
 * Fork-session開始時に独立したIssue候補を提案する。
 *
 * Why:
 *   Fork-sessionが親セッションと競合するIssueに着手すると、
 *   コンフリクトや重複作業が発生する。独立したIssue候補を
 *   提案することで、効率的な並行作業を実現する。
 *
 * What:
 *   - Fork-sessionかどうかを検出
 *   - 親/siblingセッションのworktreeを特定
 *   - 競合しない独立したIssue候補を提案
 *
 * Remarks:
 *   - 提案のみでブロックはしない
 *   - 通常セッションでは何も出力しない
 *   - session-worktree-statusは警告のみ、こちらは積極的な提案
 *   - Python版からTypeScriptへ移行（Issue #3051）
 *
 * Changelog:
 *   - silenvx/dekita#2513: フック追加（Python版）
 *   - silenvx/dekita#3051: TypeScriptに移行
 */

import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { SESSION_MARKER_FILE, TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import {
  extractIssueNumberFromBranch as extractIssueNumberFromBranchString,
  getOriginDefaultBranch,
} from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { getSessionAncestry, isForkSession, parseHookInput } from "../lib/session";

const HOOK_NAME = "fork-session-collaboration-advisor";

export interface WorktreeInfo {
  path: string;
  branch: string;
  sessionId: string | null;
  issueNumber: number | null;
  changedFiles: string[];
}

/**
 * Get list of worktrees from git.
 */
function getWorktreeList(): Array<{ path: string; branch: string; isLocked: boolean }> {
  try {
    const result = execSync("git worktree list --porcelain", {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT,
    });

    const worktrees: Array<{ path: string; branch: string; isLocked: boolean }> = [];
    let currentPath: string | null = null;
    let currentBranch = "";
    let isLocked = false;

    for (const line of result.split("\n")) {
      if (line.startsWith("worktree ")) {
        if (currentPath !== null) {
          worktrees.push({ path: currentPath, branch: currentBranch, isLocked });
        }
        currentPath = line.slice(9);
        currentBranch = "";
        isLocked = false;
      } else if (line.startsWith("branch refs/heads/")) {
        currentBranch = line.slice(18);
      } else if (line === "locked") {
        isLocked = true;
      }
    }

    if (currentPath !== null) {
      worktrees.push({ path: currentPath, branch: currentBranch, isLocked });
    }

    return worktrees;
  } catch {
    return [];
  }
}

/**
 * Get session ID from worktree's .claude-session marker.
 */
function getWorktreeSessionId(worktreePath: string): string | null {
  const markerFile = join(worktreePath, SESSION_MARKER_FILE);
  try {
    if (!existsSync(markerFile)) {
      return null;
    }
    const data = JSON.parse(readFileSync(markerFile, "utf-8"));
    return data.session_id ?? null;
  } catch {
    return null;
  }
}

/**
 * Extract issue number from branch name.
 * Returns number (for WorktreeInfo interface compatibility).
 * Uses strict mode: only matches explicit "issue-XXX" patterns.
 */
export function extractIssueNumberFromBranch(branch: string): number | null {
  const result = extractIssueNumberFromBranchString(branch, { strict: true });
  return result ? Number.parseInt(result, 10) : null;
}

/**
 * Get files changed in worktree compared to origin default branch.
 */
async function getWorktreeChangedFiles(worktreePath: string): Promise<string[]> {
  const originBranch = await getOriginDefaultBranch(worktreePath);
  try {
    const result = execSync(`git -C "${worktreePath}" diff --name-only ${originBranch}...HEAD`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM,
    });
    return result
      .trim()
      .split("\n")
      .filter((f) => f.trim());
  } catch {
    try {
      // Fallback: try without the ... syntax
      const result = execSync(`git -C "${worktreePath}" diff --name-only ${originBranch}`, {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM,
      });
      return result
        .trim()
        .split("\n")
        .filter((f) => f.trim());
    } catch {
      return [];
    }
  }
}

/**
 * Get worktree session map.
 */
async function getWorktreeSessionMap(): Promise<Map<string, WorktreeInfo>> {
  const worktrees = getWorktreeList();
  const result = new Map<string, WorktreeInfo>();

  for (const { path, branch } of worktrees) {
    // Skip main worktree (not in .worktrees directory)
    if (!path.includes(".worktrees")) {
      continue;
    }

    const sessionId = getWorktreeSessionId(path);
    const issueNumber = extractIssueNumberFromBranch(branch);
    const changedFiles = await getWorktreeChangedFiles(path);

    result.set(path, {
      path,
      branch,
      sessionId,
      issueNumber,
      changedFiles,
    });
  }

  return result;
}

/**
 * Find sibling sessions (sessions that share a common ancestor).
 *
 * A sibling session is one that:
 * 1. Has a different session ID from the current session
 * 2. Shares at least one common ancestor session ID
 *
 * Performance: Only checks sessions currently active in worktrees (Issue #3063)
 * This avoids O(N) scanning of the transcript directory and ensures we don't
 * miss active sessions that have older transcripts.
 *
 * @param currentSessionId - Current session ID
 * @param transcriptPath - Path to current session's transcript file, or null
 * @param worktreeMap - Pre-fetched worktree map to avoid redundant git calls
 */
export function getSiblingSessions(
  currentSessionId: string,
  transcriptPath: string | null,
  worktreeMap: Map<string, WorktreeInfo>,
): Set<string> {
  if (!transcriptPath) {
    return new Set();
  }

  // Get current session's ancestry
  const currentAncestry = new Set(getSessionAncestry(transcriptPath));
  if (currentAncestry.size === 0) {
    return new Set();
  }

  // Get transcript directory
  const transcriptDir = dirname(transcriptPath);
  if (!existsSync(transcriptDir)) {
    return new Set();
  }

  const siblings = new Set<string>();
  try {
    // Pre-compute current ancestry excluding self (avoid recreating in loop)
    const currentAncestryExcludingSelf = new Set(currentAncestry);
    currentAncestryExcludingSelf.delete(currentSessionId);

    // Only check sessions that are currently active in worktrees
    // worktreeMap is passed from caller to avoid redundant getWorktreeSessionMap() calls
    // Use Set to avoid duplicate processing if multiple worktrees share the same sessionId
    const activeSessionIds = new Set<string>();
    for (const info of worktreeMap.values()) {
      if (info.sessionId && info.sessionId !== currentSessionId) {
        activeSessionIds.add(info.sessionId);
      }
    }

    for (const fileSessionId of activeSessionIds) {
      // Get other session's ancestry
      const otherPath = join(transcriptDir, `${fileSessionId}.jsonl`);
      if (!existsSync(otherPath)) {
        continue;
      }

      let otherAncestry: Set<string>;
      try {
        otherAncestry = new Set(getSessionAncestry(otherPath));
      } catch {
        // Skip corrupted/inaccessible session files
        continue;
      }
      if (otherAncestry.size === 0) {
        continue;
      }

      // Check for common ancestors (excluding the sessions themselves)
      const otherAncestryExcludingSelf = new Set(otherAncestry);
      otherAncestryExcludingSelf.delete(fileSessionId);

      // Find intersection
      for (const ancestorId of currentAncestryExcludingSelf) {
        if (otherAncestryExcludingSelf.has(ancestorId)) {
          siblings.add(fileSessionId);
          break;
        }
      }
    }
  } catch {
    // Fail silently if transcript directory is inaccessible
  }

  return siblings;
}

/**
 * Get active worktree sessions grouped by relationship.
 */
async function getActiveWorktreeSessions(
  currentSessionId: string,
  transcriptPath: string | null,
): Promise<{ ancestor: WorktreeInfo[]; sibling: WorktreeInfo[]; unknown: WorktreeInfo[] }> {
  const worktreeMap = await getWorktreeSessionMap();

  // Get ancestry and siblings (pass worktreeMap to avoid redundant git calls)
  const ancestry = new Set(transcriptPath ? getSessionAncestry(transcriptPath) : []);
  const siblings = getSiblingSessions(currentSessionId, transcriptPath, worktreeMap);

  const result: { ancestor: WorktreeInfo[]; sibling: WorktreeInfo[]; unknown: WorktreeInfo[] } = {
    ancestor: [],
    sibling: [],
    unknown: [],
  };

  for (const info of worktreeMap.values()) {
    if (info.sessionId === null) {
      result.unknown.push(info);
    } else if (info.sessionId === currentSessionId) {
      // Skip current session's worktree - not included in any category
    } else if (ancestry.has(info.sessionId)) {
      result.ancestor.push(info);
    } else if (siblings.has(info.sessionId)) {
      result.sibling.push(info);
    } else {
      // Treat as unknown if we can't determine relationship
      result.unknown.push(info);
    }
  }

  return result;
}

export interface Issue {
  number: number;
  title: string;
  labels: Array<{ name: string }>;
}

/**
 * Get open issues that don't have an associated PR.
 */
function getOpenIssuesWithoutPr(): Issue[] {
  try {
    // Get open PRs to filter out issues with PRs
    // Use limit 100 to avoid false positives (default is 30)
    const prResult = execSync("gh pr list --state open --json number,headRefName --limit 100", {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM,
    });
    const prs: Array<{ number: number; headRefName: string }> = JSON.parse(prResult);

    // Extract issue numbers from PR branch names
    const issueWithPr = new Set<number>();
    for (const pr of prs) {
      const issueNum = extractIssueNumberFromBranch(pr.headRefName);
      if (issueNum) {
        issueWithPr.add(issueNum);
      }
    }

    // Get open issues
    const issueResult = execSync(
      "gh issue list --state open --json number,title,labels --limit 50",
      {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM,
      },
    );
    const issues: Issue[] = JSON.parse(issueResult);

    // Filter out issues that have PRs
    return issues.filter((issue) => !issueWithPr.has(issue.number));
  } catch {
    return [];
  }
}

/**
 * Get priority score for an issue (lower is higher priority).
 */
export function getIssuePriority(issue: Issue): number {
  const labelNames = new Set(issue.labels.map((l) => l.name));

  if (labelNames.has("P0")) return 0;
  if (labelNames.has("P1")) return 1;
  if (labelNames.has("P2")) return 2;
  if (labelNames.has("P3")) return 3;
  return 4;
}

/**
 * Suggest independent issues that can be worked on.
 */
function suggestIndependentIssues(activeWorktreeInfos: WorktreeInfo[]): Issue[] {
  // Get issue numbers currently being worked on
  const activeIssueNumbers = new Set<number>();
  for (const info of activeWorktreeInfos) {
    if (info.issueNumber !== null) {
      activeIssueNumbers.add(info.issueNumber);
    }
  }

  // Get open issues without PRs
  const openIssues = getOpenIssuesWithoutPr();

  // Filter out issues being worked on
  const availableIssues = openIssues.filter((issue) => !activeIssueNumbers.has(issue.number));

  // Sort by priority
  return availableIssues
    .sort((a, b) => {
      const priorityDiff = getIssuePriority(a) - getIssuePriority(b);
      if (priorityDiff !== 0) return priorityDiff;
      return a.number - b.number;
    })
    .slice(0, 5);
}

/**
 * Format worktree info for display.
 */
function formatWorktreeInfo(info: WorktreeInfo): string {
  const parts: string[] = [];

  if (info.issueNumber) {
    parts.push(`  - Issue #${info.issueNumber}`);
  } else {
    const pathName = info.path.split("/").pop() ?? info.path;
    parts.push(`  - ${pathName}`);
  }

  if (info.changedFiles.length > 0) {
    const files = info.changedFiles.slice(0, 3);
    let filesStr = files.join(", ");
    if (info.changedFiles.length > 3) {
      filesStr += ` (+${info.changedFiles.length - 3} more)`;
    }
    parts.push(`    Files: ${filesStr}`);
  }

  return parts.join("\n");
}

/**
 * Format issue suggestion for display.
 */
function formatIssueSuggestion(issue: Issue, index: number): string {
  const priorityLabels = issue.labels.filter((l) => l.name.startsWith("P")).map((l) => l.name);
  const priorityStr = priorityLabels.length > 0 ? ` [${priorityLabels[0]}]` : "";

  return `  ${index}. #${issue.number}: ${issue.title}${priorityStr}`;
}

async function main(): Promise<void> {
  try {
    const inputData = await parseHookInput();

    // Get session info
    const sessionId = inputData.session_id ?? "";
    const source = inputData.source ?? "";
    const transcriptPath = inputData.transcript_path ?? null;

    // Only run for fork-sessions
    if (!isForkSession(sessionId, source, transcriptPath)) {
      return;
    }

    // Get active worktree sessions
    let activeSessions: Awaited<ReturnType<typeof getActiveWorktreeSessions>>;
    try {
      activeSessions = await getActiveWorktreeSessions(sessionId, transcriptPath);
    } catch (error) {
      // Fail silently - don't block on errors
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "Failed to get active sessions (non-blocking)",
        { error: String(error) },
        { sessionId },
      );
      return;
    }

    // Build message
    const lines: string[] = [];
    lines.push("");
    lines.push("[fork-session-collaboration-advisor]");
    lines.push("");
    lines.push("🔀 **あなたはfork-sessionです**");
    lines.push("");
    lines.push("**禁止事項**:");
    lines.push("- ❌ 「他のIssueはfork-sessionに任せます」という発言");
    lines.push("- ❌ 親セッションが作業中のIssueへの着手");
    lines.push("- ❌ 自分が親セッションであるかのような振る舞い");
    lines.push("");

    // Show ancestor worktrees
    const ancestorWorktrees = activeSessions.ancestor;
    if (ancestorWorktrees.length > 0) {
      lines.push("## 親セッションの作業中Issue");
      for (const info of ancestorWorktrees) {
        lines.push(formatWorktreeInfo(info));
      }
      lines.push("");
    }

    // Show sibling worktrees (potential conflicts)
    const siblingWorktrees = activeSessions.sibling;
    if (siblingWorktrees.length > 0) {
      lines.push("## sibling forkセッション (競合注意)");
      for (const info of siblingWorktrees) {
        lines.push(formatWorktreeInfo(info));
      }
      lines.push("");
    }

    // Combine all active worktrees for suggestion
    const allActive = [...ancestorWorktrees, ...siblingWorktrees];

    // Suggest independent issues
    let suggestedIssues: Issue[] = [];
    try {
      suggestedIssues = suggestIndependentIssues(allActive);
    } catch {
      // Fail silently
    }

    if (suggestedIssues.length > 0) {
      lines.push("## 独立したIssue候補 (着手推奨)");
      suggestedIssues.forEach((issue, index) => {
        lines.push(formatIssueSuggestion(issue, index + 1));
      });
      lines.push("");
      lines.push("上記のいずれかに着手しますか？番号で指定、または別のIssueを指定してください。");
    } else if (ancestorWorktrees.length === 0 && siblingWorktrees.length === 0) {
      // No active worktrees - nothing to report
      return;
    } else {
      lines.push("## 独立したIssue候補");
      lines.push("  現在、PRのないオープンIssueはありません。");
      lines.push("");
      lines.push("新しいIssueを作成するか、既存PRのレビューを手伝ってください。");
    }

    // Output as systemMessage
    if (lines.length > 3) {
      // Only output if we have meaningful content
      const output = {
        hookSpecificOutput: {
          hookEventName: "SessionStart",
          systemMessage: lines.join("\n"),
        },
      };
      console.log(JSON.stringify(output, null, 0));
    }
  } catch (error) {
    // Fail silently - don't block session start
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }
}

if (import.meta.main) {
  main();
}
