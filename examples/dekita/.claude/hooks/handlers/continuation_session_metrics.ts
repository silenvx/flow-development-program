#!/usr/bin/env bun
/**
 * 継続セッションを検出し、前セッションのメトリクス記録と開発フローリマインダーを表示する。
 *
 * Why:
 *   コンテキスト継続（context resumption）時はStop hookが発火しないため、
 *   前セッションのメトリクスが失われる。また開発フローの意識がリセットされ
 *   手順スキップによる連続ブロックが発生する。
 *
 * What:
 *   - handoff-state.jsonの更新時刻で継続セッションを判定
 *   - 未記録の前セッションメトリクスを収集・記録
 *   - 開発フローチェックリストを表示
 *
 * State:
 *   - reads: .claude/state/handoff-state.json
 *   - reads: .claude/logs/metrics/session-metrics.log
 *   - reads: .claude/logs/execution/hook-execution-{session}.jsonl
 *   - writes: .claude/logs/metrics/session-metrics.log
 *
 * Remarks:
 *   - 情報注入型フック（ブロックしない、systemMessageで情報表示）
 *   - SessionStartで発火
 *   - collect_session_metrics.pyスクリプトを呼び出してメトリクス収集
 *   - 継続判定の時間窓は5分（CONTINUATION_WINDOW_MINUTES）
 *   - 1回の継続で最大3セッション分のメトリクスを収集
 *   - Python版: continuation_session_metrics.py
 *
 * Changelog:
 *   - silenvx/dekita#1433: 継続セッションメトリクス記録
 *   - silenvx/dekita#2006: 開発フローリマインダー追加
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
} from "node:fs";
import { basename, join } from "node:path";
import { EXECUTION_LOG_DIR, METRICS_LOG_DIR, getProjectDir } from "../lib/common";
import { TIMEOUT_HEAVY } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution, readAllSessionLogEntries } from "../lib/logging";
import { createContext, getSessionId, parseHookInput } from "../lib/session";

const HOOK_NAME = "continuation-session-metrics";

// セッション継続判定の時間窓（分）
// Claude Codeのcontext resumptionは通常5分以内に発生する
const CONTINUATION_WINDOW_MINUTES = 5;

// 1回の継続セッションで収集する最大セッション数
// メトリクス収集は重い処理のため、SessionStart時の遅延を抑えるために制限
const MAX_SESSIONS_TO_COLLECT = 3;

export interface HandoffSummary {
  previous_work_status?: string;
  previous_next_action?: string;
  previous_block_count?: number;
  previous_block_reasons?: string[];
  pending_tasks_count?: number;
  open_prs_count?: number;
}

/**
 * Get scripts directory.
 */
function getScriptsDir(): string {
  return join(getProjectDir(), ".claude", "scripts");
}

/**
 * Get execution log directory.
 * EXECUTION_LOG_DIR is already an absolute, worktree-aware path from lib/common.
 */
function getExecutionLogDir(): string {
  return EXECUTION_LOG_DIR;
}

/**
 * Get metrics log directory.
 * METRICS_LOG_DIR is already an absolute, worktree-aware path from lib/common.
 */
function getMetricsLogDir(): string {
  return METRICS_LOG_DIR;
}

/**
 * Get session metrics log file.
 */
function getSessionMetricsLog(): string {
  return join(getMetricsLogDir(), "session-metrics.log");
}

/**
 * Check if this is a continuation session.
 */
function isContinuationSession(): boolean {
  try {
    const handoffState = join(getProjectDir(), ".claude", "state", "handoff-state.json");
    if (existsSync(handoffState)) {
      const stat = statSync(handoffState);
      const mtime = stat.mtimeMs;
      const now = Date.now();
      const ageMinutes = (now - mtime) / 1000 / 60;
      if (ageMinutes < CONTINUATION_WINDOW_MINUTES) {
        return true;
      }
    }
  } catch {
    // ファイルアクセスエラーは無視（継続セッションではないと判断）
  }
  return false;
}

/**
 * Get handoff summary from handoff file.
 */
