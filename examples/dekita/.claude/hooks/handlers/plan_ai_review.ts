#!/usr/bin/env bun
/**
 * ExitPlanMode時にPlanファイルをGemini CLIでレビューする
 *
 * Why:
 *   Plan段階でAIレビューを実行することで、技術的実現性や設計妥当性を
 *   実装前に検証できる。Issue作成時のAIレビューは冗長だったが、
 *   Plan段階ではコードベース調査後の具体的な計画があるため価値が高い。
 *
 * What:
 *   - ExitPlanMode成功後に発火
 *   - .claude/plans/および~/.claude/plans/配下の最新planファイルを検出
 *   - Gemini CLIでレビューを実行
 *   - レビュー結果をsystemMessageでClaudeに通知
 *
 * Remarks:
 *   - PostToolUse:ExitPlanModeで発火
 *   - バックグラウンド実行（ブロッキングなし）
 *   - Gemini CLI単体使用（Codexは精度問題のため除外）
 *
 * Changelog:
 *   - silenvx/dekita#3179: 初期実装（Issue AIレビューからの移行）
 */

import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
} from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { type CLIReviewResult, runCLIReview } from "../lib/cli_review";
import {
  BLOCKING_SEVERITIES,
  CODEX_PRIORITY_BADGES,
  GEMINI_PRIORITY_BADGES,
  GEMINI_SECURITY_BADGES,
} from "../lib/constants";
import { formatError } from "../lib/format_error";
import { extractIssueNumberFromBranch } from "../lib/git";
import { approveAndExit, blockAndExit } from "../lib/results";
import { getToolResult, isToolResultError, parseHookInput } from "../lib/session";
import type { HookResult } from "../lib/types";

const HOOK_NAME = "plan-ai-review";
const PLANS_DIR = ".claude/plans";
const METRICS_LOG_FILE = ".claude/logs/plan-ai-review-metrics.jsonl";
const DETAIL_LOG_FILE = ".claude/logs/plan-ai-review-details.jsonl";

/** メトリクスのレビュー結果タイプ */
export type MetricsResult = "reviewed" | "skipped" | "error" | "blocked";

/** メトリクスログエントリの型 */
export interface PlanReviewMetrics {
  timestamp: string;
  issue_number: string | null;
  plan_file: string | null;
  result: MetricsResult;
  session_id: string | null;
  review_length: number | null;
  /** Gemini CLIが利用可能だったか */
  gemini_available?: boolean;
  /** Codex CLIが利用可能だったか */
  codex_available?: boolean;
  /** ブロック対象の指摘件数 */
  blocking_findings_count?: number;
  /** Gemini出力のプレビュー（最初の500文字） */
  gemini_output_preview?: string | null;
  /** Codex出力のプレビュー（最初の500文字） */
  codex_output_preview?: string | null;
}

/** 詳細ログエントリの型（Issue #3843） */
export interface PlanReviewDetailLog {
  timestamp: string;
  session_id: string | null;
  plan_file: string;
  gemini_output: string | null;
  codex_output: string | null;
  pattern_matches: {
    gemini: Array<{ pattern: string; matched: boolean }>;
    codex: Array<{ pattern: string; matched: boolean }>;
  };
}

/** 並列レビュー結果の型 */
export interface PlanReviewResult {
  gemini: CLIReviewResult;
  codex: CLIReviewResult;
}

/** CLIReviewResultからoutputを取得するヘルパー */
export function getReviewOutput(result: CLIReviewResult): string | null {
  return result.available ? result.output : null;
}

/** ブロック対象の指摘の型 */
export interface PlanFinding {
  severity: string;
  source: "codex" | "gemini";
  snippet: string;
}

/** Plan向けレビュープロンプト（テストからも参照するためexport） */
export const PLAN_REVIEW_PROMPT = `以下の実装計画をレビューしてください。

レビュー観点:
1. 技術的実現性: 提案されたアプローチはコードベースと整合しているか
2. 影響範囲: 変更ファイル数、既存機能への影響は適切か
3. 設計妥当性: 結合度・凝集度は適切か、既存パターンに従っているか
4. セキュリティ考慮: セキュリティリスクは考慮されているか
5. テスト計画: テスト方針は明確か
6. 構成要素の網羅性: Issue本文のWhy（背景・目的）、What（現状・再現手順）、How（解決策の全項目）が計画に反映されているか

改善提案があれば具体的に指摘してください。

---
`;

