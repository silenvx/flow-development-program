#!/usr/bin/env bun
/**
 * PRボディに「なぜ」と「参照」が含まれているかチェックする。
 *
 * Why:
 *   PRの背景・動機が不明なままマージすると、将来の変更履歴追跡が困難になる。
 *   また、Issue/PRへの参照がないとトレーサビリティが失われる。
 *
 * What:
 *   - gh pr create/mergeコマンドを検出
 *   - PRボディから「なぜ」セクションの存在を確認
 *   - Issue/PR/ドキュメントへの参照を確認
 *   - gh pr create: 不足している場合は警告のみ（早期検出）
 *   - gh pr merge: 不足している場合はブロック
 *
 * Remarks:
 *   - closes-keyword-checkはClosesキーワードの提案、これは全体品質チェック
 *   - --body-file/-F使用時はファイル内容を確認できないため警告のみ
 *   - 段階的移行PRにはIssue参照強制（Issue #2608）
 *
 * Changelog:
 *   - silenvx/dekita#3424: PR作成時は警告のみに変更（Issue #3424）
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { checkBodyQuality, checkIncrementalPr } from "../lib/check_utils";
import { extractPrBody, hasBodyFileOption } from "../lib/command";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";
import { stripQuotedStrings } from "../lib/strings";
import type { HookResult } from "../lib/types";

const HOOK_NAME = "pr-body-quality-check";

/**
 * Check if command is a gh pr create command.
 */
export function isGhPrCreateCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  const strippedCommand = stripQuotedStrings(command);
  return /gh\s+pr\s+create\b/.test(strippedCommand);
}

/**
 * Check if command is a gh pr merge command.
 */
export function isGhPrMergeCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  const strippedCommand = stripQuotedStrings(command);
  return /gh\s+pr\s+merge\b/.test(strippedCommand);
}

/**
 * Check if command is a gh pr edit with --body option.
 * This is used to update PR body, so we should skip quality check.
 */
export function isGhPrEditBodyCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  const strippedCommand = stripQuotedStrings(command);
  // Check for gh pr edit with body update options (--body, -b, --body-file, -F)
  // Also match --body= and --body-file= formats
  return (
    /gh\s+pr\s+edit\b/.test(strippedCommand) &&
    /(?:--body(?:=|\s|\b)|--body-file(?:=|\s|\b)|-b(?:=|\s|\b)|-F(?:=|\s|\b))/.test(strippedCommand)
  );
}

/**
 * Check if command is a gh api PATCH to update a PR.
 * Matches patterns like: gh api -X PATCH /repos/.../pulls/123
 */
export function isGhApiPatchPrCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  const strippedCommand = stripQuotedStrings(command);
  // Check for gh api with PATCH method and pulls endpoint
  const hasGhApi = /gh\s+api\b/.test(strippedCommand);
  // Match various PATCH option formats: -X PATCH, -XPATCH, --method PATCH, --method=PATCH
  const hasPatchMethod = /(?:-X\s*PATCH|--method(?:=|\s+)PATCH)\b/i.test(strippedCommand);
  // Use original command for endpoint check since URL might be quoted
  // Match only the PR resource itself (not sub-resources like /pulls/123/comments/456)
  const hasPullsEndpoint = /\/pulls\/\d+(?:$|[\s"'])/.test(command);
  return hasGhApi && hasPatchMethod && hasPullsEndpoint;
}

/**
 * Extract PR number from gh pr merge command.
 */
export function extractPrNumberFromMerge(command: string): string | null {
  const strippedCommand = stripQuotedStrings(command);
  const match = strippedCommand.match(/gh\s+pr\s+merge\s+#?(\d+)/);
  if (match) {
    return match[1];
  }
  return null;
}

// extractPrBody and hasBodyFileOption are imported from ../lib/command

/**
 * Check if PR is created by Dependabot.
 */
async function isDependabotPr(prNumber: string | null): Promise<boolean> {
  const args = ["pr", "view"];
  if (prNumber) {
    args.push(prNumber);
  }
  args.push("--json", "author", "--jq", ".author.login");

  const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_MEDIUM * 1000 });
  if (result.success) {
    const author = result.stdout.trim();
    return author === "dependabot[bot]" || author === "app/dependabot";
  }
  return false;
}

/**
 * Get PR body from GitHub API.
 */
async function getPrBodyFromApi(prNumber: string | null): Promise<string | null> {
  const args = ["pr", "view"];
  if (prNumber) {
    args.push(prNumber);
  }
  args.push("--json", "body", "--jq", ".body");

  const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_MEDIUM * 1000 });
  if (result.success) {
    return result.stdout.trim();
  }
  return null;
}

