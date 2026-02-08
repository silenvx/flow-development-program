/**
 * merge-checkフックのレビューコメント・スレッド検証機能。
 *
 * Why:
 *   PRマージ前にレビューコメントへの対応を検証することで、
 *   未解決のスレッドや不適切なdismissalを防ぐ。
 *
 * What:
 *   - Dismissal検証（Issue参照必須）
 *   - 応答検証（Claude Code応答必須）
 *   - 未解決スレッド検出
 *   - セキュリティ指摘のIssue化強制
 *
 * Remarks:
 *   - fix_verification_checker.ts: 修正主張の検証
 *   - ai_review_checker.ts: AIレビュアーステータス確認
 *   - DISMISSAL_KEYWORDS/DISMISSAL_EXCLUSIONSは意図的に冗長
 *     （substring matchingでも網羅性・可読性のため明示的に記載）
 *   - Python review_checker.py との互換性を維持
 *
 * Changelog:
 *   - silenvx/dekita#432: ACTION_KEYWORDS追加（修正報告と却下を区別）
 *   - silenvx/dekita#662: DISMISSAL_EXCLUSIONS追加（技術用語の誤検知防止）
 *   - silenvx/dekita#1123: verified:/検証済み/確認済み追加
 *   - silenvx/dekita#2710: セキュリティ指摘のIssue化強制追加
 *   - silenvx/dekita#3096: 定数をconstants.tsからインポートに変更
 *   - silenvx/dekita#3159: TypeScriptに移植
 */

import { isAiReviewer } from "./ai_review_checker";
import {
  ISSUE_REFERENCE_PATTERN,
  STRICT_ISSUE_REFERENCE_PATTERN,
  getRepoOwnerAndName,
  hasClaudeCodeResponseWithAuthor,
  stripCodeBlocks,
  truncateBody,
} from "./check_utils";
import {
  GEMINI_BOT_USER,
  GEMINI_SECURITY_BADGES,
  TIMEOUT_HEAVY,
  hasActionKeyword,
} from "./constants";
import { addRepoFlag } from "./github";
import { asyncSpawn } from "./spawn";

/**
 * Dismissal keywords that indicate a review comment was skipped/deferred.
 *
 * Design note: Redundant entries (e.g., "範囲外" and "今回は範囲外") are INTENTIONAL.
 * Since we use substring matching, "範囲外" already matches "今回は範囲外".
 * However, explicit entries improve readability and make the full set of
 * recognized phrases clear.
 */
const DISMISSAL_KEYWORDS = [
  "範囲外",
  "今回は範囲外",
  "軽微",
  "out of scope",
  "defer",
  "deferred",
  "後回し",
  "後で対応",
  "スコープ外",
  "対象外",
  "false positive",
  "誤検知",
];

/**
 * Exclusion patterns for technical terms that contain dismissal keywords.
 *
 * These patterns prevent false positives where technical terms like
 * "範囲外アクセス" (out-of-bounds access) are incorrectly matched.
 */
const DISMISSAL_EXCLUSIONS = [
  "範囲外アクセス",
  "範囲外参照",
  "範囲外読み取り",
  "範囲外書き込み",
  "範囲外エラー",
  "チェック対象外",
  "対象外となります",
  "警告対象外",
];

/** Thread with dismissal but no Issue reference */
export interface DismissalWithoutIssue {
  path: string;
  line: number | null;
  body: string;
}

/** Thread resolved without Claude Code response */
export interface ResolvedWithoutResponse {
  threadId: string;
  author: string;
  body: string;
}

/** Thread with security warning but no Issue reference */
export interface SecurityWithoutIssue {
  path: string;
  line: number | null;
  body: string;
  severity: string;
}

/** Unresolved AI review thread */
export interface UnresolvedAiThread {
  threadId: string;
  author: string;
  path: string;
  line: number | null;
  body: string;
}

