/**
 * Main monitoring loop for ci-monitor.
 *
 * Why:
 *   Contains the monitor_pr function which is the main monitoring loop.
 *   Handles CI monitoring, rebase, review waiting, and timeout handling.
 *
 * What:
 *   - monitorPr(): Main monitoring loop for a single PR
 *
 * Remarks:
 *   - Migrated from Python ci_monitor/main_loop.py (Issue #3261)
 *   - Issue #2454: Hardcoded parameters (interval, max_rebase, json_mode, wait_review)
 *
 * Changelog:
 *   - silenvx/dekita#3261: TypeScript migration from Python
 */

import {
  checkAndReportContradictions,
  hasCopilotOrCodexReviewer,
  hasGeminiReviewer,
  hasQodoComplianceViolation,
  isCopilotReviewError,
  requestCopilotReview,
} from "../../hooks/lib/ci_monitor_ai_review";
import {
  ASYNC_REVIEWER_CHECK_DELAY_SECONDS,
  COPILOT_REVIEWER_LOGIN,
  DEFAULT_COPILOT_PENDING_TIMEOUT,
  DEFAULT_GEMINI_PENDING_TIMEOUT,
  DEFAULT_LOCAL_CHANGES_MAX_WAIT,
  DEFAULT_LOCAL_CHANGES_WAIT_INTERVAL,
  DEFAULT_MAX_COPILOT_RETRY,
  DEFAULT_MAX_PR_RECREATE,
  DEFAULT_MAX_REBASE,
  DEFAULT_MAX_RETRY_WAIT_POLLS,
  DEFAULT_POLLING_INTERVAL,
  DEFAULT_TIMEOUT_MINUTES,
} from "../../hooks/lib/constants";
import { saveMonitorState } from "../../hooks/lib/monitor_state";
import {
  checkRateLimit,
  getAdjustedInterval,
  logRateLimitEvent,
  logRateLimitWarningToConsole,
} from "../../hooks/lib/rate_limit";
import { getCiMonitorSessionId } from "../../hooks/lib/session";
import type {
  IntervalDirection,
  MonitorResult,
  RateLimitEventType,
  RetryWaitStatus,
} from "../../hooks/lib/types";
import { log } from "./events";
import { logCiMonitorEvent, showWaitTimeHint } from "./monitor";
import {
  DEFAULT_STABLE_CHECK_INTERVAL,
  DEFAULT_STABLE_WAIT_MINUTES,
  getBlockedReason,
  getPrBranchName,
  getPrState,
  hasAiReviewPending,
  hasLocalChanges,
  isCodexReviewPending,
  rebasePr,
  recreatePr,
  syncLocalAfterRebase,
  waitForMainStable,
} from "./pr_operations";
import {
  autoResolveDuplicateThreads,
  classifyReviewComments,
  filterDuplicateComments,
  getAllAiReviewComments,
  getPrChangedFiles,
  getResolvedThreadHashes,
  getReviewComments,
  getUnresolvedAiThreads,
  getUnresolvedThreads,
  logReviewCommentsToQualityLog,
} from "./review_comments";

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Wrapper for logRateLimitWarningToConsole with proper callbacks.
 *
 * Creates an adapter for logRateLimitEvent that includes sessionId,
 * since logRateLimitEvent requires sessionId as its 5th parameter
 * but logRateLimitWarningToConsole's callback type expects details as 5th.
 */
function logRateLimitWarning(
  remaining: number,
  limit: number,
  resetTimestamp: number,
  jsonMode = false,
): void {
  // Create adapter function that bridges the callback signature difference
  const logEventFn = (
    eventType: RateLimitEventType,
    rem: number,
    lim: number,
    resetTs: number,
    details: Record<string, unknown> | null,
  ) => {
    logRateLimitEvent(eventType, rem, lim, resetTs, getCiMonitorSessionId(), details);
  };
  logRateLimitWarningToConsole(remaining, limit, resetTimestamp, jsonMode, log, logEventFn);
}

// =============================================================================
// Main Monitor Function
// =============================================================================

/**
 * Monitor a PR until CI completes (and optionally review completes) or timeout.
 *
 * @param prNumber - PR number to monitor
 * @param timeoutMinutes - Timeout in minutes
 * @param earlyExit - If true, exit immediately when review comments are detected
 *
 * Note:
 *   Issue #2454: The following parameters are now hardcoded to default values:
 *   - interval: DEFAULT_POLLING_INTERVAL (30s)
 *   - maxRebase: DEFAULT_MAX_REBASE (3)
 *   - jsonMode: true (always JSON output)
 *   - waitReview: true (always wait for AI review)
 *   - resolveBeforeRebase: false
 *   - waitStable: true
 */