function getHandoffSummary(sessionId?: string | null): HandoffSummary {
  const handoffDir = join(getProjectDir(), ".claude", "handoff");
  if (!existsSync(handoffDir)) {
    return {};
  }

  try {
    let handoffFile: string | null = null;

    // セッションID指定時は対応するファイルを優先
    if (sessionId) {
      const safeSessionId = basename(sessionId);
      const specificFile = join(handoffDir, `${safeSessionId}.json`);
      if (existsSync(specificFile)) {
        handoffFile = specificFile;
      }
    }

    // フォールバック: 最新のハンドオフファイルを取得
    if (!handoffFile) {
      const files = readdirSync(handoffDir)
        .filter((f) => f.endsWith(".json"))
        .map((f) => ({
          path: join(handoffDir, f),
          mtime: statSync(join(handoffDir, f)).mtimeMs,
        }))
        .sort((a, b) => b.mtime - a.mtime);

      if (files.length === 0) {
        return {};
      }
      handoffFile = files[0].path;
    }

    const content = readFileSync(handoffFile, "utf-8");
    const handoffData = JSON.parse(content);

    // サマリー情報を抽出（undefined値は除外）
    const sessionSummary = handoffData.session_summary || {};
    const result: HandoffSummary = {};

    if (handoffData.work_status !== undefined) {
      result.previous_work_status = handoffData.work_status;
    }
    if (handoffData.next_action !== undefined) {
      result.previous_next_action = handoffData.next_action;
    }
    if (sessionSummary.blocks !== undefined) {
      result.previous_block_count = sessionSummary.blocks;
    }
    if (sessionSummary.block_reasons !== undefined) {
      result.previous_block_reasons = sessionSummary.block_reasons.slice(0, 3);
    }
    if (handoffData.pending_tasks !== undefined) {
      result.pending_tasks_count = handoffData.pending_tasks.length;
    }
    if (handoffData.open_prs !== undefined) {
      result.open_prs_count = handoffData.open_prs.length;
    }

    return result;
  } catch {
    return {};
  }
}

/**
 * Get recorded session IDs from session-metrics.log.
 */
function getRecordedSessionIds(): Set<string> {
  const recorded = new Set<string>();
  const logFile = getSessionMetricsLog();

  if (!existsSync(logFile)) {
    return recorded;
  }

  try {
    const content = readFileSync(logFile, "utf-8");
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      try {
        const entry = JSON.parse(trimmed);
        // 継続マーカーはメトリクスではないので除外
        if (entry.type === "session_continuation") {
          continue;
        }
        if (entry.session_id) {
          recorded.add(entry.session_id);
        }
      } catch {
        // 無効なJSON行、スキップ
      }
    }
  } catch {
    // ログファイル読み込みエラーは無視
  }

  return recorded;
}

/**
 * Get last recorded session ID from session-metrics.log.
 */
function getLastRecordedSessionId(): string | null {
  const logFile = getSessionMetricsLog();

  if (!existsSync(logFile)) {
    return null;
  }

  try {
    let lastMetricsSid: string | null = null;
    const content = readFileSync(logFile, "utf-8");

    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      try {
        const entry = JSON.parse(trimmed);
        // 継続マーカーはメトリクスではないのでスキップ
        if (entry.type === "session_continuation") {
          continue;
        }
        if (entry.session_id) {
          lastMetricsSid = entry.session_id;
        }
      } catch {
        // 無効なJSON行、スキップ
      }
    }
    return lastMetricsSid;
  } catch {
    return null;
  }
}

/**
 * Get session IDs from hook log files.
 */
async function getSessionIdsFromHookLog(hours = 24): Promise<string[]> {
  // Read from all session-specific log files
  const entries = await readAllSessionLogEntries(getExecutionLogDir(), "hook-execution");

  const sessionLastSeen = new Map<string, number>();
  const cutoff = Date.now() - hours * 3600 * 1000;

  for (const entry of entries) {
    try {
      const timestamp = entry.timestamp as string;
      if (!timestamp) continue;

      const ts = new Date(timestamp).getTime();
      if (ts >= cutoff) {
        const sid = entry.session_id as string;
        if (sid) {
          // 最新のタイムスタンプを記録
          if (!sessionLastSeen.has(sid) || ts > sessionLastSeen.get(sid)!) {
            sessionLastSeen.set(sid, ts);
          }
        }
      }
    } catch {
      // 無効なエントリ、スキップ
    }
  }

  // 最新順にソートして返す
  return Array.from(sessionLastSeen.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([sid]) => sid);
}

/**
 * Collect metrics for a session.
 */
