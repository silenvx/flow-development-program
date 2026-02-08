#!/usr/bin/env bun
/**
 * セッション終了時にローテート済みログを圧縮。
 *
 * Why:
 *   ログファイルのローテート後、古いファイル（.log.1, .log.2等）が
 *   ディスクを圧迫する。gzip圧縮してストレージを節約する。
 *
 * What:
 *   - セッション終了時（Stop）に発火
 *   - execution/とmetrics/のローテート済みログを検索
 *   - .log.N形式のファイルをgzip圧縮
 *   - 圧縮した件数をログに記録
 *
 * Remarks:
 *   - 非ブロック型（Stopフック）
 *   - ローテーション自体はcommon.pyが担当
 *   - 既に圧縮済み（.gz）のファイルはスキップ
 *
 * Changelog:
 *   - silenvx/dekita#710: フック追加（Python）
 *   - silenvx/dekita#3148: TypeScriptに移行
 */

import { EXECUTION_LOG_DIR, METRICS_LOG_DIR } from "../lib/common";
import { compressRotatedLogs, logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "session-log-compressor";

// Note: EXECUTION_LOG_DIR and METRICS_LOG_DIR are already absolute paths from lib/common

async function main(): Promise<void> {
  // Read hook input
  const hookInput = await parseHookInput();
  const sessionId = hookInput?.session_id;

  // Skip if Stop hook is already active (prevent infinite loop)
  if (hookInput?.stop_hook_active) {
    console.log(JSON.stringify({}));
    return;
  }

  // Compress rotated logs in both directories
  let totalCompressed = 0;
  totalCompressed += await compressRotatedLogs(EXECUTION_LOG_DIR);
  totalCompressed += await compressRotatedLogs(METRICS_LOG_DIR);

  // Log the result
  await logHookExecution(
    HOOK_NAME,
    "approve",
    `Compressed ${totalCompressed} rotated log file(s)`,
    { compressed_count: totalCompressed },
    { sessionId },
  );

  // Always approve - don't block session end
  console.log(JSON.stringify({}));
}

if (import.meta.main) {
  main().catch((error) => {
    console.error(`[${HOOK_NAME}] Unexpected error:`, error);
    console.log(JSON.stringify({}));
    process.exit(0); // Don't block on error
  });
}
