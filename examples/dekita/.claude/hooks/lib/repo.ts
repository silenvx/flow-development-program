/**
 * リポジトリ関連のユーティリティ関数を提供する。
 *
 * Why:
 *   worktreeのルート検出やマージ成功判定など、リポジトリ操作で
 *   共通して必要な機能を一元化する。
 *
 * What:
 *   - getRepoRoot(): worktreeを考慮したリポジトリルート取得
 *   - isMergeSuccess(): gh pr mergeの成功判定
 *
 * Remarks:
 *   - worktreeでは.gitがファイル（ディレクトリではない）
 *   - マージ成功判定は複数のエッジケースを考慮
 *   - --delete-branchオプション使用時の特殊処理あり
 *   - Python版: lib/repo.py
 *
 * Changelog:
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";

/**
 * Get project directory, detecting git root as fallback.
 */
function getProjectDir(): string {
  const envDir = process.env.CLAUDE_PROJECT_DIR;
  if (envDir) {
    return envDir;
  }

  // Fallback: find git root from current directory
  try {
    const result = execSync("git rev-parse --show-toplevel", {
      encoding: "utf-8",
      timeout: 5000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return result.trim();
  } catch {
    // Fall through to cwd fallback on any error
  }

  // Last resort: use cwd
  return process.cwd();
}

/**
 * Get the root repository directory, handling worktree case.
 *
 * If the current directory (or provided projectDir) is a worktree,
 * resolves to the parent repository root.
 *
 * Worktrees have a .git file (not directory) containing 'gitdir: ...'
 * pointing to .git/worktrees/<name> in the parent repo.
 *
 * @param projectDir - Optional path to check. If undefined, uses CLAUDE_PROJECT_DIR
 *                     environment variable or the current working directory.
 * @returns Path to repository root, or null if not a git repository.
 */
export function getRepoRoot(projectDir?: string): string | null {
  let dir = projectDir;

  if (!dir) {
    dir = getProjectDir();
  }

  if (!dir) {
    return null;
  }

  const gitPath = join(dir, ".git");

  if (!existsSync(gitPath)) {
    return null;
  }

  // If .git is a directory, this is the main repo
  try {
    const stat = statSync(gitPath);
    if (stat.isDirectory()) {
      return dir;
    }
  } catch {
    return null;
  }

  // If .git is a file, this is a worktree - parse gitdir
  try {
    const content = readFileSync(gitPath, "utf-8").trim();
    if (content.startsWith("gitdir:")) {
      const gitdir = content.slice(7).trim();
      let gitdirPath: string;

      if (isAbsolute(gitdir)) {
        gitdirPath = gitdir;
      } else {
        gitdirPath = resolve(dir, gitdir);
      }

      // gitdir points to .git/worktrees/<name>
      // Go up to .git, then up again to repo root
      // e.g., /repo/.git/worktrees/foo -> /repo/.git -> /repo
      if (gitdirPath.includes("worktrees")) {
        // Find the .git directory (parent of worktrees)
        const gitDir = dirname(dirname(gitdirPath));
        return dirname(gitDir);
      }
    }
  } catch {
    // File read error - treat as no git repository
  }

  return null;
}

/**
 * Check if gh pr merge was successful.
 *
 * Handles various edge cases:
 * - Worktree --delete-branch edge case (exit_code != 0 but success pattern)
 * - Auto-merge scheduling (returns false - not an actual merge)
 * - Squash merge with empty output (exit_code 0 is success)
 * - Combined stdout+stderr checking
 * - Branch deletion failure in worktree (merge succeeded but branch delete failed)
 *
 * @param exitCode - Command exit code (0 typically means success)
 * @param stdout - Standard output from the command
 * @param command - Original command string (optional, for edge case detection)
 * @param stderr - Standard error from the command (optional)
 * @returns True if the merge was successful, false otherwise.
 */
export function isMergeSuccess(
  exitCode: number,
  stdout: string,
  command = "",
  stderr = "",
): boolean {
  // Skip auto-merge scheduling (not an actual merge)
  if (command?.includes("--auto")) {
    return false;
  }

  // Success patterns to check in output
  const successPatterns = [
    /[Mm]erged\s+pull\s+request/,
    /Pull\s+request\s+.*\s+merged/,
    /was already merged/i,
    /Merge completed successfully/i,
  ];

  // Branch deletion failure pattern (merge succeeded but delete failed)
  const branchDeleteFailurePattern =
    /failed to delete.*branch|cannot delete.*branch|error deleting branch/i;

  const combinedOutput = stdout + stderr;

  // If exitCode is 0, check for success indicators
  if (exitCode === 0) {
    // Squash merge may produce empty output - that's still success
    if (!combinedOutput.trim()) {
      return true;
    }
    // Check for explicit success patterns
    for (const pattern of successPatterns) {
      if (pattern.test(combinedOutput)) {
        return true;
      }
    }
    // exitCode 0 with output but no success pattern - be conservative
    return false;
  }

  // Non-zero exit code - check for worktree edge case
  // In worktrees, --delete-branch may fail but merge still succeeded
  for (const pattern of successPatterns) {
    if (pattern.test(combinedOutput)) {
      return true;
    }
  }

  // Check for branch deletion failure pattern
  if (command?.includes("--delete-branch")) {
    if (branchDeleteFailurePattern.test(combinedOutput)) {
      return true;
    }
  }

  return false;
}
