#!/usr/bin/env bun
/**
 * CodeRabbitのactionable commentsをマージ前にチェックする。
 *
 * Why:
 *   CodeRabbitが「Actionable comments」や「Outside diff range comments」として
 *   改善提案を出しても、マージ前に気づく仕組みがなく見落とされていた。
 *
 * What:
 *   - gh pr mergeコマンドを検出
 *   - CodeRabbitのレビューコメントをGitHub APIで取得
 *   - 「Actionable comments posted: N」(N>0)を検出
 *   - 「Outside diff range comments (N)」(N>0)を検出
 *   - 検出時はブロックし、対応を促す
 *
 * Remarks:
 *   - ブロック型フック（未対応のactionable comments時はブロック）
 *   - PreToolUse:Bashで発火（gh pr mergeコマンド）
 *   - SKIP_CODERABBIT_REVIEW=1でバイパス可能
 *
 * Changelog:
 *   - silenvx/dekita#3167: フック追加
 */

import { formatError } from "../lib/format_error";
import { getCurrentBranch } from "../lib/git";
import { extractPrNumber, getPrNumberForBranch, parseGhPrCommand } from "../lib/github";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { checkSkipEnv } from "../lib/strings";

const HOOK_NAME = "coderabbit-review-check";
const SKIP_ENV = "SKIP_CODERABBIT_REVIEW";
const CODERABBIT_BOT = "coderabbitai[bot]";

/** Pattern to match "Actionable comments posted: N" where N > 0 (optional markdown bold) */
const ACTIONABLE_COMMENTS_PATTERN = /(?:\*\*)?Actionable comments posted:\s*(\d+)(?:\*\*)?/;

/** Pattern to match "Outside diff range comments (N)" where N > 0 */
const OUTSIDE_DIFF_PATTERN = /Outside diff range comments\s*\((\d+)\)/;

/**
 * Check if command is a gh pr merge command.
 */
export function isGhPrMergeCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  const [subcommand] = parseGhPrCommand(command);
  return subcommand === "merge";
}

/**
 * Extract PR number from command or get from current branch.
 */
export async function getPrNumber(command: string): Promise<string | null> {
  // Try to extract from command first
  const prFromCommand = extractPrNumber(command);
  if (prFromCommand) {
    return prFromCommand;
  }

  // Fallback to current branch
  const branch = await getCurrentBranch();
  if (branch && branch !== "main" && branch !== "master") {
    return await getPrNumberForBranch(branch);
  }

  return null;
}

/** Review object from CodeRabbit API */
type Review = { body: string; submitted_at: string };

/**
 * Parse JSONL output robustly. Issue #3186: Made parsing robust against format variations.
 * Continues parsing even if some lines fail (more resilient than breaking on first error).
 */
export function parseJsonlOutput(output: string): Array<Review> {
  const trimmed = output.trim();
  if (!trimmed) {
    return [];
  }

  const results: Array<Review> = [];

  for (const line of trimmed.split("\n")) {
    const trimmedLine = line.trim();
    if (!trimmedLine) continue;

    try {
      const parsed = JSON.parse(trimmedLine);
      // Validate object structure: must be non-null object (not array) with required properties
      if (
        parsed &&
        typeof parsed === "object" &&
        !Array.isArray(parsed) &&
        typeof parsed.body === "string" &&
        typeof parsed.submitted_at === "string"
      ) {
        results.push(parsed as Review);
      }
    } catch {
      // Skip lines that fail to parse - this is intentional for robustness
      // Invalid lines are expected when API output format varies
    }
  }

  return results;
}

/**
 * Fetch CodeRabbit reviews for a PR using Bun.spawn.
 *
 * Uses --jq to filter and output JSONL (one JSON object per line).
 * This handles pagination correctly: gh api --paginate with --jq outputs
 * each item on a separate line, avoiding the issue where multiple pages
 * produce concatenated JSON arrays (e.g., `[...][...]`) that JSON.parse can't handle.
 */
export async function fetchCodeRabbitReviews(prNumber: string): Promise<Array<Review>> {
  try {
    // Use --jq to filter server-side and output JSONL
    // This is more reliable than JSON.parse on --paginate output which can produce `[...][...]`
    const proc = Bun.spawn(
      [
        "gh",
        "api",
        `repos/:owner/:repo/pulls/${prNumber}/reviews`,
        "--paginate",
        "--jq",
        `.[] | select(.user != null and .user.login == "${CODERABBIT_BOT}" and .state != "DISMISSED") | {body: (.body // ""), submitted_at: .submitted_at}`,
      ],
      {
        stdout: "pipe",
        stderr: "pipe",
      },
    );

    const stdout = await new Response(proc.stdout).text();
    const exitCode = await proc.exited;

    if (exitCode !== 0) {
      throw new Error(`gh api failed with exit code ${exitCode}`);
    }

    return parseJsonlOutput(stdout);
  } catch {
    return [];
  }
}

