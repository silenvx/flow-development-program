#!/usr/bin/env bun
/**
 * 操作成功時に動作確認Issueのチェック項目を自動更新する。
 *
 * Why:
 *   作業中に自然と実行される操作（ビルド、テスト等）で動作確認を
 *   自動検証することで、まとめテストの負担を軽減する。
 *
 * What:
 *   - ツール実行成功を検出（Bash exit 0、Read成功等）
 *   - 対応する動作確認項目を特定
 *   - observation Issueのチェック項目を自動更新
 *   - 全Claude確認項目チェック時はIssueを自動クローズ
 *
 * State:
 *   - writes: GitHub Issue (observation label)
 *
 * Remarks:
 *   - 自動化型フック（ブロックしない、Issue自動更新）
 *   - PostToolUse:Bash/Readで発火
 *   - post-merge-observation-issue.pyがIssue作成（補完関係）
 *   - 人間確認項目（UI表示、モバイル等）は自動チェック対象外
 *
 * Changelog:
 *   - silenvx/dekita#2595: フック追加
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { getObservationIssues, runGhCommand } from "../lib/github";
import { logHookExecution } from "../lib/logging";
import { getToolResult, parseHookInput } from "../lib/session";

const HOOK_NAME = "observation-auto-check";

// Mapping from operation pattern to checklist item text
// Each entry: [tool_name, command_pattern, checklist_item_pattern]
// NOTE: checklist_item_pattern must be literal strings (no regex tokens like .*)
//       because update_checklist_item uses escaping for safety
const OPERATION_TO_CHECKLIST: Array<[string, RegExp, string]> = [
  // Hook execution log verification (.claude/hooks/ pattern)
  [
    "Bash",
    /(grep|cat).*.claude\/logs\/execution\/hook-execution.*\.jsonl/i,
    "フックが正しく発火する",
  ],
  // TypeScript/JavaScript build (frontend/src/ pattern)
  ["Bash", /pnpm\s+build|npm\s+run\s+build/i, "ビルドが成功する（`pnpm build`）"],
  // TypeScript/JavaScript tests (.test. pattern)
  ["Bash", /pnpm\s+test|npm\s+test|pnpm\s+test:ci/i, "テストが全てパスする（`pnpm test:ci`）"],
  // Type check (shared/ pattern)
  ["Bash", /pnpm\s+typecheck/i, "型定義の変更がfrontend/workerで正しく反映される"],
  // Script execution with --help (.claude/scripts/ pattern)
  ["Bash", /python3?\s+.*\.py\s+--help/i, "ヘルプオプション（--help）が動作する"],
  // Script execution (.claude/scripts/ pattern)
  ["Bash", /\.claude\/scripts\/.*\.py\b/i, "スクリプトが正常に実行できる"],
  // API health check (worker/src/ pattern)
  ["Bash", /curl.*?api\.dekita\.app\/health/i, "APIが正常にレスポンスを返す"],
  // GitHub Actions run check
  ["Bash", /gh\s+run\s+list/i, "CIが正常に動作する"],
  // Settings verification (any successful read/edit of settings.json)
  ["Read", /settings\.json/i, "設定変更が反映される"],
];

// 人間確認項目 - 自動チェック対象外
// 注: 将来の人間確認項目フィルタリング用に保持
const _HUMAN_ITEMS_PATTERNS = [
  "UI表示",
  "モバイル表示",
  "アクセシビリティ",
  "体感確認",
  "実機",
  "目視",
  "エラーハンドリング",
  "エラーレスポンス",
];

/**
 * チェックリスト項目が人間確認を必要とするか判定する。
 * 注: 将来の人間確認項目フィルタリング用に保持
 */
function _isHumanItem(item: string): boolean {
  return _HUMAN_ITEMS_PATTERNS.some((pattern) => item.includes(pattern));
}

/**
 * Find the checklist item that matches the operation.
 * @internal Exported for testing
 */
export function findMatchingChecklistItem(toolName: string, commandOrPath: string): string | null {
  for (const [expectedTool, pattern, checklistItem] of OPERATION_TO_CHECKLIST) {
    if (toolName !== expectedTool) {
      continue;
    }
    if (pattern.test(commandOrPath)) {
      return checklistItem;
    }
  }
  return null;
}

/**
 * Get the body of an issue.
 */
async function getIssueBody(issueNumber: number): Promise<string | null> {
  try {
    const [success, stdout] = await runGhCommand(
      ["issue", "view", String(issueNumber), "--json", "body"],
      TIMEOUT_LIGHT * 1000,
    );
    if (!success) {
      return null;
    }
    const data = JSON.parse(stdout);
    return data.body ?? "";
  } catch {
    return null;
  }
}

/**
 * Update a checklist item from unchecked to checked.
 * @internal Exported for testing
 */
export function updateChecklistItem(
  body: string,
  itemPattern: string,
): { success: boolean; body: string } {
  // Find unchecked item matching the pattern
  // Pattern: "- [ ] <item text containing pattern>"
  const escapedPattern = itemPattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const uncheckedPattern = new RegExp(`(- \\[ \\] )([^\n]*${escapedPattern}[^\n]*)`, "i");
  const match = body.match(uncheckedPattern);

  if (!match) {
    // Item not found or already checked
    return { success: false, body };
  }

  // Replace with checked version
  const fullMatch = match[0];
  const checkedVersion = fullMatch.replace("- [ ] ", "- [x] ");
  const updatedBody = body.replace(fullMatch, checkedVersion);

  return { success: true, body: updatedBody };
}

/**
 * Count total and checked Claude Code verification items.
 * @internal Exported for testing
 */
