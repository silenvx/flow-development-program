/**
 * locked-worktree-guard用のコマンド解析ユーティリティ。
 *
 * Why:
 *   gh prコマンドやgit worktreeコマンドを正確に解析し、
 *   ロック中worktreeへの操作を検出する必要がある。
 *
 * What:
 *   - Git worktreeコマンド検出・パス抽出
 *   - gh prコマンド解析（変更系/読み取り系の判定）
 *   - ci_monitor（TypeScript版）コマンド検出
 *
 * Remarks:
 *   - shell_tokenizer.ts: 低レベルシェルトークン化を担当
 *   - 本モジュールはコマンド固有の解析ロジックに特化
 *   - shellSplit()で引用符内の誤検知を防止
 *
 * Changelog:
 *   - silenvx/dekita#608: ci_monitor.pyコマンド検出追加
 *   - silenvx/dekita#649: --delete-branch検出追加
 *   - silenvx/dekita#3157: TypeScriptに移植
 *   - silenvx/dekita#3294: Python版削除に伴いTypeScript版検出に変更
 */

import { dirname } from "node:path";
import { extractCdTargetFromCommand } from "./cwd";
import { parseGhPrCommand } from "./github";
import {
  checkSingleGitWorktreeRemove,
  extractBaseDirFromGitSegment,
  extractCdTargetBeforeGit,
  extractRmPaths,
  isBareRedirectOperator,
  isShellRedirect,
  normalizeShellOperators,
  shellQuote,
  shellSplit,
} from "./shell_tokenizer";

// Re-export for backward compatibility
export {
  normalizeShellOperators,
  extractCdTargetBeforeGit,
  isShellRedirect,
  isBareRedirectOperator,
  checkSingleGitWorktreeRemove,
  extractBaseDirFromGitSegment,
  extractRmPaths,
};

/**
 * Check if the command modifies PR state.
 *
 * Handles global flags that may appear before 'pr':
 * - gh pr merge 123
 * - gh --repo owner/repo pr merge 123
 *
 * Read-only commands (allowed):
 * - gh pr view
 * - gh pr list
 * - gh pr checks
 * - gh pr diff
 * - gh pr status
 *
 * Modifying commands (blocked if locked):
 * - gh pr merge
 * - gh pr checkout
 * - gh pr close
 * - gh pr reopen
 * - gh pr edit
 * - gh pr comment
 * - gh pr review
 *
 * Uses shellSplit() to avoid false positives from quoted strings.
 */
export function isModifyingCommand(command: string): boolean {
  const modifyingSubcommands = new Set([
    "merge",
    "checkout",
    "close",
    "reopen",
    "edit",
    "comment",
    "review",
  ]);

  const [subcommand] = parseGhPrCommand(command);
  return subcommand !== null && modifyingSubcommands.has(subcommand);
}

/**
 * Check if gh pr merge command has --delete-branch or -d flag.
 *
 * Handles:
 * - gh pr merge 123 --delete-branch
 * - gh pr merge --delete-branch 123
 * - gh pr merge 123 -d
 * - gh pr merge -d 123
 * - gh pr merge 123 --squash --delete-branch
 * - gh pr merge --delete-branch&&echo ok (operators glued to tokens)
 */
export function hasDeleteBranchFlag(command: string): boolean {
  // Normalize shell operators (&&, ||, ;, |) to ensure proper tokenization
  const normalized = normalizeShellOperators(command);
  let tokens: string[];
  try {
    tokens = shellSplit(normalized);
  } catch {
    tokens = normalized.split(/\s+/);
  }

  // Find 'gh' and 'pr' and 'merge' tokens
  let ghStart: number | null = null;
  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i] === "gh") {
      ghStart = i;
      break;
    }
  }

  if (ghStart === null) {
    return false;
  }

  // Look for --delete-branch or -d in tokens after 'gh'
  for (const token of tokens.slice(ghStart + 1)) {
    if (["|", ";", "&&", "||"].includes(token)) {
      break;
    }
    if (token === "--delete-branch" || token === "-d") {
      return true;
    }
  }

  return false;
}

/**
 * Get the positional PR selector argument from a gh pr merge command.
 *
 * The selector may be a PR number, branch name, URL, or any other form that
 * `gh pr merge` accepts. This function does not interpret the selector; it
 * simply returns the first positional argument after `merge`.
 *
 * Extracts:
 * - gh pr merge 123 -> "123"
 * - gh pr merge feature-branch -> "feature-branch"
 * - gh pr merge https://github.com/owner/repo/pull/123 -> "https://..."
 * - gh pr merge --delete-branch -> null (no positional arg)
 * - gh pr merge --squash --delete-branch -> null (no positional arg)
 */
