#!/usr/bin/env bun
/**
 * Codex CLIレビュー出力をパースしてレビューコメントを記録する。
 *
 * Why:
 *   Codex CLIレビューの結果を分析することで、レビュー品質の追跡や
 *   よくある指摘パターンの特定ができる。
 *
 * What:
 *   - codex review出力からレビューコメントを抽出
 *   - 個別コメントをreview-quality.jsonlに記録
 *   - 実行メタデータをcodex-reviews.jsonlに記録
 *   - コメント数ゼロならpass、あればfailとして記録
 *
 * State:
 *   - writes: .claude/logs/metrics/review-quality-{session}.jsonl
 *   - writes: .claude/logs/metrics/codex-reviews-{session}.jsonl
 *   - writes: .claude/logs/markers/codex-rate-limit-{branch}.marker (when rate limited)
 *
 * Remarks:
 *   - 記録型フック（ブロックしない、メトリクス記録）
 *   - PostToolUse:Bashで発火（codex reviewコマンド）
 *   - JSON出力/行単位出力の両方をパース対応
 *   - exit_code != 0の場合もerrorとして記録
 *   - レート制限検出時はマーカーファイルを作成（codex_review_checkと連携）
 *
 * Changelog:
 *   - silenvx/dekita#3310: レート制限検出とマーカー作成
 *   - silenvx/dekita#610: レビュー品質追跡システム
 *   - silenvx/dekita#1233: 実行メタデータのログ記録
 *   - silenvx/dekita#2607: セッションID対応
 *   - silenvx/dekita#3159: TypeScriptに移植
 */

import { existsSync, mkdirSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { CODEX_RATE_LIMIT_MARKER_PREFIX, CODEX_RATE_LIMIT_PATTERN } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { getCurrentBranch } from "../lib/git";
import { getPrNumberForBranch } from "../lib/github";
import { logHookExecution } from "../lib/logging";
import { getMarkersDir } from "../lib/markers";
import { type HookResult, makeApproveResult, outputResult } from "../lib/results";
import {
  type ReviewCategory,
  estimateCategory,
  logCodexReviewExecution,
  logReviewComment,
} from "../lib/review";
import { createHookContext, getToolResultAsObject, parseHookInput } from "../lib/session";
import { sanitizeBranchName, stripQuotedStrings } from "../lib/strings";

// =============================================================================
// Rate Limit Detection (Issue #3310)
// =============================================================================

/**
 * Check if Codex output indicates rate limit error.
 */
export function isCodexRateLimited(output: string): boolean {
  return CODEX_RATE_LIMIT_PATTERN.test(output);
}

/**
 * Create rate limit marker for current branch.
 * This marker is checked by codex_review_check to allow Gemini fallback.
 */
export function createRateLimitMarker(branch: string): boolean {
  try {
    const safeBranch = sanitizeBranchName(branch);
    const markersDir = getMarkersDir();
    mkdirSync(markersDir, { recursive: true });
    const markerFile = join(markersDir, `${CODEX_RATE_LIMIT_MARKER_PREFIX}${safeBranch}.marker`);
    const timestamp = new Date().toISOString();
    writeFileSync(markerFile, `${branch}:${timestamp}`);
    return true;
  } catch {
    return false;
  }
}

/**
 * Remove rate limit marker for branch.
 * Called when Codex review succeeds (no rate limit).
 */
export function removeRateLimitMarker(branch: string): void {
  try {
    const safeBranch = sanitizeBranchName(branch);
    const markersDir = getMarkersDir();
    const markerFile = join(markersDir, `${CODEX_RATE_LIMIT_MARKER_PREFIX}${safeBranch}.marker`);
    if (existsSync(markerFile)) {
      unlinkSync(markerFile);
    }
  } catch {
    // Ignore errors - marker removal is best effort
  }
}

// =============================================================================
// Command Detection
// =============================================================================

/**
 * Check if command is a codex review command.
 */
export function isCodexReviewCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  const strippedCommand = stripQuotedStrings(command);
  return /codex\s+review\b/.test(strippedCommand);
}

// =============================================================================
// Output Parsing
// =============================================================================

/**
 * Extract tokens used count from Codex CLI output.
 *
 * Looks for patterns like:
 * - "tokens used: 6,356"
 * - "tokens used: 1234"
 */
export function extractTokensUsed(output: string): number | null {
  // Match "tokens used: X,XXX" or "tokens used: XXX" (with optional commas)
  const match = output.match(/tokens\s+used:\s*([\d,]+)/i);
  if (match) {
    // Remove commas and convert to int
    const tokensStr = match[1].replace(/,/g, "");
    const tokens = Number.parseInt(tokensStr, 10);
    return Number.isNaN(tokens) ? null : tokens;
  }
  return null;
}

