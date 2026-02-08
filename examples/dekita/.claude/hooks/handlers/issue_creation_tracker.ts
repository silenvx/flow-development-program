#!/usr/bin/env bun
/**
 * セッション内で作成されたIssue番号を記録し、実装を促す。
 *
 * Why:
 *   セッション内で作成したIssueは同セッションで実装まで完遂する
 *   必要がある。Issue作成を追跡し、優先度に応じた実装指示を
 *   出すことで、Issue作成で終わらず実装まで誘導する。
 *
 * What:
 *   - gh issue createの成功を検出しIssue番号を抽出
 *   - セッションIDごとのファイルにIssue番号を記録
 *   - 優先度（P0/P1/P2）を解析し適切なメッセージを表示
 *   - P0は即時実装、P1/P2は現タスク完遂後の実装を指示
 *
 * State:
 *   - writes: .claude/logs/flow/session-created-issues-{session_id}.json
 *   - writes: .claude/logs/decisions/issue-decisions-{session_id}.jsonl
 *
 * Remarks:
 *   - 非ブロック型（PostToolUse）
 *   - related-task-checkがセッション終了時に未実装Issueをチェック
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#1943: P0即時実装警告を追加
 *   - silenvx/dekita#1950: --labelからの優先度解析でAPIコール削減
 *   - silenvx/dekita#1951: P1/P2の優先度を明示表示
 *   - silenvx/dekita#2076: セッション内Issue即着手ルール適用
 *   - silenvx/dekita#2121: 確認禁止の明確化
 *   - silenvx/dekita#2337: メッセージ強調と禁止事項明確化
 *   - silenvx/dekita#2677: Issue判定記録機能を追加
 *   - silenvx/dekita#3160: TypeScript移行
 */

