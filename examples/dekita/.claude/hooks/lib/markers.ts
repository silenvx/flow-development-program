/**
 * マーカーファイル操作のユーティリティ
 *
 * Why:
 *   複数のフック（review_check, code_simplifier_check等）がマーカーファイルを
 *   読み書きするため、共通のユーティリティとして一元化する。
 *
 * What:
 *   - getMarkersDir(): マーカーディレクトリのパスを取得（同期版）
 *   - getMarkersDirAsync(): マーカーディレクトリのパスを取得（非同期版、フォールバック付き）
 *
 * Remarks:
 *   - MARKERS_LOG_DIR定数を使用
 *   - getProjectDir()でプロジェクトルートを取得
 *   - worktree内で実行された場合、メインリポジトリのマーカーディレクトリを返す
 *
 * Changelog:
 *   - silenvx/dekita#3013: 初期実装（重複コード統合）
 *   - silenvx/dekita#3020: worktree→メインリポジトリ解決ロジックを追加
 *   - silenvx/dekita#3106: 非同期版getMarkersDirAsync()を追加
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";

import { MARKERS_LOG_DIR } from "./constants";
import { getProjectDir } from "./session";

/**
 * Parse cycle count from a review marker file (Issue #3984).
 *
 * Marker format: branch:commit[:diffHash[:cycleCount]]
 * The cycleCount is the 4th colon-separated field (index 3).
 *
 * @param markerContent - Raw content of the marker file
 * @returns Cycle count (0 if not present or invalid)
 */
export function parseCycleCountFromContent(markerContent: string): number {
  const parts = markerContent.trim().split(":");
  const countPart = parts[3];
  return countPart ? Number.parseInt(countPart, 10) || 0 : 0;
}

/**
 * Parse cycle count from a review marker file path (Issue #3984).
 *
 * @param markerFile - Path to the marker file
 * @returns Cycle count (0 if file doesn't exist or can't be parsed)
 */
export function parseCycleCount(markerFile: string): number {
  try {
    if (!existsSync(markerFile)) return 0;
    return parseCycleCountFromContent(readFileSync(markerFile, "utf-8"));
  } catch {
    return 0;
  }
}

/**
 * Get main repository path from a worktree.
 *
 * Worktrees store their git info in a `.git` file (not directory)
 * containing a path like `gitdir: /path/to/main/.git/worktrees/xxx`.
 *
 * The gitdir can be either absolute or relative:
 * - Absolute: `gitdir: /path/to/main/.git/worktrees/xxx`
 * - Relative: `gitdir: ../.git/worktrees/xxx`
 *
 * @param cwd - Directory to check (must be absolute path)
 * @returns Path to main repository, or null if not a worktree
 */
function getMainRepoFromWorktree(cwd: string): string | null {
  const gitFile = `${cwd}/.git`;

  // Worktrees have .git as a file, not a directory
  try {
    const stats = statSync(gitFile);
    if (!stats.isFile()) {
      return null;
    }
  } catch {
    // File doesn't exist or can't be accessed
    return null;
  }

  try {
    const content = readFileSync(gitFile, "utf-8").trim();

    // Expected format: "gitdir: /path/to/main/.git/worktrees/name"
    // or relative: "gitdir: ../.git/worktrees/name"
    // or Windows: "gitdir: C:/path/to/main/.git/worktrees/name"
    if (!content.startsWith("gitdir:")) {
      return null;
    }

    // Use substring(7) instead of split(":") to handle Windows paths like "C:/..."
    const gitdir = content.substring(7).trim(); // 7 is length of "gitdir:"
    if (!gitdir) {
      return null;
    }

    // Handle relative paths: resolve against the worktree directory
    // path.resolve handles both Unix and Windows paths
    const gitdirPath =
      gitdir.startsWith("/") || /^[A-Za-z]:[/\\]/.test(gitdir) ? gitdir : resolve(cwd, gitdir);

    // Navigate from ".git/worktrees/xxx" to main repo root
    // Expected structure: /main/repo/.git/worktrees/issue-123
    // Use regex to split on both forward and back slashes for cross-platform support
    const parts = gitdirPath.split(/[/\\]/);
    const worktreesIdx = parts.lastIndexOf("worktrees");
    const gitIdx = parts.lastIndexOf(".git");

    // Detect the separator used in the original path for consistent path construction
    const separator = gitdirPath.includes("\\") ? "\\" : "/";

    // Validate expected structure: .git/worktrees/<worktree-name>
    // - worktreesIdx must be second-to-last element (parts.length - 2)
    //   because structure ends with worktrees/<name>
    // - worktreesIdx must immediately follow gitIdx (.git/worktrees)
    if (
      worktreesIdx === -1 ||
      gitIdx === -1 ||
      worktreesIdx !== gitIdx + 1 ||
      worktreesIdx !== parts.length - 2
    ) {
      return null;
    }

    // Security: Verify that gitdirPath itself exists
    if (!existsSync(gitdirPath)) {
      return null;
    }

    // Verify that the .git/worktrees directory actually exists on disk
    const worktreesDir = parts.slice(0, worktreesIdx + 1).join(separator);
    try {
      const worktreesStat = statSync(worktreesDir);
      if (!worktreesStat.isDirectory()) {
        return null;
      }
    } catch {
      return null;
    }

    // Return main repository path (parent of .git)
    return parts.slice(0, gitIdx).join(separator);
  } catch {
    // Best effort - file read may fail
    return null;
  }
}

