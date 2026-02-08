/**
 * GitHub CLI（gh）関連のユーティリティ関数を提供する。
 *
 * Why:
 *   gh CLIコマンドのパース・実行・結果解析を一元化し、
 *   各フックでの重複実装とバグを防ぐ。
 *
 * What:
 *   - runGhCommand(): gh CLIコマンド実行
 *   - runGhCommandWithError(): gh CLIコマンド実行（stderr付き）
 *   - parseGhPrCommand(): gh prコマンドからサブコマンド・PR番号抽出
 *   - extractPrNumber(): PR番号のみを抽出
 *   - getPrNumberForBranch(): ブランチからPR番号取得
 *   - getPrMergeStatus(): PRのマージ状態詳細取得
 *   - getObservationIssues(): observationラベル付きIssue取得
 *   - isPrMerged(): PRのマージ済み判定
 *
 * Remarks:
 *   - shlex相当のtokenizeでクォート文字列を正しく処理
 *   - heredoc/--body引数内テキストの誤検出を防止
 *   - エラー時はnull/false/空配列を返すfail-open設計
 *
 * Changelog:
 *   - silenvx/dekita#3290: runCommand関数を削除し、runGhCommandに統一
 *   - silenvx/dekita#3284: runGhCommand/runGhCommandWithErrorを統合
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { basename } from "node:path";
import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "./constants";
import { extractCdTargetFromCommand } from "./cwd";
import { shellQuote } from "./shell_tokenizer";
import { asyncSpawn } from "./spawn";

// =============================================================================
// Types
// =============================================================================

export interface PrMergeStatus {
  mergeable: boolean | null;
  mergeStateStatus: string;
  reviewDecision: string;
  statusCheckStatus: string;
  requiredApprovals: number;
  currentApprovals: number;
  /** Number of unresolved review threads (Issue #3633) */
  unresolvedThreads: number;
  /** Whether thread resolution is required by ruleset (Issue #3633) */
  requiredThreadResolution: boolean;
  /**
   * Whether pull_request rules were found (false means fallback to reviewDecision) (Issue #3633)
   * @deprecated Use pullRequestRuleFound instead (Issue #3761)
   */
  rulesetFound: boolean;
  /**
   * Whether pull_request rules were found in ruleset.
   * When false, fall back to reviewDecision for review requirements.
   * Issue #3761: Separated from status-check rules.
   */
  pullRequestRuleFound: boolean;
  /**
   * Whether required_status_checks rules were found in ruleset.
   * Issue #3761: Separated from pull_request rules.
   */
  statusCheckRuleFound: boolean;
  blockingReasons: string[];
  suggestedActions: string[];
}

export interface ObservationIssue {
  number: number;
  title: string;
  [key: string]: unknown;
}

/** Result of a gh command execution with error output */
export interface GhCommandResult {
  success: boolean;
  stdout: string;
  stderr: string;
}

// =============================================================================
// Repository Context Helpers (Issue #3396)
// =============================================================================

/**
 * API path prefix for repository endpoints.
 * The gh CLI substitutes :owner/:repo based on -R flag or current repo.
 */
export const REPO_API_PATH = "repos/:owner/:repo";

/**
 * Add -R flag to args if repo is specified.
 * Mutates the args array for efficiency.
 */
export function addRepoFlag(args: string[], repo: string | null): void {
  if (repo) {
    args.unshift("-R", repo);
  }
}

/**
 * Build gh pr view arguments with optional repository.
 */
export function buildPrViewArgs(
  prNumber: string,
  repo: string | null,
  additionalArgs: string[] = [],
): string[] {
  const args = ["pr", "view", prNumber, ...additionalArgs];
  addRepoFlag(args, repo);
  return args;
}

// =============================================================================
// gh CLI Execution (Issue #3284)
// =============================================================================

/** Default timeout for gh commands in milliseconds */
export const GH_DEFAULT_TIMEOUT_MS = 30000;

/**
 * Run a gh command and return (success, output).
 *
 * @param args - Arguments for the gh command
 * @param timeout - Command timeout in milliseconds (default: 30000)
 * @returns Tuple of [success, stdout]
 *
 * @example
 * ```ts
 * const [success, output] = await runGhCommand(["pr", "view", "123", "--json", "state"]);
 * if (success) {
 *   const data = JSON.parse(output);
 * }
 * ```
 */
export async function runGhCommand(
  args: string[],
  timeout: number = GH_DEFAULT_TIMEOUT_MS,
): Promise<[boolean, string]> {
  const result = await asyncSpawn("gh", args, { timeout });
  return [result.success, result.stdout?.trim() ?? ""];
}

/**
 * Run a gh command and return (success, stdout, stderr).
 *
 * Unlike runGhCommand, this function also returns stderr for error diagnosis.
 * Use this when you need to know why a command failed.
 *
 * @param args - Arguments for the gh command
 * @param timeout - Command timeout in milliseconds (default: 30000)
 * @returns Object with success, stdout, stderr
 *
 * @example
 * ```ts
 * const result = await runGhCommandWithError(["pr", "merge", "123"]);
 * if (!result.success) {
 *   console.error("Failed:", result.stderr);
 * }
 * ```
 */
export async function runGhCommandWithError(
  args: string[],
  timeout: number = GH_DEFAULT_TIMEOUT_MS,
): Promise<GhCommandResult> {
  const result = await asyncSpawn("gh", args, { timeout });
  return {
    success: result.success,
    stdout: result.stdout?.trim() ?? "",
    stderr: result.stderr?.trim() ?? "",
  };
}

// =============================================================================
// Command Utils (from command_utils.py)
// =============================================================================

