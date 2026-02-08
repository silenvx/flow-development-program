#!/usr/bin/env bun
/**
 * セッション開始時にオープンIssueをリマインド表示する。
 *
 * Why:
 *   オープンIssueを把握せずに作業を始めると、重複作業や優先度の
 *   低いタスクに時間を費やしてしまう。セッション開始時にリマインド
 *   することで、優先度の高いIssueへの対応を促す。
 *
 * What:
 *   - セッションの最初のBash実行時にオープンIssueを表示
 *   - 未アサインのIssueのみを表示
 *   - 高優先度（P1/P2）のIssueを先頭に表示
 *   - systemMessageで情報提供（ブロックしない）
 *
 * Remarks:
 *   - task-start-checklistは要件確認、本フックはIssue確認
 *   - ファイルロックで並行実行時の競合を防止
 *   - Python版: open_issue_reminder.py
 *
 * Changelog:
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { spawnSync } from "node:child_process";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { checkAndUpdateSessionMarker, createHookContext, parseHookInput } from "../lib/session";

/** Labels considered high priority */
const HIGH_PRIORITY_LABELS = ["P1", "P2", "priority:high", "priority:critical"];

export interface IssueLabel {
  name: string;
}

export interface Assignee {
  login: string;
}

export interface Issue {
  number: number;
  title: string;
  labels?: IssueLabel[];
  assignees?: Assignee[];
}

/**
 * Get list of open issues from GitHub that are unassigned.
 */
function getOpenIssues(): Issue[] {
  try {
    const result = spawnSync(
      "gh",
      ["issue", "list", "--state", "open", "--json", "number,title,labels,assignees"],
      {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM,
      },
    );

    if (result.status === 0 && result.stdout) {
      const issues: Issue[] = JSON.parse(result.stdout);
      // Filter out issues that have assignees (already being worked on)
      return issues.filter((issue) => !issue.assignees || issue.assignees.length === 0);
    }
  } catch {
    // Best effort - gh command may fail
  }
  return [];
}

/**
 * Check if issue has any high priority label.
 */
export function isHighPriorityIssue(issue: Issue): boolean {
  if (!issue.labels) return false;
  const labelNames = issue.labels.map((l) => l.name);
  return labelNames.some((name) => HIGH_PRIORITY_LABELS.includes(name));
}

/**
 * Format issues into a readable message.
 * High priority issues are shown first with emphasis.
 */
export function formatIssuesMessage(issues: Issue[]): string {
  if (!issues.length) return "";

  // Separate high priority issues
  const highPriority = issues.filter(isHighPriorityIssue);
  const otherIssues = issues.filter((i) => !isHighPriorityIssue(i));

  const lines: string[] = [];

  // Show high priority issues first with strong emphasis
  if (highPriority.length > 0) {
    lines.push("🚨 **高優先度Issue（優先対応必須）**:");
    for (const issue of highPriority) {
      const labelStr =
        issue.labels && issue.labels.length > 0
          ? ` [${issue.labels.map((l) => l.name).join(", ")}]`
          : "";
      lines.push(`  → #${issue.number}: ${issue.title}${labelStr}`);
    }
    lines.push("");
  }

  // Show other unassigned issues
  if (otherIssues.length > 0) {
    lines.push("📋 **未アサインのオープンIssue** (対応検討してください):");
    for (const issue of otherIssues.slice(0, 5)) {
      const labelStr =
        issue.labels && issue.labels.length > 0
          ? ` [${issue.labels.map((l) => l.name).join(", ")}]`
          : "";
      lines.push(`  - #${issue.number}: ${issue.title}${labelStr}`);
    }

    if (otherIssues.length > 5) {
      lines.push(`  ... 他 ${otherIssues.length - 5} 件`);
    }
  }

  if (lines.length > 0) {
    lines.push("");
    lines.push("詳細: `gh issue list --state open`");
  }

  return lines.join("\n");
}

async function main(): Promise<void> {
  // Parse hook input for session ID
  const inputData = await parseHookInput();
  const ctx = createHookContext(inputData);
  const sessionId = ctx.sessionId;

  const result: { decision?: string; systemMessage?: string } = {};

  try {
    // Atomically check if new session and update marker
    if (checkAndUpdateSessionMarker("open-issue-check")) {
      const issues = getOpenIssues();
      if (issues.length > 0) {
        const message = formatIssuesMessage(issues);
        if (message) {
          result.systemMessage = message;
        }
      }
    }
  } catch (error) {
    // Don't block on errors, just skip the reminder
    console.error(`[open-issue-reminder] Error: ${formatError(error)}`);
  }

  await logHookExecution(
    "open-issue-reminder",
    result.decision ?? "approve",
    undefined,
    undefined,
    { sessionId },
  );
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
