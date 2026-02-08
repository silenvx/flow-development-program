/**
 * merge-check用のAIレビュー状態確認ユーティリティ。
 *
 * Why:
 *   AIレビュー（Copilot/Codex）がレビュー中やエラー状態のままマージすると、
 *   レビューなしでコードがマージされる。マージ前にAIレビュー状態を確認する。
 *
 * What:
 *   - AIレビュアーがレビュー中かを確認
 *   - AIレビューエラーの検出
 *   - Copilotレビューの再リクエスト
 *   - 連続エラー時の警告付きマージ許可
 *
 * Remarks:
 *   - フックではなくユーティリティモジュール
 *   - merge-check.tsから呼び出される
 *   - 連続エラー時は警告付きでマージ許可（緩和処理）
 *   - Python ai_review_checker.py との互換性を維持
 *
 * Changelog:
 *   - silenvx/dekita#630: 連続エラー時の緩和処理
 *   - silenvx/dekita#646: エラーログ出力の改善
 *   - silenvx/dekita#3159: TypeScriptに移植
 *   - silenvx/dekita#3268: Qodo stale検出ロジック追加
 */

import { truncateBody } from "./check_utils";
import {
  AI_COMMENT_EXCLUDE_PATTERNS,
  AI_ISSUE_COMMENT_REVIEWERS,
  QODO_COMPLIANCE_REPORT_PATTERN,
  QODO_COMPLIANCE_VIOLATION_PATTERN,
  QODO_REFERENCE_COMMIT_PATTERN,
  QODO_REVIEWER_LOGIN,
  QODO_VIOLATION_DETAIL_PATTERN,
  TIMEOUT_HEAVY,
  TIMEOUT_MEDIUM,
} from "./constants";
import { formatError } from "./format_error";
import { REPO_API_PATH, addRepoFlag, buildPrViewArgs } from "./github";
import { extractIssueNumbersFromPrBody } from "./issue_checker";
import { parsePaginatedJson } from "./json";
import { asyncSpawn } from "./spawn";

/** Error message pattern that indicates Copilot failed to review */
const AI_REVIEW_ERROR_PATTERN = "encountered an error";

/** Copilot reviewer login name for API requests */
const COPILOT_REVIEWER_LOGIN = "copilot-pull-request-reviewer[bot]";

/**
 * Threshold for consecutive error reviews before allowing merge with warning.
 * When Copilot fails this many times consecutively, it's likely a service issue.
 */
const AI_REVIEW_ERROR_RETRY_THRESHOLD = 2;

/** AI review error result */
export interface AIReviewError {
  /** AI reviewer login name */
  reviewer: string;
  /** Truncated error message */
  message: string;
  /** True if merge should be allowed with warning */
  allowWithWarning: boolean;
  /** Number of consecutive error reviews */
  consecutiveErrors: number;
}

/**
 * Check if AI reviewers are currently reviewing the PR.
 *
 * @param prNumber - The PR number to check.
 * @param repo - Repository in owner/repo format, or null for current repo.
 * @returns List of AI reviewers found in requested_reviewers.
 */
export async function checkAiReviewing(
  prNumber: string,
  repo: string | null = null,
): Promise<string[]> {
  try {
    const args = [
      "api",
      `${REPO_API_PATH}/pulls/${prNumber}`,
      "--jq",
      ".requested_reviewers[].login",
    ];
    addRepoFlag(args, repo);
    const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_MEDIUM * 1000 });

    if (!result.success) {
      // On subprocess error, fail open
      return [];
    }

    const reviewers = result.stdout.trim() ? result.stdout.trim().split("\n") : [];

    // Issue #3448: Use isAiReviewer for consistent detection across all AI reviewer bots
    // Fallback to substring match for robustness against unknown bots (Issue #3434)
    return reviewers.filter((reviewer) => {
      if (isAiReviewer(reviewer)) return true;
      const lower = reviewer.toLowerCase();
      // Fallback: match known AI bot patterns that might not be in the exact list
      return /copilot|codex|gemini|qodo|coderabbit|greptile|sourcery|sweep|metabob|cubic/i.test(
        lower,
      );
    });
  } catch {
    // On error, don't block (fail open)
    return [];
  }
}

/**
 * Request Copilot to review the PR via GitHub API.
 *
 * This can be used to re-request a review after Copilot encountered an error.
 *
 * @param prNumber - The PR number to request review for.
 * @param repo - Repository in owner/repo format, or null for current repo.
 * @returns True if the request was successful, false otherwise.
 */
