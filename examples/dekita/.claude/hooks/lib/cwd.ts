/**
 * カレントワーキングディレクトリの検出・検証を行う。
 *
 * Why:
 *   cdコマンド実行後のworktree削除を正しくブロックするため、
 *   複数ソースからの効果的なcwd検出が必要。
 *
 * What:
 *   - getEffectiveCwd(): 環境変数・コマンド内cdを考慮したcwd取得
 *   - checkCwdInsidePath(): cwdが指定パス内にあるか判定
 *   - extractCdTargetFromCommand(): コマンド内cdターゲット抽出
 *   - extractGitCOption(): git -Cオプションのパス抽出
 *   - extractGitCOptionsFromCommand(): git -Cオプションを全て配列で抽出
 *
 * Remarks:
 *   - 優先順: baseCwd → cd（累積） → git -C（累積） → 環境変数
 *   - cd と git -C を組み合わせ可能: 'cd dir && git -C repo' → 'dir/repo'
 *   - git -C は累積的: 'git -C a -C b' → 'a/b' (Git仕様準拠)
 *   - シェルトークン化でクォート・エスケープを正しく処理
 *   - OSエラー時はfail-close（削除をブロック）
 *
 * Changelog:
 *   - silenvx/dekita#3427: cd と git -C の組み合わせ・累積的解決をサポート
 *   - silenvx/dekita#2868: Python版から移行
 */

