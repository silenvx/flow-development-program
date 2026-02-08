/**
 * Issue・受け入れ基準チェック機能。
 *
 * Why:
 *   未完了の受け入れ基準を持つIssueがCloseされると、品質管理が形骸化する。
 *   PRからIssue参照を抽出し、受け入れ基準の完了状態を確認する。
 *
 * What:
 *   - PRボディからIssue参照を抽出（Closes #xxx等）
 *   - 受け入れ基準の完了チェック
 *
 * Changelog:
 *   - silenvx/dekita#3160: TypeScriptに移植
 */

import { ISSUE_REFERENCE_PATTERN, stripCodeBlocks } from "./check_utils";
import { TIMEOUT_MEDIUM } from "./constants";
import { addRepoFlag } from "./github";
import { asyncSpawn } from "./spawn";

/**
 * Extract issue numbers from Closes/Fixes keywords in PR body.
 */
export function extractIssueNumbersFromPrBody(body: string): string[] {
  if (!body) {
    return [];
  }

  // Find blocks starting with closing keywords
  // Handles comma-separated issues: "Closes #123, #456" and "Closes #123, Fixes #456"
  const blockPattern = /(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?):?\s+#\d+(?:\s*,\s*#\d+)*/gi;
  const blocks = body.match(blockPattern) || [];

  // Extract all issue numbers from matched blocks
  const allNumbers: string[] = [];
  for (const block of blocks) {
    const numbers = block.match(/#(\d+)/g);
    if (numbers) {
      allNumbers.push(...numbers.map((n) => n.slice(1)));
    }
  }

  return [...new Set(allNumbers)];
}

/**
 * Acceptance criteria item.
 */
export interface AcceptanceCriteriaItem {
  isCompleted: boolean;
  isStrikethrough: boolean;
  text: string;
}

/**
 * Fetch issue and extract acceptance criteria (checkbox items).
 *
 * @param issueNumber - The issue number to check
 * @param repo - Repository in owner/repo format, or null for current repo
 */
export async function fetchIssueAcceptanceCriteria(
  issueNumber: string,
  repo: string | null = null,
): Promise<{ success: boolean; title: string; criteria: AcceptanceCriteriaItem[] }> {
  try {
    const args = ["issue", "view", issueNumber, "--json", "title,body,state"];
    addRepoFlag(args, repo);
    const result = await asyncSpawn("gh", args, {
      timeout: TIMEOUT_MEDIUM * 1000,
    });

    if (!result.success) {
      return { success: false, title: "", criteria: [] };
    }

    const data = JSON.parse(result.stdout);
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
    const criteria: AcceptanceCriteriaItem[] = [];
    const pattern = /^[\s]*[-*]\s*\[([ xX])\]\s*(.+)$/gm;
    // Pattern to detect strikethrough: text starting with ~~...~~
    const strikethroughPattern = /^~~.+?~~/;

    let match = pattern.exec(bodyWithoutCode);
    while (match !== null) {
      const checkboxMark = match[1].toLowerCase();
      const criteriaText = match[2].trim();
      // Checkbox is completed if:
      // 1. Marked with [x] or [X]
      // 2. Text starts with strikethrough (~~text~~)
      const isStrikethrough = strikethroughPattern.test(criteriaText);
      const isCompleted = checkboxMark === "x" || isStrikethrough;
      criteria.push({ isCompleted, isStrikethrough, text: criteriaText });
      match = pattern.exec(bodyWithoutCode);
    }

    return { success: true, title, criteria };
  } catch {
    return { success: false, title: "", criteria: [] };
  }
}

/**
 * Check if text contains an Issue reference.
 */
export function hasIssueReference(text: string): boolean {
  return ISSUE_REFERENCE_PATTERN.test(text);
}
