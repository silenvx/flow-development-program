/**
 * AIレビュー（Copilot/Codex）のコメント追跡・分析を提供する。
 *
 * Why:
 *   レビューコメントの対応状況・品質を追跡し、
 *   重複コメント検出・カテゴリ分類で効率的なレビュー対応を支援する。
 *
 * What:
 *   - logReviewComment(): レビューコメントをログに記録
 *   - logCodexReviewExecution(): Codex CLI実行をログに記録
 *   - findSimilarComments(): 類似コメントを検出（重複防止）
 *   - estimateCategory(): コメント内容からカテゴリを推定
 *
 * State:
 *   - writes: .claude/logs/metrics/review-quality-{session}.jsonl
 *   - writes: .claude/logs/metrics/codex-reviews-{session}.jsonl
 *
 * Remarks:
 *   - 同一comment_idの重複記録を防止（リベース時の再記録対策）
 *   - 類似度0.85以上で重複と判定
 *   - カテゴリ: bug, style, performance, security, test, docs, refactor, other
 *
 * Changelog:
 *   - silenvx/dekita#610: レビュー品質追跡を追加
 *   - silenvx/dekita#1233: Codex CLI実行ログ追加
 *   - silenvx/dekita#1263: 重複記録防止を追加
 *   - silenvx/dekita#1389: 類似コメント検出を追加
 *   - silenvx/dekita#1758: common.pyから分離
 *   - silenvx/dekita#1840: セッション固有ファイル形式に変更
 *   - silenvx/dekita#2529: ppidフォールバック完全廃止
 *   - silenvx/dekita#3159: TypeScriptに移植
 */

import { basename } from "node:path";

import { METRICS_LOG_DIR } from "./constants";
import { getCurrentBranch } from "./git";
import { logToSessionFile, readAllSessionLogEntries } from "./logging";
import { isValidSessionId } from "./session";
import { getLocalTimestamp } from "./timestamp";

// =============================================================================
// Types
// =============================================================================

export interface ReviewComment {
  body: string;
  reviewer?: string;
  path?: string;
  line?: number;
}

export interface SimilarComment extends ReviewComment {
  similarityScore: number;
}

export type ReviewCategory =
  | "bug"
  | "style"
  | "performance"
  | "security"
  | "test"
  | "docs"
  | "refactor"
  | "other";

// =============================================================================
// Category Keywords
// =============================================================================

/**
 * Category keywords for automatic classification.
 */
export const CATEGORY_KEYWORDS: Record<ReviewCategory, readonly string[]> = {
  bug: ["bug", "error", "fix", "crash", "null", "undefined", "exception", "issue", "problem"],
  style: ["style", "format", "naming", "convention", "indent", "spacing", "whitespace"],
  performance: ["performance", "optimize", "slow", "memory", "cache", "efficient"],
  security: ["security", "auth", "permission", "injection", "xss", "csrf", "vulnerable"],
  test: ["test", "coverage", "assert", "mock", "spec", "unit", "integration"],
  docs: ["doc", "comment", "readme", "jsdoc", "typedoc", "documentation"],
  refactor: ["refactor", "extract", "simplify", "duplicate", "dry", "clean", "improve"],
  other: [],
} as const;

// =============================================================================
// Session ID Helper
// =============================================================================

/**
 * Get session ID or null if invalid.
 *
 * Security: Validates session_id to prevent path traversal attacks.
 *
 * Issue #2529: ppidフォールバック完全廃止、nullを返す。
 */
function getSessionIdWithFallback(sessionId: string | null | undefined): string | null {
  if (sessionId && isValidSessionId(sessionId)) {
    return sessionId;
  }
  return null;
}

// =============================================================================
// Reviewer Identification
// =============================================================================

/**
 * Identify the reviewer type from GitHub user login.
 *
 * Categorizes reviewers into:
 * - copilot: GitHub Copilot bot
 * - codex_cloud: Codex running on GitHub (via @codex review comment)
 * - unknown: Other reviewers (human or unrecognized bot)
 *
 * Note: codex_cli is identified by the hook context, not user login.
 */
