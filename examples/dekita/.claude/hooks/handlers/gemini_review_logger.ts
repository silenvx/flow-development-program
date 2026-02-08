#!/usr/bin/env bun
/**
 * Gemini CLIレビュー実行をログ記録する（gemini-review-checkと連携）
 *
 * Why:
 *   gemini-review-checkがPR作成/push前にレビュー実行済みかを確認するため、
 *   レビュー実行時にブランチ・コミット情報を記録しておく必要がある。
 *
 * What:
 *   - gemini /code-reviewコマンドを検出
 *   - パイプ入力（| gemini -p / | gemini --prompt）を検出
 *   - ブランチ名、コミットハッシュ、diffハッシュを記録
 *   - main/masterブランチでは記録しない
 *
 * State:
 *   - writes: .claude/logs/markers/gemini-review-{branch}.done
 *
 * Remarks:
 *   - 記録型フック（ブロックしない、マーカーファイル書き込み）
 *   - PreToolUse:Bashで発火（gemini /code-reviewコマンド）
 *   - gemini_review_check.tsと連携（マーカーファイル参照元）
 *   - diffハッシュ記録によりリベース後のスキップ判定が可能
 *
 * Changelog:
 *   - silenvx/dekita#2926: コマンドチェーン形式（cd && gemini）のサポートを追加
 *   - silenvx/dekita#2921: 非対話モード（gemini "/code-review"）の検出に対応
 *   - silenvx/dekita#2856: TypeScript版初期実装
 *   - silenvx/dekita#2911: パイプ入力パターン検出を追加
 */

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { formatError } from "../lib/format_error";
import { getCurrentBranch, getHeadCommitFull, getOriginDefaultBranch } from "../lib/git";
import { getMarkersDir } from "../lib/markers";
import { approveAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { sanitizeBranchName, stripQuotedStrings } from "../lib/strings";

/** Pattern to detect 'gemini /code-review' commands (unquoted) */
export const GEMINI_REVIEW_PATTERN = /gemini\s+\/code-review/;

/** Pattern to detect 'gemini "/code-review"' or 'gemini '/code-review'' commands (quoted) */
export const GEMINI_REVIEW_PATTERN_QUOTED = /gemini\s+(['"])\/code-review\1/;

/** Pattern to detect pipe input to gemini with -p or --prompt flag */
export const GEMINI_PIPE_PATTERN = /\|\s*gemini\s+(-p|--prompt)\s+/;

/**
 * Strip leading environment variable assignments
 *
 * @example
 * stripEnvPrefixes('GEMINI_API_KEY=xxx gemini /code-review') // 'gemini /code-review'
 * stripEnvPrefixes('PATH=/usr/bin KEY=val gemini /code-review') // 'gemini /code-review'
 *
 * @note Does not handle quoted values with spaces (e.g., FOO="bar baz" gemini).
 *       This is an acceptable limitation as such patterns are rare in practice.
 */
export function stripEnvPrefixes(command: string): string {
  // Match leading env var assignments: VAR=value (value may contain =, quotes, etc.)
  // Stops at first word that doesn't contain =
  return command.replace(/^(\s*\w+=\S*\s*)+/, "");
}

/**
 * Extract all commands from a command chain
 * Handles &&, ;, and || operators
 *
 * @example
 * extractAllCommands('cd repo && gemini /code-review') // ['cd repo', 'gemini /code-review']
 * extractAllCommands('gemini /code-review && echo done') // ['gemini /code-review', 'echo done']
 * extractAllCommands('export FOO=bar; gemini /code-review') // ['export FOO=bar', 'gemini /code-review']
 */
export function extractAllCommands(command: string): string[] {
  const operators = ["&&", "||", ";"];
  const commands: string[] = [];
  let currentStart = 0;
  let inQuote: string | null = null;

  for (let i = 0; i < command.length; i++) {
    const char = command[i];

    if (inQuote) {
      if (char === inQuote) {
        // Count consecutive backslashes before the quote
        let backslashCount = 0;
        for (let j = i - 1; j >= 0 && command[j] === "\\"; j--) {
          backslashCount++;
        }
        // Quote is escaped only if preceded by odd number of backslashes
        if (backslashCount % 2 === 0) {
          inQuote = null;
        }
      }
      continue;
    }

    if (char === '"' || char === "'") {
      inQuote = char;
      continue;
    }

    // Check for operators at this position
    for (const op of operators) {
      if (command.slice(i, i + op.length) === op) {
        const segment = command.slice(currentStart, i).trim();
        if (segment) {
          commands.push(segment);
        }
        currentStart = i + op.length;
        i += op.length - 1; // Skip operator chars (loop will increment by 1)
        break;
      }
    }
  }

  // Add the last segment
  const lastSegment = command.slice(currentStart).trim();
  if (lastSegment) {
    commands.push(lastSegment);
  }

  return commands.length > 0 ? commands : [command];
}

/**
 * Strip common command wrappers like time, nice, etc.
 *
 * @example
 * stripCommandWrappers('time gemini /code-review') // 'gemini /code-review'
 * stripCommandWrappers('nice -n 10 gemini /code-review') // 'gemini /code-review'
 * stripCommandWrappers('sudo gemini /code-review') // 'gemini /code-review'
 * stripCommandWrappers('env gemini /code-review') // 'gemini /code-review'
 * stripCommandWrappers('command gemini /code-review') // 'gemini /code-review'
 */
export function stripCommandWrappers(command: string): string {
  // Common command wrappers that might precede gemini
  // time: measures execution time
  // nice: adjusts process priority
  // nohup: runs command immune to hangups
  // timeout: runs command with time limit (supports floating point like 1.5s)
  // sudo: executes as superuser (with optional flags)
  // env: executes in modified environment (with optional VAR=value assignments)
  // command: runs command ignoring shell functions and aliases
  const wrapperPattern =
    /^(time|nice(\s+-n\s+\d+)?|nohup|timeout(\s+\d+(\.\d+)?[smhd]?)?|sudo(\s+-[a-zA-Z]+(\s+\S+)?)*|env(\s+\w+=\S*)*|command)\s+/;
  let result = command;
  let prev = "";

  // Repeatedly strip wrappers (in case of chained wrappers like "nice time gemini")
  while (result !== prev) {
    prev = result;
    result = result.replace(wrapperPattern, "");
  }

  return result;
}

/**
 * Strip env prefixes and command wrappers iteratively until no more can be removed
 * Handles combinations like: GEMINI_API_KEY=xxx time gemini /code-review
 *
 * @example
 * stripPrefixesAndWrappers('GEMINI_API_KEY=xxx time gemini /code-review') // 'gemini /code-review'
 * stripPrefixesAndWrappers('time GEMINI_API_KEY=xxx gemini /code-review') // 'gemini /code-review'
 */
export function stripPrefixesAndWrappers(command: string): string {
  let result = command;
  let prev = "";

  // Iterate until no more changes occur
  while (result !== prev) {
    prev = result;
    result = stripEnvPrefixes(result);
    result = stripCommandWrappers(result);
  }

  return result;
}

/**
 * Check if a single command (no chain operators) is a gemini /code-review command
 * @internal Used by isGeminiReviewCommand
 */
function isSingleGeminiReviewCommand(singleCommand: string): boolean {
  // Iteratively strip env prefixes and wrappers
  // Handles: GEMINI_API_KEY=xxx time gemini /code-review
  const cleaned = stripPrefixesAndWrappers(singleCommand);

  // Command must start with 'gemini ' after stripping prefixes
  // This prevents false positives like: echo gemini "/code-review" and excludes 'gemini-cli'
  if (!cleaned.startsWith("gemini ")) {
    return false;
  }

  // Check for quoted form first: gemini "/code-review" or gemini '/code-review'
  if (GEMINI_REVIEW_PATTERN_QUOTED.test(cleaned)) {
    return true;
  }

  // Fall back to unquoted form: gemini /code-review
  // Strip quoted strings to avoid false positives in arguments
  const stripped = stripQuotedStrings(cleaned);
  return GEMINI_REVIEW_PATTERN.test(stripped);
}

/**
 * Check if command is a gemini /code-review command
 * Handles both quoted and unquoted forms while avoiding false positives
 * Also handles command chains (&&, ;, ||) and common wrappers (time, nice, etc.)
 */
export function isGeminiReviewCommand(command: string): boolean {
  const trimmed = command.trim();
  if (!trimmed) {
    return false;
  }

  // Extract all commands from the chain and check if ANY is a gemini review command
  // This handles both:
  // - "cd repo && gemini /code-review" (gemini at end)
  // - "gemini /code-review && echo done" (gemini at start/middle)
  const commands = extractAllCommands(trimmed);
  return commands.some((cmd) => isSingleGeminiReviewCommand(cmd));
}

/**
 * Check if command pipes input to gemini with -p or --prompt flag
 * This is an alternative to 'gemini /code-review' when the extension fails
 *
 * @example
 * isGeminiPipeCommand('git diff | gemini -p "Review this"') // true
 * isGeminiPipeCommand('git diff | gemini --prompt "Review"') // true
 * isGeminiPipeCommand('gemini /code-review') // false
 */
export function isGeminiPipeCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  const stripped = stripQuotedStrings(command);
  return GEMINI_PIPE_PATTERN.test(stripped);
}

/**
 * Get diff hash for detecting unchanged diffs after rebase
 */
export async function getDiffHash(): Promise<string | null> {
  try {
    const originBranch = await getOriginDefaultBranch(process.cwd());
    const proc = Bun.spawn(["git", "diff", `${originBranch}...HEAD`], {
      stdout: "pipe",
      stderr: "pipe",
    });
    const output = await new Response(proc.stdout).text();
    await proc.exited;
    if (!output.trim()) {
      return null;
    }
    return createHash("sha256").update(output).digest("hex").slice(0, 16);
  } catch {
    return null;
  }
}

/**
 * Log review execution to marker file
 */
export function logReviewExecution(
  branch: string,
  commit: string | null,
  diffHash: string | null,
): void {
  const markersDir = getMarkersDir();
  if (!existsSync(markersDir)) {
    mkdirSync(markersDir, { recursive: true });
  }

  const safeBranch = sanitizeBranchName(branch);
  const logFile = `${markersDir}/gemini-review-${safeBranch}.done`;

  let content: string;
  if (commit && diffHash) {
    content = `${branch}:${commit}:${diffHash}`;
  } else if (commit) {
    content = `${branch}:${commit}`;
  } else {
    content = branch;
  }

  writeFileSync(logFile, content);
}

/**
 * メイン処理
 */
async function main(): Promise<void> {
  try {
    const input = await parseHookInput();
    const toolInput = input.tool_input as Record<string, unknown> | undefined;
    const command = (toolInput?.command as string) ?? "";

    // Detect gemini /code-review command or pipe input (excluding quoted strings)
    if (isGeminiReviewCommand(command) || isGeminiPipeCommand(command)) {
      const branch = await getCurrentBranch();
      if (branch && branch !== "main" && branch !== "master") {
        const commit = await getHeadCommitFull();
        const diffHash = await getDiffHash();
        logReviewExecution(branch, commit, diffHash);
      }
    }

    // Always approve - this hook only logs
    approveAndExit("gemini-review-logger");
  } catch (error) {
    console.error(`[gemini-review-logger] Hook error: ${formatError(error)}`);
    approveAndExit("gemini-review-logger");
  }
}

// 実行（テスト時はスキップ）
if (import.meta.main) {
  main();
}