export function countClaudeItems(body: string): { total: number; checked: number } {
  // Find the Claude Code verification section
  const claudeSectionMatch = body.match(/## Claude Code確認項目\n+((?:- \[[ x]\] [^\n]+\n?)+)/m);

  if (!claudeSectionMatch) {
    return { total: 0, checked: 0 };
  }

  const section = claudeSectionMatch[1];

  // Count items
  const totalMatches = section.match(/- \[[ x]\] /g);
  const checkedMatches = section.match(/- \[x\] /g);

  return {
    total: totalMatches?.length ?? 0,
    checked: checkedMatches?.length ?? 0,
  };
}

/**
 * Update the issue body via GitHub API.
 */
async function updateIssueBody(issueNumber: number, newBody: string): Promise<boolean> {
  try {
    const [success] = await runGhCommand(
      ["issue", "edit", String(issueNumber), "--body", newBody],
      TIMEOUT_MEDIUM * 1000,
    );
    return success;
  } catch {
    return false;
  }
}

/**
 * Close the issue as completed.
 */
async function closeIssue(issueNumber: number): Promise<boolean> {
  try {
    const [success] = await runGhCommand(
      ["issue", "close", String(issueNumber), "--reason", "completed"],
      TIMEOUT_LIGHT * 1000,
    );
    return success;
  } catch {
    return false;
  }
}

/**
 * Find and update observation issues with matching checklist items.
 */
async function processObservationIssues(
  checklistPattern: string,
): Promise<Array<{ issueNumber: number; wasClosed: boolean }>> {
  const updatedIssues: Array<{ issueNumber: number; wasClosed: boolean }> = [];

  // Get open observation issues
  const issues = await getObservationIssues(50, ["number", "title"]);

  for (const issue of issues) {
    const issueNumber = issue.number;
    if (!issueNumber) {
      continue;
    }

    // Get issue body
    const body = await getIssueBody(issueNumber);
    if (!body) {
      continue;
    }

    // Check if this pattern exists as unchecked item
    const { success, body: updatedBody } = updateChecklistItem(body, checklistPattern);

    if (!success) {
      continue;
    }

    // Update the issue body
    if (!(await updateIssueBody(issueNumber, updatedBody))) {
      continue;
    }

    // Check if all claude items are now checked
    const { total, checked } = countClaudeItems(updatedBody);
    const shouldClose = total > 0 && total === checked;

    if (shouldClose) {
      await closeIssue(issueNumber);
    }

    updatedIssues.push({ issueNumber, wasClosed: shouldClose });
  }

  return updatedIssues;
}

async function main(): Promise<void> {
  const hookInput = await parseHookInput();
  const sessionId = hookInput.session_id;
  const toolName = hookInput.tool_name ?? "";
  const toolInput = (hookInput.tool_input ?? {}) as Record<string, unknown>;
  const rawResult = getToolResult(hookInput);

  // null/undefinedはスキップ
  if (rawResult === null || rawResult === undefined) {
    return;
  }

  // tool_resultの型に応じて処理を分岐:
  // - Bash: 常にオブジェクト { exit_code, stdout, stderr }
  // - Read成功: string型（ファイル内容）
  // - Read失敗: { is_error: true, ... }
  let toolResult: Record<string, unknown> = {};
  let isReadSuccess = false;

  if (typeof rawResult === "object" && !Array.isArray(rawResult)) {
    toolResult = rawResult as Record<string, unknown>;
  } else if (typeof rawResult === "string") {
    // Read成功: 文字列はファイル内容なので正常終了を明示
    isReadSuccess = true;
  } else {
    // 配列など予期しない型はスキップ
    return;
  }

  // Get command or file path based on tool type
  let commandOrPath = "";

  if (toolName === "Bash") {
    commandOrPath = (toolInput.command as string) ?? "";
  } else if (toolName === "Read") {
    commandOrPath = (toolInput.file_path as string) ?? "";
  } else {
    // Only process Bash and Read for now
    return;
  }

  if (!commandOrPath) {
    return;
  }

  // Check if operation succeeded
  // Bashツールの場合、exit_codeフィールドが存在し数値であることを要求
  if (toolName === "Bash") {
    if (!("exit_code" in toolResult) || typeof toolResult.exit_code !== "number") {
      // exit_codeフィールドが欠落または不正な場合はスキップ（安全側の動作）
      return;
    }
    if (toolResult.exit_code !== 0) {
      return;
    }
  }

  // For Read, check for errors (isReadSuccessが明示されていれば成功確定)
  if (toolName === "Read" && !isReadSuccess && toolResult.is_error) {
    return;
  }

  // Find matching checklist item
  const checklistPattern = findMatchingChecklistItem(toolName, commandOrPath);
  if (!checklistPattern) {
    return;
  }

  // Process observation issues
  const updated = await processObservationIssues(checklistPattern);

  if (updated.length > 0) {
    for (const { issueNumber, wasClosed } of updated) {
      if (wasClosed) {
        console.log(
          `\n[observation-auto-check] 動作確認Issue #${issueNumber} を自動クローズしました（全項目チェック完了）`,
        );
        await logHookExecution(
          HOOK_NAME,
          "approve",
          `auto-closed issue #${issueNumber} (all items checked)`,
          undefined,
          { sessionId },
        );
      } else {
        console.log(
          `\n[observation-auto-check] Issue #${issueNumber} の` +
            `「${checklistPattern}」をチェックしました`,
        );
        await logHookExecution(
          HOOK_NAME,
          "approve",
          `checked '${checklistPattern}' in issue #${issueNumber}`,
          undefined,
          { sessionId },
        );
      }
    }
  }
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error:`, e);
    process.exit(0);
  });
}
