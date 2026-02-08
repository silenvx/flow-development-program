/**
 * locked-worktree-guard用の低レベルシェルトークン化ユーティリティ。
 *
 * Why:
 *   シェルコマンドを正確に解析するには、シェル演算子やリダイレクト、
 *   引用符を適切に処理する必要がある。低レベルのトークン化を一箇所に
 *   まとめることで、コマンド解析の信頼性を向上させる。
 *
 * What:
 *   - シェル演算子の正規化（&&, ||, ;, |）
 *   - シェルリダイレクト検出
 *   - cdターゲット抽出
 *   - rmコマンドパス抽出
 *   - gitグローバルフラグからのベースディレクトリ抽出
 *
 * Remarks:
 *   - command_parser.ts: 高レベルのコマンド固有解析を担当
 *   - 本モジュールは汎用的なシェルトークン化に特化
 *   - 引用符内の演算子処理は妥協（検出のみで実行はしないため許容）
 *
 * Changelog:
 *   - silenvx/dekita#1106: シェルリダイレクト検出追加
 *   - silenvx/dekita#1676: cdターゲット抽出追加
 *   - silenvx/dekita#3157: TypeScriptに移植
 */

import { dirname } from "node:path";

/**
 * Split a command string into tokens, handling quotes.
 * Simplified version of Python's shlex.split().
 *
 * Note: POSIX shell escaping rules in double quotes:
 * - Backslash only escapes: ", $, `, \, newline
 * - Other backslashes are preserved literally
 */