function collectMetricsForSession(sessionId: string): boolean {
  const collectScript = join(getScriptsDir(), "collect_session_metrics.py");
  if (!existsSync(collectScript)) {
    return false;
  }

  try {
    execSync(`python3 "${collectScript}" --session-id "${sessionId}"`, {
      encoding: "utf-8",
      timeout: TIMEOUT_HEAVY * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * Record continuation marker to metrics log.
 */
function recordContinuationMarker(
  currentSessionId: string,
  previousSessionId: string | null,
): void {
  const metricsLogDir = getMetricsLogDir();
  mkdirSync(metricsLogDir, { recursive: true });

  const marker = {
    timestamp: new Date().toISOString(),
    session_id: currentSessionId,
    type: "session_continuation",
    previous_session_id: previousSessionId,
  };

  try {
    appendFileSync(getSessionMetricsLog(), `${JSON.stringify(marker)}\n`, "utf-8");
  } catch {
    // ファイル書き込みエラーは無視
  }
}

/**
 * Build development flow reminder message.
 */
export function buildDevelopmentFlowReminder(handoffSummary: HandoffSummary): string {
  const workStatus = handoffSummary.previous_work_status || "不明";
  const nextAction = handoffSummary.previous_next_action || "";
  const pendingTasks = handoffSummary.pending_tasks_count || 0;
  const openPrs = handoffSummary.open_prs_count || 0;

  const lines = [
    "📋 **セッション継続 - 開発フローチェックリスト**",
    "",
    `前セッションの状態: ${workStatus}`,
  ];

  if (nextAction) {
    lines.push(`次のアクション: ${nextAction}`);
  }

  if (pendingTasks > 0 || openPrs > 0) {
    lines.push("");
    if (pendingTasks > 0) {
      lines.push(`- 保留タスク: ${pendingTasks}件`);
    }
    if (openPrs > 0) {
      lines.push(`- オープンPR: ${openPrs}件`);
    }
  }

  lines.push(
    "",
    "**作業開始前に確認**:",
    "- [ ] Issue作成前に調査・探索を実施したか",
    "- [ ] Worktree作成前にプランを作成したか",
    "- [ ] Push前にCodexレビューを実施したか",
    "",
    "💡 各ステップのスキップは個別フックがブロックします。",
  );

  return lines.join("\n");
}

async function main(): Promise<void> {
  const result: { continue: boolean; message?: string } = { continue: true };

  try {
    const inputData = await parseHookInput();
    const ctx = createContext(inputData);
    const currentSessionId = getSessionId(ctx);
    const isContinuation = isContinuationSession();

    if (!isContinuation) {
      // 通常のセッション開始 - 何もしない
      await logHookExecution(HOOK_NAME, "approve", "Normal session start", {
        is_continuation: false,
      });
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // 継続セッション検出
    const recordedSessions = getRecordedSessionIds();
    const recentSessions = await getSessionIdsFromHookLog(24);

    // 未記録のセッションを特定（現在のセッションと記録済みセッションを除外）
    const unrecordedSessions: string[] = [];
    for (const sid of recentSessions) {
      if (sid !== currentSessionId && !recordedSessions.has(sid)) {
        unrecordedSessions.push(sid);
      }
    }

    // 未記録セッションのメトリクスを収集
    let recordedCount = 0;
    const collectedSessions: string[] = [];
    for (const sid of unrecordedSessions.slice(0, MAX_SESSIONS_TO_COLLECT)) {
      if (collectMetricsForSession(sid)) {
        recordedCount++;
        collectedSessions.push(sid);
      }
    }

    // 継続マーカーを記録
    let previousSessionId: string | null;
    if (collectedSessions.length > 0) {
      // 収集したセッションのうち最初のもの（= 最新）を前セッションとする
      previousSessionId = collectedSessions[0];
    } else {
      // 収集がなければ既存の最新記録済みセッションを使用
      previousSessionId = getLastRecordedSessionId();
    }

    recordContinuationMarker(currentSessionId || "", previousSessionId);

    // ハンドオフサマリーを取得してログに記録
    const handoffSummary = getHandoffSummary(previousSessionId);

    const logDetails: Record<string, unknown> = {
      is_continuation: true,
      previous_session_id: previousSessionId,
      unrecorded_sessions: unrecordedSessions.length,
      recorded_count: recordedCount,
    };

    // ハンドオフサマリーがあれば追加
    if (Object.keys(handoffSummary).length > 0) {
      logDetails.handoff_summary = handoffSummary;
    }

    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Continuation session detected, recorded ${recordedCount} previous sessions`,
      logDetails,
    );

    // 継続セッション時に開発フローリマインダーを表示
    const reminderMessage = buildDevelopmentFlowReminder(handoffSummary);
    result.message = reminderMessage;
  } catch (error) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

// Only run main when executed directly, not when imported
if (import.meta.main) {
  main();
}
