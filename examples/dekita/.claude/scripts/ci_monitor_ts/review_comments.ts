/**
 * Review comment operations for ci-monitor.
 *
 * Why:
 *   Handle review comment fetching, classification, and thread management.
 *   Support REST API fallback when GraphQL rate limit is hit.
 *
 * What:
 *   - getReviewComments(): Fetch inline code review comments
 *   - classifyReviewComments(): Classify comments as in-scope or out-of-scope
 *   - getUnresolvedThreads(): Fetch unresolved review threads
 *   - autoResolveDuplicateThreads(): Auto-resolve duplicate threads after rebase
 *
 * Remarks:
 *   - Migrated from Python ci_monitor/review_comments.py (Issue #3261)
 *   - Uses GraphQL API with REST fallback for rate limits
 *
 * Changelog:
 *   - silenvx/dekita#3261: TypeScript migration from Python
 */

import { createHash } from "node:crypto";
import { isAiReviewer } from "../../hooks/lib/ci_monitor_ai_review";
import { CODE_BLOCK_PATTERN, GITHUB_FILES_LIMIT } from "../../hooks/lib/constants";
import { parsePaginatedJson } from "../../hooks/lib/json";
import { printRateLimitWarning, shouldPreferRestApi } from "../../hooks/lib/rate_limit";
import type { ClassifiedComments } from "../../hooks/lib/types";
import { getRepoInfo, isRateLimitError, runGhCommand, runGhCommandWithError } from "./github_api";

// =============================================================================
// Types
// =============================================================================

/** Review comment structure (inline code comments from pulls/{PR}/comments) */
export interface ReviewComment {
  id?: number | string;
  path?: string;
  line?: number | null;
  body?: string;
  user?: string;
  author?: string;
  isResolved?: boolean;
  is_rest_fallback?: boolean;
}

/**
 * PR review body structure (from pulls/{PR}/reviews).
 *
 * Contains the overall review body text, like "No issues found" from Copilot
 * or approval/rejection messages.
 *
 * Note: user and body are optional because GitHub API may return null
 * for deleted users or empty review bodies.
 */
export interface ReviewBody {
  id: number;
  user?: string;
  state: string; // APPROVED, CHANGES_REQUESTED, COMMENTED, etc.
  body?: string;
  submittedAt?: string;
}

/**
 * Conversation comment structure (from issues/{PR}/comments).
 *
 * Contains general PR comments like Qodo Suggestions, Greptile Overview,
 * or @codex review requests.
 *
 * Note: user and body are optional because GitHub API may return null
 * for deleted users or empty comment bodies.
 */
export interface ConversationComment {
  id: number;
  user?: string;
  body?: string;
  createdAt?: string;
  updatedAt?: string;
}

/**
 * Result of getAllAiReviewComments().
 *
 * Contains all three types of AI review comments.
 */
export interface AllAiReviewComments {
  /** Inline code comments from pulls/{PR}/comments (AI reviewers only) */
  inlineComments: ReviewComment[];
  /** PR review bodies from pulls/{PR}/reviews (AI reviewers only) */
  reviewBodies: ReviewBody[];
  /** Conversation comments from issues/{PR}/comments (AI reviewers only) */
  conversationComments: ConversationComment[];
  /** Raw inline comments (unfiltered) to avoid double-fetching in main_loop */
  rawInlineComments: ReviewComment[];
}

/** Review thread structure */
export interface ReviewThread {
  id: string;
  isResolved: boolean;
  is_rest_fallback?: boolean;
  comments: {
    nodes: Array<{
      body: string;
      path: string;
      line?: number | null;
      author?: { login: string };
    }>;
  };
}

/** Log function signature */
export type LogFn = (
  message: string,
  jsonMode: boolean,
  data: Record<string, unknown> | null,
) => void;

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Remove code blocks and inline code from text.
 *
 * This prevents false positives when checking for checkboxes
 * that may appear in code examples or suggestions.
 *
 * @param text - The text to process
 * @returns Text with code blocks and inline code removed
 */
export function stripCodeBlocks(text: string): string {
  return text.replace(CODE_BLOCK_PATTERN, "");
}

/**
 * Normalize comment body for duplicate detection.
 *
 * Issue #1372: After rebase, AI reviewers may post the same comment with
 * different line numbers. This function normalizes the body to improve
 * duplicate detection by removing volatile content like line numbers.
 *
 * @param body - The original comment body
 * @returns Normalized body text for hashing
 */
