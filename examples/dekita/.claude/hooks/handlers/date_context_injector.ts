#!/usr/bin/env bun
/**
 * セッション開始時に現在日時とセッションIDをコンテキストに注入する
 *
 * Why:
 *   Claude Codeは明示的な日付コンテキストがない場合、知識カットオフ時点の
 *   日時にデフォルトしやすい。現在日時を注入することで正確な時間認識を保証する。
 *
 * What:
 *   - 現在日時をISO8601形式で出力
 *   - セッションIDを出力
 *
 * Remarks:
 *   - TZ環境変数でタイムゾーン設定可能（デフォルト: Asia/Tokyo）
 *   - Python版: date_context_injector.py
 *
 * Changelog:
 *   - silenvx/dekita#2814: TypeScript版初期実装
 */

import { formatError } from "../lib/format_error";
import { parseHookInput } from "../lib/session";

/** タイムゾーン設定（環境変数またはデフォルト） */
const TZ = process.env.TZ ?? "Asia/Tokyo";

/**
 * GMT形式のオフセットをISO 8601形式に変換
 *
 * @example
 * convertToIso8601Offset("GMT+9")    // "+09:00"
 * convertToIso8601Offset("GMT-5")    // "-05:00"
 * convertToIso8601Offset("GMT+5:30") // "+05:30"
 * convertToIso8601Offset("GMT")      // "+00:00"
 */
export function convertToIso8601Offset(gmtOffset: string): string {
  // GMT単体（UTC）の場合
  if (gmtOffset === "GMT" || gmtOffset === "") {
    return "+00:00";
  }

  // GMT+9, GMT-5, GMT+5:30 などをパース
  const match = gmtOffset.match(/^GMT([+-])(\d{1,2})(?::(\d{2}))?$/);
  if (!match) {
    // パースできない場合はZ（UTC）を返す
    return "Z";
  }

  const sign = match[1];
  const hours = match[2].padStart(2, "0");
  const minutes = match[3] ?? "00";

  return `${sign}${hours}:${minutes}`;
}

/**
 * フォーマットされた日時文字列を生成
 *
 * Issue #2814: Codex review対応
 * - dayOfWeekを指定タイムゾーンで計算
 * - ISO形式にタイムゾーンオフセットを含める
 */
export function formatDateTime(
  date: Date,
  timeZone: string,
): { humanReadable: string; isoFormat: string } {
  // タイムゾーンを適用した日時文字列を生成（曜日も含める）
  const options: Intl.DateTimeFormatOptions = {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    weekday: "long", // Gemini review: longで直接フル形式を取得
    hour12: false,
  };

  const formatter = new Intl.DateTimeFormat("en-US", options);
  const parts = formatter.formatToParts(date);

  const year = parts.find((p) => p.type === "year")?.value ?? "";
  const month = parts.find((p) => p.type === "month")?.value ?? "";
  const day = parts.find((p) => p.type === "day")?.value ?? "";
  const hour = parts.find((p) => p.type === "hour")?.value ?? "";
  const minute = parts.find((p) => p.type === "minute")?.value ?? "";
  const second = parts.find((p) => p.type === "second")?.value ?? "";
  const dayOfWeek = parts.find((p) => p.type === "weekday")?.value ?? "";

  // タイムゾーンオフセットを取得（ISO 8601形式: ±HH:MM）
  // Intl.DateTimeFormatのshortOffsetは「GMT+9」形式なので、「+09:00」形式に変換
  const tzFormatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    timeZoneName: "shortOffset",
  });
  const tzParts = tzFormatter.formatToParts(date);
  const rawOffset = tzParts.find((p) => p.type === "timeZoneName")?.value ?? "";

  // GMT+9 → +09:00, GMT-5 → -05:00, GMT+5:30 → +05:30 に変換
  const tzOffset = convertToIso8601Offset(rawOffset);

  // タイムゾーン略称（JST等）を取得
  const tzAbbrFormatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    timeZoneName: "short",
  });
  const tzAbbrParts = tzAbbrFormatter.formatToParts(date);
  const tzAbbr = tzAbbrParts.find((p) => p.type === "timeZoneName")?.value ?? timeZone;

  const humanReadable = `${year}-${month}-${day} ${dayOfWeek} ${hour}:${minute}:${second} ${tzAbbr}`;

  // ISO形式（タイムゾーンオフセット付き）
  // 例: 2026-01-14T22:30:00+09:00
  const isoFormat = `${year}-${month}-${day}T${hour}:${minute}:${second}${tzOffset}`;

  return { humanReadable, isoFormat };
}

/**
 * 出力文字列を構築
 */
export function buildOutput(
  humanReadable: string,
  isoFormat: string,
  sessionId: string | null,
  source: string | null,
  error: string | null = null,
): string {
  const parts = [`[CONTEXT] 現在日時: ${humanReadable} | ISO: ${isoFormat}`];

  if (sessionId) {
    parts.push(`Session: ${sessionId}`);
  }
  if (source) {
    parts.push(`Source: ${source}`);
  }

  let output = parts.join(" | ");

  if (error) {
    output += ` (TZエラー: ${formatError(error)})`;
  }

  return output;
}

/**
 * メイン処理
 */
async function main(): Promise<void> {
  // フック入力をパース
  const input = await parseHookInput();
  const sessionId = input.session_id ?? null;
  const source = input.source ?? null;

  // 現在日時を取得
  const now = new Date();

  try {
    const { humanReadable, isoFormat } = formatDateTime(now, TZ);
    const output = buildOutput(humanReadable, isoFormat, sessionId, source);
    console.log(output);
  } catch (error) {
    // タイムゾーンエラー時のフォールバック
    const humanReadable = now
      .toISOString()
      .replace("T", " ")
      .replace(/\.\d{3}Z$/, "");
    const isoFormat = now.toISOString();
    const errorMsg = error instanceof Error ? error.message : String(error);
    const output = buildOutput(humanReadable, isoFormat, sessionId, source, errorMsg);
    console.log(output);
  }
}

// 実行（直接実行時のみ）
if (import.meta.main) {
  main().catch((error) => {
    console.error(`[date-context-injector] Error: ${formatError(error)}`);
    process.exit(1);
  });
}
