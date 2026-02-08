#!/usr/bin/env bun
/**
 * レビューコメントに返信してスレッドをresolveする。
 *
 * Why:
 *   レビュー対応を効率化し、返信・resolve・品質記録を
 *   一括で行うため。
 *
 * What:
 *   - postReply(): コメントに返信
 *   - resolveThread(): スレッドをresolve
 *   - recordResponse(): 対応を品質ログに記録
 *
 * Remarks:
 *   - --verified で修正と検証を1メッセージに統合
 *   - --resolution でaccepted/rejected/issue_createdを指定
 *   - GitHub GraphQL APIを使用
 *
 * Changelog:
 *   - silenvx/dekita#610: レビュー返信・resolve機能を追加
 *   - silenvx/dekita#3618: TypeScript版に移行
 *   - silenvx/dekita#3629: worktreeでログパスがmainリポジトリに解決されるよう修正
 *   - silenvx/dekita#3630: 未知のCLIオプションをエラーにする
 *   - silenvx/dekita#3416: 部分成功時のエラー報告改善
 */

import { $ } from "bun";
import { EXECUTION_LOG_DIR } from "../hooks/lib/common";
import { logToSessionFile } from "../hooks/lib/logging";
import { recordResponse } from "./record_review_response";

// =============================================================================
// Types
// =============================================================================

type Resolution = "accepted" | "rejected" | "issue_created";
type Validity = "valid" | "invalid" | "partially_valid";
type Category =
  | "bug"
  | "style"
  | "performance"
  | "security"
  | "test"
  | "docs"
  | "refactor"
  | "other";

interface ThreadInfo {
  id: string;
  isResolved: boolean;
  comments: {
    nodes: Array<{
      id: string;
      databaseId: number;
      body: string;
      author: { login: string };
    }>;
  };
}

/**
 * Tracks the result of each step in the review response process.
 * Issue #3416: Enable differentiation between partial success and complete failure.
 */
interface ResultTracker {
  reply: { success: boolean; id?: number; error?: string };
  resolve: { success: boolean; error?: string };
  record: { success: boolean; error?: string };
}

// =============================================================================
// Session ID
// =============================================================================

/**
 * Get session ID using PPID fallback.
 */
function getSessionIdFallback(): string {
  return `ppid-${process.ppid}`;
}

// =============================================================================
// Repository Info
// =============================================================================

/**
 * Get repository owner and name from git remote.
 */
async function getRepoInfo(): Promise<[string, string]> {
  try {
    const result = await $`gh repo view --json owner,name`.quiet();
    const data = JSON.parse(result.stdout.toString());
    return [data.owner?.login ?? "", data.name ?? ""];
  } catch {
    return ["", ""];
  }
}

// =============================================================================
// Reply Functions
// =============================================================================

/**
 * Post a reply to a review comment.
 *
 * Uses the /replies endpoint which correctly creates inline thread replies.
 * The in_reply_to parameter on the /comments endpoint does not work as expected.
 * See: Issue #748
 *
 * @returns The created reply comment ID on success, null on failure.
 */
async function postReply(
  prNumber: string,
  commentId: string,
  message: string,
  owner: string,
  repo: string,
): Promise<number | null> {
  // Add signature if not present at the end
  let finalMessage = message;
  if (!message.trimEnd().endsWith("-- Claude Code")) {
    finalMessage = `${message}\n\n-- Claude Code`;
  }

  // Escape @ prefix to prevent gh api -f from interpreting as file path
  if (finalMessage.startsWith("@")) {
    finalMessage = ` ${finalMessage}`;
  }

  try {
    // Use /replies endpoint for proper inline thread replies (Issue #748, #754)
    const result =
      await $`gh api /repos/${owner}/${repo}/pulls/${prNumber}/comments/${commentId}/replies -X POST -f body=${finalMessage}`.quiet();

    // Try to parse response to get reply ID, but treat parse failures as success
    // (GitHub API may return non-JSON output on successful requests)
    let replyId: number | undefined;
    try {
      const responseData = JSON.parse(result.stdout.toString());
      replyId = responseData.id;
    } catch {
      // JSON parse failed, but API call succeeded - treat as success with unknown ID
      console.log(`✅ Reply posted to comment #${commentId}`);
      console.error("⚠️ Could not parse response to get reply comment ID");
      return 0;
    }

    console.log(`✅ Reply posted to comment #${commentId}`);
    if (replyId) {
      console.log(`✅ Created reply comment #${replyId}`);
    }
    // GitHub comment IDs are always positive, so 0 means "unknown but success"
    return replyId ?? 0;
  } catch (error) {
    console.error(`Error posting reply: ${error}`);
    return null;
  }
}

