#!/usr/bin/env bun
/**
 * フックの期待動作と実際の動作のギャップを自動検知する。
 *
 * Why:
 *   フックが正しく動作しているかを自動検証し、サイレント障害や
 *   異常な動作パターンを早期発見する。問題のあるフックを放置すると
 *   ワークフロー全体の品質が低下する。
 *
 * What:
 *   - サイレント障害検出（例外発生したフック）
 *   - ブロックループ検出（短時間に連続ブロック）
 *   - 未実行フック検出（登録されているが実行されていない）
 *
 * State:
 *   reads: .claude/logs/execution/hook-execution-*.jsonl
 *   reads: .claude/settings.json
 *   writes: .claude/logs/metrics/behavior-anomalies-*.jsonl
 *
 * Remarks:
 *   - 情報提供のみでブロックしない（Stopフック）
 *   - hook_effectiveness_evaluatorは効率性評価、これは動作評価
 *   - settings.jsonから登録フック一覧を取得
 *
 * Changelog:
 *   - silenvx/dekita#1317: 専用メトリクスログファイル追加
 *   - silenvx/dekita#1840: セッション固有ファイルへの出力対応
 *   - silenvx/dekita#2607: HookContextによるセッションID管理追加
 *   - silenvx/dekita#2762: metadata.json依存を削除、settings.jsonベースに移行
 *   - silenvx/dekita#3160: TypeScript移行
 */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { EXECUTION_LOG_DIR, METRICS_LOG_DIR, PROJECT_DIR } from "../lib/common";