import { existsSync, realpathSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";

/**
 * Tokenize a shell command string, handling quotes and escapes.
 *
 * This is a simplified implementation similar to Python's shlex.split().
 * Handles:
 * - Single quotes (literal, no escape processing inside)
 * - Double quotes (with escape processing for \", \\)
 * - Unquoted tokens (whitespace-delimited)
 *
 * @param command - The command string to tokenize
 * @returns Array of tokens, or null if parsing fails
 */
export function shellTokenize(command: string): string[] | null {
  const tokens: string[] = [];
  let current = "";
  let inSingleQuote = false;
  let inDoubleQuote = false;
  let escapeNext = false;

  // Shell operators to detect (check longer ones first)
  const operators = ["&&", "||", ";", "|"];

  for (let i = 0; i < command.length; i++) {
    const char = command[i];

    if (escapeNext) {
      current += char;
      escapeNext = false;
      continue;
    }

    if (char === "\\" && inDoubleQuote) {
      // In double quotes, only escape \", \\, \$, \`, \newline
      const next = command[i + 1];
      if (next === '"' || next === "\\" || next === "$" || next === "`") {
        escapeNext = true;
        continue;
      }
      current += char;
      continue;
    }

    if (char === "\\" && !inSingleQuote && !inDoubleQuote) {
      escapeNext = true;
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

    // Check for shell operators only outside quotes
    if (!inSingleQuote && !inDoubleQuote) {
      // Try to match operators (longer ones first: &&, ||, then ;, |)
      let matchedOp: string | null = null;
      for (const op of operators) {
        if (command.slice(i, i + op.length) === op) {
          matchedOp = op;
          break;
        }
      }

      if (matchedOp) {
        // Push current token if any
        if (current.length > 0) {
          tokens.push(current);
          current = "";
        }
        // Push operator as separate token
        tokens.push(matchedOp);
        // Skip operator characters (minus 1 because loop will increment)
        i += matchedOp.length - 1;
        continue;
      }

      if (/\s/.test(char)) {
        if (current.length > 0) {
          tokens.push(current);
          current = "";
        }
        continue;
      }
    }

    current += char;
  }

  // Check for unbalanced quotes
  if (inSingleQuote || inDoubleQuote) {
    return null;
  }

  if (current.length > 0) {
    tokens.push(current);
  }

  return tokens;
}

/**
 * Extract all cd targets from a command string.
 *
 * When a command contains multiple cd commands like 'cd dir1 && cd dir2 && git ...',
 * this returns all cd targets in order for cumulative path resolution.
 *
 * Note: When shell tokenization fails (unbalanced quotes), falls back to regex
 * which only returns the first cd at command start. This is a best-effort fallback.
 *
 * @param command - The full command string
 * @returns Array of cd target paths in order, empty if none found.
 */
export function extractCdTargetsFromCommand(command: string): string[] {
  const tokens = shellTokenize(command);

  if (!tokens) {
    // Fallback to regex for unbalanced quotes - only get first cd
    const match = command.match(/^cd\s+(['"]?)([^'"&;]+)\1\s*(?:&&|;)/);
    if (match) {
      return [match[2].trim()];
    }
    return [];
  }

  // Valid predecessors: operators that start a new command where cd affects subsequent commands
  const validPredecessors = new Set(["&&", ";"]);

  const cdTargets: string[] = [];

  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];

    if (token === "cd" && i + 1 < tokens.length) {
      // cd must be at start or after && or ; (not | or ||)
      const isValidStart = i === 0 || validPredecessors.has(tokens[i - 1]);

      if (isValidStart) {
        const nextToken = tokens[i + 1];

        // Skip shell operators - cd must have a path argument
        if (!["&&", ";", "|", "||"].includes(nextToken)) {
          // Check if there's a separator after the path
          if (i + 2 < tokens.length && ["&&", ";"].includes(tokens[i + 2])) {
            cdTargets.push(nextToken);
          }
        }
      }
    }
  }

  return cdTargets;
}

/**
 * Extract the target directory from a 'cd <path> &&' pattern in command.
 *
 * When a command starts with 'cd /some/path && git worktree remove ...',
 * the cd will execute first, so the effective cwd for the git command
 * will be the cd target, not the current environment cwd.
 *
 * Note: For cumulative path resolution (cd a && cd b), use getEffectiveCwd()
 * which handles multiple cd commands correctly.
 *
 * @param command - The full command string
 * @returns The last cd target path if found, null otherwise.
 */
export function extractCdTargetFromCommand(command: string): string | null {
  const targets = extractCdTargetsFromCommand(command);
  return targets.length > 0 ? targets[targets.length - 1] : null;
}

/**
 * Extract all -C option paths from the last git command in the string.
 *
 * When a command contains 'git -C a -C b ...', Git resolves the paths
 * cumulatively (a/b if b is relative). This function returns all -C options
 * from the **last** git command only, since shell operators (&&, ;, etc.)
 * start a new command context.
 *
 * Example:
 * - 'git -C a -C b status' → ['a', 'b'] (single git command)
 * - 'git -C a status && git -C b push' → ['b'] (last git command only)
 *
 * @param command - The full command string
 * @returns Array of -C option paths from the last git command, empty if none found.
 */
export function extractGitCOptionsFromCommand(command: string): string[] {
  const tokens = shellTokenize(command);

  if (!tokens) {
    return [];
  }

  const shellOperators = new Set(["&&", "||", ";", "|"]);
  const flagsWithArgs = new Set(["-c", "--git-dir", "--work-tree", "--namespace"]);

  let currentGitOptions: string[] = [];
  let inGitCommand = false;

  let i = 0;
  while (i < tokens.length) {
    const token = tokens[i];

    // Check if this is a git command start
    if (token === "git" || token.endsWith("/git")) {
      inGitCommand = true;
      // Reset options for new git command
      currentGitOptions = [];
      i++;
      continue;
    }

    // Reset when hitting a shell operator
    if (shellOperators.has(token)) {
      inGitCommand = false;
      i++;
      continue;
    }

    // Only process options inside a git command
    if (inGitCommand) {
      // Handle -C option with space: -C /path
      if (token === "-C" && i + 1 < tokens.length) {
        const nextToken = tokens[i + 1];
        if (!shellOperators.has(nextToken)) {
          currentGitOptions.push(nextToken);
          i += 2;
          continue;
        }
      }

      // Handle -C/path format (no space)
      if (token.startsWith("-C") && token.length > 2) {
        currentGitOptions.push(token.slice(2));
        i++;
        continue;
      }

      // Skip options that take arguments (e.g., -c key=value)
      if (flagsWithArgs.has(token)) {
        i += 2;
        continue;
      }

      // Handle --option=value format
      if (token.startsWith("--") && token.includes("=")) {
        i++;
        continue;
      }

      // At subcommand (non-flag token that's not an option argument)
      if (!token.startsWith("-")) {
        inGitCommand = false;
      }
    }

    i++;
  }

  return currentGitOptions;
}

/**
 * Extract the -C option path from a git command.
 *
 * When a command contains 'git -C <path> ...', the git command operates
 * as if it was started in <path> instead of the current working directory.
 *
 * Note: For cumulative -C resolution (git -C a -C b), use extractGitCOptionsFromCommand()
 * which returns all options for proper path resolution.
 *
 * @param command - The full command string
 * @param firstOnly - If true, return the first -C option found. If false (default), return the last.
 * @returns The -C option path if found, null otherwise.
 */
export function extractGitCOption(command: string, firstOnly = false): string | null {
  const options = extractGitCOptionsFromCommand(command);
  if (options.length === 0) {
    return null;
  }
  return firstOnly ? options[0] : options[options.length - 1];
}

/**
 * Get cwd from environment variables or process cwd.
 *
 * Priority: CLAUDE_WORKING_DIRECTORY > PWD > process.cwd()
 */
function getEnvCwd(): string {
  // Try CLAUDE_WORKING_DIRECTORY first (set by Claude Code)
  const claudeWd = process.env.CLAUDE_WORKING_DIRECTORY;
  if (claudeWd && existsSync(claudeWd)) {
    return realpathSync(claudeWd);
  }

  // Try PWD (shell's tracked working directory after cd)
  const pwd = process.env.PWD;
  if (pwd && existsSync(pwd)) {
    return realpathSync(pwd);
  }

  // Fallback to process cwd
  return process.cwd();
}

/**
 * Expand ~ to home directory in path.
 *
 * Note: Only supports ~ and ~/path format. Does not support ~user format
 * (other user's home directory) unlike Python's Path.expanduser().
 */
export function expandHome(pathStr: string): string {
  const home = homedir();
  // homedir() returns empty string in restricted environments
  if (!home) return pathStr;
  if (pathStr === "~") {
    return home;
  }
  if (pathStr.startsWith("~/")) {
    return join(home, pathStr.slice(2));
  }
  return pathStr;
}

/**
 * Get effective current working directory.
 *
 * Considers multiple sources in priority order:
 * 1. 'cd <path> &&' prefix in command (if command provided)
 *    - Handles cumulative relative paths: 'cd a && cd b' resolves to '/cwd/a/b'
 * 2. 'git -C <path>' option in command (if command provided)
 *    - Applied after cd resolution: 'cd dir && git -C repo' resolves to 'dir/repo'
 *    - Handles cumulative -C options: 'git -C a -C b' resolves to 'a/b' (Git spec)
 * 3. CLAUDE_WORKING_DIRECTORY (set by Claude Code after cd commands)
 * 4. PWD (shell's tracked working directory)
 * 5. process.cwd() (process working directory, fallback)
 *
 * @param command - Optional command string to check for 'cd <path> &&' or 'git -C <path>' pattern
 * @param baseCwd - Optional base directory for resolving relative cd paths.
 * @returns Resolved path of effective working directory.
 */
export function getEffectiveCwd(command?: string | null, baseCwd?: string | null): string {
  // Start with baseCwd or environment cwd
  let currentPath = baseCwd ? resolve(baseCwd) : getEnvCwd();

  // Step 1: Apply 'cd <path> &&' patterns cumulatively
  // 'cd a && cd b' from /cwd resolves to /cwd/a/b
  if (command) {
    const cdTargets = extractCdTargetsFromCommand(command);
    for (const target of cdTargets) {
      const cdPath = expandHome(target);
      // Absolute path resets, relative path accumulates
      currentPath = isAbsolute(cdPath) ? cdPath : resolve(currentPath, cdPath);
    }
  }

  // Step 2: Apply 'git -C <path>' options cumulatively on top of cd-resolved path
  // 'cd dir && git -C repo' resolves to 'dir/repo'
  // 'git -C a -C b' resolves to 'a/b' (per Git specification)
  if (command) {
    const gitCTargets = extractGitCOptionsFromCommand(command);
    for (const target of gitCTargets) {
      const gitCPath = expandHome(target);
      // Absolute path resets, relative path accumulates
      currentPath = isAbsolute(gitCPath) ? gitCPath : resolve(currentPath, gitCPath);
    }
  }

  // Validate and return the resolved path
  if (existsSync(currentPath)) {
    return realpathSync(currentPath);
  }

  // If path doesn't exist, fall back to env cwd
  return getEnvCwd();
}

/**
 * Check if effective current working directory is inside the target path.
 *
 * This is critical for worktree operations because deleting a worktree
 * while cwd is inside it will cause all subsequent Bash commands to fail.
 *
 * @param targetPath - The target path to check against (e.g., worktree path).
 * @param command - Optional command string to check for 'cd <path> &&' pattern
 * @returns True if cwd is inside the target path (should block deletion).
 */
export function checkCwdInsidePath(targetPath: string, command?: string | null): boolean {
  try {
    const cwd = getEffectiveCwd(command);
    const targetResolved = realpathSync(resolve(targetPath));

    // Check if cwd is the target or a subdirectory
    if (cwd === targetResolved) {
      return true;
    }

    // Check if targetResolved is a parent of cwd
    let current = cwd;
    while (current !== dirname(current)) {
      current = dirname(current);
      if (current === targetResolved) {
        return true;
      }
    }

    return false;
  } catch {
    // If we can't determine cwd, fail-close: block deletion
    return true;
  }
}
