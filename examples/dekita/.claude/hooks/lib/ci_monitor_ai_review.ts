/**
 * AI reviewer utilities for ci-monitor.
 *
 * Why:
 *   Handle detection and management of various AI reviewers (Copilot, Codex,
 *   Gemini, CodeRabbit, Qodo) during CI monitoring. Different AI reviewers
 *   have different behaviors (rate limits, error patterns, comment formats).
 *
 * What:
 *   - Reviewer detection: is_ai_reviewer, has_copilot_or_codex_reviewer
 *   - Review retrieval: get_codex_reviews, get_copilot_reviews, etc.
 *   - Rate limit detection: is_gemini_rate_limited, is_coderabbit_rate_limited
 *   - Compliance checking: has_qodo_compliance_violation
 *
 * Remarks:
 *   - Migrated from Python ci_monitor/ai_review.py (Issue #3261)
 *   - Separate from ai_review_checker.ts which is for merge-check hooks
 *   - Uses NDJSON parsing for paginated gh api output
 *
 * Changelog:
 *   - silenvx/dekita#3261: TypeScript migration from Python
 */

import {
  extractAllViolations,
  extractInScopeViolations,
  isStaleQodoReport,
} from "./ai_review_checker";
import {
  AI_REVIEWER_IDENTIFIERS,
  CODERABBIT_REVIEWER_LOGIN,
  COPILOT_CODEX_IDENTIFIERS,
  COPILOT_REVIEWER_LOGIN,
  DEFAULT_CODERABBIT_RETRY_WAIT,
  GEMINI_REVIEWER_LOGIN,
  QODO_COMPLIANCE_REPORT_PATTERN,
  QODO_COMPLIANCE_VIOLATION_PATTERN,
  QODO_REVIEWER_LOGIN,
} from "./constants";
import { runGhCommand } from "./github";
import { extractIssueNumbersFromPrBody } from "./issue_checker";
import { parsePaginatedJson } from "./json";
import { asyncSpawn } from "./spawn";
import type { CodexReviewRequest } from "./types";

// =============================================================================
// General AI Reviewer Detection
// =============================================================================

/**
 * Check if the given author is an AI reviewer.
 *
 * @param author - The username/author string to check
 * @returns True if the author is an AI reviewer
 */
export function isAiReviewer(author: string): boolean {
  if (!author) {
    return false;
  }
  const authorLower = author.toLowerCase();
  return AI_REVIEWER_IDENTIFIERS.some((ai) => authorLower.includes(ai));
}

/**
 * Check if Copilot or Codex is in the pending reviewers.
 *
 * Uses COPILOT_CODEX_IDENTIFIERS instead of AI_REVIEWER_IDENTIFIERS
 * to avoid matching Gemini reviewers.
 *
 * @param reviewers - List of pending reviewer login names
 * @returns True if Copilot or Codex is in the list
 */
export function hasCopilotOrCodexReviewer(reviewers: string[]): boolean {
  for (const reviewer of reviewers) {
    const reviewerLower = reviewer.toLowerCase();
    if (COPILOT_CODEX_IDENTIFIERS.some((ai) => reviewerLower.includes(ai))) {
      return true;
    }
  }
  return false;
}

// =============================================================================
// Codex Functions
// =============================================================================

/**
 * Check if a comment has the 👀 (eyes) reaction.
 *
 * @param commentId - The comment ID to check
 * @returns True if the comment has eyes reaction
 */
async function checkEyesReaction(commentId: number): Promise<boolean> {
  const [success, output] = await runGhCommand([
    "api",
    `/repos/{owner}/{repo}/issues/comments/${commentId}/reactions`,
    "--jq",
    '[.[] | select(.content == "eyes")]',
  ]);

  if (!success) {
    return false;
  }

  try {
    const reactions = JSON.parse(output) as unknown[];
    return reactions.length > 0;
  } catch {
    return false;
  }
}

