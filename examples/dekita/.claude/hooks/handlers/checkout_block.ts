#!/usr/bin/env bun
/**
 * mainリポジトリでのブランチ操作をブロックする。
 *
 * Why:
 *   mainリポジトリで直接ブランチ操作を行うと、worktreeワークフローをバイパスし、
 *   他のセッションとの競合やクリーンな環境維持が困難になる。
 *
 * What:
 *   - git checkout/switchでmain/master/develop以外へのcheckoutをブロック
 *   - git branchで新規ブランチ作成をブロック
 *   - worktree作成手順を提示
 *
 * Remarks:
 *   - ブロック型フック（worktreeワークフロー強制）
 *   - worktree内では発火しない（cwdが.worktrees/内ならスキップ）
 *   - PreToolUse:Bashで発火
 *
 * Changelog:
 *   - silenvx/dekita#2874: TypeScriptに移植
 *   - silenvx/dekita#2894: isInWorktree/isMainRepositoryをlib/git.tsに集約
 */

import { isInWorktree, isMainRepository } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "checkout-block";

// Pattern to match git global options that can appear between 'git' and the subcommand
const GIT_GLOBAL_OPTIONS =
  /(?:\s+(?:-[CcOo]\s*\S+|--[\w-]+=\S+|--[\w-]+\s+(?!checkout\b|switch\b)\S+|--[\w-]+|-[pPhv]|-\d+))*/;

// Allowed branches that can be checked out in main repository
const ALLOWED_BRANCHES = new Set(["main", "develop", "master"]);

/**
 * Extract the target branch from a git checkout/switch command.
 */
export function extractCheckoutTarget(command: string): string | null {
  // Pattern for git checkout <branch>
  // Flags before branch name: -b, -B (create branch), -t (track), --orphan
  // Flags without args: -f, -m, -p, -q, --force, --detach, --merge, --quiet, --progress, --guess, --no-guess, --patch
  const checkoutPattern = new RegExp(
    `git${GIT_GLOBAL_OPTIONS.source}\\s+checkout\\s+(?:(?:-[fmpq]+|--force|--detach|--merge|--quiet|--progress|--guess|--no-guess|--patch)\\s+)*(?:-[bBt]\\s+|--track\\s+|--orphan\\s+)?(?:origin/)?(\\S+)`,
  );

  // Pattern for git switch <branch>
  // Flags with args: -c, -C (create branch), -t (track), --orphan
  // Flags without args: -d, -f, -m, -q, --force, --detach, --merge, --quiet, --progress, --guess, --no-guess, --discard-changes
  const switchPattern = new RegExp(
    `git${GIT_GLOBAL_OPTIONS.source}\\s+switch\\s+(?:(?:-[dfmq]+|--force|--detach|--merge|--quiet|--progress|--guess|--no-guess|--discard-changes)\\s+)*(?:-[cCt]\\s+|--create\\s+|--track\\s+|--orphan\\s+)?(?:origin/)?(\\S+)`,
  );

  // Try checkout pattern first
  let match = command.match(checkoutPattern);
  if (match) {
    const target = match[1];
    if (!target.startsWith("-")) {
      return target;
    }
  }

  // Try switch pattern
  match = command.match(switchPattern);
  if (match) {
    const target = match[1];
    if (!target.startsWith("-")) {
      return target;
    }
  }

  return null;
}

/**
 * Extract the target branch from a git branch create command.
 */
export function extractBranchCreateTarget(command: string): string | null {
  const branchPattern = new RegExp(
    `git${GIT_GLOBAL_OPTIONS.source}\\s+branch\\s+(?!-[dDmMlarvV]|--delete|--move|--list|--all|--remotes|--verbose|--show-current|--contains|--merged|--no-merged)(\\S+)`,
  );

  const match = command.match(branchPattern);
  if (match) {
    const target = match[1];
    // Skip if it's an option (starts with -)
    if (target.startsWith("-")) {
      return null;
    }
    // Skip shell operators
    if (["&&", "||", ";", "|", ">", ">>", "<", "<<", "&"].includes(target)) {
      return null;
    }
    return target;
  }

  return null;
}

/**
 * Check if the branch is allowed to be checked out in main repository.
 */
export function isAllowedBranch(branch: string): boolean {
  return ALLOWED_BRANCHES.has(branch);
}

/**
 * Generate block reason message.
 */
function makeBlockReason(targetBranch: string, operation: string): string {
  return `mainリポジトリでのブランチ操作がブロックされました。

操作: git ${operation}
ターゲットブランチ: ${targetBranch}

worktreeを使用してください:

  git worktree add .worktrees/<issue-name> -b ${targetBranch} main

理由:
- mainリポジトリで直接作業すると、他のセッションとの競合リスク
- 作業状態の追跡が困難
- クリーンな環境維持が難しい

許可されているブランチ: ${Array.from(ALLOWED_BRANCHES).join(", ")}`;
}

async function main(): Promise<void> {
  const data = await parseHookInput();

  if (!data) {
    // If we can't parse input, approve (fail open)
    await logHookExecution(HOOK_NAME, "approve", "parse_error", undefined, {
      sessionId: undefined,
    });
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  const ctx = createHookContext(data);
  const toolInput = data.tool_input || {};
  const command = (toolInput.command as string) || "";

  // Check git checkout/switch/branch commands
  if (!/\bgit\b.*\s+(?:checkout|switch|branch)(?:\s+|$)/.test(command)) {
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // If in worktree, allow all operations
  if (isInWorktree()) {
    await logHookExecution(HOOK_NAME, "approve", "in_worktree", undefined, {
      sessionId: ctx.sessionId ?? undefined,
    });
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // If not in main repository, allow
  if (!(await isMainRepository())) {
    await logHookExecution(HOOK_NAME, "approve", "not_main_repository", undefined, {
      sessionId: ctx.sessionId ?? undefined,
    });
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Check for git branch create command first
  const branchTarget = extractBranchCreateTarget(command);
  if (branchTarget) {
    // Block new branch creation in main repository
    const reason = makeBlockReason(branchTarget, "branch");
    await logHookExecution(
      HOOK_NAME,
      "block",
      "branch_create",
      { branch: branchTarget },
      { sessionId: ctx.sessionId ?? undefined },
    );
    console.log(JSON.stringify(makeBlockResult(HOOK_NAME, reason, ctx)));
    return;
  }

  // Extract target branch for checkout/switch
  const targetBranch = extractCheckoutTarget(command);
  if (!targetBranch) {
    // Can't determine target, approve (fail open)
    await logHookExecution(HOOK_NAME, "approve", "unknown_target", undefined, {
      sessionId: ctx.sessionId ?? undefined,
    });
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Allow checkout to allowed branches (main, develop, etc.)
  if (isAllowedBranch(targetBranch)) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      "allowed_branch",
      {
        branch: targetBranch,
      },
      { sessionId: ctx.sessionId ?? undefined },
    );
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Block checkout/switch to any non-allowed branch
  const reason = makeBlockReason(targetBranch, "checkout/switch");
  await logHookExecution(
    HOOK_NAME,
    "block",
    "non_allowed_branch",
    { branch: targetBranch },
    { sessionId: ctx.sessionId ?? undefined },
  );
  console.log(JSON.stringify(makeBlockResult(HOOK_NAME, reason, ctx)));
}

if (import.meta.main) {
  main();
}
