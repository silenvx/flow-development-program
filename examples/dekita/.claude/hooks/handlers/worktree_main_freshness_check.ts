#!/usr/bin/env bun
/**
 * worktree作成前にmainブランチが最新か確認。
 *
 * Why:
 *   PRマージ後にローカルmainをpullし忘れると、古いコードをベースに
 *   worktreeが作成される。事前チェックで最新化を強制する。
 *
 * What:
 *   - git worktree add ... main コマンド実行前（PreToolUse:Bash）に発火
 *   - worktree内からのworktree作成を検出してブロック（ネスト防止）
 *   - origin/mainをfetchしてローカルmainと比較
 *   - 遅れている場合は自動pull、失敗時はブロック
 *
 * Remarks:
 *   - ブロック型フック（mainが古い場合はブロック）
 *   - git -Cオプションをサポート（worktree内からメインリポジトリ指定可）
 *   - 自動pullを試行、失敗時のみ手動対応を要求
 *
 * Changelog:
 *   - silenvx/dekita#755: フック追加
 *   - silenvx/dekita#822: ネストworktree防止追加
 *   - silenvx/dekita#845: 自動pull機能追加
 *   - silenvx/dekita#1398: --no-rebase追加
 *   - silenvx/dekita#1405: git -Cオプションサポート
 *   - silenvx/dekita#2826: extract_git_c_directoryをlib/cwd.pyに統合
 *   - silenvx/dekita#3161: TypeScript移行
 *   - silenvx/dekita#3522: Python版から完全移行（export追加、import.meta.main、テスト追加）
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { CONTINUATION_HINT, TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { extractGitCOption } from "../lib/cwd";
import { formatError } from "../lib/format_error";
import { getDefaultBranch } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { type SpawnResult, asyncSpawn } from "../lib/spawn";

const HOOK_NAME = "worktree-main-freshness-check";

// =============================================================================
// RELATED FUNCTIONS: git_c_directory parameter
// =============================================================================
// The following functions all accept gitCDirectory parameter to support
// running git commands in a different directory (git -C option).
// When modifying one of these functions, consider if the same change is
// needed in the others:
//
// - isCwdInsideWorktree() - Checks if directory is inside worktree
// - fetchOriginDefaultBranch() - Fetches origin default branch
// - getCommitHash() - Gets commit hash for a ref
// - getBehindCount() - Gets how many commits behind
// - getCurrentBranch() - Gets current branch name
// - tryAutoPullDefaultBranch() - Tries to auto-update default branch
//
// Note: extractGitCOption() (from lib/cwd) is used to *extract* the -C value
// from a command string. Issue #2826: Consolidated with lib/cwd.ts's version.
//
// Issue #1306: This comment was added to prevent related function changes
// from being overlooked when modifying one function.
// =============================================================================

/**
 * Check if current working directory (or -C directory) is inside a git worktree.
 *
 * Issue #822: Prevent creating nested worktrees.
 * Issue #1405: Support git -C option to check a specific directory.
 *
 * @param gitCDirectory - Optional directory to check instead of cwd.
 * @returns Tuple of [isInsideWorktree, mainRepoPath].
 */
export async function isCwdInsideWorktree(
  gitCDirectory?: string | null,
): Promise<[boolean, string | null]> {
  // First, find the repository root using git
  try {
    const args = gitCDirectory
      ? ["-C", gitCDirectory, "rev-parse", "--show-toplevel"]
      : ["rev-parse", "--show-toplevel"];

    const result = await asyncSpawn("git", args, { timeout: TIMEOUT_LIGHT * 1000 });

    if (!result.success) {
      return [false, null];
    }

    const repoRoot = result.stdout.trim();
    const gitPath = join(repoRoot, ".git");

    // In a worktree, .git is a file (not a directory) containing:
    // gitdir: /path/to/main/.git/worktrees/xxx
    if (!existsSync(gitPath)) {
      return [false, null];
    }

    // Try to read as file (worktree) vs directory (main repo)
    try {
      // Issue #3263: Use statSync to check if .git is a file (worktree) or directory (main repo)
      // Bun.file().exists() returns true for directories, which would cause readFileSync to fail
      const gitStat = statSync(gitPath);
      if (!gitStat.isFile()) {
        return [false, null]; // It's a directory (main repo)
      }

      const content = readFileSync(gitPath, "utf-8").trim();
      if (content.startsWith("gitdir:")) {
        // Extract main repo path from gitdir
        // Format: gitdir: /path/to/main/.git/worktrees/xxx
        // Issue #3263: Use replace instead of split - split(":", 1) returns only 1 element
        const gitdirPath = content.replace(/^gitdir:\s*/, "");

        // Handle relative paths
        let gitdir = gitdirPath;
        if (!isAbsolute(gitdir)) {
          gitdir = resolve(dirname(gitPath), gitdir);
        }

        // Go up from .git/worktrees/xxx to get main repo
        // /path/to/main/.git/worktrees/xxx -> /path/to/main
        // Issue #3161: Use lastIndexOf to handle repos under a "worktrees/" directory
        const parts = gitdir.split("/");
        const worktreesIndex = parts.lastIndexOf("worktrees");
        if (worktreesIndex > 0) {
          const mainGitDir = parts.slice(0, worktreesIndex).join("/");
          const mainRepo = dirname(mainGitDir);
          return [true, mainRepo];
        }
      }
    } catch {
      // .git is a directory (main repo) or read error
    }

    return [false, null];
  } catch {
    return [false, null];
  }
}

