#!/usr/bin/env bun
/**
 * ユーザーフィードバック（問題指摘・懸念）を検出し、セッション状態に記録する。
 *
 * Why:
 *   ユーザーが動作確認や問題を指摘した場合、類似問題を将来検出できるよう
 *   振り返り観点の追加を促す。また、セッション状態に記録することで、
 *   セッション終了時に仕組み化の確認を可能にする。
 *
 * What:
 *   - ユーザー入力から否定的フィードバックパターンを検出
 *   - 「動いてる？」「おかしい」「バグ」等のパターンをマッチ
 *   - 検出時はACTION_REQUIREDを出力し、/adding-perspectives実行を促す
 *   - セッション状態ファイルに `user_feedback_detected: true` を記録
 *
 * Remarks:
 *   - type: "command"を使用（type: "prompt"はクラッシュ問題があるため）
 *   - 1文字入力は誤検知防止のため除外
 *   - セッション状態はStop hookで仕組み化確認に使用される
 *   - Python版: feedback_detector.py
 *
 * Changelog:
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";
import { FLOW_LOG_DIR } from "../lib/common";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

// Negative feedback patterns (問題指摘パターン)
export const NEGATIVE_PATTERNS = [
  // 動作確認・疑問形
  /動いてる[？?]?/,
  /正常[？?]?/,
  /大丈夫[？?]?/,
  /問題ない[？?]?/,
  // 問題指摘
  /おかしい/,
  /おかしく/,
  /バグ/,
  /壊れ/,
  /動かない/,
  /動作しない/,
  /エラー/,
  /失敗/,
  /期待通りじゃない/,
  /意図した動作ではない/,
  /想定と違う/,
  // 確認要求
  /確認した[？?]/,
  /テストした[？?]/,
  /検証した[？?]/,
  /チェックした[？?]/,
  // 改善提案・不足指摘
  /(?:した|する)(?:ほう|方)が(?:いい|良い|よい)/,
  /(?:検証|テスト|確認|説明|配慮|考慮|機能|実装|作り|見通し)が?(?:不十分|不足)/,
  /(?:検証|テスト|確認|説明|配慮|考慮|機能|実装|作り|見通し)が?(?:甘い|弱い)/,
  /(?:検証|テスト|確認|説明|配慮|考慮|機能|実装|作り|見通し)が?(?:足り|足ら)(?:ない|て(?:い)?ない|ん)/,
  /(?:でき|出来)て(?:い)?ない(?:気|き)がする/,
  /あまり(?:でき|出来)て(?:い)?ない/,
];

// Patterns to exclude (false positive prevention)
export const EXCLUDE_PATTERNS = [
  /^(PRを|機能を|ファイルを|コードを)/,
  /(追加して|作成して|修正して|削除して)$/,
  /(読んで|確認して|見て)$/,
  /^こんにちは/,
  /^ありがとう/,
  // 疑問文（アドバイス要求）の誤検知防止
  /(?:どの|どちら|どっち)[^。？\n]*(?:ほう|方)?が(?:いい|良い|よい)/,
  // Yes/No疑問文（アドバイス要求）の誤検知防止
  /(?:した|する)(?:ほう|方)が(?:いい|良い|よい)(?:ですか|でしょうか|かな|[？?])/,
];

/**
 * Check if the text contains negative feedback patterns.
 */
export function isFeedback(text: string | undefined): boolean {
  if (!text || text.length < 2) {
    return false;
  }

  // Check exclusion patterns first
  for (const pattern of EXCLUDE_PATTERNS) {
    if (pattern.test(text)) {
      return false;
    }
  }

  // Check negative patterns
  for (const pattern of NEGATIVE_PATTERNS) {
    if (pattern.test(text)) {
      return true;
    }
  }

  return false;
}

/**
 * Get state file path for a specific session.
 */
function getStateFile(sessionId: string): string {
  const safeSessionId = basename(sessionId);
  return join(FLOW_LOG_DIR, `state-${safeSessionId}.json`);
}

interface FlowState {
  session_id: string;
  active_workflow: string | null;
  workflows: Record<string, unknown>;
  global: {
    hooks_fired_total: number;
    session_start_time: string;
  };
  user_feedback_detected?: boolean;
  [key: string]: unknown;
}

/**
 * Load current state from session-specific state file.
 */
function loadState(sessionId: string): FlowState {
  const stateFile = getStateFile(sessionId);
  try {
    if (existsSync(stateFile)) {
      return JSON.parse(readFileSync(stateFile, "utf-8"));
    }
  } catch {
    // Best effort - corrupted state file is ignored
  }

  // Initial state for new session
  return {
    session_id: sessionId,
    active_workflow: null,
    workflows: {},
    global: {
      hooks_fired_total: 0,
      session_start_time: new Date().toISOString(),
    },
  };
}

/**
 * Save state to session-specific state file.
 */
function saveState(sessionId: string, state: FlowState): void {
  try {
    mkdirSync(FLOW_LOG_DIR, { recursive: true });
    const stateFile = getStateFile(sessionId);
    writeFileSync(stateFile, JSON.stringify(state, null, 2));
  } catch {
    // Best effort - state save may fail
  }
}

/**
 * Record user feedback detection in session state.
 */
function recordUserFeedback(sessionId: string): void {
  if (!sessionId) return;

  const state = loadState(sessionId);
  state.user_feedback_detected = true;
  saveState(sessionId, state);
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;
    const userPrompt = inputData.user_prompt ?? "";

    if (!userPrompt) {
      await logHookExecution("feedback-detector", "approve", "empty prompt", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    if (isFeedback(userPrompt)) {
      // Record feedback detection in session state for Stop hook verification
      if (sessionId) {
        recordUserFeedback(sessionId);
      }

      const message =
        "🔍 ユーザーフィードバック検出\n\n" +
        "ユーザーが動作確認、問題指摘、または改善提案をしています。\n\n" +
        "[IMMEDIATE: gh issue create]\n" +
        "ユーザー指摘を即座にIssue化してください。\n\n" +
        "[ACTION_REQUIRED: /adding-perspectives]\n\n" +
        "類似問題を将来検出できるよう、振り返り観点の追加を検討してください。";
      result.systemMessage = message;
      await logHookExecution("feedback-detector", "approve", "feedback detected", undefined, {
        sessionId,
      });
    } else {
      await logHookExecution("feedback-detector", "approve", "no feedback pattern", undefined, {
        sessionId,
      });
    }
  } catch (error) {
    // Log to stderr for debugging, but don't block user interaction
    console.error(`feedback-detector: ${formatError(error)}`);
    await logHookExecution("feedback-detector", "error", String(error), undefined, { sessionId });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