/**
 * Find @codex review comments in the PR.
 *
 * @param prNumber - PR number to check
 * @returns List of CodexReviewRequest objects
 */
export async function getCodexReviewRequests(prNumber: string): Promise<CodexReviewRequest[]> {
  const [success, output] = await runGhCommand([
    "api",
    "--paginate",
    `/repos/{owner}/{repo}/issues/${prNumber}/comments`,
    "--jq",
    '.[] | select(.body | test("@codex\\s+review"; "i")) | {id, created_at, body}',
  ]);

  if (!success) {
    return [];
  }

  const comments = parsePaginatedJson(output);
  const requests: CodexReviewRequest[] = [];

  for (const comment of comments) {
    const commentId = comment.id as number | undefined;
    if (!commentId) {
      continue;
    }

    const hasEyes = await checkEyesReaction(commentId);

    requests.push({
      commentId,
      createdAt: (comment.created_at as string) || "",
      hasEyesReaction: hasEyes,
    });
  }

  return requests;
}

/**
 * Get reviews posted by Codex bot on the PR.
 *
 * @param prNumber - PR number to check
 * @returns List of review objects
 */
export async function getCodexReviews(prNumber: string): Promise<Record<string, unknown>[]> {
  const [success, output] = await runGhCommand([
    "api",
    "--paginate",
    `/repos/{owner}/{repo}/pulls/${prNumber}/reviews`,
    "--jq",
    '.[] | select(.user.login | test("codex"; "i")) | {id, user: .user.login, submitted_at, state, body}',
  ]);

  if (!success) {
    return [];
  }

  return parsePaginatedJson(output);
}

// =============================================================================
// Copilot Functions
// =============================================================================

/**
 * Get reviews posted by Copilot on the PR.
 *
 * @param prNumber - PR number to check
 * @returns List of review objects
 */
export async function getCopilotReviews(prNumber: string): Promise<Record<string, unknown>[]> {
  const [success, output] = await runGhCommand([
    "api",
    "--paginate",
    `/repos/{owner}/{repo}/pulls/${prNumber}/reviews`,
    "--jq",
    '.[] | select(.user.login | test("^copilot.*\\[bot\\]$"; "i")) | {id, user: .user.login, submitted_at, state, body}',
  ]);

  if (!success) {
    return [];
  }

  return parsePaginatedJson(output);
}

/** Error patterns that indicate Copilot review failure */
const COPILOT_ERROR_PATTERNS = [
  "encountered an error",
  "unable to review",
  "could not complete",
  "failed to review",
  "error occurred",
];

/**
 * Check if the most recent Copilot review ended with an error.
 *
 * @param prNumber - PR number to check
 * @returns Tuple of [isError, errorMessage]
 */
export async function isCopilotReviewError(prNumber: string): Promise<[boolean, string | null]> {
  const reviews = await getCopilotReviews(prNumber);

  if (reviews.length === 0) {
    return [false, null];
  }

  // Sort by submitted_at descending to get the most recent review first
  const sortedReviews = [...reviews].sort((a, b) => {
    const aTime = (a.submitted_at as string) || "";
    const bTime = (b.submitted_at as string) || "";
    return bTime.localeCompare(aTime);
  });

  const latestReview = sortedReviews[0];
  const body = ((latestReview.body as string) || "").toLowerCase();

  for (const pattern of COPILOT_ERROR_PATTERNS) {
    if (body.includes(pattern)) {
      return [true, (latestReview.body as string) || ""];
    }
  }

  return [false, null];
}

/**
 * Request Copilot to review the PR via GitHub API.
 *
 * @param prNumber - PR number to request review for
 * @returns Tuple of [success, message]
 */