/**
 * Check if command is 'git worktree add' using main/master as base.
 *
 * @param command - The command string to check.
 * @param defaultBranch - The default branch name (e.g., "main", "master").
 */
export function isWorktreeAddFromDefaultBranch(command: string, defaultBranch: string): boolean {
  // Patterns to match:
  // - git worktree add .worktrees/xxx -b branch main
  // - git worktree add .worktrees/xxx main
  // - SKIP_PLAN=1 git worktree add ... main
  // Use regex to handle multiple spaces (Issue #3525 Gemini review)
  if (!/worktree\s+add/.test(command)) {
    return false;
  }

  // Check if default branch or origin/default-branch appears as base branch
  // Use regex to properly handle quoted arguments containing spaces (Issue #3525 Gemini review)
  // Pattern: match unquoted tokens, double-quoted strings, or single-quoted strings
  const parts = (command.match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g) || []).map((p) =>
    p.replace(/^['"]|['"]$/g, ""),
  );
  const originBranch = `origin/${defaultBranch}`;

  // Find the index of "add" to determine argument positions
  const addIndex = parts.findIndex((p) => p === "add");
  if (addIndex === -1) return false;

  for (let i = addIndex + 1; i < parts.length; i++) {
    const part = parts[i];
    if (part === defaultBranch || part === originBranch) {
      // Make sure it's the base branch, not part of a path
      // It should not be immediately after -b, -B, or --branch (which would be the new branch name)
      if (i > 0 && ["-b", "-B", "--branch"].includes(parts[i - 1])) {
        continue; // This is the new branch name, not base
      }
      // Note: This heuristic may still trigger false positives if the directory path
      // is exactly the same as the default branch name (e.g. "git worktree add main feature").
      // This is rare in practice and acceptable tradeoff for security.
      return true;
    }
  }
  return false;
}

/**
 * Fetch origin default branch to get latest remote state.
 *
 * @param gitCDirectory - Optional directory to run git command in (git -C).
 * @param defaultBranch - The default branch name (e.g., "main", "master").
 */
export async function fetchOriginDefaultBranch(
  gitCDirectory: string | null | undefined,
  defaultBranch: string,
): Promise<boolean> {
  try {
    const args = gitCDirectory
      ? ["-C", gitCDirectory, "fetch", "origin", defaultBranch]
      : ["fetch", "origin", defaultBranch];

    const result = await asyncSpawn("git", args, { timeout: TIMEOUT_MEDIUM * 1000 });
    return result.success;
  } catch {
    return false;
  }
}

/**
 * Get commit hash for a ref.
 *
 * @param ref - Git reference (branch, tag, commit hash).
 * @param gitCDirectory - Optional directory to run git command in (git -C).
 */
export async function getCommitHash(
  ref: string,
  gitCDirectory?: string | null,
): Promise<string | null> {
  try {
    const args = gitCDirectory ? ["-C", gitCDirectory, "rev-parse", ref] : ["rev-parse", ref];

    const result = await asyncSpawn("git", args, { timeout: TIMEOUT_LIGHT * 1000 });
    if (result.success) {
      return result.stdout.trim();
    }
  } catch {
    // rev-parseの失敗時はnullを返し、呼び出し元でフェイルオープン処理を行う
  }
  return null;
}

/**
 * Get how many commits default branch is behind origin.
 *
 * @param gitCDirectory - Optional directory to run git command in (git -C).
 * @param defaultBranch - The default branch name (e.g., "main", "master").
 * @param originDefaultBranch - The origin default branch name (e.g., "origin/main").
 */
export async function getBehindCount(
  gitCDirectory: string | null | undefined,
  defaultBranch: string,
  originDefaultBranch: string,
): Promise<number> {
  try {
    const args = gitCDirectory
      ? ["-C", gitCDirectory, "rev-list", "--count", `${defaultBranch}..${originDefaultBranch}`]
      : ["rev-list", "--count", `${defaultBranch}..${originDefaultBranch}`];

    const result = await asyncSpawn("git", args, { timeout: TIMEOUT_LIGHT * 1000 });
    if (result.success) {
      const count = Number.parseInt(result.stdout.trim(), 10);
      if (!Number.isNaN(count)) {
        return count;
      }
    }
  } catch {
    // rev-listの失敗時はbehind数が取得できないが、フック全体をブロックしないため0扱いにする
  }
  return 0;
}

/**
 * Get the currently checked-out branch name.
 *
 * @param gitCDirectory - Optional directory to run git command in (git -C).
 * @returns Branch name, or null if not on a branch (detached HEAD) or error.
 */
export async function getCurrentBranch(gitCDirectory?: string | null): Promise<string | null> {
  try {
    const args = gitCDirectory
      ? ["-C", gitCDirectory, "rev-parse", "--abbrev-ref", "HEAD"]
      : ["rev-parse", "--abbrev-ref", "HEAD"];

    const result = await asyncSpawn("git", args, { timeout: TIMEOUT_LIGHT * 1000 });
    if (result.success) {
      const branch = result.stdout.trim();
      // "HEAD" is returned for detached HEAD state
      return branch !== "HEAD" ? branch : null;
    }
  } catch {
    // ブランチ名取得に失敗した場合は、docstring どおり null を返す
    // （フック全体をブロックしないフェイルオープン戦略）
  }
  return null;
}

/**
 * Try to update local default branch ref from origin.
 *
 * Issue #845: Automatically update main instead of blocking.
 *
 * Strategy:
 * - If on default branch: use `git pull --ff-only origin <defaultBranch>` (updates working tree)
 * - If not on default branch: use `git fetch origin <defaultBranch>:<defaultBranch>` (updates ref only)
 *
 * @param gitCDirectory - Optional directory to run git command in (git -C).
 * @param defaultBranch - The default branch name (e.g., "main", "master").
 * @returns Tuple of [success, message].
 */
export async function tryAutoPullDefaultBranch(
  gitCDirectory: string | null | undefined,
  defaultBranch: string,
): Promise<[boolean, string]> {
  try {
    const currentBranch = await getCurrentBranch(gitCDirectory);

    let result: SpawnResult;
    if (currentBranch === defaultBranch) {
      // On default branch - use pull to update both ref and working tree
      // Issue #1398: Use --no-rebase to override user's pull.rebase config
      const args = gitCDirectory
        ? ["-C", gitCDirectory, "pull", "--ff-only", "--no-rebase", "origin", defaultBranch]
        : ["pull", "--ff-only", "--no-rebase", "origin", defaultBranch];

      result = await asyncSpawn("git", args, { timeout: TIMEOUT_MEDIUM * 1000 });
    } else {
      // Not on default branch (or detached HEAD/error where currentBranch is null)
      // Use fetch with refspec to update only the ref
      // This is safe because it only updates the ref when fast-forward is possible
      const args = gitCDirectory
        ? ["-C", gitCDirectory, "fetch", "origin", `${defaultBranch}:${defaultBranch}`]
        : ["fetch", "origin", `${defaultBranch}:${defaultBranch}`];

      result = await asyncSpawn("git", args, { timeout: TIMEOUT_MEDIUM * 1000 });
    }

    if (result.success) {
      return [true, `${defaultBranch}ブランチを自動更新しました`];
    }
    // Update failed (likely due to non-fast-forward or conflicts)
    const errorMsg = result.stderr.trim() || result.stdout.trim();
    return [false, `自動更新に失敗: ${errorMsg}`];
  } catch (e) {
    if (e instanceof Error && e.message.includes("timeout")) {
      return [false, "自動更新がタイムアウトしました"];
    }
    return [false, `gitコマンドエラー: ${formatError(e)}`];
  }
}

export async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = data.tool_input ?? {};
    const command = typeof toolInput.command === "string" ? toolInput.command : "";

    // Issue #1405: Extract git -C directory from command
    // Issue #2826: Use consolidated extractGitCOption from lib.cwd
    // firstOnly=true: returns the first -C option found (either first in single
    // command or from first git command in chain)
    const gitCDir = extractGitCOption(command, true);

    // Get the default branch dynamically
    const repoPath = gitCDir || process.cwd();
    const defaultBranch = (await getDefaultBranch(repoPath)) || "main";
    const originBranch = `origin/${defaultBranch}`;

    // Only check git worktree add commands that use default branch as base
    // Log and skip silently if not a target command (no output per design principle)
    if (!isWorktreeAddFromDefaultBranch(command, defaultBranch)) {
      await logHookExecution(
        HOOK_NAME,
        "skip",
        "Not a worktree add from default branch",
        undefined,
        { sessionId },
      );
      return;
    }

    // Issue #822: Check if cwd or the directory specified by -C is inside a worktree
    // Issue #1405: If git -C points to the main repo (not a worktree), allow
    // worktree creation even from a worktree session
    const [insideWorktree, mainRepo] = await isCwdInsideWorktree(gitCDir);
    if (insideWorktree) {
      // The target directory (gitCDir if specified, otherwise cwd) is inside
      // a worktree. Block the command and suggest using git -C to main repo.
      const reason = `worktree内からworktreeを作成しようとしています。\n\nこれによりネストされたworktreeが作成され、管理が複雑になります。\n\nメインリポジトリを指定してworktreeを作成してください:\n\n\`\`\`bash\ngit -C ${mainRepo} worktree add ...\n\`\`\`${CONTINUATION_HINT}`;
      await logHookExecution(
        HOOK_NAME,
        "block",
        `Attempted to create worktree from inside worktree (cwd: ${process.cwd()}, git_c_dir: ${gitCDir})`,
        undefined,
        { sessionId },
      );
      const result = makeBlockResult(HOOK_NAME, reason);
      console.log(JSON.stringify(result));
      return;
    }

    // Fetch latest origin default branch
    // Issue #1405: Use gitCDir to fetch from the correct repository
    if (!(await fetchOriginDefaultBranch(gitCDir, defaultBranch))) {
      // Failed to fetch, approve anyway (fail open, log only)
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Failed to fetch ${originBranch}, allowing command`,
        undefined,
        { sessionId },
      );
      return;
    }

    // Compare local default branch with origin
    // Issue #1405: Use gitCDir to get commit hashes from the correct repository
    const localHash = await getCommitHash(defaultBranch, gitCDir);
    const remoteHash = await getCommitHash(originBranch, gitCDir);

    if (!localHash || !remoteHash) {
      // Can't compare, approve anyway (fail open, log only)
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "Could not get commit hashes, allowing command",
        undefined,
        { sessionId },
      );
      return;
    }

    // Check if default branch is behind origin
    // Issue #1405: Use gitCDir to check the correct repository
    const behindCount = await getBehindCount(gitCDir, defaultBranch, originBranch);

    if (behindCount === 0) {
      // default branch is up to date or ahead of origin - allow silently
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `${defaultBranch} is up to date or ahead of ${originBranch}`,
        undefined,
        { sessionId },
      );
      return;
    }

    // default branch is behind origin (behindCount > 0)
    // Issue #845: Try to auto-pull instead of blocking
    // Issue #1405: Use gitCDir to update the correct repository
    const [pullSuccess, pullMessage] = await tryAutoPullDefaultBranch(gitCDir, defaultBranch);

    if (pullSuccess) {
      // Auto-pull succeeded, approve the command
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Auto-pulled ${defaultBranch} (${behindCount} commits): ${pullMessage}`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(
        HOOK_NAME,
        `✅ ${pullMessage}（${behindCount}コミット更新）`,
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Auto-pull failed, block with helpful message
    const reason = `${defaultBranch}ブランチが古いです（${behindCount}コミット遅れ）。\n\nローカル ${defaultBranch}: ${localHash.slice(0, 8)}\nリモート ${defaultBranch}: ${remoteHash.slice(0, 8)}\n\n自動更新を試みましたが失敗しました:\n${pullMessage}\n\n手動で以下のコマンドを実行してください:\n\n\`\`\`bash\ngit pull origin ${defaultBranch}\n\`\`\`\n\nその後、再度worktreeを作成してください。${CONTINUATION_HINT}`;
    await logHookExecution(
      HOOK_NAME,
      "block",
      `${defaultBranch} is ${behindCount} commits behind, auto-pull failed: ${pullMessage}`,
      undefined,
      { sessionId },
    );
    const result = makeBlockResult(HOOK_NAME, reason);
    console.log(JSON.stringify(result));
  } catch (e) {
    // On error, approve to avoid blocking (fail open)
    const errorMsg = `Hook error: ${formatError(e)}`;
    console.error(`[${HOOK_NAME}] ${errorMsg}`);
    const result = makeApproveResult(HOOK_NAME, errorMsg);
    console.log(JSON.stringify(result));
  }
}

if (import.meta.main) {
  main().catch((e) => {
    // Log error to console for debugging, but don't expose to user via systemMessage
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
  });
}
