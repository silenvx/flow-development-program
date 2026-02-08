/**
 * フック共通の定数を一元管理
 *
 * Why:
 *   複数のフックで使用する定数を一箇所で管理し、一貫性を保つ
 *
 * What:
 *   - タイムアウト定数
 *   - ログ設定
 *   - セッション設定
 *   - 継続ヒントメッセージ
 *   - CI Monitor設定（Issue #3261で統合）
 *
 * Remarks:
 *   - Python scripts/ci_monitor/constants.py から移行（Issue #3261）
 *
 * Changelog:
 *   - silenvx/dekita#2814: 初期実装
 *   - silenvx/dekita#3261: ci_monitor定数を統合
 */

// =============================================================================
// Timeout Constants (Issue #559)
// =============================================================================

/** Light operations: git rev-parse, git status, git symbolic-ref */
export const TIMEOUT_LIGHT = 5;

/** Medium operations: gh api (single), git log, gh issue view */
export const TIMEOUT_MEDIUM = 10;

/** Heavy operations: gh api --paginate, GraphQL queries, lint */
export const TIMEOUT_HEAVY = 30;

/** Extended operations: batch processing, metrics collection */
export const TIMEOUT_EXTENDED = 60;

/** Long operations: AI review (Gemini/Codex), may take 2-3 minutes */
export const TIMEOUT_LONG = 180;

// =============================================================================
// Session Settings
// =============================================================================

/** Session-only directory for temporary state (markers, locks)
 * Cleared on reboot, which is appropriate for session-scoped data
 * Python equivalent: common.py SESSION_DIR */
export const SESSION_DIR = `${process.env.TMPDIR ?? "/tmp"}/claude-hooks`;

/** Session gap threshold (seconds) - if last activity was more than this ago,
 * treat it as a new session. */
export const SESSION_GAP_THRESHOLD = 3600; // 1 hour

/** Threshold in seconds for "recent" commits (1 hour) */
export const RECENT_COMMIT_THRESHOLD_SECONDS = 3600;

/** Session marker file name */
export const SESSION_MARKER_FILE = ".claude-session";

// =============================================================================
// Log Settings (Issue #710)
// =============================================================================

/** Max log file size before rotation (10MB) */
export const LOG_MAX_SIZE_BYTES = 10 * 1024 * 1024;

/** Number of rotated files to keep */
export const LOG_MAX_ROTATED_FILES = 5;

/** Execution log directory relative path */
export const EXECUTION_LOG_DIR = ".claude/logs/execution";

/** Metrics log directory relative path */
export const METRICS_LOG_DIR = ".claude/logs/metrics";

/** Flow log directory relative path */
export const FLOW_LOG_DIR = ".claude/logs/flow";

/** Markers log directory relative path */
export const MARKERS_LOG_DIR = ".claude/logs/markers";

// =============================================================================
// Log Level Separation (Issue #1367)
// =============================================================================

export const ERROR_LOG_FILE = "hook-errors.log";
export const WARN_LOG_FILE = "hook-warnings.log";
export const DEBUG_LOG_FILE = "hook-debug.log";

export const ERROR_CONTEXT_BUFFER_SIZE = 10;
export const ERROR_CONTEXT_AFTER_SIZE = 5;
export const ERROR_CONTEXT_DIR = "error-context";
export const ERROR_CONTEXT_RETENTION_DAYS = 7;

/** Log level decision values */
export const LOG_LEVEL_ERROR_DECISIONS = new Set(["block", "error"]);
export const LOG_LEVEL_WARN_DECISIONS = new Set(["warn", "warning"]);
export const LOG_LEVEL_DEBUG_DECISIONS = new Set([
  "monitor_start",
  "monitor_complete",
  "info",
  "rebase",
]);

// =============================================================================
// Messages
// =============================================================================

/**
 * Continuation hint for block messages (Issue #729)
 * This message reminds Claude to continue with alternative actions after a block
 */
export const CONTINUATION_HINT =
  "\n\n💡 ブロック後も作業を継続してください。\n" +
  "代替アクションのツール呼び出しを行い、テキストのみの応答で終わらないでください。";

// =============================================================================
// Research/Exploration Settings
// =============================================================================

/** Minimum exploration depth for bypassing research requirement */
export const MIN_EXPLORATION_FOR_BYPASS = 5;

// =============================================================================
// File Size Thresholds (Issue #2625)
// =============================================================================

/** File size threshold for TypeScript/JavaScript files (lines) */
export const FILE_SIZE_THRESHOLD_TS = 400;

