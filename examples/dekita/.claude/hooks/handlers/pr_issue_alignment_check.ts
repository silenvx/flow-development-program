#!/usr/bin/env bun
/**
 * PR作成時に対象Issueの受け入れ条件を検証する。
 *
 * Why:
 *   - Issue #538 was closed with a PR that implemented something different
 *   - Issue #590 was closed with a PR that only added debug logs (not a fix)
 *   受け入れ条件を可視化し、不完全な状態でのクローズを防止する。
 *
 * What:
 *   - gh pr create コマンドを検出
 *   - Closes/Fixes キーワードからIssue番号を抽出
 *   - Issue内容と受け入れ条件を取得
 *   - 未完了の条件がある場合は警告
 *
 * Remarks:
 *   - 非ブロック型（警告のみ）
 *   - closes-validation.pyはキーワード検証、このフックは内容検証
 *   - Python版: pr_issue_alignment_check.py
 *
 * Changelog:
 *   - silenvx/dekita#543: フック追加
 *   - silenvx/dekita#592: 受け入れ条件チェック強化
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { createHookContext, parseHookInput } from "../lib/session";
import { stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "pr-issue-alignment-check";
const MAX_ISSUE_BODY_LENGTH = 1000;

/**
 * Extract acceptance criteria (checkbox items) from Issue body.
 */
export function extractAcceptanceCriteria(body: string): Array<[boolean, string]> {
  const criteria: Array<[boolean, string]> = [];

  // Match checkbox items: - [ ] or - [x] or - [X]
  // Also handles * [ ] format
  const pattern = /^[\s]*[-*]\s*\[([ xX])\]\s*(.+)$/;

  for (const line of body.split("\n")) {
    const match = line.match(pattern);
    if (match) {
      const isCompleted = match[1].toLowerCase() === "x";
      const criteriaText = match[2].trim();
      criteria.push([isCompleted, criteriaText]);
    }
  }

  return criteria;
}

/**
 * Format status message for Issue acceptance criteria.
 */
export function formatAcceptanceCriteriaMessage(
  issueNum: string,
  title: string,
  criteria: Array<[boolean, string]>,
  isClosed = false,
): string {
  const incomplete = criteria.filter(([isCompleted]) => !isCompleted).map(([, text]) => text);
  const completedItems = criteria.filter(([isCompleted]) => isCompleted).map(([, text]) => text);

  let header = `### Issue #${issueNum}: ${title}`;
  if (isClosed) {
    header += " (CLOSED)";
  }

  const lines: string[] = [header, ""];

  if (isClosed && incomplete.length > 0) {
    lines.push("ℹ️ *このIssueは既にクローズ済みです。`Closes #N` は効果がありません。*");
    lines.push("");
  }

  if (incomplete.length > 0) {
    lines.push(`❌ **未完了の受け入れ条件: ${incomplete.length}件**`);
    for (const text of incomplete) {
      lines.push(`  - [ ] ${text}`);
    }
    lines.push("");
  }

  if (completedItems.length > 0) {
    lines.push(`✅ 完了済み: ${completedItems.length}件`);
    for (const text of completedItems) {
      lines.push(`  - [x] ${text}`);
    }
  }

  return lines.join("\n");
}

/**
 * Extract issue numbers from Closes/Fixes keywords in PR body.
 */
export function extractIssueNumbersFromBody(command: string): string[] {
  let body: string | null = null;

  // Try HEREDOC pattern first (most common in this project)
  // Matches: --body "$(cat <<'EOF' ... EOF )"
  const heredocMatch = command.match(/--body\s+"\$\(cat\s+<<['"]?EOF['"]?\s*(.*?)\s*EOF\s*\)"/s);
  if (heredocMatch) {
    body = heredocMatch[1];
  }

  // Try double-quoted body (may contain escaped quotes)
  if (body === null) {
    const dqMatch = command.match(/--body\s+"((?:[^"\\]|\\.)*)"/);
    if (dqMatch) {
      body = dqMatch[1];
    }
  }

  // Try single-quoted body (may contain any chars except single quote)
  if (body === null) {
    const sqMatch = command.match(/--body\s+'([^']*)'/);
    if (sqMatch) {
      body = sqMatch[1];
    }
  }

  if (body === null) {
    return [];
  }

  // Find Closes #XXX, Fixes #XXX, Resolves #XXX patterns
  // Case insensitive, handles multiple issues, allows optional colon
  const pattern = /(?:closes?|fix(?:es)?|resolves?):?\s+#(\d+)/gi;
  const matches: string[] = [];
  for (let match = pattern.exec(body); match !== null; match = pattern.exec(body)) {
    matches.push(match[1]);
  }

  return [...new Set(matches)]; // Remove duplicates
}

/**
 * Check if command is gh pr create.
 */
export function isPrCreateCommand(command: string): boolean {
  const cmd = stripQuotedStrings(command);
  return /gh\s+pr\s+create\b/.test(cmd);
}

/**
 * Fetch issue title, body and state using gh CLI.
 */
