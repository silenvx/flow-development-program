/**
 * Git関連のユーティリティ関数を提供する。
 *
 * Why:
 *   ブランチ、コミット、worktree操作で共通して必要なgit操作を
 *   一元化し、各フックでの重複実装を防ぐ。
 *
 * What:
 *   - isInWorktree(): カレントディレクトリがworktree内か判定
 *   - isMainRepository(): カレントディレクトリがメインリポジトリか判定
 *   - getCurrentBranch(): 現在のブランチ名取得
 *   - getHeadCommit(): HEADコミットハッシュ取得
 *   - getDiffHash(): 差分のハッシュ取得（リベース検出用）
 *   - getDefaultBranch(): デフォルトブランチ検出
 *   - getOriginDefaultBranch(): デフォルトブランチ（origin/付き）
 *   - checkRecentCommits(): 直近コミットの有無確認
 *   - checkUncommittedChanges(): 未コミット変更の確認
 *   - extractIssueNumberFromBranch(): ブランチ名からIssue番号を抽出
 *
 * Remarks:
 *   - タイムアウトはconstants.tsの定数を使用
 *   - エラー時はnull/false/0を返すfail-open設計
 *   - worktree判定に使用される重要なモジュール
 *
 * Changelog:
 *   - silenvx/dekita#2874: TypeScriptに移植
 *   - silenvx/dekita#2894: isInWorktree/isMainRepository追加（重複解消）
 *   - silenvx/dekita#3227: extractIssueNumberFromBranch追加（重複解消）
 */

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { realpathSync } from "node:fs";
import { sep } from "node:path";
import { RECENT_COMMIT_THRESHOLD_SECONDS, TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "./constants";

// =============================================================================
// Helper Functions
// =============================================================================

interface SpawnResult {
  stdout: string;
  stderr: string;
  exitCode: number | null;
}

/**
 * Run a command with timeout support.
 *
 * @param command - Command to run.
 * @param args - Arguments for the command.
 * @param options - Options including timeout and cwd.
 * @returns Promise with stdout, stderr, and exit code.
 */
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
// Worktree Detection Functions
// =============================================================================

/**
 * Check if current directory is inside a worktree.
 *
 * Handles both Unix (/.worktrees/) and Windows (\\.worktrees\\) path separators.
 *
 * @returns true if inside a worktree directory.
 */
export function isInWorktree(): boolean {
  const cwd = process.cwd();
  return (
    cwd.includes("/.worktrees/") ||
    cwd.endsWith("/.worktrees") ||
    cwd.includes("\\.worktrees\\") ||
    cwd.endsWith("\\.worktrees")
  );
}

/**
 * Check if current directory is the main repository.
 *
 * Main repository is the first entry in `git worktree list`.
 * Uses realpath to handle symlinks correctly.
 *
 * @returns true if current directory is the main repository.
 */
export async function isMainRepository(): Promise<boolean> {
  try {
    const result = await runCommand("git", ["worktree", "list", "--porcelain"]);

    if (result.exitCode === 0) {
      const lines = result.stdout.trim().split("\n");
      if (lines.length > 0) {
        const firstLine = lines[0];
        if (firstLine.startsWith("worktree ")) {
          const mainRepoPath = firstLine.slice(9); // After "worktree "
          const cwd = process.cwd();

          // Check if cwd is inside main repository
          try {
            const realCwd = realpathSync(cwd);
            const realMain = realpathSync(mainRepoPath);
            return realCwd === realMain || realCwd.startsWith(`${realMain}${sep}`);
          } catch {
            // If realpath fails, fall back to string comparison
            return cwd === mainRepoPath || cwd.startsWith(`${mainRepoPath}${sep}`);
          }
        }
      }
    }
  } catch {
    // On error, assume not main repository (fail-open)
  }
  return false;
}

// =============================================================================
// Main Functions
// =============================================================================

/**
 * Get the current git branch name.
 *
 * @returns Branch name or null if not in a git repository or on error.
 */
export async function getCurrentBranch(): Promise<string | null> {
  try {
    const result = await runCommand("git", ["rev-parse", "--abbrev-ref", "HEAD"], {
      timeout: TIMEOUT_LIGHT,
    });
    if (result.exitCode === 0) {
      return result.stdout.trim() || null;
    }
  } catch (error) {
    // Ignore all exceptions: failure to get branch is non-fatal
    console.error("[git] Failed to get current branch:", error);
  }
  return null;
}

/**
 * Get the current HEAD commit hash (short form).
 *
 * @returns Short commit hash (7 chars) or null if not in a git repository or on error.
 */
export async function getHeadCommit(): Promise<string | null> {
  try {
    const result = await runCommand("git", ["rev-parse", "--short", "HEAD"], {
      timeout: TIMEOUT_LIGHT,
    });
    if (result.exitCode === 0) {
      return result.stdout.trim() || null;
    }
  } catch (error) {
    // Ignore all exceptions: failure to get commit is non-fatal
    console.error("[git] Failed to get HEAD commit:", error);
  }
  return null;
}

/**
 * Get the current HEAD commit hash (full form).
 *
 * @returns Full commit hash (40 chars) or null if not in a git repository or on error.
 */
export async function getHeadCommitFull(): Promise<string | null> {
  try {
    const result = await runCommand("git", ["rev-parse", "HEAD"], {
      timeout: TIMEOUT_LIGHT,
    });
    if (result.exitCode === 0) {
      return result.stdout.trim() || null;
    }
  } catch (error) {
    // Ignore all exceptions: failure to get commit is non-fatal
    console.error("[git] Failed to get HEAD commit (full):", error);
  }
  return null;
}

/**
 * Get a hash of the current diff against the base branch.
 *
 * This is used to detect if the actual code changes are the same even after
 * a rebase (which changes commit hashes but not the diff content).
 *
 * Uses streaming hash calculation to avoid buffering large diffs in memory.
 *
 * Note: git diff exit codes:
 *   - 0: no differences
 *   - 1: differences exist (normal)
 *   - >1: error (e.g., base branch doesn't exist)
 *
 * @param baseBranch - The base branch to compare against (default: "main").
 * @returns SHA-256 hash of the diff (first 12 chars) or null on error.
 */
export async function getDiffHash(baseBranch = "main"): Promise<string | null> {
  return new Promise((resolve) => {
    const proc = spawn("git", ["diff", baseBranch], {
      stdio: ["pipe", "pipe", "pipe"],
    });

    const hash = createHash("sha256");
    let killed = false;

    const timer = setTimeout(() => {
      killed = true;
      proc.kill("SIGTERM");
    }, TIMEOUT_MEDIUM * 1000);

    // Stream stdout directly to hash without buffering
    proc.stdout?.on("data", (chunk: Buffer) => {
      hash.update(chunk);
    });

    // stderrも消費しておかないと、バッファが一杯になりプロセスがハングする可能性がある
    proc.stderr?.on("data", () => {
      // バッファオーバーフロー防止のためstderrを消費
    });

    proc.on("close", (exitCode) => {
      clearTimeout(timer);
      if (killed) {
        resolve(null);
        return;
      }
      // Exit code 0 (no diff) or 1 (has diff) are both valid
      // Exit code > 1 indicates an error (e.g., base branch doesn't exist)
      if (exitCode !== null && exitCode <= 1) {
        resolve(hash.digest("hex").slice(0, 12));
      } else {
        resolve(null);
      }
    });

    proc.on("error", (error) => {
      clearTimeout(timer);
      console.error(`[git] Failed to get diff hash for base ${baseBranch}:`, error);
      resolve(null);
    });
  });
}

/**
 * Get the default branch name for the repository.
 *
 * Detection strategy:
 *   1. Try `git symbolic-ref refs/remotes/origin/HEAD` (most reliable)
 *   2. Fallback to checking if "main" branch exists
 *   3. Fallback to checking if "master" branch exists
 *
 * @param worktreePath - Path to the worktree to check.
 * @returns The default branch name (e.g., "main", "master"), or null if unable to determine.
 */
export async function getDefaultBranch(worktreePath: string): Promise<string | null> {
  try {
    // Strategy 1: Use symbolic-ref to get the default branch from origin
    const result1 = await runCommand(
      "git",
      ["-C", worktreePath, "symbolic-ref", "refs/remotes/origin/HEAD"],
      { timeout: TIMEOUT_MEDIUM },
    );
    if (result1.exitCode === 0) {
      const ref = result1.stdout.trim();
      const prefix = "refs/remotes/origin/";
      if (ref.startsWith(prefix)) {
        return ref.slice(prefix.length);
      }
    }

    // Strategy 2: Check if "main" branch exists
    const result2 = await runCommand("git", ["-C", worktreePath, "rev-parse", "--verify", "main"], {
      timeout: TIMEOUT_MEDIUM,
    });
    if (result2.exitCode === 0) {
      return "main";
    }

    // Strategy 3: Check if "master" branch exists
    const result3 = await runCommand(
      "git",
      ["-C", worktreePath, "rev-parse", "--verify", "master"],
      { timeout: TIMEOUT_MEDIUM },
    );
    if (result3.exitCode === 0) {
      return "master";
    }

    return null;
  } catch (error) {
    console.error(`[git] Failed to get default branch for ${worktreePath}:`, error);
    return null;
  }
}

/**
 * Get the default branch with "origin/" prefix.
 *
 * Useful for git commands that require the full remote reference.
 *
 * @param worktreePath - Path to the worktree to check.
 * @returns The default branch with origin prefix (e.g., "origin/main"), defaults to "origin/main" if unable to determine.
 */
export async function getOriginDefaultBranch(worktreePath: string): Promise<string> {
  const branch = await getDefaultBranch(worktreePath);
  return `origin/${branch || "main"}`;
}

/**
 * Get the number of commits since diverging from the default branch.
 *
 * @param worktreePath - Path to the worktree to check.
 * @returns Number of commits since default branch, or null if unable to determine.
 */
export async function getCommitsSinceDefaultBranch(worktreePath: string): Promise<number | null> {
  try {
    const defaultBranch = await getDefaultBranch(worktreePath);
    if (!defaultBranch) {
      return null;
    }

    const result = await runCommand(
      "git",
      ["-C", worktreePath, "rev-list", `${defaultBranch}..HEAD`, "--count"],
      { timeout: TIMEOUT_MEDIUM },
    );
    if (result.exitCode === 0) {
      const count = Number.parseInt(result.stdout.trim(), 10);
      return Number.isNaN(count) ? null : count;
    }
    return null;
  } catch (error) {
    console.error(`[git] Failed to get commits since default branch for ${worktreePath}:`, error);
    return null;
  }
}

/**
 * Check if there are recent commits (within threshold).
 *
 * Used by both locked-worktree-guard and worktree-removal-check
 * to detect active work in a worktree.
 *
 * Only considers commits made after diverging from main branch.
 * If no commits exist since main, returns false (no active work).
 *
 * @param worktreePath - Path to the worktree to check.
 * @returns Tuple of [hasRecentCommits, lastCommitInfo].
 *          On timeout/error, returns [true, "(確認タイムアウト)"] for fail-close.
 */
export async function checkRecentCommits(worktreePath: string): Promise<[boolean, string | null]> {
  try {
    // Check if there are any commits since diverging from default branch
    const divergedCount = await getCommitsSinceDefaultBranch(worktreePath);
    if (divergedCount === 0) {
      // No commits since default branch = no actual work in this worktree
      return [false, null];
    }

    // Use tab delimiter since %ar contains spaces (e.g., "5 minutes ago")
    const result = await runCommand(
      "git",
      ["-C", worktreePath, "log", "-1", "--format=%ct\t%ar\t%s"],
      { timeout: TIMEOUT_MEDIUM },
    );

    if (result.exitCode !== 0 || !result.stdout.trim()) {
      // Fail-close: gitエラー時は安全側に倒す（確認できなかった = 危険と判断）
      return [true, "(確認エラー)"];
    }

    const parts = result.stdout.trim().split("\t", 3);
    if (parts.length < 3) {
      return [false, null];
    }

    const commitTimestamp = Number.parseInt(parts[0], 10);
    const relativeTime = parts[1];
    const subject = parts[2];

    const now = Date.now() / 1000;
    const ageSeconds = now - commitTimestamp;

    if (ageSeconds < RECENT_COMMIT_THRESHOLD_SECONDS) {
      return [true, `${relativeTime}: ${subject.slice(0, 50)}`];
    }

    return [false, null];
  } catch (error) {
    // Fail-close: タイムアウト時は安全側に倒す（確認できなかった = 危険と判断）
    console.error(`[git] Failed to check recent commits for ${worktreePath}:`, error);
    return [true, "(確認タイムアウト)"];
  }
}

/**
 * Check for uncommitted changes in a worktree.
 *
 * Used by both locked-worktree-guard and worktree-removal-check
 * to detect active work in a worktree.
 *
 * @param worktreePath - Path to the worktree to check.
 * @returns Tuple of [hasChanges, changeCount].
 *          On timeout/error, returns [true, -1] for fail-close.
 *          -1 indicates a timeout occurred.
 */
export async function checkUncommittedChanges(worktreePath: string): Promise<[boolean, number]> {
  try {
    const result = await runCommand("git", ["-C", worktreePath, "status", "--porcelain"], {
      timeout: TIMEOUT_MEDIUM,
    });

    if (result.exitCode !== 0) {
      // Fail-close: gitエラー時は安全側に倒す（確認できなかった = 危険と判断）
      return [true, -1];
    }

    const lines = result.stdout
      .trim()
      .split("\n")
      .filter((line) => line.length > 0);
    return [lines.length > 0, lines.length];
  } catch (error) {
    // Fail-close: タイムアウト時は安全側に倒す
    console.error(`[git] Failed to check uncommitted changes for ${worktreePath}:`, error);
    return [true, -1]; // -1 は確認タイムアウトを示す
  }
}

// =============================================================================
// Branch Parsing Functions
// =============================================================================

/**
 * Options for extractIssueNumberFromBranch.
 */
export interface ExtractIssueNumberOptions {
  /**
   * If true, only match explicit "issue-XXX" patterns.
   * If false (default), also match generic numbered patterns like "123-feature".
   */
  strict?: boolean;
}

/**
 * Extract issue number from a branch name.
 *
 * Handles various branch naming patterns:
 *   - issue-123, issue/123, issue_123, issue123 (strict and broad mode)
 *   - 123-feature (number at start) - broad mode only
 *   - feature-123 (number at end) - broad mode only
 *   - feat/3056-add-hook (number in middle) - broad mode only
 *
 * @param branchName - The branch name to extract from.
 * @param options - Extraction options (default: { strict: false }).
 * @returns Issue number as string, or null if not found.
 */
export function extractIssueNumberFromBranch(
  branchName: string,
  options: ExtractIssueNumberOptions = {},
): string | null {
  if (!branchName) return null;

  const { strict = false } = options;

  // Strict patterns: only explicit "issue-XXX" formats
  // Uses leading word boundary (\b) to avoid matching substrings like "reissue-123"
  // Trailing boundary removed to allow patterns like "issue-123_fix" or "issue-123v2"
  const strictPatterns = [
    /\bissue[/_-]?(\d+)/i, // issue-123, issue/123, issue_123, issue123
  ];

  // Broad patterns: generic numbered branch names
  const broadPatterns = [
    /^(\d+)[/-]/, // 123-feature
    /[/-](\d+)$/, // feature-123
    /[/-](\d+)[/-]/, // feat/3056-add-hook
  ];

  const patterns = strict ? strictPatterns : [...strictPatterns, ...broadPatterns];

  for (const pattern of patterns) {
    const match = pattern.exec(branchName);
    if (match) {
      return match[1];
    }
  }

  return null;
}
