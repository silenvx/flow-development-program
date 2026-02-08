#!/usr/bin/env bun
/**
 * イテレーティブPlan AIレビューフック（PreToolUse:ExitPlanMode）
 *
 * Why:
 *   Plan段階でGemini/Codexが両方とも明示的に「問題なし」と宣言するまで
 *   イテレーティブにレビューを繰り返すことで、設計品質を担保する。
 *
 * What:
 *   - ExitPlanMode呼び出し前に発火（PreToolUse）
 *   - Gemini/Codex並列レビュー実行
 *   - 両モデルが「問題なし」と判定した場合のみ承認
 *   - 問題がある場合はブロックし、修正を促す
 *   - イテレーション状態を追跡
 *
 * Remarks:
 *   - PreToolUse:ExitPlanModeで発火
 *   - 既存のplan_ai_review.ts（PostToolUse）を置き換え
 *   - 環境変数PLAN_REVIEW_ITERATIVE=1で有効化（移行期間中）
 *
 * Changelog:
 *   - silenvx/dekita#3853: 初期実装
 */

import { readFileSync } from "node:fs";
import { type CLIReviewResult, runCLIReview } from "../lib/cli_review";
import {
  PLAN_REVIEW_ABSOLUTE_TIMEOUT_MINUTES,
  PLAN_REVIEW_MAX_ITERATIONS_FOR_CONFIRM,
} from "../lib/constants";
import { formatError } from "../lib/format_error";
import { checkBothApproved } from "../lib/plan_review_patterns";
import {
  type PlanReviewIteration,
  type PlanReviewState,
  addIterationToState,
  clearPlanReviewState,
  createInitialState,
  loadPlanReviewState,
  resetIterationCount,
  savePlanReviewState,
  simpleHash,
} from "../lib/plan_review_state";
import { approveAndExit, blockAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";
import type { HookResult } from "../lib/types";
import {
  type PlanFinding,
  type PlanReviewResult,
  detectPlanBlockingFindings,
  getLatestPlanFile,
  getReviewOutput,
  isCodexAvailable,
  isGeminiAvailable,
} from "./plan_ai_review";

const HOOK_NAME = "plan-ai-review-iterative";

/** イテレーティブレビュー用のプロンプト（明確な判定を要求） */
export const PLAN_REVIEW_PROMPT_ITERATIVE = `以下の実装計画をレビューしてください。

## レビュー観点
1. 技術的実現性: 提案されたアプローチはコードベースと整合しているか
2. 影響範囲: 変更ファイル数、既存機能への影響は適切か
3. 設計妥当性: 結合度・凝集度は適切か、既存パターンに従っているか
4. セキュリティ考慮: セキュリティリスクは考慮されているか
5. テスト計画: テスト方針は明確か
6. 構成要素の網羅性: Issue本文のWhy（背景・目的）、What（現状・再現手順）、How（解決策の全項目）が計画に反映されているか

## 重要: 明確な判定を出力してください

### 問題がある場合
優先度バッジを付けて具体的に指摘してください:
- ![high] 重大な問題の説明
- ![medium] 中程度の問題の説明
- ![low] 軽微な問題の説明

### 問題がない場合
以下のいずれかを明記してください（必須）:
- "レビュー結果: 問題なし"
- "No issues found"
- "LGTM"

**注意**: 質問や確認事項がある場合は「問題あり」として扱われます。
計画に曖昧な点がある場合は、質問ではなく改善提案として記載してください。

---
`;

/**
 * Gemini CLIでイテレーティブレビューを実行
 *
 * 利用可能チェックを統合し、CLI未インストール時は { available: false } を返す。
 * イテレーティブ用プロンプト（PLAN_REVIEW_PROMPT_ITERATIVE）を使用して
 * 明確な承認/拒否判定を要求する。
 */
async function runGeminiReviewIterative(planContent: string): Promise<CLIReviewResult> {
  if (!(await isGeminiAvailable())) {
    return { available: false };
  }
  const systemPrompt = "あなたは実装計画レビューアーです。簡潔に日本語でレビューしてください。";
  const prompt = `${systemPrompt}\n\n${PLAN_REVIEW_PROMPT_ITERATIVE}${planContent}`;
  return runCLIReview(["gemini", "--approval-mode", "default"], prompt);
}

/**
 * Codex CLIでイテレーティブレビューを実行
 *
 * 利用可能チェックを統合し、CLI未インストール時は { available: false } を返す。
 * イテレーティブ用プロンプト（PLAN_REVIEW_PROMPT_ITERATIVE）を使用して
 * 明確な承認/拒否判定を要求する。
 */
async function runCodexReviewIterative(planContent: string): Promise<CLIReviewResult> {
  if (!(await isCodexAvailable())) {
    return { available: false };
  }
  const prompt = `${PLAN_REVIEW_PROMPT_ITERATIVE}${planContent}`;
  return runCLIReview(["codex", "exec"], prompt);
}

/**
 * Gemini + Codexを並列実行（イテレーティブ用）
 *
 * @param planContent レビュー対象のPlanコンテンツ
 */
async function runParallelPlanReviewIterative(planContent: string): Promise<PlanReviewResult> {
  const [gemini, codex] = await Promise.all([
    runGeminiReviewIterative(planContent),
    runCodexReviewIterative(planContent),
  ]);

  return { gemini, codex };
}

/**
 * 承認時のメッセージをフォーマット
 */
function formatApproveMessage(state: PlanReviewState, reviewResult: PlanReviewResult): string {
  const sections: string[] = [];
  const geminiOutput = getReviewOutput(reviewResult.gemini);
  const codexOutput = getReviewOutput(reviewResult.codex);

  sections.push(`📋 Plan AIレビュー完了 - イテレーション ${state.iterationCount}

## レビュー結果
| モデル | 判定 |
|--------|------|
| Gemini | ${geminiOutput ? "✅ 承認" : "⏭️ スキップ"} |
| Codex  | ${codexOutput ? "✅ 承認" : "⏭️ スキップ"} |

両モデルが計画を承認しました。実装を開始できます。`);

  // Gemini結果
  if (geminiOutput) {
    sections.push(`**Gemini Review:**
${geminiOutput}`);
  }

  // Codex結果
  if (codexOutput) {
    sections.push(`**Codex Review:**
${codexOutput}`);
  }

  return sections.join("\n\n");
}

/**
 * ブロック時のメッセージをフォーマット
 */
function formatBlockMessage(
  state: PlanReviewState,
  reviewResult: PlanReviewResult,
  findings: PlanFinding[],
  geminiApproved: boolean,
  codexApproved: boolean,
  hasGeminiQuestions: boolean,
  hasCodexQuestions: boolean,
): string {
  const sections: string[] = [];

  const geminiOutput = getReviewOutput(reviewResult.gemini);
  const codexOutput = getReviewOutput(reviewResult.codex);

  // ヘッダーとレビュー結果表
  const geminiStatus = !reviewResult.gemini.available
    ? "⏭️ 利用不可"
    : reviewResult.gemini.output === null
      ? "⚠️ 実行エラー"
      : geminiApproved
        ? "✅ 承認"
        : hasGeminiQuestions
          ? "❓ 質問あり"
          : "❌ 要修正";

  const codexStatus = !reviewResult.codex.available
    ? "⏭️ 利用不可"
    : reviewResult.codex.output === null
      ? "⚠️ 実行エラー"
      : codexApproved
        ? "✅ 承認"
        : hasCodexQuestions
          ? "❓ 質問あり"
          : "❌ 要修正";

  sections.push(`📋 Plan AIレビュー - イテレーション ${state.iterationCount}

## レビュー結果
| モデル | 判定 |
|--------|------|
| Gemini | ${geminiStatus} |
| Codex  | ${codexStatus} |`);

  // 指摘事項
  if (findings.length > 0) {
    const findingsSummary = findings
      .map((f) => {
        const truncated = f.snippet.length > 80 ? `${f.snippet.slice(0, 80)}...` : f.snippet;
        return `- [${f.severity}] (${f.source}): ${truncated}`;
      })
      .join("\n");

    sections.push(`## 対応が必要な項目 (${findings.length}件)
${findingsSummary}`);
  }

  // 質問検出時の注意
  if (hasGeminiQuestions || hasCodexQuestions) {
    sections.push(`## 注意: 質問が検出されました
計画に曖昧な点があります。質問に対応するか、計画を明確化してください。`);
  }

  // Gemini結果
  if (geminiOutput) {
    sections.push(`**Gemini Review:**
${geminiOutput}`);
  }

  // Codex結果
  if (codexOutput) {
    sections.push(`**Codex Review:**
${codexOutput}`);
  }

  sections.push(`---
*両モデルが「問題なし」と判定するまでExitPlanModeはブロックされます。*
*計画を修正して再度ExitPlanModeを実行してください。*`);

  return sections.join("\n\n");
}

/**
 * タイムアウトチェック
 */
function isTimedOut(state: PlanReviewState): boolean {
  const startedAt = new Date(state.startedAt).getTime();
  const now = Date.now();
  const elapsedMinutes = (now - startedAt) / 1000 / 60;
  return elapsedMinutes >= PLAN_REVIEW_ABSOLUTE_TIMEOUT_MINUTES;
}

/**
 * イテレーション上限チェック（ユーザー確認要求）
 */
function needsUserConfirmation(state: PlanReviewState): boolean {
  return state.iterationCount >= PLAN_REVIEW_MAX_ITERATIONS_FOR_CONFIRM;
}

/**
 * メイン処理
 */
async function main(): Promise<void> {
  const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();

  try {
    const input = await parseHookInput();

    // ExitPlanMode以外は無視
    if (input.tool_name !== "ExitPlanMode") {
      approveAndExit(HOOK_NAME);
    }

    // 環境変数でイテレーティブモードが有効か確認（移行期間中）
    if (process.env.PLAN_REVIEW_ITERATIVE !== "1") {
      // 無効の場合は既存のplan_ai_review.ts（PostToolUse）に任せる
      approveAndExit(HOOK_NAME);
    }

    // sessionIdを取得（input.session_id優先、環境変数をフォールバック）
    const sessionId = input.session_id || process.env.CLAUDE_SESSION_ID;

    // セッションIDがない場合はスキップ
    if (!sessionId) {
      console.error(`[${HOOK_NAME}] No session ID, skipping`);
      approveAndExit(HOOK_NAME);
    }

    // 最新のplanファイルを取得
    const planFile = getLatestPlanFile(projectDir);
    if (!planFile) {
      console.error(`[${HOOK_NAME}] No plan file found, skipping`);
      approveAndExit(HOOK_NAME);
    }

    // planファイルの内容を読み込み
    const planContent = readFileSync(planFile, "utf-8");
    if (!planContent.trim()) {
      console.error(`[${HOOK_NAME}] Empty plan file, skipping`);
      approveAndExit(HOOK_NAME);
    }

    // 状態ファイルを読み込みまたは初期化
    let state = loadPlanReviewState(projectDir, sessionId);
    if (!state || state.planFile !== planFile) {
      // 状態がない、またはPlanファイルが変更された場合は初期化
      state = createInitialState(sessionId, planFile);
    }

    // タイムアウトチェック
    if (isTimedOut(state)) {
      console.error(
        `[${HOOK_NAME}] Timeout (${PLAN_REVIEW_ABSOLUTE_TIMEOUT_MINUTES} min), forcing approval`,
      );
      clearPlanReviewState(projectDir, sessionId);
      approveAndExit(HOOK_NAME);
    }

    // Plan内容のハッシュを計算（チェック順序変更: ハッシュ計算を先に）
    const planHash = simpleHash(planContent);

    // 直前のイテレーションと同じPlanならスキップ（変更なしでの再試行防止）
    const lastIteration = state.reviews[state.reviews.length - 1];
    if (
      lastIteration &&
      lastIteration.planHash === planHash &&
      lastIteration.result === "blocked"
    ) {
      blockAndExit(
        HOOK_NAME,
        `📋 Plan AIレビュー

計画に変更がありません。前回のレビュー指摘を反映してください。

---
*計画を修正してから再度ExitPlanModeを実行してください。*`,
      );
    }

    // プランハッシュが変更された場合、かつ上限に達している場合のみカウンタをリセット
    // 通常のレビューサイクル中は累積させ、上限到達後のプラン変更時のみリセット
    if (
      lastIteration &&
      lastIteration.planHash !== planHash &&
      state.iterationCount >= PLAN_REVIEW_MAX_ITERATIONS_FOR_CONFIRM
    ) {
      console.info(`[${HOOK_NAME}] Plan changed after reaching iteration limit, resetting counter`);
      state = resetIterationCount(state);
    }

    // イテレーション上限チェック（リセット後に実行）
    if (needsUserConfirmation(state)) {
      const message = `📋 Plan AIレビュー - ${state.iterationCount}回のイテレーションに達しました

レビューが収束していません。以下のいずれかを選択してください:
1. 計画を大幅に見直す
2. PLAN_REVIEW_ITERATIVE=0 で一時的に無効化

---
*${PLAN_REVIEW_MAX_ITERATIONS_FOR_CONFIRM}回以上のイテレーションはユーザー確認が必要です。*`;

      blockAndExit(HOOK_NAME, message);
    }

    // 並列レビュー実行（イテレーティブ用プロンプト使用、利用可能チェックは各関数内で実行）
    const reviewResult = await runParallelPlanReviewIterative(planContent);

    // 両方利用不可ならスキップ
    if (!reviewResult.gemini.available && !reviewResult.codex.available) {
      console.error(`[${HOOK_NAME}] Neither Gemini nor Codex available, skipping`);
      approveAndExit(HOOK_NAME);
    }

    const geminiOutput = getReviewOutput(reviewResult.gemini);
    const codexOutput = getReviewOutput(reviewResult.codex);

    // 承認判定
    const { approved, geminiResult, codexResult } = checkBothApproved(
      reviewResult.gemini,
      reviewResult.codex,
    );

    // ブロック対象の指摘を検出
    const blockingFindings = detectPlanBlockingFindings(geminiOutput, codexOutput);

    // イテレーション結果を記録
    // Note: iteration番号はreviews配列の長さを使用（リセット後も一意の番号を維持）
    const iteration: PlanReviewIteration = {
      iteration: state.reviews.length + 1,
      timestamp: new Date().toISOString(),
      gemini: geminiOutput ? geminiResult : null,
      codex: codexOutput ? codexResult : null,
      geminiOutput,
      codexOutput,
      planHash,
      result: approved && blockingFindings.length === 0 ? "approved" : "blocked",
    };

    // 状態を更新
    state = addIterationToState(state, iteration);
    savePlanReviewState(projectDir, state);

    // 承認判定結果に基づいて処理
    if (approved && blockingFindings.length === 0) {
      // 承認
      clearPlanReviewState(projectDir, sessionId);

      const systemMessage = formatApproveMessage(state, reviewResult);
      console.error(`\n${systemMessage}\n`);

      const result: HookResult = {
        systemMessage,
      };
      console.log(JSON.stringify(result));
      process.exit(0);
    }

    // ブロック
    const message = formatBlockMessage(
      state,
      reviewResult,
      blockingFindings,
      geminiResult.approved,
      codexResult.approved,
      geminiResult.hasQuestions,
      codexResult.hasQuestions,
    );

    blockAndExit(HOOK_NAME, message);
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    approveAndExit(HOOK_NAME);
  }
}

// 実行（テスト時はスキップ）
if (import.meta.main) {
  main();
}
