#!/usr/bin/env bun
/**
 * 別セッションが作業中のworktreeへの誤介入を防止する。
 *
 * Why:
 *   別セッションが作業中のworktreeを編集すると、競合やコンフリクトが発生し、
 *   両セッションの作業が無駄になる。セッションマーカーで所有権を確認し、
 *   別セッションのworktreeへの編集をブロックする。
 *
 * What:
 *   - Edit対象ファイルが.worktrees/配下かチェック
 *   - 該当worktreeの.claude-sessionマーカーを読む
 *   - 現在のセッションIDと比較
 *   - 不一致ならブロック（別セッションが作業中）
 *   - 一致またはマーカーなしなら許可
 *
 * State:
 *   reads: .worktrees/{name}/.claude-session
 *
 * Remarks:
 *   - worktree-creation-marker.pyがマーカー作成、本フックがマーカー検証
 *   - session-worktree-status.pyは警告、本フックはブロック
 *   - Python版: worktree_session_guard.py
 *
 * Changelog:
 *   - silenvx/dekita#1396: フック追加
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { CONTINUATION_HINT, SESSION_MARKER_FILE } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { createContext, getSessionAncestry, getSessionId, parseHookInput } from "../lib/session";

const HOOK_NAME = "worktree-session-guard";

/**
 * Extract worktree directory from file path.
 *
 * @param filePath - Absolute path to a file
 * @returns Path to worktree directory if file is inside .worktrees/, null otherwise
 */
function getWorktreeFromPath(filePath: string): string | null {
  const parts = filePath.split("/");

  for (let i = 0; i < parts.length; i++) {
    if (parts[i] === ".worktrees" && i + 1 < parts.length) {
      // Found .worktrees, next part is the worktree name
      return `/${parts.slice(1, i + 2).join("/")}`;
    }
  }

  return null;
}

/**
 * Get worktree name from path.
 */
function getWorktreeName(worktreePath: string): string {
  const parts = worktreePath.split("/");
  return parts[parts.length - 1] || worktreePath;
}

/**
 * Read session ID from worktree marker file.
 *
 * Expects JSON format:
 * {
 *     "session_id": "...",
 *     "created_at": "2025-12-30T09:30:00+00:00"
 * }
 *
 * @param worktreePath - Path to worktree directory
 * @returns Session ID if marker exists and is valid JSON, null otherwise
 */
function readSessionMarker(worktreePath: string): string | null {
  const markerPath = join(worktreePath, SESSION_MARKER_FILE);
  try {
    if (existsSync(markerPath)) {
      const content = readFileSync(markerPath, "utf-8").trim();
      const data = JSON.parse(content) as { session_id?: string };
      return data.session_id || null;
    }
  } catch {
    // File access errors or invalid JSON are treated as "no marker"
    // to fail-open and not block operations unnecessarily
  }
  return null;
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const ctx = createContext(data);
    const toolName = (data.tool_name as string) || "";
    const toolInput = (data.tool_input as Record<string, unknown>) || {};

    // Only check Edit and Write operations
    if (toolName !== "Edit" && toolName !== "Write") {
      await logHookExecution(HOOK_NAME, "skip", `Not Edit/Write: ${toolName}`, undefined, {
        sessionId,
      });
      console.log(JSON.stringify(makeApproveResult(HOOK_NAME, `Not Edit/Write: ${toolName}`)));
      return;
    }

    const filePath = (toolInput.file_path as string) || "";
    if (!filePath) {
      await logHookExecution(HOOK_NAME, "skip", "No file_path in tool_input", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(makeApproveResult(HOOK_NAME, "No file_path in tool_input")));
      return;
    }

    // Check if file is inside a worktree
    const worktreePath = getWorktreeFromPath(filePath);
    if (!worktreePath) {
      // Not in a worktree, allow
      await logHookExecution(HOOK_NAME, "approve", "File not in worktree", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(makeApproveResult(HOOK_NAME, "File not in worktree")));
      return;
    }

    const worktreeName = getWorktreeName(worktreePath);

    // Read session marker
    const markerSession = readSessionMarker(worktreePath);
    if (!markerSession) {
      // No marker = legacy worktree or new session hasn't written marker yet
      // Allow but log
      const msg = `No session marker in ${worktreeName}`;
      await logHookExecution(HOOK_NAME, "approve", msg, undefined, { sessionId });
      console.log(JSON.stringify(makeApproveResult(HOOK_NAME, msg)));
      return;
    }

    // Get current session ID
    const currentSession = getSessionId(ctx);

    // Compare sessions
    if (markerSession === currentSession) {
      // Same session, allow
      const msg = `Same session for ${worktreeName}`;
      await logHookExecution(HOOK_NAME, "approve", msg, undefined, { sessionId });
      console.log(JSON.stringify(makeApproveResult(HOOK_NAME, msg)));
      return;
    }

    // Issue #2331: Check if marker session is an ancestor (fork-session support)
    // In fork-sessions, child sessions should be able to access worktrees created
    // by their parent sessions. However, sibling sessions should NOT access each
    // other's worktrees. We check if marker_session appears BEFORE current_session
    // in the ancestry list to ensure it's a true ancestor, not a sibling.
    const transcriptPath = data.transcript_path as string | undefined;
    if (transcriptPath) {
      const ancestry = getSessionAncestry(transcriptPath);
      const markerIndex = ancestry.indexOf(markerSession);
      const currentIndex = currentSession ? ancestry.indexOf(currentSession) : -1;

      if (markerIndex !== -1 && currentIndex !== -1) {
        if (markerIndex < currentIndex) {
          // Marker session appears before current session = ancestor
          const msg = `Ancestor session worktree for ${worktreeName}`;
          await logHookExecution(HOOK_NAME, "approve", msg, undefined, { sessionId });
          console.log(JSON.stringify(makeApproveResult(HOOK_NAME, msg)));
          return;
        }
      }
    }

    // Different session - block!
    const markerShort = markerSession.slice(0, 16);
    const currentShort = (currentSession || "").slice(0, 16);

    const reason = `このworktree (${worktreeName}) は別のセッションが作業中です。\n\nマーカーセッション: ${markerShort}...\n現在のセッション: ${currentShort}...\n\n別セッションの作業を引き継がないでください。\nこのIssueをスキップして、次のIssue（worktreeがないもの）に進んでください。\n\n確認コマンド:\n\`\`\`bash\ngit worktree list\n\`\`\`${CONTINUATION_HINT}`;

    await logHookExecution(
      HOOK_NAME,
      "block",
      `Different session for ${worktreeName}: marker=${markerShort}, current=${currentShort}`,
      undefined,
      { sessionId },
    );
    console.log(JSON.stringify(makeBlockResult(HOOK_NAME, reason, ctx)));
  } catch (error) {
    // Fail open - don't block on errors
    const errorMsg = `Hook error: ${formatError(error)}`;
    console.error(`[${HOOK_NAME}] ${errorMsg}`);
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME, errorMsg)));
  }
}

// Only run main when executed directly, not when imported
if (import.meta.main) {
  main();
}

// Export for testing
export { getWorktreeFromPath, getWorktreeName, readSessionMarker };
