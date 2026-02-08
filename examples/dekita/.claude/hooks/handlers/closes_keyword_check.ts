#!/usr/bin/env bun
/**
 * gh pr create時にClosesキーワードの有無をチェックし、追加を提案する。
 *
 * Why:
 *   PRボディにClosesキーワードがないと、マージ時にIssueが自動クローズされず
 *   手動でのクローズ忘れにつながる。
 *
 * What:
 *   - ブランチ名からIssue番号を抽出
 *   - PRボディにCloses/Fixes/Resolvesキーワードがあるか確認
 *   - ない場合は追加を提案（ブロックしない）
 *
 * Remarks:
 *   - 提案型フック（ブロックしない、systemMessageで提案）
 *   - PreToolUse:Bashで発火（gh pr createコマンド）
 *   - --body-file/-Fオプション使用時はスキップ（ファイル内容は検査不可）
 *
 * Changelog:
 *   - silenvx/dekita#155: フック追加
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { extractPrBody, hasBodyFileOption } from "../lib/command";
import { formatError } from "../lib/format_error";
import { getCurrentBranch } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";
import { stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "closes-keyword-check";

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
 * Extract Issue number from branch name.
 *
 * Supports patterns like:
 * - fix/issue-123-description
 * - feature/123-description
 * - fix-123
 * - issue-123
 * - 123-description
 */
export function extractIssueFromBranch(branch: string): string | null {
  if (!branch) {
    return null;
  }

  const patterns = [
    /issue[/-](\d+)/i, // issue-123 or issue/123
    /(?:fix|feat|feature|bug|hotfix|chore|refactor)[/-](\d+)/i, // fix-123, feat/123
    /(?:^|\/)(\d+)(?:-|$)/, // /123- or 123- at start
  ];

  for (const pattern of patterns) {
    const match = branch.match(pattern);
    if (match) {
      return `#${match[1]}`;
    }
  }

  return null;
}

// extractPrBody and hasBodyFileOption are imported from ../lib/command

/**
 * Check if body contains a Closes keyword for the given issue.
 *
 * Recognizes GitHub keywords:
 * - Closes #xxx / Closes: #xxx
 * - Fixes #xxx / Fixes: #xxx
 * - Resolves #xxx / Resolves: #xxx
 * (case-insensitive)
 */
export function hasClosesKeyword(body: string, issueNumber: string): boolean {
  if (!body || !issueNumber) {
    return false;
  }

  // Extract just the number from #xxx
  const num = issueNumber.replace(/^#/, "");

  // GitHub keywords that auto-close issues
  // Supports both "Closes #123" and "Closes: #123" formats
  const pattern = new RegExp(`\\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\\s+#${num}\\b`, "i");
  return pattern.test(body);
}

async function main(): Promise<void> {
  let result: {
    decision?: string;
    reason?: string;
    systemMessage?: string;
  } = {};
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = data.tool_input || {};
    const command = (toolInput.command as string) || "";

    // Only check gh pr create commands
    if (isGhPrCreateCommand(command)) {
      // Get current branch
      const branch = await getCurrentBranch();
      if (branch) {
        // Extract Issue number from branch
        const issueNumber = extractIssueFromBranch(branch);
        if (issueNumber && !hasBodyFileOption(command)) {
          // Extract PR body - if null, body might come from template/editor
          const body = extractPrBody(command);
          if (body !== null) {
            // Check for Closes keyword
            if (hasClosesKeyword(body, issueNumber)) {
              result.systemMessage = `✅ closes-keyword-check: Closes ${issueNumber} が含まれています`;
            } else {
              result.systemMessage = `⚠️ closes-keyword-check: PRボディに \`Closes ${issueNumber}\` がありません\n\n**推奨**: PRボディに以下を追加してください:\n\`\`\`\nCloses ${issueNumber}\n\`\`\`\n\nこれにより、PRマージ時にIssue ${issueNumber}が自動closeされます。\n（ブランチ名 \`${branch}\` から推測）`;
            }
          }
        }
      }
    }
  } catch (error) {
    console.error(`[closes-keyword-check] Hook error: ${formatError(error)}`);
    result = {};
  }

  // Always log execution for accurate statistics
  logHookExecution(HOOK_NAME, result.decision ?? "approve", result.reason, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(result));
}

// Only run when executed directly, not when imported for testing
if (import.meta.main) {
  main();
}
