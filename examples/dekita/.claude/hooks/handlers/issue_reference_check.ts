#!/usr/bin/env bun
/**
 * 存在しないIssue参照をブロックする。
 *
 * Why:
 *   存在しないIssue番号を参照すると、後から追跡できなくなり
 *   誤った情報がコメントに残る。参照前に存在確認を強制する。
 *
 * What:
 *   - gh pr comment/gh issue comment等のコメント投稿を検出
 *   - コメント本文から#1234形式のIssue参照を抽出
 *   - gh issue viewで存在確認、不存在ならブロック
 *
 * Remarks:
 *   - ブロック型フック（存在しないIssue参照時はブロック）
 *   - PreToolUse:Bashで発火（gh pr/issue comment、gh api replies）
 *   - Closes/Fixes/Resolvesパターンは除外（Issue作成用途）
 *   - cross-repoコマンド（--repo）はスキップ（他リポジトリ検証困難）
 *
 * Changelog:
 *   - silenvx/dekita#2059: フック追加
 *   - silenvx/dekita#2932: TypeScriptに移行
 */

import { extractCommentBody } from "../lib/command";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "issue-reference-check";

// Pattern to detect Issue references: #1234
const ISSUE_REF_PATTERN = /#(\d+)/g;

// Pattern to detect Closes/Fixes/Resolves keywords (allowed even for non-existent Issues)
const CLOSES_PATTERN = /(?:closes?|fixes?|resolves?)\s*#\d+/gi;

// Pattern to detect PR references (allowed, validated differently from Issues)
const PR_PATTERN = /PR\s*#\d+/gi;

// Patterns to detect comment commands
const COMMENT_COMMAND_PATTERNS = [
  // gh pr comment
  /gh\s+pr\s+comment\b/i,
  // gh api .../replies
  /gh\s+api\s+.*?\/replies\b/i,
  // gh api graphql with addPullRequestReviewThreadReply
  /gh\s+api\s+graphql.*addPullRequestReviewThreadReply/is,
  // gh issue comment
  /gh\s+issue\s+comment\b/i,
];

/**
 * Check if the command is a comment-posting command.
 */
export function isCommentCommand(command: string): boolean {
  return COMMENT_COMMAND_PATTERNS.some((pattern) => pattern.test(command));
}

/**
 * Check if an Issue exists using gh CLI.
 *
 * Returns True (fail-open) for any error except explicit "not found".
 */
export async function checkIssueExists(issueNumber: number): Promise<boolean> {
  try {
    const proc = Bun.spawn(["gh", "issue", "view", String(issueNumber), "--json", "number"], {
      stdout: "pipe",
      stderr: "pipe",
    });

    const timeoutPromise = new Promise<never>((_, reject) => {
      setTimeout(() => reject(new Error("timeout")), TIMEOUT_MEDIUM * 1000);
    });

    const exitCode = await Promise.race([proc.exited, timeoutPromise]);

    if (exitCode === 0) {
      return true;
    }

    // Only return False if stderr indicates "not found" (Issue doesn't exist)
    // Other errors (network, auth, API) should fail-open
    const stderr = await new Response(proc.stderr).text();
    const stderrLower = stderr.toLowerCase();
    if (stderrLower.includes("not found") || stderrLower.includes("could not resolve")) {
      return false;
    }

    // Unknown error - fail-open to avoid blocking valid comments
    return true;
  } catch {
    // On any error, assume Issue exists to avoid false positives (fail-open)
    return true;
  }
}

/**
 * Extract Issue numbers from text, excluding Closes/Fixes and PR patterns.
 */
export function extractIssueReferences(text: string): number[] {
  // Remove Closes/Fixes patterns from text first
  const textWithoutCloses = text.replace(CLOSES_PATTERN, "");

  // Remove PR #xxx patterns (PRs are validated differently from Issues)
  const textWithoutPr = textWithoutCloses.replace(PR_PATTERN, "");

  // Find all Issue references
  const matches: number[] = [];
  for (const match of textWithoutPr.matchAll(ISSUE_REF_PATTERN)) {
    matches.push(Number.parseInt(match[1], 10));
  }

  return [...new Set(matches)]; // Deduplicate
}

async function main(): Promise<void> {
  let inputData: Awaited<ReturnType<typeof parseHookInput>>;
  let sessionId: string | undefined;
  try {
    inputData = await parseHookInput();
    sessionId = inputData.session_id;
  } catch {
    // Fail open
    return;
  }

  const toolName = inputData.tool_name ?? "";

  // Only check Bash commands
  if (toolName !== "Bash") {
    await logHookExecution(HOOK_NAME, "approve", "not Bash", undefined, { sessionId });
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  const toolInput = inputData.tool_input ?? {};
  const command = (toolInput as { command?: string }).command ?? "";

  // Check if it's a comment command
  if (!isCommentCommand(command)) {
    await logHookExecution(HOOK_NAME, "approve", "not comment command", undefined, { sessionId });
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Skip validation for cross-repo commands (--repo flag)
  // We can't reliably check Issues in other repos, so fail-open
  if (/--repo\s+\S+/.test(command)) {
    await logHookExecution(HOOK_NAME, "approve", "cross-repo command", undefined, { sessionId });
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Extract comment body
  const body = await extractCommentBody(command);
  if (!body) {
    await logHookExecution(HOOK_NAME, "approve", "no body found", undefined, { sessionId });
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Extract Issue references
  const issueNumbers = extractIssueReferences(body);
  if (issueNumbers.length === 0) {
    await logHookExecution(HOOK_NAME, "approve", "no Issue refs", undefined, { sessionId });
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Check each Issue exists
  const nonExistent: number[] = [];
  for (const issueNum of issueNumbers) {
    const exists = await checkIssueExists(issueNum);
    if (!exists) {
      nonExistent.push(issueNum);
    }
  }

  if (nonExistent.length > 0) {
    const issuesStr = nonExistent.map((n) => `#${n}`).join(", ");
    const reason = `存在しないIssueを参照しています: ${issuesStr}

Issueを参照する前に、まず \`gh issue create\` で作成してください。
Issue作成後、実際のIssue番号を使用してコメントを再投稿してください。

背景: Issue番号を推測して参照すると、
実際のIssue番号との不一致が発生します（Issue #2059）。`;

    await logHookExecution(
      HOOK_NAME,
      "block",
      reason,
      {
        non_existent: nonExistent,
        command: command.slice(0, 100),
      },
      { sessionId },
    );
    console.log(JSON.stringify(makeBlockResult(HOOK_NAME, reason)));
    return;
  }

  // All Issues exist
  await logHookExecution(
    HOOK_NAME,
    "approve",
    `verified: ${JSON.stringify(issueNumbers)}`,
    undefined,
    { sessionId },
  );
  console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
}

if (import.meta.main) {
  main().catch((error) => {
    console.error(`[${HOOK_NAME}] Error:`, error);
    process.exit(0); // Fail open
  });
}
