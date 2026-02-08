#!/usr/bin/env bun
/**
 * AskUserQuestionの選択肢にメリット/デメリット分析が含まれているか確認する。
 *
 * Why:
 *   選択肢を提示する際、メリット/デメリット/コストの説明がないと
 *   ユーザーが適切な判断を下せない。十分な情報提供を強制する。
 *
 * What:
 *   - AskUserQuestionツールの呼び出しを検出
 *   - 各選択肢のlabel/descriptionにメリット・デメリット・コストを確認
 *   - 3つのうち2つ以上がない場合はブロック
 *
 * Remarks:
 *   - ブロック型フック（説明不足時はブロック）
 *   - PreToolUse:AskUserQuestionで発火
 *   - [fact-check]/[事実確認]タグで事実確認質問はスキップ可能
 *   - 2選択肢未満の場合は判定せずスキップ
 *
 * Changelog:
 *   - silenvx/dekita#1894: フック追加
 *   - silenvx/dekita#2237: ブロック型に変更
 *   - silenvx/dekita#2305: 事実確認タグでスキップ機能追加
 *   - silenvx/dekita#2917: TypeScriptに移植
 */

import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult, outputResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "merit-demerit-check";

// Keywords indicating merit/demerit analysis is present
const MERIT_KEYWORDS_JA = ["メリット", "利点", "長所", "良い点", "利便性", "強み"];

const DEMERIT_KEYWORDS_JA = ["デメリット", "欠点", "短所", "問題点", "リスク", "弱み", "懸念"];

const COST_KEYWORDS_JA = [
  "コスト",
  "実装コスト",
  "運用コスト",
  "工数",
  "負担",
  "実装が複雑",
  "構成が複雑",
  "複雑性",
  "複雑になる",
];

const MERIT_KEYWORDS_EN = ["merit", "advantage", "benefit", "pros", "strength", "upside"];

const DEMERIT_KEYWORDS_EN = [
  "demerit",
  "disadvantage",
  "drawback",
  "cons",
  "weakness",
  "downside",
  "risk",
  "concern",
];

const COST_KEYWORDS_EN = ["cost", "maintenance", "complexity", "overhead", "effort"];

// Minimum number of options to trigger the check
const MIN_OPTIONS_FOR_CHECK = 2;

// Regex pattern to skip merit/demerit check (Issue #2305)
const FACT_CHECK_REGEX = /^\s*(?:\[fact-check\]|\[事実確認\])|(?:\[fact-check\]|\[事実確認\])\s*$/i;

export interface Option {
  label?: string;
  description?: string;
}

interface Question {
  question?: string;
  options?: Option[];
}

export interface AnalysisResult {
  totalOptions: number;
  hasMerit: boolean;
  hasDemerit: boolean;
  hasCost: boolean;
  optionsWithoutContext: string[];
}

/**
 * Check if question contains fact-check skip tag at start or end.
 */
export function isFactCheckQuestion(questionText: string): boolean {
  return FACT_CHECK_REGEX.test(questionText);
}

/**
 * Check if any keyword exists as a whole word in text.
 */
export function matchAnyWordBoundary(keywords: string[], text: string): boolean {
  if (keywords.length === 0) {
    return false;
  }
  const escaped = keywords.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`\\b(${escaped.join("|")})\\b`, "i");
  return pattern.test(text);
}

/**
 * Check if text contains merit-related keywords.
 */
export function hasMeritContext(text: string): boolean {
  // Japanese keywords: substring match
  if (MERIT_KEYWORDS_JA.some((keyword) => text.includes(keyword))) {
    return true;
  }
  // English keywords: word boundary match
  return matchAnyWordBoundary(MERIT_KEYWORDS_EN, text);
}

/**
 * Check if text contains demerit-related keywords.
 */
export function hasDemeritContext(text: string): boolean {
  // Japanese keywords: substring match
  if (DEMERIT_KEYWORDS_JA.some((keyword) => text.includes(keyword))) {
    return true;
  }
  // English keywords: word boundary match
  return matchAnyWordBoundary(DEMERIT_KEYWORDS_EN, text);
}

/**
 * Check if text contains cost-related keywords.
 */
export function hasCostContext(text: string): boolean {
  // Japanese keywords: substring match
  if (COST_KEYWORDS_JA.some((keyword) => text.includes(keyword))) {
    return true;
  }
  // English keywords: word boundary match
  return matchAnyWordBoundary(COST_KEYWORDS_EN, text);
}

/**
 * Analyze options for merit/demerit/cost coverage.
 */