/**
 * Execute GraphQL query with pagination support.
 *
 * @param repo - Repository in owner/repo format, or null for current repo
 */
async function executeGraphQLQuery(
  owner: string,
  name: string,
  prNumber: string,
  query: string,
  repo: string | null = null,
  cursor?: string,
): Promise<unknown | null> {
  const args = [
    "api",
    "graphql",
    "-f",
    `query=${query}`,
    "-F",
    `owner=${owner}`,
    "-F",
    `name=${name}`,
    "-F",
    `pr=${prNumber}`,
  ];

  addRepoFlag(args, repo);

  if (cursor) {
    args.push("-F", `cursor=${cursor}`);
  }

  const result = await asyncSpawn("gh", args, {
    timeout: TIMEOUT_HEAVY * 1000,
  });

  if (!result.success || !result.stdout.trim()) {
    return null;
  }

  try {
    return JSON.parse(result.stdout);
  } catch {
    return null;
  }
}

/** PageInfo type for GraphQL pagination */
interface PageInfo {
  hasNextPage?: boolean;
  endCursor?: string | null;
}

/** Common GraphQL response structure for PR review threads */
interface ReviewThreadsResponse<T> {
  data?: {
    repository?: {
      pullRequest?: {
        reviewThreads?: {
          nodes?: T[];
          pageInfo?: PageInfo;
        };
      };
    };
  };
}

/**
 * Extract review threads from GraphQL response.
 * Common extractor for all pagination functions.
 *
 * Issue #3221: Added logging to distinguish between PR not found vs no threads.
 */
function extractReviewThreads<T>(
  data: unknown,
): { nodes: T[] | undefined; pageInfo: PageInfo | undefined } | null {
  const typed = data as ReviewThreadsResponse<T> | null;
  const pullRequest = typed?.data?.repository?.pullRequest;

  // Using `=== undefined` distinguishes a missing key from a `null` value.
  if (pullRequest === undefined && data) {
    console.warn(
      "Unexpected GraphQL response structure: pullRequest not found.",
      JSON.stringify(data),
    );
    return null;
  }

  const reviewThreads = pullRequest?.reviewThreads;
  if (!reviewThreads) {
    // This is a valid state if the PR was not found (`pullRequest` is `null`)
    // or if the PR has no review threads.
    return null;
  }
  return { nodes: reviewThreads.nodes, pageInfo: reviewThreads.pageInfo };
}

/**
 * Fetch paginated review threads from GraphQL API.
 *
 * Issue #3221: Extract common pagination logic from multiple check functions.
 *
 * @param owner - Repository owner
 * @param name - Repository name
 * @param prNumber - PR number
 * @param query - GraphQL query with $cursor variable
 * @param extractThreads - Function to extract thread nodes and pageInfo from response
 * @param repo - Repository in owner/repo format, or null for current repo
 * @param maxPages - Maximum number of pages to fetch (default: 10)
 */
async function fetchPaginatedReviewThreads<T>(
  owner: string,
  name: string,
  prNumber: string,
  query: string,
  extractThreads: (
    data: unknown,
  ) => { nodes: T[] | undefined; pageInfo: PageInfo | undefined } | null,
  repo: string | null = null,
  maxPages = 10,
): Promise<T[]> {
  const threads: T[] = [];
  let cursor: string | undefined;

  for (let page = 0; page < maxPages; page++) {
    const data = await executeGraphQLQuery(owner, name, prNumber, query, repo, cursor);
    const result = extractThreads(data);

    if (!result) {
      break;
    }

    threads.push(...(result.nodes || []));

    const pageInfo = result.pageInfo;
    cursor = pageInfo?.endCursor ?? undefined;
    if (!pageInfo?.hasNextPage || !cursor) {
      break;
    }
  }

  return threads;
}

/** Thread type for dismissal check */
interface DismissalCheckThread {
  path: string;
  line: number | null;
  allComments: { nodes: Array<{ body: string }> };
}

