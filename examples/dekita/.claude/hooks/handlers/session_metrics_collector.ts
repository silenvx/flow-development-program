#!/usr/bin/env bun
/**
 * セッションメトリクス収集フック（Stop）
 *
 * セッション終了時に自動でメトリクスを収集・記録する。
 *
 * Why:
 *   セッションの品質を継続的に改善するため、終了時にメトリクスを
 *   収集して分析可能にする必要がある。
 *
 * What:
 *   - collect_session_metrics.pyでメトリクス収集
 *   - session_report_generator.pyでレポート生成
 *   - ブロックせず、情報収集のみ
 *
 * Remarks:
 *   - SRP: セッション終了時のメトリクス収集のみを担当
 *   - 既存フックとの重複なし
 *   - ブロックなし（情報収集のみのため）
 *   - Python版: session_metrics_collector.py からの移行
 *
 * Changelog:
 *   - silenvx/dekita#1367: セッションレポート生成追加
 *   - silenvx/dekita#1636: エラーコンテキストフラッシュ追加
 *   - silenvx/dekita#2317: session_idをコマンドライン引数で渡すように変更
 *   - silenvx/dekita#3142: TypeScriptに移植
 */

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { TIMEOUT_HEAVY } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { getErrorContextManager, logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";
import { createHookContext } from "../lib/types";

const HOOK_NAME = "session-metrics-collector";

/** Scripts directory */
const SCRIPTS_DIR = join(import.meta.dir, "..", "..", "scripts");

/**
 * Collect session metrics.
 *
 * @param sessionId - Claude Code session ID
 * @returns true if collection succeeded
 */
export function collectSessionMetrics(sessionId: string): boolean {
  const collectScript = join(SCRIPTS_DIR, "collect_session_metrics.py");

  if (!existsSync(collectScript)) {
    return false;
  }

  try {
    // Issue #2317: Pass session_id via command line argument instead of env
    const result = spawnSync("python3", [collectScript, "--session-id", sessionId], {
      encoding: "utf-8",
      timeout: TIMEOUT_HEAVY * 1000, // Convert to ms
    });
    return result.status === 0;
  } catch {
    return false;
  }
}

/**
 * Generate session report.
 *
 * Issue #1367: Generate integrated report at session end
 *
 * @param sessionId - Claude Code session ID
 * @returns true if generation succeeded
 */
export function generateSessionReport(sessionId: string): boolean {
  const reportScript = join(SCRIPTS_DIR, "session_report_generator.py");

  if (!existsSync(reportScript)) {
    return false;
  }

  try {
    // Issue #2317: Pass session_id via command line argument instead of env
    const result = spawnSync("python3", [reportScript, "--session-id", sessionId], {
      encoding: "utf-8",
      timeout: TIMEOUT_HEAVY * 1000, // Convert to ms
    });
    return result.status === 0;
  } catch {
    return false;
  }
}

async function main(): Promise<void> {
  // Stop hook receives JSON input from stdin
  const hookInput = await parseHookInput();

  // If stop hook is already active, approve immediately
  if (hookInput.stop_hook_active) {
    console.log(JSON.stringify({}));
    return;
  }

  // Get session ID (prefer hook input, fallback to context)
  // Issue #1308: Hook input takes priority, fallback to ctx.sessionId
  // Note: Empty string is treated as invalid and triggers fallback (intentional)
  const ctx = createHookContext(hookInput);
  const sessionId = hookInput.session_id || ctx.sessionId || "unknown";

  // Flush pending error context (Issue #1636)
  // Save any pending error context at session end
  const errorContextManager = getErrorContextManager();
  const contextFlushed = (await errorContextManager.flushPending(sessionId)) !== undefined;

  // Metrics collection (non-blocking)
  const metricsSuccess = collectSessionMetrics(sessionId);

  // Report generation (Issue #1367)
  const reportSuccess = generateSessionReport(sessionId);

  logHookExecution(
    HOOK_NAME,
    "approve",
    `Session metrics ${metricsSuccess ? "collected" : "collection failed"}, ` +
      `report ${reportSuccess ? "generated" : "generation failed"}, ` +
      `error context ${contextFlushed ? "flushed" : "no pending"}`,
    {
      metrics_success: metricsSuccess,
      report_success: reportSuccess,
      context_flushed: contextFlushed,
    },
  );

  // Always approve regardless of collection/report success
  console.log(JSON.stringify({}));
}

// Execute
if (import.meta.main) {
  main().catch((error) => {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    // Still approve on error to not block session end
    console.log(JSON.stringify({}));
  });
}
