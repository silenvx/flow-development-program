#!/usr/bin/env bun
/**
 * セッション開始時に未確認の動作確認Issueをリマインドする。
 *
 * Why:
 *   セッション開始時に未確認の動作確認Issueを表示することで、
 *   CI待ちや関連作業中に自然と確認する機会を提供する。
 *
 * What:
 *   - オープンな動作確認Issueを一覧取得
 *   - Issue番号と件数を簡潔に表示
 *   - 確認方法（gh issue close）を案内
 *
 * Remarks:
 *   - リマインド型フック（ブロックしない、stderrで情報表示）
 *   - SessionStartで発火
 *   - observation-reminder.pyはマージ後リマインド（責務分離）
 *   - 簡潔な表示でセッション開始時の負担を軽減
 *
 * Changelog:
 *   - silenvx/dekita#2583: フック追加（Python）
 *   - silenvx/dekita#3148: TypeScriptに移行
 */

import { getObservationIssues } from "../lib/github";
import { logHookExecution } from "../lib/logging";
import { checkAndMarkSessionAction, parseHookInput } from "../lib/session";

const HOOK_NAME = "observation-session-reminder";

async function main(): Promise<void> {
  const inputData = await parseHookInput();
  if (!inputData) {
    console.log(JSON.stringify({}));
    return;
  }
  const sessionId = inputData?.session_id;

  // Run only once per session
  if (sessionId && !checkAndMarkSessionAction(sessionId, HOOK_NAME)) {
    console.log(JSON.stringify({}));
    return;
  }

  // Get pending observation issues
  const issues = await getObservationIssues();
  if (!issues || issues.length === 0) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      "no pending observation issues at session start",
      {},
      { sessionId },
    );
    console.log(JSON.stringify({}));
    return;
  }

  // Build reminder message - concise for session start
  const count = issues.length;
  const issueList = issues.map((i) => `#${i.number ?? "?"}`).join(", ");

  console.error(`\n📋 動作確認Issue ${count}件: ${issueList}`);
  console.error("   → CI待ちや関連作業中に確認できれば `gh issue close <番号>`");

  await logHookExecution(
    HOOK_NAME,
    "approve",
    `reminded about ${count} observation issue(s) at session start`,
    {},
    { sessionId },
  );
  console.log(JSON.stringify({}));
}

if (import.meta.main) {
  main().catch((error) => {
    console.error(`[${HOOK_NAME}] Unexpected error:`, error);
    console.log(JSON.stringify({}));
    process.exit(0); // Don't block on error
  });
}
