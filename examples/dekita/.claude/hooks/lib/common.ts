/**
 * Claude Codeフック共通のディレクトリ定数とラッパー関数。
 *
 * Why:
 *   CLAUDE_PROJECT_DIR環境変数に依存するディレクトリ定数と、
 *   デフォルト引数を提供するラッパー関数を一箇所にまとめる。
 *
 * What:
 *   - PROJECT_DIR, STATE_DIR, EXECUTION_LOG_DIR等のディレクトリ定数
 *   - getProjectDir(): worktree対応のプロジェクトディレクトリ取得
 *   - getMainRepoFromWorktree(): worktreeからメインリポジトリ解決
 *
 * Remarks:
 *   - 他のユーティリティはlib/から直接インポートすること
 *   - Issue #2505: worktree内でもメインリポジトリパスを返す
 *
 * Changelog:
 *   - silenvx/dekita#2014: re-export削除
 *   - silenvx/dekita#2505: worktreeからメインリポジトリ解決
 *   - silenvx/dekita#2509: パス検証によるセキュリティ強化
 *   - silenvx/dekita#3157: TypeScriptに移植
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";

// =============================================================================
// Directory Resolution
// =============================================================================

/**
 * Get main repository path from a worktree.
 *
 * Issue #2505: Worktrees store their git info in a `.git` file (not directory)
 * containing a path like `gitdir: /path/to/main/.git/worktrees/xxx`.
 *
 * The gitdir can be either absolute or relative (default for `git worktree add`):
 * - Absolute: `gitdir: /path/to/main/.git/worktrees/xxx`
 * - Relative: `gitdir: ../.git/worktrees/xxx`
 *
 * @param cwd - Current working directory to check.
 * @returns Path to main repository, or null if not a worktree.
 */
export function getMainRepoFromWorktree(cwd: string): string | null {
  const gitFile = join(cwd, ".git");

  // Worktrees have .git as a file, not a directory
  try {
    const stat = statSync(gitFile);
    if (!stat.isFile()) {
      return null;
    }
  } catch {
    return null;
  }

  try {
    const content = readFileSync(gitFile, "utf-8").trim();
    // Expected format: "gitdir: /path/to/main/.git/worktrees/name"
    // or relative: "gitdir: ../.git/worktrees/name"
    if (!content.startsWith("gitdir:")) {
      return null;
    }

    const gitdir = content.split(":", 2)[1].trim();
    let gitdirPath = gitdir;

    // Handle relative paths: resolve against the worktree directory
    if (!isAbsolute(gitdirPath)) {
      gitdirPath = resolve(cwd, gitdirPath);
    }

    // Navigate from ".git/worktrees/xxx" to main repo root
    // Expected structure: /main/repo/.git/worktrees/issue-123
    const parts = gitdirPath.split("/");
    const worktreesIdx = parts.lastIndexOf("worktrees");
    const gitIdx = parts.lastIndexOf(".git");

    if (worktreesIdx > 0 && gitIdx >= 0 && gitIdx === worktreesIdx - 1) {
      // Issue #2509: Validate the resolved path to prevent path traversal attacks
      // First verify that gitdirPath itself exists (prevents arbitrary path construction)
      if (!existsSync(gitdirPath)) {
        return null;
      }

      // Verify that the .git/worktrees directory actually exists on disk
      const worktreesDir = dirname(gitdirPath);
      try {
        const stat = statSync(worktreesDir);
        if (!stat.isDirectory()) {
          return null;
        }
      } catch {
        return null;
      }

      // Return parent of .git directory
      return parts.slice(0, gitIdx).join("/") || "/";
    }
  } catch {
    // Best effort - file read may fail
  }

  return null;
}

/**
 * Get project directory from environment or cwd.
 *
 * Issue #2505: When running in a worktree, returns the main repository path
 * instead of the worktree path. This ensures all session logs are stored
 * in the main repository's .claude/logs/ directory, preventing log loss
 * when worktrees are deleted.
 */
export function getProjectDir(): string {
  const envDir = process.env.CLAUDE_PROJECT_DIR;
  if (envDir) {
    // Even if CLAUDE_PROJECT_DIR is set to a worktree, resolve to main repo
    const mainRepo = getMainRepoFromWorktree(envDir);
    if (mainRepo) {
      return mainRepo;
    }
    return envDir;
  }

  const cwd = process.cwd();
  // Check if we're in a worktree and resolve to main repo
  const mainRepo = getMainRepoFromWorktree(cwd);
  if (mainRepo) {
    return mainRepo;
  }

  return cwd;
}

// =============================================================================
// Directory Constants
// =============================================================================

// Cache project directory at module load time
const _PROJECT_DIR = getProjectDir();

/**
 * Project directory (resolved from worktree if needed).
 */
export const PROJECT_DIR = _PROJECT_DIR;

/**
 * Log directory for persistent logging.
 * Stored in project-local .claude/logs/ for Claude Code to analyze later.
 */
export const LOG_DIR = join(_PROJECT_DIR, ".claude", "logs");

/**
 * Execution log directory.
 * Hook execution, git operations.
 */
export const EXECUTION_LOG_DIR = join(LOG_DIR, "execution");

/**
 * Metrics log directory.
 * PR metrics, session metrics, etc.
 */
export const METRICS_LOG_DIR = join(LOG_DIR, "metrics");

/**
 * Decisions log directory.
 * Issue decision logs (Issue #2677).
 */
export const DECISIONS_LOG_DIR = join(LOG_DIR, "decisions");

/**
 * Markers log directory.
 * Review/test completion markers (.done files).
 *
 * Marker file specification (Issue #813):
 * - Filename: Uses SANITIZED branch name (e.g., "codex-review-feat-issue-123.done")
 * - Content: Uses ORIGINAL branch name (e.g., "feat/issue-123:abc1234")
 * This is intentional: filenames must be filesystem-safe, but content preserves
 * the actual branch name for accurate identification and logging.
 */
export const MARKERS_LOG_DIR = join(LOG_DIR, "markers");

/**
 * Session-only directory for temporary state (markers, locks).
 * Cleared on reboot, which is appropriate for session-scoped data.
 */
export const SESSION_DIR = join(process.env.TMPDIR ?? tmpdir() ?? "/tmp", "claude-hooks");

/**
 * Flow progress log directory.
 * Note: Flow logs are now written to session-specific files (flow-progress-{session_id}.jsonl).
 */
export const FLOW_LOG_DIR = join(LOG_DIR, "flow");

// =============================================================================
// Re-exports for compatibility
// =============================================================================

export {
  // From constants.ts (relative path versions)
  TIMEOUT_LIGHT,
  TIMEOUT_MEDIUM,
  TIMEOUT_HEAVY,
  TIMEOUT_EXTENDED,
  TIMEOUT_LONG,
  CONTINUATION_HINT,
} from "./constants";

export { getRepoRoot } from "./repo";
