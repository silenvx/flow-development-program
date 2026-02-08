#!/usr/bin/env bun
/**
 * PreToolUse hook: Block removal of AI reviewers from PRs.
 *
 * Why:
 *   AI reviewers (Copilot, Codex) should complete their reviews naturally.
 *   Removing them via API circumvents the review process.
 *
 * What:
 *   - Detects gh api calls that would remove AI reviewers
 *   - Blocks removal of Copilot or Codex from requested reviewers
 *   - Allows legitimate reviewer management
 *
 * Detection methods:
 *   - Here-string: <<< '{"reviewers":["Copilot"]}'
 *   - Heredoc: << EOF ... EOF
 *   - Flag: -f reviewers='["Copilot"]'
 *
 * Remarks:
 *   - ブロック型フック（AI reviewer削除試行時にブロック）
 *   - PreToolUseで発火（Bashコマンドのみ）
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "reviewer-removal-check";

// AI reviewer patterns (case-insensitive)
const AI_REVIEWER_PATTERNS = ["copilot", "codex"];

/**
 * Check if the name matches an AI reviewer.
 */
export function isAiReviewer(name: string): boolean {
  const nameLower = name.toLowerCase();
  return AI_REVIEWER_PATTERNS.some((pattern) => nameLower.includes(pattern));
}

/**
 * Extract JSON content from heredoc syntax.
 *
 * Supports patterns like:
 * - << 'EOF' ... EOF
 * - << EOF ... EOF
 * - <<'EOF' ... EOF
 * - <<EOF ... EOF
 *
 * Returns JSON string if found, null otherwise.
 */
export function extractJsonFromHeredoc(command: string): string | null {
  // Match heredoc: << 'EOF' or << EOF followed by content and closing EOF
  // The delimiter can be quoted or unquoted
  const heredocMatch = command.match(/<<\s*['"]?(\w+)['"]?\s*\n([\s\S]*?)\n\1/);
  if (heredocMatch) {
    return heredocMatch[2].trim();
  }
  return null;
}

/**
 * Check if the command attempts to remove AI reviewers.
 *
 * Returns [shouldBlock, message]
 */
export function checkReviewerRemoval(command: string): [boolean, string] {
  // Pattern: gh api .../requested_reviewers -X DELETE
  if (!command.includes("gh api")) {
    return [false, ""];
  }

  if (!command.includes("requested_reviewers")) {
    return [false, ""];
  }

  if (!command.includes("-X DELETE") && !command.includes("--method DELETE")) {
    return [false, ""];
  }

  // Only check reviewer names in JSON input or -f flags
  // Do NOT check entire command string - causes false positives for repo names like "copilot-tools"

  // Check JSON input via here-string
  // Pattern: --input - <<< '{"reviewers":["Copilot"]}'
  const jsonMatch = command.match(/<<<\s*['"]?(\{[\s\S]*?\})['"]?/);
  if (jsonMatch) {
    try {
      const data = JSON.parse(jsonMatch[1]);
      const reviewers = data.reviewers || [];
      for (const reviewer of reviewers) {
        if (isAiReviewer(reviewer)) {
          return [true, `AIレビュアー (${reviewer}) の解除は禁止されています`];
        }
      }
    } catch {
      // Invalid JSON in here-string, continue to check other patterns
    }
  }

  // Check JSON input via heredoc
  // Pattern: << EOF ... {"reviewers":["Copilot"]} ... EOF
  const heredocContent = extractJsonFromHeredoc(command);
  if (heredocContent) {
    try {
      const data = JSON.parse(heredocContent);
      const reviewers = data.reviewers || [];
      for (const reviewer of reviewers) {
        if (isAiReviewer(reviewer)) {
          return [true, `AIレビュアー (${reviewer}) の解除は禁止されています`];
        }
      }
    } catch {
      // Invalid JSON in heredoc, continue to check other patterns
    }
  }

  // Check -f reviewers= pattern
  const reviewersMatch = command.match(/-f\s+reviewers=['"]?\[([^\]]+)\]/);
  if (reviewersMatch) {
    const reviewerStr = reviewersMatch[1];
    for (const pattern of AI_REVIEWER_PATTERNS) {
      if (reviewerStr.toLowerCase().includes(pattern.toLowerCase())) {
        return [true, `AIレビュアー (${pattern}) の解除は禁止されています`];
      }
    }
  }

  return [false, ""];
}

interface HookResult {
  decision?: string;
  reason?: string;
}

async function main(): Promise<void> {
  let result: HookResult = {};
  let sessionId: string | undefined;

  try {
    const hookInput = await parseHookInput();
    const ctx = createHookContext(hookInput);
    sessionId = ctx.sessionId;

    const toolName = hookInput.tool_name || "";
    const toolInput = hookInput.tool_input || {};

    // Only check Bash commands
    if (toolName !== "Bash") {
      await logHookExecution(HOOK_NAME, "approve", "Not a Bash command", undefined, { sessionId });
      console.log(JSON.stringify({}));
      return;
    }

    const command = (toolInput as { command?: string }).command || "";

    const [shouldBlock, message] = checkReviewerRemoval(command);

    if (shouldBlock) {
      const errorMessage = `${message}

AIレビューが完了するまで待ってください。

タイムアウトした場合の対応:
1. ci-monitor.py の --timeout オプションで待機時間を延長
2. GitHub Copilot/Codex のステータスを確認（障害の可能性）
3. ユーザーに状況を報告して指示を仰ぐ

レビュー不要な理由がある場合は、PRにコメントで説明してください。`;

      result = makeBlockResult(HOOK_NAME, errorMessage);
    } else {
      result = {};
    }
  } catch (e) {
    // On error, approve to avoid blocking legitimate commands
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(e)}`);
    result = { reason: `Hook error: ${formatError(e)}` };
  }

  await logHookExecution(HOOK_NAME, result.decision ?? "approve", result.reason, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({}));
  });
}
