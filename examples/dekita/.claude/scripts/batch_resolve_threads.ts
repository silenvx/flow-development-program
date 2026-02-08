#!/usr/bin/env bun
/**
 * PRの未解決レビュースレッドを一括resolveする。
 *
 * Why:
 *   複数のレビュースレッドに同一メッセージで返信し、
 *   一括でresolveする作業を自動化するため。
 *
 * What:
 *   - listUnresolvedThreads(): 未解決スレッドを取得
 *   - postReply(): 返信してresolve
 *   - detectAiFindingSeverity(): AIレビュー指摘のレベル検出
 *   - checkActionResponse(): 対応状況の確認
 *
 * Remarks:
 *   - --dry-run で実行内容を確認可能
 *   - GitHub GraphQL APIを使用
 *   - AIレビュー指摘（LOW以上）解決時は対応状況の記載を推奨
 *
 * Changelog:
 *   - silenvx/dekita#1395: 一括resolve機能を追加
 *   - silenvx/dekita#3080: AIレビュー指摘の対応漏れ防止チェック追加
 *   - silenvx/dekita#3096: 定数をlib/constants.tsからインポートに変更
 *   - silenvx/dekita#3496: PythonからTypeScriptに移行
 *   - silenvx/dekita#3530: Codex等、Gemini以外のAIボット対応
 */

import {
  ISSUE_REFERENCE_PATTERN,
  STRICT_ISSUE_REFERENCE_PATTERN,
  getRepoOwnerAndName,
  stripCodeBlocks,
} from "../hooks/lib/check_utils";
import {
  CODEX_BOT_USER,
  CODEX_PRIORITY_BADGES,
  GEMINI_BOT_USER,
  GEMINI_PRIORITY_BADGES,
  GEMINI_SECURITY_BADGES,
  TIMEOUT_HEAVY,
  hasActionKeyword,
} from "../hooks/lib/constants";
import { logHookExecutionAsync } from "../hooks/lib/execution";
import { asyncSpawn } from "../hooks/lib/spawn";

// =============================================================================
// Types
// =============================================================================

interface ReviewThread {
  id: string;
  isResolved: boolean;
  path: string;
  line: number | null;
  comments: {
    nodes: Array<{
      id: string;
      databaseId: number;
      body: string;
      author: { login: string } | null;
    }>;
  };
}

interface GraphQLError {
  message: string;
  type?: string;
  path?: (string | number)[];
}

interface GraphQLResponse {
  data?: {
    repository?: {
      pullRequest?: {
        reviewThreads?: {
          pageInfo: {
            hasNextPage: boolean;
            endCursor: string | null;
          };
          nodes: ReviewThread[];
        };
      };
    };
  };
  errors?: GraphQLError[];
}

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Detect AI review finding severity from comment body.
 *
 * Issue #3080: AIレビュー指摘のレベルを検出する。
 * Issue #3530: Gemini以外のAIボット（Codex等）にも対応。
 *
 * @returns severity string or null if not an AI review finding
 */
export function detectAiFindingSeverity(body: string, author: string): string | null {
  const authorLower = author.toLowerCase();

  // Gemini Code Assist
  if (authorLower === GEMINI_BOT_USER.toLowerCase()) {
    // Strip code blocks to avoid false positives from badge examples in code
    const bodyStripped = stripCodeBlocks(body);
    // Check security badges first (higher priority)
    for (const [severity, pattern] of Object.entries(GEMINI_SECURITY_BADGES)) {
      if (pattern.test(bodyStripped)) {
        return severity;
      }
    }
    // Check priority badges
    for (const [severity, pattern] of Object.entries(GEMINI_PRIORITY_BADGES)) {
      if (pattern.test(bodyStripped)) {
        return severity;
      }
    }
    return null;
  }

  // Codex (chatgpt-codex-connector[bot])
  if (authorLower === CODEX_BOT_USER.toLowerCase()) {
    // Strip code blocks to avoid false positives from badge examples in code
    const bodyStripped = stripCodeBlocks(body);
    for (const [severity, pattern] of Object.entries(CODEX_PRIORITY_BADGES)) {
      if (pattern.test(bodyStripped)) {
        return severity;
      }
    }
    return null;
  }

  // Other AI bots (Copilot, etc.) - no severity detection
  // Note: Copilot doesn't use badges, so we don't block its comments
  return null;
}

