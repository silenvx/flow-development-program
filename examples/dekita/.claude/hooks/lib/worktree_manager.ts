/**
 * worktree状態管理のユーティリティモジュール。
 *
 * Why:
 *   locked-worktree-guard等の複数フックでworktree操作が必要。
 *   共通のworktree操作関数を提供することで、重複実装を避ける。
 *
 * What:
 *   - worktree一覧取得（パス、ブランチ、ロック状態）
 *   - セッション所有権チェック（SESSION_MARKER_FILE）
 *   - アクティブな作業の兆候検出（未コミット変更、最近のコミット）
 *   - rm対象のworktree検出
 *
 * Remarks:
 *   - フックではなくユーティリティモジュール
 *   - locked-worktree-guard, worktree-auto-cleanup等から使用
 *   - fail-open設計（エラー時はブロックしない）
 *
 * Changelog:
 *   - silenvx/dekita#3157: TypeScriptに移植
 *   - silenvx/dekita#3455: チルダ展開をexpandHomeに統一
 */

import { spawn } from "node:child_process";
import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import { readdirSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";

import { SESSION_MARKER_FILE, TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "./constants";
import { expandHome, extractCdTargetFromCommand, getEffectiveCwd } from "./cwd";
import { checkRecentCommits, checkUncommittedChanges } from "./git";
import { extractRmPaths } from "./shell_tokenizer";

// =============================================================================
// Helper: Run command with timeout
// =============================================================================

interface SpawnResult {
  stdout: string;
  stderr: string;
  exitCode: number | null;
}

async function runCommand(
  command: string,
  args: string[],
  options: { timeout?: number; cwd?: string } = {},
): Promise<SpawnResult> {
  const { timeout = TIMEOUT_MEDIUM, cwd } = options;

  return new Promise((resolve) => {
    const proc = spawn(command, args, {
      cwd,
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let killed = false;

    const timer = setTimeout(() => {
      killed = true;
      proc.kill("SIGTERM");
    }, timeout * 1000);

    proc.stdout?.on("data", (data) => {
      stdout += data.toString();
    });

    proc.stderr?.on("data", (data) => {
      stderr += data.toString();
    });

    proc.on("close", (exitCode) => {
      clearTimeout(timer);
      if (killed) {
        resolve({ stdout: "", stderr: "Timeout", exitCode: null });
      } else {
        resolve({ stdout, stderr, exitCode });
      }
    });

    proc.on("error", () => {
      clearTimeout(timer);
      resolve({ stdout: "", stderr: "Error", exitCode: null });
    });
  });
}

// =============================================================================
// Session Ownership
// =============================================================================

/**
 * Check if worktree was created by current session.
 *
 * Issue #1400: Allow operations on worktrees created by the same session,
 * even when cwd is not inside the worktree.
 *
 * @param worktreePath - Path to the worktree directory.
 * @param sessionId - The current session ID for comparison.
 * @returns True if the worktree was created by the current session.
 */
export function isSelfSessionWorktree(worktreePath: string, sessionId?: string | null): boolean {
  if (!sessionId) {
    return false;
  }

  const markerPath = resolve(worktreePath, SESSION_MARKER_FILE);
  try {
    if (existsSync(markerPath)) {
      const content = readFileSync(markerPath, "utf-8").trim();
      // Issue #3263: Handle both JSON format ({"session_id": "..."}) and plain text format
      if (content.startsWith("{")) {
        try {
          const data = JSON.parse(content);
          return data.session_id === sessionId;
        } catch {
          // Invalid JSON, fall through to plain text comparison
        }
      }
      return content === sessionId;
    }
  } catch {
    // File access errors are treated as "not self session" to fail-safe
  }
  return false;
}

// =============================================================================
// Worktree Information
// =============================================================================

/**
 * Get the worktree path for a given branch.
 *
 * @param branch - Branch name to look up.
 * @param baseDir - Optional directory to run git command in.
 * @returns Path to the worktree, or null if not found.
 */
export async function getWorktreeForBranch(
  branch: string,
  baseDir?: string | null,
): Promise<string | null> {
  try {
    const args = baseDir
      ? ["-C", baseDir, "worktree", "list", "--porcelain"]
      : ["worktree", "list", "--porcelain"];

    const result = await runCommand("git", args, { timeout: TIMEOUT_MEDIUM });
    if (result.exitCode !== 0) {
      return null;
    }

    let currentWorktree: string | null = null;

    for (const line of result.stdout.split("\n")) {
      if (line.startsWith("worktree ")) {
        currentWorktree = line.slice(9);
      } else if (line.startsWith("branch refs/heads/")) {
        const currentBranch = line.slice(18);
        if (currentBranch === branch && currentWorktree) {
          return currentWorktree;
        }
      }
    }
  } catch {
    // On any error, treat as "not found" to fail open
  }
  return null;
}

/**
 * Get the branch name for a PR.
 *
 * @param prNumber - PR number as string.
 * @returns Branch name, or null if not found.
 */
export async function getBranchForPr(prNumber: string): Promise<string | null> {
  try {
    const result = await runCommand(
      "gh",
      ["pr", "view", prNumber, "--json", "headRefName", "--jq", ".headRefName"],
      { timeout: TIMEOUT_MEDIUM },
    );
    if (result.exitCode === 0 && result.stdout.trim()) {
      return result.stdout.trim();
    }
  } catch {
    // On any error, treat as "not found" to fail open
  }
  return null;
}

/**
 * Get PR number for a branch.
 *
 * @param branch - Branch name.
 * @returns PR number as string, or null if no PR exists.
 */
export async function getPrForBranch(branch: string): Promise<string | null> {
  try {
    const result = await runCommand(
      "gh",
      ["pr", "list", "--head", branch, "--json", "number", "--jq", ".[0].number"],
      { timeout: TIMEOUT_MEDIUM },
    );
    if (result.exitCode === 0 && result.stdout.trim()) {
      return result.stdout.trim();
    }
  } catch {
    // Fail open: return null on error
  }
  return null;
}

/**
 * Check for signs of active work in a worktree.
 *
 * @param worktreePath - Path to the worktree.
 * @returns List of warning messages for detected active work signs.
 */
export async function checkActiveWorkSigns(worktreePath: string): Promise<string[]> {
  const warnings: string[] = [];

  const [hasRecent, recentInfo] = await checkRecentCommits(worktreePath);
  if (hasRecent) {
    warnings.push(`最新コミット（1時間以内）: ${recentInfo}`);
  }

  const [hasChanges, changeCount] = await checkUncommittedChanges(worktreePath);
  if (hasChanges) {
    if (changeCount < 0) {
      warnings.push("未コミット変更: (確認タイムアウト)");
    } else {
      warnings.push(`未コミット変更: ${changeCount}件`);
    }
  }

  return warnings;
}

/**
 * Get list of locked worktrees with their branch names.
 *
 * @returns List of tuples: [worktreePath, branchName].
 */
export async function getLockedWorktrees(): Promise<Array<[string, string]>> {
  const lockedWorktrees: Array<[string, string]> = [];

  try {
    const result = await runCommand("git", ["worktree", "list", "--porcelain"], {
      timeout: TIMEOUT_MEDIUM,
    });
    if (result.exitCode !== 0) {
      return [];
    }

    let currentWorktree: string | null = null;
    let currentBranch: string | null = null;
    let isLocked = false;

    for (const line of result.stdout.split("\n")) {
      if (line.startsWith("worktree ")) {
        // Save previous worktree if it was locked
        if (currentWorktree && isLocked && currentBranch) {
          lockedWorktrees.push([currentWorktree, currentBranch]);
        }

        currentWorktree = line.slice(9);
        currentBranch = null;
        isLocked = false;
      } else if (line.startsWith("branch refs/heads/")) {
        currentBranch = line.slice(18);
      } else if (line === "locked" || line.startsWith("locked ")) {
        isLocked = true;
      }
    }

    // Don't forget the last worktree
    if (currentWorktree && isLocked && currentBranch) {
      lockedWorktrees.push([currentWorktree, currentBranch]);
    }
  } catch {
    // Fail open: return empty list on error to avoid blocking
  }

  return lockedWorktrees;
}

/**
 * Get the current worktree path.
 *
 * @param cwd - Working directory to run git command in.
 * @returns Path to current worktree, or null if not found.
 */
export async function getCurrentWorktree(cwd?: string | null): Promise<string | null> {
  try {
    const result = await runCommand("git", ["rev-parse", "--show-toplevel"], {
      timeout: TIMEOUT_LIGHT,
      cwd: cwd ?? undefined,
    });
    if (result.exitCode === 0) {
      return result.stdout.trim() || null;
    }
  } catch {
    // Fail open: return null on error
  }
  return null;
}

/**
 * Get the current branch name.
 *
 * @param cwd - Working directory to run git command in.
 * @returns Branch name, or null if not on a branch or on error.
 */
export async function getCurrentBranchName(cwd?: string | null): Promise<string | null> {
  try {
    const result = await runCommand("git", ["rev-parse", "--abbrev-ref", "HEAD"], {
      timeout: TIMEOUT_LIGHT,
      cwd: cwd ?? undefined,
    });
    if (result.exitCode === 0 && result.stdout.trim()) {
      const branch = result.stdout.trim();
      // HEAD means detached state
      if (branch === "HEAD") {
        return null;
      }
      return branch;
    }
  } catch {
    // Any error while resolving the current branch is treated as "no branch"
  }
  return null;
}

/**
 * Get list of all locked worktree paths.
 *
 * @param baseDir - Optional directory to run git command in.
 * @returns List of locked worktree paths.
 */
export async function getAllLockedWorktreePaths(baseDir?: string | null): Promise<string[]> {
  const lockedPaths: string[] = [];

  try {
    const args = baseDir
      ? ["-C", baseDir, "worktree", "list", "--porcelain"]
      : ["worktree", "list", "--porcelain"];

    const result = await runCommand("git", args, { timeout: TIMEOUT_MEDIUM });
    if (result.exitCode !== 0) {
      return [];
    }

    let currentWorktree: string | null = null;
    let isLocked = false;

    for (const line of result.stdout.split("\n")) {
      if (line.startsWith("worktree ")) {
        // Save previous worktree if it was locked
        if (currentWorktree && isLocked) {
          lockedPaths.push(currentWorktree);
        }

        currentWorktree = line.slice(9);
        isLocked = false;
      } else if (line === "locked" || line.startsWith("locked ")) {
        isLocked = true;
      }
    }

    // Don't forget the last worktree
    if (currentWorktree && isLocked) {
      lockedPaths.push(currentWorktree);
    }
  } catch {
    // Fail open: return empty list on error to avoid blocking
  }

  return lockedPaths;
}

/**
 * Check if current working directory is inside the worktree.
 *
 * @param worktreePath - The worktree path to check against.
 * @param cwd - Current working directory hint.
 * @param command - Optional command string to check for 'cd <path> &&' pattern.
 * @returns True if cwd is inside the worktree, False otherwise.
 */
export function isCwdInsideWorktree(
  worktreePath: string,
  cwd?: string | null,
  command?: string | null,
): boolean {
  try {
    // Use getEffectiveCwd only if command contains cd pattern
    let cwdResolved: string;
    if (command && extractCdTargetFromCommand(command)) {
      cwdResolved = getEffectiveCwd(command, cwd);
    } else if (cwd) {
      cwdResolved = realpathSync(resolve(cwd));
    } else {
      cwdResolved = realpathSync(process.cwd());
    }

    const worktreeResolved = realpathSync(resolve(worktreePath));

    // Check if cwd is worktree or a subdirectory
    if (cwdResolved === worktreeResolved) {
      return true;
    }

    // Check if worktreeResolved is a parent of cwdResolved
    let current = cwdResolved;
    while (current !== dirname(current)) {
      current = dirname(current);
      if (current === worktreeResolved) {
        return true;
      }
    }

    return false;
  } catch {
    // Fail-close: If path resolution fails, assume we ARE inside the worktree
    // to prevent accidental deletion.
    return true;
  }
}

/**
 * Get the main repository directory (not worktree).
 *
 * @returns Path to main repository, or null if not found.
 */
export async function getMainRepoDir(): Promise<string | null> {
  try {
    const result = await runCommand("git", ["rev-parse", "--git-common-dir"], {
      timeout: TIMEOUT_LIGHT,
    });
    if (result.exitCode === 0) {
      const gitCommon = result.stdout.trim();
      // For regular repos: returns path to .git, parent is repo root
      // For worktrees: returns main repo's .git path, parent is main repo root
      return dirname(gitCommon);
    }
  } catch {
    // Fail open
  }
  return null;
}

/**
 * Get list of all worktree paths (including unlocked ones).
 *
 * @param baseDir - Optional directory to run git command in.
 * @returns List of worktree paths. First element is the main worktree.
 */
export async function getAllWorktreePaths(baseDir?: string | null): Promise<string[]> {
  const worktreePaths: string[] = [];

  try {
    const args = baseDir
      ? ["-C", baseDir, "worktree", "list", "--porcelain"]
      : ["worktree", "list", "--porcelain"];

    const result = await runCommand("git", args, { timeout: TIMEOUT_MEDIUM });
    if (result.exitCode !== 0) {
      return [];
    }

    for (const line of result.stdout.split("\n")) {
      if (line.startsWith("worktree ")) {
        worktreePaths.push(line.slice(9));
      }
    }
  } catch {
    // Fail open: return empty list on error to avoid blocking
  }

  return worktreePaths;
}

/**
 * Get directories in .worktrees/ that are NOT registered with git worktree list.
 *
 * These are "orphan" worktree directories that exist on the filesystem but are
 * not tracked by git.
 *
 * @returns List of orphan worktree directory paths.
 */
export async function getOrphanWorktreeDirectories(): Promise<string[]> {
  const orphanDirs: string[] = [];

  try {
    const mainRepo = await getMainRepoDir();
    if (!mainRepo) {
      return [];
    }

    const worktreesDir = resolve(mainRepo, ".worktrees");
    try {
      const stat = statSync(worktreesDir);
      if (!stat.isDirectory()) {
        return [];
      }
    } catch {
      return [];
    }

    // Get all registered worktree paths
    const registeredPaths = await getAllWorktreePaths();
    const registeredResolved = new Set<string>();
    for (const p of registeredPaths) {
      try {
        registeredResolved.add(realpathSync(p));
      } catch {
        registeredResolved.add(p);
      }
    }

    // Find directories in .worktrees/ that are NOT registered
    const entries = readdirSync(worktreesDir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory()) {
        continue;
      }

      const entryPath = resolve(worktreesDir, entry.name);
      let entryResolved: string;
      try {
        entryResolved = realpathSync(entryPath);
      } catch {
        entryResolved = entryPath;
      }

      if (!registeredResolved.has(entryResolved)) {
        orphanDirs.push(entryResolved);
      }
    }
  } catch {
    // Fail open: return empty list on error to avoid blocking
  }

  return orphanDirs;
}

/**
 * Get orphan worktree directories that would be deleted by an rm command.
 *
 * @param command - The command string.
 * @param hookCwd - Current working directory from hook input.
 * @returns List of tuples [rmTargetPath, orphanDirPath].
 */
export async function getRmTargetOrphanWorktrees(
  command: string,
  hookCwd?: string | null,
): Promise<Array<[string, string]>> {
  const paths = extractRmPaths(command);
  if (paths.length === 0) {
    return [];
  }

  const orphanDirs = await getOrphanWorktreeDirectories();
  if (orphanDirs.length === 0) {
    return [];
  }

  const targetOrphans: Array<[string, string]> = [];

  for (const pathStr of paths) {
    let pathResolved: string;
    try {
      let expandedPath = expandHome(pathStr);

      if (!isAbsolute(expandedPath)) {
        if (hookCwd) {
          expandedPath = resolve(hookCwd, expandedPath);
        } else {
          expandedPath = resolve(process.cwd(), expandedPath);
        }
      }

      pathResolved = realpathSync(expandedPath);
    } catch {
      continue;
    }

    // Check if path matches any orphan directory
    for (const orphanPath of orphanDirs) {
      // ケース1: 孤立ディレクトリ自体を削除
      if (pathResolved === orphanPath) {
        targetOrphans.push([pathResolved, orphanPath]);
      }
      // ケース2: 孤立ディレクトリを含む親ディレクトリを削除
      else if (orphanPath.startsWith(`${pathResolved}/`)) {
        targetOrphans.push([pathResolved, orphanPath]);
      }
    }
  }

  return targetOrphans;
}

/**
 * Get all worktrees that would be deleted by an rm command.
 *
 * @param command - The command string.
 * @param hookCwd - Current working directory from hook input.
 * @returns List of tuples [rmTargetPath, worktreePath].
 */
export async function getRmTargetWorktrees(
  command: string,
  hookCwd?: string | null,
): Promise<Array<[string, string]>> {
  const paths = extractRmPaths(command);

  const worktreePaths = await getAllWorktreePaths();
  // Need at least 2 worktrees: main repo (1) + at least one secondary worktree (1)
  if (worktreePaths.length < 2) {
    return [];
  }

  const targetWorktrees: Array<[string, string]> = [];

  for (const pathStr of paths) {
    let pathResolved: string;
    try {
      let expandedPath = expandHome(pathStr);

      if (!isAbsolute(expandedPath)) {
        if (hookCwd) {
          expandedPath = resolve(hookCwd, expandedPath);
        } else {
          expandedPath = resolve(process.cwd(), expandedPath);
        }
      }

      pathResolved = realpathSync(expandedPath);
    } catch {
      continue;
    }

    // Check if path matches any worktree (excluding main repo which is first)
    for (let i = 1; i < worktreePaths.length; i++) {
      const worktreePath = worktreePaths[i];
      try {
        const worktreeResolved = realpathSync(worktreePath);
        // Case 1: Deleting the worktree itself
        if (pathResolved === worktreeResolved) {
          targetWorktrees.push([pathResolved, worktreeResolved]);
        }
        // Case 2: Deleting a parent directory that contains the worktree
        else if (worktreeResolved.startsWith(`${pathResolved}/`)) {
          targetWorktrees.push([pathResolved, worktreeResolved]);
        }
      } catch {
        // worktreeパス解決失敗、スキップ
      }
    }
  }

  return targetWorktrees;
}

/**
 * Check if rm command targets a worktree directory.
 *
 * @param command - The command string.
 * @param hookCwd - Current working directory from hook input.
 * @returns Tuple of [isRmWorktree, targetPath].
 */
export async function isRmWorktreeCommand(
  command: string,
  hookCwd?: string | null,
): Promise<[boolean, string | null]> {
  const targets = await getRmTargetWorktrees(command, hookCwd);
  if (targets.length > 0) {
    return [true, targets[0][1]];
  }
  return [false, null];
}
