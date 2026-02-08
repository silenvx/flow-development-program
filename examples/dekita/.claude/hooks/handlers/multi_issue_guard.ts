#!/usr/bin/env bun
/**
 * 1つのworktree/PRで複数Issueを同時に対応しようとした場合に警告する。
 *
 * Why:
 *   1 worktree = 1 Issue、1 PR = 1 Issueの原則を維持することで、
 *   変更の追跡性とレビューの容易さを確保する。
 *
 * What:
 *   - git worktree addのブランチ名/パスから複数Issue番号を検出
 *   - gh pr createのbodyから複数Closes/Fixes/Resolvesを検出
 *   - 複数Issueを検出したらsystemMessageで警告
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、正当なケースがあるため）
 *   - PreToolUse:Bashで発火（git worktree add、gh pr create）
 *   - issue-auto-assignは単一Issue検出（責務分離）
 *   - 関連Issue同時修正や親子関係など正当なケースも存在
 *
 * Changelog:
 *   - silenvx/dekita#2932: TypeScriptに移行
 */

import { extractPrBody } from "../lib/command";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { extractInlineSkipEnv, isSkipEnvEnabled } from "../lib/strings";

const SKIP_ENV = "SKIP_MULTI_ISSUE_GUARD";
const HOOK_NAME = "multi-issue-guard";

interface CheckResult {
  warn: boolean;
  message: string;
  issues?: number[];
}

/**
 * Extract all unique Issue numbers from text.
 *
 * Uses the same patterns as issue-auto-assign but collects all matches.
 */
export function extractAllIssueNumbers(text: string): number[] {
  const patterns = [
    /#(\d+)/g, // #123
    /issue[_-](\d+)/gi, // issue-123, issue_123
    /\/(\d+)[-_]/g, // /123-description
    /[-_](\d+)[-_]/g, // feature-123-name
    /[-_](\d+)$/g, // feature-123 (at end)
  ];

  const issueNumbers = new Set<number>();
  for (const pattern of patterns) {
    // Use matchAll to avoid assignment in expression
    for (const match of text.matchAll(pattern)) {
      issueNumbers.add(Number.parseInt(match[1], 10));
    }
  }

  return [...issueNumbers].sort((a, b) => a - b);
}

/**
 * Extract Issue numbers from closing keywords in PR body.
 */
export function extractClosingIssueNumbers(body: string): number[] {
  const pattern = /(?:closes|fixes|resolves)\s*#?(\d+)/gi;
  const matches: number[] = [];
  // Use matchAll to avoid assignment in expression
  for (const match of body.matchAll(pattern)) {
    matches.push(Number.parseInt(match[1], 10));
  }
  return [...new Set(matches)].sort((a, b) => a - b);
}

/**
 * git worktree addのうち、引数を取る既知のオプション。
 *
 * これらのオプションは「次のトークン」を引数として消費するため、
 * パス/ブランチ名として誤認識しないよう、パース時にスキップする。
 *
 * 制限事項:
 *   - `--track=direct` のような `--opt=value` 形式は、1トークンで完結するため
 *     ここでは特別扱いしていない（`--track` のみを次トークン消費対象として扱う）。
 *   - 将来 git に新しい引数付きオプションが追加された場合は、
 *     必要に応じてこのセットを更新すること。
 */
const OPTIONS_WITH_ARG = new Set(["-b", "-B", "--reason", "--orphan"]);

/**
 * Tokenize a shell command, respecting quoted strings and escape sequences.
 *
 * Shell escape rules:
 *   - Single quotes: No escapes processed (all characters literal)
 *   - Double quotes: Only $ ` " \ \n are escapable
 *   - Unquoted: All backslash escapes processed
 *   - Line continuation: \+newline removed (outside double quotes)
 *   - Trailing backslash: Preserved as literal (shell would error/wait)
 */
