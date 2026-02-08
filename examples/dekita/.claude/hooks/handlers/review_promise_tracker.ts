#!/usr/bin/env bun
/**
 * レビュー返信で「別Issue対応」と約束した場合のIssue作成追跡フック。
 *
 * Why:
 *   レビューコメントへの返信で「別Issue」「今後の改善」等と言いながら
 *   Issue作成を忘れるケースを防止する。
 *
 * What:
 *   - PostToolUse: レビュースレッド返信で約束パターンを検出 → 記録
 *   - PostToolUse: gh issue create を検出 → 約束を解消
 *   - Stop: 未解消の約束があれば警告
 *
 * State:
 *   - writes: {SESSION_DIR}/review-promises-{session}.json
 *
 * Remarks:
 *   - PostToolUse:Bash と Stop の両方で発火
 *   - 約束パターンは日本語/英語両方対応
 *   - path traversal攻撃対策済み
 *
 * Changelog:
 *   - silenvx/dekita#1437: フック追加
 *   - silenvx/dekita#1444: REST API対応
 *   - silenvx/dekita#2545: HookContextパターン移行
 *   - silenvx/dekita#3159: TypeScriptに移植
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";

import { SESSION_DIR } from "../lib/constants";
import { logHookExecution } from "../lib/logging";
import { type HookResult, makeApproveResult, outputResult } from "../lib/results";
import { type HookContext, createHookContext, parseHookInput } from "../lib/session";

// =============================================================================
// Types
// =============================================================================

interface PromiseRecord {
  timestamp: string;
  pattern: string;
  excerpt: string;
  resolved: boolean;
  resolved_at?: string;
}

// =============================================================================
// Constants
// =============================================================================

/**
 * Promise patterns indicating "will address in separate Issue".
 *
 * Note: More specific patterns (e.g., "このPRの範囲外") must come before
 * general patterns (e.g., "範囲外") to match correctly.
 */
const PROMISE_PATTERNS: readonly string[] = [
  "別[Ii]ssue",
  "今後の改善",
  "将来対応",
  "スコープ外",
  "このPRの範囲外",
  "範囲外",
  "別途対応",
  "後で対応",
];

// =============================================================================
// Promise File Management
// =============================================================================

/**
 * Get the promise tracking file path for current session.
 */
function getPromiseFile(ctx: HookContext): string {
  const sessionId = ctx.sessionId ?? "unknown";
  // Sanitize session_id to prevent path traversal attacks
  const safeFilename = basename(sessionId);
  return join(SESSION_DIR, `review-promises-${safeFilename}.json`);
}

/**
 * Load recorded promises from session file.
 */
function loadPromises(ctx: HookContext): PromiseRecord[] {
  const promiseFile = getPromiseFile(ctx);
  if (existsSync(promiseFile)) {
    try {
      const content = readFileSync(promiseFile, "utf-8");
      return JSON.parse(content) as PromiseRecord[];
    } catch {
      // Corrupted or unreadable file - return empty list to start fresh
    }
  }
  return [];
}

/**
 * Save promises to session file.
 */
function savePromises(ctx: HookContext, promises: PromiseRecord[]): void {
  const promiseFile = getPromiseFile(ctx);
  mkdirSync(SESSION_DIR, { recursive: true });
  writeFileSync(promiseFile, JSON.stringify(promises, null, 2), "utf-8");
}

// =============================================================================
// Pattern Detection
// =============================================================================

/**
 * Detect if text contains a promise pattern.
 *
 * @returns The matched pattern if found, null otherwise.
 */
export function detectPromiseInText(text: string): string | null {
  for (const pattern of PROMISE_PATTERNS) {
    const regex = new RegExp(pattern, "i");
    if (regex.test(text)) {
      return pattern;
    }
  }
  return null;
}

// =============================================================================
// Command Detection
// =============================================================================

/**
 * Check if command is a review thread reply.
 *
 * Detects both GraphQL API and REST API reply patterns.
 *
 * @returns Tuple of [isReply, replyBody].
 */
export function isReviewThreadReply(command: string): [boolean, string | null] {
  // Check for GraphQL API (addPullRequestReviewThreadReply mutation)
  if (command.includes("addPullRequestReviewThreadReply")) {
    // Extract body from the GraphQL mutation
    // Pattern handles: body: "text with \"escaped\" quotes"
    const bodyMatch = command.match(/body:\s*"((?:[^"\\]|\\.)*)"|body:\s*'((?:[^'\\]|\\.)*)'/s);
    if (bodyMatch) {
      // Return whichever group matched (double or single quotes)
      let body = bodyMatch[1] ?? bodyMatch[2] ?? null;
      // Unescape the content
      if (body) {
        body = body.replace(/\\"/g, '"').replace(/\\'/g, "'");
      }
      return [true, body];
    }
    return [true, null];
  }

  // Check for REST API (review-respond.py uses /comments/.../replies)
  if (command.includes("/comments/") && command.includes("/replies")) {
    // Extract body from REST API call
    // Patterns: --body "...", -b "...", --field body="...", -f body="...", "body": "..."
    const bodyMatch = command.match(
      /(?:--body|-b)\s+"((?:[^"\\]|\\.)*)"|(?:--body|-b)\s+'((?:[^'\\]|\\.)*)'|(?:--field|-f)\s+body="((?:[^"\\]|\\.)*)"|(?:--field|-f)\s+body='((?:[^'\\]|\\.)*)'|"body":\s*"((?:[^"\\]|\\.)*)"/s,
    );
    if (bodyMatch) {
      let body =
        bodyMatch[1] ?? bodyMatch[2] ?? bodyMatch[3] ?? bodyMatch[4] ?? bodyMatch[5] ?? null;
      if (body) {
        body = body.replace(/\\"/g, '"').replace(/\\'/g, "'");
      }
      return [true, body];
    }
    return [true, null];
  }

  return [false, null];
}

