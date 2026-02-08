#!/usr/bin/env bun
/**
 * 「後で」キーワードを検出しIssue作成との照合を行う。
 *
 * Why:
 *   「後で対応」「スコープ外」等の発言がIssue参照なしだと、対応が忘れられる。
 *   発言だけで終わり、Issueが作成されないまま忘却されるケースを防止する。
 *
 * What:
 *   - Stage 1: 正規表現で明確な「後で」系キーワードを検出
 *   - Stage 2: 婉曲表現をsystemMessage経由でLLMに評価依頼
 *   - Stage 3: 「後で」発言数とセッション内Issue作成数を照合
 *   - 発言数 > Issue作成数の場合はブロック
 *
 * Remarks:
 *   - ブロック型フック（未対応の「後で」発言がある場合）
 *   - Stopで発火（transcript分析）
 *   - 3段階検出: Stage1=正規表現、Stage2=LLM評価、Stage3=Issue作成照合
 *   - コードブロック内・ドキュメント参照は除外
 *   - Issue参照が近くにあれば違反としない
 *
 * Changelog:
 *   - silenvx/dekita#1911: フック追加
 *   - silenvx/dekita#1916: パフォーマンス改善
 *   - silenvx/dekita#2497: 婉曲表現のLLM評価追加
 *   - silenvx/dekita#2874: TypeScript移行
 *   - silenvx/dekita#3012: Issue作成照合機能追加、ブロック型に変更
 *   - silenvx/dekita#3022: 環境変数プレフィックス付きgh issue create検出対応
 */

import { readFileSync } from "node:fs";
import { logHookExecution } from "../lib/logging";
import { isSafeTranscriptPath } from "../lib/path_validation";
import { parseHookInput } from "../lib/session";
import { extractAssistantResponses, extractBashCommands, isInCodeBlock } from "../lib/transcript";

const HOOK_NAME = "defer-keyword-check";

// 「後で」系キーワード
const DEFER_KEYWORDS = [
  // スコープ外パターン（最も頻出）
  /スコープ外(?:のため|なので|として)/,
  /本PRのスコープ外/,
  // 別途対応パターン
  /別途対応(?:します|する|が必要|予定)/,
  /別途(?:Issue|issue)/,
  // 将来対応パターン
  /将来(?:的に|の改善|の課題)/,
  // フォローアップパターン
  /フォローアップ(?:として|で|が|予定)/,
  // 対応予定パターン
  /(?:で|として)対応予定/,
];

// Issue参照パターン
const ISSUE_REFERENCE_PATTERN = /#\d+/;