function tokenizeCommand(command: string): string[] {
  const tokens: string[] = [];
  let current = "";
  let inSingleQuote = false;
  let inDoubleQuote = false;

  for (let i = 0; i < command.length; i++) {
    const char = command[i];

    // Handle backslash escapes (outside single quotes)
    // ダブルクォート内では $ ` " \ \n のみがエスケープ対象
    // その他の文字（例: \P）はバックスラッシュがリテラルとして保持される
    if (char === "\\" && !inSingleQuote) {
      if (i + 1 < command.length) {
        const nextChar = command[i + 1];
        // Line continuation (backslash + newline) - remove both characters
        // シェルでは行継続として処理される（クォート外でも内でも）
        if (nextChar === "\n" && !inDoubleQuote) {
          i++;
          continue;
        }
        // Inside double quotes, only specific characters are escaped
        if (inDoubleQuote && !["$", "`", '"', "\\", "\n"].includes(nextChar)) {
          current += "\\";
          continue;
        }
        i++;
        current += command[i];
      } else {
        // Trailing backslash: preserve as literal
        // (shell would error or wait for more input)
        current += "\\";
      }
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

    // Split on any whitespace (space, tab, newline)
    if (
      (char === " " || char === "\t" || char === "\n" || char === "\r") &&
      !inSingleQuote &&
      !inDoubleQuote
    ) {
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

/**
 * Parse git worktree add command and extract branch name and path.
 */
export function parseWorktreeAddCommand(command: string): {
  branchName: string | null;
  worktreePath: string | null;
} {
  if (!command.includes("git worktree add")) {
    return { branchName: null, worktreePath: null };
  }

  let branchName: string | null = null;
  let worktreePath: string | null = null;

  // Tokenize command respecting quotes
  const parts = tokenizeCommand(command);
  const addIdx = parts.indexOf("add");
  if (addIdx === -1) {
    return { branchName, worktreePath };
  }

  // Collect positional arguments (non-option arguments after 'add')
  const positionalArgs: string[] = [];
  let expectingBranch = false;
  let skipNext = false;
  for (const part of parts.slice(addIdx + 1)) {
    if (skipNext) {
      if (expectingBranch) {
        branchName = part;
        expectingBranch = false;
      }
      skipNext = false;
      continue;
    }
    if (part.startsWith("-")) {
      // Check if this option takes an argument
      if (OPTIONS_WITH_ARG.has(part)) {
        skipNext = true;
        if (part === "-b" || part === "-B" || part === "--orphan") {
          expectingBranch = true;
        }
      }
      continue;
    }
    positionalArgs.push(part);
  }

  if (positionalArgs.length >= 1) {
    worktreePath = positionalArgs[0];
  }

  if (positionalArgs.length >= 2 && !branchName) {
    branchName = positionalArgs[1];
  }

  return { branchName, worktreePath };
}

// extractPrBody is imported from ../lib/command

/**
 * Check git worktree add command for multiple Issues.
 */
export function checkWorktreeCommand(command: string): CheckResult {
  if (!command.includes("git worktree add")) {
    return { warn: false, message: "" };
  }

  const { branchName, worktreePath } = parseWorktreeAddCommand(command);

  // Collect Issue numbers from both branch and path
  const allIssues = new Set<number>();

  if (branchName) {
    for (const issue of extractAllIssueNumbers(branchName)) {
      allIssues.add(issue);
    }
  }

  if (worktreePath) {
    for (const issue of extractAllIssueNumbers(worktreePath)) {
      allIssues.add(issue);
    }
  }

  if (allIssues.size > 1) {
    const sortedIssues = [...allIssues].sort((a, b) => a - b);
    const issueList = sortedIssues.map((i) => `#${i}`).join(", ");
    const message = `⚠️ 複数Issueの同時対応を検出: ${issueList}

1 worktree = 1 Issue を推奨します。
本当に複数Issueを同時に対応しますか？

意図的な場合は続行してください。`;
    return { warn: true, message, issues: sortedIssues };
  }

  return { warn: false, message: "" };
}

/**
 * Check gh pr create command for multiple closing keywords.
 */
export function checkPrCommand(command: string): CheckResult {
  if (!command.includes("gh pr create")) {
    return { warn: false, message: "" };
  }

  const body = extractPrBody(command);
  if (!body) {
    return { warn: false, message: "" };
  }

  const issueNumbers = extractClosingIssueNumbers(body);

  if (issueNumbers.length > 1) {
    const issueList = issueNumbers.map((i) => `#${i}`).join(", ");
    const message = `⚠️ 複数Issueを同時にクローズしようとしています: ${issueList}

1 PR = 1 Issue を推奨します。
複数Issueを同時にクローズするのは意図的ですか？

意図的な場合は続行してください。`;
    return { warn: true, message, issues: issueNumbers };
  }

  return { warn: false, message: "" };
}

async function main(): Promise<void> {
  let inputData: Awaited<ReturnType<typeof parseHookInput>>;
  let sessionId: string | undefined;
  try {
    inputData = await parseHookInput();
    const ctx = createHookContext(inputData);
    sessionId = ctx.sessionId;
  } catch {
    // Fail open: allow on parse errors
    return;
  }

  const toolName = inputData.tool_name ?? "";
  if (toolName !== "Bash") {
    return; // Not a Bash command
  }

  const toolInput = inputData.tool_input ?? {};
  const command = (toolInput as { command?: string }).command ?? "";

  if (!command) {
    return; // Empty command
  }

  // Check SKIP environment variable (exported)
  if (isSkipEnvEnabled(process.env[SKIP_ENV])) {
    await logHookExecution(HOOK_NAME, "skip", `${SKIP_ENV}=1: チェックをスキップ`, undefined, {
      sessionId,
    });
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
    return;
  }

  // Check inline SKIP environment variable
  const inlineValue = extractInlineSkipEnv(command, SKIP_ENV);
  if (isSkipEnvEnabled(inlineValue)) {
    await logHookExecution(
      HOOK_NAME,
      "skip",
      `${SKIP_ENV}=1: チェックをスキップ（インライン）`,
      undefined,
      { sessionId },
    );
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
    return;
  }

  // Check worktree add command
  const worktreeResult = checkWorktreeCommand(command);
  if (worktreeResult.warn) {
    await logHookExecution(
      HOOK_NAME,
      "warn",
      `複数Issue検出（worktree）: ${JSON.stringify(worktreeResult.issues)}`,
      undefined,
      { sessionId },
    );
    const result = { systemMessage: worktreeResult.message };
    console.log(JSON.stringify(result));
    return;
  }

  // Check PR create command
  const prResult = checkPrCommand(command);
  if (prResult.warn) {
    await logHookExecution(
      HOOK_NAME,
      "warn",
      `複数Issue検出（PR）: ${JSON.stringify(prResult.issues)}`,
      undefined,
      { sessionId },
    );
    const result = { systemMessage: prResult.message };
    console.log(JSON.stringify(result));
    return;
  }

  // No warning needed - output nothing (per hooks-reference pattern)
}

if (import.meta.main) {
  main().catch((error) => {
    console.error(`[${HOOK_NAME}] Error:`, error);
    process.exit(0); // Fail open
  });
}
