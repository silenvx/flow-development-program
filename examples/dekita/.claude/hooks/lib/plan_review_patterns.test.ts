/**
 * plan_review_patterns.ts のテスト
 *
 * Changelog:
 *   - silenvx/dekita#3853: 初期実装
 */

import { describe, expect, test } from "bun:test";
import type { CLIReviewResult } from "./cli_review";
import {
  APPROVAL_PATTERNS,
  QUESTION_PATTERNS,
  checkApproval,
  checkBothApproved,
} from "./plan_review_patterns";

/** テスト用ヘルパー: 成功結果を作成 */
function success(output: string): CLIReviewResult {
  return { available: true, output };
}

/** テスト用ヘルパー: 利用不可結果を作成 */
function unavailable(): CLIReviewResult {
  return { available: false };
}

/** テスト用ヘルパー: 実行失敗結果を作成 */
function failed(): CLIReviewResult {
  return { available: true, output: null };
}

describe("APPROVAL_PATTERNS", () => {
  test("should match Japanese approval patterns", () => {
    const approvalTexts = [
      "この計画は問題なしです。",
      "問題ありません。実装を進めてください。",
      "レビュー結果: 問題なし",
      "レビュー結果: OK",
      "この実装計画は適切です。",
      "特に指摘なし。",
      "このままで問題ありません。",
      "計画は適切です。",
      "修正は不要です。",
    ];

    for (const text of approvalTexts) {
      const hasMatch = APPROVAL_PATTERNS.some((p) => p.test(text));
      expect(hasMatch).toBe(true);
    }
  });

  test("should match English approval patterns", () => {
    const approvalTexts = [
      "No issues found.",
      "No problems detected.",
      "Looks good to me.",
      "LGTM",
      "The plan is approved.",
      "No changes needed.",
      "The plan is well-structured.",
      "I don't have any concerns.",
      "I don't have any major concerns.",
    ];

    for (const text of approvalTexts) {
      const hasMatch = APPROVAL_PATTERNS.some((p) => p.test(text));
      expect(hasMatch).toBe(true);
    }
  });

  test("should not match non-approval text", () => {
    const nonApprovalTexts = [
      "問題があります。",
      "修正が必要です。",
      "セキュリティの考慮が不足しています。",
      "This needs improvement.",
    ];

    for (const text of nonApprovalTexts) {
      const hasMatch = APPROVAL_PATTERNS.some((p) => p.test(text));
      expect(hasMatch).toBe(false);
    }
  });
});

describe("QUESTION_PATTERNS", () => {
  test("should match questions ending with ?", () => {
    const questionTexts = [
      "どのように実装しますか?",
      "What about error handling?",
      "Is this intentional?",
    ];

    for (const text of questionTexts) {
      const hasMatch = QUESTION_PATTERNS.some((p) => p.test(text));
      expect(hasMatch).toBe(true);
    }
  });

  test("should match Japanese question expressions", () => {
    const questionTexts = [
      "この点について確認してください。",
      "検討してほしい点があります。",
      "確認が必要です。",
      "どう対応しますか。",
      "なぜこの方式を選択したのでしょうか。",
    ];

    for (const text of questionTexts) {
      const hasMatch = QUESTION_PATTERNS.some((p) => p.test(text));
      expect(hasMatch).toBe(true);
    }
  });

  test("should match English question expressions", () => {
    const questionTexts = [
      "Should we consider this?",
      "Have you considered using a different approach?",
      "What about the edge cases?",
      "Could you clarify this point?",
      "Why is this necessary?",
      "Is this intentional?",
    ];

    for (const text of questionTexts) {
      const hasMatch = QUESTION_PATTERNS.some((p) => p.test(text));
      expect(hasMatch).toBe(true);
    }
  });

  test("should not match non-question text", () => {
    const nonQuestionTexts = ["実装計画は適切です。", "The plan is well-structured.", "問題なし。"];

    for (const text of nonQuestionTexts) {
      const hasMatch = QUESTION_PATTERNS.some((p) => p.test(text));
      expect(hasMatch).toBe(false);
    }
  });
});