export function identifyReviewer(userLogin: string): "copilot" | "codex_cloud" | "unknown" {
  const loginLower = userLogin.toLowerCase();

  // GitHub Copilot patterns
  if (loginLower.includes("copilot")) {
    return "copilot";
  }

  // Codex Cloud patterns (GitHub-hosted Codex)
  if (
    loginLower.includes("codex") ||
    loginLower.includes("chatgpt") ||
    loginLower.includes("openai")
  ) {
    return "codex_cloud";
  }

  return "unknown";
}

// =============================================================================
// Category Estimation
// =============================================================================

/**
 * Estimate the category of a review comment based on its content.
 *
 * Uses keyword matching to classify comments into categories.
 * Returns "other" if no category matches.
 */
export function estimateCategory(body: string | null | undefined): ReviewCategory {
  if (!body) {
    return "other";
  }

  const bodyLower = body.toLowerCase();

  // Count keyword matches for each category
  const categoryScores: Partial<Record<ReviewCategory, number>> = {};
  for (const [category, keywords] of Object.entries(CATEGORY_KEYWORDS) as [
    ReviewCategory,
    readonly string[],
  ][]) {
    if (category === "other") continue;

    let score = 0;
    for (const keyword of keywords) {
      if (bodyLower.includes(keyword)) {
        score++;
      }
    }
    if (score > 0) {
      categoryScores[category] = score;
    }
  }

  if (Object.keys(categoryScores).length === 0) {
    return "other";
  }

  // Return the category with the highest score
  let maxCategory: ReviewCategory = "other";
  let maxScore = 0;
  for (const [category, score] of Object.entries(categoryScores)) {
    if (score > maxScore) {
      maxScore = score;
      maxCategory = category as ReviewCategory;
    }
  }

  return maxCategory;
}

// =============================================================================
// Duplicate Detection
// =============================================================================

/**
 * Get the metrics log directory path.
 */
function getMetricsLogDir(): string {
  const baseDir = process.env.CLAUDE_PROJECT_DIR ?? process.cwd();
  return `${baseDir}/${METRICS_LOG_DIR}`;
}

/**
 * Check if a comment is already logged to avoid duplicates.
 *
 * Issue #1263: Prevent duplicate entries when rebases trigger re-recording.
 * Issue #1840: Now searches across all session files using readAllSessionLogEntries.
 */
async function isCommentAlreadyLogged(
  commentId: string,
  prNumber: number | string | null | undefined,
  metricsLogDir: string,
): Promise<boolean> {
  if (!commentId) {
    return false;
  }

  // Issue #1840: Search across all session files
  const entries = await readAllSessionLogEntries(metricsLogDir, "review-quality");

  for (const entry of entries) {
    if (entry.comment_id === commentId) {
      // For same PR, consider it a duplicate
      // Normalize both to strings for robust comparison
      // Note: Skip comparison if either is null to avoid false positives
      const entryPr = entry.pr_number;
      if (entryPr != null && prNumber != null) {
        if (String(entryPr) === String(prNumber)) {
          return true;
        }
      }
    }
  }

  return false;
}

// =============================================================================
// Logging Functions
// =============================================================================

export interface LogReviewCommentOptions {
  metricsLogDir?: string;
  prNumber: number | string;
  commentId: number | string;
  reviewer: string;
  category?: ReviewCategory | null;
  filePath?: string | null;
  lineNumber?: number | null;
  bodyPreview?: string | null;
  resolution?: "accepted" | "rejected" | "issue_created" | null;
  validity?: "valid" | "invalid" | "partially_valid" | null;
  issueCreated?: number | null;
  reason?: string | null;
  sessionId?: string | null;
}

/**
 * Log a review comment to the review quality log.
 *
 * Creates a JSON Lines entry for tracking review comment handling.
 * Used for both initial comment recording and resolution updates.
 *
 * Issue #1263: Skips initial recording if comment_id already exists (prevents
 * duplicates when rebases trigger re-recording). Resolution updates are always
 * logged to allow tracking comment handling outcomes.
 * Issue #1840: Now writes to session-specific file.
 */
