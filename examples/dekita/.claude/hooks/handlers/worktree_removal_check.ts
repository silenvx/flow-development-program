#!/usr/bin/env bun
/**
 * worktree削除前にアクティブな作業やcwd衝突を検出。
 *
 * Why:
 *   別セッション作業中や、cwdが削除対象内にある状態でworktreeを削除すると、
 *   セッション破損（ENOENT）や作業消失が発生する。削除前に検出してブロックする。
 *
 * What:
 *   - git worktree remove実行前（PreToolUse:Bash）に発火
 *   - コマンドからworktreeパスを抽出
 *   - 別セッションのマーカーファイルを確認（30分以内なら作業中）
 *   - マージ済みPRがあればcwd/作業チェックをスキップ
 *   - cwdが削除対象内ならブロック
 *   - 未コミット変更・最近のコミット・stashがあれば警告
 *
 * State:
 *   - reads: .worktrees/<name>/.claude-session
 *
 * Remarks:
 *   - ブロック型フック（危険な削除はブロック）
 *   - 別セッションチェックは--forceでもバイパス不可
 *   - マージ済みPRならcwdチェックをスキップ（Issue #1809）
 *   - SKIP_WORKTREE_CHECK=1で全チェックをバイパス可能
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#589: cwdチェック追加
 *   - silenvx/dekita#840: マージ済みPRチェック追加
 *   - silenvx/dekita#914: gh pr viewに変更（削除済みブランチ対応）
 *   - silenvx/dekita#990: SKIP_WORKTREE_CHECK追加
 *   - silenvx/dekita#994: cdパターンをcwdチェックから除外
 *   - silenvx/dekita#1172: hook_cwd対応
 *   - silenvx/dekita#1452: --force位置対応
 *   - silenvx/dekita#1471: パス抽出パターン改善
 *   - silenvx/dekita#1563: 別セッション検出追加
 *   - silenvx/dekita#1604: subshell/backtick除外
 *   - silenvx/dekita#1606: fail-openログ追加
 *   - silenvx/dekita#1809: マージ済みPRでcwdチェックスキップ
 *   - silenvx/dekita#1863: JSONマーカー対応
 *   - silenvx/dekita#3161: TypeScript移行
 *   - silenvx/dekita#3518: Python版から完全移行（export追加、import.meta.main、テスト追加）
 *   - silenvx/dekita#3521: realpathSync使用でシンボリックリンク対応
 */

