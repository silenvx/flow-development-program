/**
 * PR operations for ci-monitor.
 *
 * Why:
 *   Handle PR validation, state fetching, rebasing, merging, and recreation.
 *   Extracted from ci-monitor.py as part of Issue #1765 refactoring (Phase 6).
 *
 * What:
 *   - validatePrNumber(): Validate PR number format and range
 *   - getPrState(): Fetch current PR state from GitHub
 *   - hasLocalChanges(): Check for uncommitted or unpushed local changes
 *   - rebasePr(): Attempt to rebase the PR
 *   - mergePr(): Merge the PR using squash merge
 *   - recreatePr(): Close existing PR and create a new one
 *
 * Remarks:
 *   - Migrated from Python ci_monitor/pr_operations.py (Issue #3261)
 *   - Uses asyncSpawn for git/gh command execution
 *
 * Changelog:
 *   - silenvx/dekita#3261: TypeScript migration from Python
 */

import {
  getCodexReviewRequests,
  getCodexReviews,
  hasCopilotOrCodexReviewer,
  isGeminiReviewPending,
} from "../../hooks/lib/ci_monitor_ai_review";
import { saveMonitorState } from "../../hooks/lib/monitor_state";
import { asyncSpawn } from "../../hooks/lib/spawn";
import type {
  BlockedReason,
  CheckStatus,
  MergeState,
  PRState,
  RebaseResult,
} from "../../hooks/lib/types";
import { log } from "./events";
import {
  getRepoInfo,
  getRulesetRequirements,
  runGhCommand,
  runGhCommandWithError,
} from "./github_api";

// =============================================================================
// Constants
// =============================================================================

/** Error message indicating PR branch is behind main */
export const MERGE_ERROR_BEHIND = "MERGE_ERROR_BEHIND";

/** Default stable check interval in seconds */
export const DEFAULT_STABLE_CHECK_INTERVAL = 30;

/** Default stable wait minutes */
export const DEFAULT_STABLE_WAIT_MINUTES = 2;

/** Default stable wait timeout in minutes */
export const DEFAULT_STABLE_WAIT_TIMEOUT = 30;

// =============================================================================
// Validation Functions
// =============================================================================

/**
 * Validate PR number format and range.
 *
 * @param prNumber - PR number string to validate
 * @returns Tuple of [isValid, errorMessage]
 */
export function validatePrNumber(prNumber: string): [boolean, string] {
  // Reject non-numeric strings (parseInt("123abc") would return 123)
  if (!/^\d+$/.test(prNumber)) {
    return [false, `Invalid PR number '${prNumber}': must be a positive integer`];
  }

  const num = Number.parseInt(prNumber, 10);

  if (Number.isNaN(num) || num <= 0) {
    return [false, `Invalid PR number '${prNumber}': must be a positive integer`];
  }

  // Soft limit to catch obvious typos
  if (num > 999999) {
    return [false, `Invalid PR number '${prNumber}': value too large (max: 999999)`];
  }

  return [true, ""];
}

/**
 * Validate all PR numbers and throw error if any are invalid.
 *
 * @param prNumbers - List of PR number strings to validate
 * @returns List of validated PR numbers (unchanged if all valid)
 * @throws Error if any PR number is invalid
 */
export function validatePrNumbers(prNumbers: string[]): string[] {
  const errors: string[] = [];

  for (const prNumber of prNumbers) {
    const [isValid, errorMsg] = validatePrNumber(prNumber);
    if (!isValid) {
      errors.push(errorMsg);
    }
  }

  if (errors.length > 0) {
    for (const error of errors) {
      console.error(`Error: ${error}`);
    }
    throw new Error(errors.join("\n"));
  }

  return prNumbers;
}

// =============================================================================
// PR State Functions
// =============================================================================

/**
 * Fetch current PR state from GitHub.
 *
 * @param prNumber - The PR number to fetch state for
 * @returns Tuple of [state, errorMessage]. If state is null, errorMessage contains the reason.
 */
