/**
 * 純粋な文字列操作ユーティリティ
 *
 * Why:
 *   コマンドパース、ブランチ名サニタイズ、環境変数抽出など、
 *   外部依存なしの文字列操作を一箇所に集約する。
 *
 * What:
 *   - stripQuotedStrings(): クォート内文字列を除去
 *   - splitCommandChain(): コマンドチェーンを分割（&&, ||, ;）
 *   - sanitizeBranchName(): ブランチ名をファイル名用にサニタイズ
 *   - isSkipEnvEnabled(): SKIP_*環境変数の有効判定
 *   - extractInlineSkipEnv(): インラインSKIP_*変数を抽出
 *
 * Remarks:
 *   - エスケープクォートは未対応（Claude Code用途では許容）
 *   - 外部依存なし（正規表現のみ）
 *   - 各関数はステートレスで副作用なし
 *
 * Changelog:
 *   - silenvx/dekita#2867: Python版から移行
 */

/**
 * Remove quoted strings from command to avoid false positives.
 *
 * This prevents detecting commands inside echo/printf strings like:
 * - echo 'gh pr create'
 * - printf "gh pr create"
 *
 * Note: This does not handle escaped quotes (e.g., echo 'it\'s quoted').
 * For Claude Code's typical usage patterns, this is acceptable.
 */
export function stripQuotedStrings(cmd: string): string {
  // Remove both single and double-quoted strings in one pass
  return cmd.replace(/"[^"]*"|'[^']*'/g, "");
}

/**
 * Split a command chain into individual commands.
 *
 * Splits on shell operators: &&, ||, ;
 *
 * Note: This function expects the input to have quoted strings already stripped
 * (via stripQuotedStrings). This prevents splitting on operators inside quotes.
 *
 * @example
 * const stripped = stripQuotedStrings("git worktree remove --force && git push");
 * splitCommandChain(stripped);
 * // ['git worktree remove --force', 'git push']
 *
 * @param command - Command string (should have quoted strings stripped first)
 * @returns List of individual commands
 */
export function splitCommandChain(command: string): string[] {
  const parts = command.split(/\s*(?:&&|\|\||;)\s*/);
  return parts.map((p) => p.trim()).filter((p) => p.length > 0);
}

/**
 * Split a command chain into individual commands, respecting quotes.
 *
 * Splits on shell operators (&&, ||, ;) but not when they appear inside quotes.
 * This preserves quoted strings in the output, unlike the regular splitCommandChain.
 *
 * Issue #3365: Needed for proper handling of quoted global flag values.
 *
 * @example
 * splitCommandChainQuoteAware('gh -R "owner/repo" pr merge && git status');
 * // ['gh -R "owner/repo" pr merge', 'git status']
 *
 * @param command - Command string (quotes are preserved)
 * @returns List of individual commands with quotes preserved
 */
export function splitCommandChainQuoteAware(command: string): string[] {
  const parts: string[] = [];
  let current = "";
  let inSingle = false;
  let inDouble = false;
  let i = 0;

  function pushCurrentAndSkipWhitespace(): void {
    if (current.trim()) parts.push(current.trim());
    current = "";
    while (i < command.length && (command[i] === " " || command[i] === "\t")) {
      i++;
    }
  }

  while (i < command.length) {
    const char = command[i];

    if (inSingle) {
      current += char;
      if (char === "'") inSingle = false;
    } else if (inDouble) {
      if (char === "\\" && i + 1 < command.length) {
        current += char + command[i + 1];
        i += 2;
        continue;
      }
      current += char;
      if (char === '"') inDouble = false;
    } else if (char === "'") {
      inSingle = true;
      current += char;
    } else if (char === '"') {
      inDouble = true;
      current += char;
    } else if (char === "\\" && i + 1 < command.length) {
      current += char + command[i + 1];
      i += 2;
      continue;
    } else if (command.slice(i, i + 2) === "&&" || command.slice(i, i + 2) === "||") {
      i += 2;
      pushCurrentAndSkipWhitespace();
      continue;
    } else if (char === ";") {
      i++;
      pushCurrentAndSkipWhitespace();
      continue;
    } else {
      current += char;
    }
    i++;
  }

  // クォートが不均衡な場合は、部分的な分割結果は信用できないため
  // 元のコマンド文字列を単一要素として返す
  if (inSingle || inDouble) {
    return command.trim() ? [command.trim()] : [];
  }

  if (current.trim()) parts.push(current.trim());
  return parts;
}