/**
 * Parse actionable comments count from review body.
 */
export function parseActionableComments(body: string): {
  actionableCount: number;
  outsideDiffCount: number;
} {
  let actionableCount = 0;
  let outsideDiffCount = 0;

  const actionableMatch = body.match(ACTIONABLE_COMMENTS_PATTERN);
  if (actionableMatch) {
    actionableCount = Number.parseInt(actionableMatch[1], 10);
  }

  const outsideDiffMatch = body.match(OUTSIDE_DIFF_PATTERN);
  if (outsideDiffMatch) {
    outsideDiffCount = Number.parseInt(outsideDiffMatch[1], 10);
  }

  return { actionableCount, outsideDiffCount };
}

/**
 * Check if there are unaddressed CodeRabbit comments.
 * Uses the latest review only, as CodeRabbit posts a new review on each push
 * with cumulative actionable comment counts. Aggregating all reviews would
 * cause double-counting and block merges even after issues are fixed.
 * Issue #3182: Investigated scan-all approach but reverted due to overcounting.
 */
export async function checkCodeRabbitComments(
  prNumber: string,
): Promise<{ actionableCount: number; outsideDiffCount: number } | null> {
  const reviews = await fetchCodeRabbitReviews(prNumber);

  if (reviews.length === 0) {
    return null;
  }

  // Get the latest review (CodeRabbit posts new review on each push with cumulative counts)
  const latestReview =
    reviews.length === 1
      ? reviews[0]
      : reviews.toSorted(
          (a, b) => new Date(b.submitted_at).getTime() - new Date(a.submitted_at).getTime(),
        )[0];

  const { actionableCount, outsideDiffCount } = parseActionableComments(latestReview.body);

  if (actionableCount > 0 || outsideDiffCount > 0) {
    return { actionableCount, outsideDiffCount };
  }

  return null;
}

/**
 * Format the block message.
 */
export function formatBlockMessage(
  prNumber: string,
  actionableCount: number,
  outsideDiffCount: number,
  command: string,
): string {
  const lines: string[] = ["🐰 CodeRabbitの未対応コメントがあります", "", `**PR #${prNumber}**`];

  if (actionableCount > 0) {
    lines.push(`- Actionable comments: ${actionableCount}件`);
  }

  if (outsideDiffCount > 0) {
    lines.push(`- Outside diff range comments: ${outsideDiffCount}件`);
  }

  lines.push(
    "",
    "**対応方法:**",
    "1. PRのCodeRabbitレビューを確認",
    "2. 指摘に対応するか、対応不要な理由をコメント",
    "3. 対応後、再度マージを実行",
    "",
    `**確認:** gh pr view ${prNumber} --web`,
    "",
    "**スキップ:** 対応不要な場合は以下で一時的にバイパス可能",
    "```bash",
    `SKIP_CODERABBIT_REVIEW=1 ${command}`,
    "```",
  );

  return lines.join("\n");
}

interface HookResult {
  decision?: string;
  reason?: string;
  systemMessage?: string;
}

async function main(): Promise<void> {
  let result: HookResult = {};
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = data.tool_input || {};
    const command = (toolInput as { command?: string }).command || "";

    // Check skip environment variable
    if (checkSkipEnv(HOOK_NAME, SKIP_ENV, { input_preview: command })) {
      result.systemMessage = `✅ ${HOOK_NAME}: SKIP_CODERABBIT_REVIEW=1 でスキップ`;
      logHookExecution(HOOK_NAME, "approve", "Skipped via environment variable", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    if (isGhPrMergeCommand(command)) {
      const prNumber = await getPrNumber(command);

      if (!prNumber) {
        // Cannot determine PR number, approve to avoid blocking
        result.systemMessage = `⚠️ ${HOOK_NAME}: PR番号を特定できませんでした`;
        logHookExecution(HOOK_NAME, "approve", "Could not determine PR number", undefined, {
          sessionId,
        });
        console.log(JSON.stringify(result));
        return;
      }

      const comments = await checkCodeRabbitComments(prNumber);

      if (comments) {
        const reason = formatBlockMessage(
          prNumber,
          comments.actionableCount,
          comments.outsideDiffCount,
          command,
        );
        result = makeBlockResult(HOOK_NAME, reason);
      } else {
        result.systemMessage = `✅ ${HOOK_NAME}: CodeRabbitの未対応コメントなし`;
      }
    }
  } catch (e) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(e)}`);
    result = {};
  }

  // Log only for non-block decisions (makeBlockResult logs automatically)
  if (result.decision !== "block") {
    logHookExecution(HOOK_NAME, result.decision ?? "approve", result.reason, undefined, {
      sessionId,
    });
  }
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
