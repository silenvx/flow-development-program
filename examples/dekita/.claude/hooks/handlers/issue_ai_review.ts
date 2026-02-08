#!/usr/bin/env bun
/**
 * Issue作成後にAIレビュー（Gemini/Codex）を実行し結果を通知する。
 *
 * Why:
 *   Issue作成時点でAIレビューを実行することで、Issue内容の品質を
 *   即座に向上させる機会を提供する。レビュー結果をClaudeに通知し、
 *   Issue内容への反映を促す。
 *
 * What:
 *   - gh issue createの成功を検出
 *   - Gemini/Codexによる同期レビューを実行
 *   - レビュー結果をIssueにコメント投稿
 *   - systemMessageでClaudeにレビュー結果を通知
 *
 * State:
 *   - writes: .claude/logs/flow/flow-progress-{session}.jsonl
 *
 * Remarks:
 *   - 同期実行（レビュー完了まで待機）
 *   - issue_ai_review.tsスクリプトを呼び出し（Bun経由）
 *   - PostToolUse:Bashで発火
 *
 * Changelog:
 *   - silenvx/dekita#3159: TypeScriptに移植
 */

import { existsSync } from "node:fs";
import { join } from "node:path";

import { FLOW_LOG_DIR } from "../lib/common";
import { TIMEOUT_HEAVY, TIMEOUT_LONG } from "../lib/constants";
import { completeFlowStep, registerFlowDefinition, startFlow } from "../lib/flow";
import { logHookExecution } from "../lib/logging";
import { type HookResult, makeApproveResult, outputResult } from "../lib/results";
import { type HookContext, createHookContext, getToolResult, parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";
import { stripQuotedStrings } from "../lib/strings";

// =============================================================================
// Constants
// =============================================================================

/** Minimum length for a suggestion content to be included */
const MIN_SUGGESTION_LENGTH = 10;
/** Maximum length for a single suggestion line before truncation */
const MAX_SUGGESTION_LENGTH = 150;
/** Length to truncate to (leaving room for ellipsis) */
const TRUNCATED_SUGGESTION_LENGTH = 147;
/** Maximum number of suggestions to include in the notification */
const MAX_SUGGESTIONS_COUNT = 5;

// =============================================================================
// Flow Definition Registration
// =============================================================================

// Register the issue-ai-review flow definition
registerFlowDefinition("issue-ai-review", {
  name: "Issue AIレビューフロー",
  description: "Issue作成後にAIレビューを実行し、フィードバックを反映する",
  steps: [
    { id: "review_posted", name: "AIレビュー投稿" },
    { id: "review_viewed", name: "レビュー確認" },
    { id: "issue_updated", name: "Issue更新" },
  ],
});

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Extract issue number from gh issue create output.
 */
export function extractIssueNumber(output: string): number | null {
  const match = output.match(/github\.com\/[^/]+\/[^/]+\/issues\/(\d+)/);
  if (match) {
    return Number.parseInt(match[1], 10);
  }
  return null;
}

/**
 * Check if command is a gh issue create command.
 */
export function isIssueCreateCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  const strippedCommand = stripQuotedStrings(command);
  return /gh\s+issue\s+create\b/.test(strippedCommand);
}

/**
 * Run AI reviews synchronously and return the review content.
 *
 * Calls issue_ai_review.ts which runs Gemini and Codex reviews,
 * then fetches the review comment from the issue.
 */
async function runAiReview(issueNumber: number, ctx: HookContext): Promise<string | null> {
  const projectDir = process.env.CLAUDE_PROJECT_DIR ?? process.cwd();
  const tsScriptsDir = join(projectDir, ".claude", "hooks", "ts", "scripts");
  const reviewScript = join(tsScriptsDir, "issue_ai_review.ts");

  if (!existsSync(reviewScript)) {
    await logHookExecution(
      "issue-ai-review",
      "approve",
      `Review script not found: ${reviewScript}`,
      undefined,
      { sessionId: ctx.sessionId ?? undefined },
    );
    return null;
  }

  // Run review script synchronously (may take up to 2+ minutes)
  try {
    const result = await asyncSpawn("bun", ["run", reviewScript, String(issueNumber)], {
      timeout: TIMEOUT_LONG * 1000,
    });

    if (result.exitCode !== 0) {
      await logHookExecution(
        "issue-ai-review",
        "approve",
        `Review script failed: ${result.stderr.slice(0, 200)}`,
        undefined,
        { sessionId: ctx.sessionId ?? undefined },
      );
      return null;
    }

    await logHookExecution(
      "issue-ai-review",
      "approve",
      `AI review completed for issue #${issueNumber}`,
      undefined,
      { sessionId: ctx.sessionId ?? undefined },
    );

    // Fetch the AI Review comment from the issue
    return fetchAiReviewComment(issueNumber);
  } catch (e) {
    const errorMessage = e instanceof Error ? e.message : String(e);
    await logHookExecution(
      "issue-ai-review",
      "approve",
      `Failed to run review: ${errorMessage}`,
      undefined,
      { sessionId: ctx.sessionId ?? undefined },
    );
    return null;
  }
}