export async function getPrState(prNumber: string): Promise<[PRState | null, string | null]> {
  // Get merge state, reviewDecision, isDraft, and baseRefName in one call (Issue #3663)
  const mergeResult = await runGhCommandWithError([
    "pr",
    "view",
    prNumber,
    "--json",
    "mergeStateStatus,reviewDecision,isDraft,baseRefName",
  ]);

  if (!mergeResult.success) {
    return [null, mergeResult.stderr || "Unknown error fetching merge state"];
  }

  let mergeStateStr = "UNKNOWN";
  let reviewDecision = "";
  let isDraft = false;
  let baseRefName = "main";

  try {
    const prInfo = JSON.parse(mergeResult.stdout) as {
      mergeStateStatus?: string;
      reviewDecision?: string;
      isDraft?: boolean;
      baseRefName?: string;
    };
    mergeStateStr = prInfo.mergeStateStatus || "UNKNOWN";
    reviewDecision = prInfo.reviewDecision || "";
    isDraft = !!prInfo.isDraft;
    baseRefName = prInfo.baseRefName || "main";
  } catch (e) {
    // If parsing fails, continue with defaults (log for debugging)
    console.error(`Warning: Failed to parse PR info JSON: ${e}`);
  }

  const validMergeStates = ["CLEAN", "BEHIND", "DIRTY", "BLOCKED", "UNKNOWN"];
  const mergeState: MergeState = validMergeStates.includes(mergeStateStr)
    ? (mergeStateStr as MergeState)
    : "UNKNOWN";

  // Get requested reviewers using gh api
  const [reviewersSuccess, reviewersOutput] = await runGhCommand([
    "api",
    `/repos/{owner}/{repo}/pulls/${prNumber}`,
    "--jq",
    "[.requested_reviewers[].login]",
  ]);

  let pendingReviewers: string[] = [];
  if (reviewersSuccess && reviewersOutput) {
    try {
      pendingReviewers = JSON.parse(reviewersOutput) as string[];
    } catch (parseError) {
      console.error(
        `Warning: Failed to parse requested_reviewers JSON: ${parseError instanceof Error ? parseError.message : parseError}`,
      );
      console.error(`  Raw output: ${reviewersOutput.slice(0, 200)}`);
    }
  }

  // Get CI check status
  const [checksSuccess, checksOutput] = await runGhCommand([
    "pr",
    "checks",
    prNumber,
    "--json",
    "name,state",
  ]);

  let checkStatus: CheckStatus = "pending";
  let checkDetails: Record<string, unknown>[] = [];

  if (checksSuccess && checksOutput) {
    try {
      const checks = JSON.parse(checksOutput) as Array<{ name?: string; state?: string }>;
      checkDetails = checks as Record<string, unknown>[];

      if (checks.length === 0) {
        checkStatus = "pending";
      } else if (checks.some((c) => c.state === "FAILURE")) {
        checkStatus = "failure";
      } else if (checks.some((c) => c.state === "CANCELLED")) {
        checkStatus = "cancelled";
      } else if (checks.every((c) => c.state === "SUCCESS" || c.state === "SKIPPED")) {
        checkStatus = "success";
      } else {
        checkStatus = "pending";
      }
    } catch (parseError) {
      console.error(
        `Warning: Failed to parse PR checks JSON: ${parseError instanceof Error ? parseError.message : parseError}`,
      );
      console.error(`  Raw output: ${checksOutput.slice(0, 200)}`);
    }
  }

  return [
    {
      mergeState,
      pendingReviewers,
      checkStatus,
      checkDetails,
      reviewComments: [],
      unresolvedThreads: [],
      // Issue #3663: Include reviewDecision, isDraft, baseRefName to reduce API calls
      reviewDecision,
      isDraft,
      baseRefName,
    },
    null,
  ];
}

// =============================================================================
// BLOCKED Reason Detection (Issue #3634)
// =============================================================================

/**
 * Get detailed reason why a PR is blocked.
 *
 * Issue #3634: ci_monitor reports "merge_state: BLOCKED" without specific reason.
 * This function investigates and returns detailed blocking reasons.
 *
 * @param prNumber - PR number to check
 * @param prState - Optional existing PRState to avoid redundant API calls
 * @returns BlockedReason with detailed explanation, or null on error
 */
