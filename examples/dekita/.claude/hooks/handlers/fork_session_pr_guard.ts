#!/usr/bin/env bun
/**
 * Fork-sessionが親セッションのPRに介入することを防止する。
 *
 * Why:
 *   Fork-sessionが親セッションのPRに対してコミット・プッシュ・レビュー対応を
 *   実行すると、コンフリクトや重複作業が発生する。AGENTS.mdのルールを
 *   強制的にブロックすることで、この問題を防ぐ。
 *
 * What:
 *   - Fork-sessionかどうかを検出
 *   - PR関連コマンド（gh pr merge/review/comment, git push, git commit）を検出
 *   - Edit/Writeツールで親セッションのworktree内ファイルを編集しようとした場合もブロック
 *   - 親セッションのworktree内からの操作、または親セッションのPRへの操作をブロック
 *
 * Remarks:
 *   - 親セッション判定は transcript_path から session ancestry を取得して行う
 *   - ブロッキングフック（介入を確実に防止）
 *
 * Tags:
 *   type: blocking
 *   category: collaboration-guard
 *
 * Changelog:
 *   - silenvx/dekita#3418: 初期実装
 *   - silenvx/dekita#3953: Edit/Writeツールへの拡張
 */

import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { SESSION_MARKER_FILE } from "../lib/constants";
import { expandHome } from "../lib/cwd";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import {
  getBashCommand,
  getSessionAncestry,
  getToolInput,
  isForkSession,
  parseHookInput,
} from "../lib/session";

const HOOK_NAME = "fork_session_pr_guard";

// PR/Git related command patterns (write operations that modify PR state)
const PR_RELATED_PATTERNS = [
  /gh\s+pr\s+(create|merge|review|comment|edit|close|reopen)/,
  /git\s+push\b/,
  /git\s+commit\b/,
];

/**
 * Check if a command matches any PR-related pattern.
 */
export function isPrRelatedCommand(command: string): boolean {
  return PR_RELATED_PATTERNS.some((pattern) => pattern.test(command));
}

/**
 * Read session ID from worktree's .claude-session marker.
 */
export function getWorktreeSessionId(worktreePath: string): string | null {
  const markerFile = join(worktreePath, SESSION_MARKER_FILE);
  try {
    if (!existsSync(markerFile)) {
      return null;
    }
    const data = JSON.parse(readFileSync(markerFile, "utf-8"));
    return data.session_id ?? null;
  } catch {
    return null;
  }
}

/**
 * Get the worktree path from current working directory if in a worktree.
 *
 * Returns the .worktrees/<name> path if cwd is within a worktree, null otherwise.
 */
export function getWorktreePathFromCwd(cwd: string): string | null {
  const resolved = resolve(cwd);
  const worktreeMarker = ".worktrees/";
  // Use lastIndexOf to handle nested paths correctly (e.g., /user/.worktrees/repo/.worktrees/feature)
  const idx = resolved.lastIndexOf(worktreeMarker);

  if (idx === -1) {
    return null;
  }

  // Find the end of the worktree name (next / or end of string)
  const afterMarker = idx + worktreeMarker.length;
  const endIdx = resolved.indexOf("/", afterMarker);
  const worktreePath = endIdx === -1 ? resolved : resolved.substring(0, endIdx);

  return worktreePath;
}

/**
 * Check if a path (directory or file) is within a parent session's worktree.
 *
 * @param targetPath Path to check (directory or file)
 * @param parentSessionIds Set of parent session IDs from ancestry
 * @returns The worktree path if in a parent's worktree, null otherwise
 */
export function isInParentWorktree(
  targetPath: string,
  parentSessionIds: Set<string>,
): string | null {
  const worktreePath = getWorktreePathFromCwd(targetPath);
  if (!worktreePath) {
    return null;
  }

  const worktreeSessionId = getWorktreeSessionId(worktreePath);
  if (!worktreeSessionId) {
    return null;
  }

  if (parentSessionIds.has(worktreeSessionId)) {
    return worktreePath;
  }

  return null;
}

/**
 * Get parent session IDs (all ancestors except current session).
 */
