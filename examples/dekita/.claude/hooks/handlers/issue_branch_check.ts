#!/usr/bin/env bun
/**
 * worktree作成時にブランチ名にIssue番号を含むことを強制。
 *
 * Why:
 *   Issueを作成せずにworktreeを作成すると、作業の追跡が困難になる。
 *   ブランチ名にIssue番号を含めることで、作業とIssueを紐付ける。
 *
 * What:
 *   - `git worktree add` コマンドを検出
 *   - ブランチ名にIssue番号（issue-123, #123等）が含まれているか確認
 *   - 含まれていない場合はブロック
 *
 * Remarks:
 *   - ブロック型フック（Issue番号なしはブロック）
 *   - PreToolUse:Bashで発火
 *
 * Changelog:
 *   - silenvx/dekita#2735: フック追加
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "issue-branch-check";

// Issue番号のパターン（issue-123, #123, Issue-123等）
const ISSUE_PATTERNS = [
  /issue-\d+/i, // issue-123, Issue-123, ISSUE-123
  /#\d+/, // #123
];

/**
 * git worktree addコマンドからブランチ名を抽出する。
 *
 * Supports:
 *   - git worktree add <path> -b <branch>
 *   - git worktree add --lock <path> -b <branch>
 *   - git worktree add -b <branch> <path>
 */
export function extractBranchName(command: string): string | null {
  // -b オプションの後のブランチ名を抽出
  const match = command.match(/-b\s+([^\s]+)/);
  if (match) {
    return match[1];
  }
  return null;
}

/**
 * ブランチ名にIssue番号が含まれているか確認する。
 */
export function hasIssueNumber(branchName: string): boolean {
  for (const pattern of ISSUE_PATTERNS) {
    if (pattern.test(branchName)) {
      return true;
    }
  }
  return false;
}

async function main(): Promise<void> {
  const hookInput = await parseHookInput();
  const sessionId = hookInput.session_id;
  const toolName = hookInput.tool_name || "";
  const toolInput = hookInput.tool_input || {};
  const command = (toolInput as { command?: string }).command || "";

  // Bashツール以外はスキップ
  if (toolName !== "Bash") {
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // git worktree addコマンド以外はスキップ
  if (!command.includes("git worktree add")) {
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // ブランチ名を抽出
  const branchName = extractBranchName(command);
  if (!branchName) {
    // -bオプションがない場合はスキップ（既存ブランチへのチェックアウト）
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Issue番号チェック
  if (hasIssueNumber(branchName)) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Branch name contains issue number: ${branchName}`,
      undefined,
      { sessionId },
    );
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Issue番号がない場合はブロック
  const message = `[issue-branch-check] ブランチ名にIssue番号が含まれていません。

**検出されたブランチ名**: \`${branchName}\`

**対処法**: 先にIssueを作成してから、ブランチ名にIssue番号を含めてください。

**正しいブランチ名の例**:
- \`docs/issue-2735-plugin-workflow\`
- \`feat/issue-123-add-feature\`
- \`fix/issue-456-bug-fix\`

**手順**:
1. \`gh issue create\` でIssueを作成
2. Issue番号を含むブランチ名でworktreeを作成
   \`\`\`
   git worktree add --lock .worktrees/issue-<番号> -b <type>/issue-<番号>-<description>
   \`\`\`

💡 ブロック後も作業を継続してください。
代替アクションのツール呼び出しを行い、テキストのみの応答で終わらないでください。`;

  await logHookExecution(
    HOOK_NAME,
    "block",
    `Branch name missing issue number: ${branchName}`,
    undefined,
    { sessionId },
  );
  console.log(JSON.stringify(makeBlockResult(HOOK_NAME, message)));
}

if (import.meta.main) {
  main();
}