export async function requestCopilotReview(
  prNumber: string,
  repo: string | null = null,
): Promise<boolean> {
  // Issue #3263: Declare variables in outer scope for proper cleanup in finally block
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  let proc: ReturnType<typeof Bun.spawn> | undefined;
  let stdoutPromise: Promise<string> | undefined;
  let stderrPromise: Promise<string> | undefined;

  try {
    const body = JSON.stringify({ reviewers: [COPILOT_REVIEWER_LOGIN] });

    // Build args for Bun.spawn
    const args = [
      "api",
      `${REPO_API_PATH}/pulls/${prNumber}/requested_reviewers`,
      "-X",
      "POST",
      "--input",
      "-",
    ];
    addRepoFlag(args, repo);
    const spawnArgs = ["gh", ...args];

    // Note: We need to pass body via stdin, which asyncSpawn doesn't support
    proc = Bun.spawn(spawnArgs, {
      stdin: new Blob([body]),
      stdout: "pipe",
      stderr: "pipe",
    });

    // Issue #3211: Start reading stdout/stderr before awaiting exit to prevent deadlock
    // if subprocess writes enough data to fill the pipe buffer
    // Note: When stdout/stderr are set to "pipe", they are ReadableStream, not number.
    // Type assertion is required because Bun's type definition is a union.
    stdoutPromise = new Response(proc.stdout as ReadableStream<Uint8Array>).text();
    stderrPromise = new Response(proc.stderr as ReadableStream<Uint8Array>).text();

    // Issue #3161: Add timeout to prevent indefinite hangs
    // Issue #3263: Clear timeout on success to prevent resource leak
    const timeoutMs = TIMEOUT_MEDIUM * 1000;
    const timeoutPromise = new Promise<never>((_, reject) => {
      timeoutId = setTimeout(() => {
        proc?.kill();
        reject(new Error(`Command timed out after ${TIMEOUT_MEDIUM}s`));
      }, timeoutMs);
    });

    try {
      const exitCode = await Promise.race([proc.exited, timeoutPromise]);
      await stdoutPromise; // Consume stdout even if unused
      const stderr = await stderrPromise;

      if (exitCode !== 0) {
        // Log stderr for diagnosis (Issue #646)
        const stderrMsg = stderr.trim() || "No stderr output";
        console.error(
          `[merge-check] request_copilot_review failed for PR #${prNumber}: ${stderrMsg}`,
        );
        return false;
      }

      return true;
    } catch {
      // Issue #3263: Consume stdout/stderr on timeout to release pipe buffers
      await Promise.allSettled([stdoutPromise, stderrPromise]);
      throw new Error(`requestCopilotReview timed out for PR #${prNumber}`);
    } finally {
      if (timeoutId) clearTimeout(timeoutId);
    }
  } catch (error) {
    // Log exception for diagnosis (Issue #646)
    console.error(
      `[merge-check] request_copilot_review exception for PR #${prNumber}: ${formatError(error)}`,
    );
    return false;
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
    // Cancel stderr stream explicitly to release resources
    // Type assertion: When stderr is set to "pipe", it's a ReadableStream
    const stderrStream = proc?.stderr as ReadableStream<Uint8Array> | undefined;
    if (stderrStream && !stderrStream.locked) {
      stderrStream.cancel().catch(() => {
        // キャンセルエラーを無視
      });
    }
    // Fire-and-forget cleanup to prevent unhandled rejection
    if (stderrPromise) {
      stderrPromise.catch(() => {
        // stderrエラーを無視
      });
    }
  }
}

/** Review entry from GitHub API */
interface ReviewEntry {
  author: string;
  body: string;
  state: string;
  submitted_at: string;
}

/**
 * Check if AI reviews (Copilot/Codex) have errors.
 *
 * When Copilot fails to review, it leaves a comment containing
 * "encountered an error". This is NOT a successful review.
 *
 * This function collects all reviews from AI reviewers and analyzes them
 * to detect error patterns.
 *
 * Special case (Issue #630): If there are 2+ consecutive error reviews AND
 * there was a successful review earlier, the merge is allowed with a warning.
 * This handles persistent Copilot service issues that prevent re-reviews.
 *
 * @param prNumber - The PR number to check.
 * @param repo - Repository in owner/repo format, or null for current repo.
 * @returns AIReviewError if error found, null otherwise.
 */
export async function checkAiReviewError(
  prNumber: string,
  repo: string | null = null,
): Promise<AIReviewError | null> {
  try {
    const args = [
      "api",
      "--paginate",
      `${REPO_API_PATH}/pulls/${prNumber}/reviews`,
      "--jq",
      // Issue #3448: Use regex that covers all supported AI bots for consistency with isAiReviewer
      '.[] | select(.user != null and (.user.login | test("copilot|codex|gemini|qodo|coderabbit|greptile|sourcery|sweep|metabob|cubic"; "i"))) | {author: .user.login, body: .body, state: .state, submitted_at: .submitted_at}',
    ];
    addRepoFlag(args, repo);
    const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_HEAVY * 1000 });

    if (!result.success) {
      return null;
    }

    // Parse NDJSON output and collect all AI reviews per reviewer
    const aiReviewsByAuthor: Map<string, ReviewEntry[]> = new Map();

    for (const line of result.stdout.trim().split("\n")) {
      if (!line) continue;

      try {
        const review: ReviewEntry = JSON.parse(line);
        const author = review.author || "";

        if (!aiReviewsByAuthor.has(author)) {
          aiReviewsByAuthor.set(author, []);
        }
        aiReviewsByAuthor.get(author)!.push(review);
      } catch {
        // Skip malformed JSON lines (fail-open)
      }
    }

    // Sort reviews by submitted_at for each author (chronological order)
    for (const reviews of aiReviewsByAuthor.values()) {
      reviews.sort((a, b) => (a.submitted_at || "").localeCompare(b.submitted_at || ""));
    }

    // Check each AI reviewer
    for (const [author, reviews] of aiReviewsByAuthor.entries()) {
      if (reviews.length === 0) continue;

      const latestReview = reviews[reviews.length - 1];
      const body = latestReview.body || "";

      if (body.toLowerCase().includes(AI_REVIEW_ERROR_PATTERN)) {
        // Count consecutive errors from the end
        let consecutiveErrors = 0;
        let hasSuccessfulReview = false;

        for (let i = reviews.length - 1; i >= 0; i--) {
          const reviewBody = reviews[i].body || "";
          if (reviewBody.toLowerCase().includes(AI_REVIEW_ERROR_PATTERN)) {
            consecutiveErrors++;
          } else {
            // Found a non-error review
            hasSuccessfulReview = true;
            break;
          }
        }

        // If 2+ consecutive errors and there's a successful review earlier,
        // allow merge with warning (Issue #630)
        if (consecutiveErrors >= AI_REVIEW_ERROR_RETRY_THRESHOLD && hasSuccessfulReview) {
          return {
            reviewer: author,
            message: truncateBody(body, 200),
            allowWithWarning: true,
            consecutiveErrors,
          };
        }

        // Otherwise, block as usual
        return {
          reviewer: author,
          message: truncateBody(body, 200),
          allowWithWarning: false,
          consecutiveErrors,
        };
      }
    }

    return null;
  } catch {
    // On error, don't block (fail open)
    return null;
  }
}

