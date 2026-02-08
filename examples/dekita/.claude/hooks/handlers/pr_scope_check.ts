#!/usr/bin/env bun
/**
 * gh pr create時に1 Issue = 1 PRルールを強制。
 *
 * Why:
 *   複数Issueを1つのPRにまとめると、レビューが複雑になり、
 *   問題発生時のリバートが困難になり、変更履歴が不明確になる。
 *
 * What:
 *   - gh pr create コマンドを検出
 *   - PRタイトルからIssue参照を抽出
 *   - 複数Issue参照がある場合はブロック
 *   - 単一Issue参照はOKメッセージを表示
 *
 * Remarks:
 *   - ブロック型フック
 *   - -F/--body-file使用時は--titleも指定するよう警告
 *   - Python版: pr_scope_check.py
 *
 * Changelog:
 *   - silenvx/dekita#2986: TypeScript版に移植
 */

import { extractPrTitle, hasBodyFileOption } from "../lib/command";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "pr-scope-check";

/**
 * Check if command is a gh pr create command.
 *
 * Returns false for:
 * - Commands inside quoted strings (e.g., echo 'gh pr create')
 * - Empty commands
 */
export function isGhPrCreateCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }

  // Strip quoted strings to avoid false positives
  const strippedCommand = stripQuotedStrings(command);

  // Check if gh pr create exists in the stripped command
  return /gh\s+pr\s+create\b/.test(strippedCommand);
}

// extractPrTitle is imported from ../lib/command

/**
 * Count and return Issue references in the text.
 *
 * Returns list of Issue numbers found (e.g., ['#123', '#456']).
 */
export function countIssueReferences(text: string): string[] {
  // Match #xxx pattern (Issue number)
  const matches = text.matchAll(/#(\d+)/g);
  return Array.from(matches).map((m) => `#${m[1]}`);
}

// hasBodyFileOption is imported from ../lib/command

async function main(): Promise<void> {
  let result: { decision?: string; systemMessage?: string; reason?: string } = {};
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);
    sessionId = ctx.sessionId;
    const toolInput = (data.tool_input as Record<string, unknown>) ?? {};
    const command = (toolInput.command as string) ?? "";

    // Only check gh pr create commands
    if (!isGhPrCreateCommand(command)) {
      // Early return case - still log at end
    } else {
      // Extract PR title
      const title = extractPrTitle(command);
      if (!title) {
        // No title specified - approve (GitHub will prompt for title)
        // Warn if -F/--body-file is used without --title
        if (hasBodyFileOption(command)) {
          result.systemMessage =
            "⚠️ pr-scope-check: -F/--body-file使用時は--titleも指定してください。 " +
            "対話的に入力されたタイトルはチェックされません。";
        }
      } else {
        // Check for multiple Issue references
        const issues = countIssueReferences(title);
        if (issues.length > 1) {
          const reason = `PRタイトルに複数のIssue参照があります: ${issues.join(", ")}\n\n**1 Issue = 1 PR ルール**\n各Issueは独立したPRで対応してください。\n\n理由:\n- レビューが容易になる\n- 問題発生時のリバートが簡単\n- 変更履歴が明確になる\n\n対処方法:\n1. 現在のブランチで1つのIssueのみ対応\n2. 他のIssueは別のworktree/ブランチで対応\n3. 各Issue用に別々のPRを作成`;
          result = makeBlockResult(HOOK_NAME, reason);
        } else if (issues.length === 1) {
          result.systemMessage = `✅ pr-scope-check: 単一Issue参照OK (${issues[0]})`;
        }
      }
    }
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    result = {};
  }

  // Always log execution for accurate statistics
  await logHookExecution(HOOK_NAME, result.decision ?? "approve", result.reason, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(result));

  // Exit with appropriate code
  if (result.decision === "block") {
    process.exit(2);
  }
}

if (import.meta.main) {
  main();
}
