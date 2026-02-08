#!/usr/bin/env bun
/**
 * セッション終了時に誤検知パターンを検出して警告する。
 *
 * Why:
 *   同じフックが短時間に連続でブロックする場合、誤検知の可能性が高い。
 *   セッション終了時にパターンを分析し、Issue作成を促すことで
 *   フックの品質改善につなげる。
 *
 * What:
 *   - セッションのブロックログを読み込み
 *   - 30秒以内に同じフックが2回以上ブロックした連続パターンを検出
 *   - 検出した場合は警告を表示し、Issue作成を促す
 *
 * State:
 *   - reads: .claude/logs/execution/hook-execution-{session}.jsonl
 *
 * Remarks:
 *   - Stop hookとして発動（セッション終了時）
 *   - ブロックはせず警告のみ
 *
 * Changelog:
 *   - silenvx/dekita#2437: フック追加
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { EXECUTION_LOG_DIR } from "../lib/common";
import { logHookExecution, readSessionLogEntries } from "../lib/logging";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "false-positive-detector";

// 連続ブロックの閾値（秒）
const CONSECUTIVE_BLOCK_THRESHOLD_SECONDS = 30;

/**
 * Get execution log directory path.
 * EXECUTION_LOG_DIR is already an absolute, worktree-aware path from lib/common.
 */
function getExecutionLogDir(): string {
  return EXECUTION_LOG_DIR;
}

/**
 * Parse timestamp string to Date.
 * Returns null for invalid timestamps (including "Invalid Date").
 */
function parseTimestamp(tsStr: string): Date | null {
  if (!tsStr) {
    return null;
  }

  try {
    const date = new Date(tsStr);
    // new Date() returns "Invalid Date" for invalid strings instead of throwing
    if (Number.isNaN(date.getTime())) {
      return null;
    }
    return date;
  } catch {
    return null;
  }
}

/**
 * Detect consecutive block patterns.
 */
function detectConsecutiveBlocks(
  blocks: Array<Record<string, unknown>>,
): Map<string, Array<[string, string]>> {
  // Group blocks by hook
  const blocksByHook = new Map<string, Array<Record<string, unknown>>>();

  for (const block of blocks) {
    const hook = block.hook as string | undefined;
    if (hook) {
      const existing = blocksByHook.get(hook) ?? [];
      existing.push(block);
      blocksByHook.set(hook, existing);
    }
  }

  const consecutivePatterns = new Map<string, Array<[string, string]>>();

  for (const [hook, hookBlocks] of blocksByHook) {
    // Filter blocks with timestamps
    const timestampedBlocks = hookBlocks.filter((b) => b.timestamp);
    if (timestampedBlocks.length < 2) {
      continue;
    }

    // Sort by timestamp
    const sortedBlocks = timestampedBlocks.sort((a, b) => {
      const tsA = a.timestamp as string;
      const tsB = b.timestamp as string;
      return tsA.localeCompare(tsB);
    });

    const pairs: Array<[string, string]> = [];

    for (let i = 0; i < sortedBlocks.length - 1; i++) {
      const ts1 = parseTimestamp(sortedBlocks[i].timestamp as string);
      const ts2 = parseTimestamp(sortedBlocks[i + 1].timestamp as string);

      if (ts1 && ts2) {
        const diffSeconds = Math.abs(ts2.getTime() - ts1.getTime()) / 1000;
        if (diffSeconds <= CONSECUTIVE_BLOCK_THRESHOLD_SECONDS) {
          pairs.push([
            sortedBlocks[i].timestamp as string,
            sortedBlocks[i + 1].timestamp as string,
          ]);
        }
      }
    }

    if (pairs.length > 0) {
      consecutivePatterns.set(hook, pairs);
    }
  }

  return consecutivePatterns;
}

/**
 * Format warning message.
 */
function formatWarningMessage(patterns: Map<string, Array<[string, string]>>): string {
  const lines = [
    "## 誤検知の可能性があるブロックパターンを検出",
    "",
    "以下のフックで短時間に連続ブロックが発生しました:",
    "",
  ];

  for (const [hook, pairs] of patterns) {
    lines.push(`### ${hook}`);
    lines.push(`  連続ブロック: ${pairs.length}回`);
    for (const [ts1, ts2] of pairs.slice(0, 3)) {
      lines.push(`  - ${ts1} → ${ts2}`);
    }
    if (pairs.length > 3) {
      lines.push(`  - ... 他 ${pairs.length - 3}件`);
    }
    lines.push("");
  }

  lines.push(
    "**推奨アクション**:",
    "1. 上記フックの検出ロジックを確認",
    "2. 誤検知であればIssueを作成:",
    "",
    "```bash",
    'gh issue create --title "フック誤検知: <フック名>" \\',
    '  --body "## 問題\\n<再現手順>\\n\\n## 期待動作\\n<期待動作>" \\',
    '  --label "bug,P2"',
    "```",
  );

  return lines.join("\n");
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    const hookInput = await parseHookInput();
    const ctx = createHookContext(hookInput);

    sessionId = ctx.sessionId;
    if (!sessionId) {
      await logHookExecution(HOOK_NAME, "approve", "no_session_id", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Load block events from session log
    const logDir = getExecutionLogDir();
    const entries = await readSessionLogEntries(logDir, "hook-execution", sessionId);
    const blocks = entries.filter((e) => e.decision === "block");

    if (blocks.length === 0) {
      await logHookExecution(HOOK_NAME, "approve", "no_blocks", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Detect consecutive block patterns
    const patterns = detectConsecutiveBlocks(blocks);

    if (patterns.size > 0) {
      const warningMessage = formatWarningMessage(patterns);

      // Log pattern details
      const patternCounts: Record<string, number> = {};
      for (const [hook, pairs] of patterns) {
        patternCounts[hook] = pairs.length;
      }

      await logHookExecution(
        HOOK_NAME,
        "approve",
        "patterns_detected",
        {
          patterns: patternCounts,
        },
        { sessionId },
      );

      result.systemMessage = warningMessage;
    } else {
      await logHookExecution(HOOK_NAME, "approve", "no_patterns", undefined, { sessionId });
    }
  } catch {
    // フック実行の失敗でClaude Codeをブロックしない
    await logHookExecution(HOOK_NAME, "approve", "error", undefined, { sessionId }).catch(() => {
      // 意図的に空 - ログ記録のエラーは致命的ではないため
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
