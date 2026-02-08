/**
 * Plan AIレビューの判定パターン
 *
 * Why:
 *   イテレーティブPlan AIレビューシステムで使用する判定パターンを一元管理
 *
 * What:
 *   - APPROVAL_PATTERNS: 「問題なし」判定パターン（日本語/英語）
 *   - QUESTION_PATTERNS: 疑問点・質問検出パターン（問題あり扱い）
 *   - checkApproval(): 出力が明示的に承認しているか判定
 *
 * Remarks:
 *   - 両モデル（Gemini/Codex）が明示的に「問題なし」と宣言する必要がある
 *   - 疑問点や質問は「問題あり」として扱う
 *
 * Changelog:
 *   - silenvx/dekita#3859: checkBothApproved引数をCLIReviewResultに変更
 *   - silenvx/dekita#3853: 初期実装（イテレーティブPlan AIレビュー）
 */

import type { CLIReviewResult } from "./cli_review";

/**
 * 「問題なし」判定パターン（日本語/英語）
 *
 * AIレビュー出力がこれらのパターンにマッチする場合、明示的な承認とみなす。
 */
export const APPROVAL_PATTERNS: RegExp[] = [
  // 日本語
  /問題(なし|ない|ありません|は見つかりません)/i,
  /特に指摘(なし|する点はありません)/i,
  /レビュー結果:\s*(OK|問題なし|承認)/i,
  /実装計画(は適切|に問題はありません)/i,
  /このままで(問題ありません|大丈夫)/i,
  /計画(は|が)適切/i,
  /修正(は|が)不要/i,

  // 英語
  /no\s+(issues?|problems?|concerns?)\s+(found|detected|identified)/i,
  /looks?\s+good(\s+to\s+me)?/i,
  /LGTM/i,
  /plan\s+(is\s+)?(approved|acceptable|sound)/i,
  /no\s+changes?\s+(needed|required|necessary)/i,
  /approve[ds]?\s*(the\s+plan)?/i,
  /plan\s+is\s+(well[- ]structured|comprehensive|solid)/i,
  /I\s+don't\s+have\s+any\s+(major\s+)?concerns?/i,
];

/**
 * 疑問点・質問検出パターン（問題あり扱い）
 *
 * これらのパターンが検出された場合、たとえ承認パターンがあっても問題ありとして扱う。
 * 質問や確認事項は、計画に曖昧な点があることを示唆する。
 */
export const QUESTION_PATTERNS: RegExp[] = [
  // 行末の疑問符（複数の?にも対応）
  /\?+\s*$/m,
  // 日本語の確認・質問表現（依頼形のみを検出、過去形「〜しました」を除外）
  /について確認(して(ください|ほしい)|が必要)/i,
  /(検討|考慮)して(ください|ほしい)/i,
  /確認(が必要|してください)/i,
  /どう(対応しますか|しますか)/i,
  /なぜ.+(でしょうか)/i,
  // 英語の確認・質問表現
  /should\s+(we|you|this)/i,
  /have\s+you\s+considered/i,
  /what\s+(about|if)/i,
  /could\s+you\s+(clarify|explain)/i,
  /why\s+(is|are|did|do|would)/i,
  /is\s+this\s+(intentional|correct|necessary)/i,
];

/**
 * 承認判定結果
 */
export interface ApprovalCheckResult {
  /** 明示的に承認されているか */
  approved: boolean;
  /** 疑問点・質問が検出されたか */
  hasQuestions: boolean;
  /** マッチした承認パターン（デバッグ用） */
  matchedApprovalPatterns: string[];
  /** マッチした質問パターン（デバッグ用） */
  matchedQuestionPatterns: string[];
}

/**
 * 出力が明示的に承認しているかチェック
 *
 * 承認条件:
 * 1. APPROVAL_PATTERNSのいずれかにマッチ
 * 2. かつ、QUESTION_PATTERNSのいずれにもマッチしない
 *
 * @param output AIレビュー出力
 * @returns 承認判定結果
 */
export function checkApproval(output: string | null): ApprovalCheckResult {
  if (!output) {
    return {
      approved: false,
      hasQuestions: false,
      matchedApprovalPatterns: [],
      matchedQuestionPatterns: [],
    };
  }

  // 承認パターンのマッチ確認
  const matchedApprovalPatterns: string[] = [];
  for (const pattern of APPROVAL_PATTERNS) {
    if (pattern.test(output)) {
      matchedApprovalPatterns.push(pattern.source);
    }
  }

  // 質問パターンのマッチ確認
  const matchedQuestionPatterns: string[] = [];
  for (const pattern of QUESTION_PATTERNS) {
    if (pattern.test(output)) {
      matchedQuestionPatterns.push(pattern.source);
    }
  }

  const hasQuestions = matchedQuestionPatterns.length > 0;
  const hasApproval = matchedApprovalPatterns.length > 0;

  // 承認: 承認パターンにマッチ かつ 質問パターンにマッチしない
  const approved = hasApproval && !hasQuestions;

  return {
    approved,
    hasQuestions,
    matchedApprovalPatterns,
    matchedQuestionPatterns,
  };
}

/**
 * 両モデルの出力から総合承認判定を行う
 *
 * 条件:
 * - Gemini出力が承認 AND Codex出力が承認
 * - 利用不可（available: false）の場合は、利用可能な方のみで判定
 * - 実行失敗（available: true, output: null）は「未承認」として扱う
 * - 両方利用不可の場合は承認（スキップ扱い）
 *
 * @param geminiReview Gemini CLIレビュー結果
 * @param codexReview Codex CLIレビュー結果
 * @returns 総合承認結果
 */
export function checkBothApproved(
  geminiReview: CLIReviewResult,
  codexReview: CLIReviewResult,
): {
  approved: boolean;
  geminiResult: ApprovalCheckResult;
  codexResult: ApprovalCheckResult;
} {
  const geminiOutput = geminiReview.available ? geminiReview.output : null;
  const codexOutput = codexReview.available ? codexReview.output : null;

  const geminiResult = checkApproval(geminiOutput);
  const codexResult = checkApproval(codexOutput);

  // 両方利用不可 → スキップ（承認扱い）
  if (!geminiReview.available && !codexReview.available) {
    return {
      approved: true,
      geminiResult,
      codexResult,
    };
  }

  // 片方のみ利用可能 → その結果で判定
  if (!geminiReview.available) {
    return {
      approved: codexResult.approved,
      geminiResult,
      codexResult,
    };
  }
  if (!codexReview.available) {
    return {
      approved: geminiResult.approved,
      geminiResult,
      codexResult,
    };
  }

  // 両方利用可能 → 両方が承認している必要がある
  return {
    approved: geminiResult.approved && codexResult.approved,
    geminiResult,
    codexResult,
  };
}