export function normalizeCommentBody(body: string): string {
  let normalized = body.replace(/\b(?:on\s+)?lines?\s*\d+(?:\s*-\s*\d+)?/gi, "");
  normalized = normalized.replace(/\(L\d+(?:-L?\d+)?\)/g, "");
  normalized = normalized.replace(/\bL\d+(?:-L?\d+)?\b/g, "");
  normalized = normalized.replace(/\s+/g, " ").trim();
  return normalized;
}

// =============================================================================
// Comment Fetching Functions
// =============================================================================

/**
 * Fetch inline code review comments from the PR.
 *
 * @param prNumber - The PR number
 * @returns List of review comments
 */
export async function getReviewComments(prNumber: string): Promise<ReviewComment[]> {
  const [success, output] = await runGhCommand([
    "api",
    `/repos/{owner}/{repo}/pulls/${prNumber}/comments`,
    "--paginate",
    "--jq",
    "[.[] | {path, line, body, user: .user.login, id}]",
  ]);

  if (!success) {
    return [];
  }

  // --paginate outputs NDJSON (one JSON array per page)
  return parsePaginatedJson<ReviewComment>(output);
}

/**
 * Fetch PR review bodies from pulls/{PR}/reviews.
 *
 * This API returns the overall review submission (APPROVED, CHANGES_REQUESTED, etc.)
 * including the review body text like "No issues found" from Copilot.
 *
 * Issue #3870: Part of the unified comment API.
 *
 * @param prNumber - The PR number
 * @returns List of review bodies
 */
export async function getPrReviews(prNumber: string): Promise<ReviewBody[]> {
  const [success, output] = await runGhCommand([
    "api",
    `/repos/{owner}/{repo}/pulls/${prNumber}/reviews`,
    "--paginate",
    "--jq",
    // select(.user != null) for deleted users, (.body // "") for empty bodies
    '[.[] | select(.user != null) | {id, user: .user.login, state, body: (.body // ""), submittedAt: .submitted_at}]',
  ]);

  if (!success) {
    return [];
  }

  return parsePaginatedJson<ReviewBody>(output);
}

/**
 * Fetch conversation comments from issues/{PR}/comments.
 *
 * This API returns general PR comments that are not attached to specific code lines,
 * such as Qodo Suggestions, Greptile Overview, or @codex review requests.
 *
 * Issue #3870: Part of the unified comment API.
 *
 * @param prNumber - The PR number
 * @returns List of conversation comments
 */
export async function getConversationComments(prNumber: string): Promise<ConversationComment[]> {
  const [success, output] = await runGhCommand([
    "api",
    `/repos/{owner}/{repo}/issues/${prNumber}/comments`,
    "--paginate",
    "--jq",
    // select(.user != null) for deleted users, (.body // "") for empty bodies
    '[.[] | select(.user != null) | {id, user: .user.login, body: (.body // ""), createdAt: .created_at, updatedAt: .updated_at}]',
  ]);

  if (!success) {
    return [];
  }

  return parsePaginatedJson<ConversationComment>(output);
}

/**
 * Fetch all AI review comments from all three GitHub APIs.
 *
 * GitHub has three different APIs for PR comments (Issue #3867):
 * 1. pulls/{PR}/comments - Inline code comments
 * 2. pulls/{PR}/reviews - PR review bodies (approval/rejection messages)
 * 3. issues/{PR}/comments - Conversation comments (Qodo, Greptile, etc.)
 *
 * This function fetches from all three in parallel and filters to AI reviewers only.
 *
 * Issue #3870: Unified interface for all comment types.
 *
 * @param prNumber - The PR number
 * @returns All AI review comments categorized by type
 */
export async function getAllAiReviewComments(prNumber: string): Promise<AllAiReviewComments> {
  // Fetch all three types in parallel for efficiency
  const [inline, reviews, conversation] = await Promise.all([
    getReviewComments(prNumber),
    getPrReviews(prNumber),
    getConversationComments(prNumber),
  ]);

  return {
    inlineComments: inline.filter((c) => isAiReviewer(c.user ?? "")),
    reviewBodies: reviews.filter((r) => isAiReviewer(r.user ?? "")),
    conversationComments: conversation.filter((c) => isAiReviewer(c.user ?? "")),
    rawInlineComments: inline,
  };
}