/**
 * メトリクスをJSONLファイルに記録
 *
 * Why: Plan AIレビューの効果を測定するため、レビュー実行状況を記録する
 * What: timestamp, issue_number, plan_file, result, session_id, review_lengthを記録
 * How: .claude/logs/plan-ai-review-metrics.jsonlにJSONL形式で追記
 *
 * Changelog: silenvx/dekita#3208: 初期実装
 */
export function appendMetricsLog(
  projectDir: string,
  metrics: Omit<PlanReviewMetrics, "timestamp" | "session_id">,
): void {
  try {
    const logPath = resolve(projectDir, METRICS_LOG_FILE);
    const logDir = dirname(logPath);

    // ディレクトリが存在しなければ作成
    if (!existsSync(logDir)) {
      mkdirSync(logDir, { recursive: true });
    }

    const entry: PlanReviewMetrics = {
      timestamp: new Date().toISOString(),
      session_id: process.env.CLAUDE_SESSION_ID || null,
      ...metrics,
    };

    appendFileSync(logPath, `${JSON.stringify(entry)}\n`);
  } catch {
    // メトリクス記録の失敗はサイレントに無視（フック本体の動作を妨げない）
  }
}

/**
 * 出力プレビューを生成（最初の500文字）
 *
 * Issue #3843: Plan AIレビューの実効性検証のため
 */
export function getOutputPreview(output: string | null): string | null {
  if (!output) return null;
  return output.length > 500 ? output.slice(0, 500) : output;
}

/**
 * パターンマッチング結果を生成
 *
 * Issue #3843: 検出ロジックのデバッグ用
 */
export function getPatternMatches(
  output: string | null,
  patterns: Record<string, RegExp>,
): Array<{ pattern: string; matched: boolean }> {
  if (!output) return [];

  return Object.entries(patterns).map(([severity, pattern]) => ({
    pattern: `${severity}: ${pattern.source}`,
    matched: pattern.test(output),
  }));
}

/**
 * 詳細ログをJSONLファイルに記録
 *
 * Why: Plan AIレビューの検出ロジックをデバッグするため、レビュー全文とパターンマッチング結果を記録
 * What: timestamp, session_id, plan_file, gemini_output, codex_output, pattern_matchesを記録
 * How: .claude/logs/plan-ai-review-details.jsonlにJSONL形式で追記
 *
 * Issue #3843: Plan AIレビューの実効性検証
 */
export function appendDetailLog(
  projectDir: string,
  planFile: string,
  reviewResult: PlanReviewResult,
): void {
  try {
    const logPath = resolve(projectDir, DETAIL_LOG_FILE);
    const logDir = dirname(logPath);

    // ディレクトリが存在しなければ作成
    if (!existsSync(logDir)) {
      mkdirSync(logDir, { recursive: true });
    }

    const geminiOutput = getReviewOutput(reviewResult.gemini);
    const codexOutput = getReviewOutput(reviewResult.codex);

    const entry: PlanReviewDetailLog = {
      timestamp: new Date().toISOString(),
      session_id: process.env.CLAUDE_SESSION_ID || null,
      plan_file: planFile,
      gemini_output: geminiOutput,
      codex_output: codexOutput,
      pattern_matches: {
        gemini: [
          ...getPatternMatches(geminiOutput, GEMINI_PRIORITY_BADGES),
          ...getPatternMatches(geminiOutput, GEMINI_SECURITY_BADGES),
        ],
        codex: getPatternMatches(codexOutput, CODEX_PRIORITY_BADGES),
      },
    };

    appendFileSync(logPath, `${JSON.stringify(entry)}\n`);
  } catch {
    // 詳細ログ記録の失敗はサイレントに無視（フック本体の動作を妨げない）
  }
}

/**
 * 指定ディレクトリから.mdファイルを取得
 */
function getMdFilesFromDir(dir: string): Array<{ path: string; mtime: number }> {
  if (!existsSync(dir)) {
    return [];
  }

  try {
    return readdirSync(dir, { withFileTypes: true })
      .filter((dirent) => dirent.isFile() && dirent.name.endsWith(".md"))
      .map((dirent) => {
        const fullPath = join(dir, dirent.name);
        try {
          return { path: fullPath, mtime: statSync(fullPath).mtime.getTime() };
        } catch {
          return null;
        }
      })
      .filter((item): item is { path: string; mtime: number } => item !== null);
  } catch {
    return [];
  }
}