describe("checkApproval", () => {
  test("should return approved=true for approval without questions", () => {
    const result = checkApproval("レビュー結果: 問題なし。実装を進めてください。");
    expect(result.approved).toBe(true);
    expect(result.hasQuestions).toBe(false);
    expect(result.matchedApprovalPatterns.length).toBeGreaterThan(0);
    expect(result.matchedQuestionPatterns.length).toBe(0);
  });

  test("should return approved=false for approval with questions", () => {
    const result = checkApproval("問題なしですが、この点について確認してください。");
    expect(result.approved).toBe(false);
    expect(result.hasQuestions).toBe(true);
    expect(result.matchedApprovalPatterns.length).toBeGreaterThan(0);
    expect(result.matchedQuestionPatterns.length).toBeGreaterThan(0);
  });

  test("should return approved=false for no approval patterns", () => {
    const result = checkApproval("改善が必要です。セキュリティの考慮が不足しています。");
    expect(result.approved).toBe(false);
    expect(result.hasQuestions).toBe(false);
    expect(result.matchedApprovalPatterns.length).toBe(0);
  });

  test("should return approved=false for null input", () => {
    const result = checkApproval(null);
    expect(result.approved).toBe(false);
    expect(result.hasQuestions).toBe(false);
    expect(result.matchedApprovalPatterns.length).toBe(0);
    expect(result.matchedQuestionPatterns.length).toBe(0);
  });

  test("should handle LGTM", () => {
    const result = checkApproval("LGTM! The plan looks solid.");
    expect(result.approved).toBe(true);
    expect(result.hasQuestions).toBe(false);
  });

  test("should detect questions with ? at end of line", () => {
    const result = checkApproval(`問題なし。
ただし、エラーハンドリングはどうしますか?
実装を進めてください。`);
    expect(result.approved).toBe(false);
    expect(result.hasQuestions).toBe(true);
  });
});

describe("checkBothApproved", () => {
  test("should return approved=true when both models approve", () => {
    const result = checkBothApproved(success("レビュー結果: 問題なし"), success("LGTM"));
    expect(result.approved).toBe(true);
    expect(result.geminiResult.approved).toBe(true);
    expect(result.codexResult.approved).toBe(true);
  });

  test("should return approved=false when Gemini has issues", () => {
    const result = checkBothApproved(
      success("セキュリティの考慮が不足しています。"),
      success("LGTM"),
    );
    expect(result.approved).toBe(false);
    expect(result.geminiResult.approved).toBe(false);
    expect(result.codexResult.approved).toBe(true);
  });

  test("should return approved=false when Codex has issues", () => {
    const result = checkBothApproved(
      success("レビュー結果: 問題なし"),
      success("テストが不足しています。"),
    );
    expect(result.approved).toBe(false);
    expect(result.geminiResult.approved).toBe(true);
    expect(result.codexResult.approved).toBe(false);
  });

  test("should return approved=false when both have issues", () => {
    const result = checkBothApproved(
      success("改善が必要です。"),
      success("テストが不足しています。"),
    );
    expect(result.approved).toBe(false);
    expect(result.geminiResult.approved).toBe(false);
    expect(result.codexResult.approved).toBe(false);
  });

  test("should return approved=true when both are unavailable (skip)", () => {
    const result = checkBothApproved(unavailable(), unavailable());
    expect(result.approved).toBe(true);
  });

  test("should use only Gemini when Codex is unavailable", () => {
    const result = checkBothApproved(success("レビュー結果: 問題なし"), unavailable());
    expect(result.approved).toBe(true);
    expect(result.geminiResult.approved).toBe(true);
  });

  test("should use only Codex when Gemini is unavailable", () => {
    const result = checkBothApproved(unavailable(), success("LGTM"));
    expect(result.approved).toBe(true);
    expect(result.codexResult.approved).toBe(true);
  });

  test("should fail when only available model has issues", () => {
    const result = checkBothApproved(success("改善が必要です。"), unavailable());
    expect(result.approved).toBe(false);
    expect(result.geminiResult.approved).toBe(false);
  });

  test("should fail when approval has questions", () => {
    const result = checkBothApproved(
      success("問題なしですが、確認してください。"),
      success("LGTM"),
    );
    expect(result.approved).toBe(false);
    expect(result.geminiResult.approved).toBe(false);
    expect(result.geminiResult.hasQuestions).toBe(true);
    expect(result.codexResult.approved).toBe(true);
  });

  test("should treat execution failure (available=true, output=null) as not approved", () => {
    const result = checkBothApproved(failed(), success("LGTM"));
    expect(result.approved).toBe(false);
    expect(result.geminiResult.approved).toBe(false);
    expect(result.codexResult.approved).toBe(true);
  });

  test("should treat both execution failures as not approved", () => {
    const result = checkBothApproved(failed(), failed());
    expect(result.approved).toBe(false);
  });
});