/** File size threshold for Python files (lines) */
export const FILE_SIZE_THRESHOLD_PY = 500;

/** File size threshold for other files (lines) */
export const FILE_SIZE_THRESHOLD_DEFAULT = 500;

// =============================================================================
// AI Review Constants (Issue #3106)
// =============================================================================

/**
 * Gemini priority badge patterns for non-security findings.
 * Format: ![low|medium|high] or [low|medium|high] (supports both markdown image and bracket notation)
 * Ordered from highest to lowest severity for correct detection.
 */
export const GEMINI_PRIORITY_BADGES: Record<string, RegExp> = {
  high: /!?\[high\]/i,
  medium: /!?\[medium\]/i,
  low: /!?\[low\]/i,
};

/**
 * Gemini security badge patterns.
 * Format: ![security-{level}] or [security-{level}] (supports both markdown image and bracket notation)
 * Ordered from highest to lowest severity for correct detection.
 */
export const GEMINI_SECURITY_BADGES: Record<string, RegExp> = {
  "security-critical": /!?\[security-critical\]/i,
  "security-high": /!?\[security-high\]/i,
  "security-medium": /!?\[security-medium\]/i,
};

/**
 * Codex priority badge patterns (Issue #3530).
 * Format: ![P0|P1|P2|P3 Badge](https://img.shields.io/badge/P0|P1|P2|P3-...)
 * Codex uses shields.io badge images in review comments.
 */
export const CODEX_PRIORITY_BADGES: Record<string, RegExp> = {
  P0: /!\[P0[^\]]*\]\(https:\/\/img\.shields\.io\/badge\/P0/i,
  P1: /!\[P1[^\]]*\]\(https:\/\/img\.shields\.io\/badge\/P1/i,
  P2: /!\[P2[^\]]*\]\(https:\/\/img\.shields\.io\/badge\/P2/i,
  P3: /!\[P3[^\]]*\]\(https:\/\/img\.shields\.io\/badge\/P3/i,
};

/**
 * Codex bot user name (Issue #3530).
 * Used by batch_resolve_threads to detect Codex review comments.
 */
export const CODEX_BOT_USER = "chatgpt-codex-connector[bot]";

/** Severity levels that require response (MEDIUM and above) - blocks push */
export const BLOCKING_SEVERITIES = new Set([
  "high",
  "medium",
  "security-critical",
  "security-high",
  "security-medium",
  "P0",
  "P1",
]);

/**
 * Severity levels that require attention but don't block push.
 * P2/P3 issues should be addressed or tracked in an Issue.
 * Issue #3167: Added to ensure P2 findings are not silently ignored.
 * Issue #3530: Added P3 for Codex low-priority findings.
 */
export const WARNING_SEVERITIES = new Set(["low", "P2", "P3"]);

/** Gemini bot user name */
export const GEMINI_BOT_USER = "gemini-code-assist[bot]";

/** Marker file prefix for pending review issues */
export const PENDING_REVIEW_MARKER_PREFIX = "pending-review-";

// =============================================================================
// Action Keywords (Issue #3096)
// =============================================================================

/**
 * Action keywords indicating a fix/response.
 * Used by: batch_resolve_threads.ts, review_checker.ts
 *
 * Design note: These keywords indicate that an action was taken (e.g., "修正しました")
 * rather than dismissing a review. When present, dismissal keywords are interpreted
 * as design context, not dismissal reasons.
 */
export const ACTION_KEYWORDS = [
  "修正しました",
  "対応しました",
  "実装しました",
  "変更しました",
  "追加しました",
  "削除しました",
  "更新しました",
  "修正済み",
  "verified:",
  "検証済み",
  "確認済み",
] as const;

/**
 * Keywords that should use prefix-negative matching to avoid false positives.
 * Maps keyword -> negative prefix (if keyword appears after this prefix, it's not a match)
 * Issue #3096: "verified:" matches "unverified:" without this handling
 */
export const ACTION_KEYWORD_NEGATIVE_PREFIXES: Record<string, string> = {
  "verified:": "un", // "verified:" should not match "unverified:"
};

/**
 * Check if text contains keyword, handling negative prefix cases.
 *
 * Issue #3096: Simple substring matching for "verified:" incorrectly matches
 * "unverified:". This function handles such cases by checking for negative
 * prefixes.
 */
