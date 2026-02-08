#!/usr/bin/env bun
/**
 * 本番環境URLへのアクセス前に警告・確認を促す。
 *
 * Why:
 *   本番環境への誤アクセスは意図しない副作用を起こす可能性がある。
 *   また、類似ドメイン（dekita.pages.dev等）への誤アクセスを防ぐ。
 *
 * What:
 *   - mcp__chrome-devtools__navigate_page/new_page を検出
 *   - URLが本番環境（dekita.app, api.dekita.app）なら警告表示
 *   - 間違ったURL（dekita.pages.dev等）の場合はブロック
 *
 * Remarks:
 *   - 本番URL: 警告のみ（approve with systemMessage）
 *   - 間違ったURL: ブロック
 *   - CUSTOMIZE: PRODUCTION_HOSTNAMESを自プロジェクトに合わせて変更
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2917: TypeScriptに移植
 */

import { logHookExecution } from "../lib/logging";
import { blockAndExit, outputResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";

// CUSTOMIZE: Production hostnames - Set these to your project's production domain(s)
const PRODUCTION_HOSTNAMES = ["dekita.app", "api.dekita.app"];

// CUSTOMIZE: Wrong hostnames to block - Add domains easily confused with production
const WRONG_HOSTNAMES: Record<string, string> = {
  "dekita.pages.dev": "https://dekita.app", // Different app with same-ish name
};

/**
 * Check if URL is a production URL using precise hostname matching.
 */
export function isProductionUrl(url: string): boolean {
  if (!url) {
    return false;
  }
  try {
    const parsed = new URL(url);
    const hostname = parsed.hostname.toLowerCase();
    return PRODUCTION_HOSTNAMES.includes(hostname);
  } catch {
    return false;
  }
}

/**
 * Check if URL is a known wrong URL. Returns correct URL suggestion if wrong.
 */
export function getCorrectUrlForWrongHostname(url: string): string | null {
  if (!url) {
    return null;
  }
  try {
    const parsed = new URL(url);
    const hostname = parsed.hostname.toLowerCase();
    return WRONG_HOSTNAMES[hostname] ?? null;
  } catch {
    return null;
  }
}

async function main(): Promise<void> {
  const data = await parseHookInput();
  const ctx = createHookContext(data);
  const sessionId = ctx.sessionId;
  const toolName = data.tool_name ?? "";
  const toolInput = (data.tool_input ?? {}) as Record<string, unknown>;

  // Only check navigation tools
  if (
    toolName !== "mcp__chrome-devtools__navigate_page" &&
    toolName !== "mcp__chrome-devtools__new_page"
  ) {
    outputResult({});
    return;
  }

  const url = (toolInput.url as string) ?? "";

  // Check for wrong URLs first (block)
  const correctUrl = getCorrectUrlForWrongHostname(url);
  if (correctUrl) {
    const reason = `⚠️ 間違ったURLが指定されています。\n\n指定URL: ${url}\n正しいURL: ${correctUrl}\n\ndekita.pages.dev は別のアプリです。\n本プロジェクトの本番環境は dekita.app です。`;

    await logHookExecution(
      "production-url-warning",
      "block",
      reason,
      {
        url,
        correct_url: correctUrl,
      },
      { sessionId },
    );
    blockAndExit("production-url-warning", reason);
  }

  // Check for production URLs (warn, but allow)
  if (isProductionUrl(url)) {
    const systemMessage = `📍 本番環境にアクセスします: ${url}\nAGENTS.md「環境情報」セクションを参照してください。`;

    await logHookExecution(
      "production-url-warning",
      "approve",
      systemMessage,
      {
        url,
      },
      { sessionId },
    );
    outputResult({
      systemMessage,
    });
    return;
  }

  // Not a production or wrong URL, just approve
  await logHookExecution("production-url-warning", "approve", undefined, { url }, { sessionId });
  outputResult({});
}

if (import.meta.main) {
  main();
}