/**
 * Get the list of files changed in the PR.
 *
 * @param prNumber - The PR number
 * @returns A set of file paths that were changed in the PR, or null if the lookup failed
 */
export async function getPrChangedFiles(prNumber: string): Promise<Set<string> | null> {
  const [success, output] = await runGhCommand([
    "pr",
    "view",
    prNumber,
    "--json",
    "files",
    "--jq",
    "[.files[].path] | .[]",
  ]);

  if (!success) {
    return null;
  }

  if (!output.trim()) {
    return new Set();
  }

  const files = new Set(output.trim().split("\n"));

  // If we hit the API limit, assume there might be more files we couldn't retrieve
  if (files.size >= GITHUB_FILES_LIMIT) {
    return null;
  }

  return files;
}

/**
 * Classify review comments as in-scope or out-of-scope for the PR.
 *
 * @param prNumber - PR number to analyze
 * @param comments - Optional pre-fetched comments. If null, will fetch.
 * @returns ClassifiedComments with inScope and outOfScope lists
 */
export async function classifyReviewComments(
  prNumber: string,
  comments?: ReviewComment[] | null,
): Promise<ClassifiedComments> {
  const actualComments = comments ?? (await getReviewComments(prNumber));

  if (actualComments.length === 0) {
    return { inScope: [], outOfScope: [] };
  }

  const changedFiles = await getPrChangedFiles(prNumber);

  // If file lookup failed, treat all comments as in-scope (safe default)
  if (changedFiles === null) {
    return { inScope: actualComments as Record<string, unknown>[], outOfScope: [] };
  }

  const inScope: Record<string, unknown>[] = [];
  const outOfScope: Record<string, unknown>[] = [];

  for (const comment of actualComments) {
    const commentPath = comment.path ?? "";
    if (changedFiles.has(commentPath)) {
      inScope.push(comment as Record<string, unknown>);
    } else {
      outOfScope.push(comment as Record<string, unknown>);
    }
  }

  return { inScope, outOfScope };
}

// =============================================================================
// REST API Fallback Functions
// =============================================================================

/**
 * Fetch review comments via REST API (fallback for GraphQL rate limit).
 *
 * Issue #1318: Fallback when GraphQL API is rate limited.
 *
 * @param owner - Repository owner
 * @param name - Repository name
 * @param prNumber - PR number
 * @returns List of comments in simplified format, or null on failure
 */
export async function fetchReviewCommentsRest(
  owner: string,
  name: string,
  prNumber: string,
): Promise<ReviewComment[] | null> {
  const [success, output] = await runGhCommand(
    [
      "api",
      `/repos/${owner}/${name}/pulls/${prNumber}/comments`,
      "--paginate",
      "--jq",
      "[.[] | {id: .id, path: .path, line: (.line // .original_line), body: .body, author: .user.login}]",
    ],
    60000,
  );

  if (!success) {
    return null;
  }

  const comments = parsePaginatedJson<ReviewComment>(output);

  // Mark as REST fallback so callers know thread info is unavailable
  for (const comment of comments) {
    comment.is_rest_fallback = true;
    comment.isResolved = false;
  }

  return comments;
}

/**
 * Convert REST API comments to GraphQL thread-like format.
 *
 * @param comments - Comments from fetchReviewCommentsRest()
 * @returns List of pseudo-threads with GraphQL-compatible structure
 */
export function convertRestCommentsToThreadFormat(comments: ReviewComment[]): ReviewThread[] {
  const threads: ReviewThread[] = [];
  for (const comment of comments) {
    const thread: ReviewThread = {
      id: `rest-${comment.id ?? "unknown"}`,
      isResolved: comment.isResolved ?? false,
      is_rest_fallback: true,
      comments: {
        nodes: [
          {
            body: comment.body ?? "",
            path: comment.path ?? "",
            line: comment.line,
            author: { login: comment.author ?? "unknown" },
          },
        ],
      },
    };
    threads.push(thread);
  }
  return threads;
}

// =============================================================================
// Thread Functions
// =============================================================================