/**
 * Get the markers log directory path.
 *
 * Uses MARKERS_LOG_DIR constant from constants.ts.
 * Falls back to process.cwd() if project directory is not available.
 *
 * When running in a worktree, returns the main repository's markers directory
 * to ensure consistent marker storage across worktrees and main repo.
 * This matches Python's behavior in common.py.
 *
 * @returns Full path to the markers directory.
 *
 * @example
 * getMarkersDir() // '/path/to/project/.claude/logs/markers'
 */
export function getMarkersDir(): string {
  const projectDir = getProjectDir() ?? process.cwd();

  // Check if we're in a worktree and resolve to main repo
  const mainRepo = getMainRepoFromWorktree(projectDir);
  const resolvedDir = mainRepo ?? projectDir;

  return `${resolvedDir}/${MARKERS_LOG_DIR}`;
}

/**
 * Get main repository path using `git worktree list --porcelain`.
 * Fallback when .git file parsing fails.
 */
async function getMainRepoFromWorktreeList(): Promise<string | null> {
  try {
    const proc = Bun.spawn(["git", "worktree", "list", "--porcelain"], {
      stdout: "pipe",
      stderr: "ignore",
    });
    const output = await new Response(proc.stdout).text();
    const exitCode = await proc.exited;

    if (exitCode !== 0) {
      return null;
    }

    const firstLine = output.split("\n")[0];
    if (firstLine?.startsWith("worktree ")) {
      const path = firstLine.slice(9).trim();
      if (existsSync(path)) {
        return path;
      }
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Get git repository toplevel directory.
 */
async function getGitToplevel(): Promise<string | null> {
  try {
    const proc = Bun.spawn(["git", "rev-parse", "--show-toplevel"], {
      stdout: "pipe",
      stderr: "ignore",
    });
    const output = await new Response(proc.stdout).text();
    const exitCode = await proc.exited;
    return exitCode === 0 ? output.trim() : null;
  } catch {
    return null;
  }
}

/**
 * Async version of getMarkersDir with git worktree list fallback.
 *
 * Use this version when running in scripts that may have edge cases
 * where .git file parsing fails but git commands still work.
 *
 * @returns Full path to the markers directory.
 */
export async function getMarkersDirAsync(): Promise<string> {
  // Support CLAUDE_PROJECT_DIR override
  if (process.env.CLAUDE_PROJECT_DIR) {
    return `${process.env.CLAUDE_PROJECT_DIR}/${MARKERS_LOG_DIR}`;
  }

  const toplevel = await getGitToplevel();
  if (!toplevel) {
    return `${process.cwd()}/${MARKERS_LOG_DIR}`;
  }

  // Check if we're in a worktree and resolve to main repo
  let mainRepo = getMainRepoFromWorktree(toplevel);

  // Fallback: Try git worktree list if .git file parsing fails
  if (!mainRepo) {
    try {
      const stats = statSync(`${toplevel}/.git`);
      if (stats.isFile()) {
        mainRepo = await getMainRepoFromWorktreeList();
      }
    } catch {
      mainRepo = await getMainRepoFromWorktreeList();
    }
  }

  const resolvedDir = mainRepo ?? toplevel;
  return `${resolvedDir}/${MARKERS_LOG_DIR}`;
}
