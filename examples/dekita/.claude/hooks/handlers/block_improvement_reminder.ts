#!/usr/bin/env bun
/**
 * 同一フックの連続ブロックを検知し、フック改善を提案する。
 *
 * Why:
 *   同じフックが3回以上連続でブロックする場合、フック自体に改善の余地がある
 *   可能性が高い。SKIP環境変数やメッセージ改善を提案する。
 *
 * What:
 *   - セッション内の連続ブロックをフック別にカウント
 *   - 閾値（3回連続）超過で改善リマインダーを表示
 *   - セッション内で同一フックへのリマインダーは1回のみ
 *
 * State:
 *   - reads: .claude/logs/execution/hook-execution-{session}.jsonl
 *   - writes: .claude/logs/session/block-reminder-{session}-{hook}.marker
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、改善提案を表示）
 *   - PreToolUseで発火（次のツール実行前にチェック）
 *   - マーカーファイルで同一フックへの重複リマインダーを防止
 *
 * Changelog:
 *   - silenvx/dekita#2432: フック追加
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";
import { EXECUTION_LOG_DIR } from "../lib/common";
import { logHookExecution, readSessionLogEntries } from "../lib/logging";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "block-improvement-reminder";

// Threshold for consecutive blocks to trigger reminder
const CONSECUTIVE_BLOCK_THRESHOLD = 3;

/**
 * Get execution log directory path.
 * EXECUTION_LOG_DIR is already an absolute, worktree-aware path from lib/common.
 */
function getExecutionLogDir(): string {
  return EXECUTION_LOG_DIR;
}

/**
 * Get session marker directory path.
 */
function getSessionMarkerDir(): string {
  const envDir = process.env.CLAUDE_PROJECT_DIR;
  if (envDir) {
    return join(envDir, ".claude", "logs", "session");
  }
  return join(process.cwd(), ".claude", "logs", "session");
}

/**
 * Count consecutive blocks from each hook in the session.
 */
async function getConsecutiveBlocks(sessionId: string): Promise<Map<string, number>> {
  const logDir = getExecutionLogDir();
  const entries = await readSessionLogEntries(logDir, "hook-execution", sessionId);

  // Track consecutive blocks per hook
  const consecutiveCounts = new Map<string, number>();

  for (const entry of entries) {
    const hook = entry.hook as string | undefined;
    const decision = entry.decision as string | undefined;

    if (!hook || !decision) {
      continue;
    }

    // Reset count if hook approved (or any non-block decision)
    if (decision !== "block") {
      if (consecutiveCounts.has(hook)) {
        consecutiveCounts.set(hook, 0);
      }
    } else {
      // Increment count on block
      const current = consecutiveCounts.get(hook) ?? 0;
      consecutiveCounts.set(hook, current + 1);
    }
  }

  return consecutiveCounts;
}

/**
 * Check if reminder was already shown for this hook in this session.
 */
function hasShownReminder(sessionId: string, hookName: string): boolean {
  const markerDir = getSessionMarkerDir();
  const safeSessionId = basename(sessionId);
  const markerFile = join(markerDir, `block-reminder-${safeSessionId}-${hookName}.marker`);
  return existsSync(markerFile);
}

/**
 * Mark that reminder was shown for this hook in this session.
 */
function markReminderShown(sessionId: string, hookName: string): void {
  const markerDir = getSessionMarkerDir();
  const safeSessionId = basename(sessionId);
  try {
    mkdirSync(markerDir, { recursive: true });
    const markerFile = join(markerDir, `block-reminder-${safeSessionId}-${hookName}.marker`);
    writeFileSync(markerFile, "1");
  } catch {
    // Best effort - don't fail if marker can't be written
  }
}

/**
 * Build the improvement reminder message.
 */
function buildReminderMessage(hookName: string, blockCount: number): string {
  const lines = [
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    `💡 フック改善リマインダー: ${hookName}`,
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "",
    `このセッションで \`${hookName}\` が${blockCount}回連続でブロックしています。`,
    "",
    "**検討すべき改善策:**",
    "",
    "1. **SKIP環境変数のサポート追加**",
    `   - \`SKIP_${hookName.toUpperCase().replace(/-/g, "_")}=1\` でバイパス可能に`,
    "",
    "2. **拒否メッセージの改善**",
    "   - 具体的な解決策を提示",
    "   - 何をすべきか明確に説明",
    "",
    "3. **誤検知パターンの修正**",
    "   - 正当なケースをブロックしていないか確認",
    "   - 検出ロジックの精度を改善",
    "",
    "詳細は `hooks-reference` Skill を参照してください。",
  ];
  return lines.join("\n");
}

async function main(): Promise<void> {
  try {
    const hookInput = await parseHookInput();
    const ctx = createHookContext(hookInput);

    // Only process Bash tool (where most blocks occur)
    const toolName = hookInput.tool_name ?? "";
    if (toolName !== "Bash") {
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Get session ID
    const sessionId = ctx.sessionId;
    if (!sessionId || sessionId.startsWith("ppid-")) {
      await logHookExecution(HOOK_NAME, "skip", "No valid session ID", undefined, {
        sessionId: sessionId ?? undefined,
      });
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Get consecutive block counts
    const consecutiveBlocks = await getConsecutiveBlocks(sessionId);

    // Find hooks that exceeded threshold and haven't been reminded yet
    const hooksToRemind: Array<[string, number]> = [];
    for (const [hook, count] of consecutiveBlocks) {
      if (count >= CONSECUTIVE_BLOCK_THRESHOLD) {
        if (!hasShownReminder(sessionId, hook)) {
          hooksToRemind.push([hook, count]);
        }
      }
    }

    if (hooksToRemind.length === 0) {
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Build reminder message for the first hook that needs it
    const [hookName, blockCount] = hooksToRemind[0];
    const message = buildReminderMessage(hookName, blockCount);

    // Mark reminder as shown
    markReminderShown(sessionId, hookName);

    // Log the reminder
    await logHookExecution(
      HOOK_NAME,
      "remind",
      `Showing improvement reminder for ${hookName} (${blockCount} consecutive blocks)`,
      { target_hook: hookName, block_count: blockCount },
      { sessionId },
    );

    // Return with systemMessage
    console.log(JSON.stringify({ continue: true, systemMessage: message }));
  } catch {
    // フック実行の失敗でClaude Codeをブロックしない
    console.log(JSON.stringify({ continue: true }));
  }
}

if (import.meta.main) {
  main();
}