/**
 * Fetch the latest AI Review comment from a GitHub issue.
 */
async function fetchAiReviewComment(issueNumber: number): Promise<string | null> {
  try {
    // Use jq to get only the last matching comment's body
    const result = await asyncSpawn(
      "gh",
      [
        "issue",
        "view",
        String(issueNumber),
        "--json",
        "comments",
        "--jq",
        '[.comments[] | select(.body | contains("🤖 AI Review"))] | last | .body',
      ],
      { timeout: TIMEOUT_HEAVY * 1000 },
    );

    const body = result.stdout.trim();

    // jq returns "null" when no matching comment exists
    if (result.exitCode !== 0 || !body || body === "null") {
      return null;
    }

    return body;
  } catch {
    return null;
  }
}

/**
 * Extract actionable edit suggestions from AI review content.
 *
 * Looks for patterns like:
 * - 「提案」「改善提案」「改善点」「推奨」keywords
 * - Bullet points after these keywords (e.g., "- suggestion")
 * - Numbered list items after these keywords (e.g., "1. suggestion")
 */
export function extractEditSuggestions(reviewContent: string): string[] {
  const suggestions: string[] = [];
  const lines = reviewContent.split("\n");

  // Track if we're in a suggestion section
  let inSuggestionSection = false;
  const keywords = ["提案", "改善提案", "改善点", "推奨"];

  for (const line of lines) {
    const stripped = line.trim();

    // Check if this is a bullet point
    const isBullet =
      stripped.startsWith("-") ||
      stripped.startsWith("*") ||
      stripped.startsWith("・") ||
      stripped.startsWith("•");

    // Check for numbered list (e.g., "1.", "2.", "10.")
    const numberedMatch = stripped.match(/^(\d+)\.\s*/);
    const isNumbered = numberedMatch !== null;

    if (inSuggestionSection) {
      if (isBullet) {
        const content = stripped.replace(/^[-*・•]\s*/, "").trim();
        if (content.length > MIN_SUGGESTION_LENGTH) {
          suggestions.push(content);
        }
      } else if (isNumbered) {
        const content = stripped.slice(numberedMatch[0].length).trim();
        if (content.length > MIN_SUGGESTION_LENGTH) {
          suggestions.push(content);
        }
      } else if (!stripped || stripped.startsWith("#")) {
        // Exit suggestion section on empty line or new header
        inSuggestionSection = false;
      }
    } else if (!isBullet && !isNumbered) {
      if (keywords.some((kw) => stripped.includes(kw))) {
        inSuggestionSection = true;
        // If this line itself contains a suggestion after colon
        let colonPos = -1;
        for (const c of [":", "："]) {
          const pos = stripped.indexOf(c);
          if (pos !== -1 && (colonPos === -1 || pos < colonPos)) {
            colonPos = pos;
          }
        }
        if (colonPos !== -1) {
          const afterColon = stripped.slice(colonPos + 1).trim();
          if (afterColon && afterColon.length > MIN_SUGGESTION_LENGTH) {
            suggestions.push(afterColon);
          }
        }
      }
    }

    // Limit suggestions
    if (suggestions.length >= 8) {
      break;
    }
  }

  return suggestions;
}

/**
 * Build a notification message for Claude about the AI review.
 */