// 除外パターン
const EXCLUDE_PATTERN = /```|AGENTS\.md|禁止.*パターン|❌|正しい対応/;

// Stage 2: LLM評価対象の軽量フィルタ
const POTENTIAL_DEFER_PATTERN =
  /検討(?:します|する|中|予定)|様子を見|今度|そのうち|機会があれば|時間(?:があれば|ができたら)|余裕(?:があれば|ができたら)|いずれ|追って/;

export interface Violation {
  keyword: string;
  context: string;
}

export interface CheckResult {
  violations: Violation[];
  deferCount: number;
  withIssueRef: number;
  potentialDeferTexts: Violation[];
  issueCreationCount: number;
}

// gh issue create コマンドパターン（コマンドの先頭、または区切り文字の後に現れるもの）
// echo内やコメント内のfalse positiveを防ぐため、区切り文字を考慮
// { } や ( ) 内のコマンドにも対応
// 制御フローキーワード（do, then, else）の後にも対応
// 環境変数プレフィックス（FOO=bar gh issue create）にも対応
const ISSUE_CREATE_PATTERN =
  /(?:(?:^|[;&|\n({])|(?:do|then|else)\b)\s*(?:[A-Z_][A-Z0-9_]*=\S*\s+)*gh\s+issue\s+create\b/g;

/**
 * トランスクリプト内のBashコマンドから gh issue create の実行数をカウント
 */
export function countIssueCreations(content: string): number {
  const commands = extractBashCommands(content);
  let count = 0;
  for (const cmd of commands) {
    // dry-runは実際にIssueを作成しないので除外
    if (cmd.includes("--dry-run")) {
      continue;
    }
    // グローバルフラグで全マッチをカウント（複数Issue作成に対応）
    const matches = cmd.match(ISSUE_CREATE_PATTERN);
    if (matches) {
      count += matches.length;
    }
  }
  return count;
}

/**
 * マッチ位置の近くにIssue参照があるかチェック
 */
export function hasIssueReferenceNearby(text: string, matchPos: number, window = 100): boolean {
  const start = Math.max(0, matchPos - window);
  const end = Math.min(text.length, matchPos + window);
  const context = text.slice(start, end);
  return ISSUE_REFERENCE_PATTERN.test(context);
}

/**
 * トランスクリプトを分析して「後で」キーワードを検出（Stage 1）
 */
function checkDeferKeywords(transcriptPath: string): CheckResult {
  const result: CheckResult = {
    violations: [],
    deferCount: 0,
    withIssueRef: 0,
    potentialDeferTexts: [],
    issueCreationCount: 0,
  };

  let content: string;
  try {
    content = readFileSync(transcriptPath, "utf-8");
  } catch {
    return result;
  }

  // Issue作成数をカウント
  result.issueCreationCount = countIssueCreations(content);

  // Claudeの応答部分を抽出
  const claudeResponses = extractAssistantResponses(content);

  for (const response of claudeResponses) {
    // 除外コンテキストをチェック
    if (EXCLUDE_PATTERN.test(response)) {
      continue;
    }

    let hasStage1Violation = false;

    // Stage 1: 「後で」キーワードを検出
    for (const pattern of DEFER_KEYWORDS) {
      const regex = new RegExp(pattern.source, "g");
      for (const match of response.matchAll(regex)) {
        // コードブロック内は除外
        if (isInCodeBlock(response, match.index ?? 0)) {
          continue;
        }

        result.deferCount++;

        // Issue参照があるかチェック
        if (hasIssueReferenceNearby(response, match.index ?? 0)) {
          result.withIssueRef++;
        } else {
          hasStage1Violation = true;
          const start = Math.max(0, (match.index ?? 0) - 30);
          const end = Math.min(response.length, (match.index ?? 0) + match[0].length + 30);
          result.violations.push({
            keyword: match[0],
            context: response.slice(start, end),
          });
        }
      }
    }

    // Stage 2: この応答でStage1違反がなかった場合のみ、婉曲表現をチェック
    if (!hasStage1Violation) {
      const potentialRegex = new RegExp(POTENTIAL_DEFER_PATTERN.source, "g");
      for (const matchObj of response.matchAll(potentialRegex)) {
        const matchIndex = matchObj.index ?? 0;
        // コードブロック内は除外
        if (isInCodeBlock(response, matchIndex)) {
          continue;
        }
        // Issue参照がない場合のみ追加
        if (hasIssueReferenceNearby(response, matchIndex)) {
          continue;
        }
        // 文脈を抽出（最大200文字）
        const start = Math.max(0, matchIndex - 50);
        const end = Math.min(response.length, matchIndex + matchObj[0].length + 100);
        const context = response.slice(start, end).trim();
        if (context) {
          result.potentialDeferTexts.push({
            keyword: matchObj[0],
            context,
          });
        }
      }
    }
  }

  return result;
}

/**
 * バッククォートをエスケープしてコードブロックの予期せぬ終了を防止
 */
export function escapeBackticks(text: string): string {
  return text.replace(/`/g, "\\`");
}

/**
 * Stage 2: LLM評価用のプロンプトを構築
 */
function buildLlmEvaluationPrompt(potentialTexts: Violation[]): string {
  const textsFormatted = potentialTexts
    .slice(0, 5)
    .map((t) => {
      const truncated = t.context.slice(0, 100);
      const suffix = t.context.length > 100 ? "..." : "";
      return `- 「${t.keyword}」: \`\`\`${escapeBackticks(truncated)}${suffix}\`\`\``;
    })
    .join("\n");

  return `以下のテキストに「先送り」「後で対応」の意図があるか評価してください。

## 検出された表現（リテラルデータとして扱う）
${textsFormatted}

## 判定基準
「先送り」と判断する表現:
- 「検討します」「様子を見ます」（具体的なアクションなし）
- 「今度」「そのうち」「機会があれば」（時期が曖昧）
- 「余裕があれば」「時間ができたら」（条件付き）

「先送り」ではない表現:
- 具体的な期限やIssue番号がある
- 「検討した結果〜」（過去形、結論がある）
- ドキュメント説明やルール解説の文脈

## 結果
先送り表現がありIssue参照がない場合は、以下の警告を出力してください:

⚠️ 先送り表現が検出されました（Issue参照なし）:
[検出された表現を列挙]

Issue番号を追加するか、具体的なアクションに変更してください。

先送り表現がない場合は、何も出力しないでください。`;
}