/**
 * 現在のブランチ名からIssue番号を抽出
 * 例: feat/issue-3179-plan-ai-review -> 3179
 *
 * Uses strict mode: only matches explicit "issue-XXX" patterns.
 *
 * @param projectDir gitコマンドを実行するディレクトリ
 */
function getIssueNumberFromCurrentBranch(projectDir: string): string | null {
  try {
    const proc = Bun.spawnSync(["git", "rev-parse", "--abbrev-ref", "HEAD"], {
      stdout: "pipe",
      stderr: "pipe",
      cwd: projectDir,
    });
    if (proc.exitCode !== 0) return null;

    const branch = new TextDecoder().decode(proc.stdout).trim();
    return extractIssueNumberFromBranch(branch, { strict: true });
  } catch {
    return null;
  }
}

/**
 * プランファイルのIssue番号との関連度スコアを計算
 *
 * スコア:
 * - 2: 強い関連（ファイル名にIssue番号、またはクローズキーワード）
 * - 1: 弱い関連（単純な #N 参照のみ）
 * - 0: 関連なし
 *
 * Why: 「Blocked by #3179」のような単純参照よりも「Closes #3179」を優先（Issue #3232）
 */
export function getPlanRelevanceScore(planPath: string, issueNumber: string): number {
  // 入力検証: issueNumberは数字のみ許可（正規表現インジェクション防止）
  if (!/^\d+$/.test(issueNumber)) {
    return 0;
  }

  const filename = basename(planPath);

  // ファイル名にIssue番号が含まれている場合は最高スコア
  // 否定後読み(?<!\w)で「myissue-3179」のような誤マッチを防止
  // ハイフンは文字クラスの先頭に配置して範囲指定と誤認識を防止
  const filenamePattern = new RegExp(`(?<!\\w)(issue[-_]?|#)${issueNumber}\\b`, "i");
  if (filenamePattern.test(filename)) {
    return 2;
  }

  try {
    const content = readFileSync(planPath, "utf-8");

    // クローズキーワードパターン（スコア2）
    // 例: "Closes issue-3179", "Closes issue_3179", "Closes issue 3179",
    //     "Closes issue#3179", "Closes #3179", "Fixes issue #3179" にマッチ
    // "resolves 2 problems" は誤マッチしない（issue または # の後に番号が必須）
    // ハイフンは文字クラスの先頭に配置して範囲指定と誤認識を防止
    const closePattern = new RegExp(
      `\\b(closes?|closed|fix(es|ed)?|resolves?|resolved):?\\s*(issue[-_ ]?#?|#)${issueNumber}\\b`,
      "i",
    );
    if (closePattern.test(content)) {
      return 2;
    }

    // 単純参照パターン（スコア1）
    // 否定後読み(?<!\w)で「bug3179」のような誤マッチを防止
    // ハイフンは文字クラスの先頭に配置して範囲指定と誤認識を防止
    const simplePattern = new RegExp(`(?<!\\w)(#|issue[-_ ]?)${issueNumber}\\b`, "i");
    if (simplePattern.test(content)) {
      return 1;
    }

    return 0;
  } catch (e) {
    console.error(`[plan-ai-review] Failed to read plan file '${planPath}':`, e);
    return 0;
  }
}

/**
 * 後方互換性のためのラッパー（既存のテスト用）
 * @deprecated getPlanRelevanceScore を使用してください
 */
export function isPlanRelatedToIssue(planPath: string, issueNumber: string): boolean {
  return getPlanRelevanceScore(planPath, issueNumber) > 0;
}

/**
 * .claude/plans/および~/.claude/plans/配下の最新ファイルを取得
 *
 * 優先順位:
 * 1. 現在のIssueに関連するファイル（スコア順、同スコアなら最新）
 * 2. 全体で最も新しいファイル
 *
 * @param projectDir プロジェクトディレクトリ
 * @param _issueNumber テスト用にIssue番号を直接指定（省略時はgitブランチから抽出）
 */