// =============================================================================
// Qodo Compliance Violation Check (Issue #3196)
// =============================================================================

/** Qodo compliance violation result */
export interface QodoComplianceViolation {
  /** List of violation summaries */
  violations: string[];
  /** Number of violations */
  count: number;
  /** Timestamp of the compliance report (Issue #3620) */
  reportTimestamp: string;
}

/**
 * Get issue comments from Qodo on the PR.
 *
 * Issue #3196: Qodo posts compliance reports as issue comments (not review comments).
 * These contain colored circle markers to indicate compliance status.
 *
 * @param prNumber - The PR number to check.
 * @param repo - Repository in owner/repo format, or null for current repo.
 * @returns List of Qodo comments with id, body, created_at, updated_at.
 */
async function getQodoComments(
  prNumber: string,
  repo: string | null = null,
): Promise<Array<{ id: number; body: string; created_at: string; updated_at: string }>> {
  try {
    const args = [
      "api",
      "--paginate",
      `${REPO_API_PATH}/issues/${prNumber}/comments`,
      "--jq",
      `.[] | select(.user != null and .user.login == "${QODO_REVIEWER_LOGIN}") | {id, body, created_at, updated_at}`,
    ];
    addRepoFlag(args, repo);
    const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_HEAVY * 1000 });

    if (!result.success) {
      return [];
    }

    // Parse NDJSON output
    const comments: Array<{ id: number; body: string; created_at: string; updated_at: string }> =
      [];
    for (const line of result.stdout.trim().split("\n")) {
      if (!line) continue;
      try {
        comments.push(JSON.parse(line));
      } catch {
        // Skip malformed lines
      }
    }

    return comments;
  } catch {
    return [];
  }
}

/**
 * Get PR HEAD commit SHA.
 *
 * Issue #3268: Used to detect stale Qodo compliance reports.
 *
 * @param prNumber - The PR number.
 * @param repo - Repository in owner/repo format, or null for current repo.
 * @returns Full commit SHA or null on error.
 */
async function getPrHeadCommit(
  prNumber: string,
  repo: string | null = null,
): Promise<string | null> {
  try {
    const args = buildPrViewArgs(prNumber, repo, ["--json", "headRefOid", "--jq", ".headRefOid"]);
    const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_MEDIUM * 1000 });
    if (!result.success) {
      return null;
    }
    const sha = result.stdout.trim();
    return sha.length >= 7 ? sha : null;
  } catch {
    return null;
  }
}

/**
 * Check if commit1 is an ancestor of commit2.
 *
 * Issue #3268: Used to detect if Qodo's reference commit is older than PR HEAD.
 *
 * @param commit1 - Potential ancestor commit SHA.
 * @param commit2 - Potential descendant commit SHA.
 * @returns true if commit1 is an ancestor of commit2, false otherwise, null on error.
 */
async function isAncestorOf(commit1: string, commit2: string): Promise<boolean | null> {
  try {
    const result = await asyncSpawn("git", ["merge-base", "--is-ancestor", commit1, commit2], {
      timeout: TIMEOUT_MEDIUM * 1000,
    });
    // Exit code: 0 = is ancestor, 1 = not ancestor, other = error
    if (result.exitCode === 0) return true;
    if (result.exitCode === 1) return false;
    return null;
  } catch {
    return null;
  }
}

/**
 * Check if a Qodo compliance report is stale (based on an older commit).
 *
 * Issue #3268: Qodo's report may reference an older commit after force-push.
 * If the reference commit differs from PR HEAD, the report is stale regardless
 * of ancestry (matching Python implementation behavior).
 *
 * Cases treated as stale:
 * - isAncestor === true: Reference is an ancestor of HEAD
 * - isAncestor === false: Reference is not in current history (rebased)
 * - isAncestor === null: Reference not found or git error
 *
 * @param body - The comment body to check.
 * @param prHead - The current PR HEAD commit SHA.
 * @returns true if stale, false otherwise.
 */