function buildReviewNotification(issueNumber: number, reviewContent: string): string {
  const editSuggestions = extractEditSuggestions(reviewContent);

  // Build edit suggestions section
  let suggestionText: string;
  if (editSuggestions.length > 0) {
    const suggestionLines: string[] = [];
    for (const s of editSuggestions.slice(0, MAX_SUGGESTIONS_COUNT)) {
      if (s.length > MAX_SUGGESTION_LENGTH) {
        suggestionLines.push(`- ${s.slice(0, TRUNCATED_SUGGESTION_LENGTH)}...`);
      } else {
        suggestionLines.push(`- ${s}`);
      }
    }
    suggestionText = suggestionLines.join("\n");
  } else {
    suggestionText = "（具体的な編集提案なし - レビュー全文を確認してください）";
  }

  return `📋 Issue #${issueNumber} のAIレビューが完了しました。

**編集提案:**
${suggestionText}

**【自動実行】Task toolでSubAgentを起動してAIレビュー対応を実行してください:**

\`\`\`
Task tool parameters:
- subagent_type: "haiku"
- run_in_background: true
- prompt: "Issue #${issueNumber} のAIレビューを確認し、Issueを更新する。
  1. gh issue view ${issueNumber} --comments でレビュー確認
  2. レビュー内容に基づいてIssue本文を更新（gh issue edit ${issueNumber}）"
\`\`\`

SubAgentがバックグラウンドで処理するため、メインセッションは継続できます。`;
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: HookResult = makeApproveResult("issue-ai-review");

  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);
    const toolName = data.tool_name ?? "";
    const toolInput = data.tool_input ?? {};
    const toolResult = getToolResult(data) ?? {};

    // Only process Bash commands
    if (toolName !== "Bash") {
      await logHookExecution("issue-ai-review", "approve", `not Bash: ${toolName}`, undefined, {
        sessionId: ctx.sessionId,
      });
      outputResult(result);
      return;
    }

    const command = (toolInput as { command?: string }).command ?? "";

    // Check if this is a gh issue create command
    if (!isIssueCreateCommand(command)) {
      await logHookExecution("issue-ai-review", "approve", "not gh issue create", undefined, {
        sessionId: ctx.sessionId,
      });
      outputResult(result);
      return;
    }

    await logHookExecution("issue-ai-review", "approve", "gh issue create detected", undefined, {
      sessionId: ctx.sessionId,
    });

    // Only process if command succeeded
    const exitCode = (toolResult as { exit_code?: number }).exit_code ?? 0;
    if (exitCode !== 0) {
      await logHookExecution(
        "issue-ai-review",
        "approve",
        `Command failed: exit=${exitCode}`,
        undefined,
        { sessionId: ctx.sessionId ?? undefined },
      );
      outputResult(result);
      return;
    }

    // Extract issue number from stdout or output field
    const stdout =
      (toolResult as { stdout?: string }).stdout ??
      (toolResult as { output?: string }).output ??
      "";
    const issueNumber = extractIssueNumber(stdout);

    if (issueNumber) {
      // Run review synchronously and get content
      const reviewContent = await runAiReview(issueNumber, ctx);

      if (reviewContent) {
        // Start flow to track that Claude should review and update the issue
        const flowLogDir = FLOW_LOG_DIR;
        const flowInstanceId = await startFlow(
          flowLogDir,
          "issue-ai-review",
          { issue_number: issueNumber },
          ctx.sessionId,
        );

        if (flowInstanceId) {
          // Mark review_posted step as completed
          await completeFlowStep(
            flowLogDir,
            flowInstanceId,
            "review_posted",
            "issue-ai-review",
            ctx.sessionId,
          );
          await logHookExecution(
            "issue-ai-review",
            "approve",
            `Flow started: ${flowInstanceId}`,
            undefined,
            { sessionId: ctx.sessionId ?? undefined },
          );
        } else {
          await logHookExecution(
            "issue-ai-review",
            "approve",
            `Warning: Flow tracking failed for issue #${issueNumber}`,
            undefined,
            { sessionId: ctx.sessionId ?? undefined },
          );
        }

        // Notify Claude about the review via systemMessage
        const notification = buildReviewNotification(issueNumber, reviewContent);
        result.systemMessage = notification;

        await logHookExecution(
          "issue-ai-review",
          "approve",
          `Review notification sent for issue #${issueNumber}`,
          undefined,
          { sessionId: ctx.sessionId ?? undefined },
        );
      } else {
        await logHookExecution(
          "issue-ai-review",
          "approve",
          `No review content for issue #${issueNumber}`,
          undefined,
          { sessionId: ctx.sessionId ?? undefined },
        );
      }
    } else {
      // Log the tool_result structure for debugging
      const keys = Object.keys(toolResult);
      let preview: string;
      if (stdout) {
        const maxLen = 200;
        preview = stdout.slice(0, maxLen);
        if (stdout.length > maxLen) {
          preview += `...[len=${stdout.length}]`;
        }
      } else {
        preview = "empty";
      }
      await logHookExecution(
        "issue-ai-review",
        "approve",
        `No issue#. keys=${JSON.stringify(keys)}, cmd=${command}, out=${preview}`,
        undefined,
        { sessionId: ctx.sessionId ?? undefined },
      );
    }
  } catch (e) {
    const errorMessage = e instanceof Error ? e.message : String(e);
    await logHookExecution("issue-ai-review", "error", `Hook error: ${errorMessage}`);
  }

  outputResult(result);
}

if (import.meta.main) {
  main();
}
