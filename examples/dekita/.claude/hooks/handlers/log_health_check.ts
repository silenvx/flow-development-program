#!/usr/bin/env bun
/**
 * セッション終了時にログの健全性を自動検証する。
 *
 * Why:
 *   ログ記録の問題（権限エラー、ディスク不足、セッションID不一致）を
 *   早期検出することで、メトリクス収集の信頼性を向上させる。
 *
 * What:
 *   - ログディレクトリ/ファイルの書き込み権限を確認
 *   - ディスク容量の閾値チェック
 *   - ログファイルの鮮度（更新日時）チェック
 *   - メトリクスとログエントリ数の整合性検証
 *
 * State:
 *   - reads: .claude/logs/execution/hook-execution-{session}.jsonl
 *   - reads: .claude/logs/metrics/session-metrics.log
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、systemMessageで警告）
 *   - Stopで発火
 *   - session_metrics_collector.pyはメトリクス収集（責務分離）
 *   - 閾値: 最低100MB空き容量、10分以内の更新、5回以上のフック実行
 *
 * Changelog:
 *   - silenvx/dekita#1251: フック追加
 *   - silenvx/dekita#1455: ログファイル鮮度チェック追加
 *   - silenvx/dekita#1456: 書き込み権限・ディスク容量チェック追加
 *   - silenvx/dekita#2068: セッション毎ファイル形式に対応
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { execFileSync } from "node:child_process";
import { constants, accessSync, existsSync, readFileSync, statSync } from "node:fs";
import { basename, join } from "node:path";
import { EXECUTION_LOG_DIR, METRICS_LOG_DIR } from "../lib/common";
import { logHookExecution } from "../lib/logging";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "log-health-check";

// Thresholds
const THRESHOLD_MIN_HOOK_EXECUTIONS = 5;
const THRESHOLD_MAX_HOOK_EXECUTIONS = 500;
const THRESHOLD_METRICS_LOG_DISCREPANCY_RATIO = 0.5;
const THRESHOLD_LOG_FRESHNESS_MINUTES = 10;
const THRESHOLD_MIN_DISK_SPACE_MB = 100;

interface HealthIssue {
  level: "ERROR" | "WARNING" | "INFO";
  message: string;
  details: Record<string, unknown>;
}

/**
 * Get execution log directory path.
 * EXECUTION_LOG_DIR is already an absolute, worktree-aware path from lib/common.
 */
function getExecutionLogDir(): string {
  return EXECUTION_LOG_DIR;
}

/**
 * Get metrics log directory path.
 * METRICS_LOG_DIR is already an absolute, worktree-aware path from lib/common.
 */
function getMetricsLogDir(): string {
  return METRICS_LOG_DIR;
}

/**
 * Get session log file path.
 */
function getSessionLogFile(logDir: string, prefix: string, sessionId: string): string {
  const safeSessionId = basename(sessionId);
  return join(logDir, `${prefix}-${safeSessionId}.jsonl`);
}

/**
 * Check if a path is writable.
 */
function checkLogWritable(logPath: string): { isWritable: boolean; errorMsg: string | null } {
  try {
    const stat = statSync(logPath);
    if (stat.isDirectory()) {
      try {
        accessSync(logPath, constants.W_OK);
        return { isWritable: true, errorMsg: null };
      } catch {
        return {
          isWritable: false,
          errorMsg: `ディレクトリへの書き込み権限がありません: ${logPath}`,
        };
      }
    }

    try {
      accessSync(logPath, constants.W_OK);
      return { isWritable: true, errorMsg: null };
    } catch {
      return { isWritable: false, errorMsg: `ファイルへの書き込み権限がありません: ${logPath}` };
    }
  } catch {
    // File doesn't exist, check parent directory
    const parentDir = join(logPath, "..");
    try {
      accessSync(parentDir, constants.W_OK);
      return { isWritable: true, errorMsg: null };
    } catch {
      return {
        isWritable: false,
        errorMsg: `ログディレクトリへの書き込み権限がありません: ${parentDir}`,
      };
    }
  }
}