export async function isStaleQodoReport(
  body: string,
  prHead: string | null,
  logPrefix = "[merge-check]",
): Promise<boolean> {
  if (!prHead) return false;

  const refMatch = QODO_REFERENCE_COMMIT_PATTERN.exec(body);
  if (!refMatch) return false;

  const qodoRefCommit = refMatch[1].toLowerCase();
  const prHeadLower = prHead.toLowerCase();

  // Same commit (prefix match handles short vs full SHA)
  if (prHeadLower.startsWith(qodoRefCommit) || qodoRefCommit.startsWith(prHeadLower)) {
    return false;
  }

  // Reference commit differs from HEAD - check ancestry for logging purposes
  const isAncestor = await isAncestorOf(qodoRefCommit, prHead);

  // All cases where reference differs from HEAD are stale (matching Python behavior)
  if (isAncestor === true) {
    console.error(
      `${logPrefix} Skipping stale Qodo compliance report (ref is ancestor of HEAD: ref=${qodoRefCommit.slice(0, 7)}, HEAD=${prHead.slice(0, 7)})`,
    );
  } else if (isAncestor === false) {
    console.error(
      `${logPrefix} Skipping stale Qodo compliance report (ref not in current history, possibly rebased: ref=${qodoRefCommit.slice(0, 7)}, HEAD=${prHead.slice(0, 7)})`,
    );
  } else {
    // isAncestor === null: git error or reference commit not found
    console.error(
      `${logPrefix} Skipping stale Qodo compliance report (unable to determine ancestry: ref=${qodoRefCommit.slice(0, 7)}, HEAD=${prHead.slice(0, 7)})`,
    );
  }

  return true;
}

/**
 * Get PR body for Qodo compliance checking.
 *
 * Issue #3459: Used to extract Closes clause Issues for scope filtering.
 */
async function getPrBody(prNumber: string, repo: string | null = null): Promise<string | null> {
  try {
    const args = buildPrViewArgs(prNumber, repo, ["--json", "body", "--jq", ".body"]);
    const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_MEDIUM * 1000 });
    return result.success ? result.stdout : null;
  } catch (error) {
    console.error(`[merge-check] Failed to get PR body: ${formatError(error)}`);
    return null;
  }
}

/**
 * Pattern to extract Issue sections from Qodo Ticket Compliance report.
 *
 * Issue #3459: Used to identify which Issue each violation belongs to.
 * Qodo formats each Issue as: <summary>🎫 <a href=...>#ISSUE_NUMBER</a></summary>
 * followed by <table> containing violation rows with 🔴 markers.
 *
 * Issue #4017: Capture entire section content (not just first table) to handle
 * cases where Qodo outputs multiple tables per Issue section.
 * Uses lookahead to match until next Issue summary (🎫) or the string ends.
 * The section may contain nested <details> tags for violation details.
 */
const QODO_TICKET_SECTION_PATTERN =
  /<summary>\s*🎫\s*<a[^>]*>#(\d+)<\/a>\s*<\/summary>([\s\S]*?)(?=<summary>\s*🎫|$)/gi;

/**
 * Extract all violations from the report body (used when no Closes clause).
 */
export function extractAllViolations(body: string): string[] {
  const violations: string[] = [];
  for (const match of body.matchAll(QODO_VIOLATION_DETAIL_PATTERN)) {
    violations.push(match[1]);
  }
  if (violations.length === 0 && body.includes("🔴")) {
    violations.push("Compliance violation detected (details unavailable)");
  }
  return violations;
}

/** Result of extractInScopeViolations */
export interface InScopeViolationsResult {
  /** Violations found for in-scope Issues */
  violations: string[];
  /** Number of Issue sections matched by the parser */
  sectionsMatched: number;
  /** Whether any Closes issues had their sections matched */
  closesIssuesMatched: boolean;
}

/**
 * Extract violations only for Issues that are in PR scope (Closes clause).
 *
 * Issue #3459: Qodo checks compliance for all Issues referenced in the PR,
 * including those only mentioned in code comments. This function filters
 * violations to only include Issues that the PR is actually trying to close.
 *
 * @param body - Qodo compliance report body (already stripped of "Previous compliance" section).
 * @param closesIssues - Issue numbers from PR body's Closes clause.
 * @returns Violations for in-scope Issues and parser metadata.
 */
export function extractInScopeViolations(
  body: string,
  closesIssues: string[],
): InScopeViolationsResult {
  // No Closes clause: check all violations to prevent bypass
  if (closesIssues.length === 0) {
    return {
      violations: extractAllViolations(body),
      sectionsMatched: 0,
      closesIssuesMatched: false,
    };
  }

  const closesSet = new Set(closesIssues);
  const violations: string[] = [];
  let sectionsMatched = 0;
  let closesIssuesMatched = false;

  QODO_TICKET_SECTION_PATTERN.lastIndex = 0;
  for (const match of body.matchAll(QODO_TICKET_SECTION_PATTERN)) {
    sectionsMatched++;
    const issueNumber = match[1];
    const sectionContent = match[2];

    // Track if any Closes issues were matched
    if (closesSet.has(issueNumber)) {
      closesIssuesMatched = true;
    } else {
      // Skip Issues not in the Closes clause
      continue;
    }

    // Check for violations in this Issue section
    if (!sectionContent.includes("🔴")) {
      continue;
    }

    // Extract violation details
    let foundDetails = false;
    for (const detailMatch of sectionContent.matchAll(QODO_VIOLATION_DETAIL_PATTERN)) {
      violations.push(detailMatch[1]);
      foundDetails = true;
    }

    // Fallback if 🔴 found but details unavailable
    if (!foundDetails) {
      violations.push(
        `Compliance violation detected for Issue #${issueNumber} (details unavailable)`,
      );
    }
  }

  return { violations, sectionsMatched, closesIssuesMatched };
}