export function shellSplit(command: string): string[] {
  const tokens: string[] = [];
  let current = "";
  let inSingleQuote = false;
  let inDoubleQuote = false;
  let escapeNext = false;
  // Track if we're building a token (to preserve empty quoted strings)
  let hasContent = false;

  for (let i = 0; i < command.length; i++) {
    const char = command[i];

    if (escapeNext) {
      // In double quotes, backslash only escapes specific characters (POSIX)
      if (inDoubleQuote && !/["$`\\\n]/.test(char)) {
        // Preserve the backslash for non-special characters
        current += "\\";
      }
      current += char;
      escapeNext = false;
      continue;
    }

    if (char === "\\" && !inSingleQuote) {
      escapeNext = true;
      continue;
    }

    if (char === "'" && !inDoubleQuote) {
      inSingleQuote = !inSingleQuote;
      hasContent = true; // Opening quote marks start of content
      continue;
    }

    if (char === '"' && !inSingleQuote) {
      inDoubleQuote = !inDoubleQuote;
      hasContent = true; // Opening quote marks start of content
      continue;
    }

    if (!inSingleQuote && !inDoubleQuote && /\s/.test(char)) {
      if (current || hasContent) {
        tokens.push(current);
        current = "";
        hasContent = false;
      }
      continue;
    }

    current += char;
    hasContent = true;
  }

  if (current || hasContent) {
    tokens.push(current);
  }

  return tokens;
}

/**
 * Add spaces around shell operators for proper tokenization.
 *
 * This ensures operators like '&&', '||', ';', '|' are separated from
 * adjacent tokens so shellSplit() can tokenize them properly.
 *
 * Example: 'foo&&git bar' -> 'foo && git bar'
 *
 * Note: This may modify operators inside quoted strings, but since this
 * is only used for command detection (not execution), this trade-off
 * is acceptable for security purposes.
 */
export function normalizeShellOperators(command: string): string {
  let result = command;

  // Process longer operators first to avoid partial matches
  for (const op of ["&&", "||"]) {
    // Add space before if preceded by non-whitespace
    result = result.replace(new RegExp(`(\\S)(${escapeRegExp(op)})`, "g"), "$1 $2");
    // Add space after if followed by non-whitespace
    result = result.replace(new RegExp(`(${escapeRegExp(op)})(\\S)`, "g"), "$1 $2");
  }

  // Handle single | carefully (not matching || or already-spaced |)
  // Use [^\s|] to exclude pipe characters from matching as adjacent non-whitespace
  result = result.replace(/([^\s|])\|(?!\|)/g, "$1 |");
  result = result.replace(/(?<!\|)\|([^\s|])/g, "| $1");

  // Handle ;
  result = result.replace(/(\S);/g, "$1 ;");
  result = result.replace(/;(\S)/g, "; $1");

  return result;
}

/**
 * Escape special regex characters in a string.
 */
function escapeRegExp(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Check if a token is a shell redirect operator.
 *
 * Detects patterns like:
 * - 2>&1, >&2, 1>&2 (fd redirection)
 * - >file, >>file, 2>file, 2>>file (output redirection with target)
 * - >, >> (bare redirect operators, target is next token)
 * - <file (input redirection)
 *
 * Issue #1106: Shell redirects were being passed as arguments to gh command.
 */
export function isShellRedirect(token: string): boolean {
  // Pattern: digit(s) followed by > or >> and optional target
  // e.g., 2>&1, 1>&2, 2>file, 2>>file, >, >>
  if (/^\d*>{1,2}/.test(token)) {
    return true;
  }
  // Pattern: < for input redirection
  // e.g., <file, 0<file, <
  if (/^\d*</.test(token)) {
    return true;
  }
  // Pattern: >&digit (shorthand for 1>&digit)
  // e.g., >&2
  if (/^>&\d/.test(token)) {
    return true;
  }
  return false;
}

/**
 * Check if a token is a bare redirect operator (without target).
 *
 * These are operators like '>', '>>', '<' that have their target
 * as the next token when spaced apart (e.g., '> output.log').
 *
 * Issue #1106: When redirects are written with spaces, shellSplit produces
 * separate tokens for the operator and target. We need to skip both.
 */
export function isBareRedirectOperator(token: string): boolean {
  // Bare redirect operators: >, >>, <, 2>, 2>>, etc.
  return /^\d*>{1,2}$/.test(token) || /^\d*<$/.test(token);
}

/**
 * Extract cd target directory that precedes a git worktree remove command.
 *
 * Handles patterns like:
 * - cd /path && git worktree remove ...
 * - cd /path ; git worktree remove ...
 * - cd "/path with spaces" && git worktree remove ...
 *
 * This fixes Issue #665: cd command's effect is not recognized by the guard,
 * causing path resolution to fail when using relative paths after cd.
 */
export function extractCdTargetBeforeGit(command: string): string | null {
  // Normalize shell operators first
  const normalized = normalizeShellOperators(command);
  let tokens: string[];
  try {
    tokens = shellSplit(normalized);
  } catch {
    tokens = normalized.split(/\s+/);
  }

  // Look for 'cd' followed by path, then separator, then git worktree remove
  // Pipeline handling: cd in a pipeline runs in a subshell and doesn't affect subsequent commands
  let cdTarget: string | null = null;
  let inPipeline = false; // True when we're inside a pipeline (after |)
  let i = 0;

  while (i < tokens.length) {
    const token = tokens[i];

    // Found cd command
    if (token === "cd") {
      // Find the effective cd target, skipping cd flags like -P or -L
      let j = i + 1;
      let potentialTarget: string | null = null;
      let foundTarget = false;

      while (j < tokens.length && !["&&", "||", ";", "|"].includes(tokens[j])) {
        const t = tokens[j];
        // Skip cd flags that start with '-' except for a lone '-'
        if (t.startsWith("-") && t !== "-") {
          j++;
          continue;
        }
        // First non-flag, non-separator token is the target
        potentialTarget = t;
        foundTarget = true;
        break;
      }

      if (foundTarget && potentialTarget !== null) {
        // Check what separator follows the cd command
        let k = j + 1;
        while (k < tokens.length && !["&&", "||", ";", "|"].includes(tokens[k])) {
          k++;
        }

        // Only set cdTarget if:
        // 1. Not currently in a pipeline context (after a previous |)
        // 2. This cd is not followed by | (which would make it part of a new pipeline)
        const separator = k < tokens.length ? tokens[k] : null;
        if (!inPipeline && separator !== "|") {
          cdTarget = potentialTarget;
        }
        i = j + 1;
      } else {
        i++;
      }
      continue;
    }

    // Check if this is git worktree remove
    if (token === "git") {
      // If we found cd before this git command, return the cd target
      let j = i + 1;
      // Skip git global flags
      const flagsWithArgs = new Set(["-C", "--git-dir", "--work-tree", "-c"]);

      while (j < tokens.length) {
        const t = tokens[j];
        if (["&&", "||", ";", "|"].includes(t)) {
          break;
        }
        if (t.startsWith("-")) {
          if (t.includes("=")) {
            j++;
          } else if (flagsWithArgs.has(t)) {
            j += 2;
          } else {
            j++;
          }
        } else {
          break;
        }
      }

      // Check for 'worktree remove'
      if (j < tokens.length && tokens[j] === "worktree") {
        if (j + 1 < tokens.length && tokens[j + 1] === "remove") {
          return cdTarget;
        }
      }
    }

    // Handle shell operators for pipeline and separator tracking
    if (token === "|") {
      // Enter pipeline context
      inPipeline = true;
    } else if (["&&", "||", ";"].includes(token)) {
      // Exit pipeline context
      inPipeline = false;
    }

    i++;
  }

  return null;
}

/**
 * Check if tokens starting at startIdx form a git worktree remove command.
 */
export function checkSingleGitWorktreeRemove(tokens: string[], startIdx: number): boolean {
  if (startIdx >= tokens.length || tokens[startIdx] !== "git") {
    return false;
  }

  // Skip global flags to find 'worktree'
  const flagsWithArgs = new Set(["-C", "--git-dir", "--work-tree", "-c"]);
  let i = startIdx + 1;

  while (i < tokens.length) {
    const token = tokens[i];
    // Stop at command separators
    if (["&&", "||", ";", "|"].includes(token)) {
      return false;
    }
    if (token.startsWith("-")) {
      // Check for --flag=value format
      if (token.includes("=")) {
        i++;
      } else if (flagsWithArgs.has(token)) {
        // Skip flag and its argument, but only if argument exists
        if (i + 1 < tokens.length && !["&&", "||", ";", "|"].includes(tokens[i + 1])) {
          i += 2;
        } else {
          // Malformed command: flag expects argument but none present
          break;
        }
      } else {
        // Unknown flag, skip just the flag
        i++;
      }
    } else {
      break;
    }
  }

  // Check if we found 'worktree' followed by 'remove'
  if (i < tokens.length && tokens[i] === "worktree") {
    if (i + 1 < tokens.length && tokens[i + 1] === "remove") {
      return true;
    }
  }

  return false;
}

/**
 * Extract base directory (-C, --work-tree, --git-dir) from a git command segment.
 */
export function extractBaseDirFromGitSegment(tokens: string[], gitIdx: number): string | null {
  if (gitIdx >= tokens.length || tokens[gitIdx] !== "git") {
    return null;
  }

  let i = gitIdx + 1;
  while (i < tokens.length) {
    const token = tokens[i];

    // Stop at command separators or worktree subcommand
    if (["&&", "||", ";", "|", "worktree"].includes(token)) {
      break;
    }

    // -C flag
    if (token === "-C") {
      if (i + 1 < tokens.length && !["&&", "||", ";", "|"].includes(tokens[i + 1])) {
        return tokens[i + 1];
      }
      break;
    }

    // --work-tree flag (two forms: --work-tree=/path or --work-tree /path)
    if (token.startsWith("--work-tree=")) {
      return token.slice("--work-tree=".length);
    }
    if (token === "--work-tree") {
      if (i + 1 < tokens.length && !["&&", "||", ";", "|"].includes(tokens[i + 1])) {
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
      if (i + 1 < tokens.length && !["&&", "||", ";", "|"].includes(tokens[i + 1])) {
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
 * Extract all paths from rm commands in a command string.
 *
 * This is a shared helper for get_rm_target_worktrees() and
 * get_rm_target_orphan_worktrees() to avoid code duplication.
 *
 * Handles:
 * - Basic rm commands: rm -rf foo
 * - Chained commands: rm A && rm B, rm A; rm B
 * - Sudo: sudo rm -rf foo, sudo -u root rm foo
 * - Environment variables: FOO=1 rm -rf bar
 * - Full paths: /bin/rm, /usr/bin/rm
 */
export function extractRmPaths(command: string): string[] {
  // Normalize shell operators first
  const normalized = normalizeShellOperators(command);
  let tokens: string[];
  try {
    tokens = shellSplit(normalized);
  } catch {
    tokens = normalized.split(/\s+/);
  }

  if (tokens.length === 0) {
    return [];
  }

  // Collect paths from ALL rm commands in chained commands
  const paths: string[] = [];
  let i = 0;

  // Helper to check if a token is an rm command (handles full paths)
  const isRmCommand = (token: string): boolean => {
    if (token === "rm") {
      return true;
    }
    if (token.endsWith("/rm")) {
      return true;
    }
    return false;
  };

  let atCommandStart = true;
  let afterSudo = false;

  while (i < tokens.length) {
    const token = tokens[i];

    // Command separators mark the start of a new command segment
    if (["|", ";", "&&", "||"].includes(token)) {
      atCommandStart = true;
      afterSudo = false;
      i++;
      continue;
    }

    // Skip environment variable assignments (e.g., FOO=1 rm -rf)
    if (atCommandStart && token.includes("=") && !token.startsWith("-")) {
      i++;
      continue;
    }

    // Handle sudo
    if (token === "sudo" && atCommandStart) {
      afterSudo = true;
      i++;
      continue;
    }

    // While in sudo context, look for rm command
    if (afterSudo) {
      const sudoFlagsWithArgs = new Set(["-u", "-g", "-r", "-p", "-D", "-h", "-C", "-T"]);

      if (token.startsWith("-")) {
        if (sudoFlagsWithArgs.has(token)) {
          i++;
          if (i < tokens.length && !["|", ";", "&&", "||"].includes(tokens[i])) {
            i++;
          }
        } else {
          i++;
        }
        continue;
      }

      if (isRmCommand(token)) {
        i++;
        atCommandStart = false;
        afterSudo = false;
        while (i < tokens.length) {
          const arg = tokens[i];
          if (["|", ";", "&&", "||"].includes(arg)) {
            break;
          }
          if (!arg.startsWith("-")) {
            paths.push(arg);
          }
          i++;
        }
      } else {
        atCommandStart = false;
        afterSudo = false;
        i++;
      }
      continue;
    }

    // Detect rm command at the start of a segment
    if (isRmCommand(token) && atCommandStart) {
      i++;
      atCommandStart = false;
      while (i < tokens.length) {
        const arg = tokens[i];
        if (["|", ";", "&&", "||"].includes(arg)) {
          break;
        }
        if (!arg.startsWith("-")) {
          paths.push(arg);
        }
        i++;
      }
    } else {
      atCommandStart = false;
      i++;
    }
  }

  return paths;
}

/**
 * Escape a string for safe use in shell double-quoted arguments.
 *
 * IMPORTANT: The caller MUST wrap the result in double quotes when using in commands.
 * This function only escapes characters special to bash within double quotes.
 * Without the outer double quotes, strings with spaces will be split into multiple args.
 *
 * Handles: ", $, `, \
 * Also strips: newlines, carriage returns, null bytes (replaced with space)
 * Note: ! (history expansion) is not escaped because execSync uses non-interactive shells.
 *
 * @example
 * // Correct usage - result wrapped in double quotes
 * const cmd = `gh issue create --title "${escapeShellArg(title)}"`;
 *
 * // Incorrect - missing outer quotes
 * // const cmd = `gh issue create --title ${escapeShellArg(title)}`; // WRONG!
 *
 * escapeShellArg('test"$var') // returns 'test\"$var'
 * escapeShellArg('hello`world`') // returns 'hello\`world\`'
 * escapeShellArg('line1\nline2') // returns 'line1 line2'
 */
export function escapeShellArg(str: string): string {
  // First strip dangerous control characters (newlines, carriage returns, null bytes)
  const sanitized = str.replace(/[\n\r\0]/g, " ");
  // Then escape shell metacharacters
  return sanitized.replace(/["$`\\]/g, "\\$&");
}

/**
 * Simple shell quoting for a single token.
 *
 * Used to reconstruct shell commands after tokenization while preserving
 * the integrity of paths containing spaces.
 *
 * @param s - Token to quote
 * @returns Quoted token (single quotes) if needed, or as-is if safe
 */
export function shellQuote(s: string): string {
  // If the string has no special characters, return as-is
  if (/^[a-zA-Z0-9_./-]+$/.test(s)) {
    return s;
  }
  // Use single quotes, escaping any existing single quotes
  return `'${s.replace(/'/g, "'\"'\"'")}'`;
}
