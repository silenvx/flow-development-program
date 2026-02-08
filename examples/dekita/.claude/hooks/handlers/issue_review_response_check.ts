#!/usr/bin/env bun
/**
 * gh issue close時にAIレビューへの対応状況を確認してブロック。
 *
 * Why:
 *   Issue作成後のAIレビューで改善提案が出ても、対応せずにクローズすると
 *   Issue品質が低下する。レビュー対応を強制することでIssue品質を維持する。
 *
 * What:
 *   - gh issue closeコマンドを検出
 *   - IssueにAIレビューコメント（🤖 AI Review）があるか確認
 *   - レビュー後にIssue本文が更新されていなければブロック
 *   - スキップ環境変数（SKIP_REVIEW_RESPONSE）で回避可能
 *
 * Remarks:
 *   - ブロック型フック
 *   - 過去に実行されたAIレビュー（🤖 AI Review）への対応確認
 *   - コメントでの対応理由説明も有効な対応として扱う
 *   - Python版: issue_review_response_check.py
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#1024: SKIP_REVIEW_RESPONSE環境変数サポート
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { extractInlineSkipEnv, isSkipEnvEnabled, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "issue-review-response-check";
const SKIP_ENV_NAME = "SKIP_REVIEW_RESPONSE";

/**
 * Extract issue number from gh issue close command.
 */
export function extractIssueNumber(command: string): string | null {
  const cmd = stripQuotedStrings(command);

  if (!/gh\s+issue\s+close\b/.test(cmd)) {
    return null;
  }

  const match = cmd.match(/gh\s+issue\s+close\s+(.+)/);
  if (!match) {
    return null;
  }

  const args = match[1];

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
 * Get the timestamp of AI Review comment if exists.
 */
function getAiReviewCommentTime(issueNumber: string): Date | null {
  try {
    const result = execSync(
      `gh issue view ${issueNumber} --json comments --jq '.comments[] | select(.body | contains("🤖 AI Review")) | .createdAt'`,
      {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );

    if (!result.trim()) {
      return null;
    }

    // Get the latest (newest) AI Review comment
    const timestamps = result.trim().split("\n");
    if (timestamps.length > 0) {
      const lastTimestamp = timestamps[timestamps.length - 1];
      return new Date(lastTimestamp);
    }

    return null;
  } catch {
    return null;
  }
}

/**
 * Check if issue was updated after the given time.
 *
 * Uses issue's updated_at field to detect any activity after the AI Review.
 * This intentionally treats comments as valid responses.
 */
function wasIssueEditedAfter(issueNumber: string, afterTime: Date): boolean {
  try {
    const result = execSync(`gh api repos/:owner/:repo/issues/${issueNumber} --jq '.updated_at'`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    if (!result.trim()) {
      // No updated_at found, don't block
      return true;
    }

    const updatedAt = new Date(result.trim());
    return updatedAt > afterTime;
  } catch {
    // On error, don't block
    return true;
  }
}

/**
 * Extract bullet point suggestions from AI Review comment.
 */
function getAiReviewSuggestions(issueNumber: string): string[] {
  try {
    const result = execSync(
      `gh issue view ${issueNumber} --json comments --jq '.comments[] | select(.body | contains("🤖 AI Review")) | .body'`,
      {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );

    if (!result.trim()) {
      return [];
    }

    const body = result.trim();
    const suggestions: string[] = [];

    for (const line of body.split("\n")) {
      const trimmed = line.trim();
      if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
        // Skip very short suggestions
        if (trimmed.length > 10) {
          suggestions.push(trimmed.slice(0, 100));
          if (suggestions.length >= 3) {
            break;
          }
        }
      }
    }

    return suggestions;
  } catch {
    return [];
  }
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

    // Check if this is a gh issue close command
    const issueNumber = extractIssueNumber(command);

    // Check for skip environment variable
    if (issueNumber) {
      if (isSkipEnvEnabled(process.env[SKIP_ENV_NAME])) {
        await logHookExecution(
          HOOK_NAME,
          "approve",
          `SKIP_REVIEW_RESPONSE=1: Issue #${issueNumber} のAIレビュー対応チェックをスキップ`,
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
          `SKIP_REVIEW_RESPONSE=1: Issue #${issueNumber} のAIレビュー対応チェックをスキップ（インライン）`,
          undefined,
          { sessionId },
        );
        const result = makeApproveResult(HOOK_NAME);
        console.log(JSON.stringify(result));
        return;
      }
    }

    if (!issueNumber) {
      await logHookExecution(HOOK_NAME, "approve", "no issue number found", undefined, {
        sessionId,
      });
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // Check for AI Review comment
    const aiReviewTime = getAiReviewCommentTime(issueNumber);

    if (!aiReviewTime) {
      // No AI Review, let it through
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `AIレビューなし: Issue #${issueNumber}`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // Check if issue was edited after AI Review
    if (wasIssueEditedAfter(issueNumber, aiReviewTime)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `AIレビュー後に編集あり: Issue #${issueNumber}`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // Issue has AI Review but was not edited - block
    const suggestions = getAiReviewSuggestions(issueNumber);
    let suggestionText = "";
    if (suggestions.length > 0) {
      suggestionText = `\n\n**AIレビューの改善提案例:**\n${suggestions.join("\n")}`;
    }

    const reasonLines = [
      `⚠️ Issue #${issueNumber} にはAIレビューコメントがありますが、`,
      "レビュー後にIssue本文が更新されていません。",
      "",
      "**対応方法:**",
      `1. \`gh issue view ${issueNumber} --comments\` でAIレビューを確認`,
      `2. 改善提案をIssue本文に反映（\`gh issue edit ${issueNumber}\`）`,
      "3. 対応不要な提案は、その理由をコメントに記載",
      "4. その後、再度クローズを実行",
    ];

    const blockMessage = reasonLines.join("\n") + suggestionText;

    await logHookExecution(
      HOOK_NAME,
      "block",
      `AIレビュー未対応: Issue #${issueNumber}`,
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