/**
 * Sanitize branch name for use in filename.
 *
 * Uses the same rule as existing gemini hook files (gemini_review_check.ts,
 * gemini_review_logger.ts) for consistency: only alphanumeric characters,
 * dots, underscores, and dashes are allowed; all other characters are
 * replaced with dashes.
 *
 * Design decision: This implementation prioritizes consistency with existing
 * TypeScript hooks over Python parity. Differences from Python version:
 * - No consecutive dash collapsing (Python: "a//b" → "a-b", TS: "a//b" → "a--b")
 * - No leading/trailing dash removal (Python: "/a/" → "a", TS: "/a/" → "-a-")
 * - Spaces become dashes (Python: "a b" → "a_b", TS: "a b" → "a-b")
 *
 * @example
 * const branch = "feat/issue-123";
 * const filename = `codex-review-${sanitizeBranchName(branch)}.done`;
 * // filename = "codex-review-feat-issue-123.done"
 *
 * @param branch - The git branch name to sanitize.
 * @returns A sanitized string safe for use in filenames.
 */
export function sanitizeBranchName(branch: string): string {
  // Same rule as gemini_review_check.ts and gemini_review_logger.ts
  return branch.replace(/[^a-zA-Z0-9._-]/g, "-");
}

/**
 * Check if a SKIP_* environment variable value indicates enabled.
 *
 * Only explicit truthy values ("1", "true", "True") are considered enabled.
 * This prevents accidental skips from empty strings, "0", "false", etc.
 *
 * Issue #956: Consistent validation for SKIP_* environment variables.
 *
 * @param value - The environment variable value (may be null/undefined if not set).
 * @returns True only if value is "1", "true", or "True".
 *          False for all other values including null, undefined, "", "0", "false", "False".
 *
 * @example
 * isSkipEnvEnabled("1");     // true
 * isSkipEnvEnabled("true");  // true
 * isSkipEnvEnabled("0");     // false
 * isSkipEnvEnabled(null);    // false
 * isSkipEnvEnabled("");      // false
 */
export function isSkipEnvEnabled(value: string | null | undefined): boolean {
  return value === "1" || value === "true" || value === "True";
}

/**
 * Extract inline environment variable value from command, handling quotes.
 *
 * This function:
 * 1. First checks if the env var exists outside quoted strings (to avoid
 *    false positives from commands like: echo 'SKIP_PLAN=1')
 * 2. Then extracts the value from the original command, handling quoted values
 *    like SKIP_PLAN="1" or SKIP_PLAN='true'
 *
 * Issue #956: Handle quoted inline SKIP_* values correctly.
 *
 * @param command - The command string to search in.
 * @param envName - The environment variable name (e.g., "SKIP_PLAN").
 * @returns The unquoted value if found outside quoted strings, null otherwise.
 *
 * @example
 * extractInlineSkipEnv("SKIP_PLAN=1 git worktree add", "SKIP_PLAN");
 * // '1'
 * extractInlineSkipEnv('SKIP_PLAN="true" git worktree', "SKIP_PLAN");
 * // 'true'
 * extractInlineSkipEnv("echo 'SKIP_PLAN=1'", "SKIP_PLAN");
 * // null (inside quotes)
 */
/**
 * Check if a position in a string is inside quoted text.
 *
 * @param str - The string to check.
 * @param pos - The position (index) to check.
 * @returns True if the position is inside single or double quotes.
 */
function isPositionInsideQuotes(str: string, pos: number): boolean {
  let inSingle = false;
  let inDouble = false;
  for (let i = 0; i < pos; i++) {
    if (str[i] === "'" && !inDouble) inSingle = !inSingle;
    else if (str[i] === '"' && !inSingle) inDouble = !inDouble;
  }
  return inSingle || inDouble;
}