/**
 * Check if Qodo found compliance violations in the PR.
 *
 * Issue #3196: Detect red circle markers in Qodo's compliance report.
 *
 * Issue #3268: Detect stale compliance reports by comparing Qodo's reference
 * commit against PR HEAD. If Qodo's report is based on an older commit, skip it.
 *
 * Issue #3459: Only check violations for Issues in PR's Closes clause.
 * Qodo also checks Issues mentioned in code comments, but those should not
 * block merges since they are not being closed by this PR.
 *
 * @param prNumber - The PR number to check.
 * @param repo - Repository in owner/repo format, or null for current repo.
 * @returns QodoComplianceViolation if violations found, null otherwise.
 */
export async function checkQodoComplianceViolation(
  prNumber: string,
  repo: string | null = null,
): Promise<QodoComplianceViolation | null> {
  try {
    const comments = await getQodoComments(prNumber, repo);

    if (comments.length === 0) {
      return null;
    }

    // Issue #3268: Get PR HEAD commit for stale detection
    const prHead = await getPrHeadCommit(prNumber, repo);

    // Issue #3459: Get PR body to extract Closes clause Issues
    const prBody = await getPrBody(prNumber, repo);
    const closesIssues = prBody ? extractIssueNumbersFromPrBody(prBody) : [];

    // Sort by timestamp (newest first) to search for compliance reports
    // Issue #3161: Search backwards to find the latest compliance report
    // If Qodo posts a non-compliance comment after a failed report, we should still find the violation
    const sortedComments = [...comments].sort((a, b) => {
      const aTime = a.updated_at || a.created_at || "";
      const bTime = b.updated_at || b.created_at || "";
      return bTime.localeCompare(aTime); // Descending order (newest first)
    });

    // Issue #3161: Search through comments to find any compliance report with violations
    // Don't just check the latest comment - look for the latest compliance report specifically
    for (const comment of sortedComments) {
      const fullBody = comment.body || "";

      // Issue #3226: First check if this is a compliance report (not Code Suggestions)
      // Qodo's Code Suggestions may also contain 🔴 markers for priority indicators
      QODO_COMPLIANCE_REPORT_PATTERN.lastIndex = 0;
      if (!QODO_COMPLIANCE_REPORT_PATTERN.test(fullBody)) {
        continue; // Not a compliance report, skip
      }

      // Issue #3438: Strip "Previous compliance checks" section to avoid false positives
      // Qodo includes historical compliance data that may contain old violations
      // Use regex for robustness against format variations (heading level, HTML tags, etc.)
      const previousChecksMatch = fullBody.match(
        /(?:^#+\s*|^\s*(?:<details[^>]*>)?\s*<(?:strong|b|summary)[^>]*>(?:\s*<(?:strong|b)[^>]*>)?)\s*Previous compliance(?:\s+checks)?/im,
      );
      const body =
        previousChecksMatch?.index !== undefined
          ? fullBody.slice(0, previousChecksMatch.index)
          : fullBody;

      // Check for 🔴 markers (indicates violations in the compliance report)
      // Reset regex lastIndex for global pattern
      QODO_COMPLIANCE_VIOLATION_PATTERN.lastIndex = 0;
      if (QODO_COMPLIANCE_VIOLATION_PATTERN.test(body)) {
        // Issue #3268: Skip stale reports (based on older commits)
        // Use fullBody for stale check since reference commit may be after "Previous compliance" section
        if (await isStaleQodoReport(fullBody, prHead)) {
          continue;
        }

        // Issue #3459: Only report violations for Issues in PR's Closes clause
        // This prevents false positives from Issues mentioned only in code comments
        // Reset regex lastIndex for safety with shared constant
        QODO_VIOLATION_DETAIL_PATTERN.lastIndex = 0;
        const result = extractInScopeViolations(body, closesIssues);

        if (result.violations.length === 0) {
          // Issue #3459: Safeguard for Qodo format changes
          // Only fall back when the section parser couldn't find ANY sections,
          // indicating a potential format change. If the parser found sections
          // (for any Issue) but no violations for Closes issues, that's expected
          // behavior (out-of-scope issues have violations, in-scope issues are clean).
          if (closesIssues.length > 0 && result.sectionsMatched === 0) {
            const fallback = extractAllViolations(body);
            if (fallback.length > 0) {
              console.error(
                "[merge-check] Warning: Section parser found 0 sections but 🔴 markers exist. " +
                  "Qodo format may have changed. Falling back to all violations.",
              );
              return {
                violations: fallback,
                count: fallback.length,
                reportTimestamp: comment.updated_at || comment.created_at || "",
              };
            }
          }
          // No in-scope violations found - either all Closes Issues passed or
          // the failing Issues are not in the Closes clause (out of scope)
          return null;
        }

        return {
          violations: result.violations,
          count: result.violations.length,
          reportTimestamp: comment.updated_at || comment.created_at || "",
        };
      }

      // If we find a compliance report without violations (passed/warning/neutral), stop searching
      // Pattern: Look for Qodo compliance-related content with ✅ (passed), 🟡 (warning), or ⚪ (neutral)
      // This prevents falling back to older violation reports when a newer non-violation report exists
      if (
        body.includes("Compliance") &&
        (body.includes("✅") || body.includes("🟡") || body.includes("⚪"))
      ) {
        return null;
      }
    }

    return null;
  } catch {
    // On error, don't block (fail open)
    return null;
  }
}

// =============================================================================
// Unresponded AI Issue Comments Check (Issue #3391)
// =============================================================================

/** AI issue comment that has not received a human response */
export interface UnrespondedAiComment {
  /** Comment ID */
  id: number;
  /** AI reviewer login name */
  author: string;
  /** Comment body (truncated) */
  body: string;
  /** Comment creation timestamp */
  created_at: string;
}

/**
 * Issue comment from GitHub API.
 * Issue #3627: Exported for caching and reuse across multiple check functions.
 */
export interface IssueComment {
  id: number;
  body: string;
  created_at: string;
  /** Comment last update timestamp. May differ from created_at if edited. */
  updated_at: string;
  user: {
    login: string;
  } | null;
  /** Author association with repository (OWNER, MEMBER, COLLABORATOR, CONTRIBUTOR, etc.) */
  author_association?: string;
}

/**
 * Fetch all issue comments for a PR.
 * Issue #3627: Centralized function to avoid redundant API calls.
 *
 * @param prNumber - The PR number to fetch comments for.
 * @param repo - Repository in owner/repo format, or null for current repo.
 * @returns Array of issue comments, or empty array on error.
 */
export async function fetchAllIssueComments(
  prNumber: string,
  repo: string | null = null,
): Promise<IssueComment[]> {
  try {
    // Fetch all fields needed by both checkUnrespondedAiIssueComments and checkQodoFalsePositiveDeclaration
    // Copilot review: Only fetch user.login to reduce transfer/parse overhead
    const args = [
      "api",
      "--paginate",
      `${REPO_API_PATH}/issues/${prNumber}/comments`,
      "--jq",
      ".[] | {id, body, created_at, updated_at, user: (if .user == null then null else {login: .user.login} end), author_association}",
    ];
    addRepoFlag(args, repo);
    const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_HEAVY * 1000 });

    if (!result.success) {
      // Copilot review: Log failure reason for debugging
      console.error(
        `[fetchAllIssueComments] Failed to fetch comments: exitCode=${result.exitCode}, stderr=${result.stderr}`,
      );
      return [];
    }

    // Copilot review: Use parsePaginatedJson for consistent NDJSON parsing
    return parsePaginatedJson<IssueComment>(result.stdout);
  } catch (error) {
    // Copilot review: Log exception for debugging
    console.error(`[fetchAllIssueComments] Exception: ${formatError(error)}`);
    return [];
  }
}

