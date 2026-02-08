/**
 * フック実行時間の計測ユーティリティ
 *
 * Why:
 *   フックのパフォーマンス分析のため、実行時間を自動計測し、
 *   ログに記録する仕組みを提供する。
 *
 * What:
 *   - HookTimer: 手動タイミング計測用クラス
 *
 * Remarks:
 *   - performance.now()使用で高精度計測
 *   - log_hook_execution連携は lib/execution.ts 実装後に追加予定
 *
 * Changelog:
 *   - silenvx/dekita#2866: Python版から移行
 */

/**
 * Simple timer for measuring hook execution time.
 */
export class HookTimer {
  private readonly hookName: string;
  private readonly startTime: number;

  /**
   * Initialize timer with hook name.
   *
   * @param hookName - Name of the hook for logging purposes.
   */
  constructor(hookName: string) {
    this.hookName = hookName;
    this.startTime = performance.now();
  }

  /**
   * Get the hook name.
   */
  getHookName(): string {
    return this.hookName;
  }

  /**
   * Get elapsed time in milliseconds since timer start.
   *
   * @returns Elapsed time in milliseconds (integer).
   */
  elapsedMs(): number {
    const elapsed = performance.now() - this.startTime;
    return Math.floor(elapsed);
  }

  /**
   * Get elapsed time in seconds since timer start.
   *
   * @returns Elapsed time in seconds (float).
   */
  elapsedSeconds(): number {
    return (performance.now() - this.startTime) / 1000;
  }
}