/**
 * Common command wrappers that precede the actual command.
 */
export const COMMAND_WRAPPERS = new Set([
  "sudo",
  "time",
  "command",
  "nice",
  "nohup",
  "strace",
  "ltrace",
  "exec",
  "env",
  "doas",
  "pkexec",
  "timeout",
  "watch",
  "caffeinate", // macOS
]);

/**
 * Extract the command name from a token (handles absolute paths).
 *
 * @param token - A command token (e.g., "git", "/usr/bin/git")
 * @returns The command name without path (e.g., "git")
 */
export function getCommandName(token: string): string {
  return basename(token);
}

/**
 * Check if a token ends with a shell separator.
 *
 * @param token - A command token to check
 * @returns True if the token ends with a shell separator (;, |, &)
 */
export function endsWithShellSeparator(token: string): boolean {
  return token.endsWith(";") || token.endsWith("|") || token.endsWith("&");
}

/**
 * Check if a token is a common command wrapper.
 *
 * @param token - A command token to check
 * @returns True if the token is a known command wrapper
 */
export function isCommandWrapper(token: string): boolean {
  return COMMAND_WRAPPERS.has(token);
}

/**
 * Check if the given index is within a wrapper command context.
 * This handles cases like `env -i VAR=value gh ...` where VAR=value
 * follows wrapper options.
 *
 * @param tokens - Array of command tokens
 * @param idx - Index to check from (typically the env var or option position)
 * @returns True if we're in a wrapper option context
 */
export function isWrapperOptionContext(tokens: string[], idx: number): boolean {
  // Validate index bounds
  if (idx < 0 || idx >= tokens.length) {
    return false;
  }

  // Walk backwards from idx to find a wrapper command.
  // Allowed tokens: options (-*) or env vars (VAR=value).
  for (let i = idx; i >= 0; i--) {
    const token = tokens[i];
    const isOption = token.startsWith("-");
    // Exclude options with values like -o=foo
    const isEnvVar = token.includes("=") && !token.startsWith("-");

    // Use getCommandName to support absolute paths like /usr/bin/env
    if (!isOption && !isEnvVar && isCommandWrapper(getCommandName(token))) {
      return true;
    }

    if (!isOption && !isEnvVar) {
      return false;
    }
  }
  return false;
}

/**
 * Add spaces around shell separators for proper tokenization.
 *
 * @param command - The raw command string
 * @returns Command string with spaces around unquoted shell separators
 */
export function normalizeShellSeparators(command: string): string {
  const result: string[] = [];
  let inSingleQuote = false;
  let inDoubleQuote = false;
  let i = 0;

  while (i < command.length) {
    const char = command[i];

    // Count consecutive backslashes at end of result
    let consecutiveBackslashes = 0;
    if (!inSingleQuote) {
      for (let j = result.length - 1; j >= 0; j--) {
        if (result[j] === "\\") {
          consecutiveBackslashes++;
        } else {
          break;
        }
      }
    }
    const prevIsEscape = consecutiveBackslashes % 2 === 1;

    // Track quote state
    if (char === "'" && !inDoubleQuote && !prevIsEscape) {
      inSingleQuote = !inSingleQuote;
      result.push(char);
      i++;
    } else if (char === '"' && !inSingleQuote && !prevIsEscape) {
      inDoubleQuote = !inDoubleQuote;
      result.push(char);
      i++;
    } else if (!inSingleQuote && !inDoubleQuote) {
      // Handle multi-char operators (&&, ||, |&)
      const twoChar = command.slice(i, i + 2);
      if ((twoChar === "&&" || twoChar === "||" || twoChar === "|&") && !prevIsEscape) {
        result.push(" ");
        result.push(twoChar);
        result.push(" ");
        i += 2;
      } else {
        // Check for background operator & (not part of redirection like 2>&1, &>, <&, or pipe-all |&)
        const prevChar = i > 0 ? command[i - 1] : "";
        const nextChar = i + 1 < command.length ? command[i + 1] : "";
        const isBackgroundOp =
          char === "&" &&
          prevChar !== ">" &&
          prevChar !== "<" &&
          prevChar !== "|" &&
          nextChar !== ">";

        if ((char === ";" || char === "|" || isBackgroundOp) && !prevIsEscape) {
          result.push(" ");
          result.push(char);
          result.push(" ");
          i++;
        } else {
          result.push(char);
          i++;
        }
      }
    } else {
      result.push(char);
      i++;
    }
  }

  return result.join("");
}

/**
 * Simple shell-like tokenization that respects quotes.
 *
 * This is a simplified version of Python's shlex.split.
 *
 * @param command - The command string to tokenize
 * @returns Array of tokens
 */
export function tokenize(command: string): string[] {
  const tokens: string[] = [];
  let current = "";
  let inSingleQuote = false;
  let inDoubleQuote = false;
  let escaped = false;

  for (const char of command) {
    if (escaped) {
      current += char;
      escaped = false;
      continue;
    }

    if (char === "\\" && !inSingleQuote) {
      escaped = true;
      continue;
    }

    if (char === "'" && !inDoubleQuote) {
      inSingleQuote = !inSingleQuote;
      continue;
    }

    if (char === '"' && !inSingleQuote) {
      inDoubleQuote = !inDoubleQuote;
      continue;
    }

    if ((char === " " || char === "\t" || char === "\n") && !inSingleQuote && !inDoubleQuote) {
      if (current) {
        tokens.push(current);
        current = "";
      }
      continue;
    }

    current += char;
  }

  if (current) {
    tokens.push(current);
  }

  return tokens;
}

// =============================================================================
// Main Functions
// =============================================================================