export async function getBlockedReason(
  prNumber: string,
  prState?: PRState,
): Promise<BlockedReason | null> {
  // Issue #3663: Use prState if available to avoid redundant API calls
  let mergeStateStatus: "CLEAN" | "BEHIND" | "DIRTY" | "BLOCKED" | "UNKNOWN";
  let reviewDecision: string;
  let isDraft: boolean;
  let baseRefName: string;

  if (
    prState?.reviewDecision !== undefined &&
    prState?.isDraft !== undefined &&
    prState?.baseRefName !== undefined
  ) {
    // Use existing data from PRState (Issue #3663)
    mergeStateStatus = prState.mergeState;
    reviewDecision = prState.reviewDecision;
    isDraft = prState.isDraft;
    baseRefName = prState.baseRefName;
  } else {
    // Fallback: Fetch from GitHub API when PRState doesn't have the data
    const prInfoResult = await runGhCommandWithError([
      "pr",
      "view",
      prNumber,
      "--json",
      "mergeStateStatus,reviewDecision,isDraft,baseRefName",
    ]);

    if (!prInfoResult.success) {
      console.error(`Failed to fetch PR info: ${prInfoResult.stderr}`);
      return null;
    }

    let prInfo: {
      mergeStateStatus?: string;
      reviewDecision?: string;
      isDraft?: boolean;
      baseRefName?: string;
    };

    try {
      prInfo = JSON.parse(prInfoResult.stdout);
    } catch (e) {
      console.error(`Failed to parse PR info JSON: ${e}`);
      return null;
    }

    // Issue #3663: Safe type validation for mergeStateStatus (Gemini review feedback)
    const validMergeStates: MergeState[] = ["CLEAN", "BEHIND", "DIRTY", "BLOCKED", "UNKNOWN"];
    const rawStatus = prInfo.mergeStateStatus || "UNKNOWN";
    mergeStateStatus = validMergeStates.includes(rawStatus as MergeState)
      ? (rawStatus as MergeState)
      : "UNKNOWN";
    reviewDecision = prInfo.reviewDecision || "";
    isDraft = !!prInfo.isDraft;
    baseRefName = prInfo.baseRefName || "main";
  }

  // Check if behind main
  const isBehind = mergeStateStatus === "BEHIND";

  // Get unresolved threads count - use prState if available, otherwise fetch via GraphQL
  // Note: prState.unresolvedThreads is populated by main_loop when available.
  // Empty array means "no unresolved threads" (not "unknown"), so we trust it.
  let unresolvedThreadCount = 0;

  if (prState?.unresolvedThreads !== undefined) {
    // Use existing data from PRState (may be empty array = no threads)
    unresolvedThreadCount = prState.unresolvedThreads.length;
  } else {
    // Fetch via GraphQL when PRState not available
    const repoInfo = await getRepoInfo();

    if (repoInfo) {
      const query = `
        query($owner: String!, $name: String!, $pr: Int!) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $pr) {
              reviewThreads(first: 100) {
                nodes {
                  isResolved
                }
              }
            }
          }
        }
      `;

      const threadsResult = await runGhCommandWithError([
        "api",
        "graphql",
        "-f",
        `query=${query}`,
        "-F",
        `owner=${repoInfo.owner}`,
        "-F",
        `name=${repoInfo.name}`,
        "-F",
        `pr:=${prNumber}`,
      ]);

      if (threadsResult.success) {
        try {
          const threadsData = JSON.parse(threadsResult.stdout);
          const threads = threadsData?.data?.repository?.pullRequest?.reviewThreads?.nodes || [];
          unresolvedThreadCount = threads.filter(
            (t: { isResolved?: boolean }) => t.isResolved === false,
          ).length;
        } catch {
          // Ignore parse errors, keep count as 0
        }
      }
    }
  }

  // Check for pending required reviewers - use prState if available
  let hasPendingRequiredReviewers = false;

  if (prState?.pendingReviewers) {
    // Use existing data from PRState
    hasPendingRequiredReviewers = prState.pendingReviewers.length > 0;
  } else {
    // Fetch via API when PRState not available
    const [reviewersSuccess, reviewersOutput] = await runGhCommand([
      "api",
      `/repos/{owner}/{repo}/pulls/${prNumber}`,
      "--jq",
      "[.requested_reviewers[].login]",
    ]);

    if (reviewersSuccess && reviewersOutput) {
      try {
        const pendingReviewers = JSON.parse(reviewersOutput) as string[];
        hasPendingRequiredReviewers = pendingReviewers.length > 0;
      } catch {
        // Ignore parse errors
      }
    }
  }

  // Build explanation and suggested action
  const reasons: string[] = [];
  const actions: string[] = [];

  if (isBehind) {
    reasons.push("ブランチがベースブランチより古い状態です（BEHIND）");
    actions.push("リベースを実行してください: `gh pr update-branch --rebase`");
  }

  if (unresolvedThreadCount > 0) {
    reasons.push(`${unresolvedThreadCount}件の未解決レビュースレッドがあります`);
    actions.push("レビューコメントに対応し、スレッドをResolveしてください");
  }

  if (reviewDecision === "CHANGES_REQUESTED") {
    reasons.push("レビューで変更要求（CHANGES_REQUESTED）があります");
    actions.push("指摘された変更を実施してください");
  } else if (reviewDecision === "REVIEW_REQUIRED") {
    reasons.push("レビューが必要です（REVIEW_REQUIRED）");
    actions.push("レビュアーにレビューを依頼してください");
  }

  // Note: requested_reviewers includes optional reviewers
  // Only report if reviews are not yet approved, avoiding false positives for optional reviewers
  if (hasPendingRequiredReviewers && reviewDecision !== "APPROVED") {
    reasons.push("レビュー待ちのレビュアーがいます");
    actions.push("レビュアーのレビュー完了を待ってください");
  }

  // Check for failed required checks (use prState if available)
  if (prState?.checkStatus === "failure" && prState?.checkDetails) {
    const failedChecks = prState.checkDetails
      .filter(
        (c): c is Record<string, unknown> & { state: string } =>
          typeof c.state === "string" && c.state === "FAILURE",
      )
      .map((c) => (typeof c.name === "string" ? c.name : "unknown"));
    if (failedChecks.length > 0) {
      reasons.push(`チェックが失敗しています: ${failedChecks.join(", ")}`);
      actions.push("CIエラーを修正してください");
    }
  }

  // Issue #3664: Check for pending required checks
  if (prState?.checkStatus === "pending" && prState?.checkDetails) {
    const pendingChecks = prState.checkDetails
      .filter(
        (c): c is Record<string, unknown> & { state: string } =>
          typeof c.state === "string" &&
          (c.state === "PENDING" || c.state === "IN_PROGRESS" || c.state === "QUEUED"),
      )
      .map((c) => (typeof c.name === "string" ? c.name : "unknown"));
    if (pendingChecks.length > 0) {
      reasons.push(`必須チェックが実行中です: ${pendingChecks.join(", ")}`);
      actions.push("CIの完了を待ってください");
    }
  }

  // Check for draft state
  if (isDraft) {
    reasons.push("PRがドラフト状態です");
    actions.push("レビュー準備ができたらReady for Reviewにしてください");
  }

  // If no specific reason found, use Ruleset API to provide detailed diagnosis (Issue #3748)
  let rulesetInfoForReturn: BlockedReason["rulesetInfo"] | undefined = undefined;

  if (reasons.length === 0) {
    if (mergeStateStatus === "BLOCKED") {
      // Fetch actual merge requirements from Ruleset API for the specific target branch
      // Issue #3748: Use getRulesetRequirements with target branch to avoid aggregating
      // rules from unrelated branches (e.g., release/* rules when merging to main)
      const rulesetInfo = await getRulesetRequirements(baseRefName);

      // Issue #3761: Use pullRequestRuleFound for review requirements
      // statusCheckRuleFound only indicates strictRequiredStatusChecks availability
      if (rulesetInfo.pullRequestRuleFound || rulesetInfo.statusCheckRuleFound) {
        // Store ruleset info for return value (Issue #3748)
        rulesetInfoForReturn = {
          requiredApprovingReviewCount: rulesetInfo.requiredApprovals,
          requiredReviewThreadResolution: rulesetInfo.requiredThreadResolution,
          strictRequiredStatusChecks: rulesetInfo.strictRequiredStatusChecks,
        };

        // Issue #3748: Provide accurate information based on actual ruleset configuration
        const rulesetReasons: string[] = [];
        const rulesetActions: string[] = [];

        // Check if strict status checks are required
        // Note: When mergeStateStatus === "BLOCKED", isBehind is always false (isBehind is set for "BEHIND")
        // So we inform about the strict policy requirement without assuming current sync state
        if (rulesetInfo.strictRequiredStatusChecks) {
          rulesetReasons.push(
            "strict status check policyが有効です（ベースブランチとの同期が必要な場合があります）",
          );
          rulesetActions.push(
            "ブランチが最新でない場合はリベースしてください: `gh pr update-branch --rebase`",
          );
        }

        // Check if review thread resolution is required
        if (rulesetInfo.requiredThreadResolution) {
          rulesetReasons.push("全てのレビュースレッドを解決する必要があります");
          rulesetActions.push("未解決のレビュースレッドを確認し、Resolveしてください");
        }

        // Check if approval is required (and not yet approved)
        if (rulesetInfo.requiredApprovals > 0 && reviewDecision !== "APPROVED") {
          rulesetReasons.push(`${rulesetInfo.requiredApprovals}件のレビュー承認が必要です`);
          rulesetActions.push("レビュアーの承認を取得してください");
        }

        if (rulesetReasons.length > 0) {
          reasons.push(...rulesetReasons);
          actions.push(...rulesetActions);
          // Only add "review not required" note when there are other concrete reasons
          // AND pull_request rule was found (Issue #3761: don't show this for status-check-only rulesets)
          if (rulesetInfo.pullRequestRuleFound && rulesetInfo.requiredApprovals === 0) {
            reasons.push("レビュー承認は不要です（required_approving_review_count: 0）");
          }
        } else {
          // No specific reasons found from ruleset
          // Note: Other ruleset types (required signatures, linear history, etc.) may cause blocking
          // Fall back to generic message to avoid misleading users (Codex review feedback)
          reasons.push("マージがブロックされています（Ruleset確認済み、具体的原因不明）");
          // Issue #3761: Only show "review not required" if pull_request rule was found
          if (rulesetInfo.pullRequestRuleFound && rulesetInfo.requiredApprovals === 0) {
            reasons.push("レビュー承認は不要です（required_approving_review_count: 0）");
          }
          actions.push("`gh pr merge` を実行して実際のエラーメッセージを確認してください");
        }
      } else {
        reasons.push("マージがブロックされています（詳細理由不明）");
        actions.push("リポジトリのブランチ保護ルールを確認してください");
      }
    } else if (mergeStateStatus === "DIRTY") {
      reasons.push("マージコンフリクトがあります（DIRTY）");
      actions.push("コンフリクトを解消してください");
    } else {
      reasons.push(`merge状態: ${mergeStateStatus}`);
      actions.push("PR状態を確認してください");
    }
  }

  return {
    mergeStateStatus,
    isBehind,
    unresolvedThreadCount,
    reviewDecision,
    hasPendingRequiredReviewers,
    explanation: reasons.length > 0 ? `${reasons.join("。")}。` : "",
    suggestedAction: actions.join("\n"),
    rulesetInfo: rulesetInfoForReturn,
  };
}