export async function logReviewComment(options: LogReviewCommentOptions): Promise<void> {
  const metricsLogDir = options.metricsLogDir ?? getMetricsLogDir();
  const resolvedSessionId = getSessionIdWithFallback(options.sessionId);

  // Issue #1263: Skip if comment already logged (duplicate prevention)
  // Only skip for initial recordings (resolution is null/undefined) - allow resolution updates
  // Note: Use `== null` to catch both null and undefined (when property is omitted)
  const commentIdStr = options.commentId ? String(options.commentId) : null;
  if (
    commentIdStr &&
    options.resolution == null &&
    (await isCommentAlreadyLogged(commentIdStr, options.prNumber, metricsLogDir))
  ) {
    return;
  }

  // Parse PR number: keep as int if numeric, otherwise keep as string or null
  let parsedPrNumber: number | string | null = null;
  if (options.prNumber != null) {
    if (typeof options.prNumber === "number") {
      parsedPrNumber = options.prNumber;
    } else if (/^\d+$/.test(String(options.prNumber))) {
      parsedPrNumber = Number.parseInt(String(options.prNumber), 10);
    } else {
      parsedPrNumber = String(options.prNumber); // Keep "unknown" or other non-numeric values
    }
  }

  const entry: Record<string, unknown> = {
    timestamp: getLocalTimestamp(),
    session_id: resolvedSessionId,
    pr_number: parsedPrNumber,
    comment_id: options.commentId ? String(options.commentId) : null,
    reviewer: options.reviewer,
    category: options.category ?? "other",
  };

  // Add optional fields
  if (options.filePath) {
    entry.file_path = options.filePath;
  }
  if (options.lineNumber != null) {
    entry.line_number = options.lineNumber;
  }
  if (options.bodyPreview) {
    entry.body_preview = options.bodyPreview.slice(0, 200);
  }

  // Resolution fields (may be null for initial recording)
  if (options.resolution) {
    entry.resolution = options.resolution;
  }
  if (options.validity) {
    entry.validity = options.validity;
  }
  if (options.issueCreated) {
    entry.issue_created = options.issueCreated;
  }
  if (options.reason) {
    entry.reason = options.reason;
  }

  // Add branch context
  const branch = await getCurrentBranch();
  if (branch) {
    entry.branch = branch;
  }

  // Issue #1840: Write to session-specific file
  if (resolvedSessionId) {
    await logToSessionFile(metricsLogDir, "review-quality", resolvedSessionId, entry);
  }
}

export interface LogCodexReviewExecutionOptions {
  metricsLogDir?: string;
  branch?: string | null;
  base?: string | null;
  verdict: "pass" | "fail" | "error";
  commentCount: number;
  tokensUsed?: number | null;
  exitCode?: number;
  sessionId?: string | null;
}

/**
 * Log a Codex CLI review execution to codex-reviews.jsonl.
 *
 * This function logs the full review execution metadata, not just individual comments.
 * It should be called for every codex review execution, regardless of whether issues
 * were found.
 *
 * Issue #1840: Now writes to session-specific file.
 */
export async function logCodexReviewExecution(
  options: LogCodexReviewExecutionOptions,
): Promise<void> {
  const metricsLogDir = options.metricsLogDir ?? getMetricsLogDir();
  const resolvedSessionId = getSessionIdWithFallback(options.sessionId);

  let branch = options.branch;
  if (!branch) {
    branch = await getCurrentBranch();
  }

  const entry: Record<string, unknown> = {
    timestamp: getLocalTimestamp(),
    session_id: resolvedSessionId,
    branch,
    base: options.base ?? null,
    verdict: options.verdict,
    comment_count: options.commentCount,
    exit_code: options.exitCode ?? 0,
  };

  if (options.tokensUsed != null) {
    entry.tokens_used = options.tokensUsed;
  }

  // Issue #1840: Write to session-specific file
  if (resolvedSessionId) {
    await logToSessionFile(metricsLogDir, "codex-reviews", resolvedSessionId, entry);
  }
}

// =============================================================================
// Comment Similarity Detection (Issue #1389)
// =============================================================================

