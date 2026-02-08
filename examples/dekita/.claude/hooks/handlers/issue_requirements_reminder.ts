#!/usr/bin/env bun
/**
 * PRマージ前にIssue要件の未完了項目を警告する。
 *
 * Why:
 *   継続セッション（compaction後）でIssue要件を忘れてマージしてしまう問題を防ぐ。
 *   PR #3055でIssue #3051の要件を1つしか完了せずにマージした事例がある。
 *
 * What:
 *   - gh pr mergeコマンドを検出
 *   - PRボディから`Closes #xxx`パターンでIssue番号を抽出
 *   - 各Issueの受け入れ条件（チェックボックス）を確認
 *   - 未完了項目がある場合に警告（ブロックはしない）
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、stderrで通知）
 *   - merge-checkはマージ可否を判定、本フックはIssue要件を再確認
 *   - 取り消し線（~~）付きの項目はスキップ
 *
 * Changelog:
 *   - silenvx/dekita#3056: 初期実装
 */

import { extractIssueNumberFromBranch } from "../lib/git";
import { parseGhPrCommand } from "../lib/github";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";

const HOOK_NAME = "issue-requirements-reminder";

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
 * Check if the command is 'gh pr merge'.
 */
export function isPrMergeCommand(command: string): boolean {
  const [subcommand] = parseGhPrCommand(command);
  return subcommand === "merge";
}

/**
 * Extract PR number from 'gh pr merge' command.
 */
export function extractPrNumberFromCommand(command: string): string | null {
  const [subcommand, prNumber] = parseGhPrCommand(command);
  if (subcommand === "merge" && prNumber) {
    return prNumber;
  }
  return null;
}

interface PrData {
  body: string;
  headRefName: string;
}

/**
 * Fetch PR body from GitHub.
 */
async function fetchPrBody(prNumber: string): Promise<PrData | null> {
  try {
    const result = await asyncSpawn("gh", ["pr", "view", prNumber, "--json", "body,headRefName"], {
      timeout: 30000,
    });

    if (!result.success) {
      return null;
    }

    const data = JSON.parse(result.stdout);
    return {
      body: data.body || "",
      headRefName: data.headRefName || "",
    };
  } catch {
    return null;
  }
}

/**
 * Extract issue numbers from Closes/Fixes keywords in PR body.
 */
