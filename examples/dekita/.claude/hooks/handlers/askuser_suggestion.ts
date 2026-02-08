#!/usr/bin/env bun
/**
 * 選択肢をテキストで列挙するパターンを検出し、AskUserQuestionツールの使用を提案する。
 *
 * Why:
 *   テキストでの選択肢列挙はユーザーの入力負担が大きく、選択ミスのリスクがある。
 *   AskUserQuestionツールを使うことでUXが向上する。
 *
 * What:
 *   - トランスクリプトから選択肢パターン（A案/B案、1./2.等）を検出
 *   - AskUserQuestion使用回数と比較
 *   - 過少な場合に警告を表示（ブロックしない）
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、systemMessageで通知）
 *   - コードブロック内のパターンは除外
 *   - セッション終了時（Stop）に発火
 *   - Python版: askuser_suggestion.py
 *
 * Changelog:
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { readFileSync } from "node:fs";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { isSafeTranscriptPath } from "../lib/path_validation";
import { parseHookInput } from "../lib/session";
import { extractAssistantResponses, isInCodeBlock } from "../lib/transcript";

const HOOK_NAME = "askuser-suggestion";

// 選択肢を列挙するパターン（プリコンパイル済み）
const CHOICE_PATTERNS = [
  // A案、B案パターン
  /[A-Z]案.*?[A-Z]案/,
  /[１２３４５].*?[１２３４５]/,
  /[1-5]\s*[\.）\)].*?[1-5]\s*[\.）\)]/,
  // 質問パターン
  /どちらにしますか/,
  /どれを選びますか/,
  /どれにしますか/,
  /以下から選んでください/,
  /どの.*?にしますか/,
  /どちらが.*?ですか/,
  /どちらを.*?しますか/,
  // リスト形式
  /選択肢[：:]\s*\n/,
  /オプション[：:]\s*\n/,
];

// 除外パターン（結合してプリコンパイル）
const EXCLUDE_PATTERN = /```|例[：:]|例えば|ドキュメント|AGENTS\.md|スキル/;

interface AskUserUsageResult {
  violations: Array<{ pattern: RegExp; matched_text: string }>;
  askuser_count: number;
  choice_text_count: number;
}

/**
 * トランスクリプトを分析してAskUserQuestion使用状況を確認
 */
function checkAskUserUsage(transcriptPath: string): AskUserUsageResult {
  const result: AskUserUsageResult = {
    violations: [],
    askuser_count: 0,
    choice_text_count: 0,
  };

  let content: string;
  try {
    content = readFileSync(transcriptPath, "utf-8");
  } catch {
    return result;
  }

  // AskUserQuestion使用回数をカウント
  const askUserMatches = content.match(/AskUserQuestion/g);
  result.askuser_count = askUserMatches ? askUserMatches.length : 0;

  // Claudeの応答部分を抽出
  const claudeResponses = extractAssistantResponses(content);

  for (const response of claudeResponses) {
    // 除外コンテキストをチェック
    if (EXCLUDE_PATTERN.test(response)) {
      continue;
    }

    // 選択肢パターンを検出
    for (const pattern of CHOICE_PATTERNS) {
      const globalPattern = new RegExp(pattern.source, `${pattern.flags}g`);

      for (
        let match = globalPattern.exec(response);
        match !== null;
        match = globalPattern.exec(response)
      ) {
        // コードブロック内は除外
        if (isInCodeBlock(response, match.index)) {
          continue;
        }

        result.choice_text_count += 1;
        result.violations.push({
          pattern,
          matched_text: match[0].slice(0, 50),
        });
      }
    }
  }

  return result;
}

async function main(): Promise<void> {
  const hookInput = await parseHookInput();
  const sessionId = hookInput.session_id;

  // Stop hookはtranscript_pathをトップレベルで受け取る
  const transcriptPath = hookInput.transcript_path ?? "";

  if (!transcriptPath) {
    // トランスクリプトパスがない場合はスキップ
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

  const result = checkAskUserUsage(transcriptPath);

  // 違反がある場合は警告
  if (result.violations.length > 0 && result.choice_text_count > result.askuser_count) {
    const warningMsg = `⚠️ 選択肢をテキストで${result.choice_text_count}回列挙しましたが、AskUserQuestionは${result.askuser_count}回しか使用していません。
複数の選択肢がある場合はAskUserQuestionツールの使用を推奨します。`;

    await logHookExecution(
      HOOK_NAME,
      "warn",
      `Choice text: ${result.choice_text_count}, AskUser: ${result.askuser_count}`,
      undefined,
      { sessionId },
    );

    // 警告として表示（ブロックはしない）
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
