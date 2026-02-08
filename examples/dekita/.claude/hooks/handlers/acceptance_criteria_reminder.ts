#!/usr/bin/env bun
/**
 * PR作成時に対象Issueの受け入れ条件未完了を警告する。
 *
 * Why:
 *   受け入れ条件が未完了のままPRを作成すると、マージ時にmerge-checkで
 *   ブロックされる。PR作成時に警告することで、事前に気づいて対処できる。
 *
 * What:
 *   - gh pr createコマンドを検出
 *   - ブランチ名からIssue番号を抽出
 *   - 対象Issueの受け入れ条件（チェックボックス）を確認
 *   - 未完了項目がある場合に警告（ブロックはしない）
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、systemMessageで通知）
 *   - merge-checkはマージ時、本フックはPR作成時に警告
 *   - 取り消し線（~~）付きの項目はスキップ
 *
 * Changelog:
 *   - silenvx/dekita#1288: フック追加
 *   - silenvx/dekita#823: 取り消し線の扱い
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { spawnSync } from "node:child_process";
import { extractIssueNumberFromBranch, getCurrentBranch } from "../lib/git";
import { tokenize } from "../lib/github";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "acceptance-criteria-reminder";

/**
 * Remove code blocks from text to avoid false positives.
 */
export function stripCodeBlocks(text: string): string {
  // Remove fenced code blocks (multiline)
  let result = text.replace(/```[\s\S]*?```/g, "");
  // Remove inline code
  result = result.replace(/`[^`]+`/g, "");
  return result;
}

/**
 * Check if the command is 'gh pr create'.
 */
export function isPrCreateCommand(command: string): boolean {
  try {
    const tokens = tokenize(command);
    if (tokens.length < 3) {
      return false;
    }
    return tokens[0] === "gh" && tokens[1] === "pr" && tokens[2] === "create";
  } catch {
    return false;
  }
}

interface IssueData {
  title: string;
  body: string;
  state: string;
}

interface AcceptanceCriteria {
  isCompleted: boolean;
  text: string;
}

/**
 * Fetch issue and extract acceptance criteria (checkbox items).
 */
function fetchIssueAcceptanceCriteria(issueNumber: string): {
  success: boolean;
  title: string;
  criteria: AcceptanceCriteria[];
} {
  try {
    const result = spawnSync("gh", ["issue", "view", issueNumber, "--json", "title,body,state"], {
      encoding: "utf-8",
      timeout: 30000,
    });

    if (result.status !== 0) {
      return { success: false, title: "", criteria: [] };
    }

    const data: IssueData = JSON.parse(result.stdout);
    const title = data.title || "";
    const body = data.body || "";
    const state = data.state || "";

    // Skip closed Issues
    if (state === "CLOSED") {
      return { success: false, title: "", criteria: [] };
    }

    // Strip code blocks before extracting checkboxes
    const bodyWithoutCode = stripCodeBlocks(body);

    // Extract checkbox items: - [ ] or - [x] or * [ ] format
    // Issue #823: Treat strikethrough checkboxes (- [ ] ~~text~~) as completed
    const criteria: AcceptanceCriteria[] = [];
    const pattern = /^[\s]*[-*]\s*\[([ xX])\]\s*(.+)$/;
    const strikethroughPattern = /^~~.+?~~/;

    for (const line of bodyWithoutCode.split("\n")) {
      const match = pattern.exec(line);
      if (match) {
        const checkboxMark = match[1].toLowerCase();
        const criteriaText = match[2].trim();
        const isStrikethrough = strikethroughPattern.test(criteriaText);
        const isCompleted = checkboxMark === "x" || isStrikethrough;
        criteria.push({ isCompleted, text: criteriaText });
      }
    }

    return { success: true, title, criteria };
  } catch {
    return { success: false, title: "", criteria: [] };
  }
}

interface IssueInfo {
  issueNumber: string;
  title: string;
  totalCount: number;
  completedCount: number;
  incompleteItems: string[];
}

/**
 * Check if an issue has incomplete acceptance criteria.
 */
function checkAcceptanceCriteria(issueNumber: string): IssueInfo | null {
  const { success, title, criteria } = fetchIssueAcceptanceCriteria(issueNumber);
  if (!success || criteria.length === 0) {
    return null;
  }

  const incompleteItems = criteria.filter((c) => !c.isCompleted).map((c) => c.text);
  const totalCount = criteria.length;
  const completedCount = totalCount - incompleteItems.length;

  if (incompleteItems.length > 0) {
    return {
      issueNumber,
      title,
      totalCount,
      completedCount,
      incompleteItems,
    };
  }

  return null;
}

/**
 * Format a warning message for incomplete acceptance criteria.
 */
function formatWarningMessage(issueInfo: IssueInfo): string {
  const { issueNumber, title, completedCount, totalCount, incompleteItems } = issueInfo;

  let itemsDisplay = incompleteItems
    .slice(0, 5)
    .map((item) => `  - [ ] ${item}`)
    .join("\n");
  if (incompleteItems.length > 5) {
    itemsDisplay += `\n  ...他${incompleteItems.length - 5}件`;
  }

  return `⚠️ Issue #${issueNumber} (${title}) の受け入れ条件が未完了です\n   進捗: ${completedCount}/${totalCount} 完了\n   未完了の条件:\n${itemsDisplay}\n\n   PR作成後、Issueのチェックボックスを更新してください。\n   そうしないと、マージ時にブロックされます。`;
}

async function main(): Promise<void> {
  const hookInput = await parseHookInput();
  const sessionId = hookInput?.session_id;
  if (!hookInput) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  const toolName = hookInput.tool_name || "";
  const toolInput = hookInput.tool_input || {};

  // Only process Bash tool calls
  if (toolName !== "Bash") {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  const command = (toolInput as { command?: string }).command || "";

  // Check if this is a PR create command
  if (!isPrCreateCommand(command)) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // Get current branch
  const branch = await getCurrentBranch();
  if (!branch) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // Extract issue number from branch
  const issueNumber = extractIssueNumberFromBranch(branch);
  if (!issueNumber) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // Check acceptance criteria
  const issueInfo = checkAcceptanceCriteria(issueNumber);

  if (issueInfo) {
    // Log the reminder
    logHookExecution(
      HOOK_NAME,
      "approve",
      `Issue #${issueNumber} has incomplete acceptance criteria`,
      {
        issue_number: issueNumber,
        completed: issueInfo.completedCount,
        total: issueInfo.totalCount,
        incomplete_count: issueInfo.incompleteItems.length,
      },
      { sessionId },
    );

    // Print warning message
    const warning = formatWarningMessage(issueInfo);
    console.error(warning);
  }

  console.log(JSON.stringify({ continue: true }));
}

if (import.meta.main) {
  main();
}