export function getLatestPlanFile(projectDir: string, _issueNumber?: string | null): string | null {
  const projectPlansDir = resolve(projectDir, PLANS_DIR);
  const userPlansDir = join(homedir(), ".claude", "plans");

  const allFiles = [...getMdFilesFromDir(projectPlansDir), ...getMdFilesFromDir(userPlansDir)];
  const sortedFiles = allFiles.sort((a, b) => b.mtime - a.mtime);

  const issueNumber = _issueNumber ?? getIssueNumberFromCurrentBranch(projectDir);

  if (issueNumber) {
    let score1Candidate: string | null = null;

    for (const file of sortedFiles) {
      const score = getPlanRelevanceScore(file.path, issueNumber);

      if (score === 2) {
        return file.path;
      }

      if (score === 1 && !score1Candidate) {
        score1Candidate = file.path;
      }
    }

    if (score1Candidate) {
      return score1Candidate;
    }
  }

  return sortedFiles[0]?.path ?? null;
}

/**
 * Gemini CLIが利用可能か確認
 *
 * テストからも呼び出せるようexport（Issue #3207）
 */
export async function isGeminiAvailable(): Promise<boolean> {
  try {
    const proc = Bun.spawn(["which", "gemini"], {
      stdout: "pipe",
      stderr: "pipe",
    });
    const exitCode = await proc.exited;
    return exitCode === 0;
  } catch {
    return false;
  }
}

/**
 * Codex CLIが利用可能か確認
 *
 * Issue #3392: Plan AIレビュー強化（Codex並列実行）
 */
export async function isCodexAvailable(): Promise<boolean> {
  try {
    const proc = Bun.spawn(["which", "codex"], {
      stdout: "pipe",
      stderr: "pipe",
    });
    const exitCode = await proc.exited;
    return exitCode === 0;
  } catch {
    return false;
  }
}

/**
 * Gemini CLIでバックグラウンドレビューを実行
 *
 * 利用可能チェックを統合し、CLI未インストール時は { available: false } を返す。
 *
 * 全てをstdinで渡すことで、argv長制限（E2BIG）を回避し、
 * プロンプトの順序（システム → レビュー → planContent）を正しく維持する。
 * 参照: Issue #3202
 *
 * テストからも呼び出せるようexport（Issue #3207）
 *
 * Issue #3484: --approval-mode defaultを明示し、コード自動改変を防止
 * Issue #3859: CLIReviewResult型導入（利用可能チェック統合）
 */
export async function runGeminiReview(planContent: string): Promise<CLIReviewResult> {
  if (!(await isGeminiAvailable())) {
    return { available: false };
  }
  const systemPrompt = "あなたは実装計画レビューアーです。簡潔に日本語でレビューしてください。";
  const prompt = `${systemPrompt}\n\n${PLAN_REVIEW_PROMPT}${planContent}`;
  return runCLIReview(["gemini", "--approval-mode", "default"], prompt);
}

/**
 * Codex CLIでPlanレビューを実行
 *
 * 利用可能チェックを統合し、CLI未インストール時は { available: false } を返す。
 *
 * Issue #3392: Plan AIレビュー強化（Codex並列実行）
 * Issue #3453: -qオプションは存在しないため、codex execサブコマンドを使用
 * Issue #3859: CLIReviewResult型導入（利用可能チェック統合）
 */
export async function runCodexReview(planContent: string): Promise<CLIReviewResult> {
  if (!(await isCodexAvailable())) {
    return { available: false };
  }
  const prompt = `${PLAN_REVIEW_PROMPT}${planContent}`;
  return runCLIReview(["codex", "exec"], prompt);
}

/**
 * Gemini + Codexを並列実行
 *
 * Issue #3392: Plan AIレビュー強化（Codex並列実行）
 * Issue #3859: CLIReviewResult型導入（利用可能チェックはrunGemini/CodexReviewに統合済み）
 *
 * @param planContent レビュー対象のPlanコンテンツ
 */
export async function runParallelPlanReview(planContent: string): Promise<PlanReviewResult> {
  const [gemini, codex] = await Promise.all([
    runGeminiReview(planContent),
    runCodexReview(planContent),
  ]);

  return { gemini, codex };
}

/**
 * レビュー出力からスニペットを抽出
 *
 * @param output レビュー出力
 * @param matchIndex マッチ位置（matchAll結果のindex）
 * @param matchLength マッチした文字列の長さ
 */
function extractSnippetAt(output: string, matchIndex: number, matchLength: number): string {
  const start = Math.max(0, matchIndex - 50);
  const end = Math.min(output.length, matchIndex + matchLength + 100);
  return output.slice(start, end).trim();
}