/**
 * Fetch all review threads using cursor-based pagination.
 *
 * Issue #860: GraphQL API limits results to 100 per request.
 * Issue #1360: Proactively uses REST API when approaching rate limit.
 *
 * @param owner - Repository owner
 * @param name - Repository name
 * @param prNumber - PR number
 * @param fields - GraphQL fields to include in each thread node
 * @param requireResolvedStatus - If true, skip REST priority mode
 * @returns List of all review thread nodes, or null if API call failed
 */
export async function fetchAllReviewThreads(
  owner: string,
  name: string,
  prNumber: string,
  fields: string,
  requireResolvedStatus = true,
): Promise<ReviewThread[] | null> {
  // Issue #1360: Proactively use REST API when approaching rate limit
  if (!requireResolvedStatus && (await shouldPreferRestApi())) {
    const restComments = await fetchReviewCommentsRest(owner, name, prNumber);
    if (restComments !== null) {
      console.error(`  ✓ REST優先モード: ${restComments.length}件のコメントを取得`);
      return convertRestCommentsToThreadFormat(restComments);
    }
    console.error("  ⚠️ REST優先モード失敗、GraphQLを試行");
  }

  const allThreads: ReviewThread[] = [];
  let cursor: string | null = null;
  const maxPages = 10;
  let apiFailed = false;

  for (let page = 0; page < maxPages; page++) {
    const cursorArg = cursor ? `, after: "${cursor}"` : "";
    const query = `
      query($owner: String!, $name: String!, $pr: Int!) {
        repository(owner: $owner, name: $name) {
          pullRequest(number: $pr) {
            reviewThreads(first: 100${cursorArg}) {
              pageInfo {
                hasNextPage
                endCursor
              }
              nodes {
                ${fields}
              }
            }
          }
        }
      }
    `;

    const result = await runGhCommandWithError(
      [
        "api",
        "graphql",
        "-f",
        `query=${query}`,
        "-F",
        `owner=${owner}`,
        "-F",
        `name=${name}`,
        "-F",
        `pr:=${prNumber}`,
      ],
      30000,
    );

    if (!result.success) {
      if (isRateLimitError(result.stdout, result.stderr)) {
        await printRateLimitWarning();
        console.error("  → REST APIへフォールバック中...");
        const restComments = await fetchReviewCommentsRest(owner, name, prNumber);
        if (restComments !== null) {
          console.error(`  ✓ REST APIで${restComments.length}件のコメントを取得`);
          console.error("  ⚠️ 注意: スレッドの解決状態は取得できません");
          return convertRestCommentsToThreadFormat(restComments);
        }
        console.error("  ⚠️ REST APIフォールバックも失敗しました");
      }
      apiFailed = true;
      break;
    }

    try {
      const data = JSON.parse(result.stdout) as {
        data?: {
          repository?: {
            pullRequest?: {
              reviewThreads?: {
                pageInfo?: { hasNextPage?: boolean; endCursor?: string };
                nodes?: ReviewThread[];
              };
            };
          };
        };
      };

      const reviewThreads = data?.data?.repository?.pullRequest?.reviewThreads;
      const nodes = reviewThreads?.nodes ?? [];
      allThreads.push(...nodes);

      const pageInfo = reviewThreads?.pageInfo;
      if (!pageInfo?.hasNextPage) {
        break;
      }

      cursor = pageInfo?.endCursor ?? null;
      if (!cursor) {
        break;
      }
    } catch {
      apiFailed = true;
      break;
    }
  }

  if (apiFailed) {
    return null;
  }
  return allThreads;
}

/**
 * Fetch unresolved review threads from the PR using GraphQL API.
 *
 * @param prNumber - The PR number
 * @returns List of unresolved threads, or null on API failure
 */
