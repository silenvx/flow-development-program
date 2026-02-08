import {
  hasCopilotOrCodexReviewer,
  isCopilotReviewError,
} from "../../hooks/lib/ci_monitor_ai_review";
import { DEFAULT_POLLING_INTERVAL, DEFAULT_TIMEOUT_MINUTES } from "../../hooks/lib/constants";
import { logHookExecutionSync } from "../../hooks/lib/execution";
import { asyncSpawn } from "../../hooks/lib/spawn";
import type { EventType, MonitorEvent, MultiPREvent } from "../../hooks/lib/types";
import { createEvent, emitEvent, log } from "./events";
import { runGhCommand } from "./github_api";
import { getPrState } from "./pr_operations";
import {
  getPrChangedFiles,
  getReviewComments,
  getUnresolvedThreads,
  logReviewCommentsToQualityLog,
  stripCodeBlocks,
} from "./review_comments";

// =============================================================================
// Types
// =============================================================================

/** Log action types for ci-monitor events */
type CiMonitorAction =
  | "ci_state_change"
  | "monitor_complete"
  | "monitor_start"
  | "rebase"
  | "rebase_file_increase";

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Sanitize a value for safe logging by removing control characters.
 *
 * @param value - Value to sanitize (handles str, list, object, and other types)
 * @returns Sanitized value with control characters removed from strings
 */
function sanitizeForLog(value: unknown): unknown {
  if (typeof value === "string") {
    // Remove all control characters (0x00-0x1f) except tab (0x09), LF (0x0a), and CR (0x0d)
    // biome-ignore lint/suspicious/noControlCharactersInRegex: Intentional - sanitizing control characters for safe logging
    return value.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, "");
  }
  if (Array.isArray(value)) {
    return value.map(sanitizeForLog);
  }
  if (value !== null && typeof value === "object") {
    const result: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      result[k] = sanitizeForLog(v);
    }
    return result;
  }
  return value;
}

/**
 * Log ci-monitor event to hook-execution.log for post-session analysis.
 *
 * Issue #1241: Log important ci-monitor events (monitor start, rebase,
 * CI state changes, monitor completion) for debugging and post-session analysis.
 *
 * @param prNumber - PR number being monitored
 * @param action - Type of action
 * @param result - Result of the action
 * @param details - Additional details to include in the log entry
 */
export function logCiMonitorEvent(
  prNumber: string,
  action: CiMonitorAction,
  result: string,
  details?: Record<string, unknown>,
): void {
  // Sanitize inputs to prevent log injection (remove control chars)
  const safePr = sanitizeForLog(prNumber) as string;
  const safeResult = sanitizeForLog(result) as string;
  const safeDetails = details ? (sanitizeForLog(details) as Record<string, unknown>) : undefined;

  const eventDetails: Record<string, unknown> = {
    pr_number: safePr,
    action,
    result: safeResult,
  };
  if (safeDetails) {
    Object.assign(eventDetails, safeDetails);
  }

  logHookExecutionSync(
    "ci-monitor",
    action,
    `PR #${safePr}: ${action} - ${safeResult}`,
    eventDetails,
  );
}

/**
 * Get issue numbers being closed by this PR.
 *
 * Extracts issue numbers from Closes/Fixes/Resolves keywords in PR body.
 *
 * @param prNumber - PR number to check
 * @returns List of issue numbers found
 */
