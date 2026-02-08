/**
 * merge-check関連の共通ユーティリティ。
 *
 * Why:
 *   merge-check関連の複数モジュールで共通して使用する機能を一箇所にまとめ、
 *   重複を排除する。
 *
 * What:
 *   - テキスト処理（truncation、code block stripping）
 *   - 共通パターン（Issue参照、PRボディ品質）
 *   - リポジトリ情報取得
 *
 * Remarks:
 *   - Python check_utils.py との互換性を維持
 *   - strip_code_blocksはコード例での誤検知防止に使用
 *
 * Changelog:
 *   - silenvx/dekita#2874: TypeScriptに移植
 *   - silenvx/dekita#3159: getRepoOwnerAndName追加
 */

import { TIMEOUT_MEDIUM } from "./constants";
import { asyncSpawn } from "./spawn";

/**
 * Truncate body text for display.
 */
export function truncateBody(body: string, maxLength = 100): string {
  if (body.length > maxLength) {
    return `${body.slice(0, maxLength)}...`;
  }
  return body;
}

// Pattern to match fenced code blocks and inline code
const CODE_BLOCK_PATTERN = /```[\s\S]*?```|`[^`\n]+`/g;

/**
 * Remove code blocks and inline code from text.
 *
 * Prevents false positives when checking for keywords that may appear in code examples.
 */
export function stripCodeBlocks(text: string): string {
  return text.replace(CODE_BLOCK_PATTERN, "");
}

/**
 * Check if a comment body has a Claude Code signature.
 *
 * Issue #3429: Used to verify that AI review comments have received
 * a response from Claude Code. The signature must be at the end of
 * the text (after stripping code blocks) to avoid false positives
 * from signatures appearing in quoted code examples.
 *
 * Uses regex with \s*$ to tolerate trailing whitespace (spaces, newlines)
 * after the signature, which may be added by editors or formatting tools.
 *
 * @param body - The comment body to check.
 * @returns true if the body ends with "-- Claude Code" signature.
 */
export function hasClaudeCodeSignature(body: string | null | undefined): boolean {
  if (!body) return false;
  return /-- Claude Code\s*$/.test(stripCodeBlocks(body));
}

/**
 * Check if any comment in a list has a Claude Code signature.
 *
 * Issue #3429: Helper function to check if a thread has received
 * a response from Claude Code.
 *
 * @deprecated Use hasClaudeCodeResponseWithAuthor instead to filter out AI bot comments.
 * This function is kept for backward compatibility but may produce false positives
 * if an AI bot quotes "-- Claude Code" in their comment.
 *
 * @param comments - Array of comment objects with body property.
 * @returns true if any comment has the Claude Code signature.
 */
export function hasClaudeCodeResponse(comments: Array<{ body?: string | null }>): boolean {
  return comments.some((comment) => hasClaudeCodeSignature(comment.body));
}

/**
 * Check if any non-AI-bot comment in a list has a Claude Code signature.
 *
 * Issue #3439: Enhanced version of hasClaudeCodeResponse that filters out
 * comments from AI bots, preventing false positives when an AI bot quotes
 * "-- Claude Code" in their comment.
 *
 * @param comments - Array of comment objects with body and author.login properties.
 * @param isAiReviewer - Function to check if an author is an AI reviewer.
 * @returns true if any non-AI-bot comment has the Claude Code signature.
 */
export function hasClaudeCodeResponseWithAuthor(
  comments: Array<{ body?: string | null; author?: { login: string } | null } | null>,
  isAiReviewer: (author: string) => boolean,
): boolean {
  return comments.some((comment) => {
    // Skip null elements that may appear in GraphQL responses
    if (!comment) {
      return false;
    }
    const author = comment.author?.login || "";
    // Skip AI bot comments to avoid false positives from quoted signatures
    if (isAiReviewer(author)) {
      return false;
    }
    return hasClaudeCodeSignature(comment.body);
  });
}

/**
 * Issue reference pattern: #123 or Issue #123 or issue作成
 *
 * Design note: The pattern has two parts:
 * 1. "#\d+" for issue numbers (e.g., "#123", "Issue #123")
 * 2. "issue\s*を?\s*作成" for Japanese "Issue作成" phrases
 *
 * Why include "issue作成"?
 * When someone writes "後でissue作成します", they're acknowledging the need for a follow-up
 * Issue. While they haven't yet created it (no #XXX), they've explicitly stated the intention
 * to do so.
 */
export const ISSUE_REFERENCE_PATTERN = /#\d+|issue\s*を?\s*作成/i;

/**
 * Strict Issue reference pattern (Issue #2710)
 * Unlike ISSUE_REFERENCE_PATTERN which accepts "issue作成", this requires
 * an actual Issue number (#123) since security issues must be tracked in a real Issue.
 */
export const STRICT_ISSUE_REFERENCE_PATTERN = /#\d+/;

// Incremental migration keywords patterns
const INCREMENTAL_KEYWORDS_PATTERNS = ["incremental", "phase\\s*\\d+", "follow-?up"];

const INCREMENTAL_KEYWORDS_PATTERN = new RegExp(INCREMENTAL_KEYWORDS_PATTERNS.join("|"), "i");

// Why section keywords (shared across patterns for consistency)
const WHY_KEYWORDS = "why|motivation|background|reason";

/**
 * Strip BOM (Byte Order Mark) from the beginning of a string.
 *
 * BOM (\uFEFF) can appear at the start of UTF-8 encoded content and
 * interferes with regex patterns that use ^ anchor.
 */
function stripBom(text: string): string {
  return text.startsWith("\uFEFF") ? text.slice(1) : text;
}

/**
 * Check if body contains a "Why" section.
 *
 * Recognizes:
 * - ## Why / ## Motivation / ## Background
 * - Why: / Motivation: / Background:
 * - **Why** / **Motivation** / **Background**
 */
export function hasWhySection(body: string | null): boolean {
  if (!body) {
    return false;
  }

  // Strip BOM if present (Issue #2951)
  const cleanBody = stripBom(body);

  const sectionPatterns = [
    // Match separator or end after keyword
    // \s includes \n, so no need to specify \n separately
    new RegExp(`(?:^|\\n)##?\\s*(?:${WHY_KEYWORDS})(?:$|[\\s:?])`, "i"),
    new RegExp(`\\*\\*(?:${WHY_KEYWORDS})\\*\\*`, "i"),
    new RegExp(`(?:^|\\n)(?:${WHY_KEYWORDS})\\s*[:]`, "i"),
  ];

  return sectionPatterns.some((pattern) => pattern.test(cleanBody));
}