/**
 * Check if command creates an issue.
 *
 * Only match when gh issue create appears as an actual command
 * execution at a recognized command boundary.
 */
export function isIssueCreate(command: string): boolean {
  const stripped = command.trim();
  // Skip comments
  if (stripped.startsWith("#")) {
    return false;
  }

  // Match gh issue create at command boundaries
  // - Start of command
  // - After shell operators: &&, ||, ;, |
  // - Inside subshell: $(, (
  // - After control flow keywords: if, then, else, do, etc.
  // - After env var assignment: VAR="value" or VAR=value followed by space
  return /(?:(?:^|&&|\|\||;|\||\$\(|\(|\bif\s+|\bthen\s+|\belse\s+|\bdo\s+|\{\s*)\s*|[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*'|[^\s"']+)\s+)gh\s+issue\s+create\b/.test(
    command,
  );
}

// =============================================================================
// Promise Management
// =============================================================================

/**
 * Record a promise made in a review reply.
 */
async function recordPromise(ctx: HookContext, replyBody: string, pattern: string): Promise<void> {
  const promises = loadPromises(ctx);
  promises.push({
    timestamp: new Date().toISOString(),
    pattern,
    excerpt: replyBody.slice(0, 100),
    resolved: false,
  });
  savePromises(ctx, promises);
  await logHookExecution(
    "review-promise-tracker",
    "info",
    `Promise recorded: ${pattern}`,
    undefined,
    { sessionId: ctx.sessionId },
  );
}

/**
 * Mark the most recent unresolved promise as resolved.
 */
async function resolvePromise(ctx: HookContext): Promise<void> {
  const promises = loadPromises(ctx);
  for (let i = promises.length - 1; i >= 0; i--) {
    if (!promises[i].resolved) {
      promises[i].resolved = true;
      promises[i].resolved_at = new Date().toISOString();
      savePromises(ctx, promises);
      await logHookExecution(
        "review-promise-tracker",
        "info",
        "Promise resolved by issue creation",
        undefined,
        { sessionId: ctx.sessionId },
      );
      return;
    }
  }
}

/**
 * Get all unresolved promises.
 */
function getUnresolvedPromises(ctx: HookContext): PromiseRecord[] {
  const promises = loadPromises(ctx);
  return promises.filter((p) => !p.resolved);
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: HookResult = makeApproveResult("review-promise-tracker");
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);
    sessionId = ctx.sessionId;
    const hookType = data.hook_type ?? "";
    const toolName = data.tool_name ?? "";
    const toolInput = data.tool_input ?? {};

    // Stop hook: Check for unresolved promises
    if (hookType === "Stop") {
      const unresolved = getUnresolvedPromises(ctx);
      if (unresolved.length > 0) {
        const patterns = unresolved.map((p) => p.pattern);
        await logHookExecution(
          "review-promise-tracker",
          "warn",
          `Unresolved promises: ${patterns.join(", ")}`,
          undefined,
          { sessionId: ctx.sessionId },
        );

        // Output warning via systemMessage
        result.systemMessage = `\u26a0\ufe0f レビュー返信で「別Issue対応」と約束しましたが、Issue作成が確認できません（${unresolved.length}件）。\n\n約束したパターン:\n${unresolved.map((p) => `- ${p.pattern}`).join("\n")}\n\n\`gh issue create\` でIssueを作成してください。`;
      }
      outputResult(result);
      return;
    }

    // PostToolUse: Track promises and resolutions
    if (hookType === "PostToolUse" && toolName === "Bash") {
      const command = (toolInput as { command?: string }).command ?? "";

      // Check for review thread reply with promise
      const [isReply, replyBody] = isReviewThreadReply(command);
      if (isReply && replyBody) {
        const pattern = detectPromiseInText(replyBody);
        if (pattern) {
          await recordPromise(ctx, replyBody, pattern);
        }
      } else if (isIssueCreate(command)) {
        // Use else if for mutual exclusivity - a command is either
        // a review reply or an issue creation, not both
        await resolvePromise(ctx);
      }
    }
  } catch (e) {
    // Fail open - log details but don't leak to output
    const errorMessage = e instanceof Error ? e.message : String(e);
    await logHookExecution("review-promise-tracker", "error", errorMessage, undefined, {
      sessionId,
    });
  }

  outputResult(result);
}

if (import.meta.main) {
  main();
}