// =============================================================================
// Local Changes Detection
// =============================================================================

/**
 * Check for uncommitted or unpushed local changes.
 *
 * Issue #865: Prevents ci-monitor from rebasing when local changes exist,
 * which would cause push conflicts.
 *
 * @returns Tuple of [hasChanges, description]
 */
export async function hasLocalChanges(): Promise<[boolean, string]> {
  const reasons: string[] = [];

  // Check for uncommitted changes (staged or unstaged)
  // Issue #1805: Exclude untracked files (??) as they don't affect rebase
  const statusResult = await asyncSpawn("git", ["status", "--porcelain"], { timeout: 10000 });
  if (statusResult.success && statusResult.stdout?.trim()) {
    // Filter out untracked files (lines starting with "??")
    const trackedChanges = statusResult.stdout
      .trim()
      .split("\n")
      .filter((line) => line && !line.startsWith("??"));

    if (trackedChanges.length > 0) {
      reasons.push("uncommitted changes");
    }
  }

  // Check for unpushed commits
  const logResult = await asyncSpawn("git", ["log", "@{u}..HEAD", "--oneline"], { timeout: 10000 });
  if (logResult.success && logResult.stdout?.trim()) {
    const count = logResult.stdout.trim().split("\n").length;
    reasons.push(`${count} unpushed commit(s)`);
  }

  if (reasons.length > 0) {
    return [true, reasons.join(", ")];
  }
  return [false, ""];
}

