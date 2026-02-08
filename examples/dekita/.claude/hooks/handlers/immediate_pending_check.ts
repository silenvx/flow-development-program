#!/usr/bin/env bun
/**
 * [IMMEDIATE]タグの実行漏れを早期検出する。
 *
 * Why:
 *   PRマージ後に[IMMEDIATE: /reflecting-sessions]が表示されても、ユーザーが次の入力をした場合、
 *   Claudeが別のタスクに移って振り返りを忘れることがある。
 *   Stop hookのみでは検出が遅く、セッション終了時まで気づかない。
 *
 * What:
 *   - UserPromptSubmit時にpending状態ファイルを確認
 *   - 未実行の[IMMEDIATE]アクションがあれば即座にブロック
 *   - 実行済みなら状態ファイルを削除してフローを継続
 *
 * State:
 *   - reads: /tmp/claude-hooks/immediate-pending-{session_id}.json
 *   - deletes: same (on successful verification)
 *
 * Remarks:
 *   - post-merge-reflection-enforcer.pyがpending状態を書き込み
 *   - reflection-completion-check.pyはStop hookで最終チェック
 *   - 本フックはUserPromptSubmitで早期検出
 *   - lib/reflection.tsの共通関数を使用
 *   - Python版: immediate_pending_check.py
 *
 * Changelog:
 *   - silenvx/dekita#2690: 新規作成
 *   - silenvx/dekita#2695: timestampベースのトランスクリプトフィルタリング追加
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { existsSync, readFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { isSafeTranscriptPath } from "../lib/path_validation";
import { checkImmediateActionExecuted } from "../lib/reflection";
import { createContext, getSessionId, isSafeSessionId, parseHookInput } from "../lib/session";

const HOOK_NAME = "immediate-pending-check";

// Session state directory (same as post-merge-reflection-enforcer.py)
const SESSION_DIR = join(tmpdir(), "claude-hooks");

export interface ImmediatePendingState {
  action: string;
  context: string;
  timestamp?: string;
}

/**
 * Get the file path for storing immediate pending action state.
 *
 * @param sessionId - The Claude session ID to scope the file.
 * @returns Path to session-specific immediate pending state file, or null if
 *          session_id is invalid (security: prevents path traversal).
 */
export function getImmediatePendingFile(sessionId: string): string | null {
  // Security: Validate session_id to prevent path traversal attacks
  if (!isSafeSessionId(sessionId)) {
    return null;
  }
  return join(SESSION_DIR, `immediate-pending-${sessionId}.json`);
}

/**
 * Load immediate pending action state.
 *
 * @param sessionId - The Claude session ID.
 * @returns State dictionary if file exists and is valid, null otherwise.
 */
export function loadImmediatePendingState(sessionId: string): ImmediatePendingState | null {
  try {
    const stateFile = getImmediatePendingFile(sessionId);
    if (stateFile === null) {
      return null; // Invalid session_id
    }
    if (existsSync(stateFile)) {
      const content = readFileSync(stateFile, "utf-8");
      return JSON.parse(content) as ImmediatePendingState;
    }
  } catch {
    // Best effort - corrupted state is ignored
  }
  return null;
}

/**
 * Delete immediate pending action state file.
 *
 * Called when the action has been verified as executed.
 *
 * @param sessionId - The Claude session ID.
 */
export function deleteImmediatePendingState(sessionId: string): void {
  try {
    const stateFile = getImmediatePendingFile(sessionId);
    if (stateFile === null) {
      return; // Invalid session_id
    }
    if (existsSync(stateFile)) {
      unlinkSync(stateFile);
    }
  } catch {
    // Best effort - deletion may fail
  }
}

/**
 * Read the transcript file, optionally filtering by timestamp.
 *
 * Issue #2695: Filter transcript entries by timestamp to avoid false positives
 * from reflection keywords that occurred before the IMMEDIATE action was created.
 *
 * @param transcriptPath - Path to the transcript file from input_data.
 * @param sinceTimestamp - ISO format timestamp. If provided, only entries after this
 *                         timestamp will be included in the result.
 * @returns Transcript content as string, or empty string if unavailable.
 */
export function readTranscript(
  transcriptPath: string | null | undefined,
  sinceTimestamp: string | null | undefined,
): string {
  if (!transcriptPath) {
    return "";
  }

  try {
    if (!isSafeTranscriptPath(transcriptPath)) {
      return "";
    }

    if (!existsSync(transcriptPath)) {
      return "";
    }

    const content = readFileSync(transcriptPath, "utf-8");

    if (!sinceTimestamp) {
      // No filtering, return full content
      return content;
    }

    // Parse the cutoff timestamp
    let cutoffTime: number;
    try {
      cutoffTime = new Date(sinceTimestamp).getTime();
    } catch {
      // Invalid timestamp format, return full content
      return content;
    }

    // Filter JSONL entries by timestamp
    const filteredLines: string[] = [];
    for (const line of content.split("\n")) {
      if (!line.trim()) {
        continue;
      }

      try {
        const entry = JSON.parse(line) as Record<string, unknown>;
        const entryTs =
          (entry.timestamp as string) ||
          ((entry.snapshot as Record<string, unknown>)?.timestamp as string);

        if (entryTs) {
          const entryTime = new Date(entryTs).getTime();
          if (entryTime >= cutoffTime) {
            filteredLines.push(line);
          }
        } else {
          // No timestamp in entry, include it (conservative)
          filteredLines.push(line);
        }
      } catch {
        // Invalid JSON, timestamp format, or timezone mismatch
        // (naive vs aware datetime), include line (conservative)
        filteredLines.push(line);
      }
    }

    return filteredLines.join("\n");
  } catch {
    // Best effort - transcript read failure should not block hook
  }
  return "";
}

async function main(): Promise<void> {
  const result: { continue?: boolean; decision?: string; reason?: string } = {
    continue: true,
  };
  let sessionId: string | null | undefined;

  try {
    const inputData = await parseHookInput();
    const ctx = createContext(inputData);
    sessionId = getSessionId(ctx);

    if (!sessionId) {
      console.log(JSON.stringify(result));
      return;
    }

    // Check for pending immediate action
    const pendingState = loadImmediatePendingState(sessionId);
    if (!pendingState) {
      // No pending action, continue normally
      console.log(JSON.stringify(result));
      return;
    }

    const action = pendingState.action || "";
    const context = pendingState.context || "";
    // Issue #2695: Get timestamp to filter transcript entries
    const sinceTimestamp = pendingState.timestamp;

    // Read transcript and check if action was executed
    // Issue #2695: Only check transcript entries after the pending state was created
    const transcriptPath = inputData.transcript_path as string | undefined;
    const transcriptContent = readTranscript(transcriptPath, sinceTimestamp);

    if (checkImmediateActionExecuted(action, transcriptContent)) {
      // Action was executed, delete pending state and continue
      deleteImmediatePendingState(sessionId);
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Immediate action '${action}' verified as executed`,
        { action, context },
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Action not executed, block
    await logHookExecution(
      HOOK_NAME,
      "block",
      `Immediate action '${action}' not executed`,
      {
        action,
        context,
      },
      { sessionId },
    );

    const message = `⚠️ 未実行の[IMMEDIATE]アクションがあります\n\n**アクション**: \`${action}\`\n**コンテキスト**: ${context}\n\nこのアクションを実行してから次の作業に進んでください。\n\n[IMMEDIATE: ${action}]`;

    console.log(
      JSON.stringify({
        decision: "block",
        reason: message,
      }),
    );
    return;
  } catch (error) {
    await logHookExecution(HOOK_NAME, "error", `Hook error: ${formatError(error)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