/**
 * Check disk space (using df command).
 */
function checkDiskSpace(logPath: string): {
  isSufficient: boolean;
  errorMsg: string | null;
  freeMb: number;
} {
  try {
    // Use execFileSync to avoid shell injection and platform dependencies
    // -P: POSIX format (prevents line wrapping for long mount paths)
    const result = execFileSync("df", ["-P", "-m", logPath], {
      encoding: "utf-8",
      timeout: 5000,
    });

    // Parse df output: Filesystem 1M-blocks Used Available ...
    const lines = result.trim().split("\n");
    if (lines.length < 2) {
      return { isSufficient: true, errorMsg: null, freeMb: 0 };
    }

    // Get the last line (actual data, not header)
    const dataLine = lines[lines.length - 1];
    const parts = dataLine.trim().split(/\s+/);
    // Index 3 is 'Available' (4th column)
    const freeMb = Number.parseInt(parts[3], 10);

    if (Number.isNaN(freeMb)) {
      return { isSufficient: true, errorMsg: null, freeMb: 0 };
    }

    if (freeMb < THRESHOLD_MIN_DISK_SPACE_MB) {
      return {
        isSufficient: false,
        errorMsg: `ディスク容量が不足しています: 空き ${freeMb}MB (閾値: ${THRESHOLD_MIN_DISK_SPACE_MB}MB)`,
        freeMb,
      };
    }

    return { isSufficient: true, errorMsg: null, freeMb };
  } catch {
    return { isSufficient: true, errorMsg: null, freeMb: 0 };
  }
}

/**
 * Check log file freshness.
 */
function checkLogFreshness(
  logFile: string,
  thresholdMinutes = THRESHOLD_LOG_FRESHNESS_MINUTES,
): { isFresh: boolean; ageMinutes: number | null } {
  if (!existsSync(logFile)) {
    return { isFresh: false, ageMinutes: null };
  }

  try {
    const stat = statSync(logFile);
    const mtime = stat.mtimeMs;
    const now = Date.now();
    const ageMinutes = (now - mtime) / 1000 / 60;
    const isFresh = ageMinutes < thresholdMinutes;
    return { isFresh, ageMinutes: Math.round(ageMinutes * 10) / 10 };
  } catch {
    return { isFresh: false, ageMinutes: null };
  }
}

/**
 * Count hook executions in session log.
 */
function countHookExecutionsInLog(sessionId: string): number {
  if (!sessionId) {
    return 0;
  }

  const logFile = getSessionLogFile(getExecutionLogDir(), "hook-execution", sessionId);
  if (!existsSync(logFile)) {
    return 0;
  }

  try {
    const content = readFileSync(logFile, "utf-8");
    let count = 0;
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        JSON.parse(trimmed);
        count++;
      } catch {
        // Skip invalid JSON lines
      }
    }
    return count;
  } catch {
    return 0;
  }
}

/**
 * Get session metrics from log.
 */
function getSessionMetrics(sessionId: string): Record<string, unknown> | null {
  const metricsFile = join(getMetricsLogDir(), "session-metrics.log");
  if (!existsSync(metricsFile)) {
    return null;
  }

  try {
    const content = readFileSync(metricsFile, "utf-8");
    let foundEntry: Record<string, unknown> | null = null;

    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const entry = JSON.parse(trimmed) as Record<string, unknown>;
        if (entry.session_id === sessionId) {
          foundEntry = entry;
        }
      } catch {
        // Skip invalid JSON lines
      }
    }

    return foundEntry;
  } catch {
    return null;
  }
}

/**
 * Check log health.
 */