/**
 * Find thread ID by comment database ID.
 *
 * Issue #3791: When thread_id is passed as a numeric ID (comment database ID),
 * we need to look up the actual GraphQL thread ID.
 */
async function findThreadIdByCommentId(
  commentDatabaseId: string,
  owner: string,
  repo: string,
): Promise<string | null> {
  // Gemini review: Use REST API to get node_id, then query thread directly
  // This is O(1) instead of O(N) pagination through all threads
  try {
    // 1. Get the global Node ID using REST API
    const commentRes =
      await $`gh api /repos/${owner}/${repo}/pulls/comments/${commentDatabaseId} --jq .node_id`.quiet();
    const commentNodeId = commentRes.stdout.toString().trim();

    if (!commentNodeId) {
      console.error(`❌ Could not find node_id for comment ${commentDatabaseId}`);
      return null;
    }

    // 2. Fetch the thread ID for this comment using GraphQL
    const query = `
      query($id: ID!) {
        node(id: $id) {
          ... on PullRequestReviewComment {
            pullRequestReviewThread {
              id
            }
          }
        }
      }
    `;

    const result = await $`gh api graphql -f query=${query} -f id=${commentNodeId}`.quiet();
    const data = JSON.parse(result.stdout.toString());
    return data?.data?.node?.pullRequestReviewThread?.id ?? null;
  } catch (error) {
    const errorMsg = error instanceof Error ? error.message : String(error);
    const stderrMsg = (error as { stderr?: Buffer })?.stderr?.toString?.() ?? "";
    console.error(
      `Error finding thread by comment ID: ${errorMsg}${stderrMsg ? ` (stderr: ${stderrMsg})` : ""}`,
    );
    return null;
  }
}

/**
 * Resolve a review thread.
 *
 * Issue #3791: Accepts either GraphQL node ID (PRRT_xxx) or numeric comment ID.
 * If numeric, looks up the corresponding thread ID.
 */
async function resolveThread(
  threadId: string,
  prNumber?: string,
  owner?: string,
  repo?: string,
): Promise<boolean> {
  let actualThreadId = threadId;

  // Issue #3791: If threadId looks like a numeric ID, try to look up the actual thread ID
  // Gemini review: Use threadId (not commentId) for lookup, as threadId is typically the root comment ID
  if (/^\d+$/.test(threadId)) {
    if (!prNumber || !owner || !repo) {
      console.error("❌ Numeric thread ID provided but missing PR context (prNumber, owner, repo)");
      return false;
    }
    console.log("⚠️ Numeric thread ID detected, looking up actual GraphQL thread ID...");
    const foundId = await findThreadIdByCommentId(threadId, owner, repo);
    if (foundId) {
      console.log(`✅ Found thread ID: ${foundId}`);
      actualThreadId = foundId;
    } else {
      console.error(`❌ Could not find thread for comment ID ${threadId}`);
      console.error(
        "   Thread ID must be a GraphQL node ID (e.g., PRRT_xxx) or a valid comment ID",
      );
      return false;
    }
  }

  // Use GraphQL variables to avoid injection risk
  const query = `
    mutation($threadId: ID!) {
      resolveReviewThread(input: {threadId: $threadId}) {
        thread { isResolved }
      }
    }
  `;

  try {
    const result = await $`gh api graphql -f query=${query} -f threadId=${actualThreadId}`.quiet();
    const data = JSON.parse(result.stdout.toString());

    if (data?.data?.resolveReviewThread?.thread?.isResolved) {
      console.log(`✅ Thread ${actualThreadId} resolved`);
      return true;
    }
    console.error(`⚠️ Thread resolution may have failed: ${result.stdout.toString()}`);
    return false;
  } catch (error) {
    console.error(`Error resolving thread: ${error}`);
    return false;
  }
}

/**
 * List all unresolved review threads.
 */
