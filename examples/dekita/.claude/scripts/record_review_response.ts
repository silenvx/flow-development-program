#!/usr/bin/env bun
/**
 * レビューコメントへの対応を記録する。
 *
 * Why:
 *   レビューコメントの対応状況を追跡し、
 *   品質分析に活用するため。
 *
 * What:
 *   - recordResponse(): 対応結果を記録
 *
 * State:
 *   - writes: .claude/logs/metrics/review-quality-*.jsonl
 *
 * Remarks:
 *   - resolution: accepted（対応済）, rejected（対応しない）, issue_created（Issue化）
 *   - validity: valid, invalid, partially_valid
 *   - --reason で理由を記録
 *
 * Changelog:
 *   - silenvx/dekita#1800: レビュー対応記録機能を追加
 *   - silenvx/dekita#2496: PPIDベースフォールバックに対応
 *   - silenvx/dekita#3618: TypeScript版に移行
 *   - silenvx/dekita#3629: worktreeでログパスがmainリポジトリに解決されるよう修正
 */

import { parseArgs } from "node:util";
import { METRICS_LOG_DIR } from "../hooks/lib/common";
import { logToSessionFile } from "../hooks/lib/logging";

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

interface RecordResponseOptions {
  prNumber: string;
  commentId: string;
  resolution: Resolution;
  validity?: Validity;
  category?: Category;
  issueCreated?: string;
  reason?: string;
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
// Validity Inference
// =============================================================================

/**
 * Infer validity from resolution if not explicitly provided.
 *
 * Exported for testing (Issue #3625).
 */
export function inferValidity(resolution: Resolution): Validity {
  switch (resolution) {
    case "accepted":
      return "valid";
    case "rejected":
      return "invalid";
    case "issue_created":
      return "valid"; // If we created an issue, the comment was valid
    default:
      return "valid";
  }
}

// =============================================================================
// Record Response
// =============================================================================

/**
 * Record the response to a review comment.
 *
 * This appends a new record with resolution/validity to the log.
 * The analysis script will use the latest record for each comment_id.
 *
 * Issue #2194: Now writes to session-specific files instead of global file.
 *
 * @returns True if recording was successful, false otherwise.
 */
export async function recordResponse(options: RecordResponseOptions): Promise<boolean> {
  const { prNumber, commentId, resolution, category, issueCreated, reason } = options;

  const sessionId = getSessionIdFallback();
  if (!sessionId) {
    console.error("Error: Could not get session ID");
    return false;
  }

  // Infer validity if not provided
  const validity = options.validity ?? inferValidity(resolution);

  // Parse numeric IDs for consistency with other logs (Issue #1687)
  // Use strict regex to match Python's int() behavior (reject partial matches like "123abc")
  if (!/^\d+$/.test(prNumber) || !/^\d+$/.test(commentId)) {
    console.error(
      `Error: pr_number and comment_id must be numeric. Got: pr_number='${prNumber}', comment_id='${commentId}'`,
    );
    throw new Error(`Invalid numeric ID: pr_number='${prNumber}', comment_id='${commentId}'`);
  }
  const prNumberInt = Number.parseInt(prNumber, 10);
  const commentIdInt = Number.parseInt(commentId, 10);

  // Build the record
  const record: Record<string, unknown> = {
    timestamp: new Date().toISOString(),
    session_id: sessionId,
    pr_number: prNumberInt,
    comment_id: commentIdInt,
    resolution,
    validity,
    record_type: "response", // Distinguish from initial recording
  };

  // Add optional fields
  if (category) {
    record.category = category;
  }
  if (issueCreated) {
    // Use strict regex to match Python's int() behavior
    if (!/^\d+$/.test(issueCreated)) {
      console.error(
        `Warning: issue_created must be numeric, got '${issueCreated}'. Skipping field.`,
      );
    } else {
      record.issue_created = Number.parseInt(issueCreated, 10);
    }
  }
  if (reason) {
    record.reason = reason;
  }

  // Issue #3629: METRICS_LOG_DIR from common.ts is already absolute and worktree-aware
  const metricsLogDir = METRICS_LOG_DIR;

  // Append to session-specific log file
  const success = await logToSessionFile(metricsLogDir, "review-quality", sessionId, record);

  if (success) {
    console.log(`Recorded response for comment ${commentId} on PR #${prNumber}`);
    console.log(`  Resolution: ${resolution}`);
    console.log(`  Validity: ${validity}`);
    if (category) {
      console.log(`  Category: ${category}`);
    }
    if (record.issue_created !== undefined) {
      console.log(`  Issue created: #${record.issue_created}`);
    }
    if (reason) {
      console.log(`  Reason: ${reason}`);
    }
  }

  return success;
}

// =============================================================================
// CLI Entry Point
// =============================================================================

async function main(): Promise<number> {
  const { values } = parseArgs({
    options: {
      pr: { type: "string" },
      "comment-id": { type: "string" },
      resolution: { type: "string" },
      validity: { type: "string" },
      category: { type: "string" },
      issue: { type: "string" },
      reason: { type: "string" },
      help: { type: "boolean", short: "h" },
    },
    strict: true,
  });

  if (values.help) {
    console.log(`Usage: record_review_response.ts --pr <PR> --comment-id <ID> --resolution <RES> [options]

Options:
  --pr           PR number (required)
  --comment-id   Comment ID (required)
  --resolution   How the comment was handled: accepted, rejected, issue_created (required)
  --validity     Whether the comment was valid: valid, invalid, partially_valid
  --category     Comment category: bug, style, performance, security, test, docs, refactor, other
  --issue        Issue number created (required when resolution=issue_created)
  --reason       Reason for rejection or partial validity
  --help, -h     Show this help message
`);
    return 0;
  }

  // Validate required arguments
  if (!values.pr) {
    console.error("Error: --pr is required");
    return 1;
  }
  if (!values["comment-id"]) {
    console.error("Error: --comment-id is required");
    return 1;
  }
  if (!values.resolution) {
    console.error("Error: --resolution is required");
    return 1;
  }

  const validResolutions = ["accepted", "rejected", "issue_created"];
  if (!validResolutions.includes(values.resolution)) {
    console.error(`Error: --resolution must be one of: ${validResolutions.join(", ")}`);
    return 1;
  }

  const validValidities = ["valid", "invalid", "partially_valid"];
  if (values.validity && !validValidities.includes(values.validity)) {
    console.error(`Error: --validity must be one of: ${validValidities.join(", ")}`);
    return 1;
  }

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
  if (values.category && !validCategories.includes(values.category)) {
    console.error(`Error: --category must be one of: ${validCategories.join(", ")}`);
    return 1;
  }

  // Validate issue_created requirement
  if (values.resolution === "issue_created" && !values.issue) {
    console.error("Error: --issue is required when resolution is 'issue_created'");
    return 1;
  }

  try {
    const success = await recordResponse({
      prNumber: values.pr,
      commentId: values["comment-id"],
      resolution: values.resolution as Resolution,
      validity: values.validity as Validity | undefined,
      category: values.category as Category | undefined,
      issueCreated: values.issue,
      reason: values.reason,
    });

    return success ? 0 : 1;
  } catch (error) {
    console.error(`Error: ${error}`);
    return 1;
  }
}

// Run if executed directly
if (import.meta.main) {
  main().then((code) => process.exit(code));
}