/** バッジパターンとソースの組み合わせ */
type BadgeConfig = {
  patterns: Record<string, RegExp>;
  source: "gemini" | "codex";
};

/**
 * レビュー結果からブロック対象の指摘を検出
 *
 * Issue #3392: Plan AIレビュー強化（ブロック機能）
 */
export function detectPlanBlockingFindings(
  geminiOutput: string | null,
  codexOutput: string | null,
): PlanFinding[] {
  const findings: PlanFinding[] = [];

  const configs: Array<{ output: string | null; badges: BadgeConfig[] }> = [
    {
      output: geminiOutput,
      badges: [
        { patterns: GEMINI_PRIORITY_BADGES, source: "gemini" },
        { patterns: GEMINI_SECURITY_BADGES, source: "gemini" },
      ],
    },
    {
      output: codexOutput,
      badges: [{ patterns: CODEX_PRIORITY_BADGES, source: "codex" }],
    },
  ];

  for (const { output, badges } of configs) {
    if (!output) continue;

    for (const { patterns, source } of badges) {
      for (const [severity, pattern] of Object.entries(patterns)) {
        if (!BLOCKING_SEVERITIES.has(severity)) continue;

        // matchAllで全てのマッチを検出（Issue #3392 レビュー指摘対応）
        const globalPattern = new RegExp(
          pattern.source,
          pattern.flags.includes("g") ? pattern.flags : `${pattern.flags}g`,
        );
        for (const match of output.matchAll(globalPattern)) {
          findings.push({
            severity,
            source,
            snippet: extractSnippetAt(output, match.index ?? 0, match[0].length),
          });
        }
      }
    }
  }

  return findings;
}

/**
 * ブロック時のメッセージをフォーマット
 */