/**
 * Pattern to detect actionable content in AI comments.
 * If a comment contains suggestions, findings, or violations, it should NOT be excluded
 * even if it matches an exclude pattern (e.g., "## Summary of Changes" header).
 *
 * Issue #3391: Prevent false negatives where Code Suggestions are skipped due to
 * matching exclude patterns like "Summary of Changes".
 *
 * Uses specific headers and priority markers to avoid false positives like
 * "I found no suggestions" or "No violations detected".
 */
export const AI_SUGGESTION_INDICATORS =
  /^## (?:Code Suggestions|Possible Issues|Findings|Suggestions)|\[(?:HIGH|MEDIUM|LOW|P0|P1|P2|security-(?:high|medium|low|critical))\]|🔴|🟡/im;

/**
 * Check if an AI comment should be excluded from response requirement.
 *
 * Comments are excluded if they match exclude patterns (quota messages, summaries, etc.)
 * UNLESS they also contain actionable content (suggestions, findings, violations).
 *
 * @param body - The comment body to check.
 * @returns true if the comment should be excluded, false otherwise.
 */
export function isExcludedAiComment(body: string): boolean {
  // Always exclude compliance reports as they are handled by checkQodoComplianceViolation
  // This prevents users from having to reply to old reports after code is updated
  if (/## PR Compliance Guide/i.test(body)) {
    return true;
  }

  // First, check if the comment contains actionable content
  // If so, never exclude it regardless of other patterns
  if (AI_SUGGESTION_INDICATORS.test(body)) {
    return false;
  }

  // Check against exclude patterns
  for (const pattern of AI_COMMENT_EXCLUDE_PATTERNS) {
    // Reset lastIndex for global/multiline patterns
    pattern.lastIndex = 0;
    if (pattern.test(body)) {
      return true;
    }
  }
  return false;
}

/**
 * Pre-computed Set of AI reviewer logins for O(1) lookup.
 * Converted to lowercase for case-insensitive comparison.
 */
const AI_REVIEWER_SET = new Set(AI_ISSUE_COMMENT_REVIEWERS.map((r) => r.toLowerCase()));

/**
 * Check if a user is an AI reviewer bot.
 *
 * Uses Set for O(1) lookup instead of array iteration.
 *
 * Note: This uses exact match against AI_ISSUE_COMMENT_REVIEWERS list.
 * Fallback substring matching (for unknown AI bots) is tracked in Issue #3434.
 *
 * @param login - The user login name.
 * @returns true if the user is an AI reviewer bot, false otherwise.
 */
export function isAiReviewer(login: string): boolean {
  return AI_REVIEWER_SET.has(login.toLowerCase());
}

/**
 * Get the effective timestamp for an AI comment.
 *
 * Issue #3394: Use updated_at if available, otherwise fall back to created_at.
 * If a bot updates its comment after a human response, the response should not
 * count as a response to the updated content.
 *
 * Issue #3752: Exception for qodo-code-review - always use created_at.
 * Qodo auto-updates comments on every push/rebase without changing content,
 * causing previous responses to be invalidated. Since the content doesn't
 * materially change, the original response remains valid.
 *
 * @param author - The comment author login.
 * @param createdAt - The created_at timestamp.
 * @param updatedAt - The updated_at timestamp (optional).
 * @returns The effective timestamp string to use for response comparison.
 */