export function matchesActionKeyword(text: string, keyword: string): boolean {
  const textLower = text.toLowerCase();
  const keywordLower = keyword.toLowerCase();

  let idx = 0;
  while (true) {
    const pos = textLower.indexOf(keywordLower, idx);
    if (pos === -1) {
      return false;
    }

    // Check for negative prefix
    const negativePrefix = ACTION_KEYWORD_NEGATIVE_PREFIXES[keywordLower];
    if (negativePrefix) {
      const prefixLen = negativePrefix.length;
      if (pos >= prefixLen) {
        const potentialPrefix = textLower.slice(pos - prefixLen, pos);
        if (potentialPrefix === negativePrefix) {
          // This occurrence is preceded by negative prefix, skip it
          idx = pos + 1;
          continue;
        }
      }
    }

    // Found a valid match
    return true;
  }
}

/**
 * Check if text contains any action keyword.
 *
 * Issue #3096: Centralized action keyword detection with proper handling
 * of partial match cases like "verified:" vs "unverified:".
 */
export function hasActionKeyword(text: string): boolean {
  for (const keyword of ACTION_KEYWORDS) {
    if (matchesActionKeyword(text, keyword)) {
      return true;
    }
  }
  return false;
}

// =============================================================================
// CI Monitor Constants (Issue #3261 - migrated from Python)
// =============================================================================

/** Maximum number of rebase attempts before giving up */
export const DEFAULT_MAX_REBASE = 3;

/** Polling interval in seconds for CI status checks */
export const DEFAULT_POLLING_INTERVAL = 30;

/** Default timeout in minutes for CI monitoring */
export const DEFAULT_TIMEOUT_MINUTES = 20;

/** Maximum number of Copilot review retry attempts */
export const DEFAULT_MAX_COPILOT_RETRY = 3;

/** Maximum number of retry wait polls */
export const DEFAULT_MAX_RETRY_WAIT_POLLS = 4;

/** Copilot pending timeout in seconds (20 minutes) */
export const DEFAULT_COPILOT_PENDING_TIMEOUT = 1200;

/** Maximum number of PR recreate attempts */
export const DEFAULT_MAX_PR_RECREATE = 1;

/** Maximum number of merge attempts */
export const DEFAULT_MAX_MERGE_ATTEMPTS = 3;

// -----------------------------------------------------------------------------
// Local Changes Wait Configuration (Issue #1307)
// -----------------------------------------------------------------------------

/** Maximum number of local changes wait iterations */
export const DEFAULT_LOCAL_CHANGES_MAX_WAIT = 5;

/** Wait interval in seconds between local changes checks */
export const DEFAULT_LOCAL_CHANGES_WAIT_INTERVAL = 60;

// -----------------------------------------------------------------------------
// Main Branch Stability Wait Strategy (Issue #1239)
// -----------------------------------------------------------------------------

/** Minutes to wait for main branch stability */
export const DEFAULT_STABLE_WAIT_MINUTES = 5;

/** Interval in seconds between stability checks */
export const DEFAULT_STABLE_CHECK_INTERVAL = 30;

/** Timeout in minutes for stability wait */
export const DEFAULT_STABLE_WAIT_TIMEOUT = 30;

// -----------------------------------------------------------------------------
// Merge Error Constants
// -----------------------------------------------------------------------------

/** Merge state status indicating PR is behind base branch */
export const MERGE_ERROR_BEHIND = "BEHIND";

// -----------------------------------------------------------------------------
// Rate Limit Thresholds (Issue #896)
// -----------------------------------------------------------------------------

/** Warning threshold for remaining API calls */
export const RATE_LIMIT_WARNING_THRESHOLD = 100;

/** Critical threshold for remaining API calls */
export const RATE_LIMIT_CRITICAL_THRESHOLD = 50;

/** Threshold for adjusting API call frequency */
export const RATE_LIMIT_ADJUST_THRESHOLD = 500;

/** Value indicating rate limit is exhausted */
export const RATE_LIMIT_EXHAUSTED = 0;

/** Threshold for prioritizing REST API over GraphQL */
export const RATE_LIMIT_REST_PRIORITY_THRESHOLD = 200;

/** Cache TTL for rate limit info in seconds (Issue #1347, #1291) */
export const RATE_LIMIT_CACHE_TTL = 60;

// -----------------------------------------------------------------------------
// AI Reviewer Constants (Issue #1109, #2711, #3164, #3170, #3196)
// -----------------------------------------------------------------------------

/** Delay in seconds before checking async reviewer status */
export const ASYNC_REVIEWER_CHECK_DELAY_SECONDS = 5;

/**
 * Copilot/Codex identifiers for pending reviewer check.
 * Used by has_copilot_or_codex_reviewer() to detect pending AI reviewers.
 */
