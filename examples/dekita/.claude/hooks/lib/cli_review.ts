/**
 * CLI review utilities for Plan AI review hooks.
 *
 * Why:
 *   runCLIReview関数がplan_ai_review.tsとplan_ai_review_iterative.tsで
 *   完全に重複していたため、共通ライブラリに抽出。
 *
 * What:
 *   - CLIコマンドでレビューを実行する共通ヘルパー
 *   - タイムアウト付きでプロセスを管理
 *   - stdin経由でプロンプトを渡す
 *
 * Changelog:
 *   - silenvx/dekita#3859: CLIReviewResult型導入（実行失敗と利用不可を区別）
 *   - silenvx/dekita#3861: Promise.all並列化、エラーログ出力追加
 *   - silenvx/dekita#3854: 初期実装（重複コードの抽出）
 */

import { TIMEOUT_LONG } from "./constants";

/** CLI実行のタイムアウト（プロジェクト標準: 180秒） */
export const CLI_TIMEOUT_MS = TIMEOUT_LONG * 1000;

/**
 * CLIレビュー結果の3状態型
 *
 * - { available: true, output: string } - CLI実行成功
 * - { available: true, output: null } - CLIは存在するが実行失敗（非ゼロ終了/タイムアウト/空出力/例外）
 * - { available: false } - CLIが存在しない
 */
export type CLIReviewResult =
  | { available: true; output: string }
  | { available: true; output: null }
  | { available: false };

/**
 * CLIツールでレビューを実行する共通ヘルパー
 *
 * stdin経由でプロンプトを渡し、タイムアウト付きで結果を取得する。
 *
 * タイムアウト処理:
 * - try-finallyパターンでタイマーのクリーンアップを確実に実行
 * - タイムアウト発生フラグでプロセスキルを明確に制御
 * - Promise.raceでCLIスタック対策を実装
 *
 * @param command - 実行するコマンド（例: ["gemini", "--approval-mode", "default"]）
 * @param prompt - stdinで渡すプロンプト
 * @returns CLIReviewResult（成功/実行失敗の2状態。利用不可判定は呼び出し側で実施）
 */
export async function runCLIReview(command: string[], prompt: string): Promise<CLIReviewResult> {
  let timer: Timer | undefined;
  let proc: ReturnType<typeof Bun.spawn> | undefined;
  let timedOut = false;

  try {
    proc = Bun.spawn(command, {
      stdin: new Blob([prompt]),
      stdout: "pipe",
      stderr: "pipe",
    });

    const timeoutPromise = new Promise<CLIReviewResult>((resolve) => {
      timer = setTimeout(() => {
        timedOut = true;
        resolve({ available: true, output: null });
      }, CLI_TIMEOUT_MS);
    });

    const resultPromise = (async (): Promise<CLIReviewResult> => {
      // stdout/stderr/exitedを並列で消費してパイプバッファ詰まりを防止
      // Type assertion: When stdout/stderr are set to "pipe", they are ReadableStream
      const [output, stderr, exitCode] = await Promise.all([
        new Response(proc!.stdout as ReadableStream<Uint8Array>).text(),
        new Response(proc!.stderr as ReadableStream<Uint8Array>).text(),
        proc!.exited,
      ]);
      if (exitCode !== 0) {
        console.error(
          `[runCLIReview] Command '${command.join(" ")}' failed with exit code ${exitCode}`,
        );
        if (stderr.trim()) {
          console.error(`[runCLIReview] stderr: ${stderr.trim()}`);
        }
        return { available: true, output: null };
      }
      const trimmed = output.trim();
      return trimmed ? { available: true, output: trimmed } : { available: true, output: null };
    })();

    const result = await Promise.race([resultPromise, timeoutPromise]);

    if (timedOut && proc) {
      proc.kill();
      await proc.exited;
    }

    return result;
  } catch (error) {
    console.error(`[runCLIReview] Error running ${command.join(" ")}:`, error);
    if (proc) {
      proc.kill();
      await proc.exited;
    }
    return { available: true, output: null };
  } finally {
    if (timer) {
      clearTimeout(timer);
    }
  }
}
