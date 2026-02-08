#!/usr/bin/env bun
/**
 * PR/Issue説明文に「後で」系キーワードがIssue参照なしで含まれる場合にブロックする。
 *
 * Why:
 *   「別途対応予定」「将来改善」等の表現がIssue参照なしで使用されると、
 *   対応が忘れられ、技術的負債が蓄積する。defer_keyword_check.pyは
 *   transcriptをチェックするがPR/Issue説明文は対象外だった。
 *
 * What:
 *   - gh pr create/gh issue createコマンドの--body引数を検出
 *   - 「後で」系キーワードを正規表現でチェック
 *   - Issue参照（#数字）が近くにない場合はブロック
 *
 * Remarks:
 *   - ブロック型フック（PreToolUse:Bash）
 *   - defer_keyword_check.pyと同じキーワードパターンを使用
 *   - Issue参照が100文字以内にあれば許可
 *
 * Changelog:
 *   - silenvx/dekita#2896: フック追加
 */

import { extractPrBody, hasBodyFileOption } from "../lib/command";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "pr-defer-check";

// 「後で」系キーワード（defer_keyword_check.pyから移植）
const DEFER_KEYWORDS = [
  // スコープ外パターン
  /スコープ外(?:のため|なので|として)/,
  /本PRのスコープ外/,
  // 別途対応パターン
  /別途対応(?:します|する|が必要|予定)/,
  /別(?:で|途)(?:Issue|issue)?(?:対応|実装)?予定/,
  // 将来対応パターン（Python版と一貫性を保つ）
  /将来(?:的に|の改善|の課題)/,
  // フォローアップパターン
  /フォローアップ(?:として|で|が|予定)/,
  // 対応予定パターン
  /(?:で|として)対応予定/,
  // 追加パターン（PR #2884で検出されたもの）
  /(?:別|今後)(?:で|として|にて)?対応/,
  /後(?:ほど|で)(?:対応|実装|追加)/,
];

// Issue参照パターン
const ISSUE_REFERENCE_PATTERN = /#\d+/;

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
 * Check if command is a gh issue create command.
 */
export function isGhIssueCreateCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  const strippedCommand = stripQuotedStrings(command);
  return /gh\s+issue\s+create\b/.test(strippedCommand);
}

// hasBodyFileOption and extractPrBody are imported from ../lib/command
// Note: extractPrBody is used instead of local extractBody (same functionality)

/**
 * Check if there's an Issue reference nearby in the text.
 * @param matchLength - Length of the matched keyword to include in "after" context
 */
function hasIssueReferenceNearby(
  text: string,
  matchPos: number,
  matchLength: number,
  window = 100,
): boolean {
  const start = Math.max(0, matchPos - window);
  // Calculate end relative to the end of the keyword, not its start
  const end = Math.min(text.length, matchPos + matchLength + window);
  const context = text.slice(start, end);
  return ISSUE_REFERENCE_PATTERN.test(context);
}

/**
 * Check body content for defer keywords without Issue reference.
 */
export function checkDeferKeywords(body: string): {
  violations: Array<{ keyword: string; context: string }>;
} {
  const violations: Array<{ keyword: string; context: string }> = [];

  for (const pattern of DEFER_KEYWORDS) {
    // Use .source to get pattern string, as matchAll with flags requires string input
    const matches = body.matchAll(new RegExp(pattern.source, "g"));
    for (const match of matches) {
      if (
        match.index !== undefined &&
        !hasIssueReferenceNearby(body, match.index, match[0].length)
      ) {
        violations.push({
          keyword: match[0],
          context: body.slice(Math.max(0, match.index - 30), match.index + match[0].length + 30),
        });
      }
    }
  }

  return { violations };
}

/**
 * Format the block message for defer keyword violations.
 */
function formatBlockMessage(
  violations: Array<{ keyword: string; context: string }>,
  isIssue: boolean,
): string {
  const target = isIssue ? "Issue" : "PR";
  const examples = violations.slice(0, 3);
  const exampleText = examples.map((v) => `  - 「${v.keyword}」`).join("\n");

  return `${target}説明文に「後で」系キーワードがIssue参照なしで使用されています:
${exampleText}

**対処方法:**
1. 対応するIssue番号を追加する（例: #123）
2. または具体的なアクション・期限に変更する

**背景:**
「後で」「将来」「フォローアップ」等の発言時は、必ずIssue番号を含めてください。
Issue参照がないと対応が忘れられます。`;
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
    const ctx = createHookContext(data);
    sessionId = ctx.sessionId;
    const toolInput = data.tool_input || {};
    const command = (toolInput.command as string) || "";

    const isPrCreate = isGhPrCreateCommand(command);
    const isIssueCreate = isGhIssueCreateCommand(command);

    if (isPrCreate || isIssueCreate) {
      const target = isPrCreate ? "PR" : "Issue";

      if (hasBodyFileOption(command)) {
        result.systemMessage =
          "⚠️ pr-defer-check: -F/--body-file使用時は「後で」キーワードチェック不可。" +
          "Issue参照なしの「後で」「将来」等を使用しないでください。";
      } else {
        const body = extractPrBody(command);
        if (body !== null) {
          const { violations } = checkDeferKeywords(body);
          if (violations.length > 0) {
            const reason = formatBlockMessage(violations, isIssueCreate);
            result = makeBlockResult(HOOK_NAME, reason);
          } else {
            result.systemMessage = `✅ pr-defer-check: ${target}説明文に問題なし`;
          }
        }
        // No body specified - interactive mode, skip check
      }
    }
  } catch (error) {
    console.error(`[pr-defer-check] Hook error: ${formatError(error)}`);
    result = {};
  }

  // Log hook execution for all decisions
  logHookExecution(HOOK_NAME, result.decision ?? "approve", result.reason, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
