#!/usr/bin/env bun
/**
 * セッション中のフック実行を分析し、改善提案を出力する。
 *
 * Why:
 *   フックが適切に機能しているかをセッション終了時に評価し、
 *   過剰発動・無視された警告・繰り返しブロックを検出する。
 *   フックの品質改善サイクルを回すための情報を提供する。
 *
 * What:
 *   - 過剰発動検出（発動多数でほぼapprove）
 *   - 無視された警告検出（警告出力されても対応なし）
 *   - 繰り返しブロック検出（同じ理由で複数回ブロック）
 *   - 改善提案を生成・出力
 *
 * State:
 *   - reads: .claude/logs/execution/hook-execution-*.jsonl
 *
 * Remarks:
 *   - 情報提供のみでブロックしない
 *   - hook-behavior-evaluatorは動作評価、これは効率性評価
 *   - 自己参照を除外して誤検知を防止
 *
 * Changelog:
 *   - silenvx/dekita#2607: HookContextによるセッションID管理追加
 *   - silenvx/dekita#3159: TypeScriptに移植
 */

import { EXECUTION_LOG_DIR } from "../lib/common";
import { logHookExecution, readAllSessionLogEntries } from "../lib/logging";
import { type HookResult, makeApproveResult, outputResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";

// =============================================================================
// Constants
// =============================================================================

/** More than 10 triggers = potentially overactive */
const OVERACTIVE_THRESHOLD = 10;

/** 95%+ approve = probably noise */
const NOISE_RATIO_THRESHOLD = 0.95;

/** Analyze last 60 minutes */
const SESSION_WINDOW_MINUTES = 60;

/** Max length for reason strings */
const REASON_TRUNCATION_LENGTH = 100;

/** Max length for input preview strings */
const INPUT_PREVIEW_TRUNCATION_LENGTH = 50;

/** Maximum number of suggestions to display */
const MAX_SUGGESTIONS = 5;

/** Minimum warnings to flag as "ignored" */
const WARNING_THRESHOLD = 3;

/** Minimum repeats to flag as "repeated block" */
const REPEATED_BLOCK_THRESHOLD = 3;

/** Exclude this hook from analysis to avoid self-flagging */
const SELF_HOOK_NAME = "hook-effectiveness-evaluator";

/** Warning keywords in reason text */
const WARNING_KEYWORDS = ["warning", "⚠️", "注意", "確認", "推奨"];

// =============================================================================
// Types
// =============================================================================

interface HookStats {
  total: number;
  approve: number;
  block: number;
  warn: number;
  reasons: string[];
  toolNames: string[];
  inputPreviews: string[];
}

interface EffectivenessIssue {
  hook: string;
  type: "overactive" | "repeated_block" | "ignored_warning";
  total?: number;
  approveRatio?: number;
  count?: number;
  reason?: string;
  suggestion: string;
}

interface LogEntry {
  timestamp?: string;
  hook?: string;
  decision?: string;
  reason?: string;
  details?: {
    tool_name?: string;
    input_preview?: string;
    [key: string]: unknown;
  };
  _parsedTimestamp?: Date;
}

// =============================================================================
// Log Loading
// =============================================================================

/**
 * Load hook execution logs from current session window (all sessions).
 */
async function loadSessionLogs(
  sessionWindowMinutes: number = SESSION_WINDOW_MINUTES,
): Promise<LogEntry[]> {
  // EXECUTION_LOG_DIR is already an absolute path from lib/common
  const entries = await readAllSessionLogEntries(EXECUTION_LOG_DIR, "hook-execution");
  if (entries.length === 0) {
    return [];
  }

  const logs: LogEntry[] = [];
  const cutoff = new Date(Date.now() - sessionWindowMinutes * 60 * 1000);

  for (const entry of entries) {
    try {
      let tsStr = (entry.timestamp as string) ?? "";
      if (tsStr) {
        // Normalize timestamp format
        if (tsStr.endsWith("Z")) {
          tsStr = `${tsStr.slice(0, -1)}+00:00`;
        }
        const ts = new Date(tsStr);
        if (ts >= cutoff) {
          (entry as LogEntry)._parsedTimestamp = ts;
          logs.push(entry as LogEntry);
        }
      }
    } catch {
      // Skip entries with invalid timestamps
    }
  }

  return logs;
}

// =============================================================================
// Analysis Functions
// =============================================================================

/**
 * Analyze hook trigger frequency and approve/block ratio.
 */
function analyzeHookFrequency(logs: LogEntry[]): Map<string, HookStats> {
  const stats = new Map<string, HookStats>();

  for (const entry of logs) {
    const hook = entry.hook ?? "unknown";
    const decision = entry.decision ?? "approve";

    if (!stats.has(hook)) {
      stats.set(hook, {
        total: 0,
        approve: 0,
        block: 0,
        warn: 0,
        reasons: [],
        toolNames: [],
        inputPreviews: [],
      });
    }

    const hookStats = stats.get(hook)!;
    hookStats.total++;

    if (decision === "approve") {
      hookStats.approve++;
    } else if (decision === "warn" || decision === "warning") {
      hookStats.warn++;
      const reason = entry.reason ?? "";
      if (reason) {
        hookStats.reasons.push(reason.slice(0, REASON_TRUNCATION_LENGTH));
      }
    } else {
      // block, error, or other non-approve decisions
      hookStats.block++;
      const reason = entry.reason ?? "";
      if (reason) {
        hookStats.reasons.push(reason.slice(0, REASON_TRUNCATION_LENGTH));
      }
    }

    // Collect input context from details
    const details = entry.details;
    if (details && typeof details === "object") {
      const toolName = details.tool_name;
      if (toolName) {
        hookStats.toolNames.push(toolName);
      }
      const inputPreview = details.input_preview ?? "";
      if (inputPreview) {
        hookStats.inputPreviews.push(inputPreview.slice(0, INPUT_PREVIEW_TRUNCATION_LENGTH));
      }
    }
  }

  return stats;
}

/**
 * Count occurrences of each item in an array.
 */
function countItems<T>(items: T[]): Map<T, number> {
  const counts = new Map<T, number>();
  for (const item of items) {
    counts.set(item, (counts.get(item) ?? 0) + 1);
  }
  return counts;
}

/**
 * Get most common items from a count map.
 */
function getMostCommon<T>(counts: Map<T, number>, n: number): Array<[T, number]> {
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, n);
}

