/**
 * 修正主張と検証チェックの関数群（merge-check用モジュール）。
 *
 * Why:
 *   Claude Codeが「Fixed:」「修正済み」と主張しても、実際にコードが
 *   修正されているとは限らない。検証パターンの存在を確認することで、
 *   未検証の修正主張がマージされることを防ぐ。
 *
 * What:
 *   - 修正主張キーワード検出（Fixed:, Added, 修正済み等）
 *   - 検証パターン検出（Verified:, 検証済み等）
 *   - 数値主張の検証（AIレビュアーの数値誤りを検出）
 *   - 具体的な修正箇所参照（ファイル:行番号）の検出
 *
 * Remarks:
 *   - review_checker.tsは却下・応答チェック担当
 *   - ai_review_checker.tsはAIレビュアーステータスチェック担当
 *   - 本モジュールは修正主張と検証に特化
 *
 * Changelog:
 *   - silenvx/dekita#462: FixClaimKeyword構造体でキーワード管理
 *   - silenvx/dekita#856: 具体的なファイル参照を「自己検証」として認識
 *   - silenvx/dekita#858: 数値主張の検証チェック追加
 *   - silenvx/dekita#1679: Issue参照を有効な応答として認識
 *   - silenvx/dekita#3159: TypeScriptに移植
 */

import { getRepoOwnerAndName, truncateBody } from "./check_utils";
import { TIMEOUT_HEAVY } from "./constants";
import { addRepoFlag } from "./github";
import { asyncSpawn } from "./spawn";

/**
 * Structured fix claim keyword with display metadata.
 *
 * Design note: These keywords are checked ONLY in comments with "-- Claude Code" signature.
 * This significantly reduces false positives from broad keywords like "added " or "updated ".
 */
export interface FixClaimKeyword {
  /** The text to match in comment body (case-insensitive) */
  pattern: string;
  /** Human-readable name shown in error messages */
  displayName: string;
  /** Character to strip from pattern when extracting display name (':' or ' ') */
  trailingChar: string;
}

/**
 * Fix claim keywords list.
 *
 * Uses a structured FixClaimKeyword for keyword management (Issue #462):
 * - pattern: The text to match in comment body (case-insensitive)
 * - displayName: Human-readable name shown in error messages
 * - trailingChar: Character to strip from pattern when extracting display name
 */
export const FIX_CLAIM_KEYWORDS: readonly FixClaimKeyword[] = [
  { pattern: "fixed:", displayName: "Fixed", trailingChar: ":" },
  { pattern: "already addressed:", displayName: "Already addressed", trailingChar: ":" },
  { pattern: "added ", displayName: "Added", trailingChar: " " },
  { pattern: "updated ", displayName: "Updated", trailingChar: " " },
  { pattern: "changed ", displayName: "Changed", trailingChar: " " },
  { pattern: "implemented ", displayName: "Implemented", trailingChar: " " },
  { pattern: "修正済み", displayName: "修正済み", trailingChar: "" },
  { pattern: "対応済み", displayName: "対応済み", trailingChar: "" },
] as const;

/**
 * Pattern that matches "verified:" anywhere.
 *
 * Design note (Issue #462): Instead of negative lookbehinds (which have fixed-width
 * limitations), we use two patterns:
 * - VERIFICATION_POSITIVE_PATTERN: Matches "verified:" anywhere
 * - VERIFICATION_NEGATION_PATTERN: Matches negated forms
 */
const VERIFICATION_POSITIVE_PATTERN = /\bverified:/gi;

/**
 * Pattern that matches negated forms of verification.
 *
 * Matches: "not verified:", "un verified:", "haven't verified:", "could not verify:", etc.
 *
 * Note: Word boundary \b is added before negation words to avoid false positives
 * like "run verified:" matching "un\s*verified:".
 */