export function extractInlineSkipEnv(command: string, envName: string): string | null {
  // Escape special regex characters in envName for robustness
  const escapedEnvName = envName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  // Find all matches and return the first one outside quotes.
  // Handles: echo 'SKIP_PLAN=1' && SKIP_PLAN=0 git (returns "0", not "1")
  // Issue #3362: Support quoted values like SKIP_PLAN="true value"
  // Pattern: "..." | '...' | unquoted (with escape support)
  const valuePattern = new RegExp(
    `\\b${escapedEnvName}=("(?:[^"\\\\]|\\\\.)*"|'(?:[^'\\\\]|\\\\.)*'|[^\\s;&|]+)`,
    "g",
  );
  let match: RegExpExecArray | null = valuePattern.exec(command);
  while (match !== null) {
    const pos = match.index;
    if (!isPositionInsideQuotes(command, pos)) {
      // Found a match outside quotes
      let value = match[1];
      // Remove surrounding quotes if present (both single and double)
      if (
        value.length >= 2 &&
        (value[0] === '"' || value[0] === "'") &&
        value[value.length - 1] === value[0]
      ) {
        value = value.slice(1, -1);
      }
      return value;
    }
    match = valuePattern.exec(command);
  }

  return null;
}

/**
 * Check if a SKIP_* environment variable is enabled (env or inline).
 *
 * This is a convenience function that combines:
 * 1. Checking process.env for the environment variable
 * 2. Checking the command for inline environment variable setting
 *
 * @param _hookName - Hook name for logging (currently unused, reserved for future use)
 * @param envName - Environment variable name (e.g., "SKIP_BRANCH_RENAME_GUARD")
 * @param inputContext - Input context containing command preview
 * @returns True if skip is enabled, false otherwise
 *
 * @example
 * if (checkSkipEnv(HOOK_NAME, "SKIP_BRANCH_RENAME_GUARD", inputContext)) {
 *   // Skip the check
 * }
 */
export function checkSkipEnv(
  _hookName: string,
  envName: string,
  inputContext: { input_preview?: string },
): boolean {
  // Check environment variable
  if (isSkipEnvEnabled(process.env[envName])) {
    return true;
  }

  // Check inline environment variable in command
  const command = inputContext.input_preview || "";
  const inlineValue = extractInlineSkipEnv(command, envName);
  if (isSkipEnvEnabled(inlineValue)) {
    return true;
  }

  return false;
}

/**
 * Split a shell command into individual arguments.
 *
 * Handles:
 * - Quoted strings (single and double quotes)
 * - Escaped characters within quotes
 * - Multiple spaces between arguments
 *
 * @param command - Shell command string to split
 * @returns Array of arguments
 * @throws Error if quotes are unbalanced
 *
 * @example
 * splitShellArgs('gh issue create --title "My Issue" --body "Description"');
 * // ['gh', 'issue', 'create', '--title', 'My Issue', '--body', 'Description']
 *
 * splitShellArgs("echo 'hello world'");
 * // ['echo', 'hello world']
 */
export function splitShellArgs(command: string): string[] {
  const args: string[] = [];
  let current = "";
  let isToken = false; // Track if we are currently building a token
  let inSingleQuote = false;
  let inDoubleQuote = false;
  let i = 0;

  while (i < command.length) {
    const char = command[i];

    if (inSingleQuote) {
      if (char === "'") {
        inSingleQuote = false;
      } else {
        current += char;
      }
      isToken = true; // Quotes always imply a token
    } else if (inDoubleQuote) {
      if (char === '"') {
        inDoubleQuote = false;
      } else if (char === "\\" && i + 1 < command.length) {
        // Handle escaped characters in double quotes
        const nextChar = command[i + 1];
        if (nextChar === '"' || nextChar === "\\" || nextChar === "$" || nextChar === "`") {
          current += nextChar;
          i++;
        } else {
          current += char;
        }
      } else {
        current += char;
      }
      isToken = true; // Quotes always imply a token
    } else {
      // Not in quotes
      if (char === "'") {
        inSingleQuote = true;
        isToken = true;
      } else if (char === '"') {
        inDoubleQuote = true;
        isToken = true;
      } else if (char === " " || char === "\t") {
        if (isToken) {
          args.push(current);
          current = "";
          isToken = false;
        }
      } else if (char === "\\" && i + 1 < command.length) {
        // Handle escaped characters outside quotes
        current += command[i + 1];
        i++;
        isToken = true;
      } else {
        current += char;
        isToken = true;
      }
    }

    i++;
  }

  // Check for unbalanced quotes
  if (inSingleQuote || inDoubleQuote) {
    throw new Error("Unbalanced quotes in command");
  }

  // Add the last argument if any
  if (isToken) {
    args.push(current);
  }

  return args;
}