// =============================================================================
// Main Branch Stability
// =============================================================================

/**
 * Get the timestamp of the last commit on origin/main.
 *
 * Issue #1239: Used to detect when main branch is stable (no recent updates).
 *
 * @returns Unix timestamp of the last commit, or null if failed
 */
export async function getMainLastCommitTime(): Promise<number | null> {
  // First, fetch to get latest remote state
  const fetchResult = await asyncSpawn("git", ["fetch", "origin", "main"], { timeout: 30000 });
  if (!fetchResult.success) {
    return null;
  }

  const logResult = await asyncSpawn("git", ["log", "-1", "--format=%ct", "origin/main"], {
    timeout: 10000,
  });

  if (logResult.success && logResult.stdout?.trim()) {
    const timestamp = Number.parseInt(logResult.stdout.trim(), 10);
    if (!Number.isNaN(timestamp)) {
      return timestamp;
    }
  }
  return null;
}

/**
 * Wait for main branch to stabilize (no recent updates).
 *
 * Issue #1239: When max_rebase is reached during active concurrent development,
 * wait for main to stop being updated before continuing with rebases.
 *
 * @param stableDurationMinutes - How long main must be stable before returning
 * @param checkInterval - Seconds between stability checks
 * @param timeoutMinutes - Maximum time to wait for stability
 * @param jsonMode - Whether to output in JSON format
 * @returns True if main became stable, false if timeout
 */