/**
 * Check if any review threads have dismissals without Issue reference.
 *
 * Issue #1181: Changed from comment-level to thread-level checking.
 * Now checks if ANY comment in a thread has an Issue reference.
 *
 * Issue #1202: Implements pagination to handle PRs with >50 threads.
 * Issue #3221: Refactored to use fetchPaginatedReviewThreads helper.
 *
 * @param prNumber - PR number to check
 * @param repo - Repository in owner/repo format, or null for current repo
 */
export async function checkDismissalWithoutIssue(
  prNumber: string,
  repo: string | null = null,
): Promise<DismissalWithoutIssue[]> {
  try {
    const repoInfo = await getRepoOwnerAndName(repo);
    if (!repoInfo) {
      return [];
    }

    const [owner, name] = repoInfo;

    const query = `
      query($owner: String!, $name: String!, $pr: Int!, $cursor: String) {
        repository(owner: $owner, name: $name) {
          pullRequest(number: $pr) {
            reviewThreads(first: 50, after: $cursor) {
              nodes {
                id
                path
                line
                allComments: comments(first: 30) {
                  nodes {
                    body
                  }
                }
              }
              pageInfo {
                hasNextPage
                endCursor
              }
            }
          }
        }
      }
    `;

    const threads = await fetchPaginatedReviewThreads<DismissalCheckThread>(
      owner,
      name,
      prNumber,
      query,
      extractReviewThreads,
      repo,
    );

    const threadsWithDismissalWithoutIssue: DismissalWithoutIssue[] = [];

    for (const thread of threads) {
      const comments = thread.allComments?.nodes || [];
      if (comments.length === 0) continue;

      let threadHasIssueRef = false;
      const dismissalEntries: Array<{
        idx: number;
        body: string;
        targetIdx: number | null;
      }> = [];
      const actionEntries: Array<{ idx: number; targetIdx: number | null }> = [];
      const reviewerCommentIndices: number[] = [];

      for (let i = 0; i < comments.length; i++) {
        const body = comments[i].body || "";
        const bodyStripped = stripCodeBlocks(body);

        if (ISSUE_REFERENCE_PATTERN.test(bodyStripped)) {
          threadHasIssueRef = true;
        }

        const bodyLower = bodyStripped.toLowerCase();

        // Check if this is a Claude Code comment
        if (!bodyStripped.trim().endsWith("-- Claude Code")) {
          reviewerCommentIndices.push(i);
          continue;
        }

        // Find the preceding reviewer comment
        let targetReviewerIdx: number | null = null;
        for (let j = reviewerCommentIndices.length - 1; j >= 0; j--) {
          if (reviewerCommentIndices[j] < i) {
            targetReviewerIdx = reviewerCommentIndices[j];
            break;
          }
        }

        // Check for action keywords
        if (hasActionKeyword(bodyStripped)) {
          actionEntries.push({ idx: i, targetIdx: targetReviewerIdx });
          continue;
        }

        // Check for exclusion patterns
        const hasExclusion = DISMISSAL_EXCLUSIONS.some((exclusion) =>
          bodyLower.includes(exclusion.toLowerCase()),
        );
        if (hasExclusion) {
          continue;
        }

        // Check for dismissal keywords
        const hasDismissal = DISMISSAL_KEYWORDS.some((keyword) =>
          bodyLower.includes(keyword.toLowerCase()),
        );
        if (hasDismissal) {
          dismissalEntries.push({
            idx: i,
            body,
            targetIdx: targetReviewerIdx,
          });
        }
      }

      // Filter out dismissals superseded by later actions targeting the same reviewer comment
      const filteredDismissals: string[] = [];
      for (const dismissal of dismissalEntries) {
        const superseded = actionEntries.some(
          (action) => action.idx > dismissal.idx && action.targetIdx === dismissal.targetIdx,
        );
        if (!superseded) {
          filteredDismissals.push(dismissal.body);
        }
      }

      // Only flag thread if it has dismissal(s) but NO Issue reference anywhere
      if (filteredDismissals.length > 0 && !threadHasIssueRef) {
        threadsWithDismissalWithoutIssue.push({
          path: thread.path || "unknown",
          line: thread.line,
          body: truncateBody(filteredDismissals[0]),
        });
      }
    }

    return threadsWithDismissalWithoutIssue;
  } catch {
    // On error, don't block (fail open)
    return [];
  }
}