/**
 * Pattern to match leading inline environment variable assignments.
 *
 * Matches patterns like:
 * - VAR=value
 * - env VAR=value
 * - VAR=value VAR2=value2
 * - VAR="value with spaces"
 * - VAR='value with spaces'
 *
 * Issue #3161: Handle GH_TOKEN=xxx gh pr merge, env GH_TOKEN=xxx gh pr merge
 * Issue #3263: Also handle quoted values with spaces: VAR="value with spaces"
 * Issue #3299: Move to lib/strings.ts for shared use
 * Issue #3364: Handle interleaved env and variable assignments: VAR=val env VAR2=val2 cmd
 *
 * @example
 * const pattern = envPrefixPattern;
 * "GH_TOKEN=xxx gh pr merge".replace(pattern, ""); // "gh pr merge"
 * 'env DEBUG=1 npm start'.replace(pattern, ""); // "npm start"
 * 'VAR="hello world" cmd'.replace(pattern, ""); // "cmd"
 * 'VAR=val env VAR2=val2 cmd'.replace(pattern, ""); // "cmd"
 */
export const envPrefixPattern =
  /^(?:(?:env\s+)|(?:[A-Za-z_][A-Za-z0-9_]*=(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\S*)\s+))*/;

/**
 * Strip leading inline environment variable assignments from a command.
 *
 * This is useful for normalizing commands before pattern matching, e.g.,
 * detecting `gh pr merge` even when prefixed with `GH_TOKEN=xxx`.
 *
 * Issue #3161: Handle env prefixes in merge_check.ts
 * Issue #3299: Move to lib/strings.ts for shared use
 *
 * @param command - Shell command string that may have env prefix
 * @returns Command with leading env assignments removed
 *
 * @example
 * stripEnvPrefix("GH_TOKEN=xxx gh pr merge"); // "gh pr merge"
 * stripEnvPrefix("env DEBUG=1 npm start"); // "npm start"
 * stripEnvPrefix('VAR="hello world" cmd'); // "cmd"
 * stripEnvPrefix("git status"); // "git status" (no change)
 */
export function stripEnvPrefix(command: string): string {
  return command.replace(envPrefixPattern, "");
}

/**
 * 文字列を指定された最大長（コードポイント単位）で切り詰める
 *
 * Issue #3932: 複数箇所で使われる切り詰めパターンを共通関数に抽出
 *
 * @param text 対象文字列
 * @param maxLength 最大長（コードポイント単位、負の値は0として扱う）
 * @param suffix 切り詰め時の末尾文字列（デフォルト: "..."）
 * @returns 切り詰められた文字列、または元の文字列（maxLength以下の場合）
 *
 * @example
 * truncate("Hello, World!", 5); // "Hello..."
 * truncate("Hi", 5);            // "Hi"
 * truncate("Long text", 4, "…"); // "Long…"
 * truncate("😀😀😀", 2);         // "😀😀..."
 */
export function truncate(text: string, maxLength: number, suffix = "..."): string {
  const safeMaxLength = Math.max(0, maxLength);

  // Optimization: Code point count is always <= string length (UTF-16 units).
  // If the string length is within limits, we definitely don't need to truncate.
  if (text.length <= safeMaxLength) {
    return text;
  }

  let index = 0;
  let count = 0;

  // Use iterator to handle surrogate pairs correctly without full array allocation
  for (const char of text) {
    if (count >= safeMaxLength) {
      return text.slice(0, index) + suffix;
    }
    index += char.length;
    count++;
  }

  return text;
}
