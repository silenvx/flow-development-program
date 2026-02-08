#!/usr/bin/env bun
/**
 * AIレビュー未対応コメント検知・Issue自動作成フック
 *
 * Why:
 *   AIレビュー（CodeRabbit, Copilot, Gemini等）の指摘が対応されずにマージされると、
 *   潜在的な問題が見落とされる。マージ前に検知してIssue化することで追跡可能にする。
 *
 * What:
 *   - gh pr merge コマンドを検出（PreToolUse:Bash）
 *   - PRのAIレビューコメントを取得
 *   - 未対応のactionableコメントを検出
 *   - 検出時は自動でIssueを作成し、マージを許可
 *
 * Remarks:
 *   - 非ブロック型（Issue作成後にマージを許可）
 *   - 対象AIレビュアー: coderabbitai[bot], copilot-pull-request-reviewer[bot], github-actions[bot] (Gemini/Codex)
 *   - 除外: LGTM系、resolvedスレッド
 *
 * Changelog:
 *   - silenvx/dekita#3168: 初期実装
 *   - silenvx/dekita#3190: チェーンコマンド対応（&& ; で分割して各部分をチェック）
 */

import { execSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { getRepoOwnerAndName } from "../lib/check_utils";
import { TIMEOUT_HEAVY } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { extractPrNumber, extractRepoOption, parseGhPrCommand } from "../lib/github";
import { logHookExecution } from "../lib/logging";
import { approveAndExit } from "../lib/results";
import { createContext, getSessionId, parseHookInput } from "../lib/session";
import { escapeShellArg } from "../lib/shell_tokenizer";
import { asyncSpawn } from "../lib/spawn";
import { checkSkipEnv } from "../lib/strings";

const HOOK_NAME = "ai-review-followup-check";
const SKIP_ENV = "SKIP_AI_REVIEW_FOLLOWUP";
/** Timeout in milliseconds (using TIMEOUT_HEAVY from constants, which is 30 seconds) */
const TIMEOUT_MS = TIMEOUT_HEAVY * 1000;

/**
 * Output structured JSON log to stderr
 * Satisfies Qodo's "Secure Logging Practices" compliance requirement
 */
function logWarn(message: string, context?: Record<string, unknown>): void {
  const logEntry = {
    level: "warn",
    hook: HOOK_NAME,
    message,
    timestamp: new Date().toISOString(),
    ...context,
  };
  console.warn(JSON.stringify(logEntry));
}

/**
 * AI reviewers to check
 *
 * Note: Detection relies on CodeRabbit-style "Actionable comments posted: N" format.
 * Copilot uses a different format (inline comments only), so detection is limited.
 * Future: Add Copilot-specific pattern detection (Issue #3174 or separate).
 */
// Store as lowercase for case-insensitive comparison
const AI_REVIEWERS = new Set([
  "coderabbitai[bot]",
  "copilot-pull-request-reviewer[bot]", // Limited: only detects if using CodeRabbit-style format
  "github-actions[bot]", // Gemini/Codex reviews via Actions (must use CodeRabbit format)
]);

/**
 * Patterns indicating actionable comments (CodeRabbit specific)
 *
 * Note: Patterns are designed to avoid overlap/double counting:
 * - Pattern 1: "Actionable comments posted: N" with count in capture group
 * - Pattern 2: "Outside diff range comments (N)" with count in capture group
 * Both require an explicit count to avoid ambiguous matches.
 */
const ACTIONABLE_PATTERNS = [
  /Actionable comments posted:\s*(\d+)/i,
  /Outside diff range comments?\s*\((\d+)\)/i,
];

/**
 * Required labels for follow-up issues
 */
const REQUIRED_LABELS = [
  { name: "ai-review-followup", description: "AIレビューの未対応指摘", color: "fbca04" },
  { name: "P3", description: "Priority 3 (Low)", color: "0e8a16" },
];

interface ReviewComment {
  reviewer: string;
  body: string;
  actionableCount: number;
  actionableDetails: string[];
}

interface UnresolvedThreadInfo {
  /** Whether the GraphQL API call succeeded */
  success: boolean;
  /** Total count of unresolved threads from AI reviewers */
  totalCount: number;
  byReviewer: Map<string, number>;
}

/** GraphQL response type definitions for review threads query */
interface GraphQLReviewThreadsResponse {
  errors?: Array<{ message: string }>;
  data?: {
    repository?: {
      pullRequest?: {
        reviewThreads?: {
          pageInfo?: { hasNextPage: boolean };
          nodes?: Array<{
            isResolved: boolean;
            comments?: {
              nodes?: Array<{
                author?: { login?: string };
              }>;
            };
          }>;
        };
      };
    };
  };
}

/**
 * Get unresolved review thread count from GraphQL API
 *
 * The static review summary ("Actionable comments posted: N") doesn't update
 * when threads are resolved. This function fetches actual unresolved thread
 * count to prevent false positives.
 *
 * Note: This function fetches only the first 100 threads. For PRs with >100 threads,
 * it returns success=false and falls back to static summary to avoid missing threads.
 * This is a design decision to keep the implementation simple while ensuring correctness.
 */
export async function getUnresolvedThreadCount(
  prNumber: string,
  repoOption?: string,
): Promise<UnresolvedThreadInfo> {
  const result: UnresolvedThreadInfo = {
    success: false,
    totalCount: 0,
    byReviewer: new Map(),
  };

  try {
    // Get owner and repo - use explicit repo if provided, otherwise detect from current directory
    let owner: string;
    let repoName: string;

    if (repoOption) {
      // Parse "owner/repo" format
      const parts = repoOption.split("/");
      if (parts.length !== 2 || !parts[0] || !parts[1]) {
        logWarn("Invalid repo format", { repoOption });
        return result;
      }
      [owner, repoName] = parts;
    } else {
      // Fallback to current repo detection
      const repoInfo = await getRepoOwnerAndName();
      if (!repoInfo) {
        logWarn("Failed to get repository info");
        return result;
      }
      [owner, repoName] = repoInfo;
    }

    // GraphQL query to fetch review threads with their resolved status
    // Uses asyncSpawn to avoid shell injection and blocking the event loop
    const query = `
      query($owner: String!, $repo: String!, $pr: Int!) {
        repository(owner: $owner, name: $repo) {
          pullRequest(number: $pr) {
            reviewThreads(first: 100) {
              pageInfo {
                hasNextPage
              }
              nodes {
                isResolved
                comments(first: 1) {
                  nodes {
                    author { login }
                  }
                }
              }
            }
          }
        }
      }
    `;

    // Use asyncSpawn with array args to avoid shell injection
    const spawnResult = await asyncSpawn(
      "gh",
      [
        "api",
        "graphql",
        "-f",
        `owner=${owner}`,
        "-f",
        `repo=${repoName}`,
        "-F",
        `pr=${prNumber}`,
        "-f",
        `query=${query.replace(/\n/g, " ").trim()}`,
      ],
      { timeout: TIMEOUT_MS },
    );

    if (!spawnResult.success) {
      logWarn("GraphQL query failed", { prNumber, error: spawnResult.stderr });
      return result;
    }

    const data = JSON.parse(spawnResult.stdout) as GraphQLReviewThreadsResponse;

    // Fail-open on GraphQL errors with warning log
    if (data.errors) {
      logWarn("GraphQL errors", {
        prNumber,
        errors: data.errors.map((e) => e.message),
      });
      return result;
    }

    if (!data.data?.repository?.pullRequest) {
      logWarn("PR not found or no access", { prNumber });
      return result;
    }

    const reviewThreads = data.data.repository.pullRequest.reviewThreads;

    // Fail-open if there are more than 100 threads (pagination needed)
    // Log a warning for visibility in case the original problem reoccurs
    if (reviewThreads?.pageInfo?.hasNextPage) {
      logWarn("PR has >100 threads, pagination not supported. Falling back to static summary.", {
        prNumber,
      });
      return result;
    }

    const threads = reviewThreads?.nodes ?? [];
    for (const thread of threads) {
      if (!thread.isResolved) {
        const author = thread.comments?.nodes?.[0]?.author?.login?.toLowerCase() ?? "";
        if (AI_REVIEWERS.has(author)) {
          result.totalCount++;
          result.byReviewer.set(author, (result.byReviewer.get(author) ?? 0) + 1);
        }
      }
    }

    // Mark as successful - we got valid data from GraphQL
    result.success = true;
  } catch (error) {
    // Log the error for debugging and fail-open
    logWarn("Failed to get unresolved thread count", {
      prNumber,
      error: error instanceof Error ? error.message : String(error),
    });
  }

  return result;
}

/**
 * Split command string by chain operators (&& and ;)
 * Returns array of individual commands
 */
export function splitChainedCommands(command: string): string[] {
  // Split by && or ; while preserving the ability to detect each command
  // Note: This simple split doesn't handle quoted strings containing && or ;
  // but that's acceptable for our use case (gh commands don't typically have these)
  return command
    .split(/\s*(?:&&|;)\s*/)
    .map((cmd) => cmd.trim())
    .filter((cmd) => cmd.length > 0);
}

/**
 * Check if command is a PR merge command
 * Handles chained commands like "gh pr view && gh pr merge"
 */
export function isPrMergeCommand(command: string): boolean {
  if (!command.trim() || command.includes("--help")) return false;

  return splitChainedCommands(command).some((cmd) => {
    const [subcommand] = parseGhPrCommand(cmd);
    return subcommand === "merge";
  });
}

/**
 * Check if skip environment variable is set
 */
export function hasSkipEnv(command: string): boolean {
  return checkSkipEnv(HOOK_NAME, SKIP_ENV, { input_preview: command });
}

/**
 * Fetch PR reviews from GitHub API
 *
 * @param prNumber - The PR number to fetch reviews for
 * @param repo - Optional repository in "owner/repo" format. If not specified, uses current repo.
 */
async function getPrReviews(prNumber: string, repo?: string): Promise<ReviewComment[]> {
  const results: ReviewComment[] = [];

  try {
    // Build the API path - use explicit repo if provided, otherwise use :owner/:repo placeholder
    // Quote the path to prevent command injection with special characters
    const repoPath = repo ? `repos/${repo}` : "repos/:owner/:repo";
    // Use --paginate to fetch all reviews (default is 30 per page).
    // Use map(...) to wrap results in a single JSON array for safe parsing.
    // Use null coalescing (.body // "") to handle reviews with null body.
    const output = execSync(
      `gh api "${repoPath}/pulls/${prNumber}/reviews" --paginate --jq 'map({user: .user.login, body: (.body // "")})'`,
      {
        encoding: "utf-8",
        timeout: TIMEOUT_MS,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );

    // Parse the full JSON array
    const reviews = JSON.parse(output.trim() || "[]") as Array<{ user: string; body: string }>;

    // Keep only reviews with actionable content for each AI reviewer.
    // The API returns reviews in chronological order.
    // Important: CodeRabbit posts a main review with summary, then subsequent
    // reviews with empty bodies (inline-comment wrappers). We must not let
    // empty reviews overwrite the summary containing actionable counts.
    const latestReviews = new Map<string, string>();
    for (const review of reviews) {
      const userLower = review.user.toLowerCase();
      if (AI_REVIEWERS.has(userLower)) {
        const hasSummary = ACTIONABLE_PATTERNS.some((p) => p.test(review.body));
        // Update if: (1) has actionable summary, or (2) first non-empty review
        if (hasSummary || (review.body.trim() && !latestReviews.has(userLower))) {
          latestReviews.set(userLower, review.body);
        }
      }
    }

    for (const [reviewer, body] of latestReviews) {
      const { count, details } = analyzeReviewBody(body);
      if (count > 0) {
        results.push({
          reviewer,
          body,
          actionableCount: count,
          actionableDetails: details,
        });
      }
    }

    // Filter out reviewers whose threads are all resolved
    // The static summary ("Actionable comments posted: N") doesn't update
    // when threads are resolved, causing false positives
    if (results.length > 0) {
      const unresolvedInfo = await getUnresolvedThreadCount(prNumber, repo);

      // Fail-open: if GraphQL API failed, skip filtering and return original results
      // This ensures we don't miss actionable comments due to API issues
      // Note: All failure cases in getUnresolvedThreadCount log warnings (repo info, query, errors, etc.)
      if (!unresolvedInfo.success) {
        return results;
      }

      // GraphQL succeeded - filter and transform based on actual unresolved thread count
      // Use reduce to create new objects (immutable) instead of mutating in filter
      const filtered = results.reduce<ReviewComment[]>((acc, r) => {
        const actualCount = unresolvedInfo.byReviewer.get(r.reviewer) ?? 0;
        if (actualCount > 0) {
          // Create new object with updated count (immutable)
          acc.push({
            ...r,
            actionableCount: actualCount,
            actionableDetails: [`Unresolved threads: ${actualCount}`],
          });
        }
        // Skip reviewers with all threads resolved
        return acc;
      }, []);
      return filtered;
    }
  } catch (error) {
    // Log the error for debugging and return empty (fail-open)
    logWarn("Error fetching PR reviews", {
      error: error instanceof Error ? error.message : String(error),
    });
  }

  return results;
}

/**
 * Analyze review body for actionable comments
 *
 * Note: The regex matches 0 as well to recognize zero-count summaries.
 * This allows "Actionable comments posted: 0" to overwrite older summaries
 * with non-zero counts, correctly reflecting resolved states.
 * The logic filters out count=0 when building results.
 */
export function analyzeReviewBody(body: string): { count: number; details: string[] } {
  const details: string[] = [];
  let totalCount = 0;

  for (const pattern of ACTIONABLE_PATTERNS) {
    const match = body.match(pattern);
    if (match) {
      totalCount += match[1] ? Number.parseInt(match[1], 10) : 1;
      details.push(match[0]);
    }
  }

  return { count: totalCount, details };
}

/**
 * Check if a follow-up Issue already exists for the given PR
 * Returns the existing Issue number if found, null otherwise
 *
 * @param prNumber - The PR number to search for
 * @param repo - Optional repository in "owner/repo" format. If not specified, uses current repo.
 */
function findExistingFollowupIssue(prNumber: string, repo?: string): string | null {
  try {
    // Quote repo to prevent command injection with special characters
    const repoOption = repo ? `--repo "${repo}"` : "";
    // Search by title only without label filter (#3257: detect issues created without labels)
    const output = execSync(
      `gh issue list ${repoOption} --search '[AI Review] PR #${prNumber} in:title' --state open --json number --jq '.[0].number // empty'`,
      {
        encoding: "utf-8",
        timeout: TIMEOUT_MS,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    const issueNumber = output.trim();
    return issueNumber && /^\d+$/.test(issueNumber) ? issueNumber : null;
  } catch (error) {
    // Log the error for debugging (fail-open: return null on error)
    logWarn("Error searching for existing followup issue", {
      prNumber,
      error: error instanceof Error ? error.message : String(error),
    });
    return null;
  }
}

/**
 * Create follow-up Issue for unaddressed AI review comments
 * Returns existing Issue number if one already exists (idempotent)
 *
 * If label creation fails, retries without labels to ensure the issue is created.
 * This function executes synchronously (no async operations).
 *
 * Label creation is deferred until after the existence check to avoid
 * unnecessary network calls when the issue already exists.
 *
 * @param prNumber - The PR number to create an issue for
 * @param reviews - The list of reviews with actionable comments
 * @param repo - Optional repository in "owner/repo" format. If not specified, uses current repo.
 */
function createFollowupIssue(
  prNumber: string,
  reviews: ReviewComment[],
  repo?: string,
): string | null {
  // Check for existing Issue to avoid duplicates (before expensive label operations)
  const existingIssue = findExistingFollowupIssue(prNumber, repo);
  if (existingIssue) {
    return existingIssue;
  }

  // Try to create required labels and get list of available labels
  // This is done after the existence check to avoid unnecessary network calls
  const availableLabels = tryCreateLabels(repo);

  const reviewerSummary = reviews
    .map((r) => `- **${r.reviewer}**: ${r.actionableCount}件 (${r.actionableDetails.join(", ")})`)
    .join("\n");

  // Base issue body
  const baseIssueBody = `## Why

PR #${prNumber} のAIレビューで未対応の指摘があります。マージ前にIssue化して追跡します。

## What

### 現状/実際の動作

以下のAIレビュアーから未対応の指摘があります:

${reviewerSummary}

### 理想の状態

指摘事項を確認し、必要に応じて対応する。

## How

1. PR #${prNumber} のレビューコメントを確認
2. 各指摘について対応要否を判断
3. 対応が必要な場合は別PRで修正

## Dependencies
- **Related**: #${prNumber}
`;

  // Escape repo to prevent command injection with special characters
  const repoOption = repo ? `--repo "${escapeShellArg(repo)}"` : "";

  // Build label options from available labels (escape each label name)
  const labelOptions = availableLabels
    .map((label) => `--label "${escapeShellArg(label)}"`)
    .join(" ");

  // Use a temp file for body content (gh cli --body-file doesn't support stdin)
  const tempDir = mkdtempSync(join(tmpdir(), "ai-review-followup-"));
  const bodyFile = join(tempDir, "body.md");

  // Pre-compute required label names for efficiency
  const requiredLabelNames = REQUIRED_LABELS.map((l) => l.name);

  /**
   * Helper to create issue and extract issue number from output
   */
  const tryCreateIssue = (body: string, labels: string): string | null => {
    try {
      writeFileSync(bodyFile, body, "utf-8");
    } catch (error) {
      logWarn("Failed to write issue body to temp file", { error: String(error) });
      return null;
    }
    const labelPart = labels ? ` ${labels}` : "";
    // Escape prNumber for defense-in-depth (even though it's validated as numeric)
    const escapedPrNumber = escapeShellArg(prNumber);
    const output = execSync(
      `gh issue create ${repoOption} --title "[AI Review] PR #${escapedPrNumber} の未対応指摘"${labelPart} --body-file "${bodyFile}"`,
      {
        encoding: "utf-8",
        timeout: TIMEOUT_MS,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    // Use a more flexible regex to support GitHub Enterprise (not just github.com)
    // The output from `gh issue create` is the issue URL, so false positives are unlikely
    const match = output.match(/\/issues\/(\d+)/);
    if (!match) {
      logWarn("Issue created but failed to parse issue number from output", {
        output: output.trim(),
      });
    }
    return match ? match[1] : null;
  };

  // Helper to create missing labels note
  const createMissingLabelsNote = (appliedLabels: string[]): string => {
    const missing = requiredLabelNames.filter((name) => !appliedLabels.includes(name));
    return missing.length > 0
      ? `\n\n---\n**Note**: ラベル (${missing.join(", ")}) を付与できませんでした。手動で追加してください。`
      : "";
  };

  try {
    // Attempt to create issue with available labels
    if (availableLabels.length > 0) {
      try {
        // Add note about missing labels if not all are available
        const bodyWithNote = baseIssueBody + createMissingLabelsNote(availableLabels);
        const result = tryCreateIssue(bodyWithNote, labelOptions);
        // Return result even if null - command succeeded, don't retry and create duplicates
        return result;
      } catch (error) {
        // Label application failed, log and fall through to retry without labels
        console.error(`[${HOOK_NAME}] Label-applied issue creation failed: ${formatError(error)}`);
      }
    }

    // Fallback: Create issue without labels - all required labels will be missing
    const allMissingNote = createMissingLabelsNote([]);
    return tryCreateIssue(baseIssueBody + allMissingNote, "");
  } catch (error) {
    // Log the error for debugging (fail-open: return null on error)
    logWarn("Error creating followup issue", {
      prNumber,
      error: error instanceof Error ? error.message : String(error),
    });
    return null;
  } finally {
    // Cleanup temp file
    try {
      rmSync(tempDir, { recursive: true });
    } catch {
      // Ignore cleanup errors
    }
  }
}

/**
 * Check if a label exists in the repository
 *
 * @param labelName - The label name to check
 * @param repo - Optional repository in "owner/repo" format
 * @returns true if the label exists
 */
function labelExists(labelName: string, repo?: string): boolean {
  try {
    const repoOption = repo ? `--repo "${escapeShellArg(repo)}"` : "";
    const escapedLabel = escapeShellArg(labelName);
    const output = execSync(`gh label list ${repoOption} --search "${escapedLabel}" --json name`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MS,
      stdio: ["pipe", "pipe", "pipe"],
    });
    // Parse JSON to handle exact matching (search returns partial matches)
    const labels = JSON.parse(output.trim() || "[]") as Array<{ name: string }>;
    return labels.some((l) => l.name === labelName);
  } catch (error) {
    logWarn("labelExists check failed", { label: labelName, error: String(error) });
    return false;
  }
}

/**
 * Try to create required labels and return the list of available labels
 *
 * @param repo - Optional repository in "owner/repo" format. If not specified, uses current repo.
 * @returns Array of label names that are available (either created or already existed)
 */
function tryCreateLabels(repo?: string): string[] {
  const availableLabels: string[] = [];

  // Escape repo to prevent command injection with special characters
  const repoOption = repo ? `--repo "${escapeShellArg(repo)}"` : "";
  for (const { name, description, color } of REQUIRED_LABELS) {
    try {
      const escapedName = escapeShellArg(name);
      const escapedDesc = escapeShellArg(description);
      execSync(
        `gh label create ${repoOption} "${escapedName}" --description "${escapedDesc}" --color "${color}"`,
        {
          encoding: "utf-8",
          timeout: TIMEOUT_MS,
          stdio: ["pipe", "pipe", "pipe"],
        },
      );
      availableLabels.push(name);
    } catch (error) {
      // Log the error for debugging
      logWarn("Error creating label (may already exist)", {
        label: name,
        error: error instanceof Error ? error.message : String(error),
      });
      // Label creation failed - check if it already exists
      if (labelExists(name, repo)) {
        availableLabels.push(name);
      }
      // If label doesn't exist and couldn't be created, don't add to available list
    }
  }

  return availableLabels;
}

async function main(): Promise<void> {
  try {
    const input = await parseHookInput();
    const ctx = createContext(input);
    const sessionId = getSessionId(ctx) ?? "unknown";

    const toolInput = input.tool_input as Record<string, unknown> | undefined;
    const command = (toolInput?.command as string) ?? "";

    // Check for skip environment variable
    if (hasSkipEnv(command)) {
      approveAndExit(HOOK_NAME);
    }

    // Only process PR merge commands
    if (!isPrMergeCommand(command)) {
      approveAndExit(HOOK_NAME);
    }

    // Extract PR number and repo from command
    let prNumber = extractPrNumber(command);
    const repo = extractRepoOption(command);

    // Fallback: Try to resolve PR number from current branch context
    if (!prNumber) {
      try {
        // Quote repo to prevent command injection with special characters
        const repoOption = repo ? `--repo "${repo}"` : "";
        const output = execSync(`gh pr view ${repoOption} --json number --jq .number`, {
          encoding: "utf-8",
          timeout: TIMEOUT_MS,
          stdio: ["pipe", "pipe", "pipe"],
        });
        const num = output.trim();
        if (/^\d+$/.test(num)) {
          prNumber = num;
        }
      } catch (error) {
        // Log but continue - not in a PR branch or other failure is non-critical
        logWarn("Could not resolve PR number from current branch", {
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }

    if (!prNumber) {
      approveAndExit(HOOK_NAME);
    }

    // Fetch and analyze AI reviews (pass repo for cross-repo support)
    const actionableReviews = await getPrReviews(prNumber, repo ?? undefined);

    if (actionableReviews.length === 0) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `PR #${prNumber}: no actionable AI comments`,
        undefined,
        {
          sessionId,
        },
      );
      approveAndExit(HOOK_NAME);
    }

    // Create follow-up Issue (label creation is handled inside, with fallback if unavailable)
    const issueNumber = createFollowupIssue(prNumber, actionableReviews, repo ?? undefined);

    if (issueNumber) {
      const message = `[${HOOK_NAME}] AIレビューの未対応指摘を検出しました。Issue #${issueNumber} を作成しました。`;
      console.error(message);
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `PR #${prNumber}: created Issue #${issueNumber} for ${actionableReviews.length} reviewers`,
        undefined,
        { sessionId },
      );
    } else {
      const message = `[${HOOK_NAME}] AIレビューの未対応指摘を検出しましたが、Issue作成に失敗しました。手動で確認してください。`;
      console.error(message);
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `PR #${prNumber}: failed to create Issue for actionable comments`,
        undefined,
        { sessionId },
      );
    }

    // Allow merge to proceed (non-blocking)
    approveAndExit(HOOK_NAME);
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    approveAndExit(HOOK_NAME);
  }
}

// 実行
if (import.meta.main) {
  main();
}