export const COPILOT_CODEX_IDENTIFIERS = [
  "copilot",
  "codex",
  "openai",
  "chatgpt",
  "cubic-dev",
] as const;

/**
 * All AI reviewer identifiers for comment/review author detection.
 * Used by is_ai_reviewer() to identify AI-generated reviews.
 * Note: CodeRabbit has special handling for rate limit detection.
 * Note: Qodo has special handling for compliance violation detection.
 */
export const AI_REVIEWER_IDENTIFIERS = [
  ...COPILOT_CODEX_IDENTIFIERS,
  "gemini",
  "coderabbit",
  "qodo",
  "greptile",
  "sourcery",
  "sweep",
  "metabob",
] as const;

/** Copilot reviewer bot login name for API requests */
export const COPILOT_REVIEWER_LOGIN = "copilot-pull-request-reviewer[bot]";

/** Gemini Code Assist reviewer bot login name (Issue #2711) */
export const GEMINI_REVIEWER_LOGIN = "gemini-code-assist[bot]";

/**
 * Gemini pending timeout in seconds (Issue #2711).
 * How long to wait for Gemini review before skipping (20 minutes).
 * If Gemini doesn't respond and doesn't indicate rate limiting within this time, skip waiting.
 */
export const DEFAULT_GEMINI_PENDING_TIMEOUT = 1200;

/** CodeRabbit reviewer bot login name (Issue #3170) */
export const CODERABBIT_REVIEWER_LOGIN = "coderabbitai[bot]";

/**
 * CodeRabbit rate limit retry wait time in seconds (Issue #3170).
 * CodeRabbit shows wait time in its rate limit message, but we have a default if parse fails.
 */
export const DEFAULT_CODERABBIT_RETRY_WAIT = 60;

/** Qodo reviewer bot login name (Issue #3196) */
export const QODO_REVIEWER_LOGIN = "qodo-code-review[bot]";

// -----------------------------------------------------------------------------
// GitHub API Settings
// -----------------------------------------------------------------------------

/** GitHub API pagination limit for PR files */
export const GITHUB_FILES_LIMIT = 100;

// -----------------------------------------------------------------------------
// Pattern Matching (Issue #3196, #3226)
// -----------------------------------------------------------------------------

/**
 * Pattern to match fenced code blocks and inline code.
 * Used to strip code content before checkbox detection to avoid false positives.
 */
