/**
 * フック結果（block/approve）の生成ユーティリティ
 *
 * Why:
 *   フック結果の形式を統一し、ブロック時のメッセージ表示を一元化
 *
 * What:
 *   - makeBlockResult(): ブロック結果生成
 *   - makeApproveResult(): 承認結果生成
 *
 * Remarks:
 *   - Python lib/results.py との互換性を維持
 *   - ログ記録は別モジュール（execution.ts）で行う
 *
 * Changelog:
 *   - silenvx/dekita#2814: 初期実装
 */

import { CONTINUATION_HINT } from "./constants";
import type { HookContext, HookResult } from "./types";

/**
 * Re-export HookResult and HookContext from types.ts
 *
 * Some existing hooks import HookResult from ../lib/results instead of ../lib/types.
 * This re-export maintains backward compatibility.
 */
export type { HookContext, HookResult } from "./types";

/**
 * ブロック結果を作成
 *
 * Issue #725: ブロック後のテキストのみ応答による処理停止を防止
 * Issue #938: ブロック時にstderrに詳細情報を出力
 * Issue #1279: systemMessageフィールドでユーザーにメッセージを表示
 *
 * @param hookName フック名（例: "merge-check", "ci-wait-check"）
 * @param reason ブロック理由
 * @param _ctx HookContext（将来のログ記録用）
 * @returns ブロック結果
 */
export function makeBlockResult(
  hookName: string,
  reason: string,
  _ctx?: HookContext | null,
): HookResult {
  // Format full reason with hook prefix and continuation hint
  const fullReason = `[${hookName}] ${reason}${CONTINUATION_HINT}`;

  // Extract first line for systemMessage
  const firstLine = reason.split("\n")[0];
  const systemMessage = `[${hookName}] ${firstLine}`;

  // Output to stderr for visibility (Issue #938)
  console.error(systemMessage);

  return {
    decision: "block",
    reason: fullReason,
    systemMessage,
  };
}

/**
 * 承認結果を作成
 *
 * @param _hookName フック名（ログ用）
 * @param _message オプションのメッセージ
 * @returns 承認結果
 */
export function makeApproveResult(_hookName?: string, _message?: string): HookResult {
  return {};
}

/**
 * 結果をJSON形式でstdoutに出力して終了
 *
 * @param result フック結果
 * @param exitCode 終了コード（approve: 0, block: 2）
 */
export function outputResult(result: HookResult, exitCode?: number): never {
  console.log(JSON.stringify(result));
  const code = exitCode ?? (result.decision === "block" ? 2 : 0);
  process.exit(code);
}

/**
 * 承認して終了（ヘルパー関数）
 *
 * @param hookName フック名
 */
export function approveAndExit(hookName?: string): never {
  outputResult(makeApproveResult(hookName));
}

/**
 * ブロックして終了（ヘルパー関数）
 *
 * @param hookName フック名
 * @param reason ブロック理由
 * @param ctx HookContext
 */
export function blockAndExit(hookName: string, reason: string, ctx?: HookContext | null): never {
  outputResult(makeBlockResult(hookName, reason, ctx));
}
