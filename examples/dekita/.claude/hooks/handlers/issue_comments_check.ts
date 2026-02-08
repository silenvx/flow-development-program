#!/usr/bin/env bun
/**
 * gh issue viewコマンド実行時にコメントを自動表示する。
 *
 * Why:
 *   Issueコメントに重要な解決策や追加情報があっても見落とされ、
 *   無駄な時間を費やすことがある。コメントを自動表示して
 *   情報の見落としを防ぐ。
 *
 * What:
 *   - gh issue view <number> コマンドを検出
 *   - --commentsフラグがない場合、自動でコメントを取得
 *   - systemMessageでコメント内容を表示
 *
 * Remarks:
 *   - 非ブロック型（情報提供のみ）
 *   - --comments付きのコマンドはそのまま通過
 *   - Python版: issue_comments_check.py
 *
 * Changelog:
 *   - silenvx/dekita#538: フック追加（コメント見落とし防止）
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";
import { stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "issue-comments-check";

/**
 * Extract issue number from gh issue view command.
 *
 * Handles various flag positions:
 * - gh issue view 123
 * - gh issue view #123
 * - gh issue view --web 123
 * - gh issue view 123 --web
 */
export function extractIssueNumber(command: string): string | null {
  // Remove quoted strings to avoid false positives
  const cmd = stripQuotedStrings(command);

  // Check if this is a gh issue view command
  if (!/gh\s+issue\s+view\b/.test(cmd)) {
    return null;
  }

  // Extract all arguments after "gh issue view"
  const match = cmd.match(/gh\s+issue\s+view\s+(.+)/);
  if (!match) {
    return null;
  }

  const args = match[1];

  // Find issue number (with or without #) among the arguments
  // Skip flags (--flag or -f) and their values
  for (const part of args.split(/\s+/)) {
    // Skip flags and flag values
    if (part.startsWith("-")) {
      continue;
    }
    // Match issue number (with optional # prefix)
    const numMatch = part.match(/^#?(\d+)$/);
    if (numMatch) {
      return numMatch[1];
    }
  }

  return null;
}

/**
 * Check if command already has --comments flag.
 */
export function hasCommentsFlag(command: string): boolean {
  // Remove quoted strings to avoid matching flags inside quotes
  const cmd = stripQuotedStrings(command);
  // Match --comments as a standalone flag (bounded by start/end or whitespace)
  return /(?:^|\s)--comments(?:\s|$)/.test(cmd);
}

/**
 * Fetch issue comments using gh CLI.
 *
 * @returns [success, comments]:
 *   - [true, comments] if successful with comments
 *   - [true, ""] if successful but no comments
 *   - [false, ""] if error occurred
 */
function fetchIssueComments(issueNumber: string): [boolean, string] {
  try {
    const result = execSync(
      `gh issue view ${issueNumber} --json comments --jq '.comments[] | "---\\n**" + .author.login + "** (" + .createdAt[:10] + "):\\n" + .body + "\\n"'`,
      {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    return [true, result.trim()];
  } catch {
    // gh CLI not installed, timeout, or other error
    return [false, ""];
  }
}

interface ApproveResult {
  systemMessage?: string;
}

async function main(): Promise<void> {
  const result: ApproveResult = {};
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolName = (data.tool_name as string) || "";

    if (toolName !== "Bash") {
      await logHookExecution(HOOK_NAME, "approve", `not Bash: ${toolName}`, undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    const toolInput = (data.tool_input as Record<string, unknown>) || {};
    const command = (toolInput.command as string) || "";

    // Check if this is a gh issue view command
    const issueNumber = extractIssueNumber(command);
    if (!issueNumber) {
      await logHookExecution(HOOK_NAME, "approve", "no issue number found", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // If --comments is already present, let it through
    if (hasCommentsFlag(command)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `--comments付き: Issue #${issueNumber}`,
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Fetch comments and display via systemMessage
    const [success, comments] = fetchIssueComments(issueNumber);

    if (!success) {
      // Don't show misleading message on error
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `コメント取得エラー: Issue #${issueNumber}`,
        undefined,
        { sessionId },
      );
    } else if (comments) {
      result.systemMessage = `📝 **Issue #${issueNumber} のコメント** (自動取得)\n\n${comments}\n\n💡 Issueに取り組む前に、必ずコメントを確認してください。`;
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `コメント自動表示: Issue #${issueNumber}`,
        undefined,
        { sessionId },
      );
    } else {
      result.systemMessage = `ℹ️ Issue #${issueNumber} にはコメントがありません。`;
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `コメントなし: Issue #${issueNumber}`,
        undefined,
        { sessionId },
      );
    }
  } catch (error) {
    // Don't block on errors
    await logHookExecution(HOOK_NAME, "error", `フックエラー: ${formatError(error)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
