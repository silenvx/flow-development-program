#!/usr/bin/env bun
/**
 * 同一ファイルへの短時間複数編集（手戻り）を追跡。
 *
 * Why:
 *   同一ファイルへの繰り返し編集は、計画不足や試行錯誤を示唆。
 *   警告することで、事前調査・計画の重要性を強調する。
 *
 * What:
 *   - Edit成功後（PostToolUse:Edit）に発火
 *   - 5分以内の同一ファイル編集回数を追跡
 *   - 閾値超過で3段階警告（3回: 軽度、5回: 強め、7回: 停止推奨）
 *   - メトリクスログに記録
 *
 * State:
 *   - reads/writes: /tmp/claude-hooks/edit-history.json
 *   - writes: .claude/logs/metrics/rework-metrics.log
 *
 * Remarks:
 *   - 非ブロック型（警告のみ、systemMessageで通知）
 *   - セッション変更時に履歴リセット
 *   - 計画的な編集（テスト駆動開発等）を促進
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#1335: 高閾値警告追加
 *   - silenvx/dekita#1362: 停止推奨閾値追加
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { METRICS_LOG_DIR } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "rework-tracker";

// Time window for detecting rework (edits within this window count as rework)
export const REWORK_WINDOW_MINUTES = 5;

// Threshold for warning (more than N edits to same file in window)
export const REWORK_THRESHOLD = 3;

// Threshold for strong warning (significantly more edits indicating trial-and-error)
export const REWORK_HIGH_THRESHOLD = 5;

// Threshold for critical warning (stop and review plan)
export const REWORK_CRITICAL_THRESHOLD = 7;

// Tracking file location (session-specific to avoid collisions)
const TRACKING_DIR = join(tmpdir(), "claude-hooks");

/**
 * Get session-specific tracking file path.
 * Using session ID in filename prevents race conditions between concurrent sessions.
 */
function getTrackingFile(sessionId: string): string {
  // Truncate session ID to first 8 chars to keep filename manageable
  const shortId = sessionId.slice(0, 8);
  return join(TRACKING_DIR, `edit-history-${shortId}.json`);
}

// Persistent log for analysis
const projectDir = process.env.CLAUDE_PROJECT_DIR ?? process.cwd();
const REWORK_LOG = join(projectDir, METRICS_LOG_DIR, "rework-metrics.log");

interface EditHistory {
  edits: Record<string, string[]>; // file_path -> timestamps
  session_id: string | null;
}

/**
 * Load existing edit history from session-specific file.
 */
function loadEditHistory(trackingFile: string): EditHistory {
  if (existsSync(trackingFile)) {
    try {
      return JSON.parse(readFileSync(trackingFile, "utf-8"));
    } catch {
      // Best effort - corrupted tracking data is ignored
    }
  }
  return { edits: {}, session_id: null };
}

/**
 * Save edit history to session-specific file.
 *
 * Note: This is not atomic within a single session, but using session-specific
 * files prevents cross-session collisions.
 */
function saveEditHistory(trackingFile: string, data: EditHistory): void {
  try {
    mkdirSync(TRACKING_DIR, { recursive: true });
    writeFileSync(trackingFile, JSON.stringify(data, null, 2));
  } catch {
    // Silently ignore write errors
  }
}

/**
 * Generate warning message based on edit count.
 *
 * Three-tier warning system:
 * - REWORK_THRESHOLD (3): Light warning
 * - REWORK_HIGH_THRESHOLD (5): Strong warning with root cause analysis
 * - REWORK_CRITICAL_THRESHOLD (7): Stop recommendation with plan review
 */
export function generateWarningMessage(
  filePath: string,
  editCount: number,
  windowMinutes: number,
): string | null {
  if (editCount < REWORK_THRESHOLD) {
    return null;
  }

  const fileName = basename(filePath);

  // Critical threshold - stop and review plan
  if (editCount >= REWORK_CRITICAL_THRESHOLD) {
    return `🛑 停止推奨: ${fileName} を${windowMinutes}分以内に${editCount}回編集。\n\nこれは試行錯誤による非効率な作業パターンです。\n一度立ち止まって、以下を実行してください:\n\n1. 作業を一時停止する\n2. 現在のアプローチを振り返る\n3. 必要に応じてプランを見直す\n\n続行する前に、変更の全体設計を明確にしてください。`;
  }

  // High threshold - strong warning with root cause analysis
  if (editCount >= REWORK_HIGH_THRESHOLD) {
    return `⚠️ 高頻度編集検出: ${fileName} を${windowMinutes}分以内に${editCount}回編集。\n\nこのパターンは試行錯誤アプローチを示唆しています。\n以下を確認してください:\n- テストを先に書いていますか？\n- 変更の要件は明確ですか？\n- 設計を見直す必要はありませんか？`;
  }

  // Default: Light warning
  return `📊 手戻り検出: ${fileName} を${windowMinutes}分以内に${editCount}回編集。\n事前の調査・計画で編集回数を減らせるかもしれません。`;
}

/**
 * Log rework event for later analysis.
 */
function logReworkEvent(
  filePath: string,
  editCount: number,
  windowMinutes: number,
  sessionId: string,
): void {
  try {
    const logDir = join(projectDir, METRICS_LOG_DIR);
    mkdirSync(logDir, { recursive: true });

    const entry = {
      timestamp: new Date().toISOString(),
      session_id: sessionId,
      type: "rework_detected",
      file_path: filePath,
      edit_count: editCount,
      window_minutes: windowMinutes,
    };
    appendFileSync(REWORK_LOG, `${JSON.stringify(entry)}\n`);
  } catch {
    // ログ書き込み失敗はサイレントに無視（メトリクスは必須ではない）
  }
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    const hookInput = await parseHookInput();
    const ctx = createHookContext(hookInput);
    sessionId = ctx.sessionId;
    const toolInput = (hookInput.tool_input ?? {}) as Record<string, unknown>;

    // Get the file being edited
    const filePath = toolInput.file_path as string;
    if (!filePath) {
      await logHookExecution(HOOK_NAME, "approve", "no file path", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    const now = new Date();
    const currentSession = ctx.sessionId ?? "unknown";
    const trackingFile = getTrackingFile(currentSession);

    // Load history from session-specific file
    let history = loadEditHistory(trackingFile);

    // Reset if session changed (shouldn't happen with session-specific files, but kept for safety)
    if (history.session_id !== currentSession) {
      history = { edits: {}, session_id: currentSession };
    }

    // Get edit timestamps for this file
    const edits = history.edits[filePath] ?? [];

    // Filter to only edits within the window
    const windowStart = new Date(now.getTime() - REWORK_WINDOW_MINUTES * 60 * 1000);
    const recentEdits = edits.filter((ts) => new Date(ts) > windowStart);

    // Add current edit
    recentEdits.push(now.toISOString());
    history.edits[filePath] = recentEdits;

    // Save updated history
    saveEditHistory(trackingFile, history);

    // Check for rework pattern
    const editCount = recentEdits.length;
    const warningMessage = generateWarningMessage(filePath, editCount, REWORK_WINDOW_MINUTES);
    if (warningMessage) {
      logReworkEvent(filePath, editCount, REWORK_WINDOW_MINUTES, currentSession);
      result.systemMessage = warningMessage;
    }
  } catch (error) {
    // フック実行の失敗でClaude Codeをブロックしない
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", "edit_tracked", undefined, { sessionId });
    console.log(JSON.stringify(result));
    return;
  }

  await logHookExecution(HOOK_NAME, "approve", "edit_tracked", undefined, { sessionId });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
