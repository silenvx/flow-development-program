#!/usr/bin/env bun
/**
 * セッション終了時に回避パターンを分析し、警告を表示する。
 *
 * Why:
 *   回避パターンが検出されても、振り返りで分析されなければ対策が講じられない。
 *   セッション終了時に自動的に分析結果を表示することで、対策を促す。
 *
 * What:
 *   - Stopフックで発火
 *   - セッションの回避パターンログを読み込み
 *   - 回避パターンがあれば警告を表示
 *   - 繰り返しパターンを強調
 *
 * State:
 *   - reads: .claude/logs/metrics/bypass-patterns-{session}.jsonl
 *
 * Remarks:
 *   - 非ブロック型（警告のみ、セッション終了をブロックしない）
 *   - 回避パターンがなければ何も表示しない
 *
 * Changelog:
 *   - silenvx/dekita#3009: 初期実装
 */

import { join } from "node:path";
import { METRICS_LOG_DIR } from "../lib/constants";
import { logHookExecution, readSessionLogEntries } from "../lib/logging";
import { isSafeSessionId, parseHookInput } from "../lib/session";
import { truncate } from "../lib/strings";

const HOOK_NAME = "bypass-analysis";

export interface BypassEntry {
  type: string;
  pattern_type?: string;
  description?: string;
  failed_command?: string;
  success_command?: string;
  tool_manager_from?: string;
  tool_manager_to?: string;
  timestamp?: string;
}

/**
 * Get project directory.
 */
function getProjectDir(): string {
  return process.env.CLAUDE_PROJECT_DIR ?? process.cwd();
}

/**
 * Get metrics log directory.
 */
function getMetricsLogDir(): string {
  return join(getProjectDir(), METRICS_LOG_DIR);
}

/**
 * Format bypass patterns for display.
 */
export function formatBypassSummary(patterns: BypassEntry[]): string | null {
  if (patterns.length === 0) {
    return null;
  }

  const lines: string[] = [
    `[${HOOK_NAME}] ⚠️ このセッションで ${patterns.length} 件の回避パターンを検出`,
    "",
  ];

  // Group by pattern type
  const byType: Record<string, BypassEntry[]> = {};
  for (const pattern of patterns) {
    const type = pattern.pattern_type || "unknown";
    if (!byType[type]) {
      byType[type] = [];
    }
    byType[type].push(pattern);
  }

  // Format each type
  for (const [type, typePatterns] of Object.entries(byType)) {
    const typeLabel =
      type === "tool_switch"
        ? "🔄 ツール切り替え"
        : type === "option_change"
          ? "⚙️ オプション変更"
          : `❓ ${type}`;

    lines.push(`### ${typeLabel} (${typePatterns.length}件)`);
    lines.push("");

    // Show up to 3 examples
    for (const pattern of typePatterns.slice(0, 3)) {
      if (pattern.tool_manager_from && pattern.tool_manager_to) {
        lines.push(`  - ${pattern.tool_manager_from} → ${pattern.tool_manager_to}`);
      }
      if (pattern.failed_command) {
        lines.push(`    失敗: \`${truncate(pattern.failed_command, 60)}\``);
      }
      if (pattern.success_command) {
        lines.push(`    成功: \`${truncate(pattern.success_command, 60)}\``);
      }
      lines.push("");
    }

    if (typePatterns.length > 3) {
      lines.push(`  ...他 ${typePatterns.length - 3} 件`);
      lines.push("");
    }
  }

  lines.push("---");
  lines.push("**推奨アクション**:");
  lines.push("1. 回避の根本原因を分析（なぜ最初のコマンドが失敗したか）");
  lines.push("2. 必要に応じてフック/ツールを改善するIssueを作成");
  lines.push("3. `/adding-perspectives` で振り返り観点に追加");

  return lines.join("\n");
}

async function main(): Promise<void> {
  let sessionId: string | undefined;

  try {
    const input = await parseHookInput();
    sessionId = input.session_id || process.env.CLAUDE_SESSION_ID;

    if (!sessionId) {
      await logHookExecution(HOOK_NAME, "approve", "No session ID available", undefined, {
        sessionId: undefined,
      });
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Validate session ID to prevent path traversal attacks
    if (!isSafeSessionId(sessionId)) {
      // Don't log potentially unsafe sessionId
      await logHookExecution(HOOK_NAME, "approve", "Invalid session ID", undefined, {
        sessionId: undefined,
      });
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Read bypass patterns for this session
    const metricsDir = getMetricsLogDir();
    const entries = await readSessionLogEntries(metricsDir, "bypass-patterns", sessionId);

    // Filter for bypass_detected entries
    const bypassPatterns = entries
      .filter(
        (entry) => typeof entry === "object" && entry !== null && entry.type === "bypass_detected",
      )
      .map((entry) => entry as unknown as BypassEntry);

    // Log analysis results
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Analyzed ${bypassPatterns.length} bypass patterns`,
      {
        bypass_count: bypassPatterns.length,
        pattern_types: [...new Set(bypassPatterns.map((p) => p.pattern_type))],
      },
      { sessionId },
    );

    // Format summary if patterns exist
    const summary = formatBypassSummary(bypassPatterns);
    if (summary) {
      // Stop hooks use { continue: true, message: "..." } format
      // (see block_response_tracker.ts for reference)
      console.log(JSON.stringify({ continue: true, message: summary }));
    } else {
      console.log(JSON.stringify({ continue: true }));
    }
  } catch (error) {
    // Fail-open: approve on errors
    const errorMsg = error instanceof Error ? error.message : String(error);
    await logHookExecution(HOOK_NAME, "approve", `Error: ${errorMsg}`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify({ continue: true }));
  }
}

if (import.meta.main) {
  main();
}