import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { basename, join } from "node:path";
import { DECISIONS_LOG_DIR, FLOW_LOG_DIR } from "../lib/common";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { extractLabelsFromCommand, extractPriorityFromLabels } from "../lib/labels";
import { logHookExecution } from "../lib/logging";
import { logToSessionFile } from "../lib/logging";
import { parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";
import { createHookContext } from "../lib/types";

const HOOK_NAME = "issue-creation-tracker";

// =============================================================================
// Session File Management
// =============================================================================

/**
 * Get the file path for storing session-created issues.
 */
function getSessionIssuesFile(sessionId: string): string {
  const safeSessionId = basename(sessionId);
  return join(FLOW_LOG_DIR, `session-created-issues-${safeSessionId}.json`);
}

/**
 * Load list of issue numbers created in this session.
 */
function loadSessionIssues(sessionId: string): number[] {
  const issuesFile = getSessionIssuesFile(sessionId);
  if (!existsSync(issuesFile)) {
    return [];
  }

  try {
    const data = JSON.parse(readFileSync(issuesFile, "utf-8"));
    return data.issues ?? [];
  } catch (error) {
    console.error(
      `[${HOOK_NAME}] Warning: Corrupted issues file ${issuesFile}: ${formatError(error)}`,
    );
    return [];
  }
}

/**
 * Save list of issue numbers created in this session.
 * Uses atomic write (tmp→replace) for safety.
 */
function saveSessionIssues(sessionId: string, issues: number[]): void {
  mkdirSync(FLOW_LOG_DIR, { recursive: true });

  const issuesFile = getSessionIssuesFile(sessionId);
  const tmpFile = `${issuesFile}.tmp`;

  try {
    writeFileSync(tmpFile, JSON.stringify({ issues }));
    renameSync(tmpFile, issuesFile);
  } catch (error) {
    console.error(
      `[${HOOK_NAME}] Warning: Failed to save issues file ${issuesFile}: ${formatError(error)}`,
    );
    try {
      unlinkSync(tmpFile);
    } catch {
      // Best effort cleanup
    }
  }
}

// =============================================================================
// Issue Number Extraction
// =============================================================================

/**
 * Extract issue number from gh issue create output.
 *
 * gh issue create outputs URLs like:
 * - https://github.com/owner/repo/issues/123
 */
function extractIssueNumber(output: string): number | null {
  const match = output.match(/github\.com\/[^/]+\/[^/]+\/issues\/(\d+)/);
  if (match) {
    return Number.parseInt(match[1], 10);
  }
  return null;
}

// =============================================================================
// Priority Extraction
// =============================================================================

/**
 * Extract priority label from gh issue create command string.
 */
function extractPriorityFromCommand(command: string): string | null {
  const labels = extractLabelsFromCommand(command);
  if (!labels.length) {
    return null;
  }
  // Only check P0-P2 for this hook's special messages
  return extractPriorityFromLabels(labels, new Set(["P0", "P1", "P2"]));
}

/**
 * Get priority label from issue via gh CLI.
 */
async function getIssuePriority(issueNumber: number): Promise<string | null> {
  try {
    const result = await asyncSpawn(
      "gh",
      ["issue", "view", String(issueNumber), "--json", "labels"],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (!result.success) {
      return null;
    }

    const data = JSON.parse(result.stdout);
    const labels: Array<{ name?: string }> = data.labels ?? [];

    let hasP1 = false;
    let hasP2 = false;

    for (const label of labels) {
      const name = (label.name ?? "").toUpperCase();
      if (name === "P0" || name === "PRIORITY:P0") {
        return "P0";
      }
      if (name === "P1" || name === "PRIORITY:P1") {
        hasP1 = true;
      }
      if (name === "P2" || name === "PRIORITY:P2") {
        hasP2 = true;
      }
    }

    if (hasP1) return "P1";
    if (hasP2) return "P2";
  } catch (error) {
    console.error(`[${HOOK_NAME}] Warning: Failed to get issue priority: ${formatError(error)}`);
  }
  return null;
}

// =============================================================================
// Decision Recording
// =============================================================================

/**
 * Record issue creation decision to decision log.
 */
async function recordIssueDecision(
  sessionId: string,
  issueNumber: number,
  priority: string | null,
  command: string,
): Promise<void> {
  const titleMatch = command.match(/--title\s+["']([^"']+)["']/);
  const title = titleMatch ? titleMatch[1] : `Issue #${issueNumber}`;

  const entry: Record<string, unknown> = {
    decision: "create",
    issue_number: issueNumber,
    problem: title,
    reason: "Issue created via gh issue create",
  };

  if (priority) {
    entry.severity = priority;
  }

  await logToSessionFile(DECISIONS_LOG_DIR, "issue-decisions", sessionId, entry);
}

// =============================================================================
// Message Generation
// =============================================================================

const BASE_REMINDER = `⚠️ **重要（AGENTS.md原則）**: このセッションで作成したIssueは、ユーザー確認なしに実装まで完遂してください。

**禁止**:
- 「実装しますか？」「着手しますか？」と確認を求めること
- 「何をしますか？」とユーザーに次の指示を求めること
- このメッセージを無視して別のタスクに移ること

**必須**: 今すぐworktreeを作成して実装を開始すること

※ Stop hookで未完了Issueはブロックされます`;

function generateSystemMessage(issueNumber: number, priority: string | null): string {
  if (priority === "P0") {
    return `⚠️ P0 Issue を作成しました。即時実装が必要です。

  #${issueNumber}

現在のタスクを中断し、このIssueを先に実装してください。

${BASE_REMINDER}`;
  }

  if (priority === "P1") {
    return `ℹ️ P1 Issue を作成しました。

  #${issueNumber}

現在のタスクを完遂後、このセッション内で実装してください。

${BASE_REMINDER}`;
  }

  if (priority === "P2") {
    return `ℹ️ P2 Issue を作成しました。

  #${issueNumber}

現在のタスクを完遂後、このセッション内で実装してください。

${BASE_REMINDER}`;
  }

  return `ℹ️ Issue を作成しました。

  #${issueNumber}

現在のタスクを完遂後、このセッション内で実装してください。

${BASE_REMINDER}`;
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };

  try {
    const input = await parseHookInput();
    const ctx = createHookContext(input);
    const toolName = input.tool_name ?? "";
    const toolInput = input.tool_input as Record<string, unknown> | undefined;

    // Only process Bash commands
    if (toolName !== "Bash") {
      console.log(JSON.stringify(result));
      return;
    }

    const command = (toolInput?.command as string) ?? "";

    // Check if this is a gh issue create command
    if (!command.includes("gh issue create")) {
      console.log(JSON.stringify(result));
      return;
    }

    // Get tool result
    const toolResult = input.tool_result as Record<string, unknown> | undefined;
    if (!toolResult) {
      console.log(JSON.stringify(result));
      return;
    }

    // Only record if command succeeded
    const exitCode = (toolResult.exit_code as number) ?? 0;
    if (exitCode !== 0) {
      console.log(JSON.stringify(result));
      return;
    }

    // Extract issue number from stdout or output field
    const stdout = ((toolResult.stdout as string) ?? "") || ((toolResult.output as string) ?? "");
    const issueNumber = extractIssueNumber(stdout);

    if (issueNumber) {
      const sessionId = ctx.sessionId;
      if (!sessionId) {
        console.log(JSON.stringify(result));
        return;
      }
      // Sanitize sessionId early to prevent path traversal in all uses
      const safeSessionId = basename(sessionId);

      // Add to session issues
      const issues = loadSessionIssues(safeSessionId);
      if (!issues.includes(issueNumber)) {
        issues.push(issueNumber);
        saveSessionIssues(safeSessionId, issues);

        // Try to extract priority from command first (avoid API call)
        let priority = extractPriorityFromCommand(command);
        if (priority === null) {
          // Fallback to API call if not found in command
          priority = await getIssuePriority(issueNumber);
        }

        // Record issue decision for later evaluation
        await recordIssueDecision(safeSessionId, issueNumber, priority, command);

        // Generate system message
        result.systemMessage = generateSystemMessage(issueNumber, priority);

        const logMessage = priority
          ? `Recorded ${priority} issue #${issueNumber}`
          : `Recorded issue #${issueNumber} - no priority set`;

        await logHookExecution(HOOK_NAME, "approve", logMessage, undefined, {
          sessionId: safeSessionId,
        });
      }
    }
  } catch (error) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