async function listUnresolvedThreads(prNumber: string): Promise<ThreadInfo[]> {
  const [owner, repo] = await getRepoInfo();
  if (!owner || !repo) {
    console.error("Error: Could not determine repository info");
    return [];
  }

  const query = `
    query($pr: Int!) {
      repository(owner: "${owner}", name: "${repo}") {
        pullRequest(number: $pr) {
          reviewThreads(first: 50) {
            nodes {
              id
              isResolved
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

  try {
    const result = await $`gh api graphql -f query=${query} -F pr=${prNumber}`.quiet();
    const data = JSON.parse(result.stdout.toString());
    const threads = data?.data?.repository?.pullRequest?.reviewThreads?.nodes ?? [];
    return threads.filter((t: ThreadInfo) => !t.isResolved);
  } catch (error) {
    console.error(`Error listing threads: ${error}`);
    return [];
  }
}

// =============================================================================
// Message Formatting
// =============================================================================

/**
 * Format fix claim and verification into a combined message.
 *
 * Output format:
 *   修正済み: [fix_msg]
 *
 *   Verified: [verify_msg]
 *
 * Note: The "-- Claude Code" signature is added by postReply(), not here.
 *
 * Exported for testing (Issue #3625).
 */
export function formatVerifiedMessage(fixMsg: string, verifyMsg: string): string {
  // Patterns to detect existing prefixes (with or without space after colon)
  const fixPrefixPattern = /^修正済み:\s*/;
  const verifyPrefixPattern = /^Verified:\s*/i;

  // Ensure fix message has proper prefix
  // Note: replace() returns original string if no match, so no if/else needed
  const finalFixMsg = `修正済み: ${fixMsg.replace(fixPrefixPattern, "")}`;

  // Ensure verify message has proper prefix
  const finalVerifyMsg = `Verified: ${verifyMsg.replace(verifyPrefixPattern, "")}`;

  return `${finalFixMsg}\n\n${finalVerifyMsg}`;
}

// =============================================================================
// Logging
// =============================================================================

/**
 * Log review comment response to review-comments.jsonl.
 *
 * Issue #1639: Add logging for review comment processing history.
 */
async function logReviewCommentResponse(
  prNumber: string,
  commentId: string,
  message: string,
  resolution: Resolution,
  category?: Category,
  issueCreated?: string,
): Promise<void> {
  try {
    // Issue #3629: EXECUTION_LOG_DIR from common.ts is already absolute and worktree-aware
    const executionLogDir = EXECUTION_LOG_DIR;

    // Use strict validation for IDs (match Python's int() behavior)
    const logEntry: Record<string, unknown> = {
      timestamp: new Date().toISOString(),
      session_id: getSessionIdFallback(),
      pr_number: /^\d+$/.test(prNumber) ? Number.parseInt(prNumber, 10) : 0,
      comment_id: /^\d+$/.test(commentId) ? Number.parseInt(commentId, 10) : 0,
      resolution,
      response: message.slice(0, 200), // Truncate long messages
    };

    // Optional fields
    if (category) {
      logEntry.category = category;
    }
    if (issueCreated && /^\d+$/.test(issueCreated)) {
      logEntry.issue_created = Number.parseInt(issueCreated, 10);
    }

    await logToSessionFile(executionLogDir, "review-comments", getSessionIdFallback(), logEntry);
  } catch (error) {
    // Don't fail the main operation if logging fails
    console.error(`⚠️ Warning: Failed to log review response: ${error}`);
  }
}

// =============================================================================
// Result Summary (Issue #3416)
// =============================================================================

/**
 * Print final summary of the review response process.
 * Shows success/failure status for each step.
 */
function printFinalSummary(result: ResultTracker): void {
  const allSuccess = result.reply.success && result.resolve.success;

  if (allSuccess) {
    if (result.record.success) {
      console.log("\n✅ Done! Comment replied, thread resolved, and responses logged.");
    } else {
      console.log("\n✅ Comment replied and thread resolved (recording skipped).");
    }
  } else {
    // Distinguish between complete failure (reply failed) and partial failure (reply succeeded)
    const failureLabel = result.reply.success ? "Partial failure" : "Complete failure";
    console.log(`\n❌ ${failureLabel}:`);
    const replyStatus = result.reply.success ? "✅ Success" : "❌ Failed";
    const replyId = result.reply.id ? ` (comment #${result.reply.id})` : "";
    const replyError = result.reply.error ? ` ${result.reply.error}` : "";
    console.log(`  - Reply: ${replyStatus}${replyId}${replyError}`);

    // If reply failed, resolve and record were skipped
    if (!result.reply.success) {
      console.log("  - Resolve: (Skipped)");
      console.log("  - Record:  (Skipped)");
      return;
    }

    const resolveStatus = result.resolve.success ? "✅ Success" : "❌ Failed";
    const resolveError = result.resolve.error ? ` ${result.resolve.error}` : "";
    console.log(`  - Resolve: ${resolveStatus}${resolveError}`);

    const recordStatus = result.record.success ? "✅ Success" : "❌ Failed";
    const recordError = result.record.error ? ` ${result.record.error}` : "";
    console.log(`  - Record:  ${recordStatus}${recordError}`);
  }
}