export async function requestCopilotReview(prNumber: string): Promise<[boolean, string]> {
  // Use gh pr edit to add reviewer (gh api --input - would hang as asyncSpawn doesn't support stdin)
  const editResult = await asyncSpawn(
    "gh",
    ["pr", "edit", prNumber, "--add-reviewer", COPILOT_REVIEWER_LOGIN],
    { timeout: 30000 },
  );

  if (!editResult.success) {
    return [false, editResult.stderr || "Command failed"];
  }

  return [true, ""];
}

// =============================================================================
// Gemini Functions
// =============================================================================

/**
 * Get reviews posted by Gemini Code Assist on the PR.
 *
 * @param prNumber - PR number to check
 * @returns List of review objects
 */
export async function getGeminiReviews(prNumber: string): Promise<Record<string, unknown>[]> {
  const [success, output] = await runGhCommand([
    "api",
    "--paginate",
    `/repos/{owner}/{repo}/pulls/${prNumber}/reviews`,
    "--jq",
    `.[] | select(.user.login == "${GEMINI_REVIEWER_LOGIN}") | {id, user: .user.login, submitted_at, state, body}`,
  ]);

  if (!success) {
    return [];
  }

  return parsePaginatedJson(output);
}

/**
 * Check if Gemini is in the pending reviewers.
 *
 * @param reviewers - List of pending reviewer login names
 * @returns True if Gemini is in the list
 */
export function hasGeminiReviewer(reviewers: string[]): boolean {
  return reviewers.includes(GEMINI_REVIEWER_LOGIN);
}

/** Gemini rate limit patterns */
const GEMINI_RATE_LIMIT_PATTERNS = [
  "rate limit",
  "rate-limit",
  "quota exceeded",
  "too many requests",
];

/**
 * Check if Gemini has hit rate limits based on review comments.
 *
 * @param prNumber - PR number to check
 * @returns Tuple of [isRateLimited, message]
 */
export async function isGeminiRateLimited(prNumber: string): Promise<[boolean, string | null]> {
  const reviews = await getGeminiReviews(prNumber);

  if (reviews.length === 0) {
    return [false, null];
  }

  // Sort by submitted_at descending
  const sortedReviews = [...reviews].sort((a, b) => {
    const aTime = (a.submitted_at as string) || "";
    const bTime = (b.submitted_at as string) || "";
    return bTime.localeCompare(aTime);
  });

  const latestReview = sortedReviews[0];
  const body = ((latestReview.body as string) || "").toLowerCase();

  for (const pattern of GEMINI_RATE_LIMIT_PATTERNS) {
    if (body.includes(pattern)) {
      return [true, (latestReview.body as string) || ""];
    }
  }

  return [false, null];
}

/**
 * Check if Gemini review is pending and not rate limited.
 *
 * @param prNumber - PR number to check
 * @param pendingReviewers - List of pending reviewers
 * @returns True if Gemini review is pending and should be waited for
 */
export async function isGeminiReviewPending(
  prNumber: string,
  pendingReviewers: string[],
): Promise<boolean> {
  // Check if Gemini is in pending reviewers
  if (!hasGeminiReviewer(pendingReviewers)) {
    return false;
  }

  // Check if rate limited (don't wait if rate limited)
  const [isLimited] = await isGeminiRateLimited(prNumber);
  if (isLimited) {
    return false;
  }

  return true;
}

// =============================================================================
// CodeRabbit Functions
// =============================================================================

/**
 * Get issue comments from CodeRabbit on the PR.
 *
 * @param prNumber - PR number to check
 * @returns List of comment objects
 */
export async function getCoderabbitComments(prNumber: string): Promise<Record<string, unknown>[]> {
  const [success, output] = await runGhCommand([
    "api",
    "--paginate",
    `/repos/{owner}/{repo}/issues/${prNumber}/comments`,
    "--jq",
    `.[] | select(.user.login == "${CODERABBIT_REVIEWER_LOGIN}") | {id, body, created_at, updated_at}`,
  ]);

  if (!success) {
    return [];
  }

  return parsePaginatedJson(output);
}

