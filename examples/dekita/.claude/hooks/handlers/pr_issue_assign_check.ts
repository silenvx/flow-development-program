#!/usr/bin/env bun
/**
 * gh pr create 時に Closes で参照される Issue のアサイン確認・自動アサイン。
 *
 * Why:
 *   PRで参照されるIssueが誰にもアサインされていないと、
 *   他のセッションが同じIssueに着手する競合リスクがある。
 *   自動アサインで競合を防止する。
 *
 * What:
 *   - gh pr createコマンドからPRボディを抽出
 *   - Closes #xxxパターンからIssue番号を抽出
 *   - アサインされていないIssueを自動アサイン
 *   - 他者にアサイン済みの場合は警告
 *
 * Remarks:
 *   - 非ブロック型（PreToolUse）
 *   - issue-auto-assign.pyはworktree作成時、本フックはPR作成時
 *   - --body inline引数のみ対応（エディタ入力は対象外）
 *
 * Changelog:
 *   - silenvx/dekita#203: フック追加
 *   - silenvx/dekita#3160: TypeScript移行
 */

import { extractPrBody } from "../lib/command";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { extractIssueNumbersFromPrBody } from "../lib/issue_checker";
import { logHookExecution } from "../lib/logging";
import { approveAndExit } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";
import { stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "pr-issue-assign-check";

// =============================================================================
// Command Detection
// =============================================================================

/**
 * Check if command is a gh pr create command.
 */
function isGhPrCreateCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  const stripped = stripQuotedStrings(command);
  return /gh\s+pr\s+create\b/.test(stripped);
}

// extractPrBody is imported from ../lib/command

// =============================================================================
// Issue Extraction
// =============================================================================

/**
 * Extract issue numbers from Closes/Fixes/Resolves keywords.
 *
 * Uses shared library to correctly handle comma-separated issues
 * (e.g., "Closes #1, #2" or "Closes #1, Fixes #2").
 */
function extractClosesIssues(body: string): number[] {
  if (!body) {
    return [];
  }

  // Use shared library for robust issue number extraction
  const issueStrings = extractIssueNumbersFromPrBody(body);
  return issueStrings.map((num) => Number.parseInt(num, 10)).sort((a, b) => a - b);
}

// =============================================================================
// GitHub API Functions
// =============================================================================

/**
 * Get current assignees of an issue.
 */
async function getIssueAssignees(issueNumber: number): Promise<string[] | null> {
  try {
    const result = await asyncSpawn(
      "gh",
      ["issue", "view", String(issueNumber), "--json", "assignees"],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (result.success) {
      const data = JSON.parse(result.stdout);
      return (data.assignees ?? []).map((a: { login?: string }) => a.login ?? "");
    }
  } catch {
    // Fail silently
  }
  return null;
}

/**
 * Get current GitHub user login.
 */
async function getCurrentUser(): Promise<string | null> {
  try {
    const result = await asyncSpawn("gh", ["api", "user", "--jq", ".login"], {
      timeout: TIMEOUT_MEDIUM * 1000,
    });

    if (result.success) {
      return result.stdout.trim();
    }
  } catch {
    // Fail silently
  }
  return null;
}

/**
 * Assign the issue to the current user.
 */
async function assignIssue(issueNumber: number): Promise<boolean> {
  try {
    const result = await asyncSpawn(
      "gh",
      ["issue", "edit", String(issueNumber), "--add-assignee", "@me"],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );
    return result.success;
  } catch {
    return false;
  }
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};
  let sessionId: string | undefined;

  try {
    const input = await parseHookInput();
    const ctx = createHookContext(input);
    sessionId = ctx.sessionId;
    const toolInput = input.tool_input as Record<string, unknown> | undefined;
    const command = (toolInput?.command as string) ?? "";

    // Only check gh pr create commands
    if (!isGhPrCreateCommand(command)) {
      await logHookExecution(HOOK_NAME, "approve", "Not gh pr create", undefined, { sessionId });
      approveAndExit(HOOK_NAME);
    }

    // Extract PR body
    const body = extractPrBody(command);
    if (!body) {
      await logHookExecution(HOOK_NAME, "approve", "No body found", undefined, { sessionId });
      approveAndExit(HOOK_NAME);
    }

    // Extract issue numbers from Closes keywords
    const issueNumbers = extractClosesIssues(body);
    if (issueNumbers.length === 0) {
      await logHookExecution(HOOK_NAME, "approve", "No Closes keywords", undefined, { sessionId });
      approveAndExit(HOOK_NAME);
    }

    // Get current user for comparison
    const currentUser = await getCurrentUser();

    // Check and auto-assign each issue
    const messages: string[] = [];
    for (const issueNum of issueNumbers) {
      const assignees = await getIssueAssignees(issueNum);

      if (assignees === null) {
        // Lookup failed
        messages.push(`⚠️ Issue #${issueNum} のアサイン確認に失敗。手動確認を推奨`);
      } else if (assignees.length === 0) {
        // No assignees - auto-assign
        if (await assignIssue(issueNum)) {
          messages.push(`✅ Issue #${issueNum} に自動アサインしました（競合防止）`);
        } else {
          messages.push(
            `⚠️ Issue #${issueNum} のアサインに失敗。手動: \`gh issue edit ${issueNum} --add-assignee @me\``,
          );
        }
      } else if (currentUser && !assignees.includes(currentUser)) {
        // Assigned to someone else
        messages.push(`ℹ️ Issue #${issueNum} は他者にアサイン済み: ${assignees.join(", ")}`);
      }
      // If current user is assigned, no message needed
    }

    if (messages.length > 0) {
      result.systemMessage = messages.join("\n");
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Processed ${issueNumbers.length} issue(s)`,
        {
          issues: issueNumbers,
        },
        { sessionId },
      );
    } else {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "All issues already assigned",
        {
          issues: issueNumbers,
        },
        { sessionId },
      );
    }
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(error)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
  process.exit(0);
}

if (import.meta.main) {
  main();
}