/**
 * Check if the response message indicates proper action.
 *
 * Issue #3080: 返信メッセージに適切な対応が記載されているか確認する。
 * Issue #3096: hasActionKeyword()を使用し、verified:/unverified:の誤検知を防止。
 *
 * @returns Tuple of [hasActionKeyword, hasIssueReference, hasStrictIssueReference]
 */
function checkActionResponse(
  message: string,
): [hasAction: boolean, hasIssue: boolean, hasStrictIssue: boolean] {
  const hasAction = hasActionKeyword(message);
  const hasIssue = ISSUE_REFERENCE_PATTERN.test(message);
  const hasStrictIssue = STRICT_ISSUE_REFERENCE_PATTERN.test(message);

  return [hasAction, hasIssue, hasStrictIssue];
}

/**
 * List all unresolved review threads with comment details.
 *
 * Uses pagination to fetch all threads (up to 50 per page).
 */
async function listUnresolvedThreads(
  prNumber: string,
  owner: string,
  repo: string,
): Promise<ReviewThread[]> {
  const allThreads: ReviewThread[] = [];
  let hasNextPage = true;
  let cursor: string | null = null;

  // GraphQL query with pagination support
  const query = `
    query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $pr) {
          reviewThreads(first: 50, after: $cursor) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              id
              isResolved
              path
              line
              comments(first: 1) {
                nodes {
                  id
                  databaseId
                  body
                  author { login }
                }
              }
            }
          }
        }
      }
    }
  `;

  while (hasNextPage) {
    // Note: Use -F for typed fields (pr as Int), -f for literal strings (owner, repo)
    // -F parses numeric strings as numbers, which is required for GraphQL Int! type.
    const args = [
      "api",
      "graphql",
      "-f",
      `query=${query}`,
      "-f",
      `owner=${owner}`,
      "-f",
      `repo=${repo}`,
      "-F",
      `pr=${prNumber}`,
    ];

    if (cursor) {
      args.push("-f", `cursor=${cursor}`);
    }

    const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_HEAVY * 1000 });

    if (!result.success) {
      throw new Error(`Failed to list review threads: ${result.stderr}`);
    }

    // Issue #3534: Parse JSON separately to avoid brittle error message matching
    let data: GraphQLResponse;
    try {
      data = JSON.parse(result.stdout);
    } catch (e) {
      throw new Error(`Failed to parse review threads response: ${(e as Error).message}`);
    }

    // Issue #3534: Check for GraphQL errors
    if (data.errors && data.errors.length > 0) {
      const errorMessages = data.errors.map((e) => e.message).join("; ");
      throw new Error(`GraphQL API error: ${errorMessages}`);
    }

    // Issue #3534: Check if repository/pullRequest exists (invalid PR number or permission issue)
    const repository = data.data?.repository;
    if (!repository) {
      throw new Error(`Repository not found or access denied: ${owner}/${repo}`);
    }
    if (!repository.pullRequest) {
      throw new Error(`Pull request #${prNumber} not found in ${owner}/${repo}`);
    }

    const reviewThreads = repository.pullRequest.reviewThreads;

    // Issue #3534: Warn if reviewThreads is undefined (unexpected API response)
    if (!reviewThreads) {
      const logEntry = {
        level: "warn",
        script: "batch_resolve_threads",
        message: "reviewThreads is undefined in API response",
        timestamp: new Date().toISOString(),
        owner,
        repo,
        prNumber,
      };
      console.warn(JSON.stringify(logEntry));
      return allThreads.filter((t) => !t.isResolved);
    }

    allThreads.push(...reviewThreads.nodes);

    hasNextPage = reviewThreads.pageInfo.hasNextPage;
    cursor = reviewThreads.pageInfo.endCursor;
  }

  return allThreads.filter((t) => !t.isResolved);
}

/**
 * Post a reply to a review comment.
 */
async function postReply(
  prNumber: string,
  commentId: number,
  message: string,
  owner: string,
  repo: string,
): Promise<boolean> {
  // Add signature if not present
  // Note: Use regex to check if signature is at the end of message (with optional trailing whitespace)
  let finalMessage = message;
  if (!/-- Claude Code\s*$/.test(message)) {
    // Avoid excessive blank lines if message already ends with newline
    const separator = message.endsWith("\n") ? "\n" : "\n\n";
    finalMessage = `${message.trimEnd()}${separator}-- Claude Code`;
  }

  // Note: gh api -f treats values starting with '@' as file paths.
  // Prefix with space to ensure it's treated as literal string.
  const safeBody = finalMessage.startsWith("@") ? ` ${finalMessage}` : finalMessage;

  const result = await asyncSpawn(
    "gh",
    [
      "api",
      `/repos/${owner}/${repo}/pulls/${prNumber}/comments/${commentId}/replies`,
      "-X",
      "POST",
      "-f",
      `body=${safeBody}`,
    ],
    { timeout: TIMEOUT_HEAVY * 1000 },
  );

  if (!result.success) {
    console.error("  Error posting reply:", result.stderr);
    return false;
  }

  return true;
}