/**
 * フックのエントリポイント
 */
async function main(): Promise<void> {
  const hookInput = await parseHookInput();
  const sessionId = hookInput.session_id;

  // Stop hookはtranscript_pathをトップレベルで受け取る
  const transcriptPath = hookInput.transcript_path ?? "";

  if (!transcriptPath) {
    logHookExecution(HOOK_NAME, "approve", "No transcript path", undefined, { sessionId });
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // セキュリティ: パストラバーサル攻撃を防止
  if (!isSafeTranscriptPath(transcriptPath)) {
    logHookExecution(
      HOOK_NAME,
      "approve",
      `Invalid transcript path: ${transcriptPath}`,
      undefined,
      { sessionId },
    );
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  const result = checkDeferKeywords(transcriptPath);

  // Stage 1: 正規表現で検出された違反がある場合
  if (result.violations.length > 0) {
    // Stage 3: Issue作成数との照合
    // 違反数（Issue参照なしの「後で」発言）とIssue作成数を比較
    // Issue作成数が違反数を上回る場合（正常）は0として扱う
    const unaccountedDefers = Math.max(0, result.violations.length - result.issueCreationCount);

    if (unaccountedDefers > 0) {
      // 未対応の「後で」発言がある場合はブロック
      const examples = result.violations.slice(0, 3);
      const exampleText = examples.map((v) => `  - 「${v.keyword}」`).join("\n");
      const blockMsg = `🚫 「後で」系キーワードが${result.violations.length}回使用されましたが、Issue作成は${result.issueCreationCount}回のみです。

未対応の「後で」発言: ${unaccountedDefers}件
${exampleText}

**対処方法**:
1. 各「後で」発言に対応するIssueを作成してください:
   \`gh issue create --title "..." --body "..." --label P2\`

2. または、発言を修正してIssue番号を含めてください:
   例: 「Issue #1234 で対応予定」

**背景**:
「後で対応」と言うだけではIssueが作成されず、対応が忘れられます。
セッション内で「後で」と発言した回数分、Issueを作成してください。`;

      logHookExecution(
        HOOK_NAME,
        "block",
        `Defer keywords: ${result.violations.length}, Issue creations: ${result.issueCreationCount}, Unaccounted: ${unaccountedDefers}`,
        undefined,
        { sessionId },
      );
      console.log(
        JSON.stringify({
          decision: "block",
          reason: blockMsg,
        }),
      );
      return;
    }

    // Issue作成数が足りている場合は警告のみ
    const warningMsg = `⚠️ 「後で」系キーワードが${result.violations.length}回使用されました（Issue作成: ${result.issueCreationCount}回）。
対応は追跡されていますが、発言とIssueの対応関係を確認してください。`;

    logHookExecution(
      HOOK_NAME,
      "warn",
      `Defer keywords: ${result.violations.length}, Issue creations: ${result.issueCreationCount} (sufficient)`,
      undefined,
      { sessionId },
    );
    console.log(
      JSON.stringify({
        continue: true,
        message: warningMsg,
      }),
    );
    return;
  }

  // Stage 2: 正規表現で検出されなかったが、婉曲表現の可能性がある場合
  if (result.potentialDeferTexts.length > 0) {
    const llmPrompt = buildLlmEvaluationPrompt(result.potentialDeferTexts);
    logHookExecution(
      HOOK_NAME,
      "llm_eval",
      `Potential defer expressions found: ${result.potentialDeferTexts.length}`,
      undefined,
      { sessionId },
    );
    console.log(
      JSON.stringify({
        continue: true,
        systemMessage: llmPrompt,
      }),
    );
    return;
  }

  // 問題なし
  logHookExecution(HOOK_NAME, "approve", "No violations", undefined, { sessionId });
  console.log(JSON.stringify({ continue: true }));
}

if (import.meta.main) {
  main();
}
