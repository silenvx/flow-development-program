#!/usr/bin/env bun
/**
 * gh issue close時に未完了チェックボックスを検出してブロック。
 *
 * Why:
 *   Issue本文にタスクリスト（チェックボックス）がある場合、
 *   未完了項目があるままクローズすると作業漏れが発生する。
 *   部分完了でのクローズを防止する。
 *
 * What:
 *   - gh issue closeコマンドを検出
 *   - Issue本文からチェックボックスを解析
 *   - 未チェック項目があればブロック
 *   - スキップ環境変数（SKIP_INCOMPLETE_CHECK）で回避可能
 *
 * Remarks:
 *   - ブロック型フック
 *   - issue-review-response-checkはAIレビュー対応確認、本フックはタスク完了確認
 *   - Python版: issue_incomplete_close_check.py
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#956: スキップ環境変数の値検証を統一
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { extractInlineSkipEnv, isSkipEnvEnabled, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "issue-incomplete-close-check";
const SKIP_ENV_NAME = "SKIP_INCOMPLETE_CHECK";

/**
 * Extract issue number from gh issue close command.
 */
export function extractIssueNumber(command: string): string | null {
  const cmd = stripQuotedStrings(command);

  // Check if this is a gh issue close command
  if (!/gh\s+issue\s+close\b/.test(cmd)) {
    return null;
  }

  // Extract all arguments after "gh issue close"
  const match = cmd.match(/gh\s+issue\s+close\s+(.+)/);
  if (!match) {
    return null;
  }

  const args = match[1];

  // Find issue number (with or without #) among the arguments
  for (const part of args.split(/\s+/)) {
    if (part.startsWith("-")) {
      continue;
    }
    const numMatch = part.match(/^#?(\d+)$/);
    if (numMatch) {
      return numMatch[1];
    }
  }

  return null;
}

/**
 * Fetch issue body from GitHub.
 */
function getIssueBody(issueNumber: string): string | null {
  try {
    const result = execSync(`gh issue view ${issueNumber} --json body --jq '.body'`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return result;
  } catch {
    return null;
  }
}

/**
 * Parse checkboxes from issue body.
 *
 * @returns Tuple of [checked_items, unchecked_items]
 */
export function parseCheckboxes(body: string): [string[], string[]] {
  const checked: string[] = [];
  const unchecked: string[] = [];

  // Match checkbox patterns: - [ ] or - [x] or - [X]
  // Also match * [ ] variants
  const checkboxPattern = /^[\s]*[-*]\s+\[([ xX])\]\s+(.+)$/gm;

  let match: RegExpExecArray | null = checkboxPattern.exec(body);
  while (match !== null) {
    const state = match[1];
    let text = match[2].trim();

    // Truncate long text
    if (text.length > 80) {
      text = `${text.slice(0, 77)}...`;
    }

    if (state.toLowerCase() === "x") {
      checked.push(text);
    } else {
      unchecked.push(text);
    }

    match = checkboxPattern.exec(body);
  }

  return [checked, unchecked];
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolName = (data.tool_name as string) || "";

    // Only check Bash commands
    if (toolName !== "Bash") {
      await logHookExecution(HOOK_NAME, "approve", `not Bash: ${toolName}`, undefined, {
        sessionId,
      });
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    const toolInput = (data.tool_input as Record<string, unknown>) || {};
    const command = (toolInput.command as string) || "";

    // Check for skip environment variable (Issue #956: consistent value validation)
    if (isSkipEnvEnabled(process.env[SKIP_ENV_NAME])) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "SKIP_INCOMPLETE_CHECK でスキップ（環境変数）",
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    const inlineValue = extractInlineSkipEnv(command, SKIP_ENV_NAME);
    if (isSkipEnvEnabled(inlineValue)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "SKIP_INCOMPLETE_CHECK でスキップ（インライン）",
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // Check if this is a gh issue close command
    const issueNumber = extractIssueNumber(command);
    if (!issueNumber) {
      await logHookExecution(HOOK_NAME, "approve", "no issue number found", undefined, {
        sessionId,
      });
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // Fetch issue body
    const body = getIssueBody(issueNumber);
    if (!body) {
      // Can't fetch body, don't block
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Issue #${issueNumber} の本文取得失敗`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // Parse checkboxes
    const [checked, unchecked] = parseCheckboxes(body);

    // No checkboxes at all - let it through
    if (checked.length === 0 && unchecked.length === 0) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Issue #${issueNumber} にチェックボックスなし`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // All checkboxes are checked - let it through
    if (unchecked.length === 0) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Issue #${issueNumber} の全項目完了 (${checked.length}件)`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // There are unchecked items - block
    let uncheckedList = unchecked
      .slice(0, 5)
      .map((item) => `- [ ] ${item}`)
      .join("\n");
    if (unchecked.length > 5) {
      uncheckedList += `\n... 他 ${unchecked.length - 5} 件`;
    }

    const blockMessage = `Issue #${issueNumber} に未完了項目があります。

**未完了 (${unchecked.length}件):**
${uncheckedList}

**完了済み (${checked.length}件)**

**対応方法:**
1. 残り項目を完了してからクローズ
2. 別Issueに分割: 残り項目を新Issueとして作成してからクローズ
3. 対応不要: コメントで理由を説明してからクローズ

**スキップ方法（確認済みの場合）:**
\`\`\`
SKIP_INCOMPLETE_CHECK=1 gh issue close ${issueNumber}
\`\`\``;

    await logHookExecution(
      HOOK_NAME,
      "block",
      `Issue #${issueNumber} に未完了項目 ${unchecked.length}件`,
      undefined,
      { sessionId },
    );

    const result = makeBlockResult(HOOK_NAME, blockMessage);
    console.log(JSON.stringify(result));
    process.exit(2);
  } catch (error) {
    // Don't block on errors - approve silently
    await logHookExecution(HOOK_NAME, "error", `フックエラー: ${formatError(error)}`, undefined, {
      sessionId,
    });
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
  }
}

if (import.meta.main) {
  main();
}
