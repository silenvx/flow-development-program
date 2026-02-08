#!/usr/bin/env bun
/**
 * レビューコメント読み込み後にアクション継続を促すリマインダー。
 *
 * Why:
 *   レビューコメントを `gh api` で読み込んだ後、テキストのみで終わると
 *   問題の見落としや対応漏れが発生する。リアルタイムでリマインドすることで
 *   アクション継続を促進する。
 *
 * What:
 *   - `gh api repos/.../pulls/.../comments` の成功を検出
 *   - systemMessage でアクション継続を促すリマインダーを出力
 *   - 振り返り時に事後チェック（reflection_self_check.py）と連携
 *
 * Trigger:
 *   - PostToolUse (Bash)
 *
 * State:
 *   - Stateless（状態管理なし）
 *
 * Remarks:
 *   - 振り返り観点 `review_comment_action_continuation` と連携
 *   - ブロックではなくリマインダー（continue: true + systemMessage）
 *
 * Changelog:
 *   - silenvx/dekita#3570: 初期実装
 *   - silenvx/dekita#3573: 引数ベースのパース方式に移行
 */

import { formatError } from "../lib/format_error";
import { getExitCode } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { getBashCommand, getToolResultAsObject, parseHookInput } from "../lib/session";
import {
  splitCommandChainQuoteAware,
  splitShellArgs,
  stripEnvPrefix,
  stripQuotedStrings,
} from "../lib/strings";
import { createHookContext } from "../lib/types";

const HOOK_NAME = "review-comment-action-reminder";

// =============================================================================
// gh api Command Parsing (Issue #3573)
// =============================================================================

/**
 * Parsed information from a `gh api` command.
 */
interface GhApiCommandInfo {
  /** The API endpoint (e.g., "repos/owner/repo/pulls/123/comments") */
  endpoint: string | null;
  /** HTTP method (GET, POST, PATCH, PUT, DELETE) */
  method: string;
  /** Whether the command includes any -f or -F field flag (implies POST) */
  hasFieldFlag: boolean;
  /** Whether the command includes a body= field */
  hasBodyField: boolean;
  /** Whether the command includes an event= field */
  hasEventField: boolean;
  /** Whether the command includes --input flag */
  hasInputFlag: boolean;
  /** Whether the endpoint is a reply endpoint (comments/{id}/replies) */
  isReplyEndpoint: boolean;
}

/**
 * Flags that take a value in gh api command.
 * Similar to GH_API_VALUE_FLAGS in merge_check.ts (not identical).
 * Note: --paginate does NOT take a value, so it's excluded from this pattern.
 * Note: -i is --include (no value), NOT --input (Gemini review fix)
 */
const GH_API_VALUE_FLAGS =
  /^(-[XfFHtqp]|--field|--header|--raw-field|--input|--jq|--template|--method|--cache|--preview|--hostname)$/;

/**
 * Parse a gh api command into structured information.
 *
 * Issue #3573: Uses argument-based parsing instead of regex for robustness.
 *
 * @param command - A single command (not a chain)
 * @returns Parsed command info, or null if not a gh api command
 */