/**
 * Determine exit code based on result.
 * - 0: All success (reply ✅ + resolve ✅)
 * - 1: Partial success (reply ✅ + resolve ❌), or pre-execution errors
 *      (argument validation, repo info lookup, etc.)
 * - 2: Complete failure (reply ❌)
 *
 * Note: Pre-execution errors (before reply attempt) return exit code 1
 * directly in main() to preserve backward compatibility.
 */
function getExitCode(result: ResultTracker): number {
  if (!result.reply.success) return 2; // Complete failure
  if (!result.resolve.success) return 1; // Partial success
  return 0; // All success
}

// =============================================================================
// CLI Entry Point
// =============================================================================

function printUsage(): void {
  console.log(`Usage:
  review_respond.ts <pr_number> <comment_id> <thread_id> "<message>" [options]
  review_respond.ts <pr_number> <comment_id> <thread_id> --verified "<fix>" "<verify>" [options]
  review_respond.ts <pr_number> --list  (list unresolved threads)

Options:
  --resolution   accepted (default), rejected, issue_created
  --validity     valid, invalid, partially_valid
  --category     bug, style, performance, security, test, docs, refactor, other
  --issue        Issue number (required for issue_created)
  --reason       Reason for rejection

Examples:
  review_respond.ts 464 123456789 PRRT_xxx "修正済み: 追加"
  review_respond.ts 464 123456789 PRRT_xxx --verified "処理順序修正" "file.py:10-20"
  review_respond.ts 464 123456789 PRRT_xxx "False positive" --resolution rejected
  review_respond.ts 464 --list`);
}

