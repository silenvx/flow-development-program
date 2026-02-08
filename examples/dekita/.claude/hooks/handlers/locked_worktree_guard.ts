#!/usr/bin/env bun
/**
 * 他セッションが所有するPRとworktreeへの操作をブロックする。
 *
 * Why:
 *   ロックされたworktreeは別のClaude Codeセッションが作業中であることを示す。
 *   競合するPR操作やworktree削除をブロックし、セッション間の干渉を防止する。
 *
 * What:
 *   - gh pr merge/checkout/close等の変更操作を検出
 *   - git worktree removeコマンドを検出
 *   - rm コマンドによるworktree削除を検出
 *   - ロック中worktreeの所有PRへの操作をブロック
 *   - CWD内のworktree削除をブロック
 *   - 非ロック中でもアクティブな作業があれば警告
 *
 * Remarks:
 *   - 読み取り専用コマンド（gh pr view等）は許可
 *   - guard_rules.ts、command_parser.ts、worktree_manager.tsに分割
 *   - FORCE_RM_ORPHAN=1で孤立worktree削除チェックをスキップ可能
 *
 * Changelog:
 *   - silenvx/dekita#289: rm -rfによるworktree削除ブロック追加
 *   - silenvx/dekita#317: hook_cwdによる正確なworktree検出
 *   - silenvx/dekita#528: 非ロックworktreeのアクティブ作業警告追加
 *   - silenvx/dekita#608: ci-monitor.pyコマンドの検出追加
 *   - silenvx/dekita#649: 自己ブランチ削除チェック追加
 *   - silenvx/dekita#795: 孤立worktreeディレクトリ削除ブロック追加
 *   - silenvx/dekita#1400: 自セッションworktreeのスキップ追加
 *   - silenvx/dekita#2496: session_idによる自セッション検出追加
 *   - silenvx/dekita#2618: FORCE_RM_ORPHANインライン指定対応
 *   - silenvx/dekita#3161: TypeScript移行
 */

import {
  isCiMonitorCommand,
  isGhPrCommand,
  isModifyingCommand,
  isWorktreeRemoveCommand,
} from "../lib/command_parser";
import { formatError } from "../lib/format_error";
import { extractPrNumber } from "../lib/github";
import {
  checkRmOrphanWorktree,
  checkRmWorktree,
  checkSelfBranchDeletion,
  checkWorktreeRemove,
} from "../lib/guard_rules";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { extractInlineSkipEnv, isSkipEnvEnabled } from "../lib/strings";
import {
  checkActiveWorkSigns,
  getBranchForPr,
  getLockedWorktrees,
  getPrForBranch,
  getWorktreeForBranch,
  isCwdInsideWorktree,
  isSelfSessionWorktree,
} from "../lib/worktree_manager";

const HOOK_NAME = "locked-worktree-guard";
const FORCE_RM_ORPHAN_ENV = "FORCE_RM_ORPHAN";

/**
 * Check if FORCE_RM_ORPHAN environment variable is set with truthy value.
 *
 * Supports both:
 * - Exported: export FORCE_RM_ORPHAN=1 && rm -rf ...
 * - Inline: FORCE_RM_ORPHAN=1 rm -rf ... (including FORCE_RM_ORPHAN="1")
 *
 * Only "1", "true", "True" are considered truthy (Issue #956).
 *
 * @param command - The command string to check for inline env var.
 * @returns True if FORCE_RM_ORPHAN is set with truthy value, False otherwise.
 */
function hasForceRmOrphanEnv(command: string): boolean {
  // Check exported environment variable with value validation
  if (isSkipEnvEnabled(process.env[FORCE_RM_ORPHAN_ENV])) {
    return true;
  }
  // Check inline environment variable in command (handles quoted values)
  const inlineValue = extractInlineSkipEnv(command, FORCE_RM_ORPHAN_ENV);
  if (isSkipEnvEnabled(inlineValue)) {
    return true;
  }
  return false;
}

