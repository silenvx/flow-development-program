#!/usr/bin/env bun
/**
 * Copilot reviewの繰り返しエラー時にPR作り直しを提案する。
 *
 * Why:
 *   Copilot reviewは特定の状況でエラーを返し続けることがあり、
 *   PRを作り直すことで解決する場合がある。無駄なリトライを防ぐ。
 *
 * What:
 *   - Copilot reviewエラーを検出・カウント
 *   - 閾値を超えたらPR作り直しを提案
 *   - PR切り替え時にカウンタをリセット
 *
 * State:
 *   - writes: {TMPDIR}/claude-hooks/copilot-review-errors-{session}.json
 *
 * Remarks:
 *   - 提案型フック（ブロックしない、systemMessageで提案）
 *   - PostToolUse:Bashで発火
 *   - エラー閾値は3回（ERROR_THRESHOLD）
 *   - PR切り替え時にカウンタ自動リセット
 *   - 成功時もカウンタリセット
 *
 * Changelog:
 *   - silenvx/dekita#544: フック追加
 *   - silenvx/dekita#563: セッションID取得をctx経由に統一
 *   - silenvx/dekita#3159: TypeScriptに移植
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { type HookResult, makeApproveResult, outputResult } from "../lib/results";
import {
  type HookContext,
  createHookContext,
  getToolResultAsObject,
  parseHookInput,
} from "../lib/session";

// =============================================================================
// Constants
// =============================================================================

const TRACKING_DIR = join(tmpdir(), "claude-hooks");
const ERROR_THRESHOLD = 3;

// =============================================================================
// Types
// =============================================================================

interface ErrorTrackingData {
  count: number;
  lastPr: string | null;
}

// =============================================================================
// Error Tracking
// =============================================================================

/**
 * Get the error tracking file path for the current session.
 */
function getErrorTrackingFile(ctx: HookContext): string {
  const sessionId = ctx.sessionId ?? "unknown";
  const safeSessionId = basename(sessionId);
  return join(TRACKING_DIR, `copilot-review-errors-${safeSessionId}.json`);
}

/**
 * Load error tracking data from session file.
 */
function loadErrorCount(ctx: HookContext): ErrorTrackingData {
  try {
    const trackingFile = getErrorTrackingFile(ctx);
    if (existsSync(trackingFile)) {
      const content = readFileSync(trackingFile, "utf-8");
      const data = JSON.parse(content);
      return {
        count: data.count ?? 0,
        lastPr: data.last_pr ?? data.lastPr ?? null,
      };
    }
  } catch {
    // Silently ignore file read/parse errors and return default
  }
  return { count: 0, lastPr: null };
}

/**
 * Save error tracking data to session file.
 */
function saveErrorCount(ctx: HookContext, data: ErrorTrackingData): void {
  try {
    mkdirSync(TRACKING_DIR, { recursive: true });
    const trackingFile = getErrorTrackingFile(ctx);
    writeFileSync(
      trackingFile,
      JSON.stringify({ count: data.count, last_pr: data.lastPr }),
      "utf-8",
    );
  } catch {
    // Silently ignore file write errors (non-critical)
  }
}

// =============================================================================
// Detection Functions
// =============================================================================

/**
 * Check if command is checking Copilot review status.
 */
export function isCopilotReviewCheck(command: string, stdout: string): boolean {
  // Check for gh pr checks or gh api commands related to reviews
  if (/gh\s+pr\s+checks\b/.test(command)) {
    return true;
  }
  if (/gh\s+api.*pulls.*reviews/.test(command)) {
    return true;
  }
  if (/gh\s+api.*requested_reviewers/.test(command)) {
    return true;
  }
  // ci-monitor.py output containing Copilot status (both error and success)
  if (stdout.includes("Copilot")) {
    return true;
  }
  return false;
}

/**
 * Check if output indicates Copilot review error.
 */