/**
 * Format the block message for missing items (used for merge).
 */
export function formatBlockMessage(missing: string[]): string {
  let message = "PR body is missing required items (blocking merge)\n\n";
  message += "**Missing items:**\n";
  for (const item of missing) {
    message += `- ${item}\n`;
  }

  message += "\n**Recommended PR body format:**\n";
  message += "```markdown\n";
  message += "## Why\n";
  message += "Describe the motivation/background for this change\n";
  message += "\n";
  message += "## What\n";
  message += "Describe what this change does\n";
  message += "\n";
  message += "## How\n";
  message += "Describe how this change is implemented\n";
  message += "\n";
  message += "Closes #XXX\n";
  message += "```\n";

  message += "\n**How to fix:**\n";
  message += '1. Update PR body with `gh pr edit <PR-number> --body "..."`\n';
  message += "2. Or edit PR via GitHub Web UI\n";

  return message;
}

/**
 * Format warning message for PR creation (non-blocking).
 * Early detection helps improve PR quality before merge.
 */
export function formatWarningMessage(missing: string[]): string {
  let message =
    "⚠️ pr-body-quality-check: PR body is missing the following items. Please add them before merging:\n";
  for (const item of missing) {
    message += `- ${item}\n`;
  }
  return message;
}

async function main(): Promise<void> {
  let result: HookResult = {};
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);
    sessionId = ctx.sessionId;
    const toolInput = data.tool_input || {};
    const command = (toolInput.command as string) || "";

    // Priority order: create/merge checks must run even in command chains
    // Skip checks (edit/api patch) are last so they don't shadow create/merge
    if (isGhPrCreateCommand(command)) {
      // Check PR body at creation time
      if (hasBodyFileOption(command)) {
        result.systemMessage =
          "⚠️ pr-body-quality-check: Cannot check body quality with -F/--body-file. " +
          "Please include Why section and references.";
      } else {
        const body = extractPrBody(command);
        if (body !== null) {
          const [isValid, missing] = checkBodyQuality(body);
          if (isValid) {
            result.systemMessage = "✅ pr-body-quality-check: Required items OK";
          } else {
            // PR作成時は警告のみ（早期検出）- マージ時にブロック
            result.systemMessage = formatWarningMessage(missing);
          }
        } else {
          // No body specified - will be entered interactively
          result.systemMessage =
            "⚠️ pr-body-quality-check: Cannot check quality without --body. " +
            "Please include Why section and references when entering interactively.";
        }
      }
    } else if (isGhPrMergeCommand(command)) {
      // Check PR body before merge
      const prNumber = extractPrNumberFromMerge(command);

      // Skip quality check for Dependabot PRs
      if (await isDependabotPr(prNumber)) {
        result.systemMessage = "✅ pr-body-quality-check: Skipped for Dependabot PR";
      } else {
        const body = await getPrBodyFromApi(prNumber);
        if (body === null) {
          result.systemMessage =
            "⚠️ pr-body-quality-check: Failed to get PR body. " + "Skipping quality check.";
        } else {
          const [isValid, missing] = checkBodyQuality(body);
          if (!isValid) {
            const reason = formatBlockMessage(missing);
            result = makeBlockResult(HOOK_NAME, reason);
          } else {
            // Check for incremental migration keywords
            const [incrementalValid, incrementalReason] = checkIncrementalPr(body);
            if (!incrementalValid && incrementalReason) {
              result = makeBlockResult(HOOK_NAME, incrementalReason);
            } else {
              result.systemMessage = "✅ pr-body-quality-check: Pre-merge quality check OK";
            }
          }
        }
      }
    } else if (isGhPrEditBodyCommand(command)) {
      // Skip quality check for PR body update commands
      // These commands are used to FIX the body quality, so blocking them would be counterproductive
      result.systemMessage =
        "✅ pr-body-quality-check: Skipped for PR body update (gh pr edit --body)";
    } else if (isGhApiPatchPrCommand(command)) {
      result.systemMessage = "✅ pr-body-quality-check: Skipped for PR body update (gh api PATCH)";
    }
  } catch (error) {
    console.error(`[pr-body-quality-check] Hook error: ${formatError(error)}`);
    result = {};
  }

  // Log only for non-block decisions (makeBlockResult logs automatically)
  if (result.decision !== "block") {
    logHookExecution(HOOK_NAME, result.decision ?? "approve", result.reason, undefined, {
      sessionId,
    });
  }
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