import { formatError } from "../lib/format_error";
import { logToSessionFile } from "../lib/logging";
import { approveAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { getLocalTimestamp } from "../lib/timestamp";
import { type HookContext, type HookInput, createHookContext } from "../lib/types";

// =============================================================================
// Configuration Constants
// =============================================================================

/** Analyze last 60 minutes */
const SESSION_WINDOW_MINUTES = 60;
/** Same hook blocking 5+ times in short window */
const LOOP_THRESHOLD = 5;
/** Window for loop detection */
const LOOP_WINDOW_SECONDS = 60;
/** Maximum issues to report */
const MAX_ISSUES_TO_REPORT = 10;

/** Path to settings */
const SETTINGS_PATH = join(PROJECT_DIR, ".claude", "settings.json");

/** Self reference to avoid infinite loops */
const HOOK_NAME = "hook_behavior_evaluator";

// =============================================================================
// Types
// =============================================================================

interface HookSettings {
  hooks?: Record<string, HookGroup[]>;
}

interface HookGroup {
  hooks?: HookConfig[];
}

interface HookConfig {
  command?: string;
}

interface LogEntry {
  timestamp?: string;
  hook?: string;
  decision?: string;
  reason?: string;
  _parsedTimestamp?: Date;
}

interface BehaviorIssue {
  type: "silent_failure" | "block_loop" | "missing_execution";
  hook: string;
  count?: number;
  examples?: string[];
  windowSeconds?: number;
  trigger?: string;
  message: string;
}

// =============================================================================
// Settings Loading
// =============================================================================

/**
 * Load settings.json.
 */
function loadSettings(): HookSettings {
  try {
    if (existsSync(SETTINGS_PATH)) {
      const content = readFileSync(SETTINGS_PATH, "utf-8");
      return JSON.parse(content);
    }
  } catch {
    // Settings file is optional; return empty if unreadable
  }
  return {};
}

/**
 * Extract registered hooks and their triggers from settings.json.
 *
 * @returns Map of hook_name to list of trigger types
 */
function getRegisteredHooks(settings: HookSettings): Map<string, string[]> {
  const registered = new Map<string, string[]>();
  const hooksConfig = settings.hooks ?? {};

  for (const [eventType, eventHooks] of Object.entries(hooksConfig)) {
    if (!Array.isArray(eventHooks)) continue;

    for (const hookGroup of eventHooks) {
      if (typeof hookGroup !== "object" || hookGroup === null) continue;

      const hooksList = hookGroup.hooks ?? [];
      for (const hook of hooksList) {
        if (typeof hook !== "object" || hook === null) continue;

        const command = hook.command ?? "";
        // Extract hook name from command path
        // e.g., "bun run .../hooks/branch_check.ts" -> "branch_check"
        // or "python3 .../hooks/branch_check.py" -> "branch_check"
        const match = command.match(/\/([^/]+)\.(ts|py)["']?\s*$/);
        if (match) {
          const hookName = match[1];
          if (!registered.has(hookName)) {
            registered.set(hookName, []);
          }
          const triggers = registered.get(hookName)!;
          if (!triggers.includes(eventType)) {
            triggers.push(eventType);
          }
        }
      }
    }
  }
  return registered;
}

// =============================================================================
// Log Loading
// =============================================================================

/**
 * Load hook execution logs from current session window (all sessions).
 */
function loadSessionLogs(sessionWindowMinutes: number = SESSION_WINDOW_MINUTES): LogEntry[] {
  const entries: LogEntry[] = [];
  const cutoff = new Date(Date.now() - sessionWindowMinutes * 60 * 1000);

  try {
    if (!existsSync(EXECUTION_LOG_DIR)) {
      return [];
    }

    const files = readdirSync(EXECUTION_LOG_DIR);
    const pattern = /^hook-execution-.*\.jsonl$/;

    for (const file of files) {
      if (!pattern.test(file)) continue;

      const filePath = join(EXECUTION_LOG_DIR, file);
      try {
        const content = readFileSync(filePath, "utf-8");
        for (const line of content.split("\n")) {
          const trimmed = line.trim();
          if (!trimmed) continue;

          try {
            const entry = JSON.parse(trimmed) as LogEntry;
            const tsStr = entry.timestamp ?? "";
            if (tsStr) {
              const ts = new Date(tsStr);
              if (ts >= cutoff) {
                entry._parsedTimestamp = ts;
                entries.push(entry);
              }
            }
          } catch {
            // Skip malformed entries
          }
        }
      } catch {
        // Skip unreadable files
      }
    }
  } catch {
    // Directory access error
  }

  return entries;
}

// =============================================================================
// Detection Functions
// =============================================================================

/**
 * Detect hooks that threw exceptions or had errors.
 */
function detectSilentFailures(logs: LogEntry[]): BehaviorIssue[] {
  const issues: BehaviorIssue[] = [];
  const errorPatterns = [
    /Error:/i,
    /Exception/i,
    /Traceback/i,
    /failed to/i,
    /timeout/i,
    /could not/i,
  ];

  const errorHooks = new Map<string, string[]>();

  for (const entry of logs) {
    const hook = entry.hook ?? "unknown";
    if (hook === HOOK_NAME) continue;

    const reason = entry.reason ?? "";
    if (reason && errorPatterns.some((pattern) => pattern.test(reason))) {
      if (!errorHooks.has(hook)) {
        errorHooks.set(hook, []);
      }
      errorHooks.get(hook)!.push(reason.slice(0, 100));
    }
  }

  for (const [hook, errors] of errorHooks) {
    if (errors.length > 0) {
      issues.push({
        type: "silent_failure",
        hook,
        count: errors.length,
        examples: errors.slice(0, 3),
        message: `${hook} で ${errors.length} 件のエラーが発生。例: ${errors[0].slice(0, 50)}...`,
      });
    }
  }

  return issues;
}

/**
 * Detect hooks that block repeatedly in short time windows.
 */
function detectBlockLoops(logs: LogEntry[]): BehaviorIssue[] {
  const issues: BehaviorIssue[] = [];

  // Sort logs by timestamp
  const sortedLogs = logs
    .filter((log) => log._parsedTimestamp)
    .sort((a, b) => a._parsedTimestamp!.getTime() - b._parsedTimestamp!.getTime());

  // Track consecutive blocks per hook
  const hookBlockSequences = new Map<string, Date[]>();

  for (const entry of sortedLogs) {
    if (entry.decision !== "block") continue;

    const hook = entry.hook ?? "unknown";
    if (hook === HOOK_NAME) continue;

    if (!hookBlockSequences.has(hook)) {
      hookBlockSequences.set(hook, []);
    }
    hookBlockSequences.get(hook)!.push(entry._parsedTimestamp!);
  }

  // Analyze sequences for loops
  for (const [hook, timestamps] of hookBlockSequences) {
    if (timestamps.length < LOOP_THRESHOLD) continue;

    // Check for clusters within LOOP_WINDOW_SECONDS
    for (let i = 0; i <= timestamps.length - LOOP_THRESHOLD; i++) {
      const windowStart = timestamps[i];
      const windowEnd = new Date(windowStart.getTime() + LOOP_WINDOW_SECONDS * 1000);
      const blocksInWindow = timestamps.slice(i).filter((ts) => ts <= windowEnd).length;

      if (blocksInWindow >= LOOP_THRESHOLD) {
        issues.push({
          type: "block_loop",
          hook,
          count: blocksInWindow,
          windowSeconds: LOOP_WINDOW_SECONDS,
          message: `${hook} が ${LOOP_WINDOW_SECONDS}秒以内に ${blocksInWindow}回連続ブロック。無限ループの兆候または条件の見直しが必要。`,
        });
        break; // Report once per hook
      }
    }
  }

  return issues;
}

/**
 * Detect hooks registered in settings.json but never executed.
 *
 * Note: If no PreToolUse/PostToolUse hooks were executed during the session,
 * this check is skipped to avoid false positives for sessions without tool usage.
 */
function detectMissingHooks(
  logs: LogEntry[],
  registeredHooks: Map<string, string[]>,
): BehaviorIssue[] {
  const issues: BehaviorIssue[] = [];
  const executedHooks = new Set(logs.map((entry) => entry.hook));

  const hasPreToolPostTool = (triggers: string[]): boolean => {
    return triggers.some((t) => t.includes("PreToolUse") || t.includes("PostToolUse"));
  };

  // Check if any PreToolUse/PostToolUse hooks were executed in this session
  const preToolPostToolHooks = new Set<string>();
  for (const [hookName, triggers] of registeredHooks) {
    if (hasPreToolPostTool(triggers)) {
      preToolPostToolHooks.add(hookName);
    }
  }

  const executedPreToolPostTool = [...executedHooks].filter((hook) =>
    preToolPostToolHooks.has(hook ?? ""),
  );
  if (executedPreToolPostTool.length === 0) {
    return issues; // Skip check - no tool usage in this session
  }

  for (const [hookName, triggers] of registeredHooks) {
    if (executedHooks.has(hookName)) continue;
    if (hookName === HOOK_NAME) continue;

    // Only report if it's a PreToolUse or PostToolUse hook
    if (hasPreToolPostTool(triggers)) {
      issues.push({
        type: "missing_execution",
        hook: hookName,
        trigger: triggers.join(", "),
        message: `${hookName} は登録済みだがセッション中に一度も実行されていない。設定ミスまたはトリガー条件の問題の可能性。`,
      });
    }
  }

  return issues;
}

// =============================================================================
// Logging Functions
// =============================================================================

/**
 * Log behavior anomalies to dedicated metrics file.
 *
 * Logs each issue as a separate JSONL entry for easier analysis and aggregation.
 */
async function logBehaviorAnomalies(
  ctx: HookContext,
  issues: BehaviorIssue[],
  logCount: number,
): Promise<void> {
  if (issues.length === 0) return;

  const sessionId = ctx.sessionId;
  if (!sessionId) return;

  for (const issue of issues) {
    const entry: Record<string, unknown> = {
      timestamp: getLocalTimestamp(),
      analyzed_logs: logCount,
      type: issue.type,
      hook: issue.hook,
    };

    // Add type-specific details
    if (issue.type === "silent_failure") {
      entry.count = issue.count ?? 0;
      entry.examples = (issue.examples ?? []).slice(0, 3);
    } else if (issue.type === "block_loop") {
      entry.count = issue.count ?? 0;
      entry.window_seconds = issue.windowSeconds ?? 0;
    } else if (issue.type === "missing_execution") {
      entry.trigger = issue.trigger ?? "";
    }

    await logToSessionFile(METRICS_LOG_DIR, "behavior-anomalies", sessionId, entry);
  }
}

// =============================================================================
// Report Formatting
// =============================================================================

/**
 * Format issues into a human-readable report.
 */
function formatReport(issues: BehaviorIssue[], logCount: number): string {
  if (issues.length === 0) return "";

  const lines: string[] = [
    "## Hook 動作評価レポート",
    `分析対象: 直近 ${SESSION_WINDOW_MINUTES} 分間、${logCount} 件の実行ログ`,
    "",
  ];

  // Group by type
  const byType = new Map<string, BehaviorIssue[]>();
  for (const issue of issues) {
    if (!byType.has(issue.type)) {
      byType.set(issue.type, []);
    }
    byType.get(issue.type)!.push(issue);
  }

  const typeLabels: Record<string, string> = {
    silent_failure: "エラー検出",
    block_loop: "ブロックループ兆候",
    missing_execution: "未実行 Hook",
  };

  for (const [issueType, typeIssues] of byType) {
    const label = typeLabels[issueType] ?? issueType;
    lines.push(`### ${label} (${typeIssues.length} 件)`);
    for (let i = 0; i < Math.min(typeIssues.length, MAX_ISSUES_TO_REPORT); i++) {
      lines.push(`${i + 1}. ${typeIssues[i].message}`);
    }
    lines.push("");
  }

  lines.push(
    "---",
    "**推奨アクション**:",
    "- エラー検出: ログを確認し、例外処理を改善",
    "- ブロックループ: トリガー条件が厳しすぎないか確認",
    "- 未実行: settings.json の登録状況を確認",
  );

  return lines.join("\n");
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};

  try {
    const input: HookInput = await parseHookInput();
    const ctx = createHookContext(input);

    // Prevent infinite loops
    if (input.stop_hook_active) {
      approveAndExit(HOOK_NAME);
    }

    // Load data
    const logs = loadSessionLogs();
    const settings = loadSettings();
    const registeredHooks = getRegisteredHooks(settings);

    if (logs.length === 0) {
      // Early return when no logs
      console.log(JSON.stringify(result));
      return;
    }

    // Run detections
    const allIssues: BehaviorIssue[] = [
      ...detectSilentFailures(logs),
      ...detectBlockLoops(logs),
      ...detectMissingHooks(logs, registeredHooks),
    ];

    if (allIssues.length > 0) {
      const report = formatReport(allIssues, logs.length);
      result.systemMessage = report;

      // Log to dedicated metrics file
      await logBehaviorAnomalies(ctx, allIssues, logs.length);
    }
  } catch (error) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({}));
  });
}
