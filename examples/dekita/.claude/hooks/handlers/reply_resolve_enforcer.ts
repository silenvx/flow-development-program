#!/usr/bin/env bun
/**
 * レビューコメント返信後のResolve実行を強制する。
 *
 * Why:
 *   gh apiでレビューコメントに返信した後、resolveReviewThreadを呼ばずに
 *   マージしようとすると、未解決スレッドとしてブロックされる。
 *   返信後にResolve忘れを防止するため、状態追跡と強制を行う。
 *
 * What:
 *   - gh api .../replies 成功を検出し、pending状態を記録（複数対応）
 *   - resolveReviewThread成功を検出し、pending状態をクリア
 *   - gh pr merge時にpending状態があればブロック
 *
 * State:
 *   - writes: /tmp/claude-hooks/reply-pending-resolve-{session_id}.json
 *
 * Remarks:
 *   - PreToolUse/PostToolUseの両方で動作
 *   - スクリプト review_respond.ts の使用を推奨
 *
 * Changelog:
 *   - silenvx/dekita#3589: 初期実装
 *   - silenvx/dekita#3589: 複数返信の追跡対応、-X GET check追加
 *   - silenvx/dekita#3912: GraphQL addPullRequestReviewComment mutation対応
 */

import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { formatError } from "../lib/format_error";
import { getExitCode } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import {
  createContext,
  getBashCommand,
  getSessionId,
  getToolResultAsObject,
  isSafeSessionId,
  parseHookInput,
} from "../lib/session";
import { stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "reply-resolve-enforcer";
const SESSION_DIR = join(tmpdir(), "claude-hooks");

/**
 * Placeholder PR number used when PR number is not available in GraphQL mutation.
 * Used in extractReplyInfo() and checked in clearPendingResolveState().
 * Exported for test consistency.
 */
export const UNKNOWN_PR_NUMBER = "unknown";

/**
 * Regex pattern for detecting GraphQL inReplyTo field with quoted value.
 *
 * Matches:
 * - {inReplyTo: "PRRC_kwDO..." (after opening brace)
 * - ,inReplyTo: "PRRC_kwDO..." (after comma)
 * - (inReplyTo: "PRRC_kwDO..." (after opening parenthesis)
 * - inReplyTo: \"PRRC_kwDO...\" (escaped double quotes in JSON)
 * - inReplyTo: \\"PRRC_kwDO...\\" (double-escaped for shell)
 *
 * Pattern breakdown:
 * - [{,(] : Must be preceded by '{', ',', or '(' (GraphQL field/argument delimiters)
 * - \s* : Optional whitespace after delimiter
 * - inReplyTo\s*: : Field name and colon (with optional whitespace)
 * - \\*["'](.*?)\\*["'] : Quoted value with optional escaping (non-greedy capture)
 *
 * Why not include general whitespace before inReplyTo:
 * - Whitespace is too permissive and would match "inReplyTo:" inside body text
 *   (e.g., body: "Please check inReplyTo: \"PRRC_xxx\"")
 * - GraphQL fields in mutations are always preceded by '{', ',', or '('
 * - This stricter constraint prevents false positives with high reliability
 *
 * Shared between isReplyCommand() and extractReplyInfo() for consistency.
 */
const GRAPHQL_IN_REPLY_TO_PATTERN = /[{,(]\s*inReplyTo\s*:\s*\\*["'](.*?)\\*["']/;

/**
 * Pattern for environment variable prefixes before command execution.
 *
 * Matches:
 * - Direct env vars: FOO=1 bun run ...
 * - Multiple env vars: FOO=1 BAR=2 bun run ...
 * - env command: env VAR=1 bun run ...
 * - env with multiple vars: env FOO=1 BAR=2 bun run ...
 *
 * Pattern breakdown:
 * - (?:env\s+)? - Optional "env " prefix
 * - (?:\w+=\S*\s+)* - Zero or more VAR=value patterns (each followed by space)
 */
const ENV_PREFIX_PATTERN = "(?:env\\s+)?(?:\\w+=\\S*\\s+)*";

interface PendingResolveEntry {
  prNumber: string;
  commentId: string;
  timestamp: string;
}

interface PendingResolveState {
  entries: PendingResolveEntry[];
}

/**
 * Get state file path for pending resolve tracking.
 */
export function getStateFilePath(sessionId: string): string | null {
  if (!isSafeSessionId(sessionId)) {
    return null;
  }
  return join(SESSION_DIR, `reply-pending-resolve-${sessionId}.json`);
}

/**
 * Load pending resolve state (all entries).
 *
 * - invalid session ID の場合: 空のエントリ `{ entries: [] }` を返す。
 * - state ファイルが存在しない場合や読み込みエラー時: `null` を返す。
 */
export function loadPendingResolveState(sessionId: string): PendingResolveState | null {
  try {
    const stateFile = getStateFilePath(sessionId);
    if (!stateFile) return { entries: [] };
    if (existsSync(stateFile)) {
      const content = readFileSync(stateFile, "utf-8");
      const parsed = JSON.parse(content);
      // Handle legacy single-entry format
      if (parsed.prNumber && parsed.commentId) {
        return {
          entries: [
            {
              prNumber: parsed.prNumber,
              commentId: parsed.commentId,
              timestamp: parsed.timestamp,
            },
          ],
        };
      }
      // Validate that entries is an array (guard against malformed JSON like {})
      if (Array.isArray(parsed.entries)) {
        return parsed as PendingResolveState;
      }
      // Invalid format, treat as empty
      return { entries: [] };
    }
  } catch {
    // Best effort
  }
  return null;
}

/**
 * Add a pending resolve entry.
 */
export function addPendingResolveEntry(sessionId: string, entry: PendingResolveEntry): void {
  try {
    const stateFile = getStateFilePath(sessionId);
    if (!stateFile) return;
    if (!existsSync(SESSION_DIR)) {
      mkdirSync(SESSION_DIR, { recursive: true });
    }

    const existing = loadPendingResolveState(sessionId);
    const entries = existing?.entries ?? [];

    // Avoid duplicates (same PR + commentId)
    const isDuplicate = entries.some(
      (e) => e.prNumber === entry.prNumber && e.commentId === entry.commentId,
    );
    if (!isDuplicate) {
      entries.push(entry);
    }

    writeFileSync(stateFile, JSON.stringify({ entries }), "utf-8");
  } catch {
    // Best effort
  }
}

/**
 * Clear pending resolve state for a specific PR or all entries.
 *
 * @param sessionId - Session ID
 * @param prNumber - If provided, clear only entries for this PR. If omitted, clear all.
 *
 * Design decision:
 * - batch_resolve_threads.ts includes PR number, so we can clear per-PR
 * - Direct GraphQL mutation doesn't include PR number, so we clear all (fallback)
 */
export function clearPendingResolveState(sessionId: string, prNumber?: string): void {
  try {
    const stateFile = getStateFilePath(sessionId);
    if (!stateFile) return;

    if (!prNumber) {
      // prNumber not specified → clear all (backward compatibility)
      if (existsSync(stateFile)) {
        unlinkSync(stateFile);
      }
      return;
    }

    // prNumber specified → delete only entries for this PR
    const state = loadPendingResolveState(sessionId);

    if (state === null) {
      // JSON corruption or read failure - fall back to delete entire file for safety
      if (existsSync(stateFile)) {
        unlinkSync(stateFile);
      }
      return;
    }

    if (state.entries.length === 0) return;

    // Clear entries for the specified PR AND "unknown" entries
    // "unknown" entries come from GraphQL mutations where PR number isn't available.
    // When user resolves via batch_resolve_threads.ts (with real PR number), we should
    // also clear these "unknown" entries to avoid persistent blocking state.
    //
    // Trade-off (documented in Gemini review):
    // - In multi-PR workflows, clearing "unknown" with any PR may accidentally clear
    //   pending checks for a different PR (where GraphQL was used)
    // - This is an intentional "fail-open" design: prefer allowing merge over
    //   permanent blocking when state is ambiguous
    // - Risk is acceptable because:
    //   1. Multi-PR work with mixed GraphQL/REST replies is rare
    //   2. CI/GitHub still enforces unresolved threads independently
    //   3. Alternative (permanent block) is worse UX
    const filtered = state.entries.filter(
      (e) => e.prNumber !== prNumber && e.prNumber !== UNKNOWN_PR_NUMBER,
    );

    if (filtered.length === 0) {
      // All entries removed → delete the file itself
      // Note: existsSync check omitted since loadPendingResolveState succeeded above
      unlinkSync(stateFile);
    } else if (filtered.length !== state.entries.length) {
      // Save remaining entries (only if changed)
      writeFileSync(stateFile, JSON.stringify({ entries: filtered }), "utf-8");
    }
  } catch {
    // Best effort
  }
}

/**
 * Check if command is a reply to review comment.
 *
 * Detects two patterns:
 * 1. REST API: gh api /repos/.../pulls/.../comments/.../replies (POST)
 * 2. GraphQL: gh api graphql ... addPullRequestReviewComment ... inReplyTo
 *
 * For REST API:
 * - Detects POST method via -X POST, --method POST, or -f/-F flags (implicit POST).
 * - Excludes explicit -X GET to avoid false positives.
 *
 * For GraphQL:
 * - Detects addPullRequestReviewComment mutation with inReplyTo parameter.
 * - inReplyTo indicates this is a reply to an existing comment, not a new comment.
 */
export function isReplyCommand(command: string): boolean {
  const stripped = stripQuotedStrings(command);
  // Verify gh api is outside quotes
  if (!/gh\s+api/.test(stripped)) {
    return false;
  }

  // Pattern 1: GraphQL addPullRequestReviewComment mutation with inReplyTo
  // Note: We check command (not stripped) because mutation name and inReplyTo
  // are inside the GraphQL query string which is quoted.
  // Uses shared GRAPHQL_IN_REPLY_TO_PATTERN for consistency with extractReplyInfo()
  if (
    /gh\s+api\s+graphql/.test(stripped) &&
    command.includes("addPullRequestReviewComment") &&
    GRAPHQL_IN_REPLY_TO_PATTERN.test(command)
  ) {
    return true;
  }

  // Pattern 2: REST API POST to /replies endpoint
  const replyPattern = /pulls\/\d+\/comments\/\d+\/replies/;
  if (!replyPattern.test(command)) {
    return false;
  }

  // Explicit POST
  if (/-X\s+POST/i.test(command) || /--method\s+POST/i.test(command)) {
    return true;
  }

  // Implicit POST via -f/-F, but not if explicit -X GET
  if (/-[fF]\s/.test(command) && !/-X\s+GET/i.test(command)) {
    return true;
  }

  return false;
}

/**
 * Extract PR number and comment ID from reply command.
 *
 * Supports two patterns:
 * 1. REST API: repos/{owner}/{repo}/pulls/{pr}/comments/{id}/replies
 * 2. GraphQL: addPullRequestReviewComment mutation with inReplyTo
 *
 * For REST API:
 * - Supports both full path and short path (pulls/...).
 * - Leading slash is optional since gh api accepts both formats.
 *
 * For GraphQL:
 * - Extracts inReplyTo ID (node_id format, e.g., "PRRC_kwDO...")
 * - PR number is not available in GraphQL mutation, returns "unknown"
 *
 * Note: Short path (pulls/...) works when gh cli infers owner/repo from git remote.
 * This is consistent with isReplyCommand which uses the same pattern.
 */
export function extractReplyInfo(command: string): { prNumber: string; commentId: string } | null {
  // Pattern 1: REST API path
  const restMatch = command.match(
    /(?:\/?repos\/[^/]+\/[^/]+\/)?pulls\/(\d+)\/comments\/(\d+)\/replies/,
  );
  if (restMatch) {
    return { prNumber: restMatch[1], commentId: restMatch[2] };
  }

  // Pattern 2: GraphQL inReplyTo
  // Uses shared GRAPHQL_IN_REPLY_TO_PATTERN for consistency with isReplyCommand()
  const graphqlMatch = command.match(GRAPHQL_IN_REPLY_TO_PATTERN);
  if (graphqlMatch) {
    // PR number is not available in GraphQL mutation
    return { prNumber: UNKNOWN_PR_NUMBER, commentId: graphqlMatch[1] };
  }

  return null;
}

/**
 * Check if command is a resolveReviewThread mutation or batch resolve script.
 *
 * Detects:
 * 1. Direct GraphQL mutation: gh api graphql ... resolveReviewThread
 * 2. Batch resolve script execution: bun run ... batch_resolve_threads.ts
 *
 * Note: We check command (not stripped) for resolveReviewThread because
 * the mutation keyword is always inside the GraphQL query string which is quoted.
 */
export function isResolveCommand(command: string): boolean {
  const stripped = stripQuotedStrings(command);

  // Direct GraphQL mutation
  const isDirectResolve =
    /gh\s+api\s+graphql/.test(stripped) && command.includes("resolveReviewThread");

  // Batch resolve script execution
  // Requires bun run/node at a command boundary (start of string or after ;/&&/||/|)
  // to avoid false positives from echo/cat/ls etc.
  // Supports environment variable prefixes (FOO=1 bun run ..., env VAR=1 bun run ...)
  // \b ensures we don't match suffixes like "foo_batch_resolve_threads.ts"
  const batchResolvePattern = new RegExp(
    `(?:^|[;&|]{1,2}\\s*)${ENV_PREFIX_PATTERN}(?:bun\\s+run|node)\\s+[^;&|]*\\bbatch_resolve_threads\\.ts`,
  );
  const isBatchResolve = batchResolvePattern.test(stripped);

  return isDirectResolve || isBatchResolve;
}

/**
 * Extract PR number from batch_resolve_threads.ts command.
 *
 * Pattern: bun run ... batch_resolve_threads.ts {PR_NUMBER}
 *
 * Supports:
 * - PR number with or without quotes: 123, "123", '123'
 * - Word boundary check to avoid partial matches
 *
 * Returns null for:
 * - Direct GraphQL mutations (gh api graphql ... resolveReviewThread), since
 *   extracting PR number from threadId would require additional API calls.
 * - Commands that contain multiple different PR numbers for batch_resolve_threads.ts,
 *   in which case the caller should fall back to the safer "clear all" behavior.
 */
export function extractPrNumberFromResolveCommand(command: string): string | null {
  const stripped = stripQuotedStrings(command);

  // Direct GraphQL mutation - cannot extract PR number safely, return null
  const isDirectResolve =
    /gh\s+api\s+graphql/.test(stripped) && command.includes("resolveReviewThread");
  if (isDirectResolve) {
    return null;
  }

  // Verify the command contains actual execution (bun run / node) at a command boundary,
  // not just as part of an argument like `echo "bun run ..."`
  // Pattern: start of string OR after command separators (;, &&, ||, |)
  // Supports environment variable prefixes (FOO=1 bun run ..., env VAR=1 bun run ...)
  // \b ensures we don't match suffixes like "foo_batch_resolve_threads.ts"
  const execPattern = new RegExp(
    `(?:^|[;&|]{1,2}\\s*)${ENV_PREFIX_PATTERN}(?:bun\\s+run|node)\\s+[^;&|]*\\bbatch_resolve_threads\\.ts`,
  );
  if (!execPattern.test(stripped)) {
    return null;
  }

  // Match PR number with optional quotes (single or double)
  // Use original command to preserve quoted PR numbers
  // \b ensures we don't match suffixes like "foo_batch_resolve_threads.ts"
  // (?!\d) is a negative lookahead ensuring the number doesn't continue
  const regex = /\bbatch_resolve_threads\.ts\s+(['"]?)(\d+)\1(?!\d)/g;
  const matches = Array.from(command.matchAll(regex), (m) => m[2]);

  if (matches.length === 0) {
    return null;
  }

  // If same PR number appears multiple times, treat as single PR
  const uniquePrNumbers = Array.from(new Set(matches));

  if (uniquePrNumbers.length === 1) {
    return uniquePrNumbers[0];
  }

  // Multiple different PR numbers → return null for safe fallback (clear all)
  return null;
}

/**
 * Check if command is a gh pr merge command.
 */
export function isMergeCommand(command: string): boolean {
  const stripped = stripQuotedStrings(command);
  return /gh\s+pr\s+merge\b/.test(stripped);
}

async function main(): Promise<void> {
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    const toolName = inputData.tool_name ?? "";
    const hookType = inputData.hook_type ?? "";
    const ctx = createContext(inputData);
    sessionId = getSessionId(ctx) ?? undefined;

    if (!sessionId || toolName !== "Bash") {
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    const command = getBashCommand(inputData);

    // PreToolUse: Check for merge with pending resolve
    if (hookType === "PreToolUse") {
      if (isMergeCommand(command)) {
        const pendingState = loadPendingResolveState(sessionId);
        if (pendingState && pendingState.entries.length > 0) {
          const entries = pendingState.entries;
          const firstEntry = entries[0];
          const entriesText = entries
            .map((e) => `- PR #${e.prNumber}, コメントID: ${e.commentId} (${e.timestamp})`)
            .join("\n");

          const blockReason = `⚠️ 返信したスレッドがResolveされていません（${entries.length}件）。

**未Resolveのスレッド**:
${entriesText}

レビューコメントに返信した後は、必ずスレッドをResolveしてください。

**推奨コマンド**:
\`\`\`bash
bun run .claude/scripts/batch_resolve_threads.ts ${firstEntry.prNumber} "対応しました"
\`\`\`

または手動で:
1. スレッドIDを取得: \`gh api /repos/{owner}/{repo}/pulls/${firstEntry.prNumber}/comments --jq '.[] | select(.id == ${firstEntry.commentId}) | .node_id'\`
2. Resolve: \`gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "<thread_id>"}) { thread { id } } }'\``;

          await logHookExecution(
            HOOK_NAME,
            "block",
            `Merge blocked: ${entries.length} pending resolves`,
            undefined,
            { sessionId },
          );
          console.log(JSON.stringify(makeBlockResult(HOOK_NAME, blockReason)));
          process.exit(2);
        }
      }
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // PostToolUse: Track reply/resolve commands
    if (hookType === "PostToolUse") {
      const toolResult = getToolResultAsObject(inputData);
      const exitCode = getExitCode(toolResult);

      // Only track successful commands
      if (exitCode !== 0) {
        console.log(JSON.stringify({ continue: true }));
        return;
      }

      // Check for reply command
      if (isReplyCommand(command)) {
        const replyInfo = extractReplyInfo(command);
        if (replyInfo) {
          const entry: PendingResolveEntry = {
            prNumber: replyInfo.prNumber,
            commentId: replyInfo.commentId,
            timestamp: new Date().toISOString(),
          };
          addPendingResolveEntry(sessionId, entry);

          await logHookExecution(
            HOOK_NAME,
            "track",
            `Reply tracked for PR #${replyInfo.prNumber}`,
            undefined,
            { sessionId },
          );

          const message = `📝 レビューコメントへの返信を検出しました。

**重要**: 返信後は必ずスレッドをResolveしてください。

推奨: \`bun run .claude/scripts/batch_resolve_threads.ts ${replyInfo.prNumber} "対応しました"\`

⚠️ Resolveせずにマージしようとするとブロックされます。`;

          console.log(
            JSON.stringify({
              continue: true,
              systemMessage: message,
            }),
          );
          return;
        }
      }

      // Check for resolve command - clear pending state (per-PR if possible)
      //
      // Design decision: Clear per-PR when using batch_resolve_threads.ts,
      // clear all when using direct GraphQL mutation.
      //
      // Rationale:
      // 1. batch_resolve_threads.ts includes PR number → clear only that PR's entries
      // 2. Direct gh api graphql doesn't include PR number → clear all (fallback)
      //
      // Trade-off: Direct GraphQL mutation still clears all entries, but this is
      // acceptable because:
      // - batch_resolve_threads.ts is the recommended approach
      // - Direct GraphQL mutation while working on multiple PRs is rare
      // - Worst case: merge succeeds with some unresolved threads (caught by CI)
      if (isResolveCommand(command)) {
        const prNumber = extractPrNumberFromResolveCommand(command);
        clearPendingResolveState(sessionId, prNumber ?? undefined);

        await logHookExecution(
          HOOK_NAME,
          "clear",
          prNumber
            ? `Pending resolve state cleared for PR #${prNumber}`
            : "Pending resolve state cleared (all PRs)",
          undefined,
          { sessionId },
        );
      }

      console.log(JSON.stringify({ continue: true }));
      return;
    }

    console.log(JSON.stringify({ continue: true }));
  } catch (e) {
    await logHookExecution(HOOK_NAME, "error", `Hook error: ${formatError(e)}`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify({ continue: true }));
  }
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error:`, e);
    console.log(JSON.stringify({ continue: true }));
    process.exit(1);
  });
}