export async function waitForMainStable(
  stableDurationMinutes: number = DEFAULT_STABLE_WAIT_MINUTES,
  checkInterval: number = DEFAULT_STABLE_CHECK_INTERVAL,
  timeoutMinutes: number = DEFAULT_STABLE_WAIT_TIMEOUT,
  jsonMode = false,
): Promise<boolean> {
  const startTime = Date.now();
  const timeoutMs = timeoutMinutes * 60 * 1000;
  const stableDurationMs = stableDurationMinutes * 60 * 1000;

  log(`mainブランチの安定を待機中（${stableDurationMinutes}分間更新なしが必要）...`, jsonMode);

  if (!jsonMode) {
    console.log(
      `\n⏳  リベース試行上限に達しました。mainの安定を待機中。\n   mainは${stableDurationMinutes}分間更新がない必要があります。\n   タイムアウト: ${timeoutMinutes}分。\n`,
    );
  }

  let lastKnownCommitTime: number | null = null;

  while (Date.now() - startTime < timeoutMs) {
    const currentCommitTime = await getMainLastCommitTime();
    if (currentCommitTime === null) {
      log("mainのコミット時刻を取得できませんでした。再試行中...", jsonMode);
      await Bun.sleep(checkInterval * 1000);
      continue;
    }

    const now = Math.floor(Date.now() / 1000);
    const timeSinceLastCommit = now - currentCommitTime;

    if (timeSinceLastCommit * 1000 >= stableDurationMs) {
      log(
        `mainブランチが安定しました（最終更新: ${Math.floor(timeSinceLastCommit / 60)}分前）`,
        jsonMode,
      );
      if (!jsonMode) {
        console.log(
          `\n✅  mainブランチが安定しました。最終更新は${Math.floor(timeSinceLastCommit / 60)}分前です。\n   リベース操作を再開します。\n`,
        );
      }
      return true;
    }

    if (lastKnownCommitTime !== currentCommitTime) {
      if (lastKnownCommitTime !== null) {
        log("mainが更新されました。安定タイマーをリセット", jsonMode);
        if (!jsonMode) {
          console.log("   ↻ mainが更新されました。安定タイマーをリセット。");
        }
      }
      lastKnownCommitTime = currentCommitTime;
    }

    const remainingWait = Math.floor(stableDurationMs / 1000) - timeSinceLastCommit;
    if (!jsonMode) {
      process.stdout.write(
        `   ⏳ main最終更新: ${timeSinceLastCommit}秒前、あと${remainingWait}秒の安定が必要\r`,
      );
    }

    await Bun.sleep(checkInterval * 1000);
  }

  log("mainの安定待機がタイムアウトしました", jsonMode);
  if (!jsonMode) {
    console.log(`\n⏰  ${timeoutMinutes}分経過でタイムアウトしました。処理を続行します。\n`);
  }
  return false;
}

// =============================================================================
// Rebase Operations
// =============================================================================

/**
 * Attempt to rebase the PR (Issue #1348: Enhanced logging).
 *
 * @param prNumber - The PR number to rebase
 * @returns RebaseResult with success status, conflict flag, and optional error message
 */
export async function rebasePr(prNumber: string): Promise<RebaseResult> {
  const result = await runGhCommandWithError(["pr", "update-branch", prNumber, "--rebase"], 60000);

  if (result.success) {
    return { success: true, conflict: false, errorMessage: null };
  }

  const errorOutput = result.stderr || result.stdout || "";
  const conflictIndicators = ["conflict", "merge conflict", "could not be rebased"];
  const hasConflict = conflictIndicators.some((indicator) =>
    errorOutput.toLowerCase().includes(indicator),
  );

  return {
    success: false,
    conflict: hasConflict,
    errorMessage: errorOutput || null,
  };
}

/**
 * Merge the PR using squash merge.
 *
 * @param prNumber - The PR number
 * @returns Tuple of [success, message]. On failure, message is MERGE_ERROR_BEHIND
 *          if the branch is behind main and needs rebase, otherwise the error message.
 */
export async function mergePr(prNumber: string): Promise<[boolean, string]> {
  const result = await runGhCommandWithError(["pr", "merge", prNumber, "--squash"], 120000);

  if (result.success) {
    return [true, "Merge successful"];
  }

  const errorOutput = result.stderr || result.stdout;
  if (errorOutput?.toLowerCase().includes("not up to date")) {
    return [false, MERGE_ERROR_BEHIND];
  }
  return [false, errorOutput || "Unknown error"];
}

// =============================================================================
// Branch Operations
// =============================================================================

/**
 * Get the head branch name of a PR.
 *
 * @param prNumber - The PR number
 * @returns The branch name, or null if it could not be determined
 */
export async function getPrBranchName(prNumber: string): Promise<string | null> {
  const [success, output] = await runGhCommand([
    "pr",
    "view",
    prNumber,
    "--json",
    "headRefName",
    "--jq",
    ".headRefName",
  ]);

  if (success && output) {
    return output.trim();
  }
  return null;
}

/**
 * Format rebase count message for output (Issue #1364).
 *
 * When multiple rebases were needed, suggests considering merge queue
 * to reduce CI churn from concurrent development.
 *
 * @param count - Number of rebases performed
 * @returns Formatted message string
 */
export function formatRebaseSummary(count: number): string {
  const suffix = count >= 2 ? " (consider merge queue)" : "";
  return `Rebases performed: ${count}${suffix}`;
}

/**
 * Sync local branch after remote rebase (Issue #895).
 *
 * After ci-monitor rebases the remote branch via `gh pr update-branch`,
 * the local branch becomes out of sync. This function pulls the rebased
 * changes to keep local and remote in sync.
 *
 * @param branchName - The branch name to sync
 * @param jsonMode - If true, suppress human-readable output
 * @returns True if sync was successful or not needed, false if sync failed
 */