export function hasCopilotReviewError(stdout: string, stderr: string): boolean {
  const combined = stdout + stderr;

  // Known error patterns
  const errorPatterns = [
    /Copilot encountered an error/i,
    /Copilot.*unable to review/i,
    /review.*error.*Copilot/i,
    /Copilot.*failed/i,
  ];

  return errorPatterns.some((pattern) => pattern.test(combined));
}

/**
 * Extract PR number from command if present.
 */
export function extractPrNumber(command: string): string | null {
  // Match patterns like: pulls/123, pull/123, pr 123, pr checks 123
  // Also handles spaceless patterns like pull123 (edge case)
  const match = command.match(/(?:pulls?[/\s]?|pr\s+(?:checks\s+)?)(\d+)/i);
  if (match) {
    return match[1];
  }
  return null;
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: HookResult = makeApproveResult("copilot-review-retry-suggestion");

  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);
    const toolName = data.tool_name ?? "";

    if (toolName !== "Bash") {
      await logHookExecution(
        "copilot-review-retry-suggestion",
        "approve",
        `not Bash: ${toolName}`,
        undefined,
        { sessionId: ctx.sessionId ?? undefined },
      );
      outputResult(result);
      return;
    }

    const toolInput = data.tool_input ?? {};
    const toolResult = getToolResultAsObject(data);
    const command = (toolInput as { command?: string }).command ?? "";
    const stdout = typeof toolResult.stdout === "string" ? toolResult.stdout : "";
    const stderr = typeof toolResult.stderr === "string" ? toolResult.stderr : "";

    // Check if this is a Copilot review check
    if (!isCopilotReviewCheck(command, stdout)) {
      await logHookExecution(
        "copilot-review-retry-suggestion",
        "approve",
        "not a Copilot review check",
        undefined,
        { sessionId: ctx.sessionId ?? undefined },
      );
      outputResult(result);
      return;
    }

    // Check if there's a Copilot review error
    if (hasCopilotReviewError(stdout, stderr)) {
      // Track the error
      const trackingData = loadErrorCount(ctx);
      const prNum = extractPrNumber(command);

      // Reset counter if switching to a different PR or leaving PR context
      // Issue #3211: Simplified condition - reset when lastPr exists and prNum differs (including null)
      if (trackingData.lastPr && prNum !== trackingData.lastPr) {
        trackingData.count = 0;
        if (!prNum) {
          trackingData.lastPr = null;
        }
      }

      trackingData.count++;
      if (prNum) {
        trackingData.lastPr = prNum;
      }
      saveErrorCount(ctx, trackingData);

      await logHookExecution(
        "copilot-review-retry-suggestion",
        "approve",
        `Copilotレビューエラー検出: ${trackingData.count}回目`,
        undefined,
        { sessionId: ctx.sessionId ?? undefined },
      );

      // Suggest PR recreation after threshold
      if (trackingData.count >= ERROR_THRESHOLD) {
        const prCloseCmd = trackingData.lastPr
          ? `gh pr close ${trackingData.lastPr}`
          : "gh pr close <PR番号>";

        result.systemMessage = `⚠️ **Copilot reviewが${trackingData.count}回連続でエラーを返しています**

このエラーはPRを作り直すことで解決する場合があります:

\`\`\`bash
# 1. 現在のPRをクローズ
${prCloseCmd}

# 2. 新しいPRを作成（同じブランチから）
gh pr create --title "..." --body "..."
\`\`\`

💡 PR作り直し後、Copilot reviewが正常に動作することがあります。`;
      }
    } else {
      // Reset counter on successful check (no error)
      const trackingData = loadErrorCount(ctx);
      if (trackingData.count > 0) {
        trackingData.count = 0;
        trackingData.lastPr = null;
        saveErrorCount(ctx, trackingData);
      }
    }
  } catch (e) {
    await logHookExecution(
      "copilot-review-retry-suggestion",
      "error",
      `フックエラー: ${formatError(e)}`,
    );
  }

  outputResult(result);
}

if (import.meta.main) {
  main();
}
