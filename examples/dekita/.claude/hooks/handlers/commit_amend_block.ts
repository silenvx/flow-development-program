#!/usr/bin/env bun
/**
 * mainリポジトリでのgit commit --amendをブロックする。
 *
 * Why:
 *   mainブランチの履歴を変更すると、他のworktreeやリモートと不整合が発生する。
 *   誤操作を防ぐため、mainリポジトリでの--amendは禁止する。
 *
 * What:
 *   - git commit --amendコマンドを検出
 *   - worktree内での実行は許可
 *   - mainリポジトリでの実行はブロック
 *   - workteeへの移動手順を提示
 *
 * Remarks:
 *   - ブロック型フック（mainリポジトリでの--amendはブロック）
 *   - PreToolUse:Bashで発火（git commitコマンド）
 *   - checkout-block.pyと同様のworktree判定ロジック
 *
 * Changelog:
 *   - silenvx/dekita#1368: mainブランチでのgit commit --amend誤操作防止
 *   - silenvx/dekita#2874: TypeScriptに移植
 *   - silenvx/dekita#2894: isInWorktree/isMainRepositoryをlib/git.tsに集約
 */

import { formatError } from "../lib/format_error";
import { isInWorktree, isMainRepository } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { splitCommandChain, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "commit-amend-block";

// Pattern to match git global options that can appear between 'git' and the subcommand
const GIT_GLOBAL_OPTIONS =
  "(?:\\s+(?:-[CcOo]\\s*\\S+|--[\\w-]+=\\S+|" +
  "--[\\w-]+\\s+(?!commit\\b)\\S+|--[\\w-]+|-[pPhv]|-\\d+))*";

/**
 * Check if command contains git commit --amend.
 *
 * Handles:
 *   git commit --amend
 *   git commit --amend -m "message"
 *   git commit -m "message" --amend
 *   git -C path commit --amend
 */
export function containsAmendFlag(command: string): boolean {
  // Strip quoted strings to avoid false positives like: echo "git commit --amend"
  const strippedCommand = stripQuotedStrings(command);

  // Split command chain to avoid matching --amend in unrelated chained commands
  const commands = splitCommandChain(strippedCommand);

  // Pattern for git commit --amend
  const pattern = new RegExp(`git${GIT_GLOBAL_OPTIONS}\\s+commit\\s+.*?--amend(?:\\s|$)`);

  return commands.some((cmd) => pattern.test(cmd));
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

    // Skip if not a git commit --amend command
    if (!containsAmendFlag(command)) {
      logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Allow in worktrees
    if (isInWorktree()) {
      logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Block in main repository
    if (await isMainRepository()) {
      const reason =
        "[commit-amend-block] mainリポジトリでのgit commit --amendはブロックされました。\n\n" +
        "mainブランチの履歴を変更することは危険です。\n\n" +
        "【対処法】\n" +
        "1. worktreeで作業してください:\n" +
        "   git worktree add .worktrees/issue-XXX -b fix/issue-XXX\n" +
        "   cd .worktrees/issue-XXX\n\n" +
        "2. 直前のコミットを修正したい場合は、worktree内で --amend を実行してください。\n\n" +
        "💡 ブロック後も作業を継続してください。\n" +
        "代替アクションのツール呼び出しを行い、テキストのみの応答で終わらないでください。";
      result = makeBlockResult(HOOK_NAME, reason);
      logHookExecution(HOOK_NAME, "block", "git commit --amend in main repo", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Not in main repository, approve
  } catch (error) {
    console.error(`[commit-amend-block] Hook error: ${formatError(error)}`);
    result = { reason: `Hook error: ${formatError(error)}` };
  }

  logHookExecution(HOOK_NAME, result.decision ?? "approve", undefined, undefined, { sessionId });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