function fetchIssueContent(issueNumber: string): [boolean, string, string, string] {
  try {
    const result = execSync(`gh issue view ${issueNumber} --json title,body,state`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    const data = JSON.parse(result);
    const title = data.title || "";
    const body = data.body || ""; // Handle null body
    const state = data.state || "OPEN";
    return [true, title, body, state];
  } catch {
    return [false, "", "", ""];
  }
}

interface ApproveResult {
  systemMessage?: string;
}

async function main(): Promise<void> {
  const result: ApproveResult = {};
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);
    sessionId = ctx.sessionId;
    const toolName = (data.tool_name as string) || "";

    if (toolName !== "Bash") {
      await logHookExecution(HOOK_NAME, "approve", `not Bash: ${toolName}`, undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    const toolInput = (data.tool_input as Record<string, unknown>) || {};
    const command = (toolInput.command as string) || "";

    // Check if this is a gh pr create command
    if (!isPrCreateCommand(command)) {
      await logHookExecution(HOOK_NAME, "approve", "not gh pr create", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Extract issue numbers from body
    const issueNumbers = extractIssueNumbersFromBody(command);
    if (issueNumbers.length === 0) {
      await logHookExecution(HOOK_NAME, "approve", "no issue numbers in body", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Fetch and analyze issue content for each issue
    const messages: string[] = [];
    let hasIncompleteOpenCriteria = false;
    let totalIncompleteOpen = 0;
    const closedIssuesWithIncomplete: string[] = [];

    for (const issueNum of issueNumbers) {
      const [success, title, body, state] = fetchIssueContent(issueNum);
      if (!success) {
        continue;
      }

      const isClosed = state === "CLOSED";

      // Extract and check acceptance criteria
      const criteria = extractAcceptanceCriteria(body);

      if (criteria.length > 0) {
        // Has acceptance criteria - format with completion status
        const incompleteCount = criteria.filter(([isCompleted]) => !isCompleted).length;
        if (incompleteCount > 0) {
          if (isClosed) {
            closedIssuesWithIncomplete.push(issueNum);
          } else {
            hasIncompleteOpenCriteria = true;
            totalIncompleteOpen += incompleteCount;
          }
        }

        const statusMsg = formatAcceptanceCriteriaMessage(issueNum, title, criteria, isClosed);
        messages.push(statusMsg);
      } else {
        // No acceptance criteria - show issue content for reference
        let displayBody = body;
        if (displayBody.length > MAX_ISSUE_BODY_LENGTH) {
          displayBody = `${displayBody.slice(0, MAX_ISSUE_BODY_LENGTH)}\n...`;
        }
        let header = `### Issue #${issueNum}: ${title}`;
        if (isClosed) {
          header += " (CLOSED)";
        }
        messages.push(`${header}\n\n（受け入れ条件なし）\n\n${displayBody}`);
      }
    }

    if (messages.length > 0) {
      if (hasIncompleteOpenCriteria) {
        // Strong warning for incomplete criteria on OPEN issues
        result.systemMessage = `🚨 **警告: 未完了の受け入れ条件があります！**\n\n❌ このPRでクローズされる全てのIssueの受け入れ条件のうち、合計 ${totalIncompleteOpen} 件が未完了です。\n\n${messages.join("\n\n---\n\n")}\n\n⚠️ **このPRをマージすると、Issueが不完全な状態でクローズされる可能性があります。**\n\n確認してください:\n1. 実装内容がIssueの全ての要求を満たしていますか？\n2. 未完了の項目は意図的に対象外としていますか？\n3. Issueの受け入れ条件を更新する必要がありますか？`;
        await logHookExecution(
          HOOK_NAME,
          "approve",
          `未完了の受け入れ条件あり: ${totalIncompleteOpen}件 (#${issueNumbers.join(", #")})`,
          undefined,
          { sessionId },
        );
      } else if (closedIssuesWithIncomplete.length > 0) {
        // Info message for closed issues with incomplete criteria
        result.systemMessage = `ℹ️ **PR作成前のIssue確認**\n\n${messages.join("\n\n---\n\n")}\n\n💡 Issue #${closedIssuesWithIncomplete.join(", #")} は既にクローズ済みのため、\`Closes #N\` は効果がありません。`;
        await logHookExecution(
          HOOK_NAME,
          "approve",
          `CLOSED Issueへの参照: #${closedIssuesWithIncomplete.join(", #")}`,
          undefined,
          { sessionId },
        );
      } else {
        // Info message when all criteria complete or no criteria
        result.systemMessage = `✅ **PR作成前のIssue確認**\n\n${messages.join("\n\n---\n\n")}\n\n💡 実装内容がIssueの要求と一致していることを確認してください。`;
        await logHookExecution(
          HOOK_NAME,
          "approve",
          `受け入れ条件確認: #${issueNumbers.join(", #")}`,
          undefined,
          { sessionId },
        );
      }
    }
  } catch (error) {
    // Don't block on errors
    await logHookExecution(HOOK_NAME, "error", `フックエラー: ${formatError(error)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