function checkLogHealth(sessionId: string): HealthIssue[] {
  const issues: HealthIssue[] = [];
  const executionLogDir = getExecutionLogDir();
  const metricsLogDir = getMetricsLogDir();

  // Check write permissions
  const pathsToCheck = [executionLogDir, metricsLogDir, join(metricsLogDir, "session-metrics.log")];

  if (sessionId) {
    const sessionLogFile = getSessionLogFile(executionLogDir, "hook-execution", sessionId);
    if (existsSync(sessionLogFile)) {
      pathsToCheck.push(sessionLogFile);
    }
  }

  for (const logPath of pathsToCheck) {
    const { isWritable, errorMsg } = checkLogWritable(logPath);
    if (!isWritable) {
      issues.push({
        level: "ERROR",
        message: "ログ書き込み権限エラー",
        details: {
          path: logPath,
          error: errorMsg,
          possible_cause: "パーミッション設定を確認してください",
        },
      });
    }
  }

  // Check disk space
  const { isSufficient, errorMsg: diskError, freeMb } = checkDiskSpace(metricsLogDir);
  if (!isSufficient) {
    issues.push({
      level: "WARNING",
      message: "ディスク容量警告",
      details: {
        path: metricsLogDir,
        free_mb: freeMb,
        threshold_mb: THRESHOLD_MIN_DISK_SPACE_MB,
        error: diskError,
        possible_cause: "不要なファイルを削除してディスク容量を確保してください",
      },
    });
  }

  // Check log freshness
  if (sessionId) {
    const sessionLogFile = getSessionLogFile(executionLogDir, "hook-execution", sessionId);
    const { isFresh: hookLogFresh, ageMinutes: hookLogAge } = checkLogFreshness(sessionLogFile);
    if (hookLogAge !== null && !hookLogFresh) {
      issues.push({
        level: "WARNING",
        message: `セッションログの更新が古いです: ${hookLogAge}分前`,
        details: {
          log_file: sessionLogFile,
          age_minutes: hookLogAge,
          threshold_minutes: THRESHOLD_LOG_FRESHNESS_MINUTES,
          possible_cause: "フックログ記録が停止している可能性",
        },
      });
    }
  }

  const metricsLogFile = join(metricsLogDir, "session-metrics.log");
  const { isFresh: metricsLogFresh, ageMinutes: metricsLogAge } = checkLogFreshness(metricsLogFile);
  if (metricsLogAge !== null && !metricsLogFresh) {
    issues.push({
      level: "WARNING",
      message: `session-metrics.logの更新が古いです: ${metricsLogAge}分前`,
      details: {
        log_file: metricsLogFile,
        age_minutes: metricsLogAge,
        threshold_minutes: THRESHOLD_LOG_FRESHNESS_MINUTES,
        possible_cause: "メトリクス収集が停止している可能性",
      },
    });
  }

  // Check hook execution counts
  const logEntryCount = countHookExecutionsInLog(sessionId);
  const metrics = getSessionMetrics(sessionId);

  if (metrics) {
    const hookExecutions = (metrics.hook_executions as number) ?? 0;
    const blocks = (metrics.blocks as number) ?? 0;
    const approves = (metrics.approves as number) ?? 0;

    if (hookExecutions === 0 && blocks === 0 && approves === 0) {
      issues.push({
        level: "ERROR",
        message: "セッションメトリクスが全てゼロです",
        details: {
          session_id: sessionId,
          possible_cause: "session_id不一致の可能性があります",
          reference: "Issue #1232",
        },
      });
    } else if (hookExecutions < THRESHOLD_MIN_HOOK_EXECUTIONS) {
      issues.push({
        level: "WARNING",
        message: `フック実行回数が少なすぎます: ${hookExecutions}回`,
        details: {
          session_id: sessionId,
          threshold: THRESHOLD_MIN_HOOK_EXECUTIONS,
          possible_cause: "セッションが短すぎる、またはログ記録の問題",
        },
      });
    } else if (hookExecutions > THRESHOLD_MAX_HOOK_EXECUTIONS) {
      issues.push({
        level: "INFO",
        message: `長時間セッション: フック実行回数 ${hookExecutions}回`,
        details: {
          session_id: sessionId,
          threshold: THRESHOLD_MAX_HOOK_EXECUTIONS,
        },
      });
    }

    // Check consistency between metrics and log entries
    if (logEntryCount > 0 && hookExecutions > 0) {
      const ratio =
        Math.abs(hookExecutions - logEntryCount) / Math.max(hookExecutions, logEntryCount);
      if (ratio > THRESHOLD_METRICS_LOG_DISCREPANCY_RATIO) {
        issues.push({
          level: "WARNING",
          message: "メトリクスとログエントリ数に大きな乖離があります",
          details: {
            session_id: sessionId,
            metrics_hook_executions: hookExecutions,
            log_entry_count: logEntryCount,
            discrepancy_ratio: Math.round(ratio * 100) / 100,
            threshold: THRESHOLD_METRICS_LOG_DISCREPANCY_RATIO,
            possible_cause: "ログ記録の重複または欠落の可能性",
          },
        });
      }
    }
  } else {
    if (logEntryCount === 0) {
      issues.push({
        level: "WARNING",
        message: "セッションログにエントリがありません",
        details: {
          session_id: sessionId,
          possible_cause: "ログ記録が正常に動作していない可能性",
        },
      });
    } else if (logEntryCount < THRESHOLD_MIN_HOOK_EXECUTIONS) {
      issues.push({
        level: "WARNING",
        message: `フック実行回数が少なすぎます（ログ直接カウント）: ${logEntryCount}回`,
        details: {
          session_id: sessionId,
          threshold: THRESHOLD_MIN_HOOK_EXECUTIONS,
          note: "session-metrics.logにエントリなし、セッションログから直接カウント",
        },
      });
    }
  }

  return issues;
}