import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { SESSION_MARKER_FILE, TIMEOUT_MEDIUM } from "../lib/constants";
import { extractGitCOption, getEffectiveCwd } from "../lib/cwd";
import { formatError } from "../lib/format_error";
import { checkRecentCommits, checkUncommittedChanges } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";
import { isSkipEnvEnabled, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "worktree-removal-check";

// 他セッションがアクティブと判断する閾値（分）
// Issue #1563: 30分以内に更新されたセッションマーカーがあれば、別セッションが作業中と判断
const OTHER_SESSION_ACTIVE_THRESHOLD_MINUTES = 30;

/**
 * Print continue result and log skip reason.
 * Issue #3263: Made async to properly await logHookExecution.
 */
async function printContinueAndLogSkip(reason: string, sessionId?: string | null): Promise<void> {
  await logHookExecution(HOOK_NAME, "approve", reason, undefined, {
    sessionId: sessionId ?? undefined,
  });
  console.log(JSON.stringify({ continue: true }));
}

/**
 * Resolve worktree path from command argument.
 *
 * Handles both:
 * - Relative paths like ".worktrees/issue-123" or "." (resolved from cwd)
 * - Absolute paths like "/path/to/.worktrees/issue-123"
 */
export function resolveWorktreePath(worktreeArg: string, cwd: string): string | null {
  const worktreePath = isAbsolute(worktreeArg) ? worktreeArg : resolve(cwd, worktreeArg);

  try {
    if (existsSync(worktreePath)) {
      return resolve(worktreePath);
    }
  } catch {
    // Path doesn't exist
  }
  return null;
}

/**
 * Check if another session is actively working in the worktree.
 *
 * Issue #1563: Detect when another session has the worktree as its cwd.
 *
 * @returns [hasOtherSession, otherSessionId, minutesAgo]
 */
function checkOtherSessionActive(
  worktreePath: string,
  currentSessionId: string | null | undefined,
): [boolean, string | null, number | null] {
  const markerPath = resolve(worktreePath, SESSION_MARKER_FILE);

  if (!existsSync(markerPath)) {
    return [false, null, null];
  }

  try {
    // Check marker file modification time
    const stat = statSync(markerPath);
    const mtime = stat.mtimeMs;
    const now = Date.now();
    const ageMinutes = (now - mtime) / 1000 / 60;

    // If marker is too old, consider it stale
    if (ageMinutes > OTHER_SESSION_ACTIVE_THRESHOLD_MINUTES) {
      return [false, null, null];
    }

    // Read session ID from marker
    // Issue #1863: Support both JSON format (new) and plain text (old)
    const markerContent = readFileSync(markerPath, "utf-8").trim();
    if (!markerContent) {
      return [false, null, null];
    }

    let markerSessionId: string;
    // Try to parse as JSON first (new format from worktree-creation-marker.py)
    if (markerContent.startsWith("{")) {
      try {
        const markerData = JSON.parse(markerContent);
        markerSessionId = markerData.session_id ?? "";
      } catch {
        // Invalid JSON, treat as plain text
        markerSessionId = markerContent;
      }
    } else {
      // Plain text format (old format from session-marker-updater.py)
      markerSessionId = markerContent;
    }

    if (!markerSessionId) {
      return [false, null, null];
    }

    // If it's our own session, allow cleanup
    if (markerSessionId === currentSessionId) {
      return [false, null, null];
    }

    // Another session is active in this worktree
    return [true, markerSessionId, ageMinutes];
  } catch {
    // Fail-open: if we can't read the marker, don't block
    return [false, null, null];
  }
}

/**
 * Check for stashed changes.
 *
 * @returns [hasStashes, stashCount]
 */
async function checkStashedChanges(worktreePath: string): Promise<[boolean, number]> {
  try {
    const result = await asyncSpawn("git", ["-C", worktreePath, "stash", "list"], {
      timeout: TIMEOUT_MEDIUM * 1000,
    });

    if (!result.success) {
      return [false, 0];
    }

    const lines = result.stdout
      .trim()
      .split("\n")
      .filter((line) => line.length > 0);
    return [lines.length > 0, lines.length];
  } catch {
    // Fail-close: timeout means assume there might be stashes
    return [true, -1]; // -1 indicates timeout
  }
}

// Note: extractGitCOption is imported from ../lib/cwd for robust handling of quoted paths

/**
 * Extract worktree path from git worktree remove command.
 *
 * Handles:
 * - git worktree remove <path>
 * - git worktree remove -f <path>
 * - git worktree remove --force <path>
 * - git -C <repo> worktree remove <path>
 * - git worktree remove "<quoted path>"
 * - git worktree remove '<quoted path>'
 */
export function extractWorktreePathFromCommand(command: string): string | null {
  // Match various forms of git worktree remove command
  // Note: May false-positive on `echo "git worktree remove path"` but this is rare
  // Issue #1471: Exclude quotes from path capture to handle bash -c 'cmd' pattern
  // Issue #1604: Exclude parentheses to handle subshell pattern (cd && git worktree remove)
  // Issue #1608: Exclude backticks to handle `...` command substitution
  // Issue #3161: Support quoted paths to prevent bypass via quotes

  // Pattern matching both quoted and unquoted paths
  // Capture groups: 1=double-quoted, 2=single-quoted, 3=unquoted
  // Issue #3161: Support quoted -C paths to prevent bypass via: git -C "path with spaces" worktree remove
  const pattern =
    /git\s+(?:-C\s+(?:"[^"]+"|'[^']+'|\S+)\s+)?worktree\s+remove\s+(?:-f\s+|--force\s+)?(?:"([^"]+)"|'([^']+)'|([^\s;|&'"()`]+))/;
  const match = command.match(pattern);
  if (!match) {
    return null;
  }
  // Return the first non-undefined capture group (quoted double, quoted single, or unquoted)
  return match[1] ?? match[2] ?? match[3] ?? null;
}

/**
 * Check if command includes force flag (-f or --force).
 *
 * Checks for -f or --force as standalone arguments in either position:
 * - git worktree remove --force path
 * - git worktree remove path --force
 *
 * Issue #1452: Support --force flag after path argument.
 * Issue #3161: Use stripQuotedStrings to avoid matching flags inside quoted paths.
 */
export function hasForceFlag(command: string): boolean {
  // Strip quoted strings to avoid matching flags inside paths
  const stripped = stripQuotedStrings(command);
  // Check for -f or --force as standalone arguments
  return /(?:^|\s)(?:-f|--force)(?:\s|$)/.test(stripped);
}

/**
 * Get the branch name of the worktree.
 */
async function getWorktreeBranch(worktreePath: string): Promise<string | null> {
  try {
    const result = await asyncSpawn(
      "git",
      ["-C", worktreePath, "rev-parse", "--abbrev-ref", "HEAD"],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (result.success) {
      const branch = result.stdout.trim();
      return branch && branch !== "HEAD" ? branch : null;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Check if there's a merged PR for the given branch.
 *
 * Implementation note (Issue #914):
 * `gh pr list --head <branch> --state merged` fails when the remote branch
 * has been deleted after merge. Instead, we use `gh pr view <branch>` which
 * queries by branch name in the PR database and works even after branch deletion.
 *
 * @returns [isMerged, prNumber]
 */
async function checkPrMergedForBranch(
  branchName: string,
  worktreePath: string,
): Promise<[boolean, number | null]> {
  try {
    const result = await asyncSpawn("gh", ["pr", "view", branchName, "--json", "number,mergedAt"], {
      timeout: TIMEOUT_MEDIUM * 1000,
      cwd: worktreePath,
    });

    if (result.success) {
      const data = JSON.parse(result.stdout);
      if (data.mergedAt) {
        // mergedAt is set when PR is merged
        return [true, data.number ?? null];
      }
    }
    return [false, null];
  } catch {
    return [false, null];
  }
}

/**
 * Check if cwd is inside the worktree.
 *
 * Issue #3521: Use realpathSync instead of resolve to handle symlinks correctly.
 * resolve() only normalizes paths but doesn't resolve symlinks, so if a user is
 * in a directory via symlink, the check would fail incorrectly.
 *
 * @returns True if cwd is inside the worktree (should block deletion).
 */
export function checkCwdInsideWorktree(worktreePath: string, hookCwd?: string | null): boolean {
  // Issue #3521: Resolve symlinks with fallback to resolve for non-existent paths
  const resolvePathWithSymlink = (path: string): string =>
    existsSync(path) ? realpathSync(path) : resolve(path);

  try {
    // Issue #1172: Use hook_cwd directly to detect session's actual cwd
    // get_effective_cwd(None, hook_cwd) uses hook_cwd as base, ignoring cd patterns
    const sessionCwd = getEffectiveCwd(undefined, hookCwd);
    const sessionCwdResolved = resolvePathWithSymlink(sessionCwd);
    const targetResolved = resolvePathWithSymlink(worktreePath);

    // Check if cwd is worktree or a subdirectory
    if (sessionCwdResolved === targetResolved) {
      return true;
    }

    // Check if targetResolved is a parent of sessionCwdResolved
    let current = sessionCwdResolved;
    const root = dirname(current) === current ? current : null;
    while (current !== root && current !== dirname(current)) {
      current = dirname(current);
      if (current === targetResolved) {
        return true;
      }
    }

    return false;
  } catch {
    // Fail-closed: If path resolution fails, assume we ARE inside the worktree
    // to prevent accidental deletion.
    return true;
  }
}

export async function main(): Promise<void> {
  let result: Record<string, unknown> = { continue: true };

  try {
    const inputData = await parseHookInput();
    const ctx = createHookContext(inputData);
    const toolInput = inputData.tool_input ?? {};
    const command = typeof toolInput.command === "string" ? toolInput.command : "";
    // Issue #1172: Get cwd from hook input (Claude Code provides session's actual cwd)
    const hookCwd = inputData.cwd ?? undefined;

    // Only check git worktree remove commands
    if (!command.includes("git") || !command.includes("worktree") || !command.includes("remove")) {
      console.log(JSON.stringify(result));
      return;
    }

    // Issue #990: SKIP_WORKTREE_CHECK environment variable support
    // Allows bypassing all checks including cwd check for recovery scenarios
    if (isSkipEnvEnabled(process.env.SKIP_WORKTREE_CHECK)) {
      await logHookExecution(HOOK_NAME, "approve", "SKIP_WORKTREE_CHECK enabled");
      console.log(JSON.stringify(result));
      return;
    }

    // Check for force flag (will bypass active work checks but NOT cwd check)
    const forceFlagPresent = hasForceFlag(command);

    // Extract worktree path from command
    const worktreeArg = extractWorktreePathFromCommand(command);
    if (!worktreeArg) {
      // Issue #1606: Log fail-open for debugging (was cause of Issue #1604 bypass)
      await printContinueAndLogSkip(
        `worktree path抽出失敗 (fail-open): ${command.slice(0, 100)}`,
        ctx.sessionId,
      );
      return;
    }

    // Determine the working directory for path resolution
    // Use getEffectiveCwd() to resolve relative paths like "."
    // Note: We pass command here for path resolution (git -C, relative paths)
    // but NOT to check_cwd_inside_worktree (Issue #994)
    let cwd: string;
    const gitCPath = extractGitCOption(command, true);
    if (gitCPath) {
      let gitCResolved = gitCPath;
      if (!isAbsolute(gitCPath)) {
        // Resolve relative -C path from effective current directory
        // Issue #1172: Pass hook_cwd for proper session cwd detection
        gitCResolved = resolve(getEffectiveCwd(command, hookCwd), gitCPath);
      }
      cwd = resolve(gitCResolved);
      if (!existsSync(cwd)) {
        // -C path doesn't exist - let git handle the error
        // Issue #1606: Log fail-open for debugging
        await printContinueAndLogSkip(`-C path存在しない (fail-open): ${gitCPath}`, ctx.sessionId);
        return;
      }
    } else {
      // Use effective current working directory for relative path resolution
      // Pass command to handle 'cd <path> &&' pattern for path resolution
      // Issue #1172: Pass hook_cwd for proper session cwd detection
      cwd = getEffectiveCwd(command, hookCwd);
    }

    // Resolve worktree path from the determined working directory
    const worktreePath = resolveWorktreePath(worktreeArg, cwd);
    if (!worktreePath) {
      // Path doesn't exist - let git handle the error
      // Issue #1606: Log fail-open for debugging (was related to Issue #1604 bypass)
      await printContinueAndLogSkip(
        `worktree path解決失敗 (fail-open): arg=${worktreeArg}, cwd=${cwd}`,
        ctx.sessionId,
      );
      return;
    }

    // Issue #1809: Check if PR is merged BEFORE cwd check
    // If PR is merged, worktree deletion is safe regardless of cwd location
    // This allows cleanup even when session cwd is inside the worktree
    // Note: We still need to check for other active sessions (Issue #1563)
    const branchName = await getWorktreeBranch(worktreePath);
    let prIsMerged = false;
    let mergedPrNumber: number | null = null;
    if (branchName) {
      [prIsMerged, mergedPrNumber] = await checkPrMergedForBranch(branchName, worktreePath);
    }

    // Issue #1563: Check if another session is actively working in this worktree
    // This check is NOT bypassed by --force OR merged PR because it would break another session
    const [hasOtherSession, otherSid, minutesAgo] = checkOtherSessionActive(
      worktreePath,
      ctx.sessionId,
    );
    if (hasOtherSession) {
      const worktreeName = worktreePath.split("/").pop() ?? "";
      const shortSid = otherSid?.slice(0, 8) ?? "unknown";
      const reason = `🚫 worktree '${worktreeName}' の削除をブロックしました。\n\n別セッション (${shortSid}...) がこのworktree内で作業中です。\n（${Math.round(minutesAgo ?? 0)}分前に更新）\n\n対処方法:\n1. 該当セッションが終了するまで待つ\n2. または環境変数 SKIP_WORKTREE_CHECK=1 を設定して強制削除\n\n⚠️ このチェックは --force やPRマージ済みでもバイパスできません。\n   他セッションのcwdが消失すると、そのセッションが破損します。`;
      result = makeBlockResult(HOOK_NAME, reason);
      await logHookExecution(
        HOOK_NAME,
        "block",
        `他セッション作業中: ${worktreeName} (session ${shortSid})`,
      );
      console.log(JSON.stringify(result));
      return;
    }

    // If PR is merged, skip cwd check and other active work checks
    // (other session check was already done above)
    if (prIsMerged) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `マージ済みPR #${mergedPrNumber} 検出: ${branchName} - cwd/アクティブ作業チェックをスキップ`,
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Critical check: Is cwd inside the worktree being deleted?
    // This check is NOT bypassed by --force because it would break the session
    // Issue #994: Do NOT pass command here - 'cd <path> &&' in a Bash command
    // does NOT change the session's actual cwd (it runs in a subshell).
    // Trusting the cd pattern caused session corruption when worktree was deleted.
    // Issue #1172: Use hook_cwd directly to detect session's actual cwd
    const cwdInsideWorktree = checkCwdInsideWorktree(worktreePath, hookCwd);
    if (cwdInsideWorktree) {
      const worktreeName = worktreePath.split("/").pop() ?? "";
      // Issue #1809: Provide actionable guidance
      const reason = `🚫 worktree '${worktreeName}' の削除をブロックしました。\n\n現在の作業ディレクトリ (cwd) が削除対象のworktree内にあります。\n削除するとセッションの全Bashコマンドが失敗します。\n\n対処方法（いずれか1つを選択）:\n\n【方法1】PRがマージ済みの場合:\n  PRがマージされていれば、このチェックは自動的にスキップされます。\n  まずPRをマージしてから再度削除を試してください。\n\n【方法2】新しいターミナルで手動削除:\n  別のターミナルを開いて以下を実行:\n  git worktree remove ${worktreePath}\n\n【方法3】環境変数でバイパス:\n  SKIP_WORKTREE_CHECK=1 git worktree remove ${worktreePath}\n\n⚠️ このチェックは --force でもバイパスできません。`;
      result = makeBlockResult(HOOK_NAME, reason);
      await logHookExecution(HOOK_NAME, "block", `cwdがworktree内: ${worktreeName}`);
      console.log(JSON.stringify(result));
      return;
    }

    // Skip active work checks if force flag is present
    if (forceFlagPresent) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "force flagあり: アクティブ作業チェックをスキップ",
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Check for signs of active work
    const issues: string[] = [];

    const [hasRecent, recentInfo] = await checkRecentCommits(worktreePath);
    if (hasRecent) {
      issues.push(`最新コミット（1時間以内）: ${recentInfo}`);
    }

    const [hasChanges, changeCount] = await checkUncommittedChanges(worktreePath);
    if (hasChanges) {
      if (changeCount < 0) {
        // タイムアウトの場合
        issues.push("未コミット変更: (確認タイムアウト)");
      } else {
        issues.push(`未コミット変更: ${changeCount}件`);
      }
    }

    const [hasStashes, stashCount] = await checkStashedChanges(worktreePath);
    if (hasStashes) {
      if (stashCount < 0) {
        // タイムアウトの場合
        issues.push("stash: (確認タイムアウト)");
      } else {
        issues.push(`stash: ${stashCount}件`);
      }
    }

    if (issues.length > 0) {
      const worktreeName = worktreePath.split("/").pop() ?? "";
      const issuesText = issues.map((issue) => `  - ${issue}`).join("\n");
      const reason = `⚠️ worktree '${worktreeName}' にアクティブな作業が検出されました:\n${issuesText}\n\n別セッションが作業中の可能性があります。\n削除する場合は --force オプションを使用するか、\n先に作業状態を確認してください。`;
      result = makeBlockResult(HOOK_NAME, reason);
      await logHookExecution(HOOK_NAME, "block", reason);
    } else {
      const worktreeName = worktreePath.split("/").pop() ?? "";
      await logHookExecution(HOOK_NAME, "approve", `worktree削除を許可: ${worktreeName}`);
    }
  } catch (e) {
    // Don't block on errors - log and continue
    await logHookExecution(HOOK_NAME, "error", `フックエラー: ${formatError(e)}`);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({ continue: true }));
  });
}