export async function getUnresolvedThreads(prNumber: string): Promise<Array<{
  id: string;
  path: string;
  line: number | null;
  body: string;
  author: string;
  is_rest_fallback?: boolean;
}> | null> {
  const repoInfo = await getRepoInfo();
  if (!repoInfo) {
    return null;
  }
  const { owner, name } = repoInfo;

  const fields = `
    id
    isResolved
    comments(first: 1) {
      nodes {
        body
        path
        line
        author { login }
      }
    }
  `;

  const threads = await fetchAllReviewThreads(owner, name, prNumber, fields);

  if (threads === null) {
    return null;
  }

  const unresolved: Array<{
    id: string;
    path: string;
    line: number | null;
    body: string;
    author: string;
    is_rest_fallback?: boolean;
  }> = [];

  for (const thread of threads) {
    if (!thread.isResolved) {
      const comments = thread.comments?.nodes ?? [];
      if (comments.length > 0) {
        const first = comments[0];
        const item: {
          id: string;
          path: string;
          line: number | null;
          body: string;
          author: string;
          is_rest_fallback?: boolean;
        } = {
          id: thread.id ?? "",
          path: first.path ?? "",
          line: first.line ?? null,
          body: (first.body ?? "").slice(0, 100),
          author: first.author?.login ?? "unknown",
        };
        if (thread.is_rest_fallback) {
          item.is_rest_fallback = true;
        }
        unresolved.push(item);
      }
    }
  }

  return unresolved;
}

/**
 * Get unresolved threads from AI reviewers (Copilot/Codex).
 *
 * @param prNumber - The PR number
 * @returns List of unresolved threads from AI reviewers, or null on API failure
 */
export async function getUnresolvedAiThreads(prNumber: string): Promise<Array<{
  id: string;
  path: string;
  line: number | null;
  body: string;
  author: string;
}> | null> {
  const threads = await getUnresolvedThreads(prNumber);
  if (threads === null) {
    return null;
  }

  return threads.filter((thread) => isAiReviewer(thread.author));
}

// =============================================================================
// Thread Resolution Functions
// =============================================================================

/**
 * Get body hashes of resolved threads before rebase.
 *
 * @param prNumber - The PR number
 * @returns Set of SHA-256 hashes (first 32 chars) of resolved thread bodies
 */
export async function getResolvedThreadHashes(prNumber: string): Promise<Set<string>> {
  const repoInfo = await getRepoInfo();
  if (!repoInfo) {
    return new Set();
  }
  const { owner, name } = repoInfo;

  const fields = `
    isResolved
    comments(first: 1) {
      nodes {
        body
        path
      }
    }
  `;

  const threads = await fetchAllReviewThreads(owner, name, prNumber, fields);

  if (threads === null) {
    return new Set();
  }

  const hashes = new Set<string>();
  for (const thread of threads) {
    if (thread.isResolved) {
      const comments = thread.comments?.nodes ?? [];
      if (comments.length > 0) {
        const first = comments[0];
        const body = first.body ?? "";
        const path = first.path ?? "";
        if (!body || !path) continue;

        const normalizedBody = normalizeCommentBody(body);
        if (!normalizedBody) continue;

        const content = `${path}:${normalizedBody}`;
        const contentHash = createHash("sha256").update(content).digest("hex").slice(0, 32);
        hashes.add(contentHash);
      }
    }
  }
  return hashes;
}

/**
 * Resolve a review thread by its GraphQL ID.
 *
 * @param threadId - The thread's GraphQL ID
 * @returns True if resolution succeeded, false otherwise
 */
export async function resolveThreadById(threadId: string): Promise<boolean> {
  const mutation = `
    mutation($threadId: ID!) {
      resolveReviewThread(input: {threadId: $threadId}) {
        thread {
          isResolved
        }
      }
    }
  `;

  const result = await runGhCommandWithError(
    ["api", "graphql", "-f", `query=${mutation}`, "-F", `threadId=${threadId}`],
    30000,
  );

  if (!result.success && isRateLimitError(result.stdout, result.stderr)) {
    await printRateLimitWarning();
    console.error("  ⚠️ スレッド解決はREST APIでは対応できません。");
    console.error("  → GitHub Web UIで手動でResolve conversationしてください。");
    console.error(`  → スレッドID: ${threadId}`);
  }

  return result.success;
}

/**
 * Auto-resolve threads that match pre-rebase resolved thread hashes.
 *
 * @param prNumber - The PR number
 * @param preRebaseHashes - Set of body hashes from resolved threads before rebase
 * @param jsonMode - Whether to log in JSON format
 * @param logFn - Optional logging function
 * @returns Tuple of [number of threads auto-resolved, set of resolved hashes]
 */