// Reference section keywords (shared across patterns for consistency)
const REF_KEYWORDS = "refs?|references?|related";

/**
 * Check if body contains Issue/PR references.
 *
 * Recognizes:
 * - #123 (Issue/PR number)
 * - Closes #123 / Fixes #123 / Resolves #123
 * - URL links to GitHub issues/PRs
 * - Refs: / Related:
 */
export function hasReference(body: string | null): boolean {
  if (!body) {
    return false;
  }

  // Strip BOM if present (Issue #2951)
  const cleanBody = stripBom(body);

  // Issue/PR number reference
  if (/#\d+/.test(cleanBody)) {
    return true;
  }

  // GitHub URL references
  if (/github\.com\/[\w-]+\/[\w-]+\/(?:issues|pull)\/\d+/.test(cleanBody)) {
    return true;
  }

  // Reference section headers
  // Match separator or end after keyword
  // \s includes \n, so no need to specify \n separately
  const refPatterns = [
    new RegExp(`(?:^|\\n)##?\\s*(?:${REF_KEYWORDS})(?:$|[\\s:?])`, "i"),
    new RegExp(`(?:^|\\n)(?:${REF_KEYWORDS})\\s*[:]`, "i"),
  ];

  return refPatterns.some((pattern) => pattern.test(cleanBody));
}

/**
 * Check PR body quality.
 *
 * @returns Tuple of [is_valid, missing_items]
 */
export function checkBodyQuality(body: string | null): [boolean, string[]] {
  const missing: string[] = [];

  if (!hasWhySection(body)) {
    missing.push("Why section (motivation/background)");
  }

  if (!hasReference(body)) {
    missing.push("Reference (Issue number #XXX or related link)");
  }

  return [missing.length === 0, missing];
}

/**
 * Check if body contains incremental migration keywords.
 */
export function hasIncrementalKeywords(body: string | null): boolean {
  if (!body) {
    return false;
  }

  const bodyWithoutCode = stripCodeBlocks(body);
  return INCREMENTAL_KEYWORDS_PATTERN.test(bodyWithoutCode);
}

/**
 * Check if incremental PR has follow-up Issue reference.
 *
 * @returns Tuple of [is_valid, reason]
 */
export function checkIncrementalPr(body: string | null): [boolean, string | null] {
  if (!body) {
    return [true, null];
  }

  if (!hasIncrementalKeywords(body)) {
    return [true, null];
  }

  // Incremental keywords found - check for Issue reference
  if (hasReference(body)) {
    return [true, null];
  }

  return [
    false,
    "Incremental PRs require a follow-up Issue reference.\n\n" +
      "**Detected keywords**: incremental/phase/follow-up etc.\n\n" +
      "**How to fix**:\n" +
      '1. Create a follow-up Issue: `gh issue create --title "Follow-up: ..." --body "..."`\n' +
      "2. Add Issue reference to PR body: `Related: #XXX (Follow-up)`\n" +
      '3. Or update PR body with `gh pr edit <PR-number> --body "..."`',
  ];
}

/**
 * Get repository owner and name.
 *
 * When repo is provided, parse it directly. Otherwise, use gh CLI to detect.
 * Supports both "owner/repo" and "host/owner/repo" formats.
 *
 * @param repo - Repository in owner/repo format, or null for current repo
 * @returns Tuple of [owner, name], or null if failed
 */
export async function getRepoOwnerAndName(
  repo: string | null = null,
): Promise<[string, string] | null> {
  if (repo) {
    const parts = repo.split("/");
    if (parts.length >= 2) {
      const owner = parts[parts.length - 2];
      const name = parts[parts.length - 1];
      if (owner && name) {
        return [owner, name];
      }
    }
    return null;
  }

  // Use gh CLI to detect current repo
  try {
    const result = await asyncSpawn(
      "gh",
      ["repo", "view", "--json", "owner,name", "--jq", ".owner.login,.name"],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (!result.success) {
      return null;
    }

    const lines = result.stdout.trim().split("\n");
    if (lines.length >= 2) {
      return [lines[0], lines[1]];
    }

    return null;
  } catch {
    return null;
  }
}