const VERIFICATION_NEGATION_PATTERN =
  /\b(?:not|un|never|haven't|couldn't|could not|did not|didn't|won't|will not|cannot|can't)\s*verified:/gi;

/**
 * Pattern to detect AI review comments containing numeric claims (Issue #858).
 *
 * Design note: AI reviewers (Copilot/Codex) sometimes make incorrect claims about
 * numbers (e.g., "should be 33 characters" when it's actually 32).
 *
 * Matches:
 * - "should be 10", "は10", "を10に"
 * - "10文字", "10 characters" - but NOT line references like "10行目", "10行付近", "10行周辺", "10行番号"
 *
 * Issue #3211: Added "周辺" and "番号" to negative lookahead to reduce false positives
 */
const NUMERIC_CLAIM_PATTERN =
  /(?:should be|は|を|から)\s*\d+|\d+\s*(?:文字|行(?!目|付近|周辺|番号)|個|件|要素|bytes?|characters?|lines?|items?)/gi;

/**
 * Pattern to detect verification of numeric claims (Issue #858).
 *
 * When responding to numeric claims, include verification like:
 * "検証済み: 実際は32文字" or "Verified: counted 32 characters"
 *
 * Issue #1679: Also recognize Issue references as valid responses.
 * Issue #1738, #1744: Add negative lookbehinds to prevent negated patterns
 * Issue #1735: Support "recorded in issue #123" variation
 */
const NUMERIC_VERIFICATION_PATTERN =
  /検証済み:|verified:|確認済み:|counted\s*\d|実際[はに]\d+|actually\s*\d+|issue\s*#\d+\s*(?:に記録|として追跡|for\s*follow-?up)|#\d+\s*(?:に記録|として追跡)|(?<!not )(?<!never )\brecorded\s*in\s*(?:issue\s*)?#\d+/gi;

// Known source file extensions for file reference detection (Issue #856, #887)
const SOURCE_FILE_EXTENSIONS =
  // Scripting / interpreted
  "py|rb|pl|php|lua|r|R" +
  "|" +
  // JavaScript / TypeScript ecosystem
  "js|jsx|ts|tsx|mjs|cjs|vue|svelte" +
  "|" +
  // Compiled languages
  "go|rs|c|h|cpp|hpp|cc|cxx|java|kt|kts|scala|cs|fs|swift|m|mm" +
  "|" +
  // Web / markup / styling
  "html|htm|css|scss|sass|less" +
  "|" +
  // Data / config
  "json|yml|yaml|xml|toml|ini|cfg|env|properties" +
  "|" +
  // Database
  "sql" +
  "|" +
  // Documentation / text
  "md|mdx|txt|rst" +
  "|" +
  // Shell / scripts
  "sh|bash|zsh|fish|ps1|bat|cmd" +
  "|" +
  // Build / package management
  "gradle|gemspec|bazel|bzl|cmake|make|ninja|sbt|pom" +
  "|" +
  // Lock / dependency files
  "lock" +
  "|" +
  // Other
  "graphql|proto|tf|hcl";

// Special build/config files without extensions (e.g., Makefile, Dockerfile)
const SPECIAL_BUILD_FILES = "Makefile|Dockerfile|Jenkinsfile|Vagrantfile|Gemfile|Rakefile";

/**
 * Pattern to detect specific file path references (Issue #856, #887).
 *
 * Matches: file.py:10, src/utils.ts:25-30, common.py, ./path/to/file.tsx, Makefile:5
 *
 * Issue #887: Requires known source file extensions to avoid false positives
 * from URLs (example.com:8080), IPs (192.168.1.1:8080), and versions (v1.2.3:4567).
 *
 * Note: JavaScript regex doesn't support lookbehind in older environments,
 * but modern Bun supports it.
 */
const SPECIFIC_FILE_REFERENCE_PATTERN = new RegExp(
  `(?:(?<![a-zA-Z0-9/.])[a-zA-Z0-9_.\\-][a-zA-Z0-9_\\-./]*\\.(?:${SOURCE_FILE_EXTENSIONS}):\\d+(?:-\\d+)?|(?<![a-zA-Z0-9:/])/[a-zA-Z0-9_\\-./]+\\.(?:${SOURCE_FILE_EXTENSIONS}):\\d+(?:-\\d+)?|(?<![a-zA-Z0-9/.])[a-zA-Z0-9_\\-]+\\.(?:${SOURCE_FILE_EXTENSIONS})\\b|\\b(?:${SPECIAL_BUILD_FILES})(?::\\d+(?:-\\d+)?)?\\b)`,
  "i",
);

/**
 * Pattern to detect commit hash references (Issue #856).
 *
 * Matches: "in abc1234", "commit abc1234def", "Fixed in abc1234def5678"
 *
 * IMPORTANT: Requires "in " or "commit " prefix with word boundary to avoid
 * false positives (e.g., "resubmit abc1234" should not match)
 */
const COMMIT_HASH_REFERENCE_PATTERN = /(?:\bin\s+|\bcommit\s+)[0-9a-f]{7,40}\b/i;

/**
 * Pattern to detect explicit "not verified/verify" statements (Issue #856).
 *
 * Matches: "not verified", "unverified", "haven't verified", "not yet verified",
 *          "couldn't verify", "didn't verify locally", etc.
 *
 * Used to override self-verification from specific fix claims.
 * The pattern allows up to 2 intermediate words like "yet", "fully", "actually".
 */
const EXPLICIT_NOT_VERIFIED_PATTERN =
  /\b(?:unverified|(?:not|never|haven't|hasn't|couldn't|could not|did not|didn't|won't|will not|cannot|can't)(?:\s+\w+){0,2}\s+verif(?:y|ied))\b/i;

/**
 * Check if text contains at least one valid (non-negated) verification.
 *
 * This function finds all "verified:" occurrences and checks each one to see
 * if it's preceded by a negation word. Returns True if at least one occurrence
 * is NOT negated.
 *
 * @param text - The comment text to check
 * @returns True if there's at least one valid verification, False otherwise
 */
export function hasValidVerification(text: string): boolean {
  // Find all positive matches
  const positiveMatches: RegExpExecArray[] = [];
  VERIFICATION_POSITIVE_PATTERN.lastIndex = 0;
  let match: RegExpExecArray | null = VERIFICATION_POSITIVE_PATTERN.exec(text);
  while (match !== null) {
    positiveMatches.push(match);
    match = VERIFICATION_POSITIVE_PATTERN.exec(text);
  }

  if (positiveMatches.length === 0) {
    return false;
  }

  // Find all negation matches
  const negationMatches: RegExpExecArray[] = [];
  VERIFICATION_NEGATION_PATTERN.lastIndex = 0;
  match = VERIFICATION_NEGATION_PATTERN.exec(text);
  while (match !== null) {
    negationMatches.push(match);
    match = VERIFICATION_NEGATION_PATTERN.exec(text);
  }

  // Check if any positive match is NOT at a negated position
  for (const posMatch of positiveMatches) {
    const posStart = posMatch.index;
    // Check if this position is NOT part of a negated match
    let isNegated = false;
    for (const negMatch of negationMatches) {
      // The "verified:" in the negation match is at the end
      const negVerifiedPos = negMatch.index + negMatch[0].length - "verified:".length;
      if (posStart === negVerifiedPos) {
        isNegated = true;
        break;
      }
    }
    if (!isNegated) {
      return true;
    }
  }

  return false;
}

/**
 * Check if a fix claim comment contains specific evidence.
 *
 * Issue #856: When a fix claim includes specific file paths or commit hashes,
 * it's considered "self-verifying" because the reviewer can easily verify
 * the claim by checking the referenced location.
 *
 * @param text - The comment text to check
 * @returns True if the comment contains specific file references or commit hashes
 */
export function isSpecificFixClaim(text: string): boolean {
  // Check for file path references
  if (SPECIFIC_FILE_REFERENCE_PATTERN.test(text)) {
    return true;
  }

  // Check for commit hash references
  if (COMMIT_HASH_REFERENCE_PATTERN.test(text)) {
    return true;
  }

  return false;
}

/** Result from check_resolved_without_verification */
export interface ResolvedWithoutVerification {
  threadId: string;
  author: string;
  fixClaim: string;
  /** The keyword pattern that matched (e.g., "added ", "updated ") for debugging */
  matchedPattern: string;
  body: string;
}

/**
 * Check if resolved threads have fix claims without verification.
 *
 * When a Claude Code comment claims a fix (e.g., "Fixed:", "Already addressed:"),
 * there should be a corresponding "Verified:" comment to confirm the fix was actually applied.
 *
 * GraphQL Limitations (Issue #561, Issue #1215):
 *   - reviewThreads(first: 50): Only the first 50 threads per PR are checked.
 *   - comments(last: 100): Only the last 100 comments per thread are checked.
 *
 * @param prNumber - The PR number to check
 * @param repo - Repository in owner/repo format, or null for current repo
 * @returns List of threads with unverified fix claims
 */
export async function checkResolvedWithoutVerification(
  prNumber: string,
  repo: string | null = null,
): Promise<ResolvedWithoutVerification[]> {
  try {
    const repoInfo = await getRepoOwnerAndName(repo);
    if (!repoInfo) {
      return [];
    }

    const [owner, name] = repoInfo;

    // GraphQL query - see docstring for limitations (Issue #561, Issue #1215)
    const query = `
      query($owner: String!, $name: String!, $pr: Int!) {
        repository(owner: $owner, name: $name) {
          pullRequest(number: $pr) {
            reviewThreads(first: 50) {
              nodes {
                id
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
                  }
                }
              }
            }
          }
        }
      }
    `;

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
    const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_HEAVY * 1000 });

    // Issue #1026: Check for empty stdout before JSON parsing
    if (!result.success || !result.stdout.trim()) {
      return [];
    }

    const data = JSON.parse(result.stdout);
    const threads = data?.data?.repository?.pullRequest?.reviewThreads?.nodes ?? [];

    const threadsWithoutVerification: ResolvedWithoutVerification[] = [];

    for (const thread of threads) {
      if (!thread?.isResolved) {
        continue;
      }

      // Get the first comment (original AI review comment) for author identification
      const firstComments = thread?.firstComment?.nodes ?? [];
      if (firstComments.length === 0) {
        continue;
      }

      const firstComment = firstComments[0];
      const firstBody = firstComment?.body ?? "";
      const author = firstComment?.author?.login ?? "unknown";

      // Skip threads not started by AI reviewer
      const authorLower = author.toLowerCase();
      if (!authorLower.includes("copilot") && !authorLower.includes("codex")) {
        continue;
      }

      // Check for fix claims and verifications in recent comments
      // Only check Claude Code comments (identified by signature) for fix claims
      const recentComments = thread?.recentComments?.nodes ?? [];
      let hasFixClaim = false;
      let fixClaimText = "";
      let matchedPattern = "";
      let hasVerification = false;

      for (const comment of recentComments) {
        const body = comment?.body ?? "";
        const bodyLower = body.toLowerCase();

        // Only check comments with Claude Code signature for fix claims
        if (body.includes("-- Claude Code")) {
          // Check for fix claim keywords using structured FixClaimKeyword
          for (const keyword of FIX_CLAIM_KEYWORDS) {
            if (bodyLower.includes(keyword.pattern.toLowerCase())) {
              hasFixClaim = true;
              // Use displayName directly (Issue #462: rstrip improvement)
              if (!fixClaimText) {
                fixClaimText = keyword.displayName;
                // Issue #3679: Record the actual pattern for debugging
                matchedPattern = keyword.pattern;
              }
              // Issue #856: Check if THIS fix claim comment has specific
              // evidence (file path or commit hash). Only the comment
              // containing the fix claim is checked, not later comments.
              // But don't count as verified if the comment explicitly
              // states it's not verified (e.g., "Not verified yet").
              if (isSpecificFixClaim(body)) {
                if (!EXPLICIT_NOT_VERIFIED_PATTERN.test(body)) {
                  hasVerification = true;
                }
              }
              break;
            }
          }

          // Check for verification pattern (Issue #462: improved negation handling)
          if (hasValidVerification(body)) {
            hasVerification = true;
          }
        }
      }

      // If there's a fix claim but no verification, flag it
      if (hasFixClaim && !hasVerification) {
        threadsWithoutVerification.push({
          threadId: thread?.id ?? "unknown",
          author,
          fixClaim: fixClaimText,
          matchedPattern,
          body: truncateBody(firstBody),
        });
      }
    }

    return threadsWithoutVerification;
  } catch {
    // On error, don't block (fail open)
    return [];
  }
}