export async function syncLocalAfterRebase(branchName: string, jsonMode = false): Promise<boolean> {
  // Check if we're in a git repository
  const inRepoResult = await asyncSpawn("git", ["rev-parse", "--is-inside-work-tree"], {
    timeout: 5000,
  });
  if (!inRepoResult.success) {
    return true;
  }

  // Check current branch
  const branchResult = await asyncSpawn("git", ["rev-parse", "--abbrev-ref", "HEAD"], {
    timeout: 5000,
  });
  if (!branchResult.success) {
    return true;
  }

  const currentBranch = branchResult.stdout?.trim();
  if (currentBranch !== branchName) {
    if (!jsonMode) {
      console.log(
        `ℹ️  Local branch is not on the target branch (${currentBranch} != ${branchName}), skipping sync`,
      );
    }
    return true;
  }

  // Check for uncommitted changes
  const statusResult = await asyncSpawn("git", ["status", "--porcelain"], { timeout: 5000 });
  if (statusResult.success && statusResult.stdout?.trim()) {
    if (!jsonMode) {
      console.log(
        "⚠️  コミットされていないローカル変更があります。自動同期をスキップします。手動で実行:",
      );
      console.log(`   git pull --rebase origin ${branchName}`);
    }
    return false;
  }

  // Attempt to sync
  if (!jsonMode) {
    console.log("🔄 ローカルブランチをリモートと同期中...");
  }

  const pullResult = await asyncSpawn("git", ["pull", "--rebase", "origin", branchName], {
    timeout: 30000,
  });

  if (pullResult.success) {
    if (!jsonMode) {
      console.log("✅ ローカルブランチの同期が完了しました");
    }
    return true;
  }

  if (!jsonMode) {
    console.log(`⚠️  ローカルブランチの同期に失敗しました: ${pullResult.stderr?.trim()}`);
    console.log(`   手動で実行: git pull --rebase origin ${branchName}`);
  }
  return false;
}

// =============================================================================
// PR Recreation
// =============================================================================

/**
 * Reopen a PR with retry logic.
 *
 * Issue #1558: When PR reopen fails, retry up to maxRetries times
 * with a short delay between attempts.
 *
 * @param prNumber - The PR number to reopen
 * @param comment - Comment to add when reopening
 * @param maxRetries - Maximum number of retry attempts (default: 3, minimum: 1)
 * @returns Tuple of [success, errorMessage, attemptsMade]
 */
export async function reopenPrWithRetry(
  prNumber: string,
  comment: string,
  maxRetries = 3,
): Promise<[boolean, string, number]> {
  const retries = Math.max(1, maxRetries);
  let lastError = "";

  for (let attempt = 0; attempt < retries; attempt++) {
    const [success, output] = await runGhCommand(["pr", "reopen", prNumber, "--comment", comment]);

    if (success) {
      return [true, "", attempt + 1];
    }
    lastError = output;

    if (attempt < retries - 1) {
      await Bun.sleep(1000);
    }
  }

  return [false, lastError, retries];
}

/**
 * Close existing PR and create a new one from the same branch.
 *
 * Issue #1532: When Copilot review is stuck in pending state for too long,
 * recreating the PR often resolves the issue.
 *
 * @param prNumber - The PR number to recreate
 * @returns Tuple of [success, newPrNumber, message]
 */
