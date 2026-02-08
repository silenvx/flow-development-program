#!/usr/bin/env bun
/**
 * 「マージしますか？」パターンを検出し、原則違反を警告する。
 *
 * Why:
 *   AGENTS.mdの「マージまで完遂」原則では、マージ可能になったら
 *   確認なしに即座にマージすべき。確認パターンは原則違反である。
 *
 * What:
 *   - Claudeの応答から「マージしますか？」等のパターンを検出
 *   - 検出したらセッション終了時に警告を表示
 *   - ルール説明やコードブロック内は誤検知防止で除外
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、事後検出で警告）
 *   - Stopで発火（transcript分析）
 *   - AGENTS.md参照や❌例示は誤検知防止で除外
 *   - 次回からの改善を促すフィードバック
 *
 * Changelog:
 *   - silenvx/dekita#2284: フック追加
 *   - silenvx/dekita#3161: TypeScript移行
 */

import { readFileSync } from "node:fs";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { isSafeTranscriptPath } from "../lib/path_validation";
import { createHookContext, parseHookInput } from "../lib/session";
import { extractAssistantResponses, isInCodeBlock } from "../lib/transcript";

const HOOK_NAME = "merge-confirmation-warning";

// マージ確認パターン（単一の正規表現に結合）
const MERGE_CONFIRMATION_PATTERN = new RegExp(
  "(?:" +
    // 直接的な確認パターン
    "マージしますか[\\?？]" +
    "|マージしてよい(?:です)?か[\\?？]" +
    "|マージしてもよろしい(?:です)?か[\\?？]" +
    "|マージを実行しますか[\\?？]" +
    "|マージしても(?:いい|良い)(?:です)?か[\\?？]" +
    // PR関連の確認パターン
    "|PR(?:を)?マージしますか[\\?？]" +
    "|プルリクエスト(?:を)?マージしますか[\\?？]" +
    // 完了報告後の待機パターン
    // Issue #3161: Use [\s\S] instead of . to match across newlines
    "|次は何をしますか[\\?？][\\s\\S]*?(?:マージ|merge)" +
    ")",
  "gi",
);

// 除外パターン（ルール説明や例示を除外）
const EXCLUDE_PATTERN = /AGENTS\.md|禁止.*パターン|❌|正しい対応|例:/;

// 除外パターンのコンテキスト範囲（マッチ前後の文字数）
const EXCLUDE_CONTEXT_RANGE = 30;

interface Violation {
  pattern: string;
  context: string;
}

interface CheckResult {
  violations: Violation[];
  confirmationCount: number;
}

/**
 * トランスクリプトを分析してマージ確認パターンを検出。
 */
function checkMergeConfirmation(transcriptPath: string): CheckResult {
  const result: CheckResult = {
    violations: [],
    confirmationCount: 0,
  };

  let content: string;
  try {
    content = readFileSync(transcriptPath, "utf-8");
  } catch {
    return result;
  }

  // Claudeの応答部分を抽出
  const claudeResponses = extractAssistantResponses(content);

  for (const response of claudeResponses) {
    // マージ確認パターンを検出
    // Reset lastIndex for global regex
    MERGE_CONFIRMATION_PATTERN.lastIndex = 0;

    for (const match of response.matchAll(MERGE_CONFIRMATION_PATTERN)) {
      // コードブロック内は除外
      if (isInCodeBlock(response, match.index)) {
        continue;
      }

      // マッチ周辺のコンテキストのみで除外パターンをチェック
      const contextStart = Math.max(0, match.index - EXCLUDE_CONTEXT_RANGE);
      const contextEnd = Math.min(
        response.length,
        match.index + match[0].length + EXCLUDE_CONTEXT_RANGE,
      );
      const matchContext = response.slice(contextStart, contextEnd);

      if (EXCLUDE_PATTERN.test(matchContext)) {
        continue;
      }

      result.confirmationCount++;

      // 報告用のコンテキスト（前後50文字）
      const reportStart = Math.max(0, match.index - 50);
      const reportEnd = Math.min(response.length, match.index + match[0].length + 50);
      result.violations.push({
        pattern: match[0],
        context: response.slice(reportStart, reportEnd),
      });
    }
  }

  return result;
}

async function main(): Promise<void> {
  const hookInput = await parseHookInput();
  const ctx = createHookContext(hookInput);
  const sessionId = ctx.sessionId;

  // Stop hookはtranscript_pathをトップレベルで受け取る
  const transcriptPath = hookInput.transcript_path ?? "";

  if (!transcriptPath) {
    await logHookExecution(HOOK_NAME, "approve", "No transcript path", undefined, { sessionId });
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // セキュリティ: パストラバーサル攻撃を防止
  if (!isSafeTranscriptPath(transcriptPath)) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Invalid transcript path: ${transcriptPath}`,
      undefined,
      { sessionId },
    );
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  const result = checkMergeConfirmation(transcriptPath);

  // 違反がある場合は警告
  if (result.violations.length > 0) {
    const examples = result.violations.slice(0, 3);
    const exampleText = examples.map((v) => `  - 「${v.pattern}」`).join("\n");
    const warningMsg = `⚠️ 「マージ確認」パターンが${result.violations.length}回検出されました:\n${exampleText}\n\nAGENTS.mdの「マージまで完遂」原則:\n  ❌ 「マージしますか？」と確認を求める\n  ✅ マージ可能になったら即座に \`gh pr merge\` を実行\n\n次回から、CIパス・レビュー完了後はユーザー確認なしにマージを実行してください。`;

    await logHookExecution(
      HOOK_NAME,
      "warn",
      `Merge confirmation patterns detected: ${result.violations.length}`,
      undefined,
      { sessionId },
    );
    console.log(
      JSON.stringify({
        continue: true,
        message: warningMsg,
      }),
    );
  } else {
    await logHookExecution(HOOK_NAME, "approve", "No violations", undefined, { sessionId });
    console.log(JSON.stringify({ continue: true }));
  }
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({ continue: true }));
  });
}