/**
 * Check if any review threads were resolved without Claude Code response.
 *
 * Issue #3211: Add pagination to handle PRs with >50 review threads.
 * Issue #3221: Refactored to use fetchPaginatedReviewThreads helper.
 * Issue #3432: Now delegates to fetchAllAiReviewThreads.
 *
 * @param prNumber - PR number to check
 * @param repo - Repository in owner/repo format, or null for current repo
 */
export async function checkResolvedWithoutResponse(
  prNumber: string,
  repo: string | null = null,
): Promise<ResolvedWithoutResponse[]> {
  const results = await fetchAllAiReviewThreads(prNumber, repo);
  return results.resolvedWithoutResponse;
}

/** Thread type for security issue check */
interface SecurityCheckThread {
  path: string;
  line: number | null;
  comments: {
    nodes: Array<{ body: string; author: { login: string } }>;
  };
}

/**
 * Check if any Gemini security warnings lack corresponding Issue references.
 *
 * Issue #2710: Gemini may flag security issues that should be tracked as Issues.
 * Issue #3221: Refactored to use fetchPaginatedReviewThreads helper.
 *
 * @param prNumber - PR number to check
 * @param repo - Repository in owner/repo format, or null for current repo
 */
export async function checkSecurityIssuesWithoutIssue(
  prNumber: string,
  repo: string | null = null,
): Promise<SecurityWithoutIssue[]> {
  try {
    const repoInfo = await getRepoOwnerAndName(repo);
    if (!repoInfo) {
      return [];
    }

    const [owner, name] = repoInfo;

    const query = `
      query($owner: String!, $name: String!, $pr: Int!, $cursor: String) {
        repository(owner: $owner, name: $name) {
          pullRequest(number: $pr) {
            reviewThreads(first: 50, after: $cursor) {
              nodes {
                id
                path
                line
                comments(first: 30) {
                  nodes {
                    body
                    author { login }
                  }
                }
              }
              pageInfo {
                hasNextPage
                endCursor
              }
            }
          }
        }
      }
    `;

    const threads = await fetchPaginatedReviewThreads<SecurityCheckThread>(
      owner,
      name,
      prNumber,
      query,
      extractReviewThreads,
      repo,
    );

    const threadsWithSecurityWithoutIssue: SecurityWithoutIssue[] = [];

    for (const thread of threads) {
      const comments = thread.comments?.nodes || [];
      if (comments.length === 0) continue;

      // Check if the first comment is from Gemini
      const firstComment = comments[0];
      const author = firstComment.author?.login || "";
      if (author.toLowerCase() !== GEMINI_BOT_USER.toLowerCase()) {
        continue;
      }

      // Check for security badges
      const body = firstComment.body || "";
      let detectedSeverity: string | null = null;

      for (const [badge, pattern] of Object.entries(GEMINI_SECURITY_BADGES)) {
        if (pattern.test(body)) {
          detectedSeverity = badge;
          break;
        }
      }

      if (!detectedSeverity) {
        continue;
      }

      // Check if ANY comment has a real Issue reference (#123)
      const threadHasIssueRef = comments.some((comment) => {
        const commentBody = comment.body || "";
        const stripped = stripCodeBlocks(commentBody);
        return STRICT_ISSUE_REFERENCE_PATTERN.test(stripped);
      });

      if (!threadHasIssueRef) {
        threadsWithSecurityWithoutIssue.push({
          path: thread.path || "unknown",
          line: thread.line,
          body: truncateBody(body),
          severity: detectedSeverity,
        });
      }
    }

    return threadsWithSecurityWithoutIssue;
  } catch {
    // On error, don't block (fail open)
    return [];
  }
}