export function analyzeOptions(options: Option[]): AnalysisResult {
  const result: AnalysisResult = {
    totalOptions: options.length,
    hasMerit: false,
    hasDemerit: false,
    hasCost: false,
    optionsWithoutContext: [],
  };

  for (const opt of options) {
    const label = opt.label ?? "";
    const description = opt.description ?? "";
    const combinedText = `${label} ${description}`;

    const optHasMerit = hasMeritContext(combinedText);
    const optHasDemerit = hasDemeritContext(combinedText);
    const optHasCost = hasCostContext(combinedText);

    result.hasMerit = result.hasMerit || optHasMerit;
    result.hasDemerit = result.hasDemerit || optHasDemerit;
    result.hasCost = result.hasCost || optHasCost;

    // Track options without any context
    if (!optHasMerit && !optHasDemerit && !optHasCost) {
      const truncatedLabel = label.length > 30 ? `${label.slice(0, 30)}...` : label;
      result.optionsWithoutContext.push(truncatedLabel);
    }
  }

  return result;
}

/**
 * Format block message for missing context.
 */
export function formatBlockMessage(analysis: AnalysisResult, question: string): string {
  const missing: string[] = [];
  if (!analysis.hasMerit) {
    missing.push("メリット/利点");
  }
  if (!analysis.hasDemerit) {
    missing.push("デメリット/欠点");
  }
  if (!analysis.hasCost) {
    missing.push("コスト/工数");
  }

  let optionsInfo = "";
  if (analysis.optionsWithoutContext.length > 0) {
    optionsInfo = `\n詳細不足の選択肢: ${analysis.optionsWithoutContext.join(", ")}`;
  }

  const truncatedQuestion = question.length > 50 ? `${question.slice(0, 50)}...` : question;

  return `🚫 選択肢の説明が不十分なためブロックしました。

質問: ${truncatedQuestion}

不足している観点: ${missing.join(", ")}${optionsInfo}

【必須】各選択肢のdescriptionに以下を追記してください:
- メリット/利点（例: 確実に対応される、フローを止めずに改善を促せる）
- デメリット/リスク（例: 軽微なケースでも止まる、強制力がない）
- コスト/工数（例: 実装不要、Claude側の対応ロジックが必要）

💡 ブロック後も作業を継続してください。
   AskUserQuestionを修正して再度呼び出してください。`;
}

async function main(): Promise<void> {
  let inputData: Record<string, unknown>;
  let sessionId: string | undefined;
  try {
    inputData = await parseHookInput();
    const ctx = createHookContext(inputData);
    sessionId = ctx.sessionId;
  } catch {
    // Invalid input - approve silently
    outputResult({});
  }

  const toolName = (inputData.tool_name as string) ?? "";

  // Only check AskUserQuestion
  if (toolName !== "AskUserQuestion") {
    outputResult({});
  }

  const toolInput = (inputData.tool_input as Record<string, unknown>) ?? {};
  const questions = (toolInput.questions as Question[]) ?? [];

  if (questions.length === 0) {
    outputResult({});
  }

  // Check each question's options
  const blockMessages: string[] = [];
  let factCheckSkipCount = 0;
  let sufficientContextCount = 0;

  for (const q of questions) {
    const options = q.options ?? [];
    const questionText = q.question ?? "";

    // Skip if fewer than 2 options (not a real choice)
    if (options.length < MIN_OPTIONS_FOR_CHECK) {
      continue;
    }

    // Issue #2305: Skip fact-check questions
    if (isFactCheckQuestion(questionText)) {
      factCheckSkipCount++;
      continue;
    }

    const analysis = analyzeOptions(options);

    // Check if sufficient context is provided
    // Require at least 2 of 3 categories to be covered
    const coverageCount = [analysis.hasMerit, analysis.hasDemerit, analysis.hasCost].filter(
      Boolean,
    ).length;

    if (coverageCount < 2) {
      blockMessages.push(formatBlockMessage(analysis, questionText));
    } else {
      sufficientContextCount++;
    }
  }

  // Block if options lack sufficient context
  if (blockMessages.length > 0) {
    const combinedMessage = blockMessages.join("\n\n");
    const result = makeBlockResult(HOOK_NAME, combinedMessage);
    outputResult(result);
  } else {
    // Build accurate log message
    let reason: string;
    if (factCheckSkipCount > 0 && sufficientContextCount > 0) {
      reason = "一部事実確認タグでスキップ、残りは選択肢に十分な説明あり";
    } else if (factCheckSkipCount > 0) {
      reason = "事実確認タグでスキップ";
    } else {
      reason = "選択肢に十分な説明あり";
    }
    await logHookExecution(HOOK_NAME, "approve", reason, undefined, { sessionId });
    const result = makeApproveResult(HOOK_NAME);
    outputResult(result);
  }
}

if (import.meta.main) {
  main();
}
