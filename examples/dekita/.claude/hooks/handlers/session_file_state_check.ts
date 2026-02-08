#!/usr/bin/env bun
/**
 * セッション再開時ファイル状態検証フック（SessionStart）
 *
 * Why:
 *   セッション再開時にサマリーと実際のファイル状態が乖離していることがある。
 *   サマリーには「編集完了」と記載されているが、実際は未コミットの場合、
 *   サマリーを信頼して次のステップに進むと問題が発生する。
 *
 * What:
 *   - `git status` でuncommitted changesを確認
 *   - セッション再開時（resume/compact）に未コミット変更があれば警告を表示
 *   - 直前のコミット内容を表示して整合性確認を促す
 *
 * State:
 *   - reads: none (git commandのみ)
 *
 * Remarks:
 *   - 非ブロック型（情報表示のみ）
 *   - 責務: セッション再開時にファイル状態を検証
 *   - session-resume-warningは競合警告、こちらはファイル状態検証
 *   - Python版: session_file_state_check.py
 *
 * Changelog:
 *   - silenvx/dekita#2468: フック追加
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { TIMEOUT_LIGHT } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "session-file-state-check";

export interface GitStatus {
  staged: string[];
  unstaged: string[];
  untracked: string[];
}

/**
 * Get uncommitted changes from git status.
 */
function getGitStatus(): GitStatus {
  const result: GitStatus = { staged: [], unstaged: [], untracked: [] };

  try {
    const statusResult = execSync("git status --porcelain -z", {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    // -z option: NUL-separated entries, handles special characters in filenames
    // Format: XY filename\0 (or XY oldname\0newname\0 for renames)
    const entries = statusResult.split("\0");
    let i = 0;

    while (i < entries.length) {
      const entry = entries[i];
      if (!entry) {
        i++;
        continue;
      }

      // Porcelain format: XY filename
      // X = index status, Y = work tree status
      const indexStatus = entry.length > 0 ? entry[0] : " ";
      const worktreeStatus = entry.length > 1 ? entry[1] : " ";
      const filename = entry.length > 3 ? entry.slice(3) : "";

      if (indexStatus === "?") {
        result.untracked.push(filename);
      } else if (indexStatus !== " ") {
        result.staged.push(filename);
      }

      if (worktreeStatus !== " " && worktreeStatus !== "?") {
        result.unstaged.push(filename);
      }

      // Handle renames (R) and copies (C) which have a second filename
      if (indexStatus === "R" || indexStatus === "C") {
        i++; // Skip the next entry (old filename)
      }

      i++;
    }
  } catch {
    // Return empty result on error
  }

  return result;
}

/**
 * Get the last commit message and affected files.
 */
function getLastCommitInfo(): string | null {
  try {
    // Get last commit hash, message, and time
    const logResult = execSync('git log -1 --format="%h %s (%ar)"', {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    }).trim();

    if (!logResult) {
      return null;
    }

    // Get files changed in last commit (-z for NUL-separated output)
    let files: string[] = [];
    try {
      const filesResult = execSync("git diff-tree --no-commit-id --name-only -r -z HEAD", {
        encoding: "utf-8",
        timeout: TIMEOUT_LIGHT * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      });
      files = filesResult
        .split("\0")
        .filter((f) => f)
        .slice(0, 5);
    } catch {
      // Ignore file listing errors
    }

    let result = `  ${logResult}`;
    if (files.length > 0) {
      let filesStr = files.join(", ");
      if (files.length >= 5) {
        filesStr += ", ...";
      }
      result += `\n  変更ファイル: ${filesStr}`;
    }

    return result;
  } catch {
    return null;
  }
}

/**
 * Format the file state warning message.
 */
export function formatFileStateWarning(status: GitStatus, lastCommit: string | null): string {
  const parts: string[] = [
    "⚠️ **ファイル状態の確認が必要です**\n",
    "セッション再開時に未コミットの変更が検出されました。",
    "**サマリーとファイル状態が乖離している可能性があります**。\n",
  ];

  // Show uncommitted changes
  if (status.staged.length > 0) {
    parts.push(`**ステージ済み** (${status.staged.length}件):`);
    for (const f of status.staged.slice(0, 5)) {
      parts.push(`  - ${f}`);
    }
    if (status.staged.length > 5) {
      parts.push(`  ... 他 ${status.staged.length - 5}件`);
    }
    parts.push("");
  }

  if (status.unstaged.length > 0) {
    parts.push(`**未ステージ変更** (${status.unstaged.length}件):`);
    for (const f of status.unstaged.slice(0, 5)) {
      parts.push(`  - ${f}`);
    }
    if (status.unstaged.length > 5) {
      parts.push(`  ... 他 ${status.unstaged.length - 5}件`);
    }
    parts.push("");
  }

  if (status.untracked.length > 0) {
    parts.push(`**未追跡ファイル** (${status.untracked.length}件):`);
    for (const f of status.untracked.slice(0, 3)) {
      parts.push(`  - ${f}`);
    }
    if (status.untracked.length > 3) {
      parts.push(`  ... 他 ${status.untracked.length - 3}件`);
    }
    parts.push("");
  }

  // Show last commit for comparison
  if (lastCommit) {
    parts.push("**直前のコミット**:");
    parts.push(lastCommit);
    parts.push("");
  }

  // Add guidance
  parts.push(
    "📋 **確認事項**:",
    "- サマリーの「完了」項目が実際にコミット済みか確認",
    "- 未コミット変更がサマリーの作業内容と一致するか確認",
    "- 不整合がある場合、`git status` と `git diff` で詳細確認",
  );

  return parts.join("\n");
}

async function main(): Promise<void> {
  const result: { continue: boolean; message?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;
    const source = (inputData.source as string) || "";

    // Only check on session resume (resume or compact)
    if (source !== "resume" && source !== "compact") {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Not a resume session (source=${source})`,
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Get git status
    const status = getGitStatus();
    const hasChanges =
      status.staged.length > 0 || status.unstaged.length > 0 || status.untracked.length > 0;

    // If no changes at all, nothing to warn about
    if (!hasChanges) {
      await logHookExecution(HOOK_NAME, "approve", "Working tree is clean", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Get last commit info for context
    const lastCommit = getLastCommitInfo();

    // Format and display warning
    result.message = formatFileStateWarning(status, lastCommit);

    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Uncommitted changes detected (staged=${status.staged.length}, unstaged=${status.unstaged.length}, untracked=${status.untracked.length})`,
      {
        source,
        staged_count: status.staged.length,
        unstaged_count: status.unstaged.length,
        untracked_count: status.untracked.length,
      },
      { sessionId },
    );
  } catch (error) {
    await logHookExecution(HOOK_NAME, "approve", `Error: ${formatError(error)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

// Only run main when executed directly, not when imported
if (import.meta.main) {
  main();
}
