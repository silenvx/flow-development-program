#!/usr/bin/env bun
/**
 * 同一Issueへの重複PR作成・マージを防止するフック。
 *
 * Why:
 *   PR #3903で同時に2セッションが同じIssueに着手し、PR #3904とPR #3905が両方マージされた。
 *   同一Issue番号を参照するPRの重複を検知してブロックすることで、この問題を防止する。
 *
 * What:
 *   - PR作成時: 同一Issue番号を参照するオープンPRを検出してブロック
 *   - PR作成時: 同一Issue番号を参照するマージ済みPRを検出してブロック
 *   - マージ時: 同一Issue番号を参照するマージ済みPRを検出してブロック（自分以外）
 *
 * Remarks:
 *   - PreToolUse:Bashで発火（gh pr create, gh pr mergeコマンド）
 *   - タイトルまたはボディからIssue番号を抽出（#xxx形式）
 *   - Issue番号がない場合は何もしない（closes_keyword_checkとの併用を想定）
 *   - ほぼ同時（数秒差）に作成された場合の競合は許容（稀なケース）
 *
 * Limitations (by design):
 *   - -R/--repo flag is ignored: This hook assumes single repository usage.
 *     Cross-repository PR operations (gh pr merge -R other/repo) are not supported.
 *   - Cross-repository issue references: Issue numbers from external repositories
 *     (e.g., https://github.com/other/repo/issues/123) are treated as local issues.
 *     This is acceptable for single-repo projects like dekita.
 *
 * Changelog:
 *   - silenvx/dekita#3911: 初期実装
 *   - silenvx/dekita#3914: フォローアップPRのスキップ機構追加
 */