export function getAiCommentEffectiveTime(
  author: string,
  createdAt: string,
  updatedAt?: string,
): string {
  const useCreatedAtOnly = author.toLowerCase() === QODO_REVIEWER_LOGIN.toLowerCase();
  return useCreatedAtOnly ? createdAt : updatedAt || createdAt;
}

/**
 * Check for AI issue comments that have not received a human response.
 *
 * Issue #3391: AI reviewers (qodo, Gemini, etc.) post Code Suggestions as issue
 * comments rather than PR review comments. These should be acknowledged by a human
 * before merging.
 *
 * A comment is considered "responded" if any human, non-bot user (i.e. not an AI
 * reviewer and not an account whose login ends with "[bot]") posted a comment after it.
 * This is less strict than checkResolvedWithoutResponse (which requires Claude Code
 * signature) because:
 * 1. Issue comments don't have a "resolved" state
 * 2. Any human acknowledgment (even just "確認済み") is sufficient
 *
 * Note: This intentionally excludes other GitHub/third-party bot accounts from being
 * counted as a human response, even if they are not detected by isAiReviewer.
 *
 * @param prNumber - The PR number to check.
 * @param repo - Repository in owner/repo format, or null for current repo.
 * @returns List of AI comments that need a response.
 */
export async function checkUnrespondedAiIssueComments(
  prNumber: string,
  repo: string | null = null,
): Promise<UnrespondedAiComment[]> {
  try {
    // Issue #3816: DRY refactoring - delegate to fetchAllIssueComments + FromComments version
    const comments = await fetchAllIssueComments(prNumber, repo);
    return checkUnrespondedAiIssueCommentsFromComments(comments);
  } catch (error) {
    // Gemini review: Fail open to avoid blocking on errors
    console.error("[checkUnrespondedAiIssueComments] Exception:", error);
    return [];
  }
}

/**
 * Check for unresponded AI issue comments using pre-fetched comments.
 * Issue #3627: Accepts cached comments to avoid redundant API calls.
 *
 * @param comments - Pre-fetched issue comments from fetchAllIssueComments.
 * @returns List of AI comments that need a response.
 */
export function checkUnrespondedAiIssueCommentsFromComments(
  comments: IssueComment[],
): UnrespondedAiComment[] {
  if (comments.length === 0) {
    return [];
  }

  // Find AI comments that need a response
  const unrespondedComments: UnrespondedAiComment[] = [];

  // Track the latest human comment timestamp (O(N) optimization)
  // Issue #3394: Only need the latest human response time to compare against AI updates.
  let lastHumanResponseTime = 0;
  for (const comment of comments) {
    const login = comment.user?.login || "";
    const body = comment.body || "";
    if (!login.toLowerCase().endsWith("[bot]") && !isAiReviewer(login) && body.trim()) {
      const time = new Date(comment.updated_at || comment.created_at || "").getTime();
      if (!Number.isNaN(time) && time > lastHumanResponseTime) {
        lastHumanResponseTime = time;
      }
    }
  }

  for (const comment of comments) {
    const author = comment.user?.login || "";

    if (!isAiReviewer(author)) {
      continue;
    }

    const body = comment.body || "";

    if (isExcludedAiComment(body)) {
      continue;
    }

    const aiCommentEffectiveTime = getAiCommentEffectiveTime(
      author,
      comment.created_at || "",
      comment.updated_at,
    );
    const aiEffectiveDate = new Date(aiCommentEffectiveTime).getTime();

    const hasHumanResponse =
      !Number.isNaN(aiEffectiveDate) && lastHumanResponseTime > aiEffectiveDate;

    if (!hasHumanResponse) {
      unrespondedComments.push({
        id: comment.id,
        author,
        body: truncateBody(body.trim().replace(/\n+/g, " "), 200),
        created_at: comment.created_at || "",
      });
    }
  }

  return unrespondedComments;
}

// =============================================================================
// Qodo False Positive Declaration Check (Issue #3620)
// =============================================================================

/**
 * Pattern to detect Qodo false positive declarations in PR comments.
 *
 * Matches variations like:
 * - "Qodo false positive: reason"
 * - "Qodo False Positive: reason"
 * - "[Qodo false positive]: reason"
 * - "**Qodo false positive**: reason"
 *
 * Exported for use in tests (Issue #3624).
 */
export const QODO_FALSE_POSITIVE_PATTERN =
  /^\[?\*{0,2}Qodo\s+false\s+positive\*{0,2}\]?\s*:\s*(.+)/im;

/** Result of Qodo false positive declaration check */
export interface QodoFalsePositiveDeclaration {
  /** The reason provided for the false positive declaration */
  reason: string;
  /** The author who made the declaration */
  author: string;
  /** The author's association with the repository */
  authorAssociation: string;
  /** When the declaration was made or last updated (supports edited comments) */
  declaredAt: string;
}

/**
 * Trusted author associations that can declare false positives.
 * Issue #3626: Prevent external contributors from bypassing Qodo compliance checks.
 */
const TRUSTED_AUTHOR_ASSOCIATIONS = new Set(["OWNER", "MEMBER", "COLLABORATOR"]);