export function getMergePositionalArg(command: string): string | null {
  const normalized = normalizeShellOperators(command);
  let tokens: string[];
  try {
    tokens = shellSplit(normalized);
  } catch {
    tokens = normalized.split(/\s+/);
  }

  // Find 'merge' token position
  let mergeIdx: number | null = null;
  let inGhPr = false;
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (token === "gh") {
      inGhPr = true;
    } else if (inGhPr && token === "pr") {
      // gh prコマンドの追跡を継続
    } else if (inGhPr && token === "merge") {
      mergeIdx = i;
      break;
    } else if (["|", ";", "&&", "||"].includes(token)) {
      inGhPr = false;
    }
  }

  if (mergeIdx === null) {
    return null;
  }

  // Look for positional argument after 'merge'
  // Skip flags (--flag, -f) and their values
  const flagsWithArgs = new Set([
    "--body",
    "-b",
    "--body-file",
    "-F",
    "--match-head-commit",
    "--subject",
    "-t",
    "--author-email",
    "-A",
    "--repo",
    "-R",
  ]);

  let i = mergeIdx + 1;
  while (i < tokens.length) {
    const token = tokens[i];
    // Stop at command separators
    if (["|", ";", "&&", "||"].includes(token)) {
      break;
    }
    // Skip flags
    if (token.startsWith("-")) {
      if (token.includes("=")) {
        // --flag=value format
        i++;
      } else if (flagsWithArgs.has(token)) {
        // Skip flag and its value
        i += 2;
      } else {
        // Boolean flag
        i++;
      }
    } else {
      // Found a positional argument (PR number or branch name)
      return token;
    }
  }

  return null;
}

/**
 * Check if gh pr merge command has a positional argument.
 *
 * Convenience wrapper around getMergePositionalArg.
 */
export function hasMergePositionalArg(command: string): boolean {
  return getMergePositionalArg(command) !== null;
}

/**
 * Extract only the first gh pr merge command from a potentially chained command.
 *
 * This is critical for safe execution - we must NOT run any chained commands
 * (like && echo done, || rm -rf, etc.) that may have been in the original command.
 *
 * Issue #1106: Also removes shell redirects (like 2>&1) which should not be
 * passed as arguments to the gh command.
 */
export function extractFirstMergeCommand(command: string): string {
  const shellOperators = new Set(["&&", "||", ";", "|", "&"]);

  const normalized = normalizeShellOperators(command);
  let tokens: string[];
  try {
    tokens = shellSplit(normalized);
  } catch {
    tokens = normalized.split(/\s+/);
  }

  // Extract tokens up to the first shell operator
  const firstCommandTokens: string[] = [];
  for (const token of tokens) {
    if (shellOperators.has(token)) {
      break;
    }
    firstCommandTokens.push(token);
  }

  // Remove --delete-branch/-d and shell redirects
  const resultTokens: string[] = [];
  let i = 0;
  while (i < firstCommandTokens.length) {
    const token = firstCommandTokens[i];
    if (token === "--delete-branch" || token === "-d") {
      i++;
      continue;
    }
    // Issue #1106: Skip shell redirect tokens
    if (isShellRedirect(token)) {
      if (isBareRedirectOperator(token) && i + 1 < firstCommandTokens.length) {
        i += 2; // Skip both operator and target
      } else {
        i++; // Skip only the redirect
      }
      continue;
    }
    resultTokens.push(token);
    i++;
  }

  // Proper shell escaping
  return resultTokens.map(shellQuote).join(" ");
}

/**
 * Check if command is a gh pr command.
 *
 * Handles global flags that may appear before 'pr':
 * - gh pr merge 123
 * - gh --repo owner/repo pr merge 123
 * - gh -R owner/repo pr merge 123
 *
 * Uses shellSplit() to avoid false positives from quoted strings.
 */
export function isGhPrCommand(command: string): boolean {
  const [subcommand] = parseGhPrCommand(command);
  return subcommand !== null;
}

/**
 * Check if command is a ci_monitor command that operates on PRs.
 *
 * Detects:
 * - bun run .claude/scripts/ci_monitor_ts/main.ts 602
 * - ci_monitor_ts/main.ts 602 603 604 (multi-PR mode)
 * - ./scripts/ci_monitor_ts/main.ts 602
 *
 * @returns Tuple of [is_ci_monitor, pr_numbers]
 */
