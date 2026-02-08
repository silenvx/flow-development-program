/**
 * CI Monitor TypeScript Entry Point
 *
 * Why:
 *   TypeScript migration of ci_monitor.py (5,748 lines, 13 modules)
 *   for unified TypeScript/Bun codebase.
 *
 * What:
 *   Re-exports all ci_monitor modules for easy consumption.
 *   Provides complete CI monitoring functionality in TypeScript.
 *
 * Remarks:
 *   - Issue #3261: ci_monitor.py TypeScript migration
 *   - Phase 4 completion: Main monitor loop
 *
 * Changelog:
 *   - silenvx/dekita#3261: Initial TypeScript migration
 */

// GitHub API functions
export * from "./github_api";

// Event emission and logging
export * from "./events";

// PR operations
export * from "./pr_operations";

// Session management
export * from "./session";

// Worktree management
export * from "./worktree";

// Review comments
export * from "./review_comments";

// Monitor functions (Phase 4)
export * from "./monitor";

// Main monitoring loop (Phase 4)
export * from "./main_loop";

// Re-export from lib modules (Phase 1-2)
export {
  // Types
  type EventType,
  type CheckStatus,
  type MergeState,
  type RetryWaitStatus,
  type RateLimitEventType,
  type IntervalDirection,
  type PRState,
  type MonitorEvent,
  type MonitorResult,
  type ClassifiedComments,
  type RebaseResult,
  type CodexReviewRequest,
  type MultiPREvent,
  type RateLimitInfo,
  hasUnresolvedThreads,
} from "../../hooks/lib/types";

export {
  // Constants
  DEFAULT_TIMEOUT_MINUTES,
  DEFAULT_POLLING_INTERVAL,
  DEFAULT_MAX_REBASE,
  RATE_LIMIT_WARNING_THRESHOLD,
  RATE_LIMIT_CRITICAL_THRESHOLD,
  RATE_LIMIT_ADJUST_THRESHOLD,
  RATE_LIMIT_REST_PRIORITY_THRESHOLD,
  RATE_LIMIT_CACHE_TTL,
  AI_REVIEWER_IDENTIFIERS,
} from "../../hooks/lib/constants";

export {
  // Rate limit functions
  checkRateLimit,
  getRateLimitResetTime,
  getAdjustedInterval,
  shouldPreferRestApi,
  printRateLimitWarning,
  logRateLimitEvent,
  clearRateLimitCache,
} from "../../hooks/lib/rate_limit";

export {
  // Monitor state functions
  getStateFilePath,
  saveMonitorState,
  loadMonitorState,
  clearMonitorState,
} from "../../hooks/lib/monitor_state";

export {
  // AI review functions
  isAiReviewer,
  hasCopilotOrCodexReviewer,
  getCodexReviewRequests,
  getCodexReviews,
  getCopilotReviews,
  getGeminiReviews,
  isCopilotReviewError,
  isGeminiRateLimited,
  isCoderabbitRateLimited,
  isGeminiReviewPending,
  hasQodoComplianceViolation,
} from "../../hooks/lib/ci_monitor_ai_review";