/** CodeRabbit rate limit pattern */
const CODERABBIT_RATE_LIMIT_PATTERN =
  /Rate limit exceeded.*?wait\s+\*\*(?:(\d+)\s+minutes?\s+and\s+)?(\d+)\s+seconds?\*\*/i;

/**
 * Check if CodeRabbit has hit rate limits based on issue comments.
 *
 * @param prNumber - PR number to check
 * @returns Tuple of [isRateLimited, waitSeconds, message]
 */
export async function isCoderabbitRateLimited(
  prNumber: string,
): Promise<[boolean, number | null, string | null]> {
  const comments = await getCoderabbitComments(prNumber);

  if (comments.length === 0) {
    return [false, null, null];
  }

  // Sort by updated_at descending (CodeRabbit edits comments)
  const sortedComments = [...comments].sort((a, b) => {
    const aTime = (a.updated_at as string) || (a.created_at as string) || "";
    const bTime = (b.updated_at as string) || (b.created_at as string) || "";
    return bTime.localeCompare(aTime);
  });

  const latestComment = sortedComments[0];
  const body = (latestComment.body as string) || "";

  if (!body.toLowerCase().includes("rate limit exceeded")) {
    return [false, null, null];
  }

  // Try to parse wait time
  const match = body.match(CODERABBIT_RATE_LIMIT_PATTERN);
  if (match) {
    const minutes = match[1] ? Number.parseInt(match[1], 10) : 0;
    const seconds = Number.parseInt(match[2], 10);
    const waitSeconds = minutes * 60 + seconds;
    return [true, waitSeconds, body];
  }

  // Rate limit detected but couldn't parse wait time
  return [true, DEFAULT_CODERABBIT_RETRY_WAIT, body];
}

/**
 * Request CodeRabbit to review the PR by posting a comment.
 *
 * @param prNumber - PR number to request review for
 * @returns Tuple of [success, message]
 */
export async function requestCoderabbitReview(prNumber: string): Promise<[boolean, string]> {
  const result = await asyncSpawn(
    "gh",
    ["pr", "comment", prNumber, "--body", "@coderabbitai review"],
    { timeout: 30000 },
  );

  if (!result.success) {
    return [false, result.stderr || "Command failed"];
  }

  return [true, ""];
}

/**
 * Check if a CodeRabbit comment was edited after creation.
 *
 * @param comment - Comment object with created_at and updated_at
 * @returns True if the comment was edited
 */
export function isCoderabbitCommentEdited(comment: Record<string, unknown>): boolean {
  const createdAt = (comment.created_at as string) || "";
  const updatedAt = (comment.updated_at as string) || "";

  if (!createdAt || !updatedAt) {
    return false;
  }

  return updatedAt > createdAt;
}

// =============================================================================
// Qodo Functions
// =============================================================================

/**
 * Get issue comments from Qodo on the PR.
 *
 * @param prNumber - PR number to check
 * @returns List of comment objects
 */
export async function getQodoComments(prNumber: string): Promise<Record<string, unknown>[]> {
  const [success, output] = await runGhCommand([
    "api",
    "--paginate",
    `/repos/{owner}/{repo}/issues/${prNumber}/comments`,
    "--jq",
    `.[] | select(.user.login == "${QODO_REVIEWER_LOGIN}") | {id, body, created_at, updated_at}`,
  ]);

  if (!success) {
    return [];
  }

  return parsePaginatedJson(output);
}

/**
 * Get PR body for Qodo scope filtering.
 *
 * Issue #3462: Need PR body to extract Closes clause for scope filtering.
 *
 * @param prNumber - PR number
 * @returns PR body or null on error
 */
async function getPrBodyForQodo(prNumber: string): Promise<string | null> {
  const [success, output] = await runGhCommand([
    "pr",
    "view",
    prNumber,
    "--json",
    "body",
    "-q",
    ".body",
  ]);
  return success ? output.trim() : null;
}