export function isCiMonitorCommand(command: string): [boolean, string[]] {
  let tokens: string[];
  try {
    tokens = shellSplit(command);
  } catch {
    tokens = command.split(/\s+/);
  }

  // Find ci_monitor_ts/main.ts in tokens
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (token.endsWith("ci_monitor_ts/main.ts")) {
      // Look for all PR numbers in subsequent tokens (skip flags)
      const prNumbers: string[] = [];
      for (let j = i + 1; j < tokens.length; j++) {
        const arg = tokens[j];
        if (arg.startsWith("-")) {
          continue;
        }
        // Collect all numeric arguments as PR numbers
        if (/^\d+$/.test(arg)) {
          prNumbers.push(arg);
        }
      }
      return [true, prNumbers];
    }
  }

  return [false, []];
}

/**
 * Check if command contains a git worktree remove command.
 *
 * Handles git global flags:
 * - git worktree remove path
 * - git -C /path worktree remove path
 * - git --work-tree=/path worktree remove path
 * - git --work-tree="/path with spaces" worktree remove path
 *
 * Also handles chained commands (Issue #612):
 * - git worktree unlock path && git worktree remove path
 * - cmd1 ; git worktree remove path
 * - cmd1 || git worktree remove path
 *
 * Uses shellSplit() to properly handle quoted arguments.
 */
export function isWorktreeRemoveCommand(command: string): boolean {
  const normalized = normalizeShellOperators(command);
  let tokens: string[];
  try {
    tokens = shellSplit(normalized);
  } catch {
    tokens = normalized.split(/\s+/);
  }

  // Check ALL 'git' commands in the token list
  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i] === "git") {
      if (checkSingleGitWorktreeRemove(tokens, i)) {
        return true;
      }
    }
  }

  return false;
}

/**
 * Extract base directory from git global flags if present.
 *
 * Handles:
 * - git -C /path worktree remove
 * - git -C "/path with spaces" worktree remove
 * - git --git-dir=/path/.git worktree remove
 * - git --git-dir="/path/.git" worktree remove
 * - git --work-tree=/path worktree remove
 * - git --work-tree="/path with spaces" worktree remove
 */
export function extractGitBaseDirectory(command: string): string | null {
  let tokens: string[];
  try {
    tokens = shellSplit(command);
  } catch {
    tokens = command.split(/\s+/);
  }

  // Find 'git' command position
  let gitIdx: number | null = null;
  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i] === "git") {
      gitIdx = i;
      break;
    }
  }

  if (gitIdx === null) {
    return null;
  }

  // Look for flags in tokens after 'git'
  let i = gitIdx + 1;
  while (i < tokens.length) {
    const token = tokens[i];

    // Stop at worktree command
    if (token === "worktree") {
      break;
    }

    // -C flag
    if (token === "-C") {
      if (i + 1 < tokens.length) {
        return tokens[i + 1];
      }
      break;
    }

    // --work-tree flag
    if (token.startsWith("--work-tree=")) {
      return token.slice("--work-tree=".length);
    }
    if (token === "--work-tree") {
      if (i + 1 < tokens.length) {
        return tokens[i + 1];
      }
      break;
    }

    // --git-dir flag (extract parent directory)
    if (token.startsWith("--git-dir=")) {
      const gitDir = token.slice("--git-dir=".length);
      if (gitDir.endsWith(".git")) {
        return dirname(gitDir);
      }
      return gitDir;
    }
    if (token === "--git-dir") {
      if (i + 1 < tokens.length) {
        const gitDir = tokens[i + 1];
        if (gitDir.endsWith(".git")) {
          return dirname(gitDir);
        }
        return gitDir;
      }
      break;
    }

    i++;
  }

  return null;
}

/**
 * Extract worktree path and base_dir from a single git worktree remove command.
 *
 * @returns Tuple of (worktree_path, base_dir), or (null, null) if not found.
 */
export function extractWorktreePathFromGitCommand(
  tokens: string[],
  gitIdx: number,
): [string | null, string | null] {
  if (gitIdx >= tokens.length || tokens[gitIdx] !== "git") {
    return [null, null];
  }

  // Skip global flags to find 'worktree'
  const flagsWithArgs = new Set(["-C", "--git-dir", "--work-tree", "-c"]);
  let i = gitIdx + 1;
  while (i < tokens.length) {
    const token = tokens[i];
    if (["&&", "||", ";", "|"].includes(token)) {
      return [null, null];
    }
    if (token.startsWith("-")) {
      if (token.includes("=")) {
        i++;
      } else if (flagsWithArgs.has(token)) {
        if (i + 1 < tokens.length && !["&&", "||", ";", "|"].includes(tokens[i + 1])) {
          i += 2;
        } else {
          i++;
        }
      } else {
        i++;
      }
    } else {
      break;
    }
  }

  // Check for 'worktree remove'
  if (i >= tokens.length || tokens[i] !== "worktree") {
    return [null, null];
  }
  if (i + 1 >= tokens.length || tokens[i + 1] !== "remove") {
    return [null, null];
  }

  // Find the worktree path
  let j = i + 2;
  while (j < tokens.length) {
    const token = tokens[j];
    if (["&&", "||", ";", "|"].includes(token)) {
      break;
    }
    if (token.startsWith("-")) {
      j++;
      continue;
    }
    // Found the path
    const baseDir = extractBaseDirFromGitSegment(tokens, gitIdx);
    return [token, baseDir];
  }

  return [null, null];
}