export async function monitorPr(
  prNumber: string,
  timeoutMinutes: number = DEFAULT_TIMEOUT_MINUTES,
  earlyExit = false,
): Promise<MonitorResult> {
  // Issue #2454: Hardcode removed parameters to their default values
  const interval = DEFAULT_POLLING_INTERVAL;
  const maxRebase = DEFAULT_MAX_REBASE;
  const jsonMode = true; // Always JSON output
  const waitReview = true; // Always wait for AI review
  const resolveBeforeRebase = false;
  const waitStable = true;

  let startTime = Date.now();
  const timeoutMs = timeoutMinutes * 60 * 1000;
  let rebaseCount = 0;
  let localChangesWaitCount = 0;
  let copilotRetryCount = 0;
  let copilotRetryInProgress = false;
  let copilotRetryWaitPolls = 0;
  let reviewNotified = false;
  let rebaseReviewChecked = false;
  let preRebaseHashes = new Set<string>();
  let copilotPendingSince: number | null = null;
  let geminiPendingSince: number | null = null;
  let geminiTimedOut = false;
  let prRecreateCount = 0;
  let hasLoggedBlockedReason = false; // Issue #3520: Prevent repeated BLOCKED logging

  // Get initial state for reviewer tracking
  const [initialState] = await getPrState(prNumber);
  let previousReviewers = initialState?.pendingReviewers || [];
  let wasCodexPending = await isCodexReviewPending(prNumber);
  let wasGeminiPending = hasGeminiReviewer(previousReviewers);

  // Track polling iterations for periodic hints
  let pollIteration = 0;

  // Rate limit monitoring (Issue #896)
  let adjustedInterval = interval;
  let lastRateLimitCheck = 0;
  const rateLimitCheckFrequency = 10;

  // Initial rate limit check
  let {
    remaining: rateRemaining,
    limit: rateLimit,
    resetTimestamp: resetTs,
  } = await checkRateLimit();
  if (rateLimit > 0) {
    logRateLimitWarning(rateRemaining, rateLimit, resetTs, jsonMode);
    adjustedInterval = getAdjustedInterval(interval, rateRemaining);
    if (adjustedInterval !== interval) {
      const direction: IntervalDirection = adjustedInterval < interval ? "decrease" : "increase";
      log(`Adjusted polling interval to ${adjustedInterval}s due to rate limit`, jsonMode);
      logRateLimitEvent(
        "adjusted_interval",
        rateRemaining,
        rateLimit,
        resetTs,
        getCiMonitorSessionId(),
        {
          old_interval: interval,
          new_interval: adjustedInterval,
          direction,
        },
      );
    }
  }

  // Issue #1351: Helper functions
  const handleCopilotRetryWait = (): RetryWaitStatus => {
    copilotRetryWaitPolls += 1;
    if (copilotRetryWaitPolls < DEFAULT_MAX_RETRY_WAIT_POLLS) {
      log("Waiting for Copilot to start new review...", jsonMode);
      return "CONTINUE" as RetryWaitStatus;
    }
    log(`Retry wait timeout (${copilotRetryWaitPolls} polls), considering retry failed`, jsonMode);
    copilotRetryInProgress = false;
    copilotRetryWaitPolls = 0;
    return "TIMEOUT" as RetryWaitStatus;
  };

  const executeCopilotRetry = async (errorMessage: string): Promise<boolean> => {
    copilotRetryCount += 1;
    if (copilotRetryCount <= DEFAULT_MAX_COPILOT_RETRY) {
      log(
        `Copilot review error detected, retrying (${copilotRetryCount}/${DEFAULT_MAX_COPILOT_RETRY})...`,
        jsonMode,
        jsonMode ? { error_message: errorMessage, retry_count: copilotRetryCount } : undefined,
      );
      const [success, errorMsg] = await requestCopilotReview(prNumber);
      if (success) {
        log("Copilot review re-requested, waiting...", jsonMode);
        copilotRetryInProgress = true;
        copilotRetryWaitPolls = 0;
      } else {
        log(`Failed to re-request Copilot review: ${errorMsg}`, jsonMode);
      }
      previousReviewers = [COPILOT_REVIEWER_LOGIN];
      wasCodexPending = false;
      return true;
    }
    return false;
  };

  const finalizeMonitoring = async (
    success: boolean,
    message: string,
    options?: { unresolvedThreads?: number; reviewCompleted?: boolean },
  ): Promise<void> => {
    const stateData: Record<string, unknown> = {
      status: "completed",
      success,
      message,
      rebase_count: rebaseCount,
      elapsed_seconds: Math.floor((Date.now() - startTime) / 1000),
    };
    if (options?.unresolvedThreads !== undefined) {
      stateData.unresolved_threads = options.unresolvedThreads;
    }
    if (options?.reviewCompleted !== undefined) {
      stateData.review_completed = options.reviewCompleted;
    }
    await saveMonitorState(prNumber, stateData);
  };

  const formatCopilotError = (errorMessage: string | null): string => {
    const truncated = errorMessage ? errorMessage.slice(0, 100) : "Unknown error";
    return `Copilot review failed after ${DEFAULT_MAX_COPILOT_RETRY} retries: ${truncated}`;
  };

  log(`Starting CI monitor for PR #${prNumber}`, jsonMode);
  log(`Interval: ${interval}s, Timeout: ${timeoutMinutes}min, Max rebase: ${maxRebase}`, jsonMode);

  logCiMonitorEvent(prNumber, "monitor_start", "started", {
    interval,
    timeout_minutes: timeoutMinutes,
    max_rebase: maxRebase,
    wait_review: waitReview,
  });

  while (true) {
    const elapsed = Date.now() - startTime;

    // Save state for background execution monitoring
    const stateDict: Record<string, unknown> = {
      status: "monitoring",
      rebase_count: rebaseCount,
      elapsed_seconds: Math.floor(elapsed / 1000),
      timeout_seconds: Math.floor(timeoutMs / 1000),
      poll_iteration: pollIteration,
    };
    if (rateLimit > 0) {
      stateDict.rate_limit = {
        remaining: rateRemaining,
        limit: rateLimit,
        reset_at: resetTs,
      };
    }
    await saveMonitorState(prNumber, stateDict);

    if (elapsed > timeoutMs) {
      // Build timeout message with guidance
      let timeoutMsg = `Timeout after ${timeoutMinutes} minutes`;
      const guidanceParts: string[] = [];

      const [currentState] = await getPrState(prNumber);
      if (currentState) {
        if (currentState.checkStatus === "pending") {
          guidanceParts.push("CI still pending");
        }
        if (await hasAiReviewPending(prNumber, currentState.pendingReviewers)) {
          guidanceParts.push("AI review still pending");
        }
      }

      if (guidanceParts.length > 0) {
        timeoutMsg += ` (${guidanceParts.join(", ")})`;
      }

      logCiMonitorEvent(prNumber, "monitor_complete", "timeout", {
        elapsed_seconds: Math.floor(elapsed / 1000),
        timeout_minutes: timeoutMinutes,
        poll_iterations: pollIteration,
        rebase_count: rebaseCount,
        guidance: guidanceParts,
      });

      await finalizeMonitoring(false, timeoutMsg);
      return {
        success: false,
        message: timeoutMsg,
        rebase_count: rebaseCount,
        final_state: currentState,
      };
    }

    const [state, error] = await getPrState(prNumber);
    if (state === null) {
      const errorDetail = error ? `: ${error}` : "";
      log(`Failed to fetch PR state${errorDetail}, retrying...`, jsonMode);
      await Bun.sleep(adjustedInterval * 1000);
      continue;
    }

    // Check merge state
    if (state.mergeState === "BEHIND") {
      if (rebaseCount >= maxRebase) {
        if (waitStable) {
          log(
            `Max rebase attempts (${maxRebase}) reached, waiting for main to stabilize...`,
            jsonMode,
          );
          const remainingTimeout = Math.max(1, Math.floor((timeoutMs - elapsed) / 60000));

          if (
            await waitForMainStable(
              DEFAULT_STABLE_WAIT_MINUTES,
              DEFAULT_STABLE_CHECK_INTERVAL,
              remainingTimeout,
              jsonMode,
            )
          ) {
            log("Main stabilized, resetting rebase counter and continuing", jsonMode);
            rebaseCount = 0;
          } else {
            const maxRebaseStableMsg = `Max rebase attempts (${maxRebase}) reached and main did not stabilize`;
            await finalizeMonitoring(false, maxRebaseStableMsg);
            return {
              success: false,
              message: maxRebaseStableMsg,
              rebase_count: rebaseCount,
              final_state: state,
            };
          }
        } else {
          const maxRebaseMsg = `Max rebase attempts (${maxRebase}) reached`;
          await finalizeMonitoring(false, maxRebaseMsg);
          return {
            success: false,
            message: maxRebaseMsg,
            rebase_count: rebaseCount,
            final_state: state,
          };
        }
      }

      // Check for local changes before rebasing
      const [hasChanges, changeDescription] = await hasLocalChanges();
      if (hasChanges) {
        localChangesWaitCount += 1;
        if (localChangesWaitCount > DEFAULT_LOCAL_CHANGES_MAX_WAIT) {
          log(
            `BEHIND detected, but max wait for local changes (${DEFAULT_LOCAL_CHANGES_MAX_WAIT}) exceeded: ${changeDescription}`,
            jsonMode,
          );
          const localChangesMsg = `Rebase skipped due to local changes (after ${DEFAULT_LOCAL_CHANGES_MAX_WAIT} wait cycles): ${changeDescription}`;
          await finalizeMonitoring(false, localChangesMsg);
          return {
            success: false,
            message: localChangesMsg,
            rebase_count: rebaseCount,
            final_state: state,
          };
        }

        log(
          `BEHIND detected, waiting for local changes to be resolved (${localChangesWaitCount}/${DEFAULT_LOCAL_CHANGES_MAX_WAIT}): ${changeDescription}`,
          jsonMode,
        );
        const waitInterval = Math.max(DEFAULT_LOCAL_CHANGES_WAIT_INTERVAL, adjustedInterval);
        await Bun.sleep(waitInterval * 1000);
        pollIteration += 1;
        continue;
      }
      if (localChangesWaitCount > 0) {
        log(`Local changes resolved after ${localChangesWaitCount} wait cycles`, jsonMode);
        localChangesWaitCount = 0;
      }

      // Check for unresolved AI review threads before rebasing
      if (resolveBeforeRebase) {
        const aiThreads = await getUnresolvedAiThreads(prNumber);
        if (aiThreads === null) {
          log(
            "BEHIND detected, but cannot verify AI thread status (API error) - waiting",
            jsonMode,
          );
          await Bun.sleep(adjustedInterval * 1000);
          continue;
        }
        if (aiThreads.length > 0) {
          log(
            `BEHIND detected, but ${aiThreads.length} unresolved AI review thread(s) - waiting for resolution`,
            jsonMode,
          );
          await Bun.sleep(adjustedInterval * 1000);
          continue;
        }
      }

      log(`BEHIND detected, attempting rebase (${rebaseCount + 1}/${maxRebase})...`, jsonMode);

      // Capture resolved thread hashes before rebase
      preRebaseHashes = await getResolvedThreadHashes(prNumber);
      if (preRebaseHashes.size > 0) {
        log(
          `Captured ${preRebaseHashes.size} resolved thread hashes for duplicate detection`,
          jsonMode,
        );
      }

      // Capture file count before rebase
      const filesBefore = await getPrChangedFiles(prNumber);
      const filesBeforeCount = filesBefore !== null ? filesBefore.size : -1;

      const rebaseResult = await rebasePr(prNumber);
      if (rebaseResult.success) {
        rebaseCount += 1;
        rebaseReviewChecked = false;
        log("Rebase successful, waiting for new CI to start...", jsonMode);

        // Check for mixed changes after rebase
        const filesAfter = await getPrChangedFiles(prNumber);
        const filesAfterCount = filesAfter !== null ? filesAfter.size : -1;

        const filesBeforeList = filesBefore !== null ? [...filesBefore].sort().slice(0, 20) : [];
        const filesAfterList = filesAfter !== null ? [...filesAfter].sort().slice(0, 20) : [];
        const addedFiles =
          filesBefore !== null && filesAfter !== null
            ? [...filesAfter]
                .filter((f) => !filesBefore.has(f))
                .sort()
                .slice(0, 10)
            : [];
        const removedFiles =
          filesBefore !== null && filesAfter !== null
            ? [...filesBefore]
                .filter((f) => !filesAfter.has(f))
                .sort()
                .slice(0, 10)
            : [];

        if (filesBeforeCount > 0 && filesAfterCount > filesBeforeCount) {
          log(
            `⚠️  Warning: Changed files increased after rebase (${filesBeforeCount} → ${filesAfterCount}). Possible unintended changes mixed in.`,
            jsonMode,
          );
          logCiMonitorEvent(prNumber, "rebase_file_increase", "warning", {
            before: filesBeforeCount,
            after: filesAfterCount,
            diff: filesAfterCount - filesBeforeCount,
            added_files: addedFiles,
          });
        }

        logCiMonitorEvent(prNumber, "rebase", "success", {
          attempt: rebaseCount,
          max_rebase: maxRebase,
          files_before_count: filesBeforeCount,
          files_after_count: filesAfterCount,
          files_before: filesBeforeList,
          files_after: filesAfterList,
          added_files: addedFiles,
          removed_files: removedFiles,
        });

        if (rebaseCount >= 2) {
          log(
            `⚠️ ${rebaseCount}回目のリベースが必要でした（並行作業が多い可能性。merge queue検討を推奨）`,
            jsonMode,
          );
        }

        // Sync local branch after remote rebase
        const branchName = await getPrBranchName(prNumber);
        if (branchName) {
          const syncSuccess = await syncLocalAfterRebase(branchName, jsonMode);
          if (!syncSuccess) {
            log("Local sync failed (uncommitted changes?). Manual sync may be needed.", jsonMode);
          }
        }

        await Bun.sleep(10000);
        // Reset start time after successful rebase
        startTime = Date.now();
        continue;
      }
      if (rebaseResult.conflict) {
        log("Rebase failed: conflict detected", jsonMode);
      } else {
        log("Rebase failed", jsonMode);
      }
      logCiMonitorEvent(prNumber, "rebase", "failure", {
        attempt: rebaseCount + 1,
        max_rebase: maxRebase,
        conflict: rebaseResult.conflict,
        error_message: rebaseResult.errorMessage?.slice(0, 200) || null,
      });
      await finalizeMonitoring(false, "Rebase failed");
      return {
        success: false,
        message: "Rebase failed",
        rebase_count: rebaseCount,
        final_state: state,
      };
    }
    if (state.mergeState === "DIRTY") {
      const dirtyMsg = "Conflict detected (DIRTY). Manual resolution required.";
      await finalizeMonitoring(false, dirtyMsg);
      return {
        success: false,
        message: dirtyMsg,
        rebase_count: rebaseCount,
        final_state: state,
      };
    }

    // Check review completion
    let currentAiPending = await hasAiReviewPending(prNumber, state.pendingReviewers);

    // Issue #3520: Check for BLOCKED state and report unresolved threads (log only once)
    // Only check when AI review is pending to avoid redundant API call before success block
    if (currentAiPending && state.mergeState === "BLOCKED" && state.checkStatus === "success") {
      if (!hasLoggedBlockedReason) {
        const unresolvedThreads = await getUnresolvedThreads(prNumber);
        if (unresolvedThreads && unresolvedThreads.length > 0) {
          log(
            `⚠️  Merge State: BLOCKED - ${unresolvedThreads.length} unresolved review thread(s) found`,
            jsonMode,
            jsonMode
              ? {
                  unresolved_count: unresolvedThreads.length,
                  threads: unresolvedThreads.slice(0, 5).map((t) => ({
                    author: t.author,
                    path: t.path,
                  })),
                }
              : undefined,
          );
          log("   → Resolve all review threads before merge", jsonMode);
        } else if (unresolvedThreads === null) {
          log("⚠️  Merge State: BLOCKED - Cannot determine reason (API error)", jsonMode);
        } else {
          log("⚠️  Merge State: BLOCKED - Reason unknown (check branch protection rules)", jsonMode);
        }
        hasLoggedBlockedReason = true;
      }
    } else if (state.mergeState !== "BLOCKED") {
      // Reset flag only when mergeState changes from BLOCKED
      hasLoggedBlockedReason = false;
    }

    // Reset retry-in-progress flag when new pending reviewer appears
    if (copilotRetryInProgress && currentAiPending) {
      copilotRetryInProgress = false;
      copilotRetryWaitPolls = 0;
      log("New AI reviewer detected, retry wait complete", jsonMode);
    }

    // Track Copilot pending state and check for timeout
    if (currentAiPending && hasCopilotOrCodexReviewer(state.pendingReviewers)) {
      if (copilotPendingSince === null) {
        copilotPendingSince = Date.now();
        log("Copilot pending timer started", jsonMode);
      } else {
        const pendingDuration = (Date.now() - copilotPendingSince) / 1000;
        if (
          pendingDuration > DEFAULT_COPILOT_PENDING_TIMEOUT &&
          prRecreateCount < DEFAULT_MAX_PR_RECREATE
        ) {
          log(
            `Copilot pending timeout (${pendingDuration.toFixed(0)}s > ${DEFAULT_COPILOT_PENDING_TIMEOUT}s), recreating PR...`,
            jsonMode,
          );
          const [success, newPrNumber, message] = await recreatePr(prNumber);
          prRecreateCount += 1;
          copilotPendingSince = null;
          if (success) {
            log(message, jsonMode);
            let recreateMsg: string;
            let details: Record<string, unknown>;
            if (newPrNumber) {
              recreateMsg = `PR再作成完了: 新PR #${newPrNumber} を監視してください`;
              details = { recreated_pr: newPrNumber, original_pr: prNumber };
            } else {
              recreateMsg = "PR再作成完了: 新しいPRのURLを確認してください";
              details = { original_pr: prNumber };
            }
            await finalizeMonitoring(false, recreateMsg);
            return {
              success: false,
              message: recreateMsg,
              rebase_count: rebaseCount,
              final_state: state,
              review_completed: false,
              ci_passed: state.checkStatus === "success",
              details,
            };
          }
          log(`PR再作成に失敗しました: ${message}`, jsonMode);
        }
      }
    } else {
      if (copilotPendingSince !== null) {
        copilotPendingSince = null;
      }
    }

    // Track Gemini pending state and check for timeout
    const geminiIsPending = hasGeminiReviewer(state.pendingReviewers);
    if (geminiIsPending) {
      if (geminiTimedOut) {
        if (!hasCopilotOrCodexReviewer(state.pendingReviewers)) {
          if (!(await isCodexReviewPending(prNumber))) {
            currentAiPending = false;
          }
        }
      } else if (geminiPendingSince === null) {
        geminiPendingSince = Date.now();
        log("Gemini待機タイマー開始", jsonMode);
      } else {
        const pendingDuration = (Date.now() - geminiPendingSince) / 1000;
        if (pendingDuration > DEFAULT_GEMINI_PENDING_TIMEOUT) {
          log(
            `Gemini待機タイムアウト (${pendingDuration.toFixed(0)}s > ${DEFAULT_GEMINI_PENDING_TIMEOUT}s), Gemini待機をスキップ...`,
            jsonMode,
          );
          geminiTimedOut = true;
          if (!hasCopilotOrCodexReviewer(state.pendingReviewers)) {
            if (!(await isCodexReviewPending(prNumber))) {
              currentAiPending = false;
              log("他のAIレビュー待機なし、Geminiなしで続行", jsonMode);
            }
          }
        }
      }
    } else {
      if (geminiPendingSince !== null) {
        geminiPendingSince = null;
      }
      if (geminiTimedOut) {
        geminiTimedOut = false;
      }
    }

    if (!reviewNotified && !currentAiPending) {
      if (hasCopilotOrCodexReviewer(previousReviewers) || wasCodexPending || wasGeminiPending) {
        const [isError, errorMessage] = await isCopilotReviewError(prNumber);
        if (isError) {
          if (copilotRetryInProgress) {
            if (handleCopilotRetryWait() === "CONTINUE") {
              await Bun.sleep(adjustedInterval * 1000);
              continue;
            }
          }

          if (await executeCopilotRetry(errorMessage || "")) {
            await Bun.sleep(adjustedInterval * 1000);
            continue;
          }

          log(
            "Copilot review failed with error!",
            jsonMode,
            jsonMode ? { error_message: errorMessage } : undefined,
          );
          const copilotErrorMsg = formatCopilotError(errorMessage);
          await finalizeMonitoring(false, copilotErrorMsg);
          return {
            success: false,
            message: copilotErrorMsg,
            rebase_count: rebaseCount,
            final_state: state,
            review_completed: false,
            ci_passed: state.checkStatus === "success",
          };
        }

        reviewNotified = true;
        copilotRetryCount = 0;
        copilotRetryInProgress = false;
        copilotRetryWaitPolls = 0;

        // Auto-resolve duplicate threads after rebase
        let resolvedDuplicateHashes = new Set<string>();
        if (preRebaseHashes.size > 0) {
          const [resolvedCount, hashes] = await autoResolveDuplicateThreads(
            prNumber,
            preRebaseHashes,
            jsonMode,
          );
          resolvedDuplicateHashes = hashes;
          if (resolvedCount > 0) {
            log(
              `Auto-resolved ${resolvedCount} duplicate thread(s) after rebase`,
              jsonMode,
              jsonMode ? { resolved_count: resolvedCount } : undefined,
            );
          }
          preRebaseHashes = new Set();
        }

        const previousComments = state.reviewComments;

        // Issue #3870: Fetch all three types of AI review comments
        // Use rawInlineComments to avoid double-fetching (Codex/Gemini review feedback)
        const allAiComments = await getAllAiReviewComments(prNumber);

        // Filter duplicate inline comments first (Gemini review: apply filter before count)
        const filteredInlineComments = filterDuplicateComments(
          allAiComments.inlineComments,
          resolvedDuplicateHashes,
        );

        // Calculate total AI comment count using filtered inline comments
        const totalAiCommentCount =
          filteredInlineComments.length +
          allAiComments.reviewBodies.length +
          allAiComments.conversationComments.length;

        // For backward compatibility, continue using inline comments for main processing
        // Use rawInlineComments from getAllAiReviewComments to avoid redundant API call
        let comments = allAiComments.rawInlineComments;
        comments = filterDuplicateComments(comments || [], resolvedDuplicateHashes);

        // Check for Qodo compliance violations
        const [hasQodoViolation, qodoViolations] = await hasQodoComplianceViolation(prNumber);
        if (hasQodoViolation) {
          for (const violation of qodoViolations) {
            comments.push({
              body: `🔴 Qodo Compliance Violation: ${violation}`,
              path: "",
              line: undefined,
              user: "qodo-code-review[bot]",
              id: 0,
            });
          }
        }

        // Detect potential contradictions
        checkAndReportContradictions(comments, previousComments || [], jsonMode);

        state.reviewComments = comments;
        await logReviewCommentsToQualityLog(prNumber, comments);

        const classified = await classifyReviewComments(prNumber, comments);

        log(
          "Review completed!",
          jsonMode,
          jsonMode
            ? {
                // Log only metadata to avoid exposing sensitive info in comment bodies
                review_comments: comments.map((c) => ({
                  id: c.id,
                  path: c.path,
                  line: c.line,
                  user: c.user,
                })),
                // Issue #3870: Use totalAiCommentCount to include all AI comment types
                requires_action: totalAiCommentCount > 0,
                comment_count: comments.length,
                in_scope_count: classified?.inScope?.length ?? 0,
                out_of_scope_count: classified?.outOfScope?.length ?? 0,
                // Issue #3870: Include all AI comment types in log (use filtered counts)
                all_ai_comments: {
                  inline_count: filteredInlineComments.length,
                  review_body_count: allAiComments.reviewBodies.length,
                  conversation_count: allAiComments.conversationComments.length,
                  total_count: totalAiCommentCount,
                },
              }
            : undefined,
        );

        // Early exit when review comments are detected
        // Issue #3870: Use totalAiCommentCount to include all AI comment types
        if (
          earlyExit &&
          totalAiCommentCount > 0 &&
          state.checkStatus !== "failure" &&
          state.checkStatus !== "cancelled"
        ) {
          log("Early exit: Review comments detected, exiting for immediate action", jsonMode);
          const earlyExitMsg = `Review comments detected (${totalAiCommentCount} comments including review bodies and conversations) - early exit for shift-left`;
          await finalizeMonitoring(true, earlyExitMsg, { reviewCompleted: true });
          return {
            success: true,
            message: earlyExitMsg,
            rebase_count: rebaseCount,
            final_state: state,
            review_completed: true,
            ci_passed: state.checkStatus === "success",
          };
        }
      }
    }

    // Update previous reviewers for next iteration
    previousReviewers = state.pendingReviewers;
    wasCodexPending = await isCodexReviewPending(prNumber);
    wasGeminiPending = hasGeminiReviewer(state.pendingReviewers);

    // Check CI status
    if (state.checkStatus === "success") {
      log("CI passed!", jsonMode);
      const elapsedTime = Math.floor((Date.now() - startTime) / 1000);
      logCiMonitorEvent(prNumber, "ci_state_change", "success", {
        elapsed_seconds: elapsedTime,
        poll_iterations: pollIteration,
        rebase_count: rebaseCount,
      });

      // After CI passes, Copilot may be re-requested after rebase
      if (rebaseCount > 0 && !rebaseReviewChecked) {
        log("Checking for async AI reviewer re-requests after rebase...", jsonMode);
        await Bun.sleep(ASYNC_REVIEWER_CHECK_DELAY_SECONDS * 1000);
        const [refreshedState, refreshError] = await getPrState(prNumber);
        if (refreshedState === null) {
          const errorDetail = refreshError ? `: ${refreshError}` : "";
          log(`Failed to refresh PR state${errorDetail}, retrying...`, jsonMode);
          await Bun.sleep(adjustedInterval * 1000);
          continue;
        }

        if (refreshedState.mergeState === "BEHIND" || refreshedState.mergeState === "DIRTY") {
          log(
            `Merge state changed to ${refreshedState.mergeState} after refresh, restarting loop...`,
            jsonMode,
          );
          continue;
        }
        if (refreshedState.checkStatus !== "success") {
          log(
            `CI status changed to ${refreshedState.checkStatus} after refresh, restarting loop...`,
            jsonMode,
          );
          continue;
        }

        rebaseReviewChecked = true;
        if (await hasAiReviewPending(prNumber, refreshedState.pendingReviewers)) {
          log("AI reviewer re-requested after rebase, waiting for review...", jsonMode);
          previousReviewers = refreshedState.pendingReviewers;
          wasCodexPending = await isCodexReviewPending(prNumber);
          wasGeminiPending = hasGeminiReviewer(refreshedState.pendingReviewers);
          reviewNotified = false;
          copilotRetryCount = 0;
          copilotRetryInProgress = false;
          copilotRetryWaitPolls = 0;
          geminiPendingSince = null;
          geminiTimedOut = false;
          await Bun.sleep(adjustedInterval * 1000);
          continue;
        }
      }

      // If review is not yet completed, continue waiting
      if (!reviewNotified) {
        if (await hasAiReviewPending(prNumber, state.pendingReviewers)) {
          if (copilotRetryInProgress) {
            copilotRetryInProgress = false;
            copilotRetryWaitPolls = 0;
            log("New AI reviewer detected, retry wait complete", jsonMode);
          }
          log("Waiting for AI review to complete...", jsonMode);
          await Bun.sleep(adjustedInterval * 1000);
          continue;
        }
        const [isError, errorMessage] = await isCopilotReviewError(prNumber);
        if (isError) {
          if (copilotRetryInProgress) {
            if (handleCopilotRetryWait() === "CONTINUE") {
              await Bun.sleep(adjustedInterval * 1000);
              continue;
            }
          }

          if (await executeCopilotRetry(errorMessage || "")) {
            await Bun.sleep(adjustedInterval * 1000);
            continue;
          }

          log(
            "Copilot review failed with error!",
            jsonMode,
            jsonMode ? { error_message: errorMessage } : undefined,
          );
          const copilotErrorMsg = formatCopilotError(errorMessage);
          await finalizeMonitoring(false, copilotErrorMsg);
          return {
            success: false,
            message: copilotErrorMsg,
            rebase_count: rebaseCount,
            final_state: state,
            review_completed: false,
            ci_passed: state.checkStatus === "success",
          };
        }
        log("No pending AI reviewers detected, proceeding...", jsonMode);
      }

      // Check for unresolved review threads
      let unresolved = await getUnresolvedThreads(prNumber);
      const threadApiFailed = unresolved === null;
      if (threadApiFailed) {
        log("Warning: Failed to fetch review threads (GraphQL API error)", jsonMode);
        unresolved = [];
      }
      if (unresolved && unresolved.length > 0) {
        state.unresolvedThreads = unresolved;
        log(`Warning: ${unresolved.length} unresolved review threads detected`, jsonMode);
      }

      // Always fetch comments if not yet fetched
      if (!state.reviewComments || state.reviewComments.length === 0) {
        const comments = await getReviewComments(prNumber);
        if (comments && comments.length > 0) {
          state.reviewComments = comments;
        }
      }

      // Issue #3634: Get detailed BLOCKED reason if merge state is not CLEAN
      if (state.mergeState !== "CLEAN") {
        const blockedReason = await getBlockedReason(prNumber, state);
        if (blockedReason) {
          state.blockedReason = blockedReason;
          log(
            `⚠️  Merge State: ${state.mergeState} - ${blockedReason.explanation}`,
            jsonMode,
            jsonMode
              ? {
                  merge_state: state.mergeState,
                  blocked_reason: blockedReason,
                }
              : undefined,
          );
          log(`   → ${blockedReason.suggestedAction}`, jsonMode);
        }
      }

      // Build result message
      const unresolvedCount = unresolved?.length || 0;
      const messageParts = ["CI passed"];
      if (reviewNotified) {
        messageParts.push("and review completed");
      }
      if (threadApiFailed) {
        messageParts.push("(thread status unknown - API error)");
      } else if (unresolvedCount > 0) {
        messageParts.push(`(${unresolvedCount} unresolved thread(s) to address)`);
      }

      logCiMonitorEvent(prNumber, "monitor_complete", "success", {
        total_wait_seconds: Math.floor(elapsed / 1000),
        poll_iterations: pollIteration,
        rebase_count: rebaseCount,
        review_completed: reviewNotified,
        unresolved_threads: unresolvedCount,
      });

      const finalMessage = messageParts.join(" ");
      await finalizeMonitoring(true, finalMessage, { unresolvedThreads: unresolvedCount });
      return {
        success: true,
        message: finalMessage,
        rebase_count: rebaseCount,
        final_state: state,
        review_completed: reviewNotified,
        ci_passed: true,
      };
    }
    if (state.checkStatus === "failure") {
      const failedChecks = state.checkDetails
        .filter((c) => c.state === "FAILURE")
        .map((c) => c.name || "unknown");
      const failureMessage = `CI failed: ${failedChecks.join(", ")}`;
      log(failureMessage, jsonMode);
      const elapsedTime = Math.floor((Date.now() - startTime) / 1000);
      logCiMonitorEvent(prNumber, "ci_state_change", "failure", {
        elapsed_seconds: elapsedTime,
        poll_iterations: pollIteration,
        rebase_count: rebaseCount,
        failed_checks: failedChecks,
      });
      await finalizeMonitoring(false, failureMessage);
      return {
        success: false,
        message: failureMessage,
        rebase_count: rebaseCount,
        final_state: state,
        review_completed: reviewNotified,
        ci_passed: false,
      };
    }
    if (state.checkStatus === "cancelled") {
      log("CI cancelled", jsonMode);
      const elapsedTime = Math.floor((Date.now() - startTime) / 1000);
      logCiMonitorEvent(prNumber, "ci_state_change", "cancelled", {
        elapsed_seconds: elapsedTime,
        poll_iterations: pollIteration,
        rebase_count: rebaseCount,
      });
      await finalizeMonitoring(false, "CI cancelled");
      return {
        success: false,
        message: "CI cancelled",
        rebase_count: rebaseCount,
        final_state: state,
        review_completed: reviewNotified,
        ci_passed: false,
      };
    }

    // Still pending
    const pendingChecks = state.checkDetails
      .filter((c) => c.state === "IN_PROGRESS" || c.state === "PENDING")
      .map((c) => c.name || "unknown");
    const remaining = Math.floor((timeoutMs - elapsed) / 1000);
    log(`Waiting... (${pendingChecks.length} checks pending, ${remaining}s remaining)`, jsonMode);

    // Show wait time utilization hints periodically
    await showWaitTimeHint(prNumber, pollIteration, jsonMode);
    pollIteration += 1;

    // Periodic rate limit check
    if (pollIteration - lastRateLimitCheck >= rateLimitCheckFrequency) {
      ({
        remaining: rateRemaining,
        limit: rateLimit,
        resetTimestamp: resetTs,
      } = await checkRateLimit());
      if (rateLimit > 0) {
        logRateLimitWarning(rateRemaining, rateLimit, resetTs, jsonMode);
        const newInterval = getAdjustedInterval(interval, rateRemaining);
        if (newInterval !== adjustedInterval) {
          const oldInterval = adjustedInterval;
          adjustedInterval = newInterval;
          const direction: IntervalDirection = newInterval < oldInterval ? "decrease" : "increase";
          log(`Adjusted polling interval to ${adjustedInterval}s`, jsonMode);
          logRateLimitEvent(
            "adjusted_interval",
            rateRemaining,
            rateLimit,
            resetTs,
            getCiMonitorSessionId(),
            {
              old_interval: oldInterval,
              new_interval: adjustedInterval,
              direction,
            },
          );
          if (newInterval === interval && oldInterval !== interval) {
            log(`Rate limit recovered - polling interval returned to ${interval}s`, jsonMode);
            logRateLimitEvent(
              "recovered",
              rateRemaining,
              rateLimit,
              resetTs,
              getCiMonitorSessionId(),
              {
                base_interval: interval,
                previous_interval: oldInterval,
              },
            );
          }
        }
      }
      lastRateLimitCheck = pollIteration;
    }

    await Bun.sleep(adjustedInterval * 1000);
  }
}