/**
 * Validate repo format to prevent command injection.
 * Supports both owner/repo and host/owner/repo formats (e.g., github.com/owner/repo)
 *
 * @param val - Repository string to validate
 * @returns True if valid format
 */
function isValidRepoFormat(val: string): boolean {
  const parts = val.split("/");

  // Standard owner/repo format (2 parts)
  if (parts.length === 2) {
    return /^[\w.-]+$/.test(parts[0]) && /^[\w.-]+$/.test(parts[1]);
  }

  // Host/owner/repo format (3 parts)
  // Security note: asyncSpawn is used for execution, so shell injection is already prevented.
  // The [\w.-]+ pattern prevents shell metacharacters, making strict TLD validation unnecessary.
  if (parts.length === 3) {
    const [host, owner, repo] = parts;
    return /^[\w.-]+$/.test(host) && /^[\w.-]+$/.test(owner) && /^[\w.-]+$/.test(repo);
  }

  return false;
}

/**
 * Parse gh pr command to extract subcommand, PR number, and repo option.
 *
 * Uses tokenization to properly handle quoted strings, avoiding false positives
 * from text inside --body, heredocs, or other string arguments.
 *
 * @param command - The full command string
 * @returns Tuple of [subcommand, prNumber, repo] or [null, null, null] if not a gh pr command
 */
export function parseGhPrCommand(command: string): [string | null, string | null, string | null] {
  // Issue #3169: Delegate to parseAllGhPrCommands for code reuse
  // Note: We only return first 3 elements for backward compatibility (Issue #3340)
  const results = parseAllGhPrCommands(command);
  if (results.length === 0) {
    return [null, null, null];
  }
  const [subcommand, prNumber, repo] = results[0];
  return [subcommand, prNumber, repo];
}

/**
 * Extract PR number from gh pr command.
 *
 * @param command - The full command string
 * @returns PR number as string, or null if not found
 */
export function extractPrNumber(command: string): string | null {
  const [, prNumber] = parseGhPrCommand(command);
  return prNumber;
}

/**
 * Extract PR number from a GitHub PR URL.
 *
 * Issue #3345: Supports URLs like:
 * - https://github.com/owner/repo/pull/123
 * - github.com/owner/repo/pull/123
 *
 * @param url - The URL string
 * @returns PR number as string, or null if not a valid PR URL
 */