/**
 * Extract base branch from codex review command.
 *
 * Looks for patterns like:
 * - "codex review --base main"
 * - "codex review --base origin/main"
 */
export function extractBaseBranch(command: string): string | null {
  const match = command.match(/--base\s+(\S+)/);
  if (match) {
    return match[1];
  }
  return null;
}

// =============================================================================
// Comment Parsing Types
// =============================================================================

interface ParsedComment {
  filePath: string | null;
  lineNumber: number | null;
  body: string;
}

// =============================================================================
// Line-by-Line Parsing
// =============================================================================

/**
 * Parse a line that may contain file:line: comment format.
 *
 * Supported formats:
 * - file.ts:10: message
 * - file.ts:10:5: message (with column)
 * - src/path/file.tsx (line 25): message
 * - file.ts#L10: message
 */
function parseFileLineComment(line: string): ParsedComment | null {
  // Format: file:line: message or file:line:column: message
  let match = line.match(/^([^:]+):(\d+)(?::\d+)?:\s*(.+)$/);
  if (match) {
    return {
      filePath: match[1].trim(),
      lineNumber: Number.parseInt(match[2], 10),
      body: match[3].trim(),
    };
  }

  // Format: file (line N): message
  match = line.match(/^(.+?)\s*\(line\s+(\d+)\):\s*(.+)$/i);
  if (match) {
    return {
      filePath: match[1].trim(),
      lineNumber: Number.parseInt(match[2], 10),
      body: match[3].trim(),
    };
  }

  // Format: file#LN: message
  match = line.match(/^(.+?)#L(\d+):\s*(.+)$/);
  if (match) {
    return {
      filePath: match[1].trim(),
      lineNumber: Number.parseInt(match[2], 10),
      body: match[3].trim(),
    };
  }

  return null;
}

// =============================================================================
// JSON Output Parsing
// =============================================================================

/**
 * Extract comment body from various possible keys.
 */
function extractCommentBody(item: Record<string, unknown>): string | null {
  for (const key of ["body", "message", "comment", "text"]) {
    if (key in item && item[key]) {
      return String(item[key]);
    }
  }
  return null;
}

/**
 * Check if item has comment content in any supported key.
 */
function hasCommentContent(item: Record<string, unknown>): boolean {
  return extractCommentBody(item) !== null;
}

/**
 * Try to parse output as JSON containing review comments.
 */
function parseJsonOutput(output: string): ParsedComment[] {
  const comments: ParsedComment[] = [];

  try {
    const data = JSON.parse(output);

    if (Array.isArray(data)) {
      for (const item of data) {
        if (typeof item === "object" && item !== null && hasCommentContent(item)) {
          comments.push({
            filePath: (item.file as string) ?? (item.path as string) ?? null,
            lineNumber: (item.line as number) ?? null,
            body: extractCommentBody(item) ?? "",
          });
        }
      }
    } else if (typeof data === "object" && data !== null) {
      // Handle single comment or nested structure
      if ("comments" in data && Array.isArray(data.comments)) {
        return parseJsonOutput(JSON.stringify(data.comments));
      }
      if (hasCommentContent(data)) {
        comments.push({
          filePath: (data.file as string) ?? (data.path as string) ?? null,
          lineNumber: (data.line as number) ?? null,
          body: extractCommentBody(data) ?? "",
        });
      }
    }
  } catch {
    // Not JSON format - will fall back to line-by-line parsing
  }

  return comments;
}

/**
 * Parse Codex CLI review output to extract comments.
 */
function parseCodexReviewOutput(output: string): ParsedComment[] {
  // First, try JSON parsing
  const jsonComments = parseJsonOutput(output);
  if (jsonComments.length > 0) {
    return jsonComments;
  }

  // Fall back to line-by-line parsing
  const comments: ParsedComment[] = [];
  let currentComment: ParsedComment | null = null;

  for (const rawLine of output.split("\n")) {
    const line = rawLine.trim();

    if (!line) {
      // Empty line may end a multi-line comment
      if (currentComment?.body) {
        comments.push(currentComment);
        currentComment = null;
      }
      continue;
    }

    // Try to parse as a new comment
    const parsed = parseFileLineComment(line);
    if (parsed) {
      // Save previous comment if exists
      if (currentComment?.body) {
        comments.push(currentComment);
      }
      currentComment = parsed;
    } else if (currentComment) {
      // Append to current comment body (multi-line comment)
      currentComment.body = `${currentComment.body || ""} ${line}`;
    }
  }

  // Don't forget the last comment
  if (currentComment?.body) {
    comments.push(currentComment);
  }

  return comments;
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: HookResult = makeApproveResult("codex-review-output-logger");
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    // Issue #2607: Create context for session_id logging
    const ctx = createHookContext(data);
    sessionId = ctx.sessionId ?? undefined;
    const toolInput = data.tool_input ?? {};
    const toolResult = getToolResultAsObject(data);

    const command = (toolInput as { command?: string }).command ?? "";

    // Only process codex review commands
    if (!isCodexReviewCommand(command)) {
      await logHookExecution(
        "codex-review-output-logger",
        "approve",
        "not a codex review command",
        undefined,
        { sessionId: ctx.sessionId ?? undefined },
      );
      result.reason = "not a codex review command";
      outputResult(result);
      return;
    }

    const stdout = typeof toolResult.stdout === "string" ? toolResult.stdout : "";
    const exitCode = typeof toolResult.exit_code === "number" ? toolResult.exit_code : 0;

    // Extract metadata from command and output
    const baseBranch = extractBaseBranch(command);
    const tokensUsed = extractTokensUsed(stdout);
    const branch = await getCurrentBranch();

    // Don't process further if command failed, but still log the execution
    if (exitCode !== 0) {
      // Issue #3310: Detect rate limit error and create marker
      const stderr = typeof toolResult.stderr === "string" ? toolResult.stderr : "";
      const combinedOutput = `${stdout}\n${stderr}`;

      if (branch && isCodexRateLimited(combinedOutput)) {
        // Create rate limit marker for Gemini fallback
        const markerCreated = createRateLimitMarker(branch);
        const logMessage = markerCreated
          ? "Codex rate limited, marker created"
          : "Codex rate limited, marker creation failed";
        await logHookExecution("codex-review-output-logger", "approve", logMessage, undefined, {
          sessionId: ctx.sessionId ?? undefined,
        });
      } else if (branch) {
        // Non-rate-limit failure: clear any stale marker to reflect latest state
        removeRateLimitMarker(branch);
      }

      await logCodexReviewExecution({
        branch,
        base: baseBranch,
        verdict: "error",
        commentCount: 0,
        tokensUsed,
        exitCode,
        sessionId: ctx.sessionId ?? undefined,
      });

      await logHookExecution("codex-review-output-logger", "approve", undefined, undefined, {
        sessionId: ctx.sessionId ?? undefined,
      });
      outputResult(result);
      return;
    }

    // Issue #3310: Success case - remove rate limit marker if exists
    if (branch) {
      removeRateLimitMarker(branch);
    }

    // Parse the output for comments
    const comments = parseCodexReviewOutput(stdout);

    // Determine verdict: pass if no comments, fail if comments found
    const verdict = comments.length === 0 ? "pass" : "fail";

    // Always log review execution (Issue #1233)
    await logCodexReviewExecution({
      branch,
      base: baseBranch,
      verdict,
      commentCount: comments.length,
      tokensUsed,
      exitCode,
      sessionId: ctx.sessionId,
    });

    // Log individual comments to review-quality.jsonl
    if (comments.length > 0) {
      let prNumber: string | null = null;
      if (branch) {
        prNumber = await getPrNumberForBranch(branch);
      }

      // Log each comment with unique ID per execution (nanosecond precision)
      // Issue #3211: Use BigInt to avoid exceeding Number.MAX_SAFE_INTEGER
      const executionTs =
        BigInt(Date.now()) * 1000000n + BigInt(Math.floor(Math.random() * 1000000));

      for (let i = 0; i < comments.length; i++) {
        const comment = comments[i];
        const body = comment.body ?? "";
        const category: ReviewCategory = estimateCategory(body);

        await logReviewComment({
          prNumber: prNumber ?? "unknown",
          commentId: `codex-cli-${executionTs.toString()}-${i}`,
          reviewer: "codex_cli",
          category,
          filePath: comment.filePath,
          lineNumber: comment.lineNumber,
          bodyPreview: body.slice(0, 200),
          sessionId: ctx.sessionId,
        });
      }

      result.systemMessage = `Codex CLI review: ${comments.length} comment(s) logged`;
    } else {
      result.systemMessage = "Codex CLI review: pass (no issues found)";
    }
  } catch (e) {
    // Hook failure should not block Claude Code
    // Log error but continue
    console.error(`[codex-review-output-logger] Error: ${formatError(e)}`);
  }

  await logHookExecution("codex-review-output-logger", "approve", undefined, undefined, {
    sessionId,
  });
  outputResult(result);
}

if (import.meta.main) {
  main();
}