export function getParentSessionIds(
  transcriptPath: string | null,
  currentSessionId: string,
): Set<string> {
  const ancestry = getSessionAncestry(transcriptPath);
  const parents = new Set<string>();

  for (const sessionId of ancestry) {
    if (sessionId !== currentSessionId) {
      parents.add(sessionId);
    }
  }

  return parents;
}

/**
 * Get file path from Edit/Write tool input.
 */
export function getFilePathFromToolInput(data: Record<string, unknown>): string | null {
  const toolInput = getToolInput(data as Parameters<typeof getToolInput>[0]);
  const filePath = toolInput.file_path;
  return typeof filePath === "string" && filePath ? filePath : null;
}

/**
 * Check if the tool is Edit or Write.
 */
function isEditWriteTool(data: Record<string, unknown>): boolean {
  const toolName = data.tool_name;
  return typeof toolName === "string" && ["Edit", "Write"].includes(toolName);
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const source = data.source ?? "";
    const transcriptPath = data.transcript_path ?? null;
    const hookCwd = (data.cwd as string | undefined) ?? process.cwd();

    // Determine the check target based on tool type
    const isEditWrite = isEditWriteTool(data);
    const command = isEditWrite ? "" : getBashCommand(data);
    const filePath = isEditWrite ? getFilePathFromToolInput(data) : null;

    // For Bash: skip if not a PR-related command
    // For Edit/Write: always check (any file edit in parent worktree should be blocked)
    if (!isEditWrite && !isPrRelatedCommand(command)) {
      console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
      return;
    }

    // For Edit/Write: skip if no file_path
    if (isEditWrite && !filePath) {
      console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
      return;
    }

    // Skip if not a fork-session
    if (!isForkSession(sessionId ?? "", source, transcriptPath)) {
      console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
      return;
    }

    // Get parent session IDs
    const parentSessionIds = getParentSessionIds(transcriptPath, sessionId ?? "");
    if (parentSessionIds.size === 0) {
      console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
      return;
    }

    // Determine the target path to check
    // For Edit/Write: resolve file_path against hookCwd to handle relative paths
    // For Bash: use cwd
    const targetPath = isEditWrite ? resolve(hookCwd, expandHome(filePath!)) : resolve(hookCwd);
    const parentWorktree = isInParentWorktree(targetPath, parentSessionIds);

    if (parentWorktree) {
      const operationDesc = isEditWrite
        ? `Edit/Write: ${filePath!.substring(0, 50)}${filePath!.length > 50 ? "..." : ""}`
        : `${command.substring(0, 50)}${command.length > 50 ? "..." : ""}`;

      const reason = [
        isEditWrite
          ? "Fork-sessionが親セッションのworktree内のファイルを編集しようとしました。"
          : "Fork-sessionが親セッションのworktree内でPR関連操作を試みました。",
        "",
        "AGENTS.mdの「fork-sessionの行動指針」に従い、この操作はブロックされました。",
        "",
        "**禁止事項**:",
        "- ❌ 親セッションが作業中のworktree内ファイルの編集",
        "- ❌ 親セッションのPRへのコミット・プッシュ",
        "",
        `検出された操作: ${operationDesc}`,
        `親worktree: ${parentWorktree}`,
        "",
        "**推奨アクション**:",
        `1. メインリポジトリに戻る: cd ${process.env.CLAUDE_PROJECT_DIR ?? "."}`,
        "2. 独立したIssueを選択して作業を開始",
        "3. 詳細は fork-session-collaboration-advisor の提案を参照",
      ].join("\n");

      await logHookExecution(
        HOOK_NAME,
        "block",
        "Fork-session tried to modify parent worktree",
        {
          worktree: parentWorktree,
          operation: operationDesc.substring(0, 100),
        },
        { sessionId },
      );

      console.log(JSON.stringify(makeBlockResult(HOOK_NAME, reason)));
      return;
    }

    // Not in a parent worktree, approve
    await logHookExecution(
      HOOK_NAME,
      "approve",
      isEditWrite ? "Edit/Write in own worktree" : "PR operation in own worktree",
      undefined,
      { sessionId },
    );
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
  } catch (e) {
    // On error, approve to avoid blocking
    const errorMsg = `Hook error: ${e instanceof Error ? e.message : String(e)}`;
    console.error(`[${HOOK_NAME}] ${errorMsg}`);
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
  }
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error:`, e);
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
  });
}