export function extractIssueNumbersFromPrBody(body: string): string[] {
  if (!body) {
    return [];
  }

  // Strip code blocks first
  const bodyWithoutCode = stripCodeBlocks(body);

  const allNumbers = new Set<string>();

  // Pattern 1: Closes #123, Fixes #456, Resolves #789
  // Also handles multiple issues: Closes #123, #456
  const shorthandPattern = /(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?):?\s+#\d+(?:\s*,\s*#\d+)*/gi;
  const shorthandBlocks = bodyWithoutCode.match(shorthandPattern) || [];

  for (const block of shorthandBlocks) {
    const numbers = block.match(/#(\d+)/g) || [];
    for (const num of numbers) {
      const issueNumber = num.slice(1); // Remove '#'
      allNumbers.add(issueNumber);
    }
  }

  // Pattern 2: Closes https://github.com/org/repo/issues/123
  // Also handles comma-separated URLs: Closes URL1, URL2
  const urlBlockPattern =
    /(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?):?\s+https:\/\/github\.com\/[^\s,]+(?:\s*,\s*https:\/\/github\.com\/[^\s,]+)*/gi;
  const urlBlocks = bodyWithoutCode.match(urlBlockPattern) || [];

  for (const block of urlBlocks) {
    // Extract issue numbers from /issues/N pattern in the block
    const issueMatches = block.matchAll(/\/issues\/(\d+)/g);
    for (const issueMatch of issueMatches) {
      const issueNumber = issueMatch[1];
      allNumbers.add(issueNumber);
    }
  }

  return Array.from(allNumbers);
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
async function fetchIssueAcceptanceCriteria(issueNumber: string): Promise<{
  success: boolean;
  title: string;
  criteria: AcceptanceCriteria[];
}> {
  try {
    const result = await asyncSpawn(
      "gh",
      ["issue", "view", issueNumber, "--json", "title,body,state"],
      {
        timeout: 30000,
      },
    );

    if (!result.success) {
      return { success: false, title: "", criteria: [] };
    }

    const data: IssueData = JSON.parse(result.stdout);
    const title = data.title || "";
    const body = data.body || "";
    const state = data.state || "";

    // Skip closed Issues (they've already been resolved)
    if (state === "CLOSED") {
      return { success: false, title: "", criteria: [] };
    }

    // Strip code blocks before extracting checkboxes
    const bodyWithoutCode = stripCodeBlocks(body);

    // Extract checkbox items: - [ ] or - [x] or * [ ] format
    // Issue #823: Treat strikethrough checkboxes (- [ ] ~~text~~) as completed
    const criteria: AcceptanceCriteria[] = [];
    const pattern = /^[\s]*[-*]\s*\[([ xX])\]\s*(.+)$/;

    // acceptance_criteria_reminder.ts と同じロジックを使用
    const strikethroughPattern = /^~~.+?~~/;

    for (const line of bodyWithoutCode.split("\n")) {
      const match = pattern.exec(line);
      if (match) {
        const checkboxMark = match[1].toLowerCase();
        const criteriaText = match[2].trim();
        // Check if the text starts with strikethrough
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
async function checkAcceptanceCriteria(issueNumber: string): Promise<IssueInfo | null> {
  const { success, title, criteria } = await fetchIssueAcceptanceCriteria(issueNumber);
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
function formatWarningMessage(issuesWithIncomplete: IssueInfo[]): string {
  const lines: string[] = [];

  lines.push("⚠️ マージ前にIssue要件を再確認してください");
  lines.push("");

  for (const issueInfo of issuesWithIncomplete) {
    const { issueNumber, title, completedCount, totalCount, incompleteItems } = issueInfo;

    lines.push(`📋 Issue #${issueNumber}: ${title}`);
    lines.push(`   進捗: ${completedCount}/${totalCount} 完了`);
    lines.push("   未完了の条件:");

    // Show up to 5 items
    const displayItems = incompleteItems.slice(0, 5);
    for (const item of displayItems) {
      lines.push(`     - [ ] ${item}`);
    }

    if (incompleteItems.length > 5) {
      lines.push(`     ...他${incompleteItems.length - 5}件`);
    }

    lines.push("");
  }

  lines.push("💡 継続セッションでは `gh issue view <Issue番号>` でIssue要件を再確認してください。");
  lines.push("   マージ後にIssueのチェックボックスが未完了だと問題が残ります。");

  return lines.join("\n");
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

  // Check if this is a PR merge command
  if (!isPrMergeCommand(command)) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // Extract PR number from command
  let prNumber = extractPrNumberFromCommand(command);

  // If no PR number in command, try to get current branch's PR
  if (!prNumber) {
    try {
      const result = await asyncSpawn("gh", ["pr", "view", "--json", "number"], {
        timeout: 30000,
      });
      if (result.success) {
        const data = JSON.parse(result.stdout);
        prNumber = data.number?.toString() || null;
      }
    } catch {
      // Ignore error - will skip if no PR found
    }
  }

  if (!prNumber) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // Fetch PR body
  const prData = await fetchPrBody(prNumber);
  if (!prData) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // Extract issue numbers from PR body
  let issueNumbers = extractIssueNumbersFromPrBody(prData.body);

  // Fallback: try to extract from branch name
  if (issueNumbers.length === 0) {
    const branchIssue = extractIssueNumberFromBranch(prData.headRefName);
    if (branchIssue) {
      issueNumbers = [branchIssue];
    }
  }

  if (issueNumbers.length === 0) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // Check each issue for incomplete acceptance criteria (parallel execution)
  const issuesWithIncomplete = (
    await Promise.all(issueNumbers.map((issueNumber) => checkAcceptanceCriteria(issueNumber)))
  ).filter((issueInfo): issueInfo is IssueInfo => issueInfo !== null);

  if (issuesWithIncomplete.length > 0) {
    // Log the reminder
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Found ${issuesWithIncomplete.length} issue(s) with incomplete acceptance criteria`,
      {
        pr_number: prNumber,
        issues_with_incomplete: issuesWithIncomplete.map((i) => ({
          issue_number: i.issueNumber,
          completed: i.completedCount,
          total: i.totalCount,
          incomplete_count: i.incompleteItems.length,
        })),
      },
      { sessionId },
    );

    // Print warning message to stderr
    const warning = formatWarningMessage(issuesWithIncomplete);
    console.error(warning);
  }

  console.log(JSON.stringify({ continue: true }));
}

if (import.meta.main) {
  main();
}