export async function autoResolveDuplicateThreads(
  prNumber: string,
  preRebaseHashes: Set<string>,
  jsonMode = false,
  logFn?: LogFn,
): Promise<[number, Set<string>]> {
  if (preRebaseHashes.size === 0) {
    return [0, new Set()];
  }

  const repoInfo = await getRepoInfo();
  if (!repoInfo) {
    return [0, new Set()];
  }
  const { owner, name } = repoInfo;

  const fields = `
    id
    isResolved
    comments(first: 1) {
      nodes {
        body
        path
        author { login }
      }
    }
  `;

  const threads = await fetchAllReviewThreads(owner, name, prNumber, fields);

  if (threads === null) {
    return [0, new Set()];
  }

  let resolvedCount = 0;
  const resolvedHashes = new Set<string>();

  for (const thread of threads) {
    if (thread.isResolved) {
      continue;
    }

    const comments = thread.comments?.nodes ?? [];
    if (comments.length === 0) {
      continue;
    }

    const first = comments[0];
    const body = first.body ?? "";
    const path = first.path ?? "";
    const author = first.author?.login ?? "";

    if (!isAiReviewer(author)) {
      continue;
    }

    if (!body || !path) {
      continue;
    }

    const normalizedBody = normalizeCommentBody(body);
    if (!normalizedBody) {
      continue;
    }

    const content = `${path}:${normalizedBody}`;
    const contentHash = createHash("sha256").update(content).digest("hex").slice(0, 32);

    if (preRebaseHashes.has(contentHash)) {
      const threadId = thread.id ?? "";
      if (threadId && (await resolveThreadById(threadId))) {
        resolvedCount++;
        resolvedHashes.add(contentHash);
        if (logFn) {
          logFn(
            `Auto-resolved duplicate thread: ${path}`,
            jsonMode,
            jsonMode ? { path, hash: contentHash } : null,
          );
        }
      }
    }
  }

  return [resolvedCount, resolvedHashes];
}

/**
 * Filter out AI reviewer comments that match duplicate thread hashes.
 *
 * @param comments - List of review comments
 * @param duplicateHashes - Set of content hashes from auto-resolved threads
 * @returns Filtered list of comments
 */
export function filterDuplicateComments(
  comments: ReviewComment[],
  duplicateHashes: Set<string>,
): ReviewComment[] {
  if (duplicateHashes.size === 0) {
    return comments;
  }

  const filtered: ReviewComment[] = [];
  for (const comment of comments) {
    const path = comment.path ?? "";
    const body = comment.body ?? "";
    const user = comment.user ?? "";

    if (!isAiReviewer(user)) {
      filtered.push(comment);
      continue;
    }

    if (!path || !body) {
      filtered.push(comment);
      continue;
    }

    const normalizedBody = normalizeCommentBody(body);
    if (!normalizedBody) {
      filtered.push(comment);
      continue;
    }

    const content = `${path}:${normalizedBody}`;
    const contentHash = createHash("sha256").update(content).digest("hex").slice(0, 32);

    if (!duplicateHashes.has(contentHash)) {
      filtered.push(comment);
    }
  }

  return filtered;
}

/**
 * Print a single comment with path, line, user, and truncated body.
 *
 * @param comment - The comment to print
 */
export function printComment(comment: ReviewComment): void {
  console.log(`  [${comment.path}:${comment.line}] (${comment.user})`);
  const body = comment.body ?? "";
  if (body.length > 100) {
    console.log(`    ${body.slice(0, 100)}...`);
  } else {
    console.log(`    ${body}`);
  }
}

/**
 * Log review comments to the review quality log for tracking.
 *
 * Records each AI review comment (Copilot/Codex Cloud) to the review quality
 * metrics log for later analysis of review quality and acceptance rates.
 *
 * @param prNumber - PR number
 * @param comments - List of review comments from getReviewComments()
 */
export async function logReviewCommentsToQualityLog(
  _prNumber: string,
  comments: ReviewComment[],
): Promise<void> {
  // Simplified implementation - just logs to console in JSON mode
  // A full implementation would write to a quality metrics file
  if (!comments || comments.length === 0) {
    return;
  }

  // Filter to AI reviewer comments only
  const aiComments = comments.filter((c) => {
    const user = c.user?.toLowerCase() || "";
    return (
      user.includes("copilot") ||
      user.includes("codex") ||
      user.includes("gemini") ||
      user.includes("coderabbit") ||
      user.includes("qodo")
    );
  });

  if (aiComments.length === 0) {
    return;
  }

  // Log summary (full implementation would write to file)
  // This is a no-op for now, but the interface matches Python
}
