/**
 * イテレーティブPlan AIレビューの状態管理
 *
 * Why:
 *   複数回のExitPlanMode呼び出しにわたってレビュー状態を追跡するため
 *
 * What:
 *   - PlanReviewState: レビュー状態の型定義
 *   - loadPlanReviewState(): 状態ファイルの読み込み
 *   - savePlanReviewState(): 状態ファイルの保存
 *   - clearPlanReviewState(): 状態ファイルの削除
 *   - getStateFilePath(): 状態ファイルパスの取得
 *
 * Remarks:
 *   - 状態ファイルは .claude/state/plan-review-{session_id}.json に保存
 *   - monitor_state.ts のパターンを踏襲
 *   - アトミック書き込み（temp file + rename）で部分読み込みを防止
 *
 * Changelog:
 *   - silenvx/dekita#3853: 初期実装（イテレーティブPlan AIレビュー）
 */

import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { formatError } from "./format_error";
import type { ApprovalCheckResult } from "./plan_review_patterns";

// =============================================================================
// Configuration
// =============================================================================

/** 状態ファイルのディレクトリ（プロジェクトルートからの相対パス） */
const STATE_FILE_DIR = ".claude/state";

/** 状態ファイル名のプレフィックス */
const STATE_FILE_PREFIX = "plan-review-";

// =============================================================================
// Types
// =============================================================================

/**
 * 単一イテレーションのレビュー結果
 */
export interface PlanReviewIteration {
  /** イテレーション番号（1始まり） */
  iteration: number;
  /** タイムスタンプ（ISO形式） */
  timestamp: string;
  /** Geminiの承認判定結果（null = 利用不可） */
  gemini: ApprovalCheckResult | null;
  /** Codexの承認判定結果（null = 利用不可） */
  codex: ApprovalCheckResult | null;
  /** Geminiの生出力（null = 利用不可） */
  geminiOutput: string | null;
  /** Codexの生出力（null = 利用不可） */
  codexOutput: string | null;
  /** Plan内容のハッシュ（変更検知用） */
  planHash: string;
  /** このイテレーションの結果 */
  result: "blocked" | "approved";
}

/**
 * Plan AIレビューの状態
 */
export interface PlanReviewState {
  /** セッションID */
  sessionId: string;
  /** レビュー対象のPlanファイルパス */
  planFile: string;
  /** 現在のイテレーション回数 */
  iterationCount: number;
  /** レビュー開始時刻（ISO形式） */
  startedAt: string;
  /** 最終更新時刻（ISO形式） */
  updatedAt: string;
  /** 各イテレーションの履歴 */
  reviews: PlanReviewIteration[];
}

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * 文字列のシンプルなハッシュを計算
 *
 * @param str ハッシュ対象の文字列
 * @returns ハッシュ値（16進数文字列）
 */
export function simpleHash(str: string): string {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash = hash & hash; // 32ビット整数に変換
  }
  // 符号なし32ビット整数として扱い、衝突リスクを低減
  return (hash >>> 0).toString(16).padStart(8, "0");
}

/**
 * セッションIDの検証
 *
 * @param sessionId セッションID
 * @returns 有効なセッションIDかどうか
 */
function isValidSessionId(sessionId: string): boolean {
  if (!sessionId || !sessionId.trim()) {
    return false;
  }
  // UUID形式またはアルファベット・数字・ハイフン・アンダースコア・ドットのみ
  // ドットはClaude CLIのセッションIDに含まれる可能性がある
  // ただし、..（ディレクトリトラバーサル）は禁止
  const safePattern = /^[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)*$/;
  return safePattern.test(sessionId) && !sessionId.includes("..") && sessionId.length <= 100;
}

// =============================================================================
// State Management Functions
// =============================================================================

/**
 * 状態ファイルのパスを取得
 *
 * @param projectDir プロジェクトディレクトリ
 * @param sessionId セッションID
 * @returns 状態ファイルのパス
 * @throws Error セッションIDが無効な場合
 */
export function getStateFilePath(projectDir: string, sessionId: string): string {
  if (!isValidSessionId(sessionId)) {
    throw new Error(`Invalid session_id specified: ${sessionId}`);
  }
  return join(projectDir, STATE_FILE_DIR, `${STATE_FILE_PREFIX}${sessionId}.json`);
}

/**
 * 状態ファイルを読み込む
 *
 * @param projectDir プロジェクトディレクトリ
 * @param sessionId セッションID
 * @returns 状態オブジェクト、または存在しない/無効な場合はnull
 */