export async function getPrClosesIssues(prNumber: string): Promise<string[]> {
  const [success, output] = await runGhCommand([
    "pr",
    "view",
    prNumber,
    "--json",
    "body",
    "--jq",
    ".body",
  ]);

  if (!success || !output) {
    return [];
  }

  // Find blocks starting with closing keywords
  // Handles comma-separated issues: "Closes #123, #456"
  const blockPattern = /(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?):?\s+#\d+(?:\s*,\s*#\d+)*/gi;
  const blocks = output.match(blockPattern) || [];

  // Extract all issue numbers from matched blocks
  const allNumbers: string[] = [];
  for (const block of blocks) {
    const numbers = block.match(/#(\d+)/g) || [];
    allNumbers.push(...numbers.map((n) => n.slice(1)));
  }

  return [...new Set(allNumbers)];
}

/**
 * Get incomplete acceptance criteria for an issue.
 *
 * Fetches issue body and extracts incomplete (unchecked and non-strikethrough) checkbox items.
 *
 * @param issueNumber - Issue number to check
 * @returns List of incomplete criteria text (empty if none or error)
 */
export async function getIssueIncompleteCriteria(issueNumber: string): Promise<string[]> {
  const [success, output] = await runGhCommand([
    "issue",
    "view",
    issueNumber,
    "--json",
    "body,state",
  ]);

  if (!success || !output) {
    return [];
  }

  try {
    const data = JSON.parse(output) as { body?: string; state?: string };
    const body = data.body || "";
    const state = data.state || "";

    // Skip closed Issues
    if (state === "CLOSED") {
      return [];
    }

    // Strip code blocks before extracting checkboxes (Issue #830)
    const bodyWithoutCode = stripCodeBlocks(body);

    // Extract checkbox items
    // Issue #823: Treat strikethrough checkboxes as completed
    const incomplete: string[] = [];
    const pattern = /^[\s]*[-*]\s*\[([ xX])\]\s*(.+)$/;
    const strikethroughPattern = /^~~.+~~$/;

    for (const line of bodyWithoutCode.split("\n")) {
      const match = line.match(pattern);
      if (match) {
        const checkboxMark = match[1].toLowerCase();
        let criteriaText = match[2].trim();
        // Checkbox is incomplete if not marked and not strikethrough
        const isCompleted = checkboxMark === "x" || strikethroughPattern.test(criteriaText);
        if (!isCompleted) {
          // Truncate long criteria text
          if (criteriaText.length > 30) {
            criteriaText = `${criteriaText.slice(0, 27)}...`;
          }
          incomplete.push(`「${criteriaText}」`);
        }
      }
    }

    return incomplete;
  } catch {
    return [];
  }
}

/**
 * Get open issues with observation label.
 *
 * Issue #2583: Check for pending observation issues during CI waits.
 */
export async function getObservationIssues(): Promise<Array<{ number: number; title: string }>> {
  try {
    const result = await asyncSpawn(
      "gh",
      [
        "issue",
        "list",
        "--label",
        "observation",
        "--state",
        "open",
        "--json",
        "number,title",
        "--limit",
        "3",
      ],
      { timeout: 10000 },
    );

    if (!result.success || !result.stdout) {
      return [];
    }

    return JSON.parse(result.stdout) as Array<{ number: number; title: string }>;
  } catch {
    return [];
  }
}

/**
 * Get actionable suggestions for wait time utilization.
 *
 * Checks for unresolved threads and returns specific action suggestions.
 */
export async function getWaitTimeSuggestions(prNumber: string): Promise<string[]> {
  const suggestions: string[] = [];

  // Check for unresolved review threads
  // Issue #1195: Handle API failure (null means API failed, not "no unresolved threads")
  const unresolved = await getUnresolvedThreads(prNumber);
  if (unresolved && unresolved.length > 0) {
    suggestions.push(`未解決スレッド ${unresolved.length}件 → resolveまたはコメント対応`);
  }

  // Check for review comments (may include comments in resolved threads)
  const comments = await getReviewComments(prNumber);
  if (comments && comments.length > 0) {
    suggestions.push(
      `レビューコメント（解決済み含む） ${comments.length}件 → 必要に応じて対応・返信を確認`,
    );
  }

  // Check for incomplete acceptance criteria in Closes target Issues (Issue #831)
  const closesIssues = await getPrClosesIssues(prNumber);
  for (const issueNum of closesIssues) {
    const incomplete = await getIssueIncompleteCriteria(issueNum);
    if (incomplete.length > 0) {
      // Show up to 2 criteria with "等" suffix if more
      let criteriaText = incomplete.slice(0, 2).join(", ");
      if (incomplete.length > 2) {
        criteriaText += "等";
      }
      suggestions.push(`Issue #${issueNum} の受け入れ条件を確認 → 未完了: ${criteriaText}`);
    }
  }

  return suggestions;
}

/**
 * Show wait time utilization hints periodically.
 *
 * @param prNumber - PR number to check for actionable items
 * @param iteration - Current polling iteration count
 * @param jsonMode - Whether to output in JSON format
 * @param hintInterval - Show hints every N iterations (default: 3)
 */
export async function showWaitTimeHint(
  prNumber: string,
  iteration: number,
  jsonMode = false,
  hintInterval = 3,
): Promise<void> {
  // Only show hints every hintInterval iterations (to avoid spam)
  if (iteration % hintInterval !== 0 || iteration === 0) {
    return;
  }

  const suggestions = await getWaitTimeSuggestions(prNumber);
  if (suggestions.length === 0) {
    return;
  }

  if (jsonMode) {
    log("待ち時間の活用提案があります", jsonMode, { suggestions });
  } else {
    console.log("    💡 待ち時間の有効活用:");
    for (const suggestion of suggestions) {
      console.log(`       - ${suggestion}`);
    }
  }
}

/**
 * Check PR state once and return an event if something notable happened.
 *
 * Returns null if no notable event occurred.
 */
export async function checkOnce(
  prNumber: string,
  previousReviewers: string[],
): Promise<MonitorEvent | null> {
  const [state, error] = await getPrState(prNumber);
  if (state === null) {
    const errorDetail = error ? `: ${error}` : "";
    return createEvent(
      "ERROR" as EventType,
      prNumber,
      `PR状態の取得に失敗しました${errorDetail}`,
      undefined,
      "再試行するか、GitHub APIの状態を確認してください",
    );
  }

  // Check merge state
  if (state.mergeState === "BEHIND") {
    return createEvent(
      "BEHIND_DETECTED" as EventType,
      prNumber,
      "ブランチがmainより古くなっています",
      { merge_state: state.mergeState },
      `gh pr update-branch ${prNumber} --rebase`,
    );
  }

  if (state.mergeState === "DIRTY") {
    return createEvent(
      "DIRTY_DETECTED" as EventType,
      prNumber,
      "コンフリクトが検出されました",
      { merge_state: state.mergeState },
      "手動でコンフリクトを解決してください",
    );
  }

  // Check review completion
  const hadAiReviewer = hasCopilotOrCodexReviewer(previousReviewers);
  const hasAiReviewerNow = hasCopilotOrCodexReviewer(state.pendingReviewers);

  if (hadAiReviewer && !hasAiReviewerNow) {
    // Check if Copilot review ended with an error
    const [isError, errorMessage] = await isCopilotReviewError(prNumber);
    if (isError) {
      return createEvent(
        "REVIEW_ERROR" as EventType,
        prNumber,
        "Copilotレビューがエラーで失敗しました",
        { error_message: errorMessage },
        "Copilotレビューを再リクエストしてください",
      );
    }

    const comments = await getReviewComments(prNumber);
    // Log AI review comments for quality tracking (Issue #610)
    await logReviewCommentsToQualityLog(prNumber, comments || []);
    return createEvent(
      "REVIEW_COMPLETED" as EventType,
      prNumber,
      `AIレビューが完了しました（コメント${comments?.length || 0}件）`,
      {
        comment_count: comments?.length || 0,
        comments: comments || [],
      },
      "コメントを確認して対応してください",
    );
  }

  // Check CI status
  if (state.checkStatus === "success") {
    return createEvent(
      "CI_PASSED" as EventType,
      prNumber,
      "全てのCIチェックが成功しました",
      {
        checks: state.checkDetails.map((c) => c.name || "unknown"),
      },
      "マージ可能です",
    );
  }

  if (state.checkStatus === "failure") {
    const failed = state.checkDetails
      .filter((c) => c.state === "FAILURE")
      .map((c) => c.name || "unknown");
    return createEvent(
      "CI_FAILED" as EventType,
      prNumber,
      `CIが失敗しました: ${failed.join(", ")}`,
      {
        failed_checks: failed,
        all_checks: state.checkDetails.map((c) => c.name || "unknown"),
      },
      "失敗したテスト/チェックを修正してください",
    );
  }

  if (state.checkStatus === "cancelled") {
    return createEvent(
      "CI_FAILED" as EventType,
      prNumber,
      "CIがキャンセルされました",
      {
        all_checks: state.checkDetails.map((c) => c.name || "unknown"),
      },
      "CIを再実行するか、キャンセル原因を調査してください",
    );
  }

  // No notable event
  return null;
}

/**
 * Check PR state once and emit an event if something notable happened.
 *
 * Designed for use with Claude Code's parallel task spawning.
 *
 * @returns 0 if an event was emitted, 1 if no notable event
 */
export async function monitorNotifyOnly(prNumber: string): Promise<number> {
  const [state, error] = await getPrState(prNumber);
  if (state === null) {
    const errorDetail = error ? `: ${error}` : "";
    const event = createEvent(
      "ERROR" as EventType,
      prNumber,
      `PR状態の取得に失敗しました${errorDetail}`,
    );
    emitEvent(event);
    return 0;
  }

  // Note: With empty previousReviewers, REVIEW_COMPLETED event won't be
  // detected on first call. This is a design limitation - notify-only mode
  // cannot detect review completion without state from a previous check.
  // Use blocking mode for full review tracking, or call notify-only repeatedly.
  const event = await checkOnce(prNumber, []);
  if (event) {
    emitEvent(event);
    return 0;
  }

  // No notable event - output status
  const pendingChecks = state.checkDetails
    .filter((c) => c.state === "IN_PROGRESS" || c.state === "PENDING")
    .map((c) => c.name || "unknown");
  const logData = {
    type: "status",
    pr_number: prNumber,
    merge_state: state.mergeState,
    check_status: state.checkStatus,
    pending_checks: pendingChecks,
    pending_reviewers: state.pendingReviewers,
  };
  console.log(JSON.stringify(logData));
  return 1;
}

/**
 * Check if this PR modifies ci_monitor itself.
 *
 * When monitoring a PR that changes ci_monitor, the running version
 * may have bugs that are being fixed, leading to confusing behavior.
 *
 * @returns True if ci_monitor files are in the changed files, false otherwise
 */
export async function checkSelfReference(prNumber: string): Promise<boolean> {
  const changedFiles = await getPrChangedFiles(prNumber);

  if (changedFiles === null) {
    return false;
  }

  // Match any path ending with ci_monitor.py, inside the ci_monitor_ts directory,
  // or inside the shared library path that ci_monitor_ts depends on
  return Array.from(changedFiles).some(
    (f) =>
      f.endsWith("ci_monitor.py") ||
      f.includes("ci_monitor_ts/") ||
      f.includes(".claude/hooks/lib/"),
  );
}

/**
 * Monitor a single PR and return when an actionable event occurs.
 *
 * This is a simplified monitor for multi-PR mode that returns as soon as
 * any actionable event (review completed, CI passed/failed, rebase needed) occurs.
 *
 * @param prNumber - PR number to monitor
 * @param interval - Polling interval in seconds
 * @param timeoutMinutes - Timeout in minutes
 * @param shouldStop - Function to check if we should stop early
 * @returns MultiPREvent with the first actionable event detected, or null event if stopped
 */
export async function monitorSinglePrForEvent(
  prNumber: string,
  interval: number = DEFAULT_POLLING_INTERVAL,
  timeoutMinutes: number = DEFAULT_TIMEOUT_MINUTES,
  shouldStop?: () => boolean,
): Promise<MultiPREvent> {
  const startTime = Date.now();
  const timeoutMs = timeoutMinutes * 60 * 1000;

  // Get initial state for reviewer tracking
  const [initialState] = await getPrState(prNumber);
  let previousReviewers = initialState?.pendingReviewers || [];

  while (true) {
    // Check if we should stop (another PR already has an event)
    if (shouldStop?.()) {
      const [stopState] = await getPrState(prNumber);
      return { prNumber, event: null, state: stopState };
    }

    const elapsed = Date.now() - startTime;
    if (elapsed > timeoutMs) {
      const [timeoutState] = await getPrState(prNumber);
      return {
        prNumber,
        event: createEvent(
          "TIMEOUT" as EventType,
          prNumber,
          `${timeoutMinutes}分経過でタイムアウトしました`,
        ),
        state: timeoutState,
      };
    }

    const [state] = await getPrState(prNumber);
    if (state === null) {
      await Bun.sleep(interval * 1000);
      continue;
    }

    // Check for actionable events
    const event = await checkOnce(prNumber, previousReviewers);
    if (event) {
      return { prNumber, event, state };
    }

    // Update previous reviewers for next iteration
    previousReviewers = state.pendingReviewers;

    await Bun.sleep(interval * 1000);
  }
}

/**
 * Monitor multiple PRs in parallel and return on first actionable event.
 *
 * Uses Promise.race to monitor multiple PRs concurrently.
 * Returns immediately when any PR has an actionable event.
 *
 * @param prNumbers - List of PR numbers to monitor
 * @param interval - Polling interval in seconds
 * @param timeoutMinutes - Timeout in minutes for each PR
 * @param jsonMode - Output in JSON format
 * @returns List of MultiPREvent containing events detected before returning
 */
export async function monitorMultiplePrs(
  prNumbers: string[],
  interval: number = DEFAULT_POLLING_INTERVAL,
  timeoutMinutes: number = DEFAULT_TIMEOUT_MINUTES,
  jsonMode = false,
): Promise<MultiPREvent[]> {
  if (prNumbers.length === 0) {
    return [];
  }

  log(`PRの並列監視を開始: ${prNumbers.join(", ")}`, jsonMode);
  log(`ポーリング間隔: ${interval}秒, タイムアウト: ${timeoutMinutes}分/PR`, jsonMode);

  const events: MultiPREvent[] = [];
  let stopped = false;

  // Create monitors for each PR with shared stop flag
  const monitors = prNumbers.map((prNumber) =>
    monitorSinglePrForEvent(prNumber, interval, timeoutMinutes, () => stopped),
  );

  // Return as soon as any PR has an actionable event
  try {
    const firstResult = await Promise.race(monitors);
    if (firstResult.event) {
      stopped = true;
      events.push(firstResult);
      log(`PR #${firstResult.prNumber} でイベント検出: ${firstResult.event.message}`, jsonMode);
    }

    // Wait for remaining monitors to stop (they should check stopped flag)
    const remaining = await Promise.allSettled(monitors);
    for (const result of remaining) {
      if (result.status === "fulfilled" && result.value.event) {
        // Only add if not already added
        if (!events.some((e) => e.prNumber === result.value.prNumber)) {
          events.push(result.value);
        }
      }
    }
  } catch (error) {
    log(`並列監視中にエラー: ${error}`, jsonMode);
  }

  return events;
}