export function extractPrNumberFromUrl(url: string): string | null {
  // Match GitHub PR URL pattern: (https://)(github.com|ghe.example.com)/owner/repo/pull/123
  const match = url.match(/(?:https?:\/\/)?[^/]+\/[^/]+\/[^/]+\/pull\/(\d+)(?:\/|$|\?|#)/);
  if (match) {
    return match[1];
  }
  return null;
}

/**
 * Extract merge target (branch name or URL) from gh pr command.
 *
 * Issue #3345: When prNumber is not found, this returns the first non-flag
 * argument which could be a branch name or URL.
 *
 * @param command - The full command string
 * @returns Merge target as string, or null if not found
 */
export function extractMergeTarget(command: string): string | null {
  const results = parseAllGhPrCommands(command);
  if (results.length === 0) {
    return null;
  }
  // 5th element is mergeTarget
  return results[0][4] ?? null;
}

/**
 * Parse ALL gh pr commands from a chained command string.
 *
 * This function is designed to prevent bypass vulnerabilities where
 * chained commands like "gh pr merge A && gh pr merge B --delete-branch"
 * would only check the first command.
 *
 * @param command - The full command string (may contain chained commands)
 * @returns Array of tuples [(subcommand, prNumber, repo, cdTarget, mergeTarget, hasDeleteBranch), ...]
 *          Issue #3340: cdTarget contains the cd target for each command (preceding part only)
 *          Issue #3345: mergeTarget contains the branch name or URL when prNumber is not found
 *          Issue #3553: hasDeleteBranch indicates if --delete-branch or -d flag is present in this command's tokens
 */
export function parseAllGhPrCommands(
  command: string,
): Array<[string | null, string | null, string | null, string | null, string | null, boolean]> {
  // Normalize shell separators
  const normalized = normalizeShellSeparators(command);

  // Tokenize the command
  let tokens: string[];
  try {
    tokens = tokenize(normalized);
  } catch {
    tokens = normalized.split(/\s+/);
  }

  if (tokens.length === 0) {
    return [];
  }

  const results: Array<
    [string | null, string | null, string | null, string | null, string | null, boolean]
  > = [];
  const shellOperators = new Set(["|", ";", "&&", "||", "&"]);
  const flagsWithArgs = new Set(["--repo", "-R", "--hostname", "--config"]);
  const flagsWithoutArgs = new Set(["--help", "-h", "--version"]);
  const subcommandFlagsWithArgs = new Set([
    "--title",
    "--body",
    "-b", // short for --body (gh pr merge)
    "--body-file",
    "-F",
    "--comment",
    "--message",
    "--commit-title",
    "--commit-body",
    "--subject",
    "-t", // short for --subject (gh pr merge)
    "--author-email",
    "-A", // short for --author-email (gh pr merge)
    "--match-head-commit", // gh pr merge
    "--assignee",
    "--reviewer",
    "--label",
    "--base",
    "--head",
    "--milestone",
    "--project",
    "--reason",
    "--template",
    "--author",
    "--search",
    "--json",
    "--jq",
    "--state",
    "--limit",
    "-L",
    "--page",
    "--repo",
    "-R",
  ]);
  const allFlagsWithArgs = new Set([...flagsWithArgs, ...subcommandFlagsWithArgs]);

  // Find ALL 'gh' commands in the token list
  for (let idx = 0; idx < tokens.length; idx++) {
    if (getCommandName(tokens[idx]) !== "gh") {
      continue;
    }

    // Check if in valid command position
    let isValidPosition = false;
    if (idx === 0) {
      isValidPosition = true;
    } else {
      const prevToken = tokens[idx - 1];
      if (shellOperators.has(prevToken) || endsWithShellSeparator(prevToken)) {
        isValidPosition = true;
      } else if (prevToken.includes("=") && !prevToken.startsWith("-")) {
        // Issue #3313: prevToken が有効なコマンド位置にある場合のみ環境変数として認識
        // 有効な位置: 先頭、シェル演算子の直後、ラッパーの直後、前の環境変数の直後、
        // またはラッパーオプションのコンテキスト内（Issue #3337）
        const isValidEnvVarPosition =
          idx === 1 ||
          (idx > 1 &&
            (shellOperators.has(tokens[idx - 2]) ||
              endsWithShellSeparator(tokens[idx - 2]) ||
              (tokens[idx - 2].includes("=") && !tokens[idx - 2].startsWith("-")) ||
              isWrapperOptionContext(tokens, idx - 2)));
        if (isValidEnvVarPosition) {
          isValidPosition = true;
        }
      } else if (isWrapperOptionContext(tokens, idx - 1)) {
        // Support cases like `env -i gh pr view` (wrapper options before gh)
        isValidPosition = true;
      }
    }

    if (!isValidPosition) {
      continue;
    }

    // Extract tokens after 'gh' until we hit a separator
    const ghTokens: string[] = [];
    for (const token of tokens.slice(idx + 1)) {
      if (shellOperators.has(token) || endsWithShellSeparator(token)) {
        break;
      }
      ghTokens.push(token);
    }

    if (ghTokens.length === 0) {
      continue;
    }

    // Skip global flags to find subcommand
    let i = 0;
    while (i < ghTokens.length) {
      const token = ghTokens[i];
      if (token.startsWith("-")) {
        if (token.includes("=")) {
          i++;
        } else if (flagsWithArgs.has(token)) {
          if (i + 1 < ghTokens.length && !ghTokens[i + 1].startsWith("-")) {
            i += 2;
          } else {
            i++;
          }
        } else if (flagsWithoutArgs.has(token)) {
          i++;
        } else {
          if (
            i + 1 < ghTokens.length &&
            !ghTokens[i + 1].startsWith("-") &&
            !["pr", "issue", "repo", "auth", "api"].includes(ghTokens[i + 1])
          ) {
            i += 2;
          } else {
            i++;
          }
        }
      } else {
        break;
      }
    }

    // Check if this is a 'pr' command
    if (i >= ghTokens.length || ghTokens[i] !== "pr" || i + 1 >= ghTokens.length) {
      continue;
    }

    const subcommand = ghTokens[i + 1];
    const prFlagsStart = i + 2;

    // Find PR number (first numeric argument after subcommand)
    // Issue #3345: Also track mergeTarget (branch name or URL) when prNumber is not found
    let prNumber: string | null = null;
    let mergeTarget: string | null = null;
    let j = prFlagsStart;
    while (j < ghTokens.length) {
      const token = ghTokens[j];
      if (token.startsWith("-")) {
        if (token.includes("=")) {
          j++;
          continue;
        }
        if (allFlagsWithArgs.has(token)) {
          if (j + 1 < ghTokens.length && !ghTokens[j + 1].startsWith("-")) {
            j += 2;
          } else {
            j++;
          }
        } else {
          j++;
        }
        continue;
      }
      if (/^\d+$/.test(token)) {
        prNumber = token;
        break;
      }
      if (token.startsWith("#") && /^\d+$/.test(token.slice(1))) {
        prNumber = token.slice(1);
        break;
      }
      // Issue #3345: Record first non-flag, non-numeric argument as mergeTarget
      // This captures branch names or URLs
      if (mergeTarget === null) {
        mergeTarget = token;
      }
      j++;
    }

    // Extract --repo / -R option
    let repo: string | null = null;
    for (let k = 0; k < ghTokens.length; k++) {
      const token = ghTokens[k];
      if (token.startsWith("--repo=")) {
        const val = token.slice("--repo=".length);
        if (isValidRepoFormat(val)) repo = val;
        continue;
      }
      if (token.startsWith("-R=")) {
        const val = token.slice("-R=".length);
        if (isValidRepoFormat(val)) repo = val;
        continue;
      }
      if (token.startsWith("-R") && token.length > 2 && token[2] !== "=") {
        const value = token.slice(2);
        if (isValidRepoFormat(value)) {
          repo = value;
        }
        continue;
      }
      if (token.startsWith("-") && allFlagsWithArgs.has(token)) {
        if (token === "--repo" || token === "-R") {
          if (k + 1 < ghTokens.length) {
            const nextToken = ghTokens[k + 1];
            if (!nextToken.startsWith("-") && isValidRepoFormat(nextToken)) {
              repo = nextToken;
            }
          }
        }
        if (k + 1 < ghTokens.length && !ghTokens[k + 1].startsWith("-")) {
          k++;
        }
      }
    }

    // Issue #3340: Extract cd target from preceding tokens only
    // Re-quote tokens to preserve path integrity for paths with spaces
    const precedingPart = tokens.slice(0, idx).map(shellQuote).join(" ");
    const cdTarget = extractCdTargetFromCommand(precedingPart);

    // Issue #3553: Detect --delete-branch or -d flag within this command's tokens
    // This prevents false positives from flags inside quoted strings or other commands
    // CRITICAL: Must skip arguments of other flags (e.g., -t "-d" should not detect -d)
    //
    // Algorithm: Track when we're expecting a flag argument vs. a new flag.
    // When we see a flag that takes an argument, mark the next token to be skipped
    // (only if the next token doesn't start with "-", matching the PR number extraction logic).
    let hasDeleteBranch = false;
    let skipNext = false; // True if the next token is an argument to be skipped
    for (let k = prFlagsStart; k < ghTokens.length; k++) {
      const token = ghTokens[k];

      // If this token should be skipped (it's an argument to the previous flag)
      if (skipNext) {
        skipNext = false;
        continue;
      }

      // Check for --delete-branch or -d flag (Gemini review: use includes for readability)
      // Note: -d is specifically --delete-branch in gh pr merge context (boolean, no argument)
      if (["--delete-branch", "-d"].includes(token)) {
        hasDeleteBranch = true;
        break;
      }

      // Check if this is a flag that takes an argument
      // If so, mark the next token to be skipped unconditionally
      // NOTE: This differs from PR number extraction logic (lines 669-674) because:
      // - For PR numbers, we need to detect numeric arguments specifically
      // - For delete-branch flag, we want to avoid false positives from quoted strings
      //   like `-t "-d"` where "-d" is the argument to -t, not the delete flag
      // The tokenizer removes quotes, so `-t "-d"` becomes ["-t", "-d"] tokens.
      // We cannot distinguish between `-t "-d"` (quoted) and `-t -d` (unquoted),
      // but unconditionally skipping is safer: if someone writes `-t --delete-branch`,
      // it's an invalid command anyway (missing -t argument), so detecting it doesn't help.
      if (token.startsWith("-") && !token.includes("=")) {
        if (allFlagsWithArgs.has(token)) {
          // This flag takes an argument - skip the next token unconditionally
          skipNext = true;
        }
      }
      // Note: --flag=value format doesn't need special handling here
      // because the value is part of the same token
    }

    // Issue #3345: Include mergeTarget as 5th element
    // Issue #3553: Include hasDeleteBranch as 6th element
    results.push([subcommand, prNumber, repo, cdTarget, mergeTarget, hasDeleteBranch]);
  }

  return results;
}

/**
 * Extract --repo / -R option value from gh pr command.
 *
 * This function extracts the repo option only from `gh pr` commands,
 * avoiding false positives from other gh commands like `gh issue list -R other/repo`.
 *
 * @param command - The full command string
 * @returns Repository in "owner/repo" format, or null if not specified or invalid
 */
export function extractRepoOption(command: string): string | null {
  const [, , repo] = parseGhPrCommand(command);
  return repo;
}

/**
 * Get PR number associated with a branch.
 *
 * @param branch - The git branch name
 * @param repo - Optional repository in owner/repo format (e.g., "owner/repo")
 * @returns PR number as string, or null if no PR found
 */
export async function getPrNumberForBranch(
  branch: string,
  repo: string | null = null,
): Promise<string | null> {
  try {
    const args = ["pr", "view", branch, "--json", "number"];
    addRepoFlag(args, repo);
    const [success, stdout] = await runGhCommand(args, 10 * 1000);
    if (success) {
      const data = JSON.parse(stdout);
      const prNum = data.number;
      return prNum ? String(prNum) : null;
    }
  } catch {
    // Silently ignore all errors - this function is used as a fallback
    // and failure is expected when no PR exists for the branch
  }
  return null;
}

/**
 * Result of ruleset requirements check.
 * When pullRequestRuleFound is false, caller should fall back to reviewDecision.
 *
 * Issue #3761: Split rulesetFound into pullRequestRuleFound and statusCheckRuleFound
 * to avoid skipping reviewDecision fallback when only status-check rules exist.
 */
export interface RulesetRequirements {
  requiredApprovals: number;
  requiredThreadResolution: boolean;
  /** Whether strict status checks are required (branch must be up to date with base) */
  strictRequiredStatusChecks: boolean;
  /**
   * Whether pull_request rules were found in ruleset.
   * When true, use requiredApprovals/requiredThreadResolution.
   * When false, fall back to reviewDecision for review requirements.
   * @deprecated Use pullRequestRuleFound instead (Issue #3761)
   */
  rulesetFound: boolean;
  /**
   * Whether pull_request rules were found in ruleset.
   * When true, use requiredApprovals/requiredThreadResolution.
   * When false, fall back to reviewDecision for review requirements.
   * Issue #3761: Separated from status-check rules to avoid false positives.
   */
  pullRequestRuleFound: boolean;
  /**
   * Whether required_status_checks rules were found in ruleset.
   * Used for strictRequiredStatusChecks diagnosis.
   * Issue #3761: Separated from pull_request rules.
   */
  statusCheckRuleFound: boolean;
}

/**
 * Get ruleset requirements for a branch (Issue #3633).
 *
 * Uses the efficient /rules/branches/:branch endpoint that returns aggregated rules
 * for the specific branch, avoiding N+1 API calls and manual pattern matching.
 *
 * Issue #3761: Split rulesetFound into pullRequestRuleFound and statusCheckRuleFound.
 * Only pullRequestRuleFound should be used to determine review requirement source.
 *
 * @param baseBranch - The target branch name (e.g., "main")
 * @returns Object with requiredApprovals, requiredThreadResolution, strictRequiredStatusChecks,
 *          pullRequestRuleFound, statusCheckRuleFound, and deprecated rulesetFound flag
 */
export async function getRulesetRequirements(baseBranch: string): Promise<RulesetRequirements> {
  const defaults: RulesetRequirements = {
    requiredApprovals: 0,
    requiredThreadResolution: false,
    strictRequiredStatusChecks: false,
    rulesetFound: false, // deprecated, kept for backward compatibility
    pullRequestRuleFound: false,
    statusCheckRuleFound: false,
  };

  try {
    // Use the dedicated endpoint to get aggregated rules for the specific branch
    // This handles all include/exclude logic and rule aggregation server-side
    // URL-encode branch name to handle branches with slashes (e.g., "release/1.0")
    const encodedBranch = encodeURIComponent(baseBranch);
    const [success, stdout] = await runGhCommand(
      ["api", `${REPO_API_PATH}/rules/branches/${encodedBranch}`],
      TIMEOUT_MEDIUM * 1000,
    );

    if (!success) {
      return defaults;
    }

    const rules = JSON.parse(stdout);
    if (!Array.isArray(rules)) {
      return defaults;
    }

    let maxRequiredApprovals = 0;
    let anyRequiredThreadResolution = false;
    let anyStrictRequiredStatusChecks = false;
    let hasPullRequestRule = false;
    let hasStatusCheckRule = false;

    for (const rule of rules) {
      if (rule.type === "pull_request") {
        hasPullRequestRule = true;
        const params = rule.parameters || {};
        const requiredApprovals = params.required_approving_review_count || 0;
        if (requiredApprovals > maxRequiredApprovals) {
          maxRequiredApprovals = requiredApprovals;
        }
        if (params.required_review_thread_resolution === true) {
          anyRequiredThreadResolution = true;
        }
      }
      // Issue #3748: Check for strict status checks requirement
      if (rule.type === "required_status_checks") {
        hasStatusCheckRule = true;
        const params = rule.parameters || {};
        if (params.strict_required_status_checks_policy === true) {
          anyStrictRequiredStatusChecks = true;
        }
      }
    }

    // Issue #3761: Return separate flags for pull_request and status_check rules
    // Only pullRequestRuleFound should be used for review requirement decisions
    // to avoid skipping reviewDecision fallback when only status-check rules exist
    return {
      requiredApprovals: maxRequiredApprovals,
      requiredThreadResolution: anyRequiredThreadResolution,
      strictRequiredStatusChecks: anyStrictRequiredStatusChecks,
      // Deprecated: kept for backward compatibility, use pullRequestRuleFound instead
      rulesetFound: hasPullRequestRule,
      pullRequestRuleFound: hasPullRequestRule,
      statusCheckRuleFound: hasStatusCheckRule,
    };
  } catch (e) {
    console.error(`Failed to get ruleset requirements for branch "${baseBranch}":`, e);
    return defaults;
  }
}

/**
 * Get detailed PR merge status for guidance messages.
 *
 * @param prNumber - The PR number as a string.
 * @returns Detailed merge status information
 */
export async function getPrMergeStatus(prNumber: string): Promise<PrMergeStatus> {
  const result: PrMergeStatus = {
    mergeable: null,
    mergeStateStatus: "UNKNOWN",
    reviewDecision: "",
    statusCheckStatus: "UNKNOWN",
    requiredApprovals: 0,
    currentApprovals: 0,
    unresolvedThreads: 0,
    requiredThreadResolution: false,
    rulesetFound: false, // deprecated
    pullRequestRuleFound: false,
    statusCheckRuleFound: false,
    blockingReasons: [],
    suggestedActions: [],
  };

  try {
    const [success, stdout] = await runGhCommand(
      [
        "pr",
        "view",
        prNumber,
        "--json",
        "mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,reviews,baseRefName,reviewThreads",
      ],
      TIMEOUT_MEDIUM * 1000,
    );

    if (!success) {
      return result;
    }

    const data = JSON.parse(stdout);
    result.mergeable = data.mergeable === "MERGEABLE";
    result.mergeStateStatus = data.mergeStateStatus || "UNKNOWN";
    result.reviewDecision = data.reviewDecision || "";
    const baseBranch = data.baseRefName || "main";

    // Count approvals (unique reviewers only)
    const reviews = data.reviews || [];
    const approvingReviewers = new Set<string>();
    for (const review of reviews) {
      if (review.state === "APPROVED" && review.author?.login) {
        approvingReviewers.add(review.author.login);
      }
    }
    result.currentApprovals = approvingReviewers.size;

    // Check CI status
    const checks = data.statusCheckRollup || [];
    if (checks.length > 0) {
      const statuses = checks.map(
        (c: { conclusion?: string; status?: string }) => c.conclusion || c.status || "",
      );
      const successStatuses = new Set(["SUCCESS", "SKIPPED"]);
      if (statuses.every((s: string) => successStatuses.has(s))) {
        result.statusCheckStatus = "SUCCESS";
      } else if (statuses.some((s: string) => s === "FAILURE" || s === "ERROR")) {
        result.statusCheckStatus = "FAILURE";
      } else {
        result.statusCheckStatus = "PENDING";
      }
    } else {
      result.statusCheckStatus = "NONE";
    }

    // Get ruleset requirements (Issue #3633, #3761)
    const rulesetReqs = await getRulesetRequirements(baseBranch);
    result.requiredApprovals = rulesetReqs.requiredApprovals;
    result.requiredThreadResolution = rulesetReqs.requiredThreadResolution;
    // Issue #3761: Use pullRequestRuleFound for review requirement decisions
    result.rulesetFound = rulesetReqs.pullRequestRuleFound; // deprecated, kept for backward compat
    result.pullRequestRuleFound = rulesetReqs.pullRequestRuleFound;
    result.statusCheckRuleFound = rulesetReqs.statusCheckRuleFound;

    // Calculate unresolved threads from the main response data (optimized to avoid extra API call)
    const threads = data.reviewThreads || [];
    result.unresolvedThreads = threads.filter(
      (thread: { isResolved?: boolean }) => thread.isResolved === false,
    ).length;

    // Determine blocking reasons and suggested actions
    const mergeState = result.mergeStateStatus;

    if (mergeState === "BEHIND") {
      result.blockingReasons.push("mainブランチより遅れています（BEHIND）");
      result.suggestedActions.push("git rebase origin/main でリベースしてください");
    }

    if (mergeState === "BLOCKED" || mergeState === "BEHIND") {
      // Check for unresolved review threads first (Issue #3633)
      // This is often the cause of BLOCKED status when requiredApprovingReviewCount is 0
      if (result.requiredThreadResolution && result.unresolvedThreads > 0) {
        result.blockingReasons.push(
          `未解決のレビュースレッドが${result.unresolvedThreads}件あります`,
        );
        result.suggestedActions.push("gh api graphql でレビュースレッドをResolveしてください");
      }

      // Check review approval requirements (Issue #3633, #3761)
      // If pull_request ruleset was found, use requiredApprovals from ruleset
      // If NOT found (classic branch protection, API failure, or status-check-only ruleset),
      // fall back to reviewDecision for review requirements
      if (rulesetReqs.pullRequestRuleFound) {
        // Ruleset found: use explicit required approval count
        if (result.requiredApprovals > 0 && result.currentApprovals < result.requiredApprovals) {
          result.blockingReasons.push(
            `レビュー承認が${result.requiredApprovals}件必要ですが、${result.currentApprovals}件です`,
          );
          result.suggestedActions.push("別のレビュアーにレビュー承認を依頼してください");
        }

        // Handle CHANGES_REQUESTED and REVIEW_REQUIRED regardless of approval count
        // These can occur even when approval count is met (e.g., Code Owners not approved)
        if (result.reviewDecision === "CHANGES_REQUESTED") {
          result.blockingReasons.push("レビュアーから変更がリクエストされています");
          result.suggestedActions.push("指摘事項を修正し、再レビューを依頼してください");
        } else if (result.reviewDecision === "REVIEW_REQUIRED") {
          // This can happen even with requiredApprovals > 0 when Code Owners haven't approved
          result.blockingReasons.push("コードオーナーなどのレビュー承認が必要です");
          result.suggestedActions.push("指定されたレビュアーにレビューを依頼してください");
        }
      } else {
        // Ruleset not found: fall back to reviewDecision for classic branch protection
        if (result.reviewDecision === "CHANGES_REQUESTED") {
          result.blockingReasons.push("レビュアーから変更がリクエストされています");
          result.suggestedActions.push("指摘事項を修正し、再レビューを依頼してください");
        } else if (result.reviewDecision === "REVIEW_REQUIRED" || result.reviewDecision === "") {
          result.blockingReasons.push("レビュー承認が必要ですが、承認されていません");
          result.suggestedActions.push("別のレビュアーにレビュー承認を依頼してください");
        }
      }

      if (result.statusCheckStatus === "FAILURE") {
        result.blockingReasons.push("CIチェックが失敗しています");
        result.suggestedActions.push(`gh pr checks ${prNumber} でCI状態を確認してください`);
      } else if (result.statusCheckStatus === "PENDING") {
        result.blockingReasons.push("CIチェックが実行中です");
        result.suggestedActions.push("CIが完了するまで待機してください");
      }

      // If BLOCKED but no specific reason found, report generic message
      if (mergeState === "BLOCKED" && result.blockingReasons.length === 0) {
        result.blockingReasons.push("マージがブロックされています（原因不明）");
        result.suggestedActions.push(
          "gh pr view でPR状態を確認し、GitHub UIでRuleset設定を確認してください",
        );
      }
    }
  } catch {
    // Return default values on error
  }

  return result;
}

/**
 * Get open issues with observation label.
 *
 * @param limit - Maximum number of issues to return. Default 50.
 * @param fields - JSON fields to retrieve. Default ["number", "title"].
 * @param timeoutSeconds - Timeout in seconds. Default uses TIMEOUT_LIGHT.
 * @returns List of observation issues as dictionaries.
 */
export async function getObservationIssues(
  limit = 50,
  fields: string[] = ["number", "title"],
  timeoutSeconds: number = TIMEOUT_LIGHT,
): Promise<ObservationIssue[]> {
  try {
    const [success, stdout] = await runGhCommand(
      [
        "issue",
        "list",
        "--label",
        "observation",
        "--state",
        "open",
        "--json",
        fields.join(","),
        "--limit",
        String(limit),
      ],
      timeoutSeconds * 1000,
    );

    if (!success) {
      return [];
    }

    return JSON.parse(stdout);
  } catch {
    return [];
  }
}

/**
 * Check if a PR is already merged.
 *
 * @param prNumber - The PR number as a string.
 * @param repo - Repository in owner/repo format, or null for current repo.
 * @returns True if the PR is merged, False otherwise (including errors).
 */
export async function isPrMerged(prNumber: string, repo: string | null = null): Promise<boolean> {
  try {
    const args = ["api", `${REPO_API_PATH}/pulls/${prNumber}`, "--jq", ".merged"];
    addRepoFlag(args, repo);
    const [success, stdout] = await runGhCommand(args, TIMEOUT_MEDIUM * 1000);
    if (success) {
      return stdout.trim().toLowerCase() === "true";
    }
  } catch {
    // On error, assume not merged to fail open
  }
  return false;
}

// =============================================================================
// PR Merge Command Detection (Issue #3674)
// =============================================================================

/**
 * Check if the command is a PR merge command.
 *
 * Uses position anchors and character classes to avoid false positives
 * like `echo "gh pr merge"`. Also handles shell separators (`;`, `&`, `|`)
 * for command chaining.
 *
 * @param command - The command string to check
 * @returns True if the command contains `gh pr merge`
 *
 * @example
 * isPrMergeCommand("gh pr merge 123")           // true
 * isPrMergeCommand("gh pr merge; git pull")     // true
 * isPrMergeCommand('echo "gh pr merge"')        // false
 */
export function isPrMergeCommand(command: string): boolean {
  return /(^|\s|;|&|\|)gh pr merge(\s|$|;|&|\|)/.test(command);
}

// =============================================================================
// Issue Extraction from PR Details (Issue #3674)
// =============================================================================

/** PR details for issue extraction */
export interface PrDetailsForIssueExtraction {
  body?: string;
  title?: string;
  headRefName?: string;
}

/**
 * Extract issue number from PR details (body, title, branch name).
 *
 * Searches for GitHub linking keywords (close(s/d), fix(es/ed), resolve(s/d))
 * in PR body and title, then falls back to branch name pattern (issue-NNN).
 *
 * @param prDetails - PR details containing body, title, and headRefName
 * @returns Issue number if found, null otherwise
 *
 * @example
 * extractIssueFromPrDetails({ body: "Closes #123" })           // 123
 * extractIssueFromPrDetails({ body: "Fixed #456" })            // 456
 * extractIssueFromPrDetails({ headRefName: "feat/issue-789" }) // 789
 * extractIssueFromPrDetails({ body: "This discloses #123" })   // null (word boundary)
 */
export function extractIssueFromPrDetails(prDetails: PrDetailsForIssueExtraction): number | null {
  const body = prDetails.body ?? "";
  const title = prDetails.title ?? "";
  const branch = prDetails.headRefName ?? "";

  // Search for issue references in body and title
  for (const text of [body, title]) {
    // Match GitHub linking keywords: close(s/d), fix(es/ed), resolve(s/d)
    // Uses word boundary to avoid false positives like "discloses #123"
    const match = text.match(/\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s+#(\d+)/i);
    if (match) {
      return Number.parseInt(match[1], 10);
    }
  }

  // Also check branch name for issue number
  // Match "issue-123", "feat/issue-123-xxx"
  // Uses word boundary to ensure exact "issue-" prefix match
  const branchMatch = branch.match(/\bissue-(\d+)\b/i);
  if (branchMatch) {
    return Number.parseInt(branchMatch[1], 10);
  }

  return null;
}

/**
 * Extract PR number from merge command or get PR for current branch.
 *
 * First tries to extract PR number from the command string using parseAllGhPrCommands,
 * focusing only on the `gh pr merge` subcommand to avoid confusion with other commands
 * (e.g., "gh pr view 123 && gh pr merge 456" should return 456).
 * If not found, falls back to `gh pr view` to get PR for current branch.
 *
 * @param command - The command string (e.g., "gh pr merge 123")
 * @returns PR number if found, null otherwise
 */
export async function extractPrNumberFromMergeCommand(command: string): Promise<number | null> {
  // Use parseAllGhPrCommands and find the last `merge` subcommand
  // This handles cases like "gh pr view 123 && gh pr merge 456"
  const allCommands = parseAllGhPrCommands(command);
  const mergeCommands = allCommands.filter(([subcommand]) => subcommand === "merge");

  // Use the last merge command (the one being executed)
  if (mergeCommands.length > 0) {
    const lastMerge = mergeCommands[mergeCommands.length - 1];
    // parseAllGhPrCommands returns: [subcommand, prNumber, repo, cdTarget, mergeTarget]
    const prStr = lastMerge[1];
    if (prStr) {
      return Number.parseInt(prStr, 10);
    }

    // Try extracting from mergeTarget (branch name or URL)
    // This handles "gh pr merge feature-branch" where no explicit PR number is given
    const repo = lastMerge[2];
    const mergeTarget = lastMerge[4];
    if (mergeTarget) {
      // Try extracting PR number from URL (e.g., https://github.com/owner/repo/pull/123)
      const prFromUrl = extractPrNumberFromUrl(mergeTarget);
      if (prFromUrl) {
        return Number.parseInt(prFromUrl, 10);
      }

      // Try getting PR for the specified branch name
      // Pass repo if --repo option was specified in the command
      const prFromBranch = await getPrNumberForBranch(mergeTarget, repo);
      if (prFromBranch) {
        return Number.parseInt(prFromBranch, 10);
      }
    }
  }

  // If no PR number in command, get PR for current branch
  try {
    const [success, stdout] = await runGhCommand(
      ["pr", "view", "--json", "number"],
      TIMEOUT_MEDIUM * 1000,
    );

    if (success) {
      const data = JSON.parse(stdout);
      return data.number ?? null;
    }
  } catch {
    // gh CLI not available, network error, or invalid JSON
  }

  return null;
}

/**
 * Get the linked Issue number from a PR.
 *
 * Fetches PR details and extracts issue references from body, title, and branch name.
 *
 * @param prNumber - The PR number
 * @returns Issue number if found, null otherwise
 */
export async function getLinkedIssueFromPr(prNumber: number): Promise<number | null> {
  try {
    const [success, stdout] = await runGhCommand(
      ["pr", "view", String(prNumber), "--json", "body,title,headRefName"],
      TIMEOUT_MEDIUM * 1000,
    );

    if (!success) {
      return null;
    }

    const data = JSON.parse(stdout) as PrDetailsForIssueExtraction;
    return extractIssueFromPrDetails(data);
  } catch {
    return null;
  }
}
