#!/usr/bin/env bun
/**
 * マージ成功後に未確認の動作確認Issueをリマインドする。
 *
 * Why:
 *   マージ後に動作確認を忘れると本番で問題が発生する可能性がある。
 *   マージ成功のタイミングでリマインドし確認漏れを防止する。
 *
 * What:
 *   - gh pr mergeの成功を検出
 *   - オープンな動作確認Issueを一覧表示
 *   - 確認手順と対応方法を案内
 *
 * Remarks:
 *   - リマインド型フック（ブロックしない、stderrで情報表示）
 *   - PostToolUse:Bashで発火（gh pr mergeコマンド成功時）
 *   - post-merge-observation-issue.pyがIssue作成（補完関係）
 *   - observation-session-reminder.tsはセッション開始時（責務分離）
 *   - Python版: observation_reminder.py
 *
 * Changelog:
 *   - silenvx/dekita#2547: フック追加
 *   - silenvx/dekita#2588: createdAtで経過時間表示
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { getObservationIssues } from "../lib/github";
import { getExitCode, getToolResult } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { isMergeSuccess } from "../lib/repo";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "observation-reminder";

/**
 * Check if the command is a PR merge command.
 */
export function isPrMergeCommand(command: string): boolean {
  return command.includes("gh pr merge");
}

/**
 * Format issue age in a human-readable way.
 */
export function formatIssueAge(createdAt: string | null | undefined): string {
  if (!createdAt || createdAt === "") {
    return "不明";
  }

  try {
    const created = new Date(createdAt);
    const now = new Date();
    const deltaMs = now.getTime() - created.getTime();
    const hours = deltaMs / (1000 * 60 * 60);

    if (hours < 1) {
      return "1時間以内";
    }
    if (hours < 24) {
      return `${Math.floor(hours)}時間前`;
    }
    const days = Math.floor(hours / 24);
    return `${days}日前`;
  } catch {
    return "不明";
  }
}

async function main(): Promise<void> {
  const inputData = await parseHookInput();
  if (!inputData) {
    return;
  }
  const ctx = createHookContext(inputData);
  const sessionId = ctx.sessionId;

  const toolName = (inputData.tool_name as string) || "";
  if (toolName !== "Bash") {
    return;
  }

  const toolInput = (inputData.tool_input as Record<string, unknown>) || {};
  const command = (toolInput.command as string) || "";

  if (!isPrMergeCommand(command)) {
    return;
  }

  const toolOutput = (inputData.tool_output as string) || "";
  const toolResult = getToolResult(inputData) || {};
  const exitCode = getExitCode(toolResult as Record<string, unknown> | string | null | undefined);

  if (!isMergeSuccess(exitCode, toolOutput, command)) {
    return;
  }

  // Get pending observation issues
  // Issue #2588: Use shared function with createdAt for age display
  const issues = await getObservationIssues(10, ["number", "title", "createdAt"]);
  if (!issues || issues.length === 0) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      "no pending observation issues after merge",
      undefined,
      { sessionId },
    );
    return;
  }

  // Build reminder message
  console.log(`\n${"=".repeat(60)}`);
  console.log("[動作確認リマインダー] 未確認の動作確認Issueがあります");
  console.log("=".repeat(60));
  console.log();

  for (const issue of issues) {
    const number = issue.number ?? "?";
    const title = issue.title ?? "";
    const createdAt = (issue.createdAt as string) ?? "";
    const age = formatIssueAge(createdAt);

    console.log(`  #${number}: ${title}`);
    console.log(`         作成: ${age}`);
    console.log();
  }

  console.log("確認手順:");
  console.log("  1. 該当機能が期待通り動作することを確認");
  console.log("  2. 問題なければ `gh issue close <番号>` でクローズ");
  console.log("  3. 問題があれば別途バグIssueを作成");
  console.log();
  console.log("自動検証:");
  console.log("  `bun run .claude/scripts/observation_verifier_ts/main.ts --execute`");
  console.log("  で確認コマンド付きIssueを一括検証・自動クローズできます。");
  console.log();
  console.log("=".repeat(60));

  await logHookExecution(
    HOOK_NAME,
    "approve",
    `reminded about ${issues.length} observation issue(s) after merge`,
    undefined,
    { sessionId },
  );
}

if (import.meta.main) {
  main();
}