async function main(): Promise<void> {
  let result: Record<string, unknown> = {};
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;
    const toolInput = inputData.tool_input ?? {};
    const command = typeof toolInput.command === "string" ? toolInput.command : "";

    // Check for FORCE_RM_ORPHAN bypass (Issue #795, Issue #2618)
    const forceRmOrphan = hasForceRmOrphanEnv(command);

    // Get cwd from hook input to correctly identify current worktree
    // This fixes Issue #317: hooks run in main repo, not worktree
    const hookCwd = inputData.cwd ?? undefined;

    // Check for worktree remove commands
    // NOTE: Do not early-return on approval to allow gh pr checks for mixed commands
    if (isWorktreeRemoveCommand(command)) {
      const blockResult = await checkWorktreeRemove(command, hookCwd);
      if (blockResult) {
        await logHookExecution(
          HOOK_NAME,
          "block",
          "worktree remove blocked",
          {
            command,
          },
          { sessionId },
        );
        console.log(JSON.stringify(blockResult));
        return;
      }
      // If worktree removal is allowed, continue to check for gh pr commands
      // in case this is a mixed command like "git worktree remove foo && gh pr merge 123"
    }

    // Check for rm commands targeting worktrees (Issue #289)
    // This prevents shell corruption when deleting worktree while CWD is inside
    const rmWorktreeBlock = await checkRmWorktree(command, hookCwd);
    if (rmWorktreeBlock) {
      await logHookExecution(
        HOOK_NAME,
        "block",
        "rm worktree blocked",
        {
          command,
        },
        { sessionId },
      );
      console.log(JSON.stringify(rmWorktreeBlock));
      return;
    }

    // Check for rm commands targeting orphan worktree directories (Issue #795)
    // This prevents accidental deletion of worktrees not registered with git
    if (!forceRmOrphan) {
      const orphanBlock = await checkRmOrphanWorktree(command, hookCwd);
      if (orphanBlock) {
        await logHookExecution(
          HOOK_NAME,
          "block",
          "rm orphan worktree blocked",
          {
            command,
          },
          { sessionId },
        );
        console.log(JSON.stringify(orphanBlock));
        return;
      }
    }

    // Get cwd and locked worktrees once for all checks
    // Issue #806: Use isCwdInsideWorktree() for more robust self-worktree detection
    // (avoids path resolution issues with current_worktree == worktree_path comparison)
    const cwd = hookCwd ?? undefined;
    const lockedWorktrees = await getLockedWorktrees();

    // Check for ci-monitor.py commands (Issue #608)
    // ci-monitor.py internally calls gh pr commands, so we need to intercept it
    // NOTE: Do not early-return on approval to allow gh pr checks for mixed commands
    // (e.g., "python ci-monitor.py 123 && gh pr merge 456")
    const [isCiMonitor, ciPrNumbers] = isCiMonitorCommand(command);
    if (isCiMonitor && ciPrNumbers.length > 0) {
      for (const [worktreePath, branch] of lockedWorktrees) {
        // Skip current worktree (we own it)
        // Issue #806: Use isCwdInsideWorktree for robust detection
        // Issue #1400: Also skip if current session created the worktree
        // Issue #3262: Pass command to detect inline cd
        if (
          isCwdInsideWorktree(worktreePath, cwd, command) ||
          isSelfSessionWorktree(worktreePath, sessionId)
        ) {
          continue;
        }

        // Get PR for this branch
        const branchPr = await getPrForBranch(branch);
        // Check if any of the ci-monitor PR numbers matches the locked branch
        for (const ciPrNumber of ciPrNumbers) {
          if (branchPr && branchPr === ciPrNumber) {
            const reason = `PR #${ciPrNumber} は別のセッションが処理中です。\n\nロック中のworktree: ${worktreePath}\nブランチ: ${branch}\n\nci-monitor.py は内部で gh pr コマンドを実行するため、\nロック中のworktreeのPRに対する操作はブロックされます。\n\nこのPRを監視する必要がある場合は:\n1. 該当セッションの完了を待つ\n2. または git worktree unlock でロック解除（他セッションに影響あり）`;
            const blockResult = makeBlockResult(HOOK_NAME, reason);
            await logHookExecution(
              HOOK_NAME,
              "block",
              `ci-monitor.py for PR #${ciPrNumber} owned by locked worktree`,
              { worktree: worktreePath, branch },
              { sessionId },
            );
            console.log(JSON.stringify(blockResult));
            return;
          }
        }
      }
      // ci-monitor.py command but no PR in locked worktree
      // Continue to check for gh pr commands in case of mixed commands
    }

    // Check for self-branch deletion via gh pr merge --delete-branch (Issue #649)
    // This MUST be checked before other gh pr checks because it's a self-inflicted issue
    // (not about locked worktrees or other sessions)
    const selfBranchBlock = await checkSelfBranchDeletion(command, hookCwd);
    if (selfBranchBlock) {
      await logHookExecution(
        HOOK_NAME,
        "block",
        "self-branch deletion blocked",
        {
          command,
        },
        { sessionId },
      );
      console.log(JSON.stringify(selfBranchBlock));
      return;
    }

    // Only check gh pr commands (handles global flags before 'pr')
    if (!isGhPrCommand(command)) {
      result = {};
      await logHookExecution(
        HOOK_NAME,
        "approve",
        undefined,
        {
          reason: "not relevant command",
        },
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Only check modifying commands
    if (!isModifyingCommand(command)) {
      result = {};
      await logHookExecution(
        HOOK_NAME,
        "approve",
        undefined,
        {
          reason: "read-only command",
        },
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Extract PR number from command or current branch's PR
    // Issue #3263: Handle `gh pr merge --squash` without explicit PR number
    // Issue #3263: Run gh pr view in hookCwd to get correct PR for worktree
    let prNumber = extractPrNumber(command);
    if (!prNumber) {
      const { asyncSpawn } = await import("../lib/spawn");
      const { TIMEOUT_MEDIUM } = await import("../lib/constants");
      // Run in hookCwd to get the correct PR for the working directory
      const spawnResult = await asyncSpawn("gh", ["pr", "view", "--json", "number"], {
        timeout: TIMEOUT_MEDIUM * 1000,
        cwd: hookCwd,
      });
      if (spawnResult.success && spawnResult.stdout) {
        try {
          const data = JSON.parse(spawnResult.stdout.trim());
          prNumber = data.number ? String(data.number) : null;
        } catch {
          // JSON parse error - continue without PR number
        }
      }
    }

    if (!prNumber) {
      result = {};
      await logHookExecution(
        HOOK_NAME,
        "approve",
        undefined,
        {
          reason: "no PR number found in command or current branch",
        },
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Check if the PR belongs to any locked worktree (excluding current)
    // (cwd and lockedWorktrees already obtained above)
    for (const [worktreePath, branch] of lockedWorktrees) {
      // Skip current worktree (we own it)
      // Issue #806: Use isCwdInsideWorktree for robust detection
      // Issue #1400: Also skip if current session created the worktree
      // Issue #3262: Pass command to detect inline cd
      if (
        isCwdInsideWorktree(worktreePath, cwd, command) ||
        isSelfSessionWorktree(worktreePath, sessionId)
      ) {
        continue;
      }

      // Get PR for this branch
      const branchPr = await getPrForBranch(branch);
      if (branchPr && branchPr === prNumber) {
        const reason = `PR #${prNumber} は別のセッションが処理中です。\n\nロック中のworktree: ${worktreePath}\nブランチ: ${branch}\n\nこのPRを操作する必要がある場合は:\n1. 該当セッションの完了を待つ\n2. または git worktree unlock でロック解除（他セッションに影響あり）`;
        const blockResult = makeBlockResult(HOOK_NAME, reason);
        await logHookExecution(
          HOOK_NAME,
          "block",
          `PR #${prNumber} owned by locked worktree`,
          {
            worktree: worktreePath,
            branch,
          },
          { sessionId },
        );
        console.log(JSON.stringify(blockResult));
        return;
      }
    }

    // Check for active work signs in non-locked worktrees (Issue #528)
    // This provides a warning (not block) when another session might be working
    const prBranch = await getBranchForPr(prNumber);
    if (prBranch) {
      const worktreeForPr = await getWorktreeForBranch(prBranch);
      if (worktreeForPr) {
        // Skip if this is our own worktree
        // Issue #806: Use isCwdInsideWorktree for robust detection
        // Issue #3262: Pass command to detect inline cd
        if (!isCwdInsideWorktree(worktreeForPr, cwd, command)) {
          const activeSigns = await checkActiveWorkSigns(worktreeForPr);
          if (activeSigns.length > 0) {
            const signsText = activeSigns.map((s) => `  - ${s}`).join("\n");
            result = {
              systemMessage: `⚠️ このPRは別セッションが作業中の可能性があります。\n\n検出された状態:\n  - worktree: ${worktreeForPr}\n${signsText}\n\n続行する場合は、元のセッションとの競合に注意してください。`,
            };
            await logHookExecution(
              HOOK_NAME,
              "approve",
              `PR #${prNumber} has active work signs (warning)`,
              {
                worktree: worktreeForPr,
                branch: prBranch,
                signs: activeSigns,
              },
              { sessionId },
            );
            console.log(JSON.stringify(result));
            return;
          }
        }
      }
    }

    // All checks passed
    result = {};
    await logHookExecution(
      HOOK_NAME,
      "approve",
      undefined,
      {
        pr: prNumber,
        reason: "no conflict with locked worktrees",
      },
      { sessionId },
    );
  } catch (e) {
    // On error, approve to avoid blocking (fail open)
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(e)}`);
    result = {};
    await logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(e)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    // Issue #3263: Output approve result to ensure hook always produces valid JSON
    // Without this, Claude Code may not receive a decision and could hang
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({}));
  });
}