/**
 * Format health report.
 */
function formatHealthReport(issues: HealthIssue[]): string {
  if (issues.length === 0) {
    return "";
  }

  const lines = ["\n[log_health_check] ログ健全性レポート:"];

  for (const issue of issues) {
    const prefix = issue.level === "ERROR" ? "❌" : issue.level === "WARNING" ? "⚠️" : "ℹ️";
    lines.push(`  ${prefix} [${issue.level}] ${issue.message}`);

    const details = issue.details;
    if (details.possible_cause) {
      lines.push(`      原因: ${details.possible_cause}`);
    }
    if (details.reference) {
      lines.push(`      参照: ${details.reference}`);
    }

    for (const [key, value] of Object.entries(details)) {
      if (key === "possible_cause" || key === "reference" || key === "session_id") {
        continue;
      }
      if (value === null || value === undefined || value === "") {
        continue;
      }
      const formattedValue = typeof value === "object" ? JSON.stringify(value) : String(value);
      lines.push(`      ${key}: ${formattedValue}`);
    }
  }

  return lines.join("\n");
}

async function main(): Promise<void> {
  const hookInput = await parseHookInput();
  const ctx = createHookContext(hookInput);

  // Prevent infinite loops in Stop hooks
  if (hookInput.stop_hook_active) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  const sessionId = (hookInput.session_id as string) ?? ctx.sessionId;

  // Run health check
  const issues = checkLogHealth(sessionId);

  // Log results
  const hasErrors = issues.some((i) => i.level === "ERROR");
  const hasWarnings = issues.some((i) => i.level === "WARNING");

  await logHookExecution(
    HOOK_NAME,
    "approve",
    `Health check completed: ${issues.length} issue(s) found`,
    {
      issues_count: issues.length,
      has_errors: hasErrors,
      has_warnings: hasWarnings,
      issues,
    },
    { sessionId },
  );

  // Format report
  const report = formatHealthReport(issues);

  // Always approve, use systemMessage for issues
  const result: { continue: boolean; systemMessage?: string } = { continue: true };
  if (report) {
    result.systemMessage = report;
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