/** Thread type for unresolved AI thread check */
interface UnresolvedAiCheckThread {
  id: string;
  isResolved: boolean;
  comments: {
    nodes: Array<{
      body: string;
      path: string;
      line: number | null;
      author: { login: string };
    }>;
  };
}

/**
 * Check if any AI review threads are still unresolved.
 *
 * Issue #3211: Add pagination to handle PRs with >50 review threads.
 * Issue #3221: Refactored to use fetchPaginatedReviewThreads helper.
 *
 * @param prNumber - PR number to check
 * @param repo - Repository in owner/repo format, or null for current repo
 */
export async function checkUnresolvedAiThreads(
  prNumber: string,
  repo: string | null = null,
): Promise<UnresolvedAiThread[]> {
  try {
    const repoInfo = await getRepoOwnerAndName(repo);
    if (!repoInfo) {
      return [];
    }

    const [owner, name] = repoInfo;

    const query = `
      query($owner: String!, $name: String!, $pr: Int!, $cursor: String) {
        repository(owner: $owner, name: $name) {
          pullRequest(number: $pr) {
            reviewThreads(first: 50, after: $cursor) {
              nodes {
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
              }
              pageInfo {
                hasNextPage
                endCursor
              }
            }
          }
        }
      }
    `;

    const threads = await fetchPaginatedReviewThreads<UnresolvedAiCheckThread>(
      owner,
      name,
      prNumber,
      query,
      extractReviewThreads,
      repo,
    );

    const unresolvedAiThreads: UnresolvedAiThread[] = [];

    for (const thread of threads) {
      if (thread.isResolved) {
        continue;
      }

      const comments = thread.comments?.nodes || [];
      if (comments.length === 0) {
        continue;
      }

      const firstComment = comments[0];
      const author = firstComment.author?.login || "unknown";

      // Use isAiReviewer for consistent detection across all AI reviewer bots
      if (!isAiReviewer(author)) {
        continue;
      }

      const body = firstComment.body || "";
      unresolvedAiThreads.push({
        threadId: thread.id || "unknown",
        author,
        path: firstComment.path || "unknown",
        line: firstComment.line,
        body: truncateBody(body),
      });
    }

    return unresolvedAiThreads;
  } catch {
    // On error, don't block (fail open)
    return [];
  }
}

// =============================================================================
// Unified AI Review Thread Check (Issue #3432)
// =============================================================================

/** AI review comment that has not received a Claude Code response */
export interface UnrespondedAiReviewComment {
  /** Thread ID */
  threadId: string;
  /** AI reviewer login name */
  author: string;
  /** File path */
  path: string;
  /** Line number */
  line: number | null;
  /** Comment body (truncated) */
  body: string;
}

/** Combined result from unified AI review thread fetch */
export interface AiReviewThreadResults {
  /** Check 5: Resolved threads without Claude Code response */
  resolvedWithoutResponse: ResolvedWithoutResponse[];
  /** Check 7.6: Unresolved threads without Claude Code response */
  unrespondedAiReviewComments: UnrespondedAiReviewComment[];
}

/** Thread type for unified AI review check (superset of fields) */
interface UnifiedAiReviewCheckThread {
  id: string;
  path: string;
  line: number | null;
  isResolved: boolean;
  firstComment: { nodes: Array<{ body: string; author: { login: string } }> };
  recentComments: { nodes: Array<{ body: string; author: { login: string } }> };
}

/**
 * Fetch all AI review threads once and classify them for Check 5 and Check 7.6.
 *
 * Issue #3432: Eliminates duplicate GraphQL API calls by fetching review threads
 * once and sharing data between:
 * - Check 5 (checkResolvedWithoutResponse): RESOLVED threads without response
 * - Check 7.6 (checkUnrespondedAiReviewComments): UNRESOLVED threads without response
 *
 * @param prNumber - The PR number to check.
 * @param repo - Repository in owner/repo format, or null for current repo.
 * @returns Combined results for both checks.
 */