function parseGhApiCommand(command: string): GhApiCommandInfo | null {
  const normalized = stripEnvPrefix(command);

  if (!/^\s*gh\s+api\s+/.test(normalized)) {
    return null;
  }

  try {
    const args = splitShellArgs(normalized);
    const apiIndex = args.findIndex((a) => a === "api");
    if (apiIndex === -1) return null;

    let endpoint: string | null = null;
    let method = "GET";
    let hasFieldFlag = false;
    let hasBodyField = false;
    let hasEventField = false;
    let hasInputFlag = false;

    // Parse arguments after "api"
    for (let i = apiIndex + 1; i < args.length; i++) {
      const arg = args[i];

      // Handle -X METHOD or --method METHOD
      if (arg === "-X" || arg === "--method") {
        if (i + 1 < args.length) {
          method = args[i + 1].toUpperCase();
          i++;
        }
        continue;
      }

      // Handle -XPOST (combined form)
      if (arg.startsWith("-X") && arg.length > 2) {
        method = arg.slice(2).toUpperCase();
        continue;
      }

      // Handle --method=METHOD
      if (arg.startsWith("--method=")) {
        method = arg.slice(9).toUpperCase();
        continue;
      }

      // Handle --input flag (note: -i is --include, not --input; Gemini review fix)
      if (arg === "--input") {
        hasInputFlag = true;
        if (i + 1 < args.length) i++; // Skip the value
        continue;
      }

      // Handle --input=value form
      if (arg.startsWith("--input=")) {
        hasInputFlag = true;
        continue;
      }

      // Handle field flags: -f, -F, --field, --raw-field
      // Note: -f/-F implies POST in gh api (Copilot review fix)
      if (arg === "-f" || arg === "-F" || arg === "--field" || arg === "--raw-field") {
        hasFieldFlag = true;
        if (i + 1 < args.length) {
          const fieldValue = args[i + 1];
          if (fieldValue.startsWith("body=")) hasBodyField = true;
          if (fieldValue.startsWith("event=")) hasEventField = true;
          i++;
        }
        continue;
      }

      // Handle -f=value, -F=value, --field=value forms
      if (
        arg.startsWith("-f=") ||
        arg.startsWith("-F=") ||
        arg.startsWith("--field=") ||
        arg.startsWith("--raw-field=")
      ) {
        hasFieldFlag = true;
        const eqIndex = arg.indexOf("=");
        const fieldValue = arg.slice(eqIndex + 1);
        if (fieldValue.startsWith("body=")) hasBodyField = true;
        if (fieldValue.startsWith("event=")) hasEventField = true;
        continue;
      }

      // Skip other flags that take values
      if (arg.startsWith("-")) {
        if (GH_API_VALUE_FLAGS.test(arg) && i + 1 < args.length) {
          i++; // Skip the value
        }
        continue;
      }

      // First non-flag argument is the endpoint
      if (endpoint === null) {
        // Remove query parameters and shell operators in one pass (Gemini review fix)
        // Shell operators like >, |, ; may be attached without spaces
        endpoint = arg.split(/[?|>;&#]/)[0];
      }
    }

    const isReplyEndpoint = endpoint ? /comments\/\d+\/replies/.test(endpoint) : false;

    return {
      endpoint,
      method,
      hasFieldFlag,
      hasBodyField,
      hasEventField,
      hasInputFlag,
      isReplyEndpoint,
    };
  } catch {
    // splitShellArgs throws on unbalanced quotes
    return null;
  }
}

/**
 * Patterns to match review comment endpoints.
 * Issue #3573: Separated from the main regex patterns for clarity.
 * Gemini review fix: Support full URLs (https://api.github.com/repos/...)
 */
const REVIEW_COMMENT_ENDPOINT_PATTERNS = [
  /^(?:https?:\/\/[^/]+\/)?\/?repos\/[^/]+\/[^/]+\/pulls\/\d+\/comments\/?$/,
  /^(?:https?:\/\/[^/]+\/)?\/?repos\/[^/]+\/[^/]+\/pulls\/\d+\/reviews\/?$/,
];

/**
 * Check if an endpoint is a review comment endpoint.
 *
 * @param endpoint - The API endpoint to check
 * @returns true if it's a review comment endpoint
 */
function isReviewCommentEndpoint(endpoint: string | null): boolean {
  if (!endpoint) return false;
  return REVIEW_COMMENT_ENDPOINT_PATTERNS.some((pattern) => pattern.test(endpoint));
}

/**
 * Check if the parsed command represents a write operation.
 *
 * Write operations include:
 * - Non-GET HTTP methods (POST, PATCH, PUT, DELETE)
 * - Commands with any -f/-F field flag (implies POST in gh api)
 * - Commands with --input flag
 * - Reply endpoints
 *
 * Note: hasFieldFlag check covers hasBodyField and hasEventField,
 * but we keep them for explicit documentation and backward compatibility.
 *
 * @param parsed - Parsed command info
 * @returns true if it's a write operation
 */
function isWriteOperation(parsed: GhApiCommandInfo): boolean {
  // Simplified using || (Gemini review fix)
  return (
    parsed.method !== "GET" ||
    parsed.hasFieldFlag || // Copilot review fix: any -f/-F implies POST
    parsed.hasBodyField ||
    parsed.hasEventField ||
    parsed.hasInputFlag ||
    parsed.isReplyEndpoint
  );
}

/**
 * Check if the command is reading review comments (not writing).
 *
 * Issue #3570: Handle quoted endpoints while avoiding false positives.
 * Issue #3573: Refactored to use argument-based parsing for robustness.
 *
 * Strategy:
 * 1. Check that `gh api` appears outside quotes (using stripped command)
 * 2. Split command chain and parse each part
 * 3. Check if any part is a review comment read operation
 *
 * @param command - The bash command to check.
 * @returns true if the command is reading review comments.
 */
export function isReadingReviewComments(command: string): boolean {
  if (!command.trim()) {
    return false;
  }

  // Strip quoted strings to check if gh api is a real command (not inside echo/heredoc)
  const strippedCommand = stripQuotedStrings(command);

  // First, verify that `gh api` itself is not inside quotes
  // If gh api doesn't appear in stripped command, it's inside quotes (false positive)
  if (!/gh\s+api/.test(strippedCommand)) {
    return false;
  }

  // Split command chain and check each part
  // Track both read and write operations to suppress reminder when
  // user is already taking action (Gemini review fix)
  let hasReviewCommentRead = false;
  let hasAnyWrite = false;

  for (const part of splitCommandChainQuoteAware(command)) {
    const parsed = parseGhApiCommand(part);
    if (!parsed) continue;

    // Check if it's a review comment read operation
    if (isReviewCommentEndpoint(parsed.endpoint) && !isWriteOperation(parsed)) {
      hasReviewCommentRead = true;
    }

    // Check if any operation in the chain is a write (user is taking action)
    if (isWriteOperation(parsed)) {
      hasAnyWrite = true;
    }
  }

  // Only remind if there's a read but no write action in the same command chain
  return hasReviewCommentRead && !hasAnyWrite;
}

/**
 * Check if the command execution was successful.
 *
 * @param toolResult - The tool result from the hook input.
 * @returns true if the command succeeded (exit code 0).
 */
function isSuccessfulExecution(toolResult: Record<string, unknown>): boolean {
  const exitCode = getExitCode(toolResult);
  return exitCode === 0;
}

/**
 * Extract PR number from the command for context.
 *
 * @param command - The bash command.
 * @returns PR number or null if not found.
 */
export function extractPrNumberFromCommand(command: string): string | null {
  const match = command.match(/pulls\/(\d+)/);
  return match?.[1] ?? null;
}

async function main(): Promise<void> {
  const result: Record<string, unknown> = { continue: true };
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    const toolName = inputData.tool_name ?? "";
    const ctx = createHookContext(inputData);
    sessionId = ctx.sessionId;

    if (toolName !== "Bash") {
      console.log(JSON.stringify(result));
      return;
    }

    const toolResult = getToolResultAsObject(inputData);
    const command = getBashCommand(inputData);

    // Check if this is a successful review comment read operation
    if (isReadingReviewComments(command) && isSuccessfulExecution(toolResult)) {
      const prNumber = extractPrNumberFromCommand(command);

      await logHookExecution(
        HOOK_NAME,
        "remind",
        `Reminder for PR #${prNumber ?? "?"} comments`,
        { command_pattern: "review_comment_read" },
        { sessionId },
      );

      // Output reminder as systemMessage (not blocking)
      const message =
        "📝 **レビューコメント読み込み完了**\n\n" +
        "レビューコメントを確認した後は、以下のアクションを実行してください:\n" +
        "- 修正が必要な場合: Edit/Writeツールでコード修正\n" +
        "- 返信が必要な場合: `gh api` でコメント返信\n" +
        "- 問題がない場合: Resolveして次へ進む\n\n" +
        "⚠️ テキストのみで終わらず、必ずツール呼び出しを行ってください。";

      console.log(
        JSON.stringify({
          continue: true,
          systemMessage: message,
        }),
      );
      return;
    }
  } catch (e) {
    await logHookExecution(HOOK_NAME, "error", `Hook error: ${formatError(e)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
  });
}