/**
 * Get PR HEAD commit SHA for stale report detection.
 *
 * Issue #3462: Need HEAD commit to skip stale Qodo reports (Issue #3268).
 *
 * @param prNumber - PR number
 * @returns HEAD commit SHA or null on error
 */
async function getPrHeadForQodo(prNumber: string): Promise<string | null> {
  const [success, output] = await runGhCommand([
    "pr",
    "view",
    prNumber,
    "--json",
    "headRefOid",
    "-q",
    ".headRefOid",
  ]);
  return success ? output.trim() : null;
}

/**
 * Strip "Previous compliance checks" section from Qodo comment.
 *
 * Issue #3462: Qodo includes historical compliance data that may contain
 * old violations. This matches merge-check behavior (Issue #3438).
 *
 * @param body - Full comment body
 * @returns Body with historical section removed
 */
function stripPreviousComplianceSection(body: string): string {
  const previousChecksMatch = body.match(
    /(?:^#+\s*|^\s*(?:<details[^>]*>)?\s*<(?:strong|b|summary)[^>]*>(?:\s*<(?:strong|b)[^>]*>)?)\s*Previous compliance(?:\s+checks)?/im,
  );
  return previousChecksMatch?.index !== undefined ? body.slice(0, previousChecksMatch.index) : body;
}

/**
 * Check if Qodo found compliance violations in the PR.
 *
 * Issue #3462: Only check violations for Issues in PR's Closes clause.
 * This aligns with merge-check's checkQodoComplianceViolation behavior.
 *
 * Features (matching merge-check):
 * - Scope filtering: Only check Issues in PR's Closes clause
 * - Previous compliance section stripping (Issue #3438)
 * - Stale report skipping (Issue #3268)
 * - Non-violation report detection (stops searching older reports)
 *
 * @param prNumber - PR number to check
 * @returns Tuple of [hasViolation, violationSummaries]
 */
export async function hasQodoComplianceViolation(prNumber: string): Promise<[boolean, string[]]> {
  const comments = await getQodoComments(prNumber);

  if (comments.length === 0) {
    return [false, []];
  }

  // Get PR body to extract Closes clause for scope filtering
  const prBody = await getPrBodyForQodo(prNumber);
  const closesIssues = prBody ? extractIssueNumbersFromPrBody(prBody) : [];

  // Get PR HEAD for stale report detection (Issue #3268)
  const prHead = await getPrHeadForQodo(prNumber);

  // Sort by timestamp descending
  const sortedComments = [...comments].sort((a, b) => {
    const aTime = (a.updated_at as string) || (a.created_at as string) || "";
    const bTime = (b.updated_at as string) || (b.created_at as string) || "";
    return bTime.localeCompare(aTime);
  });

  // Find the latest compliance report
  for (const comment of sortedComments) {
    const fullBody = (comment.body as string) || "";

    // Check if this comment contains a compliance report table
    QODO_COMPLIANCE_REPORT_PATTERN.lastIndex = 0;
    if (!QODO_COMPLIANCE_REPORT_PATTERN.test(fullBody)) {
      continue; // Skip non-compliance comments
    }

    // Issue #3438: Strip "Previous compliance checks" section
    const body = stripPreviousComplianceSection(fullBody);

    // Check for violations (🔴 only)
    QODO_COMPLIANCE_VIOLATION_PATTERN.lastIndex = 0;
    if (!QODO_COMPLIANCE_VIOLATION_PATTERN.test(body)) {
      // No 🔴 marker - check if this is a passing report that should stop searching
      if (
        body.includes("Compliance") &&
        (body.includes("✅") || body.includes("🟡") || body.includes("⚪"))
      ) {
        // Found a passing/warning/neutral compliance report - stop searching
        return [false, []];
      }
      continue;
    }

    // Issue #3268: Skip stale reports (based on older commits)
    // Use fullBody for stale check since reference commit may be after "Previous compliance" section
    if (await isStaleQodoReport(fullBody, prHead, "[ci-monitor]")) {
      continue;
    }

    // Apply scope filtering using shared logic from ai_review_checker
    const result = extractInScopeViolations(body, closesIssues);

    if (result.violations.length === 0) {
      // Safeguard for Qodo format changes
      // Only fall back when the section parser couldn't find ANY sections
      if (closesIssues.length > 0 && result.sectionsMatched === 0) {
        const fallback = extractAllViolations(body);
        if (fallback.length > 0) {
          console.error(
            "[ci-monitor] Warning: Section parser found 0 sections but 🔴 markers exist. " +
              "Qodo format may have changed. Falling back to all violations.",
          );
          return [true, fallback];
        }
      }
      // No in-scope violations found
      return [false, []];
    }

    return [true, result.violations];
  }

  // No compliance report found
  return [false, []];
}

// =============================================================================
// Contradiction Detection
// =============================================================================

/**
 * Check for contradictions in AI review comments and print warnings.
 *
 * Issue #1399: Detect potential contradictions between review comments.
 * Issue #1596: Also detect within first batch when previous_comments is empty.
 * Issue #1597: Extracted from main loop to reduce nesting.
 *
 * Only compares AI reviewer comments (Copilot/Codex) to avoid false positives.
 *
 * @param comments - Current batch of review comments
 * @param previousComments - Previous batch of review comments (may be null/empty)
 * @param jsonMode - If true, skip printing (JSON output mode)
 */
export function checkAndReportContradictions(
  comments: Array<{
    body?: string;
    user?: string;
    path?: string;
    line?: number | null;
    id?: number | string;
  }>,
  previousComments: Array<{
    body?: string;
    user?: string;
    path?: string;
    line?: number | null;
    id?: number | string;
  }> | null,
  _jsonMode: boolean,
): void {
  // Note: jsonMode no longer affects this function - warnings go to stderr
  if (!comments || comments.length === 0) {
    return;
  }

  const aiNew = comments.filter((c) => isAiReviewer(c.user || ""));
  if (aiNew.length === 0) {
    return;
  }

  // Build list of previous AI comments to compare against
  let aiPrev: Array<{ body?: string; user?: string; path?: string; id?: number | string }> = [];
  let newAiComments = aiNew;

  if (previousComments && previousComments.length > 0) {
    aiPrev = previousComments.filter((c) => isAiReviewer(c.user || ""));
    if (aiPrev.length > 0) {
      // Filter out already-seen comments to avoid repeated warnings (Issue #3261)
      const prevIds = new Set(aiPrev.map((c) => c.id).filter((id) => id !== undefined));
      newAiComments = aiNew.filter((c) => c.id !== undefined && !prevIds.has(c.id));
    }
  } else {
    // Issue #1596: First batch - compare within itself for contradictions
    // Check if multiple AI reviewers commented on the same path
    const pathCounts = new Map<string, number>();
    for (const comment of aiNew) {
      if (comment.path) {
        pathCounts.set(comment.path, (pathCounts.get(comment.path) || 0) + 1);
      }
    }
    for (const [path, count] of pathCounts) {
      if (count > 1) {
        console.error(
          `⚠️ Warning: Multiple AI comments on ${path} - review for potential contradiction`,
        );
        return; // Only warn once per batch
      }
    }
    return;
  }

  if (newAiComments.length === 0 || aiPrev.length === 0) {
    return;
  }

  // Simple contradiction detection: same path, different recommendation
  // Full implementation would use semantic analysis
  for (const newComment of newAiComments) {
    for (const prevComment of aiPrev) {
      if (newComment.path === prevComment.path) {
        // Same file, different comments - potential contradiction
        // Log warning for manual review
        console.error(
          `⚠️ Warning: Multiple AI comments on ${newComment.path} - review for potential contradiction`,
        );
        return; // Only warn once per batch
      }
    }
  }
}
