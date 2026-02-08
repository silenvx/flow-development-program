#!/usr/bin/env bun
/**
 * Issue参照なしの「フォローアップ」発言をブロックする。
 *
 * Why:
 *   「後で対応します」とコメントしてもIssueを作らないと、フォローアップは
 *   忘れられて実行されない。コメント投稿前にIssue参照を強制することで、
 *   約束を形骸化させないようにする。
 *
 * What:
 *   - gh pr/issue commentコマンドを検出
 *   - 「後で」「フォローアップ」「スコープ外」等のキーワードを検索
 *   - Issue参照（#1234等）がない場合はブロック
 *   - Issue参照がある場合は許可
 *
 * Remarks:
 *   - SKIP_FOLLOWUP_ISSUE_GUARD=1で無効化可能
 *   - 対象はコメントコマンドのみ（コミットメッセージは対象外）
 *
 * Changelog:
 *   - silenvx/dekita#1496: フック追加
 *   - silenvx/dekita#2932: TypeScriptに移行
 */

import { extractCommentBodySync } from "../lib/command";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { isSkipEnvEnabled } from "../lib/strings";

const HOOK_NAME = "followup-issue-guard";
const SKIP_ENV_VAR = "SKIP_FOLLOWUP_ISSUE_GUARD";

// Keywords that indicate a "follow up later" statement
const FOLLOWUP_KEYWORDS: RegExp[] = [
  /後で/,
  /将来/,
  /フォローアップ/,
  /別途/,
  /今後.*対応/,
  /今後.*検討/,
  /スコープ外/,
  /scope\s*外/i,
  /out\s*of\s*scope/i,
  /later\b/i,
  /future\b/i,
  /follow[\s-]*up/i,
];

// Pattern to match Issue references: #1234, Issue #1234, issue-1234, etc.
const ISSUE_REF_PATTERN = /(?:#\d+|Issue\s*#?\d+|issue-\d+)/i;

/**
 * Check if the command is a gh comment command.
 */
export function isCommentCommand(command: string): boolean {
  // Match: gh pr comment, gh issue comment, gh api ...comments
  // Only match at the start of command or after command separators (&&, ;, |)
  // Avoid matching inside quoted strings
  const patterns = [
    /(?:^|&&|;|\|)\s*gh\s+pr\s+comment\b/i,
    /(?:^|&&|;|\|)\s*gh\s+issue\s+comment\b/i,
    /(?:^|&&|;|\|)\s*gh\s+api\s+.*comments/i,
  ];
  return patterns.some((pattern) => pattern.test(command));
}

/**
 * Check if text contains follow-up keywords.
 */
export function containsFollowupKeyword(text: string): { found: boolean; keyword: string | null } {
  for (const keyword of FOLLOWUP_KEYWORDS) {
    if (keyword.test(text)) {
      return { found: true, keyword: keyword.source };
    }
  }
  return { found: false, keyword: null };
}

/**
 * Check if text contains an Issue reference.
 */
export function containsIssueReference(text: string): boolean {
  return ISSUE_REF_PATTERN.test(text);
}

async function main(): Promise<void> {
  let inputData: Awaited<ReturnType<typeof parseHookInput>>;
  let sessionId: string | undefined;
  try {
    inputData = await parseHookInput();
    sessionId = inputData.session_id;
  } catch {
    // Fail open: allow on parse errors
    return;
  }

  // Skip if env var is set
  if (isSkipEnvEnabled(process.env[SKIP_ENV_VAR])) {
    await logHookExecution(HOOK_NAME, "skip", `${SKIP_ENV_VAR}=1: チェックをスキップ`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Only process Bash tool
  const toolName = inputData.tool_name ?? "";
  if (toolName !== "Bash") {
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  const toolInput = inputData.tool_input ?? {};
  const command = (toolInput as { command?: string }).command ?? "";

  // Only check comment commands
  if (!isCommentCommand(command)) {
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Extract comment body
  const commentBody = extractCommentBodySync(command);
  if (!commentBody) {
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Check for follow-up keywords
  const { found: hasFollowup, keyword: matchedKeyword } = containsFollowupKeyword(commentBody);
  if (!hasFollowup) {
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Check for Issue reference
  if (containsIssueReference(commentBody)) {
    // Has Issue reference, approve
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Follow-up comment with Issue reference: ${matchedKeyword}`,
      undefined,
      { sessionId },
    );
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Block: follow-up keyword without Issue reference
  const reason = `「フォローアップ」発言にIssue参照がありません。

検出されたキーワード: ${matchedKeyword}

コメント本文にIssue番号が含まれていません。
「後で対応」と言う前に、必ずIssueを作成してください。

対処方法:
1. まずIssueを作成:
   gh issue create --title "<タイトル>" --body "<内容>"

2. Issue番号を含めてコメントを再投稿:
   例: "Issue #1234 を作成しました。今後のフォローアップとして対応します。"

参照: AGENTS.md「後でフォローアップ」発言時のIssue作成（必須）`;

  await logHookExecution(
    HOOK_NAME,
    "block",
    `Follow-up comment without Issue reference: ${matchedKeyword}`,
    {
      command: command.slice(0, 200),
      matched_keyword: matchedKeyword,
    },
    { sessionId },
  );

  console.log(JSON.stringify(makeBlockResult(HOOK_NAME, reason)));
}

if (import.meta.main) {
  main().catch((error) => {
    console.error(`[${HOOK_NAME}] Error:`, error);
    process.exit(0); // Fail open
  });
}
