#!/usr/bin/env bun
/**
 * worktree内のセッションマーカーのmtimeを定期更新。
 *
 * Why:
 *   長時間セッション（30分以上）でマーカーのmtimeが古くなると、
 *   worktree-removal-checkが「古いセッション」と判断してしまう。
 *   定期的にmtimeを更新してセッション活性を示す。
 *
 * What:
 *   - PostToolUse時に発火
 *   - CWDがworktree内かどうか確認
 *   - マーカーのmtimeが10分以上古ければtouchで更新
 *   - 更新が不要なら何もしない（パフォーマンス最適化）
 *
 * When:
 *   - PostToolUse
 *
 * State:
 *   - writes: .worktrees/*\/.claude-session（mtimeのみ更新）
 *
 * Remarks:
 *   - 非ブロック型（マーカーのtouchのみ）
 *   - session-marker-updaterはSessionStart時、本フックはPostToolUse
 *   - 10分間隔でのみ更新（REFRESH_INTERVAL）
 *   - Python版: session_marker_refresh.py
 *
 * Changelog:
 *   - silenvx/dekita#1572: フック追加（長時間セッション対策）
 *   - silenvx/dekita#2986: TypeScript版に移植
 */

import { existsSync, statSync, utimesSync } from "node:fs";
import { join } from "node:path";
import { SESSION_MARKER_FILE } from "../lib/constants";
import { getEffectiveCwd } from "../lib/cwd";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "session-marker-refresh";

// Refresh interval in seconds (10 minutes)
export const REFRESH_INTERVAL = 600;

/**
 * Extract worktree root from a path.
 *
 * Uses regex to match .worktrees pattern (same as session-marker-updater.py).
 * This handles edge cases like /path/.worktrees.backup/.worktrees/issue-123
 */
export function extractWorktreeRoot(path: string): string | null {
  const match = path.match(/(.*?[/\\]\.worktrees[/\\][^/\\]+)/);
  if (match) {
    return match[1];
  }
  return null;
}

/**
 * Check if marker age exceeds refresh interval.
 */
export function shouldRefreshMarker(markerAgeSecs: number): boolean {
  return markerAgeSecs > REFRESH_INTERVAL;
}

/**
 * Get the worktree root directory if effective CWD is inside a worktree.
 *
 * Uses getEffectiveCwd() to handle cases where the hook process runs from
 * the project root but the session has cd'd into a worktree.
 */
function getWorktreeRoot(): string | null {
  const cwd = getEffectiveCwd();
  return extractWorktreeRoot(cwd);
}

/**
 * Check if marker needs to be refreshed.
 */
function needsRefresh(markerPath: string): boolean {
  if (!existsSync(markerPath)) {
    return false;
  }

  try {
    const stats = statSync(markerPath);
    const mtime = stats.mtimeMs / 1000; // Convert to seconds
    const age = Date.now() / 1000 - mtime;
    return age > REFRESH_INTERVAL;
  } catch {
    return false;
  }
}

/**
 * Refresh the marker file's mtime.
 */
function refreshMarker(markerPath: string): boolean {
  try {
    // Touch the file to update mtime
    const now = new Date();
    utimesSync(markerPath, now, now);
    return true;
  } catch {
    return false;
  }
}

async function main(): Promise<void> {
  const result = { continue: true };
  let sessionId: string | undefined;

  try {
    // Parse hook input for session_id (required for log_hook_execution)
    const hookInput = await parseHookInput();

    sessionId = hookInput.session_id;

    const worktreeRoot = getWorktreeRoot();

    if (worktreeRoot === null) {
      // Not in a worktree, nothing to do
      console.log(JSON.stringify(result));
      return;
    }

    const markerPath = join(worktreeRoot, SESSION_MARKER_FILE);

    if (!existsSync(markerPath)) {
      // No marker file, nothing to do
      console.log(JSON.stringify(result));
      return;
    }

    if (needsRefresh(markerPath)) {
      const worktreeName = worktreeRoot.split("/").pop() ?? worktreeRoot;
      if (refreshMarker(markerPath)) {
        await logHookExecution(
          HOOK_NAME,
          "success",
          `Refreshed marker in ${worktreeName}`,
          undefined,
          { sessionId },
        );
      } else {
        await logHookExecution(
          HOOK_NAME,
          "warning",
          `Failed to refresh marker in ${worktreeName}`,
          undefined,
          { sessionId },
        );
      }
    }

    console.log(JSON.stringify(result));
  } catch (error) {
    await logHookExecution(
      HOOK_NAME,
      "error",
      `Unexpected error: ${formatError(error)}`,
      undefined,
      { sessionId },
    );
    // Don't block on errors
    console.log(JSON.stringify(result));
  }
}

if (import.meta.main) {
  main();
}
