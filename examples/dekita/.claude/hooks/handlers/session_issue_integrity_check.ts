#!/usr/bin/env bun
/**
 * セッション別Issue追跡データの整合性を検証。
 *
 * Why:
 *   issue-creation-trackerで記録したIssueが正しくファイルに保存されているか
 *   検証する必要がある。データ損失を早期に検出し、警告する。
 *
 * What:
 *   - セッション開始時（SessionStart）に発火
 *   - 過去5セッションのログとファイルを照合
 *   - 実行ログに記録されたIssueがファイルにも存在するか確認
 *   - 不一致があればsystemMessageで警告
 *
 * State:
 *   - reads: .claude/logs/execution/hook-execution-*.jsonl
 *   - reads: .claude/logs/flow/session-created-issues-*.json
 *   - reads: .claude/logs/flow/state-*.json
 *
 * Remarks:
 *   - 非ブロック型（警告のみ）
 *   - Issue #2003の修正適用前のデータで不整合が発生する可能性
 *   - Python版: session_issue_integrity_check.py
 *
 * Changelog:
 *   - silenvx/dekita#2004: フック追加（整合性検証）
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { EXECUTION_LOG_DIR, FLOW_LOG_DIR } from "../lib/common";
import { formatError } from "../lib/format_error";
import { logHookExecution, readSessionLogEntries } from "../lib/logging";
import { type HookContext, createContext, getSessionId, parseHookInput } from "../lib/session";

const HOOK_NAME = "session-issue-integrity-check";

// Pattern to extract issue number from reason field
// e.g., "Recorded P2 issue #1971 - implement after current task"
export const ISSUE_PATTERN = /Recorded (?:P\d+ )?issue #(\d+)/;

/**
 * Get the execution log directory.
 */
function getLogDir(): string {
  // EXECUTION_LOG_DIR is already an absolute path from lib/common
  return EXECUTION_LOG_DIR;
}

/**
 * Get the flow log directory.
 */
function getFlowDir(): string {
  // FLOW_LOG_DIR is already an absolute path from lib/common
  return FLOW_LOG_DIR;
}

/**
 * Extract issue numbers recorded for a session from execution log.
 */
async function getIssuesFromExecutionLog(sessionId: string): Promise<Set<number>> {
  const issues = new Set<number>();

  // Read from session-specific log file
  const entries = await readSessionLogEntries(getLogDir(), "hook-execution", sessionId);

  for (const entry of entries) {
    // Check if this is an issue-creation-tracker entry
    if (entry.hook === "issue-creation-tracker" && entry.decision === "approve") {
      const reason = (entry.reason as string) || "";
      const match = reason.match(ISSUE_PATTERN);
      if (match) {
        issues.add(Number.parseInt(match[1], 10));
      }
    }
  }

  return issues;
}

/**
 * Load issues from session-specific file.
 */
function getIssuesFromSessionFile(sessionId: string): Set<number> {
  const issuesFile = join(getFlowDir(), `session-created-issues-${sessionId}.json`);

  if (!existsSync(issuesFile)) {
    return new Set();
  }

  try {
    const content = readFileSync(issuesFile, "utf-8");
    const data = JSON.parse(content);
    if (typeof data === "object" && data !== null && Array.isArray(data.issues)) {
      return new Set(data.issues.filter((n: unknown) => typeof n === "number"));
    }
    return new Set();
  } catch (error) {
    console.error(
      `[${HOOK_NAME}] Warning: Failed to read session issues file ${issuesFile}: ${formatError(error)}`,
    );
    return new Set();
  }
}

/**
 * Get recent session IDs from state files.
 */
function getRecentSessionIds(limit = 5): string[] {
  const flowDir = getFlowDir();
  if (!existsSync(flowDir)) {
    return [];
  }

  // Find state files and sort by modification time
  // Handle TOCTOU race condition where files may be deleted during iteration
  const stateFilesWithMtime: Array<{ mtime: number; name: string }> = [];

  try {
    const files = readdirSync(flowDir);
    for (const file of files) {
      if (!file.startsWith("state-") || !file.endsWith(".json")) {
        continue;
      }
      const filePath = join(flowDir, file);
      try {
        const fileStat = statSync(filePath);
        stateFilesWithMtime.push({
          mtime: fileStat.mtimeMs,
          name: file,
        });
      } catch {
        // Error ignored - fail-open pattern
      }
    }
  } catch {
    return [];
  }

  // Sort by mtime descending (most recent first)
  stateFilesWithMtime.sort((a, b) => b.mtime - a.mtime);

  const sessionIds: string[] = [];
  for (const { name } of stateFilesWithMtime.slice(0, limit)) {
    // Extract session ID from filename: state-{session_id}.json
    const stem = name.replace(/\.json$/, ""); // state-{session_id}
    if (stem.startsWith("state-")) {
      sessionIds.push(stem.slice(6)); // Remove "state-" prefix
    }
  }

  return sessionIds;
}

/**
 * Verify integrity of recent sessions' issue tracking.
 */
async function verifyIntegrity(ctx: HookContext): Promise<string[]> {
  const warnings: string[] = [];
  const currentSessionId = getSessionId(ctx);

  // Check recent sessions (excluding current)
  for (const sessionId of getRecentSessionIds(5)) {
    if (sessionId === currentSessionId) {
      continue;
    }

    // Get issues from both sources
    const logIssues = await getIssuesFromExecutionLog(sessionId);
    const fileIssues = getIssuesFromSessionFile(sessionId);

    // Check for missing issues (logged but not in file)
    const missing = new Set([...logIssues].filter((x) => !fileIssues.has(x)));
    if (missing.size > 0) {
      const sortedMissing = Array.from(missing).sort((a, b) => a - b);
      warnings.push(
        `Session ${sessionId.slice(0, 8)}...: Issues logged but missing from file: ${JSON.stringify(sortedMissing)}`,
      );
    }

    // Check for extra issues (in file but not logged) - less critical
    // This is informational, not a warning
  }

  return warnings;
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };

  try {
    const inputData = await parseHookInput();
    const ctx = createContext(inputData);
    const hookType = inputData.hook_type || "";

    // Only run on SessionStart
    if (hookType !== "SessionStart") {
      console.log(JSON.stringify(result));
      return;
    }

    // Verify integrity of recent sessions
    const warnings = await verifyIntegrity(ctx);

    if (warnings.length > 0) {
      const warningText = warnings.map((w) => `  - ${w}`).join("\n");
      const systemMessage = `⚠️ Issue追跡データの整合性問題を検出しました:\n\n${warningText}\n\nIssue #2003 の修正が適用される前のデータかもしれません。`;
      result.systemMessage = systemMessage;
      await logHookExecution(
        HOOK_NAME,
        "warn",
        `Integrity issues found: ${warnings.length} sessions affected`,
      );
    } else {
      await logHookExecution(HOOK_NAME, "approve", "No integrity issues found");
    }
  } catch (error) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

// Only run main when executed directly, not when imported
if (import.meta.main) {
  main();
}