/**
 * Check if any PR comment declares Qodo compliance violation as a false positive.
 *
 * This allows users to explicitly mark a Qodo violation as a false positive,
 * preventing merge blocking when Qodo misinterprets Issue content.
 *
 * Issue #3626: Only PR author or trusted roles (OWNER/MEMBER/COLLABORATOR) can declare
 * false positives to prevent external contributors from bypassing compliance checks.
 *
 * @param prNumber - The PR number to check.
 * @param repo - Repository in owner/repo format, or null for current repo.
 * @param prAuthor - The PR author's login (optional, will be fetched if not provided).
 * @returns The false positive declaration if found and authorized, null otherwise.
 */
export async function checkQodoFalsePositiveDeclaration(
  prNumber: string,
  repo: string | null = null,
  prAuthor: string | null = null,
): Promise<QodoFalsePositiveDeclaration | null> {
  try {
    // Issue #3816: DRY refactoring - delegate to fetchAllIssueComments + FromComments version
    const comments = await fetchAllIssueComments(prNumber, repo);

    // Issue #3626: Lazy load PR author - only fetch when needed for verification
    // Gemini review: Only fetch PR author if an UNTRUSTED user declares a false positive
    // If only trusted users (OWNER/MEMBER/COLLABORATOR) declare, PR author check is unnecessary
    let effectivePrAuthor = prAuthor;
    if (!effectivePrAuthor) {
      const hasUntrustedDeclaration = comments.some(
        (c) =>
          c.body?.match(QODO_FALSE_POSITIVE_PATTERN) &&
          !TRUSTED_AUTHOR_ASSOCIATIONS.has((c.author_association || "").toUpperCase()),
      );
      if (hasUntrustedDeclaration) {
        const prArgs = ["pr", "view", prNumber, "--json", "author", "--jq", ".author.login"];
        addRepoFlag(prArgs, repo);
        const prResult = await asyncSpawn("gh", prArgs, { timeout: TIMEOUT_MEDIUM * 1000 });
        if (prResult.success) {
          effectivePrAuthor = prResult.stdout.trim() || null;
        }
      }
    }

    return checkQodoFalsePositiveDeclarationFromComments(comments, effectivePrAuthor);
  } catch (error) {
    // Gemini review: Fail open to avoid blocking on errors
    console.error("[checkQodoFalsePositiveDeclaration] Exception:", error);
    return null;
  }
}

/**
 * Check if any pre-fetched comment declares Qodo compliance violation as a false positive.
 * Issue #3627: Accepts cached comments to avoid redundant API calls.
 *
 * This allows users to explicitly mark a Qodo violation as a false positive,
 * preventing merge blocking when Qodo misinterprets Issue content.
 *
 * Issue #3626: Only PR author or trusted roles (OWNER/MEMBER/COLLABORATOR) can declare
 * false positives to prevent external contributors from bypassing compliance checks.
 *
 * @param comments - Pre-fetched issue comments from fetchAllIssueComments.
 * @param prAuthor - The PR author's login. Optional; if null, authorization relies only on trusted roles.
 * @returns The false positive declaration if found and authorized, null otherwise.
 */
export function checkQodoFalsePositiveDeclarationFromComments(
  comments: IssueComment[],
  prAuthor: string | null = null,
): QodoFalsePositiveDeclaration | null {
  if (comments.length === 0) {
    return null;
  }

  // Issue #3620: Find the LATEST matching declaration (GitHub API returns oldest first)
  // Multiple declarations may exist if user re-declares after Qodo re-runs
  let latestDeclaration: QodoFalsePositiveDeclaration | null = null;

  for (const comment of comments) {
    const match = comment.body?.match(QODO_FALSE_POSITIVE_PATTERN);
    if (!match) {
      continue;
    }

    // Validate that reason is not empty after trim (Copilot review)
    const reason = match[1].trim();
    if (!reason) {
      continue; // Skip declarations without meaningful reason
    }

    // Issue #3626: Authorization check - only PR author or trusted roles can declare
    const authorAssociation = comment.author_association || "";
    const isTrustedRole = TRUSTED_AUTHOR_ASSOCIATIONS.has(authorAssociation.toUpperCase());
    const commentAuthor = comment.user?.login || "";

    const isPrAuthor = prAuthor && commentAuthor.toLowerCase() === prAuthor.toLowerCase();

    if (!isTrustedRole && !isPrAuthor) {
      // Skip declarations from unauthorized users (external contributors)
      console.error(
        `[checkQodoFalsePositiveDeclarationFromComments] Skipping unauthorized declaration from ${commentAuthor} (${authorAssociation})`,
      );
      continue;
    }

    // Keep track of the latest matching declaration based on actual timestamp
    // Use updated_at if available to support edited comments (Codex/Gemini review)
    const declaredAt = comment.updated_at || comment.created_at || "";
    const declaredTime = new Date(declaredAt).getTime();

    // Skip declarations with invalid timestamps (Cubic/Copilot review - NaN handling)
    if (Number.isNaN(declaredTime)) {
      continue;
    }

    // Compare timestamps to find the truly latest declaration
    // (GitHub API returns by creation date, but edits can make older comments newer)
    const currentLatestTime = latestDeclaration
      ? new Date(latestDeclaration.declaredAt).getTime()
      : 0;
    if (!latestDeclaration || declaredTime > currentLatestTime) {
      latestDeclaration = {
        reason,
        author: commentAuthor || "unknown",
        authorAssociation,
        declaredAt,
      };
    }
  }

  return latestDeclaration;
}