export function loadPlanReviewState(projectDir: string, sessionId: string): PlanReviewState | null {
  try {
    const stateFile = getStateFilePath(projectDir, sessionId);

    if (!existsSync(stateFile)) {
      return null;
    }

    const content = readFileSync(stateFile, "utf-8");
    return JSON.parse(content) as PlanReviewState;
  } catch (error) {
    if (error instanceof Error && error.message.includes("Invalid session_id")) {
      console.error(`Warning: Invalid session ID: ${error.message}`);
    } else {
      console.error(`Warning: Failed to load state: ${formatError(error)}`);
    }
    return null;
  }
}

/**
 * 状態ファイルを保存
 *
 * アトミック書き込み（temp file + rename）で部分読み込みを防止
 *
 * @param projectDir プロジェクトディレクトリ
 * @param state 保存する状態オブジェクト
 * @returns 保存成功時はtrue、失敗時はfalse
 */
export function savePlanReviewState(projectDir: string, state: PlanReviewState): boolean {
  let tempFile: string | null = null;

  try {
    const stateFile = getStateFilePath(projectDir, state.sessionId);
    const stateDir = dirname(stateFile);

    // ディレクトリが存在しなければ作成
    if (!existsSync(stateDir)) {
      mkdirSync(stateDir, { recursive: true });
    }

    // 更新時刻を設定
    const stateToSave: PlanReviewState = {
      ...state,
      updatedAt: new Date().toISOString(),
    };

    // アトミック書き込み
    tempFile = `${stateFile}.tmp`;
    writeFileSync(tempFile, JSON.stringify(stateToSave, null, 2), "utf-8");
    renameSync(tempFile, stateFile);

    return true;
  } catch (error) {
    if (error instanceof Error && error.message.includes("Invalid session_id")) {
      console.error(`Warning: Invalid session ID: ${error.message}`);
    } else {
      console.error(`Warning: Failed to save state: ${formatError(error)}`);
    }

    // エラー時のtempファイルクリーンアップ
    if (tempFile) {
      try {
        unlinkSync(tempFile);
      } catch {
        // ベストエフォート
      }
    }

    return false;
  }
}

/**
 * 状態ファイルを削除
 *
 * レビュー完了時のクリーンアップ用
 *
 * @param projectDir プロジェクトディレクトリ
 * @param sessionId セッションID
 * @returns 削除成功または存在しない場合はtrue、エラー時はfalse
 */
export function clearPlanReviewState(projectDir: string, sessionId: string): boolean {
  try {
    const stateFile = getStateFilePath(projectDir, sessionId);

    if (existsSync(stateFile)) {
      unlinkSync(stateFile);
    }

    return true;
  } catch (error) {
    if (error instanceof Error && error.message.includes("Invalid session_id")) {
      console.error(`Warning: Invalid session ID: ${error.message}`);
    } else {
      console.error(`Warning: Failed to clear state: ${formatError(error)}`);
    }
    return false;
  }
}

/**
 * 新しい状態オブジェクトを作成
 *
 * @param sessionId セッションID
 * @param planFile Planファイルパス
 * @returns 初期状態オブジェクト
 */
export function createInitialState(sessionId: string, planFile: string): PlanReviewState {
  const now = new Date().toISOString();
  return {
    sessionId,
    planFile,
    iterationCount: 0,
    startedAt: now,
    updatedAt: now,
    reviews: [],
  };
}

/**
 * イテレーション結果を状態に追加
 *
 * @param state 現在の状態
 * @param iteration 追加するイテレーション結果
 * @returns 更新された状態（新しいオブジェクト）
 */
export function addIterationToState(
  state: PlanReviewState,
  iteration: PlanReviewIteration,
): PlanReviewState {
  return {
    ...state,
    iterationCount: state.iterationCount + 1,
    reviews: [...state.reviews, iteration],
  };
}

/**
 * イテレーションカウンタをリセット
 *
 * プランが大幅に変更された場合に使用。
 * 履歴（reviews配列）は保持したまま、カウンタのみリセットする。
 *
 * Why:
 *   イテレーション上限に達した後でも、プランを修正すれば
 *   新しいレビューサイクルを開始できるようにするため。
 *
 * @param state 現在の状態
 * @returns カウンタがリセットされた状態（新しいオブジェクト）
 */
export function resetIterationCount(state: PlanReviewState): PlanReviewState {
  return {
    ...state,
    iterationCount: 0,
  };
}