/**
 * Resolve a review thread.
 */
async function resolveThread(threadId: string): Promise<boolean> {
  const query = `
    mutation($threadId: ID!) {
      resolveReviewThread(input: {threadId: $threadId}) {
        thread { isResolved }
      }
    }
  `;

  const result = await asyncSpawn(
    "gh",
    ["api", "graphql", "-f", `query=${query}`, "-f", `threadId=${threadId}`],
    { timeout: TIMEOUT_HEAVY * 1000 },
  );

  if (!result.success) {
    console.error("  Error resolving thread:", result.stderr);
    return false;
  }

  try {
    const data = JSON.parse(result.stdout);
    return data.data?.resolveReviewThread?.thread?.isResolved ?? false;
  } catch (e) {
    console.error("  Error resolving thread:", e);
    return false;
  }
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const args = process.argv.slice(2);

  if (args.length < 1) {
    console.log("Usage:");
    console.log('  batch_resolve_threads.ts <pr_number> "<message>"');
    console.log("  batch_resolve_threads.ts <pr_number> --dry-run");
    console.log();
    console.log("Examples:");
    console.log('  batch_resolve_threads.ts 1395 "修正しました。Verified: 全指摘に対応"');
    console.log("  batch_resolve_threads.ts 1395 --dry-run");
    process.exit(1);
  }

  const prNumber = args[0];

  // Validate PR number is a positive integer
  const prInt = Number.parseInt(prNumber, 10);
  if (Number.isNaN(prInt) || prInt <= 0) {
    console.error(`Error: Invalid PR number '${prNumber}'`);
    process.exit(1);
  }

  // Issue #3080: Robust argument parsing - --dry-run can appear anywhere after PR number
  const dryRun = args.slice(1).includes("--dry-run");
  // Join all non-flag arguments as message (handles unquoted multi-word input)
  const messageArgs = args.slice(1).filter((arg) => arg !== "--dry-run");
  const message = messageArgs.length > 0 ? messageArgs.join(" ") : null;

  if (!dryRun && !message) {
    console.error("Error: Message is required unless using --dry-run");
    process.exit(1);
  }

  // Get repo info
  const repoInfo = await getRepoOwnerAndName();
  if (!repoInfo) {
    console.error("Error: Could not determine repository info");
    process.exit(1);
  }
  const [owner, repo] = repoInfo;

  console.log(`Fetching unresolved threads for PR #${prNumber}...`);
  const threads = await listUnresolvedThreads(prNumber, owner, repo);

  let successCount = 0;
  let failCount = 0;
  let aiFindingBlocked = 0; // Issue #3080: Track AI findings blocked due to no action

  if (threads.length === 0) {
    console.log("No unresolved threads found.");
  } else {
    console.log(`Found ${threads.length} unresolved thread(s):\n`);

    for (let i = 0; i < threads.length; i++) {
      const t = threads[i];
      const threadId = t.id;
      const path = t.path ?? "unknown";
      const line = t.line ?? "?";
      const comments = t.comments?.nodes ?? [];

      if (comments.length === 0) {
        console.log(`[${i + 1}/${threads.length}] Skipping thread with no comments: ${threadId}`);
        continue;
      }

      const comment = comments[0];
      const commentId = comment.databaseId;
      const author = comment.author?.login ?? "unknown";
      const body = comment.body ?? "";
      const bodyPreview = body.replace(/\n/g, " ").trim().slice(0, 60);

      console.log(`[${i + 1}/${threads.length}] ${path}:${line} (${author})`);
      console.log(`  Preview: ${bodyPreview}...`);
      console.log(`  Thread ID: ${threadId}`);
      console.log(`  Comment ID: ${commentId}`);

      // Issue #3080: Check if this is an AI review finding
      const severity = detectAiFindingSeverity(body, author);
      if (severity) {
        const isSecurity = severity.startsWith("security-");
        console.log(`  ⚠️  AI Finding Detected: ${severity.toUpperCase()}`);

        // Check if response message has proper action
        if (message) {
          const [hasAction, hasIssue, hasStrictIssue] = checkActionResponse(message);

          // Security findings strictly require an actual Issue reference (#xxx)
          // "issue作成します" promise is NOT sufficient for security findings
          if (isSecurity && !hasStrictIssue) {
            console.log(
              `  ❌ Error: Security finding (${severity.toUpperCase()}) requires Issue reference`,
            );
            console.log("     Security findings MUST be tracked in an Issue (#xxx)");
            console.log("  → Create an Issue first, then include #xxx in your response");
            aiFindingBlocked++;
            failCount++;
            continue;
          }

          // Regular findings need either action or issue
          if (!isSecurity && !hasAction && !hasIssue) {
            console.log(`  ⚠️  Blocked: Resolving ${severity.toUpperCase()} finding without action`);
            console.log("     Response should include:");
            console.log("     - Action keyword (修正しました, 対応しました, etc.)");
            console.log("     - Or Issue reference (#xxx)");
            console.log(
              "  → Consider: コード修正して「修正しました」、または Issue化して「#xxx で対応予定」",
            );
            // Block resolution of AI findings without proper action
            aiFindingBlocked++;
            failCount++;
            continue;
          }

          if (hasAction) {
            console.log("  ✓ Response includes action keyword");
          }
          if (hasIssue) {
            console.log("  ✓ Response includes Issue reference");
          }
        } else {
          // Issue #3080: dry-runでメッセージなしの場合もAI指摘を警告
          // これにより、ユーザーは事前にAI指摘の存在を確認できる
          console.log(
            `  ⚠️  Info: ${severity.toUpperCase()} finding - requires message with action keyword`,
          );
          console.log("     For actual run, message with action keyword or Issue ref is needed");
          // Don't count as failure in dry-run mode (informational only)
          if (!dryRun) {
            aiFindingBlocked++;
            failCount++;
          }
          continue;
        }
      }

      if (dryRun) {
        if (message) {
          console.log("  [DRY RUN] Would post reply and resolve");
        } else {
          console.log("  [DRY RUN] Thread found (no message provided)");
        }
        successCount++;
        continue;
      }

      // Post reply
      console.log("  Posting reply...");
      if (!(await postReply(prNumber, commentId, message!, owner, repo))) {
        console.log("  ❌ Failed to post reply");
        failCount++;
        continue;
      }

      // Resolve thread
      console.log("  Resolving thread...");
      if (await resolveThread(threadId)) {
        console.log("  ✅ Done");
        successCount++;
      } else {
        console.log("  ❌ Failed to resolve");
        failCount++;
      }

      console.log();
    }
  } // end of threads.length > 0

  // Summary
  console.log("=".repeat(50));
  if (dryRun) {
    console.log(`[DRY RUN] Would process ${successCount} thread(s)`);
  } else {
    const status = failCount === 0 ? "✅ All succeeded" : "⚠️ Completed with failures";
    console.log(`${status}: ${successCount} resolved, ${failCount} failed`);
  }

  // Issue #3080: Show blocked info in both dry-run and actual mode
  // This allows users to know in advance if their message would cause blocks
  if (failCount > 0) {
    const prefix = dryRun ? "[DRY RUN] Would fail" : "❌ Failed";
    console.log(`${prefix}: ${failCount} thread(s)`);
  }
  if (aiFindingBlocked > 0) {
    const blockedPrefix = dryRun ? "would be blocked" : "blocked";
    console.log(`⚠️  AI findings ${blockedPrefix} (no action): ${aiFindingBlocked}`);
    console.log("   → Resolve with: コード修正 or Issue参照 (#xxx)");
    console.log("   → Re-run after adding action to message");
  }

  // Issue #1419: Log batch resolve execution to hook-execution.log
  await logHookExecutionAsync(
    "batch-resolve-threads",
    "approve",
    !dryRun ? `Batch resolved ${successCount} thread(s)` : "Dry run",
    {
      pr_number: prNumber,
      total_threads: threads.length,
      resolved_count: successCount,
      failed_count: failCount,
      ai_finding_blocked: aiFindingBlocked, // Issue #3080
      dry_run: dryRun,
    },
  );

  // Exit with non-zero code if any failures occurred
  if (failCount > 0) {
    process.exit(1);
  }
}

// Only run main() when executed directly, not when imported for testing
if (import.meta.main) {
  main().catch((e) => {
    console.error("Fatal error:", e);
    process.exit(1);
  });
}
