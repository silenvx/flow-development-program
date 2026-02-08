#!/usr/bin/env bun
/**
 * UserPromptSubmit時にsession_idをコンテキストに出力する
 *
 * Why:
 *   Fork-session判定には、SessionStartとUserPromptSubmitのsession_idを
 *   比較する必要がある。この2つが異なる場合がfork-sessionである。
 *
 * What:
 *   - UserPromptSubmit時にsession_idを取得
 *   - タイムスタンプ付きでadditionalContextに出力
 *   - Claudeがコンテキスト内の2つのIDを比較してfork判定
 *
 * Remarks:
 *   - ファイルベースのステートフルな設計は避ける
 *   - タイムスタンプで最新のsession_id情報を特定可能
 *   - SessionStartのSession ID = fork元（古いセッション）
 *   - USER_PROMPT_SESSION_IDの最新 = 現在のセッション
 *   - Python版: fork_session_id_updater.py
 *
 * Changelog:
 *   - silenvx/dekita#2814: TypeScript版初期実装
 */

import { formatError } from "../lib/format_error";
import { parseHookInput } from "../lib/session";

/** UUID形式の正規表現パターン */
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * session_idがUUID形式かを検証
 * セキュリティ: 外部入力がLLMコンテキストに入るため、形式を検証
 */
function isValidSessionId(sessionId: string): boolean {
  return UUID_PATTERN.test(sessionId);
}

/** タイムゾーン設定（環境変数またはデフォルト） */
const TZ = process.env.TZ ?? "Asia/Tokyo";

/**
 * 現在のタイムスタンプをISO形式で取得
 */
function getCurrentTimestamp(): string {
  const now = new Date();

  try {
    // タイムゾーンを適用した日時文字列を生成
    const options: Intl.DateTimeFormatOptions = {
      timeZone: TZ,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    };

    const formatter = new Intl.DateTimeFormat("en-US", options);
    const parts = formatter.formatToParts(now);

    const year = parts.find((p) => p.type === "year")?.value ?? "";
    const month = parts.find((p) => p.type === "month")?.value ?? "";
    const day = parts.find((p) => p.type === "day")?.value ?? "";
    const hour = parts.find((p) => p.type === "hour")?.value ?? "";
    const minute = parts.find((p) => p.type === "minute")?.value ?? "";
    const second = parts.find((p) => p.type === "second")?.value ?? "";

    // タイムゾーンオフセットを取得
    const tzFormatter = new Intl.DateTimeFormat("en-US", {
      timeZone: TZ,
      timeZoneName: "shortOffset",
    });
    const tzParts = tzFormatter.formatToParts(now);
    const rawOffset = tzParts.find((p) => p.type === "timeZoneName")?.value ?? "";

    // GMT+9 → +09:00 形式に変換
    let tzOffset = "Z";
    const match = rawOffset.match(/^GMT([+-])(\d{1,2})(?::(\d{2}))?$/);
    if (match) {
      const sign = match[1];
      const hours = match[2].padStart(2, "0");
      const minutes = match[3] ?? "00";
      tzOffset = `${sign}${hours}:${minutes}`;
    } else if (rawOffset === "GMT" || rawOffset === "") {
      tzOffset = "+00:00";
    }

    return `${year}-${month}-${day}T${hour}:${minute}:${second}${tzOffset}`;
  } catch {
    // フォールバック: UTC
    return now.toISOString().replace(/\.\d{3}Z$/, "Z");
  }
}

/**
 * メイン処理
 */
async function main(): Promise<void> {
  const input = await parseHookInput();
  const sessionId = input.session_id;

  // session_idがない場合、または不正な形式の場合は何も出力しない
  // セキュリティ: LLMコンテキストへのインジェクション防止
  if (!sessionId || !isValidSessionId(sessionId)) {
    return;
  }

  // Issue #2372: タイムスタンプと説明を追加
  const timestamp = getCurrentTimestamp();
  const explanation =
    "(現在のsession_id。日付が新しいほど最新。" +
    "SessionStartのSession IDはfork元。異なる場合はfork-session)";
  const context = `[USER_PROMPT_SESSION_ID] ${timestamp} | ${sessionId} | ${explanation}`;

  const output = {
    hookSpecificOutput: {
      hookEventName: "UserPromptSubmit",
      additionalContext: context,
    },
  };

  console.log(JSON.stringify(output));
}

if (import.meta.main) {
  main().catch((error) => {
    console.error(`[fork-session-id-updater] Error: ${formatError(error)}`);
    // Hooks should not block on internal errors - output empty object
    console.log(JSON.stringify({}));
    process.exit(0);
  });
}