export async function recreatePr(prNumber: string): Promise<[boolean, string | null, string]> {
  // 1. Get existing PR details
  const [detailsSuccess, detailsOutput] = await runGhCommand([
    "pr",
    "view",
    prNumber,
    "--json",
    "title,body,headRefName,baseRefName,labels,assignees,isDraft",
  ]);

  if (!detailsSuccess) {
    return [false, null, `Failed to get PR details: ${detailsOutput}`];
  }

  let prData: Record<string, unknown>;
  try {
    prData = JSON.parse(detailsOutput) as Record<string, unknown>;
  } catch {
    return [false, null, `Failed to parse PR details: ${detailsOutput}`];
  }

  const title = (prData.title as string) || "";
  const body = (prData.body as string) || "";
  const headBranch = (prData.headRefName as string) || "";
  const baseBranch = (prData.baseRefName as string) || "main";
  const labels = ((prData.labels as Array<{ name?: string }>) || [])
    .map((l) => l.name || "")
    .filter(Boolean);
  const assignees = ((prData.assignees as Array<{ login?: string }>) || [])
    .map((a) => a.login || "")
    .filter(Boolean);
  const isDraft = prData.isDraft as boolean;

  if (!title) {
    return [false, null, `PRタイトルを取得できませんでした (PR番号: ${prNumber})`];
  }

  if (!headBranch) {
    return [false, null, `ブランチ名を取得できませんでした (PR: ${prNumber})`];
  }

  // 2. Close existing PR with comment
  const closeComment =
    "Copilotレビューがpending状態でスタックしているため、PRを自動で作り直します。\n\n" +
    "新しいPRが自動作成されます。";

  const [closeSuccess, closeOutput] = await runGhCommand([
    "pr",
    "close",
    prNumber,
    "--comment",
    closeComment,
  ]);

  if (!closeSuccess) {
    return [false, null, `PRのクローズに失敗しました: ${closeOutput}`];
  }

  // 3. Add auto-recreation note to body
  const recreationNote = `\n\n---\n🔄 **自動再作成**: Copilotレビューがpending状態でスタックしていたため、PR #${prNumber} から自動で作り直されました。`;
  const newBody = body + recreationNote;

  // 4. Create new PR
  const createArgs = [
    "pr",
    "create",
    "--title",
    title,
    "--body",
    newBody,
    "--base",
    baseBranch,
    "--head",
    headBranch,
  ];

  for (const label of labels) {
    createArgs.push("--label", label);
  }

  for (const assignee of assignees) {
    createArgs.push("--assignee", assignee);
  }

  if (isDraft) {
    createArgs.push("--draft");
  }

  const [createSuccess, createOutput] = await runGhCommand(createArgs);

  if (!createSuccess) {
    // Reopen original PR since creation failed
    const reopenComment = `新しいPRの作成に失敗したため、このPRを再オープンしました。\n\n**失敗理由**: ${createOutput}`;

    const [reopenSuccess, reopenError, attempts] = await reopenPrWithRetry(
      prNumber,
      reopenComment,
      3,
    );

    if (reopenSuccess) {
      return [
        false,
        null,
        `新しいPRの作成に失敗しました: ${createOutput}。元のPR #${prNumber} を再オープンしました。`,
      ];
    }

    // All retry attempts failed - save recovery state
    const recoveryState = {
      status: "pr_recovery_needed",
      success: false,
      message: `PR #${prNumber} が閉じられたまま復旧に失敗しました`,
      closed_pr: prNumber,
      create_error: createOutput,
      reopen_error: reopenError,
      reopen_attempts: attempts,
    };
    await saveMonitorState(prNumber, recoveryState);

    return [
      false,
      null,
      `新しいPRの作成に失敗しました: ${createOutput}。元のPRの再オープンにも失敗しました（${attempts}回リトライ）: ${reopenError}`,
    ];
  }

  // Extract new PR number from output
  const match = createOutput.match(/\/pull\/(\d+)/);
  const newPrNumber = match ? match[1] : null;

  if (newPrNumber) {
    return [true, newPrNumber, `PR #${prNumber} を閉じて新しい PR #${newPrNumber} を作成しました`];
  }

  return [true, null, `新しいPRを作成しましたが、PR番号を取得できませんでした: ${createOutput}`];
}

// =============================================================================
// AI Review Functions
// =============================================================================

/**
 * Check if there's a pending Codex review request.
 *
 * A Codex review is pending if there's an @codex review comment and
 * no Codex review has been posted after that comment was created.
 *
 * @param prNumber - The PR number to check
 * @returns True if Codex review is requested but not yet complete
 */
export async function isCodexReviewPending(prNumber: string): Promise<boolean> {
  const requests = await getCodexReviewRequests(prNumber);
  if (requests.length === 0) {
    return false;
  }

  const reviews = await getCodexReviews(prNumber);

  for (const request of requests) {
    const requestTime = request.createdAt;
    const hasReview = reviews.some((review) => (review.submitted_at as string) > requestTime);

    if (!hasReview) {
      return true;
    }
  }

  return false;
}

/**
 * Check if any AI review (Copilot, Codex Cloud, or Gemini) is pending.
 *
 * Issue #2711: Added Gemini Code Assist to the checks.
 *
 * This combines two detection mechanisms:
 * 1. GitHub reviewer assignments (Copilot, Codex, or Gemini in pendingReviewers,
 *    with rate limit detection for Gemini)
 * 2. Codex Cloud via @codex review comments
 *
 * @param prNumber - PR number to check
 * @param pendingReviewers - List of pending reviewers from GitHub API
 * @returns True if AI review is in progress but not yet complete
 */
export async function hasAiReviewPending(
  prNumber: string,
  pendingReviewers: string[],
): Promise<boolean> {
  if (hasCopilotOrCodexReviewer(pendingReviewers)) {
    return true;
  }

  if (await isCodexReviewPending(prNumber)) {
    return true;
  }

  // Issue #2711: Check for Gemini review (skipped if rate limited)
  if (await isGeminiReviewPending(prNumber, pendingReviewers)) {
    return true;
  }

  return false;
}
