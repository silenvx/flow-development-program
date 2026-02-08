#!/usr/bin/env bun
/**
 * sibling fork-session間での同一ファイル編集を警告する。
 *
 * Why:
 *   複数のfork-sessionが同一ファイルを編集すると、マージ時に
 *   コンフリクトが発生する。事前に警告し調整を促す。
 *
 * What:
 *   - 編集対象ファイルを取得
 *   - sibling fork-sessionの変更ファイル一覧を取得
 *   - 同一ファイルを編集中のsiblingがあれば警告
 *
 * State:
 *   - reads: transcript files
 *   - reads: git worktree status
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、systemMessageで警告）
 *   - PreToolUse:Edit/Writeで発火（fork-sessionのみ）
 *   - session-worktree-statusは起動時のみ（リアルタイム警告との違い）
 *   - パス正規化でworktree間の比較を可能に
 *
 * Changelog:
 *   - silenvx/dekita#2513: フック追加
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { basename } from "node:path";
import { formatError } from "../lib/format_error";
import { getOriginDefaultBranch } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { isForkSession, parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";

const HOOK_NAME = "parallel-edit-conflict-check";

interface WorktreeInfo {
  path: string;
  issueNumber: string | null;
  changedFiles: string[];
}

interface ConflictInfo {
  issueNumber: string | null;
  path: string;
  changedFiles: string[];
  totalFiles: number;
}

/**
 * Extract target file path from tool input.
 */
function getTargetFile(toolInput: Record<string, unknown>): string | null {
  return (toolInput.file_path as string) ?? null;
}

/**
 * Normalize file path for comparison.
 *
 * Converts absolute paths to relative paths from repo root.
 * Handles both worktree paths and main repo paths.
 */
function normalizePath(filePath: string): string {
  // If path is already relative (does not start with /), return as is
  if (!filePath.startsWith("/")) {
    return filePath;
  }

  const parts = filePath.split("/").filter((p) => p);

  // Look for .worktrees pattern (handles worktree paths)
  for (let i = 0; i < parts.length; i++) {
    if (parts[i] === ".worktrees") {
      // Skip .worktrees/<name>/ prefix
      if (i + 2 < parts.length) {
        return parts.slice(i + 2).join("/");
      }
      return filePath;
    }
  }

  // For non-worktree paths, try to find a common repo root indicator
  const repoMarkers = new Set([".git", ".claude", "src", "frontend", "worker", "shared"]);
  for (let i = 0; i < parts.length; i++) {
    if (repoMarkers.has(parts[i]) && i > 0) {
      return parts.slice(i).join("/");
    }
  }

  return filePath;
}

/**
 * Get worktree list with their paths and lock status.
 * Parses lock status in a single pass to avoid N+1 queries.
 */
async function getWorktreeList(): Promise<Array<{ path: string; name: string; locked: boolean }>> {
  try {
    const result = await asyncSpawn("git", ["worktree", "list", "--porcelain"], {
      timeout: 5000,
    });
    if (!result.success) {
      return [];
    }

    const worktrees: Array<{ path: string; name: string; locked: boolean }> = [];
    let currentPath: string | null = null;
    let currentLocked = false;

    for (const line of result.stdout.split("\n")) {
      if (line.startsWith("worktree ")) {
        // Save previous worktree if exists
        if (currentPath !== null) {
          worktrees.push({
            path: currentPath,
            name: basename(currentPath),
            locked: currentLocked,
          });
        }
        currentPath = line.slice(9);
        currentLocked = false;
      } else if (line.startsWith("locked")) {
        currentLocked = true;
      }
    }

    // Don't forget the last worktree
    if (currentPath !== null) {
      worktrees.push({
        path: currentPath,
        name: basename(currentPath),
        locked: currentLocked,
      });
    }

    return worktrees;
  } catch {
    return [];
  }
}

/**
 * Execute git diff and return file list.
 * Returns empty array on error (fail silently).
 */
async function getGitDiffFiles(args: string[], cwd: string): Promise<string[]> {
  try {
    const result = await asyncSpawn("git", args, {
      timeout: 5000,
      cwd,
    });
    if (!result.success) {
      return [];
    }
    return result.stdout.trim().split("\n").filter(Boolean);
  } catch {
    return [];
  }
}

/**
 * Get changed files in a worktree compared to origin default branch.
 *
 * Combines two sources:
 * 1. `git diff --name-only origin/main...HEAD` - committed changes
 * 2. `git diff --name-only HEAD` - uncommitted changes (staged/unstaged)
 *
 * This ensures both committed and uncommitted changes are detected.
 */
export async function getWorktreeChangedFiles(worktreePath: string): Promise<string[]> {
  const originBranch = await getOriginDefaultBranch(worktreePath);
  const [committed, uncommitted] = await Promise.all([
    getGitDiffFiles(["diff", "--name-only", `${originBranch}...HEAD`], worktreePath),
    getGitDiffFiles(["diff", "--name-only", "HEAD"], worktreePath),
  ]);
  return [...new Set([...committed, ...uncommitted])];
}

/**
 * Extract issue number from worktree name.
 */
function extractIssueNumber(worktreeName: string): string | null {
  const match = worktreeName.match(/issue-(\d+)/i);
  return match ? match[1] : null;
}