/** Result from check_numeric_claims_verified */
export interface NumericClaimWithoutVerification {
  threadId: string;
  author: string;
  body: string;
}

/**
 * Check if AI review comments with numeric claims have verification.
 *
 * When an AI reviewer (Copilot/Codex) makes a claim involving numbers
 * (e.g., "should be 33 characters"), Claude Code's response should include
 * verification that the number was actually confirmed.
 *
 * Background (Issue #858): In PR #851, Copilot claimed "33 characters" but it was
 * actually 32. Blindly trusting the AI led to test failures.
 *
 * GraphQL Limitations (Issue #561, Issue #1215):
 *   - reviewThreads(first: 50): Only the first 50 threads per PR are checked.
 *   - comments(last: 100): Only the last 100 comments per thread are checked.
 *
 * @param prNumber - The PR number to check
 * @param repo - Repository in owner/repo format, or null for current repo
 * @returns List of threads with numeric claims lacking verification
 */
export async function checkNumericClaimsVerified(
  prNumber: string,
  repo: string | null = null,
): Promise<NumericClaimWithoutVerification[]> {
  try {
    const repoInfo = await getRepoOwnerAndName(repo);
    if (!repoInfo) {
      return [];
    }

    const [owner, name] = repoInfo;

    const query = `
      query($owner: String!, $name: String!, $pr: Int!) {
        repository(owner: $owner, name: $name) {
          pullRequest(number: $pr) {
            reviewThreads(first: 50) {
              nodes {
                id
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
                  }
                }
              }
            }
          }
        }
      }
    `;

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
    const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_HEAVY * 1000 });

    // Issue #1026: Check for empty stdout before JSON parsing
    if (!result.success || !result.stdout.trim()) {
      return [];
    }

    const data = JSON.parse(result.stdout);
    const threads = data?.data?.repository?.pullRequest?.reviewThreads?.nodes ?? [];

    const threadsWithoutVerification: NumericClaimWithoutVerification[] = [];

    for (const thread of threads) {
      if (!thread?.isResolved) {
        continue;
      }

      const firstComments = thread?.firstComment?.nodes ?? [];
      if (firstComments.length === 0) {
        continue;
      }

      const firstComment = firstComments[0];
      const firstBody = firstComment?.body ?? "";
      const author = firstComment?.author?.login ?? "unknown";

      // Only check threads started by AI reviewers (Copilot/Codex)
      const authorLower = author.toLowerCase();
      if (!authorLower.includes("copilot") && !authorLower.includes("codex")) {
        continue;
      }

      // Check if the AI comment contains numeric claims
      // Reset lastIndex since pattern is global
      NUMERIC_CLAIM_PATTERN.lastIndex = 0;
      if (!NUMERIC_CLAIM_PATTERN.test(firstBody)) {
        continue;
      }

      // Check if any Claude Code response has numeric verification
      const recentComments = thread?.recentComments?.nodes ?? [];
      let hasVerification = false;

      for (const comment of recentComments) {
        const body = comment?.body ?? "";
        if (!body.includes("-- Claude Code")) {
          continue;
        }
        NUMERIC_VERIFICATION_PATTERN.lastIndex = 0;
        if (NUMERIC_VERIFICATION_PATTERN.test(body)) {
          hasVerification = true;
          break;
        }
      }

      if (!hasVerification) {
        threadsWithoutVerification.push({
          threadId: thread?.id ?? "unknown",
          author,
          body: truncateBody(firstBody),
        });
      }
    }

    return threadsWithoutVerification;
  } catch {
    // On error, don't block (fail open)
    return [];
  }
}