/**
 * Extract worktree path and base_dir from a single git worktree unlock command.
 *
 * This fixes Issue #700: unlock && remove pattern detection.
 */
export function extractUnlockPathFromGitCommand(
  tokens: string[],
  gitIdx: number,
): [string | null, string | null] {
  if (gitIdx >= tokens.length || tokens[gitIdx] !== "git") {
    return [null, null];
  }

  const flagsWithArgs = new Set(["-C", "--git-dir", "--work-tree", "-c"]);
  let i = gitIdx + 1;
  while (i < tokens.length) {
    const token = tokens[i];
    if (["&&", "||", ";", "|"].includes(token)) {
      return [null, null];
    }
    if (token.startsWith("-")) {
      if (token.includes("=")) {
        i++;
      } else if (flagsWithArgs.has(token)) {
        if (i + 1 < tokens.length && !["&&", "||", ";", "|"].includes(tokens[i + 1])) {
          i += 2;
        } else {
          i++;
        }
      } else {
        i++;
      }
    } else {
      break;
    }
  }

  // Check for 'worktree unlock'
  if (i >= tokens.length || tokens[i] !== "worktree") {
    return [null, null];
  }
  if (i + 1 >= tokens.length || tokens[i + 1] !== "unlock") {
    return [null, null];
  }

  // Find the worktree path
  let j = i + 2;
  while (j < tokens.length) {
    const token = tokens[j];
    if (["&&", "||", ";", "|"].includes(token)) {
      break;
    }
    if (token.startsWith("-")) {
      j++;
      continue;
    }
    const baseDir = extractBaseDirFromGitSegment(tokens, gitIdx);
    return [token, baseDir];
  }

  return [null, null];
}

/**
 * Resolve a path using base directory and hook cwd.
 */
function resolvePath(
  pathStr: string,
  baseDir: string | null,
  cdTarget: string | null,
  hookCwd: string | null,
): string {
  const { resolve, isAbsolute } = require("node:path");

  let worktreePath = pathStr;

  if (!isAbsolute(worktreePath)) {
    if (baseDir) {
      let baseDirPath = baseDir;
      if (!isAbsolute(baseDirPath)) {
        if (cdTarget) {
          let cdPath = cdTarget;
          if (!isAbsolute(cdPath) && hookCwd) {
            cdPath = resolve(hookCwd, cdPath);
          }
          baseDirPath = resolve(cdPath, baseDirPath);
        } else if (hookCwd) {
          baseDirPath = resolve(hookCwd, baseDirPath);
        }
      }
      worktreePath = resolve(baseDirPath, worktreePath);
    } else if (cdTarget) {
      let cdPath = cdTarget;
      if (!isAbsolute(cdPath) && hookCwd) {
        cdPath = resolve(hookCwd, cdPath);
      }
      worktreePath = resolve(cdPath, worktreePath);
    } else if (hookCwd) {
      worktreePath = resolve(hookCwd, worktreePath);
    } else {
      worktreePath = resolve(process.cwd(), worktreePath);
    }
  }

  try {
    const { realpathSync } = require("node:fs");
    return realpathSync(worktreePath);
  } catch {
    return worktreePath;
  }
}

/**
 * Extract resolved worktree paths from 'git worktree unlock' commands.
 *
 * @param command - The full command string (may contain chained commands).
 * @param hookCwd - Current working directory from hook input.
 * @param beforePosition - If specified, only extract unlocks before this position.
 */
