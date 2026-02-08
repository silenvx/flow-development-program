#!/usr/bin/env bun
/**
 * PRマージ後のフローステップ（issue_updated）を自動完了。
 *
 * Why:
 *   gh pr merge成功後、Issueにコメントを追加してフローステップを
 *   自動完了させる。手動操作なしでフローを進行させる。
 *
 * What:
 *   - PRからリンクされたIssue番号を抽出
 *   - Issueに完了コメントを追加（issue_updatedステップトリガー）
 *
 * Remarks:
 *   - PostToolUseフック
 *   - pr-merge-pull-reminderはpull、本フックはフローステップ完了
 *   - 自動化型: マージ成功後にIssueコメントを自動追加
 *
 * Changelog:
 *   - silenvx/dekita#3161: TypeScript移行
 */

import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import {
  type PrDetailsForIssueExtraction,
  extractIssueFromPrDetails,
  extractPrNumberFromMergeCommand,
  getLinkedIssueFromPr,
  isPrMergeCommand,
} from "../lib/github";
import { getExitCode } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { isMergeSuccess } from "../lib/repo";
import {
  createHookContext,
  getBashCommand,
  getToolResultAsObject,
  parseHookInput,
} from "../lib/session";
import { asyncSpawn } from "../lib/spawn";

const HOOK_NAME = "post-merge-flow-completion";

// Re-export for backward compatibility with tests
export { extractIssueFromPrDetails, isPrMergeCommand };
export type PrDetails = PrDetailsForIssueExtraction;

/**
 * Add a completion comment to the Issue.
 */
export async function addCompletionComment(
  issueNumber: number,
  prNumber: number,
): Promise<boolean> {
  try {
    const comment = `PR #${prNumber} マージ完了。フローステップ自動完了。`;
    const result = await asyncSpawn(
      "gh",
      ["issue", "comment", String(issueNumber), "--body", comment],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );
    return result.success;
  } catch {
    return false;
  }
}

async function main(): Promise<void> {
  const inputData = await parseHookInput();
  const ctx = createHookContext(inputData);
  const sessionId = ctx.sessionId;

  const toolName = inputData.tool_name ?? "";
  if (toolName !== "Bash") {
    return;
  }

  const command = getBashCommand(inputData);

  if (!isPrMergeCommand(command)) {
    return;
  }

  const rawToolOutput = inputData.tool_output;
  const toolOutput = typeof rawToolOutput === "string" ? rawToolOutput : "";
  const toolResult = getToolResultAsObject(inputData);
  const exitCode = getExitCode(toolResult);

  if (!isMergeSuccess(exitCode, toolOutput, command)) {
    return;
  }

  const prNumber = await extractPrNumberFromMergeCommand(command);
  if (!prNumber) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      "skipped: could not extract PR number",
      undefined,
      { sessionId },
    );
    return;
  }

  const issueNumber = await getLinkedIssueFromPr(prNumber);
  if (!issueNumber) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `skipped: no issue linked to PR #${prNumber}`,
      undefined,
      { sessionId },
    );
    return;
  }

  const success = await addCompletionComment(issueNumber, prNumber);
  if (success) {
    console.log(`[${HOOK_NAME}] Issue #${issueNumber} にフロー完了コメントを追加しました`);
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `comment added to issue #${issueNumber} for PR #${prNumber}`,
      undefined,
      { sessionId },
    );
  } else {
    console.log(`[${HOOK_NAME}] Issue #${issueNumber} へのコメント追加に失敗しました`);
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `failed to add comment to issue #${issueNumber}`,
      undefined,
      { sessionId },
    );
  }
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
  });
}