export const CODE_BLOCK_PATTERN = /```[\s\S]*?```|`[^`\n]+`/gm;

/**
 * Qodo compliance violation pattern (Issue #3196, #3226).
 * Qodo uses colored circle markers in HTML tables to indicate compliance status:
 * 🔴 = Not Compliant (must block merge)
 * 🟡 = Partial Compliant (warning)
 * 🟢 = Fully Compliant (passed)
 * ⚪ = Requires Further Human Verification
 * Uses \s* to match only whitespace around the emoji (status-only cell format).
 * This prevents false positives from cells containing 🔴 in descriptive text.
 */
export const QODO_COMPLIANCE_VIOLATION_PATTERN = /<td[^>]*>\s*🔴\s*<\/td>/gi;

/**
 * Pattern to detect Qodo compliance report comments (Issue #3226).
 * Matches "## PR Compliance Guide" header that only appears in compliance reports.
 * Used to identify compliance report comments vs. other bot comments (e.g., Code Suggestions).
 * Note: Code Suggestions may also contain 🔴 for priority indicators, but without this header.
 */
export const QODO_COMPLIANCE_REPORT_PATTERN = /## PR Compliance Guide/gi;

/**
 * Pattern to extract violation details from Qodo comment (Issue #3196, #3226).
 * Format: <td>🔴</td><td><details><summary><strong|b>Title</strong|b>...
 * Uses \s* for status cell (consistent with QODO_COMPLIANCE_VIOLATION_PATTERN).
 * Allows both <strong> and <b> tags for robustness against format changes.
 */
export const QODO_VIOLATION_DETAIL_PATTERN =
  /<td[^>]*>\s*🔴\s*<\/td>\s*<td[^>]*>\s*<details[^>]*>\s*<summary[^>]*>[^<]*<(?:strong|b)[^>]*>(.*?)<\/(?:strong|b)>/gis;

/**
 * Pattern to extract Qodo's reference commit (Issue #3268, #3308).
 * Supports both old text format and new HTML comment format:
 * - Old: "Compliance updated until commit https://github.com/.../commit/abc123"
 * - New: "<!-- https://github.com/.../commit/abc123 -->"
 */
export const QODO_REFERENCE_COMMIT_PATTERN =
  /(?:Compliance updated until commit |<!--\s*)https?:\/\/[^/]+\/[^/]+\/[^/]+\/commit\/([a-f0-9]{7,40})/i;

// -----------------------------------------------------------------------------
// Codex Rate Limit Detection (Issue #3310)
// -----------------------------------------------------------------------------

/**
 * Marker file prefix for Codex rate limit status.
 * When Codex CLI returns rate limit error, this marker is created.
 * Format: codex-rate-limit-{branch}.marker
 */
export const CODEX_RATE_LIMIT_MARKER_PREFIX = "codex-rate-limit-";

/**
 * Pattern to detect Codex rate limit error.
 * Codex CLI outputs "usage_limit_reached" when rate limited.
 */
export const CODEX_RATE_LIMIT_PATTERN = /usage_limit_reached/i;

/**
 * Marker file prefix for Gemini review completion.
 * Format: gemini-review-{branch}.done
 */
export const GEMINI_REVIEW_MARKER_PREFIX = "gemini-review-";

// -----------------------------------------------------------------------------
// AI Issue Comment Reviewers (Issue #3391)
// -----------------------------------------------------------------------------

/**
 * AI reviewer bot login names that post issue comments (not PR review comments).
 * These bots post Code Suggestions or summaries as issue comments.
 * Used to detect unresponded AI comments that need human acknowledgment.
 */
export const AI_ISSUE_COMMENT_REVIEWERS = [
  "qodo-code-review[bot]",
  "gemini-code-assist[bot]",
  "chatgpt-codex-connector[bot]",
  "copilot-pull-request-reviewer[bot]",
  "cubic-dev-ai[bot]",
  "coderabbitai[bot]",
  "greptile-apps[bot]",
  "sourcery-ai[bot]",
  "sweep-ai[bot]",
  "metabob[bot]",
] as const;

/**
 * Patterns to exclude from AI issue comment response requirement.
 * These comments are informational and don't require a response.
 *
 * - quota切れメッセージ: "Your monthly quota for Qodo has expired"
 * - Summaryコメント: "## Summary of Changes" or similar headers
 * - 情報のみのコメント: "<pre>ⓘ" prefix (rate limit or info messages)
 * - Compliance report: "## PR Compliance Guide" (checked separately)
 * - Walkthrough: "## Walkthrough" (CodeRabbit summary)
 * - Static analysis: "## Static analysis" (CodeRabbit analysis)
 * - Rate limit: "Your account has reached" (CodeRabbit rate limit)
 */
export const AI_COMMENT_EXCLUDE_PATTERNS: RegExp[] = [
  /Your monthly quota for Qodo has expired/i,
  /^## Summary of Changes/m,
  /^<pre>ⓘ/m,
  // Note: "## PR Compliance Guide" is handled explicitly in isExcludedAiComment
  // to ensure it bypasses AI_SUGGESTION_INDICATORS check
  /^## Walkthrough/m,
  /^## Static analysis/m,
  /Your account has reached/i,
  // Gemini Code Assist daily quota warning (Issue #3570)
  /You have reached your daily quota limit/i,
];

// =============================================================================
// Enforcement Keywords (Issue #3976)
// =============================================================================

/** Keywords indicating enforcement rules in AGENTS.md */
export const ENFORCEMENT_KEYWORDS = [
  "禁止",
  "必須",
  "ブロック",
  "強制",
  "してはならない",
  "してはいけない",
  "MUST",
  "NEVER",
  "REQUIRED",
  "FORBIDDEN",
];

// =============================================================================
// Iterative Plan Review Constants (Issue #3853)
// =============================================================================

/**
 * Maximum iterations before requesting user confirmation.
 * After this many iterations without approval, ask user if they want to continue.
 */
export const PLAN_REVIEW_MAX_ITERATIONS_FOR_CONFIRM = 10;

/**
 * Absolute timeout in minutes for plan review process.
 * After this time, the review process is forcibly terminated.
 */
export const PLAN_REVIEW_ABSOLUTE_TIMEOUT_MINUTES = 30;

// =============================================================================
// Review Cycle Limit Constants (Issue #3984)
// =============================================================================

/**
 * Maximum number of review cycles before allowing LOW-only results to pass.
 * Prevents infinite loops when AI reviewers keep finding new MEDIUM issues
 * on each cycle (e.g., Japanese regex edge cases).
 */
export const MAX_REVIEW_CYCLES = 5;