export function extractUnlockTargetsFromCommand(
  command: string,
  hookCwd: string | null = null,
  beforePosition: number | null = null,
): string[] {
  const normalized = normalizeShellOperators(command);
  let tokens: string[];
  try {
    tokens = shellSplit(normalized);
  } catch {
    tokens = normalized.split(/\s+/);
  }

  const unlockPaths: string[] = [];
  let cdTarget: string | null = null;
  let i = 0;

  while (i < tokens.length) {
    const token = tokens[i];

    // Reset cd_target only for pipe
    if (token === "|") {
      cdTarget = null;
      i++;
      continue;
    }

    // Track cd command
    if (token === "cd") {
      let j = i + 1;
      while (j < tokens.length && !["&&", "||", ";", "|"].includes(tokens[j])) {
        const t = tokens[j];
        if (t.startsWith("-") && t !== "-") {
          j++;
          continue;
        }
        cdTarget = t;
        break;
      }
      i++;
      continue;
    }

    // Check for git worktree unlock
    if (token === "git") {
      // Only consider unlocks before the remove position
      if (beforePosition !== null && i >= beforePosition) {
        i++;
        continue;
      }

      // Only allow bypass when connected by &&
      if (beforePosition !== null) {
        let hasUnsafeConnector = false;
        for (let j = i; j < beforePosition; j++) {
          if (j < tokens.length && ["||", ";", "|"].includes(tokens[j])) {
            hasUnsafeConnector = true;
            break;
          }
        }
        if (hasUnsafeConnector) {
          i++;
          continue;
        }
      }

      const [pathStr, baseDir] = extractUnlockPathFromGitCommand(tokens, i);
      if (pathStr !== null) {
        const resolved = resolvePath(pathStr, baseDir, cdTarget, hookCwd);
        unlockPaths.push(resolved);
      }
    }

    i++;
  }

  return unlockPaths;
}

/**
 * Find the token position of 'git' in 'git worktree remove' command.
 *
 * @returns Token index of 'git' for the remove command, or null if not found.
 */
export function findGitWorktreeRemovePosition(command: string): number | null {
  const normalized = normalizeShellOperators(command);
  let tokens: string[];
  try {
    tokens = shellSplit(normalized);
  } catch {
    tokens = normalized.split(/\s+/);
  }

  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i] === "git") {
      const [path] = extractWorktreePathFromGitCommand(tokens, i);
      if (path !== null) {
        return i;
      }
    }
  }

  return null;
}

/**
 * Extract worktree path and base directory from git worktree remove command.
 *
 * Handles:
 * - git worktree remove path
 * - git worktree remove --force path
 * - git worktree remove -f path
 * - git worktree remove path --force
 * - git worktree remove "path with spaces"
 * - git -C /repo worktree remove path
 * - git -C "/repo with spaces" worktree remove path
 * - cd /path && git worktree remove .relative (Issue #665)
 * - cd "/path with spaces" && git worktree remove .relative
 *
 * Also handles chained commands (Issue #612):
 * - git worktree unlock path && git worktree remove path
 * - cmd1 ; git worktree remove path
 * - git -C /repo1 worktree unlock foo && git -C /repo2 worktree remove bar
 *
 * @returns Tuple of (worktree_path, base_directory) or (null, null)
 */
export function extractWorktreePathFromCommand(command: string): [string | null, string | null] {
  // Reuse extractAllWorktreePathsFromCommand and return the first result
  const allPaths = extractAllWorktreePathsFromCommand(command);
  if (allPaths.length === 0) {
    return [null, null];
  }
  return allPaths[0];
}

/**
 * Extract ALL worktree paths from git worktree remove commands in a chain.
 *
 * This function is designed to prevent bypass vulnerabilities where
 * chained commands like "git worktree remove safe && git worktree remove locked"
 * would only check the first command.
 *
 * @returns Array of tuples [(worktree_path, base_directory), ...]
 */
export function extractAllWorktreePathsFromCommand(
  command: string,
): Array<[string, string | null]> {
  const normalized = normalizeShellOperators(command);
  let tokens: string[];
  try {
    tokens = shellSplit(normalized);
  } catch {
    tokens = normalized.split(/\s+/);
  }

  const results: Array<[string, string | null]> = [];

  // Check ALL 'git' commands in the token list
  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i] === "git") {
      const [path, baseDir] = extractWorktreePathFromGitCommand(tokens, i);
      if (path !== null) {
        // If no -C flag, check for cd command before git (Issue #665)
        // Issue #3340: Use extractCdTargetFromCommand with only preceding tokens
        // to handle chained cd commands correctly
        // Re-quote tokens to preserve path integrity for paths with spaces
        let effectiveBaseDir = baseDir;
        if (baseDir === null) {
          const precedingPart = tokens.slice(0, i).map(shellQuote).join(" ");
          const cdTarget = extractCdTargetFromCommand(precedingPart);
          if (cdTarget) {
            effectiveBaseDir = cdTarget;
          }
        }
        results.push([path, effectiveBaseDir]);
      }
    }
  }

  return results;
}