export async function fetchAllAiReviewThreads(
  prNumber: string,
  repo: string | null = null,
): Promise<AiReviewThreadResults> {
  const emptyResult: AiReviewThreadResults = {
    resolvedWithoutResponse: [],
    unrespondedAiReviewComments: [],
  };

  try {
    const repoInfo = await getRepoOwnerAndName(repo);
    if (!repoInfo) {
      return emptyResult;
    }

    const [owner, name] = repoInfo;

    // Unified query: includes path/line (needed by Check 7.6) and isResolved
    // (needed to split between Check 5 and Check 7.6).
    // Uses first:1 for thread author check and last:100 for recent responses
    // to avoid false negatives when threads have >30 comments.
    const query = `
      query($owner: String!, $name: String!, $pr: Int!, $cursor: String) {
        repository(owner: $owner, name: $name) {
          pullRequest(number: $pr) {
            reviewThreads(first: 50, after: $cursor) {
              nodes {
                id
                path
                line
                isResolved
                firstComment: comments(first: 1) {
                  nodes {
                    body
                    author { login }
                  }
                }
                recentComments: comments(last: 100) {
                  nodes {
                    body
                    author { login }
                  }
                }
              }
              pageInfo {
                hasNextPage
                endCursor
              }
            }
          }
        }
      }
    `;

    const threads = await fetchPaginatedReviewThreads<UnifiedAiReviewCheckThread>(
      owner,
      name,
      prNumber,
      query,
      extractReviewThreads,
      repo,
    );

    const resolvedWithoutResponse: ResolvedWithoutResponse[] = [];
    const unrespondedAiReviewComments: UnrespondedAiReviewComment[] = [];

    for (const thread of threads) {
      const firstComment = thread.firstComment?.nodes?.[0];
      if (!firstComment) {
        continue;
      }

      const author = firstComment.author?.login || "unknown";

      // Skip threads not started by AI reviewers
      if (!isAiReviewer(author)) {
        continue;
      }

      // Check if any recent comment has Claude Code signature.
      // Issue #3439: Use author-aware version to filter out AI bot comments
      // that quote the signature.
      const recentComments = thread.recentComments?.nodes || [];
      const hasResponse = hasClaudeCodeResponseWithAuthor(recentComments, isAiReviewer);

      if (hasResponse) {
        continue;
      }

      if (thread.isResolved) {
        // Check 5: Resolved without response
        resolvedWithoutResponse.push({
          threadId: thread.id || "unknown",
          author,
          body: truncateBody(firstComment.body || ""),
        });
      } else {
        // Check 7.6: Unresolved without response
        unrespondedAiReviewComments.push({
          threadId: thread.id || "unknown",
          author,
          path: thread.path || "unknown",
          line: thread.line,
          body: truncateBody(firstComment.body || ""),
        });
      }
    }

    return { resolvedWithoutResponse, unrespondedAiReviewComments };
  } catch {
    // On error, don't block (fail open)
    return emptyResult;
  }
}

/**
 * Check for UNRESOLVED AI review comments that have not received a Claude Code response.
 *
 * Issue #3429: Wrapper for standalone use and backward compatibility.
 * Issue #3432: Now delegates to fetchAllAiReviewThreads.
 *
 * @param prNumber - The PR number to check.
 * @param repo - Repository in owner/repo format, or null for current repo.
 * @returns List of unresolved AI review comments that need a response.
 */
export async function checkUnrespondedAiReviewComments(
  prNumber: string,
  repo: string | null = null,
): Promise<UnrespondedAiReviewComment[]> {
  const results = await fetchAllAiReviewThreads(prNumber, repo);
  return results.unrespondedAiReviewComments;
}