/**
 * Detect hooks that trigger too frequently with little effect.
 */
function detectOveractiveHooks(stats: Map<string, HookStats>): EffectivenessIssue[] {
  const issues: EffectivenessIssue[] = [];

  for (const [hook, data] of stats) {
    // Skip self to avoid false positive
    if (hook === SELF_HOOK_NAME) {
      continue;
    }

    const total = data.total;
    const approve = data.approve;

    if (total >= OVERACTIVE_THRESHOLD) {
      const ratio = total > 0 ? approve / total : 0;
      if (ratio >= NOISE_RATIO_THRESHOLD) {
        // Analyze input patterns for more specific suggestions
        const toolNames = data.toolNames;
        const inputPreviews = data.inputPreviews;

        // Find most common tool triggering this hook
        const toolCounter = countItems(toolNames);
        const mostCommonTool = getMostCommon(toolCounter, 1);

        // Find common input patterns
        const inputCounter = countItems(inputPreviews);
        const commonInputs = getMostCommon(inputCounter, 3);

        let suggestion = `${hook}が${total}回発動し${Math.round(ratio * 100 * 10) / 10}%がapprove。`;

        // Add tool-specific insight
        if (mostCommonTool.length > 0) {
          const [tool, count] = mostCommonTool[0];
          suggestion += `主に${tool}ツールで発動(${count}回)。`;
        }

        // Add input pattern insight
        if (commonInputs.length > 0) {
          const patterns = commonInputs
            .slice(0, 2)
            .filter(([inp]) => inp)
            .map(([inp]) => `"${inp}"`);
          if (patterns.length > 0) {
            suggestion += `よく見る入力: ${patterns.join(", ")}。`;
          }
        }

        suggestion += "発動条件を絞るか、不要なら無効化を検討。";

        issues.push({
          hook,
          type: "overactive",
          total,
          approveRatio: Math.round(ratio * 100 * 10) / 10,
          suggestion,
        });
      }
    }
  }

  return issues;
}

/**
 * Detect hooks that repeatedly block for the same reason.
 */
function detectRepeatedBlocks(logs: LogEntry[]): EffectivenessIssue[] {
  const issues: EffectivenessIssue[] = [];
  const blockSequences = new Map<string, string[]>();

  for (const entry of logs) {
    if (entry.decision === "block") {
      const hook = entry.hook ?? "unknown";
      const reason = (entry.reason ?? "").slice(0, REASON_TRUNCATION_LENGTH);

      if (!blockSequences.has(hook)) {
        blockSequences.set(hook, []);
      }
      blockSequences.get(hook)!.push(reason);
    }
  }

  for (const [hook, reasons] of blockSequences) {
    if (reasons.length >= REPEATED_BLOCK_THRESHOLD) {
      // Check for repeated similar reasons
      const reasonCounts = countItems(reasons);
      const mostCommon = getMostCommon(reasonCounts, 1);
      if (mostCommon.length > 0 && mostCommon[0][1] >= REPEATED_BLOCK_THRESHOLD) {
        const [reason, count] = mostCommon[0];
        issues.push({
          hook,
          type: "repeated_block",
          count,
          reason,
          suggestion: `${hook}が同じ理由で${count}回ブロック。ブロック条件の見直しまたはガイダンス改善を検討。`,
        });
      }
    }
  }

  return issues;
}

