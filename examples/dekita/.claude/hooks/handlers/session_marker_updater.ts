#!/usr/bin/env bun
/**
 * セッション開始時にworktree内のセッションマーカーを更新。
 *
 * Why:
 *   既存worktree内でセッションを開始した場合、マーカーが古いセッションIDのまま
 *   だとlocked-worktree-guardの自己セッションバイパスが機能しない。
 *   現在のセッションIDで更新する必要がある。
 *
 * What:
 *   - セッション開始時（SessionStart）に発火
 *   - CWDがworktree内かどうか確認
 *   - .claude-sessionファイルを現在のセッションIDで上書き
 *
 * State:
 *   - writes: .worktrees/{name}/.claude-session
 *
 * Remarks:
 *   - 非ブロック型（情報書き込みのみ）
 *   - worktree-creation-markerは新規作成時、本フックは既存worktreeでのセッション開始時
 *   - session-marker-refreshと連携してマーカーを維持
 *   - Python版: session_marker_updater.py
 *
 * Changelog:
 *   - silenvx/dekita#1431: フック追加（既存worktreeでのセッション開始対策）
 *   - silenvx/dekita#2545: HookContextパターン移行
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { SESSION_MARKER_FILE } from "../lib/constants";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "session-marker-updater";

/**
 * Get the worktree root directory if CWD is inside a worktree.
 */
export function getWorktreeRoot(cwd: string): string | null {
  // Check if .worktrees/ is in the path
  const match = cwd.match(/(.*?[/\\]\.worktrees[/\\][^/\\]+)/);
  if (match) {
    return match[1];
  }
  return null;
}

/**
 * Write current session ID to worktree marker file.
 */
function writeSessionMarker(worktreePath: string, sessionId: string): boolean {
  try {
    const markerPath = join(worktreePath, SESSION_MARKER_FILE);
    writeFileSync(markerPath, sessionId);
    return true;
  } catch {
    return false;
  }
}

async function main(): Promise<void> {
  const result: { continue: boolean } = { continue: true };

  try {
    const inputData = await parseHookInput();
    const sessionId = inputData.session_id ?? "";
    const cwd = process.cwd();
    const worktreeRoot = getWorktreeRoot(cwd);

    if (worktreeRoot === null) {
      // Not in a worktree, nothing to do
      await logHookExecution(HOOK_NAME, "success", "Not in worktree");
      console.log(JSON.stringify(result));
      return;
    }

    // Update session marker
    if (sessionId && writeSessionMarker(worktreeRoot, sessionId)) {
      const worktreeName = worktreeRoot.split(/[/\\]/).pop() ?? "";
      await logHookExecution(
        HOOK_NAME,
        "success",
        `Updated marker in ${worktreeName} with session ${sessionId.slice(0, 8)}...`,
      );
    } else {
      const worktreeName = worktreeRoot.split(/[/\\]/).pop() ?? "";
      await logHookExecution(HOOK_NAME, "warning", `Failed to write marker in ${worktreeName}`);
    }
  } catch (_error) {
    await logHookExecution(
      HOOK_NAME,
      "error",
      "An unexpected error occurred while updating the session marker.",
    );
  }

  // Don't block session start
  console.log(JSON.stringify(result));
}

// Only run main when executed directly, not when imported
if (import.meta.main) {
  main();
}