/**
 * Get active worktree sessions (simplified version).
 * Uses lock status from getWorktreeList() to avoid N+1 queries.
 */
async function getActiveWorktreeSessions(
  _currentSessionId: string,
  _transcriptPath: string | null,
): Promise<{ sibling: WorktreeInfo[] }> {
  const siblings: WorktreeInfo[] = [];

  // Get current worktree path
  let currentWorktreePath: string | null = null;
  try {
    const result = await asyncSpawn("git", ["rev-parse", "--show-toplevel"], {
      timeout: 5000,
    });
    if (!result.success) {
      return { sibling: [] };
    }
    currentWorktreePath = result.stdout.trim();
  } catch {
    return { sibling: [] };
  }

  // Get all worktrees (includes lock status)
  const worktrees = await getWorktreeList();

  for (const wt of worktrees) {
    // Skip current worktree
    if (wt.path === currentWorktreePath) {
      continue;
    }

    // Skip main repo (not a worktree)
    if (!wt.path.includes(".worktrees")) {
      continue;
    }

    // Only include locked worktrees (active sessions)
    if (!wt.locked) {
      continue;
    }

    const changedFiles = await getWorktreeChangedFiles(wt.path);
    if (changedFiles.length === 0) {
      continue;
    }

    siblings.push({
      path: wt.path,
      issueNumber: extractIssueNumber(wt.name),
      changedFiles,
    });
  }

  return { sibling: siblings };
}

/**
 * Find worktrees that have the same file in their changed files.
 */
function findConflictingWorktrees(
  targetFile: string,
  activeSessions: { sibling: WorktreeInfo[] },
): ConflictInfo[] {
  const conflicts: ConflictInfo[] = [];
  const normalizedTarget = normalizePath(targetFile);

  // Check sibling worktrees for conflicts
  for (const info of activeSessions.sibling) {
    // Check if target file is in this worktree's changed files
    for (const changedFile of info.changedFiles) {
      if (normalizePath(changedFile) === normalizedTarget) {
        conflicts.push({
          issueNumber: info.issueNumber,
          path: info.path,
          changedFiles: info.changedFiles.sort().slice(0, 5),
          totalFiles: info.changedFiles.length,
        });
        break;
      }
    }
  }

  return conflicts;
}

/**
 * Format the conflict warning message.
 */
function formatWarning(targetFile: string, conflicts: ConflictInfo[]): string {
  const lines = ["⚠️ 並行編集の競合可能性:\n"];
  lines.push(`編集対象: ${normalizePath(targetFile)}\n`);
  lines.push("同一ファイルを編集中のsibling session:");

  for (const conflict of conflicts) {
    const issueStr = conflict.issueNumber
      ? `Issue #${conflict.issueNumber}`
      : (conflict.path.split("/").pop() ?? conflict.path);
    lines.push(`  - ${issueStr}`);
    lines.push(`    Worktree: ${conflict.path}`);

    const files = conflict.changedFiles;
    const totalFiles = conflict.totalFiles;
    let filesStr = files.slice(0, 3).join(", ");
    if (totalFiles > 3) {
      filesStr += ` (+${totalFiles - 3} more)`;
    }
    lines.push(`    変更中: ${filesStr}`);
    lines.push("");
  }

  lines.push("マージ時にコンフリクトが発生する可能性があります。");
  lines.push("Tip: 競合を避けるため、siblingセッションと調整するか、");
  lines.push("     別の独立したIssueに着手することを検討してください。");

  return lines.join("\n");
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };

  try {
    const hookInput = await parseHookInput();
    const sessionId = hookInput.session_id ?? "";
    const source = hookInput.source ?? "";
    const transcriptPath = hookInput.transcript_path ?? null;
    const toolInput = (hookInput.tool_input ?? {}) as Record<string, unknown>;

    // Only run for fork-sessions
    if (!isForkSession(sessionId, source, transcriptPath)) {
      await logHookExecution(HOOK_NAME, "approve", "Not a fork-session");
      console.log(JSON.stringify(result));
      return;
    }

    // Get target file
    const targetFile = getTargetFile(toolInput);
    if (!targetFile) {
      await logHookExecution(HOOK_NAME, "approve", "No target file");
      console.log(JSON.stringify(result));
      return;
    }

    // Get active worktree sessions
    let activeSessions: { sibling: WorktreeInfo[] };
    try {
      activeSessions = await getActiveWorktreeSessions(sessionId, transcriptPath);
    } catch {
      // Fail silently - don't block on errors
      await logHookExecution(HOOK_NAME, "approve", "Error getting sessions");
      console.log(JSON.stringify(result));
      return;
    }

    // Find conflicts
    const conflicts = findConflictingWorktrees(targetFile, activeSessions);

    if (conflicts.length > 0) {
      const warning = formatWarning(targetFile, conflicts);
      result.systemMessage = warning;

      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Warning: ${conflicts.length} conflicting worktree(s)`,
        { target_file: targetFile, conflicts },
      );
    } else {
      await logHookExecution(HOOK_NAME, "approve", "No conflicts");
    }
  } catch (error) {
    const errorMsg = `Hook error: ${formatError(error)}`;
    console.error(`[${HOOK_NAME}] ${errorMsg}`);
    await logHookExecution(HOOK_NAME, "approve", errorMsg);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