/**
 * Detect warnings that were likely ignored (same hook warned multiple times).
 *
 * Issue #3211: Consider both explicit warn/warning decisions AND
 * approve decisions with warning keywords in the reason text.
 */
function detectIgnoredWarnings(logs: LogEntry[]): EffectivenessIssue[] {
  const issues: EffectivenessIssue[] = [];
  const warningSequences = new Map<string, number>();

  for (const entry of logs) {
    const decision = entry.decision ?? "approve";
    const hook = entry.hook ?? "unknown";
    const reason = entry.reason ?? "";

    // Issue #3211: Simplified with boolean flags for readability
    const isExplicitWarning = decision === "warn" || decision === "warning";
    const isImplicitWarning =
      decision === "approve" &&
      reason &&
      WARNING_KEYWORDS.some((kw) => reason.toLowerCase().includes(kw));

    if (isExplicitWarning || isImplicitWarning) {
      warningSequences.set(hook, (warningSequences.get(hook) ?? 0) + 1);
    }
  }

  for (const [hook, count] of warningSequences) {
    if (count >= WARNING_THRESHOLD) {
      issues.push({
        hook,
        type: "ignored_warning",
        count,
        suggestion: `${hook}の警告が${count}回出力されたが対応なし。警告メッセージの明確化またはblock化を検討。`,
      });
    }
  }

  return issues;
}

/**
 * Generate actionable improvement suggestions.
 */
function generateImprovementSuggestions(logs: LogEntry[], stats: Map<string, HookStats>): string[] {
  const suggestions: string[] = [];

  // Analyze hooks
  const overactive = detectOveractiveHooks(stats);
  const repeated = detectRepeatedBlocks(logs);
  const ignored = detectIgnoredWarnings(logs);

  for (const issue of overactive) {
    suggestions.push(`[過剰発動] ${issue.suggestion}`);
  }

  for (const issue of repeated) {
    suggestions.push(`[繰り返しブロック] ${issue.suggestion}`);
  }

  for (const issue of ignored) {
    suggestions.push(`[無視された警告] ${issue.suggestion}`);
  }

  return suggestions;
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: HookResult = makeApproveResult("hook-effectiveness-evaluator");

  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);

    // Prevent infinite loops: if stop_hook_active is set, approve immediately
    if (data.stop_hook_active) {
      await logHookExecution(SELF_HOOK_NAME, "approve", "stop_hook_active", undefined, {
        sessionId: ctx.sessionId ?? undefined,
      });
      outputResult(result);
      return;
    }

    // Load and analyze session logs
    const logs = await loadSessionLogs();

    if (logs.length === 0) {
      // No logs to analyze
      await logHookExecution(SELF_HOOK_NAME, "approve", "ログなし", undefined, {
        sessionId: ctx.sessionId ?? undefined,
      });
      outputResult(result);
      return;
    }

    const stats = analyzeHookFrequency(logs);
    const suggestions = generateImprovementSuggestions(logs, stats);

    if (suggestions.length > 0) {
      // Format summary
      const summaryLines = [
        "## フック有効性レビュー",
        `分析対象: 直近${SESSION_WINDOW_MINUTES}分間、${logs.length}件のフック実行`,
        "",
        "### 改善提案:",
      ];

      for (let i = 0; i < Math.min(suggestions.length, MAX_SUGGESTIONS); i++) {
        summaryLines.push(`${i + 1}. ${suggestions[i]}`);
      }

      summaryLines.push(
        "",
        "**アクション**: 上記フックの改善が必要な場合、",
        "- フックスクリプトの条件を調整",
        "- settings.jsonでの無効化",
        "- メッセージの明確化",
        "のいずれかを検討してください。",
      );

      result.systemMessage = summaryLines.join("\n");

      await logHookExecution(
        SELF_HOOK_NAME,
        "approve",
        `${suggestions.length}件の改善提案`,
        { suggestions: suggestions.length, analyzed_logs: logs.length },
        { sessionId: ctx.sessionId ?? undefined },
      );
    } else {
      // No issues found
      await logHookExecution(
        SELF_HOOK_NAME,
        "approve",
        "問題なし",
        { analyzed_logs: logs.length },
        { sessionId: ctx.sessionId ?? undefined },
      );
    }
  } catch (e) {
    // Don't block on errors
    const errorMessage = e instanceof Error ? e.message : String(e);
    console.error(`[${SELF_HOOK_NAME}] Error: ${errorMessage}`);
    await logHookExecution(SELF_HOOK_NAME, "approve", `Error: ${errorMessage}`);
  }

  outputResult(result);
}

if (import.meta.main) {
  main();
}
