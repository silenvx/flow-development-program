#!/usr/bin/env bun
/**
 * PRメトリクス自動収集フック（PostToolUse）
 *
 * Why:
 *   PRのメトリクス（サイクルタイム、レビュー数等）を自動収集し、
 *   開発プロセスの改善に役立てる。
 *
 * What:
 *   - gh pr merge コマンドの成功を検出
 *   - PR番号を抽出
 *   - GitHub APIでメトリクスを収集・ログに記録
 *
 * State:
 *   - reads: GitHub API (gh pr view)
 *   - writes: .claude/logs/execution/hook-execution-*.jsonl
 *
 * Remarks:
 *   - 非ブロック型（PostToolUse）
 *   - メトリクス収集失敗時もブロックしない
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2203: get_exit_code統一
 *   - silenvx/dekita#3160: TypeScript移行
 *   - silenvx/dekita#3649: Pythonスクリプト削除、TypeScriptネイティブ実装に移行
 */

import { PROJECT_DIR } from "../lib/common";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { extractPrNumber } from "../lib/github";
import { logHookExecution } from "../lib/logging";
import { createHookContext, parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";

const HOOK_NAME = "pr-metrics-collector";

// 60 seconds timeout for large PRs with many reviews (matches original Python script)
const METRICS_FETCH_TIMEOUT = 60000;

// =============================================================================
// PR Number Extraction
// =============================================================================

/**
 * Get PR number for current branch.
 */
async function getCurrentBranchPr(): Promise<number | null> {
  try {
    const result = await asyncSpawn("gh", ["pr", "view", "--json", "number"], {
      timeout: TIMEOUT_MEDIUM * 1000,
    });

    if (result.success) {
      const data = JSON.parse(result.stdout);
      return data.number ?? null;
    }
  } catch {
    // PR number fetch failed
  }
  return null;
}

// =============================================================================
// Metrics Collection
// =============================================================================

interface CiCheckStats {
  passed: number;
  failed: number;
  total: number;
}

interface PrMetrics {
  number: number;
  title: string;
  createdAt: string;
  mergedAt: string | null;
  additions: number;
  deletions: number;
  changedFiles: number;
  uniqueReviewerCount: number;
  cycleTimeHours: number | null;
  ciChecks: CiCheckStats;
}

/**
 * Calculate cycle time in hours between PR creation and merge.
 */
function calculateCycleTimeHours(createdAt: string, mergedAt: string | null): number | null {
  if (!mergedAt) {
    return null;
  }
  try {
    const created = new Date(createdAt);
    const merged = new Date(mergedAt);
    const diffMs = merged.getTime() - created.getTime();
    return Math.round((diffMs / (1000 * 60 * 60)) * 100) / 100; // Round to 2 decimal places
  } catch {
    return null;
  }
}

interface ReviewAuthor {
  login?: string;
  is_bot?: boolean;
  __typename?: string;
  type?: string;
}

/**
 * Check if a review author is a bot.
 * GitHub CLI returns __typename: "Bot" for bot accounts.
 */
function isBot(author: ReviewAuthor | undefined): boolean {
  if (!author) {
    return false;
  }
  // Check multiple bot indicators for compatibility
  return (
    author.is_bot === true ||
    author.__typename === "Bot" ||
    author.type === "Bot" ||
    (author.login?.endsWith("[bot]") ?? false)
  );
}

/**
 * Count unique reviewers from reviews array.
 * Excludes bots and counts each human reviewer only once.
 */
function countUniqueReviewers(reviews: Array<{ author?: ReviewAuthor }>): number {
  if (!Array.isArray(reviews)) {
    return 0;
  }
  const uniqueLogins = new Set<string>();
  for (const review of reviews) {
    const login = review.author?.login;
    if (login && !isBot(review.author)) {
      uniqueLogins.add(login);
    }
  }
  return uniqueLogins.size;
}

/**
 * Parse CI check statistics from statusCheckRollup.
 * GitHub CLI returns check runs with different fields depending on the source:
 * - Check runs: conclusion (SUCCESS, FAILURE, etc.)
 * - Commit statuses: state (success, failure, etc.)
 * - Status checks: status (COMPLETED, PENDING, etc.)
 */
function parseCiChecks(
  statusCheckRollup: Array<{ conclusion?: string; state?: string; status?: string }> | undefined,
): CiCheckStats {
  if (!Array.isArray(statusCheckRollup)) {
    return { passed: 0, failed: 0, total: 0 };
  }

  let passed = 0;
  let failed = 0;

  for (const check of statusCheckRollup) {
    const conclusion = check.conclusion?.toLowerCase();
    const state = check.state?.toLowerCase();
    // Note: check.status indicates check state (completed, pending), not result
    // For completed checks, we rely on conclusion/state for the result

    // Success indicators
    if (conclusion === "success" || state === "success") {
      passed++;
    }
    // Failure indicators
    else if (
      conclusion === "failure" ||
      state === "failure" ||
      conclusion === "error" ||
      state === "error"
    ) {
      failed++;
    }
  }

  return { passed, failed, total: statusCheckRollup.length };
}

/**
 * Collect PR metrics using GitHub API.
 * Uses PROJECT_DIR as cwd to ensure repo context is available.
 */
async function collectPrMetrics(prNumber: number): Promise<PrMetrics | null> {
  try {
    const result = await asyncSpawn(
      "gh",
      [
        "pr",
        "view",
        String(prNumber),
        "--json",
        "number,title,createdAt,mergedAt,additions,deletions,changedFiles,reviews,statusCheckRollup",
      ],
      {
        timeout: METRICS_FETCH_TIMEOUT,
        cwd: PROJECT_DIR, // Ensure repo context is available
      },
    );

    if (!result.success) {
      return null;
    }

    const data = JSON.parse(result.stdout);
    return {
      number: data.number,
      title: data.title,
      createdAt: data.createdAt,
      mergedAt: data.mergedAt,
      additions: data.additions,
      deletions: data.deletions,
      changedFiles: data.changedFiles,
      uniqueReviewerCount: countUniqueReviewers(data.reviews),
      cycleTimeHours: calculateCycleTimeHours(data.createdAt, data.mergedAt),
      ciChecks: parseCiChecks(data.statusCheckRollup),
    };
  } catch {
    return null;
  }
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    const input = await parseHookInput();
    const ctx = createHookContext(input);
    sessionId = ctx.sessionId;
    const toolName = input.tool_name ?? "";
    const toolInput = input.tool_input as Record<string, unknown> | undefined;
    const toolResult = input.tool_result as Record<string, unknown> | undefined;

    // Skip non-Bash tools
    if (toolName !== "Bash") {
      console.log(JSON.stringify(result));
      return;
    }

    const command = (toolInput?.command as string) ?? "";

    // Check for gh pr merge command
    if (!command.includes("gh pr merge")) {
      console.log(JSON.stringify(result));
      return;
    }

    // Check if command succeeded
    const exitCode = (toolResult?.exit_code as number) ?? 0;
    if (exitCode !== 0) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "Merge command failed, skipping metrics collection",
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Extract PR number from command
    const prNumberStr = extractPrNumber(command);
    let prNumber: number | null = prNumberStr ? Number.parseInt(prNumberStr, 10) : null;

    // Try to get from current branch if not found in command
    if (prNumber === null) {
      prNumber = await getCurrentBranchPr();
    }

    if (prNumber === null) {
      await logHookExecution(HOOK_NAME, "approve", "Could not determine PR number", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Collect metrics
    const metrics = await collectPrMetrics(prNumber);

    if (metrics) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `PR #${prNumber} metrics collected`,
        {
          pr_number: prNumber,
          success: true,
          metrics,
        },
        { sessionId },
      );
      result.systemMessage = `PR #${prNumber} メトリクスを記録しました`;
    } else {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `PR #${prNumber} metrics collection failed`,
        {
          pr_number: prNumber,
          success: false,
        },
        { sessionId },
      );
    }
  } catch (error) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(error)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch(console.error);
}