function formatBlockMessage(reviewResult: PlanReviewResult, findings: PlanFinding[]): string {
  const sections: string[] = [];
  const geminiOutput = getReviewOutput(reviewResult.gemini);
  const codexOutput = getReviewOutput(reviewResult.codex);

  // 指摘サマリー（80文字超過時のみ切り詰め）
  const findingsSummary = findings
    .map((f) => {
      const truncated = f.snippet.length > 80 ? `${f.snippet.slice(0, 80)}...` : f.snippet;
      return `- [${f.severity}] (${f.source}): ${truncated}`;
    })
    .join("\n");

  sections.push(`📋 Plan AIレビューで重大な指摘が検出されました

**検出された指摘 (${findings.length}件):**
${findingsSummary}`);

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
*P0/P1/HIGH/MEDIUM以上の指摘が検出されたため、Planの見直しが必要です。*
*指摘を修正してから再度ExitPlanModeを実行してください。*`);

  return sections.join("\n\n");
}

/**
 * メトリクス付きでフックを終了するヘルパー
 */
function approveWithMetrics(
  projectDir: string,
  metrics: Omit<PlanReviewMetrics, "timestamp" | "session_id">,
): never {
  appendMetricsLog(projectDir, metrics);
  approveAndExit(HOOK_NAME);
}

/**
 * 共通のskippedメトリクスを生成するヘルパー
 */
function skippedMetrics(
  issueNumber: string | null,
  planFile: string | null,
): Omit<PlanReviewMetrics, "timestamp" | "session_id"> {
  return {
    issue_number: issueNumber,
    plan_file: planFile,
    result: "skipped",
    review_length: null,
  };
}

/**
 * メイン処理
 *
 * Issue #3392: Codex並列実行 + ブロック機能を追加
 */
async function main(): Promise<void> {
  const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();

  // エラー時にもメトリクス記録できるよう、tryブロック外で宣言
  let issueNumber: string | null = null;
  let planFile: string | null = null;

  try {
    const input = await parseHookInput();

    // ExitPlanMode以外は無視（メトリクス記録なし）
    if (input.tool_name !== "ExitPlanMode") {
      approveAndExit(HOOK_NAME);
    }

    // イテレーティブモードが有効な場合はスキップ（PreToolUse版に任せる）
    // Issue #3853: 移行期間中の重複実行防止
    if (process.env.PLAN_REVIEW_ITERATIVE === "1") {
      approveAndExit(HOOK_NAME);
    }

    issueNumber = getIssueNumberFromCurrentBranch(projectDir);

    // ExitPlanMode失敗時はスキップ（staleなplanのレビューを防止）
    if (isToolResultError(getToolResult(input))) {
      approveWithMetrics(projectDir, skippedMetrics(issueNumber, null));
    }

    // 最新のplanファイルを取得（issueNumberを渡して重複したgit呼び出しを回避）
    planFile = getLatestPlanFile(projectDir, issueNumber);
    if (!planFile) {
      approveWithMetrics(projectDir, skippedMetrics(issueNumber, null));
    }

    // planファイルの内容を読み込み
    const planContent = readFileSync(planFile, "utf-8");
    if (!planContent.trim()) {
      approveWithMetrics(projectDir, skippedMetrics(issueNumber, planFile));
    }

    // 並列レビュー実行（Gemini + Codex、利用可能チェックは各関数内で実行）
    const reviewResult = await runParallelPlanReview(planContent);

    const geminiAvail = reviewResult.gemini.available;
    const codexAvail = reviewResult.codex.available;
    const geminiOutput = getReviewOutput(reviewResult.gemini);
    const codexOutput = getReviewOutput(reviewResult.codex);

    // 両方利用不可ならスキップ
    if (!geminiAvail && !codexAvail) {
      approveWithMetrics(projectDir, {
        ...skippedMetrics(issueNumber, planFile),
        gemini_available: false,
        codex_available: false,
      });
    }

    // 両方出力なし（実行失敗 or 片方利用不可+片方失敗）の場合はエラー
    // 「両方利用不可」は上でスキップ済みなので、ここでは出力有無のみ確認
    if (!geminiOutput && !codexOutput) {
      approveWithMetrics(projectDir, {
        issue_number: issueNumber,
        plan_file: planFile,
        result: "error",
        review_length: null,
        gemini_available: geminiAvail,
        codex_available: codexAvail,
      });
    }

    // 詳細ログを記録（Issue #3843: レビュー全文とパターンマッチング結果）
    appendDetailLog(projectDir, planFile, reviewResult);

    // ブロック判定
    const blockingFindings = detectPlanBlockingFindings(geminiOutput, codexOutput);

    const totalReviewLength = (geminiOutput?.length ?? 0) + (codexOutput?.length ?? 0);

    // 出力プレビューを生成（Issue #3843）
    const geminiPreview = getOutputPreview(geminiOutput);
    const codexPreview = getOutputPreview(codexOutput);

    if (blockingFindings.length > 0) {
      // ブロック
      appendMetricsLog(projectDir, {
        issue_number: issueNumber,
        plan_file: planFile,
        result: "blocked",
        review_length: totalReviewLength,
        gemini_available: geminiAvail,
        codex_available: codexAvail,
        blocking_findings_count: blockingFindings.length,
        gemini_output_preview: geminiPreview,
        codex_output_preview: codexPreview,
      });

      blockAndExit(HOOK_NAME, formatBlockMessage(reviewResult, blockingFindings));
    }

    // メトリクス記録（成功）
    appendMetricsLog(projectDir, {
      issue_number: issueNumber,
      plan_file: planFile,
      result: "reviewed",
      review_length: totalReviewLength,
      gemini_available: geminiAvail,
      codex_available: codexAvail,
      blocking_findings_count: 0,
      gemini_output_preview: geminiPreview,
      codex_output_preview: codexPreview,
    });

    // レビュー結果をsystemMessageで通知
    const reviewSections: string[] = [];
    if (geminiOutput) {
      reviewSections.push(`**Gemini Review:**\n${geminiOutput}`);
    }
    if (codexOutput) {
      reviewSections.push(`**Codex Review:**\n${codexOutput}`);
    }

    const systemMessage = `📋 Plan AIレビュー完了

${reviewSections.filter(Boolean).join("\n\n")}

---
*Plan段階でのAIレビューにより、実装前に設計の問題点を検出できます。*`;

    // ユーザーにも表示（stderrはユーザーに直接表示される）
    console.error(`\n${systemMessage}\n`);

    const result: HookResult = {
      systemMessage,
    };

    console.log(JSON.stringify(result));
    process.exit(0);
  } catch (error) {
    // エラー時もメトリクス記録（取得済みの情報を含める）
    appendMetricsLog(projectDir, {
      issue_number: issueNumber,
      plan_file: planFile,
      result: "error",
      review_length: null,
    });
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    approveAndExit(HOOK_NAME);
  }
}

// 実行（テスト時はスキップ）
if (import.meta.main) {
  main();
}