import { extractPrBody, extractPrTitle } from "../lib/command";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";
import { splitShellArgs, stripEnvPrefix, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "duplicate-pr-check";

/**
 * Keywords that indicate this PR is a deliberate followup to a previous PR.
 * PRs with these keywords in title or body will skip the duplicate check.
 */
const FOLLOWUP_KEYWORDS = [
  "[followup]",
  "[follow-up]",
  "[revert]",
  "[part ", // [part 2], [part 2/3] etc.
];

/**
 * Check if PR title or body contains a followup keyword.
 * This allows deliberate followup PRs to skip the duplicate check.
 *
 * @example
 * hasFollowupKeyword("[followup] fix: additional fix for #123") // true
 * hasFollowupKeyword("[revert] revert: #123") // true
 * hasFollowupKeyword("feat: implement feature #123 [part 2]") // true
 * hasFollowupKeyword("fix(hooks): ... #3911") // false
 */
export function hasFollowupKeyword(title: string, body?: string): boolean {
  const lowerTitle = title.toLowerCase();
  if (FOLLOWUP_KEYWORDS.some((kw) => lowerTitle.includes(kw))) return true;

  if (body) {
    const lowerBody = body.toLowerCase();
    return FOLLOWUP_KEYWORDS.some((kw) => lowerBody.includes(kw));
  }

  return false;
}

export interface PrInfo {
  number: number;
  title: string;
  body?: string;
  mergedAt?: string | null;
}

/**
 * Extract all Issue numbers from PR title.
 * Looks for #xxx patterns.
 *
 * @example
 * extractIssueNumbersFromTitle("fix(hooks): detect merged PR #3903") // ["3903"]
 * extractIssueNumbersFromTitle("fix: #3903 and #3904") // ["3903", "3904"]
 * extractIssueNumbersFromTitle("chore: update deps") // []
 */
export function extractIssueNumbersFromTitle(title: string): string[] {
  if (!title) return [];

  // Match all #xxx patterns
  const matches = title.matchAll(/#(\d+)/g);
  return Array.from(matches, (m) => m[1]);
}

/**
 * Extract Issue number from PR title (returns first match for backward compatibility).
 * @deprecated Use extractIssueNumbersFromTitle instead
 */
export function extractIssueNumberFromTitle(title: string): string | null {
  const numbers = extractIssueNumbersFromTitle(title);
  return numbers.length > 0 ? numbers[0] : null;
}

/**
 * Extract Issue numbers from PR body.
 * Looks for Closes #xxx, Fixes #xxx, Resolves #xxx patterns.
 * Also supports GitHub issue URLs: Closes https://github.com/owner/repo/issues/123
 *
 * @example
 * extractIssueNumbersFromBody("Closes #3903\n\nFixes #3904") // ["3903", "3904"]
 * extractIssueNumbersFromBody("Closes https://github.com/owner/repo/issues/123") // ["123"]
 */
export function extractIssueNumbersFromBody(body: string): string[] {
  if (!body) return [];

  // Match both #123 and https://github.com/owner/repo/issues/123 formats
  // Use \s* to allow no space after colon (e.g., "Closes:#123")
  const pattern =
    /\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s*(?:#(\d+)|https:\/\/github\.com\/[^/]+\/[^/]+\/issues\/(\d+))/gi;
  const numbers = new Set<string>();
  const matches = body.matchAll(pattern);

  for (const match of matches) {
    // match[1] is for #123, match[2] is for URL format
    const issueNum = match[1] || match[2];
    if (issueNum) {
      numbers.add(issueNum);
    }
  }

  return Array.from(numbers);
}

/**
 * Check if command is a gh pr create command.
 */
export function isGhPrCreateCommand(command: string): boolean {
  if (!command.trim()) return false;
  const stripped = stripQuotedStrings(stripEnvPrefix(command));
  // Use (?:\s|$) instead of \b to avoid false positives like "gh pr create-release"
  return /gh\s+pr\s+create(?:\s|$)/.test(stripped);
}

/**
 * Check if command is a gh pr merge command.
 */
export function isGhPrMergeCommand(command: string): boolean {
  if (!command.trim()) return false;
  const stripped = stripQuotedStrings(stripEnvPrefix(command));
  // Use (?:\s|$) instead of \b to avoid false positives like "gh pr merge-request"
  return /gh\s+pr\s+merge(?:\s|$)/.test(stripped);
}

/**
 * Extract PR number from gh pr merge command.
 *
 * @example
 * extractPrNumberFromMergeCommand("gh pr merge 123") // "123"
 * extractPrNumberFromMergeCommand("gh pr merge") // null (uses current branch)
 */
export function extractPrNumberFromMergeCommand(command: string): string | null {
  let args: string[];
  try {
    args = splitShellArgs(command);
  } catch {
    return null;
  }

  // Find "gh pr merge" in args
  let mergeIndex = -1;
  for (let i = 0; i < args.length - 2; i++) {
    if (args[i] === "gh" && args[i + 1] === "pr" && args[i + 2] === "merge") {
      mergeIndex = i + 2;
      break;
    }
  }

  if (mergeIndex === -1) return null;

  // Look for PR number after "merge" (could be an argument without flag)
  for (let i = mergeIndex + 1; i < args.length; i++) {
    const arg = args[i];
    // Skip flags and their values
    if (arg.startsWith("-")) {
      // Skip flag value if it's a flag that takes a value
      // Note: -m is --merge (boolean), not a flag with value
      // Note: -s is --squash (boolean flag), not --strategy
      // gh pr merge does not have --strategy flag
      if (
        (arg === "--body" ||
          arg === "-b" ||
          arg === "--body-file" ||
          arg === "-F" ||
          arg === "-t" ||
          arg === "--subject" ||
          arg === "-R" ||
          arg === "--repo" ||
          arg === "--match-head-commit") &&
        i + 1 < args.length
      ) {
        i++; // Skip the value
      }
      continue;
    }
    // Found a positional argument - could be PR number, URL, or branch name
    // Support "123", "https://github.com/owner/repo/pull/123", or branch names
    const prNumberMatch = arg.match(/^(?:.*\/)?(\d+)$/);
    if (prNumberMatch) {
      return prNumberMatch[1];
    }
    // If not a number pattern, it could be a branch name - return as-is
    // getPrInfo will resolve it via gh pr view
    return arg;
  }

  return null;
}

/**
 * Search for PRs with the given Issue number in title or body.
 * Filters results to ensure exact issue number match (avoids fuzzy match false positives).
 */
function searchPrsByIssueNumber(issueNumber: string, state: "open" | "merged"): PrInfo[] {
  try {
    // Search without "in:title" restriction to include body matches
    // Include body field for accurate filtering
    const result = Bun.spawnSync(
      [
        "gh",
        "pr",
        "list",
        "--state",
        state,
        "--search",
        `#${issueNumber}`,
        "--limit",
        "10",
        "--json",
        "number,title,body,mergedAt",
      ],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (result.exitCode !== 0) {
      return [];
    }

    const prs = JSON.parse(result.stdout.toString()) as PrInfo[];

    // Filter to ensure exact issue number match (GitHub search is fuzzy)
    // e.g., searching for #390 may return PRs mentioning #3903
    return prs.filter((pr) => {
      const fromTitle = extractIssueNumbersFromTitle(pr.title);
      const fromBody = pr.body ? extractIssueNumbersFromBody(pr.body) : [];
      return fromTitle.includes(issueNumber) || fromBody.includes(issueNumber);
    });
  } catch {
    return [];
  }
}

/**
 * Get PR info by number or current branch PR.
 * If prNumber is not provided, uses the current branch's PR.
 */
function getPrInfo(prNumber?: string): PrInfo | null {
  try {
    const args = ["gh", "pr", "view", "--json", "number,title,body,mergedAt"];
    if (prNumber) {
      args.splice(3, 0, prNumber);
    }
    const result = Bun.spawnSync(args, { timeout: TIMEOUT_MEDIUM * 1000 });

    if (result.exitCode !== 0) {
      return null;
    }

    return JSON.parse(result.stdout.toString()) as PrInfo;
  } catch {
    return null;
  }
}

/**
 * Format block message for PR creation when open PR exists.
 */
export function formatOpenPrBlockMessage(issueNumber: string, existingPrs: PrInfo[]): string {
  const lines = [
    `[${HOOK_NAME}] 🚫 同一Issueに対するオープンPRが存在します`,
    "",
    `Issue: #${issueNumber}`,
  ];

  for (const pr of existingPrs) {
    lines.push(`既存PR: #${pr.number} "${pr.title}"`);
  }

  lines.push("");
  lines.push("同じIssueに対して複数のPRを作成しようとしています。");
  lines.push("既存のPRを確認し、必要に応じてそちらに変更を追加してください。");
  lines.push("");
  lines.push("【対処法】");
  lines.push(`1. 既存PRを確認: gh pr view ${existingPrs[0].number}`);
  lines.push("2. 既存PRに変更を追加するか、このPR作成を中止");
  lines.push(
    "3. フォローアップPRの場合はタイトルまたは本文に [followup] / [follow-up] / [revert] / [part ...] を追加して再試行",
  );

  return lines.join("\n");
}

/**
 * Format block message when merged PR exists (for both create and merge).
 */
export function formatMergedPrBlockMessage(
  issueNumber: string,
  mergedPrs: PrInfo[],
  isForMerge: boolean,
  currentPrNumber?: string,
): string {
  const lines = [
    `[${HOOK_NAME}] 🚫 同一Issueに対するマージ済みPRが存在します`,
    "",
    `Issue: #${issueNumber}`,
  ];

  for (const pr of mergedPrs) {
    const mergeDate = pr.mergedAt ? pr.mergedAt.split("T")[0] : "不明";
    lines.push(`マージ済みPR: #${pr.number} "${pr.title}" (${mergeDate})`);
  }

  lines.push("");
  lines.push("この変更は既にマージされています。");
  lines.push("このPRは不要な可能性があります。");
  lines.push("");
  lines.push("【対処法】");
  lines.push(`1. マージ済みPRを確認: gh pr view ${mergedPrs[0].number}`);
  if (isForMerge && currentPrNumber) {
    lines.push(`2. 重複する場合はこのPRをクローズ: gh pr close ${currentPrNumber}`);
  } else {
    lines.push("2. 重複する場合はこのPR作成を中止");
  }
  lines.push(
    "3. フォローアップPRの場合はタイトルまたは本文に [followup] / [follow-up] / [revert] / [part ...] を追加して再試行",
  );

  return lines.join("\n");
}

async function main(): Promise<void> {
  let result: {
    decision?: string;
    reason?: string;
    systemMessage?: string;
  } = {};
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = data.tool_input || {};
    const command = (toolInput.command as string) || "";

    const isPrCreate = isGhPrCreateCommand(command);
    const isPrMerge = isGhPrMergeCommand(command);

    if (!isPrCreate && !isPrMerge) {
      console.log(JSON.stringify(result));
      return;
    }

    let issueNumbers: string[] = [];
    let currentPrNumber: string | null = null;

    if (isPrCreate) {
      // Extract issue numbers from title or body
      const title = extractPrTitle(command);
      const body = extractPrBody(command);

      // Check for followup keywords before duplicate check
      if (hasFollowupKeyword(title || "", body || undefined)) {
        logHookExecution(
          HOOK_NAME,
          "approve",
          "followup PR detected, skipping duplicate check",
          undefined,
          { sessionId },
        );
        console.log(JSON.stringify(result));
        return;
      }

      if (title) {
        const fromTitle = extractIssueNumbersFromTitle(title);
        issueNumbers.push(...fromTitle);
      }

      if (body) {
        const fromBody = extractIssueNumbersFromBody(body);
        issueNumbers.push(...fromBody);
      }

      // Deduplicate
      issueNumbers = [...new Set(issueNumbers)];
    } else if (isPrMerge) {
      // For merge, get PR number from command (or use current branch PR)
      const prNumberFromCommand = extractPrNumberFromMergeCommand(command);

      // Resolve PR info (uses command arg or defaults to current branch)
      const prInfo = getPrInfo(prNumberFromCommand ?? undefined);

      if (prInfo) {
        currentPrNumber = prInfo.number.toString();

        // Check for followup keywords before duplicate check (for merge)
        if (hasFollowupKeyword(prInfo.title, prInfo.body || undefined)) {
          logHookExecution(
            HOOK_NAME,
            "approve",
            "followup PR detected (merge), skipping duplicate check",
            undefined,
            { sessionId },
          );
          console.log(JSON.stringify(result));
          return;
        }

        const fromTitle = extractIssueNumbersFromTitle(prInfo.title);
        issueNumbers.push(...fromTitle);

        if (prInfo.body) {
          const fromBody = extractIssueNumbersFromBody(prInfo.body);
          issueNumbers.push(...fromBody);
        }

        // Deduplicate
        issueNumbers = [...new Set(issueNumbers)];
      }
    }

    if (issueNumbers.length === 0) {
      // No issue number found, skip check
      logHookExecution(HOOK_NAME, "approve", "no issue number found", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Check for each issue number
    for (const issueNumber of issueNumbers) {
      if (isPrCreate) {
        // Check for open PRs first
        const openPrs = searchPrsByIssueNumber(issueNumber, "open");
        if (openPrs.length > 0) {
          result = {
            decision: "block",
            reason: formatOpenPrBlockMessage(issueNumber, openPrs),
          };
          logHookExecution(
            HOOK_NAME,
            "block",
            `open PR exists for issue #${issueNumber}`,
            {
              issue_number: issueNumber,
              open_prs: openPrs.map((p) => p.number),
            },
            { sessionId },
          );
          console.log(JSON.stringify(result));
          return;
        }

        // Check for merged PRs
        const mergedPrs = searchPrsByIssueNumber(issueNumber, "merged");
        if (mergedPrs.length > 0) {
          result = {
            decision: "block",
            reason: formatMergedPrBlockMessage(issueNumber, mergedPrs, false),
          };
          logHookExecution(
            HOOK_NAME,
            "block",
            `merged PR exists for issue #${issueNumber}`,
            {
              issue_number: issueNumber,
              merged_prs: mergedPrs.map((p) => p.number),
            },
            { sessionId },
          );
          console.log(JSON.stringify(result));
          return;
        }
      } else if (isPrMerge) {
        // Check for merged PRs (excluding current PR)
        const mergedPrs = searchPrsByIssueNumber(issueNumber, "merged");
        const otherMergedPrs = currentPrNumber
          ? mergedPrs.filter((p) => p.number.toString() !== currentPrNumber)
          : mergedPrs;

        if (otherMergedPrs.length > 0) {
          result = {
            decision: "block",
            reason: formatMergedPrBlockMessage(
              issueNumber,
              otherMergedPrs,
              true,
              currentPrNumber ?? undefined,
            ),
          };
          logHookExecution(
            HOOK_NAME,
            "block",
            `merged PR exists for issue #${issueNumber}`,
            {
              issue_number: issueNumber,
              merged_prs: otherMergedPrs.map((p) => p.number),
              current_pr: currentPrNumber,
            },
            { sessionId },
          );
          console.log(JSON.stringify(result));
          return;
        }
      }
    }

    // No duplicates found
    logHookExecution(
      HOOK_NAME,
      "approve",
      "no duplicate PRs found",
      {
        issue_numbers: issueNumbers,
      },
      { sessionId },
    );
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    logHookExecution(HOOK_NAME, "approve", `error: ${formatError(error)}`, undefined, {
      sessionId,
    });
    result = {};
  }

  console.log(JSON.stringify(result));
}

// Only run when executed directly, not when imported for testing
if (import.meta.main) {
  main();
}
