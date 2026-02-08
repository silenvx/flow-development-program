#!/usr/bin/env bun
/**
 * PR作成時に未完了タスクのあるIssueをCloseしようとしていないかチェックする。
 *
 * Why:
 *   受け入れ条件が未完了のIssueをPRでCloseすると、タスクが未完のままクローズされる。
 *   PR作成時に検出することで、マージ前に対処できる。
 *
 * What:
 *   - gh pr createコマンドを検出
 *   - PRボディからCloses/Fixes #xxxを抽出
 *   - 対象Issueの受け入れ条件（チェックボックス）を確認
 *   - 未完了項目がありIssue参照がない場合はブロック
 *
 * Remarks:
 *   - ブロック型フック（未完了タスクClose時はブロック）
 *   - PreToolUse:Bashで発火（gh pr createコマンド）
 *   - 取り消し線付き項目は完了扱い（Issue #823）
 *   - 未完了項目に別Issue参照があれば許可（段階的実装パターン）
 *
 * Changelog:
 *   - silenvx/dekita#1986: フック追加
 *   - silenvx/dekita#823: 取り消し線の扱い
 *   - silenvx/dekita#3160: TypeScriptに移植
 */

import { extractPrBody } from "../lib/command";
import { formatError } from "../lib/format_error";
import {
  extractIssueNumbersFromPrBody,
  fetchIssueAcceptanceCriteria,
  hasIssueReference,
} from "../lib/issue_checker";
import { logHookExecution } from "../lib/logging";
import { approveAndExit, blockAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { splitCommandChain, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "closes-scope-check";

// =============================================================================
// Types
// =============================================================================

interface IssueWithProblems {
  issueNumber: string;
  title: string;
  uncheckedItems: string[];
  totalUnchecked: number;
}

// =============================================================================
// Command Detection
// =============================================================================

/**
 * Check if command is a gh pr create invocation.
 */
function isPrCreateCommand(command: string): boolean {
  const stripped = stripQuotedStrings(command);
  const parts = splitCommandChain(stripped);

  for (const part of parts) {
    if (/^\s*gh\s+pr\s+create\b/.test(part)) {
      return true;
    }
  }
  return false;
}

// extractPrBody is imported from ../lib/command

// =============================================================================
// Issue Checking
// =============================================================================

/**
 * Check each Issue for unchecked items without Issue references.
 *
 * Uses fetchIssueAcceptanceCriteria from issue_checker for consistency
 * with merge-time checks (including strikethrough handling per Issue #823).
 */
async function checkIssuesForIncompleteItems(issueNumbers: string[]): Promise<IssueWithProblems[]> {
  const issuesWithProblems: IssueWithProblems[] = [];

  for (const issueNum of issueNumbers) {
    const result = await fetchIssueAcceptanceCriteria(issueNum);
    if (!result.success) {
      continue;
    }

    // Find unchecked items that don't have Issue references
    // isCompleted already handles [x] marks and strikethrough items
    const problematicItems: string[] = [];
    for (const item of result.criteria) {
      if (!item.isCompleted && !hasIssueReference(item.text)) {
        problematicItems.push(item.text);
      }
    }

    if (problematicItems.length > 0) {
      issuesWithProblems.push({
        issueNumber: issueNum,
        title: result.title,
        uncheckedItems: problematicItems.slice(0, 5), // Show max 5
        totalUnchecked: problematicItems.length,
      });
    }
  }

  return issuesWithProblems;
}

// =============================================================================
// Message Formatting
// =============================================================================

/**
 * Format the blocking message with issues and guidance.
 */
function formatBlockMessage(issuesWithProblems: IssueWithProblems[]): string {
  const lines: string[] = [
    "[closes-scope-check] PRが未完了タスクのあるIssueをクローズしようとしています。",
    "",
  ];

  for (const issue of issuesWithProblems) {
    lines.push(`**Issue #${issue.issueNumber}**: ${issue.title}`);
    lines.push(`未完了項目（${issue.totalUnchecked}件）:`);

    for (const item of issue.uncheckedItems) {
      // Truncate long items
      const displayItem = item.length > 60 ? `${item.slice(0, 60)}...` : item;
      lines.push(`  - [ ] ${displayItem}`);
    }

    if (issue.totalUnchecked > issue.uncheckedItems.length) {
      lines.push(`  - ... 他 ${issue.totalUnchecked - issue.uncheckedItems.length} 件`);
    }
    lines.push("");
  }

  lines.push(
    "**対処方法（いずれかを選択）**:",
    "",
    "1. **全タスクを完了する場合**",
    "   → 全てのチェックボックスをチェック状態にしてからPR作成",
    "",
    "2. **一部のみ実装する場合**",
    "   a. 残りタスク用のIssueを作成: `gh issue create`",
    "   b. 元のIssueで未完了項目に「→ #XXXX」とリンクを追記",
    "   c. 「Closes」の代わりに「Refs」を使用",
    "   d. 新Issueで残りを対応後、元Issueを手動クローズ",
    "",
    "3. **サブIssueパターンを使用する場合**",
    "   a. 各タスクをサブIssueとして作成",
    "   b. PRは「Closes #(サブIssue番号)」を使用",
    "   c. 親Issueは全サブIssue完了後に手動クローズ",
    "",
    "**重要**: 未完了項目を「→ スコープ外」と書くだけでは不十分です。",
    "必ず別Issueへのリンク（#番号）を含めてください。",
  );

  return lines.join("\n");
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const command = (data.tool_input?.command as string) ?? "";

    // Only check gh pr create commands
    if (!isPrCreateCommand(command)) {
      await logHookExecution(HOOK_NAME, "skip", "Not a PR create command", undefined, {
        sessionId,
      });
      approveAndExit(HOOK_NAME);
    }

    // Extract PR body from command
    const prBody = extractPrBody(command);
    if (!prBody) {
      await logHookExecution(HOOK_NAME, "skip", "No PR body found", undefined, { sessionId });
      approveAndExit(HOOK_NAME);
    }

    // Extract Issue numbers from Closes/Fixes patterns
    const issueNumbers = extractIssueNumbersFromPrBody(prBody!);
    if (issueNumbers.length === 0) {
      await logHookExecution(HOOK_NAME, "skip", "No Closes/Fixes Issues found", undefined, {
        sessionId,
      });
      approveAndExit(HOOK_NAME);
    }

    // Check each Issue for incomplete items
    const issuesWithProblems = await checkIssuesForIncompleteItems(issueNumbers);

    if (issuesWithProblems.length > 0) {
      const message = formatBlockMessage(issuesWithProblems);
      await logHookExecution(
        HOOK_NAME,
        "block",
        `Issues with incomplete items: ${issuesWithProblems.map((i) => i.issueNumber).join(", ")}`,
        undefined,
        { sessionId },
      );
      blockAndExit(HOOK_NAME, message);
    } else {
      await logHookExecution(HOOK_NAME, "approve", "All Issues have complete criteria", undefined, {
        sessionId,
      });
      approveAndExit(HOOK_NAME);
    }
  } catch (e) {
    // On error, don't block (fail open)
    console.error(`[${HOOK_NAME}] Hook error:`, e);
    await logHookExecution(HOOK_NAME, "error", `${formatError(e)}`, undefined, { sessionId });
    approveAndExit(HOOK_NAME);
  }
}

if (import.meta.main) {
  main();
}