/**
 * Normalize comment text for comparison.
 *
 * Issue #1389: Prepare text for similarity comparison by normalizing:
 * - Lowercase conversion
 * - Whitespace normalization (multiple spaces/newlines → single space)
 * - Common punctuation removal
 */
export function normalizeCommentText(text: string | null | undefined): string {
  if (!text) {
    return "";
  }

  // Lowercase
  let normalized = text.toLowerCase();

  // Normalize whitespace (newlines, tabs, multiple spaces → single space)
  normalized = normalized.replace(/\s+/g, " ");

  // Remove common markdown formatting
  normalized = normalized.replace(/\*\*|__|\*|_|`/g, "");

  // Remove common punctuation that doesn't affect meaning
  normalized = normalized.replace(/[.,;:!?()\[\]{}\"']/g, "");

  return normalized.trim();
}

/**
 * Calculate similarity between two strings using Levenshtein distance ratio.
 *
 * Returns a ratio between 0.0 (completely different) and 1.0 (identical).
 *
 * This is a simplified implementation. For production use, consider
 * using a library like fast-levenshtein or difflib-equivalent.
 */
export function calculateCommentSimilarity(text1: string, text2: string): number {
  if (!text1 || !text2) {
    return 0.0;
  }

  // Normalize both texts for comparison
  const normalized1 = normalizeCommentText(text1);
  const normalized2 = normalizeCommentText(text2);

  if (!normalized1 || !normalized2) {
    return 0.0;
  }

  // Identical strings
  if (normalized1 === normalized2) {
    return 1.0;
  }

  // Calculate Levenshtein distance ratio (similar to difflib.SequenceMatcher)
  const len1 = normalized1.length;
  const len2 = normalized2.length;

  // Early return for very different lengths
  const maxLen = Math.max(len1, len2);
  if (maxLen === 0) {
    return 1.0;
  }

  // Simple approach: count matching characters
  // For a more accurate implementation, use dynamic programming Levenshtein
  const shorter = len1 <= len2 ? normalized1 : normalized2;
  const longer = len1 > len2 ? normalized1 : normalized2;

  // Find longest common subsequence (approximate)
  let matches = 0;
  const usedIndices = new Set<number>();

  for (const char of shorter) {
    for (let i = 0; i < longer.length; i++) {
      if (!usedIndices.has(i) && longer[i] === char) {
        matches++;
        usedIndices.add(i);
        break;
      }
    }
  }

  // Return ratio (0-1)
  return (2.0 * matches) / (len1 + len2);
}

/**
 * Find similar comments from previous review threads.
 *
 * Issue #1389: Detect duplicate review comments by comparing:
 * 1. Same reviewer (e.g., Copilot)
 * 2. Same or nearby file path
 * 3. Text similarity above threshold
 */
export function findSimilarComments(
  newComment: ReviewComment,
  previousComments: ReviewComment[],
  threshold = 0.85,
): SimilarComment[] {
  if (!newComment.body) {
    return [];
  }

  const newBody = newComment.body;
  const newReviewer = (newComment.reviewer ?? "").toLowerCase();
  const newPath = newComment.path ?? "";

  const similar: SimilarComment[] = [];

  for (const prev of previousComments) {
    const prevBody = prev.body ?? "";
    const prevReviewer = (prev.reviewer ?? "").toLowerCase();
    const prevPath = prev.path ?? "";

    // Skip if reviewers don't match (we only care about same reviewer duplicates)
    // Require both reviewers to exist and be equal - missing reviewer = non-match
    if (!newReviewer || !prevReviewer || newReviewer !== prevReviewer) {
      continue;
    }

    // Skip if completely different file paths
    if (newPath && prevPath && newPath !== prevPath) {
      // Allow partial path matches (e.g., same filename in different dirs)
      const newFilename = basename(newPath);
      const prevFilename = basename(prevPath);
      if (newFilename !== prevFilename) {
        continue;
      }
    }

    // Calculate text similarity
    const score = calculateCommentSimilarity(newBody, prevBody);
    if (score >= threshold) {
      similar.push({
        ...prev,
        similarityScore: score,
      });
    }
  }

  // Sort by similarity score (highest first)
  similar.sort((a, b) => b.similarityScore - a.similarityScore);

  return similar;
}
