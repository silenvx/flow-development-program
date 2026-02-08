#!/usr/bin/env bun
/**
 * タスク開始時に確認チェックリストをリマインド表示する。
 *
 * Why:
 *   タスク開始時に要件・設計の確認を怠ると、実装後の手戻りが発生する。
 *   チェックリストをリマインドすることで、確認漏れを防ぐ。
 *
 * What:
 *   - セッションの最初のツール実行時にチェックリストを表示
 *   - 要件確認、設計判断、影響範囲、前提条件のチェック項目を提示
 *   - systemMessageで情報提供（ブロックしない）
 *
 * Remarks:
 *   - open-issue-reminderはIssue確認、本フックは要件・設計確認
 *   - セッションマーカー機構を使用（セッション毎に1回のみ表示）
 *
 * Changelog:
 *   - silenvx/dekita#1234: フック追加
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { checkAndUpdateSessionMarker, parseHookInput } from "../lib/session";

const HOOK_NAME = "task-start-checklist";

/**
 * Generate the task start checklist message.
 */
export function getChecklistMessage(): string {
  const lines = [
    "📋 **タスク開始前の確認チェックリスト**",
    "",
    "以下の点を確認してからタスクを開始してください:",
    "",
    "**⚠️ セッション開始時ファイル確認（最重要）**:",
    "  [ ] セッション開始時にファイルを読み込んだか？",
    "  [ ] 読み込んだファイルの内容は**タスク**か？",
    "  [ ] タスクなら、他の作業より先に実行すること",
    "",
    "**要件確認**:",
    "  [ ] 要件は明確か？曖昧な点があれば質問する",
    "  [ ] ユーザーの意図を正しく理解しているか？",
    "  [ ] 「〜したい」の背景・目的は何か？",
    "",
    "**設計判断**:",
    "  [ ] 設計上の選択肢がある場合、ユーザーに確認する",
    "  [ ] 既存のコードパターン・規約を把握しているか？",
    "  [ ] 事前に決めておくべきことはないか？",
    "",
    "**影響範囲**:",
    "  [ ] 変更の影響範囲を把握しているか？",
    "  [ ] 破壊的変更はないか？あれば事前に確認する",
    "",
    "**前提条件**:",
    "  [ ] 必要な環境・依存関係は整っているか？",
    "  [ ] Context7/Web検索で最新情報を確認すべきか？",
    "",
    "💡 不明点があれば、実装前に必ず質問してください。",
  ];
  return lines.join("\n");
}

/**
 * PreToolUse hook for Edit/Write/Bash commands.
 *
 * Shows task start checklist on first tool execution of each session.
 * Uses atomic check-and-update to prevent race conditions.
 */
async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};

  let sessionId: string | undefined;

  try {
    // Parse input to set session context
    const hookInput = await parseHookInput();

    sessionId = hookInput.session_id;

    // Atomically check if new session and update marker
    // Returns true only for the first caller when concurrent calls occur
    if (checkAndUpdateSessionMarker(HOOK_NAME)) {
      result.systemMessage = getChecklistMessage();
    }
  } catch (error) {
    // Don't block on errors, just skip the reminder
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