async function main(): Promise<number> {
  const args = process.argv.slice(2);

  if (args.length < 2) {
    printUsage();
    return 1;
  }

  const prNumber = args[0];

  // List mode
  if (args.length === 2 && args[1] === "--list") {
    const threads = await listUnresolvedThreads(prNumber);
    if (threads.length === 0) {
      console.log("No unresolved threads found.");
      return 0;
    }

    console.log(`Found ${threads.length} unresolved thread(s):\n`);
    for (const t of threads) {
      const comments = t.comments?.nodes ?? [];
      if (comments.length > 0) {
        const comment = comments[0];
        const author = comment.author?.login ?? "unknown";
        const body = comment.body.slice(0, 80).replace(/\n/g, " ");
        console.log(`Thread: ${t.id}`);
        console.log(`  Comment ID: ${comment.databaseId}`);
        console.log(`  Author: ${author}`);
        console.log(`  Body: ${body}...`);
        console.log(
          `  Command: bun run .claude/scripts/review_respond.ts ${prNumber} ${comment.databaseId} ${t.id} "<message>"`,
        );
        console.log();
      }
    }
    return 0;
  }

  // Parse arguments
  let commentId: string;
  let threadId: string;
  let message: string;
  let extraArgsStart: number;

  // Check for --verified mode
  if (args.length >= 4 && args[3] === "--verified") {
    if (args.length < 6) {
      console.error("Error: --verified requires both fix and verify messages.");
      console.log(
        'Usage: review_respond.ts <pr_number> <comment_id> <thread_id> --verified "<fix>" "<verify>"',
      );
      return 1;
    }
    commentId = args[1];
    threadId = args[2];
    message = formatVerifiedMessage(args[4], args[5]);
    extraArgsStart = 6;
  } else if (args.length >= 4) {
    // Standard mode
    commentId = args[1];
    threadId = args[2];
    message = args[3];
    extraArgsStart = 4;
  } else {
    console.error("Error: Missing arguments.");
    console.log('Usage: review_respond.ts <pr_number> <comment_id> <thread_id> "<message>"');
    return 1;
  }

  // Validate numeric IDs before API calls (match Python's int() behavior)
  if (!/^\d+$/.test(prNumber)) {
    console.error(`Error: pr_number must be numeric. Got: '${prNumber}'`);
    return 1;
  }
  if (!/^\d+$/.test(commentId)) {
    console.error(`Error: comment_id must be numeric. Got: '${commentId}'`);
    return 1;
  }

  // Parse optional quality tracking arguments
  let resolution: Resolution = "accepted";
  let validity: Validity | undefined;
  let category: Category | undefined;
  let issue: string | undefined;
  let reason: string | undefined;

  const validResolutions = ["accepted", "rejected", "issue_created"];
  const validValidities = ["valid", "invalid", "partially_valid"];
  const validCategories = [
    "bug",
    "style",
    "performance",
    "security",
    "test",
    "docs",
    "refactor",
    "other",
  ];

  // Issue #3630: Error on unknown CLI options (match Python argparse behavior)
  const knownOptions = ["--resolution", "--validity", "--category", "--issue", "--reason"];

  for (let i = extraArgsStart; i < args.length; i++) {
    const arg = args[i];
    if (arg === "--resolution") {
      if (i + 1 >= args.length) {
        console.error("Error: --resolution requires a value");
        return 1;
      }
      const value = args[++i];
      if (!validResolutions.includes(value)) {
        console.error(`Error: --resolution must be one of: ${validResolutions.join(", ")}`);
        return 1;
      }
      resolution = value as Resolution;
    } else if (arg === "--validity") {
      if (i + 1 >= args.length) {
        console.error("Error: --validity requires a value");
        return 1;
      }
      const value = args[++i];
      if (!validValidities.includes(value)) {
        console.error(`Error: --validity must be one of: ${validValidities.join(", ")}`);
        return 1;
      }
      validity = value as Validity;
    } else if (arg === "--category") {
      if (i + 1 >= args.length) {
        console.error("Error: --category requires a value");
        return 1;
      }
      const value = args[++i];
      if (!validCategories.includes(value)) {
        console.error(`Error: --category must be one of: ${validCategories.join(", ")}`);
        return 1;
      }
      category = value as Category;
    } else if (arg === "--issue") {
      if (i + 1 >= args.length) {
        console.error("Error: --issue requires a value");
        return 1;
      }
      issue = args[++i];
    } else if (arg === "--reason") {
      if (i + 1 >= args.length) {
        console.error("Error: --reason requires a value");
        return 1;
      }
      reason = args[++i];
    } else if (arg.startsWith("--")) {
      // Unknown option - error instead of silently ignoring
      console.error(`Error: Unknown option '${arg}'`);
      console.error(`Valid options for this mode: ${knownOptions.join(", ")}`);
      console.error("(Note: --list and --verified are mode selectors, not optional flags)");
      return 1;
    }
  }

  // Validate: --issue is required for issue_created
  if (resolution === "issue_created" && !issue) {
    console.error("Error: --issue is required when resolution is 'issue_created'");
    return 1;
  }

  // Get repo info for API calls
  const [owner, repo] = await getRepoInfo();
  if (!owner || !repo) {
    console.error("Error: Could not determine repository info");
    return 1;
  }

  // Issue #3416: Track results for each step
  const result: ResultTracker = {
    reply: { success: false },
    resolve: { success: false },
    record: { success: false },
  };

  // Post reply
  const replyId = await postReply(prNumber, commentId, message, owner, repo);
  if (replyId === null) {
    result.reply = { success: false, error: "(Failed to post reply)" };
    printFinalSummary(result);
    return getExitCode(result); // Complete failure (exit 2)
  }
  result.reply = { success: true, id: replyId || undefined };

  // Resolve thread (Issue #3791: pass context for numeric ID lookup)
  const resolved = await resolveThread(threadId, prNumber, owner, repo);
  result.resolve = {
    success: resolved,
    error: resolved ? undefined : "(Failed to resolve thread)",
  };

  // Record response for quality tracking (Issue #1432)
  // Continue even if resolve failed - we want to record what succeeded
  try {
    const recordSucceeded = await recordResponse({
      prNumber,
      commentId,
      resolution,
      validity,
      category,
      issueCreated: issue,
      reason,
    });
    result.record = { success: recordSucceeded };
  } catch (error) {
    console.error(`\n⚠️ Warning: Failed to record response: ${error}`);
    result.record = { success: false, error: String(error) };
  }

  // Log review comment response (Issue #1639)
  await logReviewCommentResponse(prNumber, commentId, message, resolution, category, issue);

  // Print final summary and return appropriate exit code
  printFinalSummary(result);
  return getExitCode(result);
}

// Run if executed directly
if (import.meta.main) {
  main().then((code) => process.exit(code));
}
