/**
 * locked-worktree-guardのガードルールと検証ロジック。
 *
 * Why:
 *   Worktree関連の危険な操作（自己ブランチ削除、ロック中worktree削除、
 *   孤立worktree削除等）を検出し、適切なブロックまたは警告を行う。
 *
 * What:
 *   - 自己ブランチ削除チェック（gh pr merge --delete-branch）
 *   - worktree削除の安全性チェック（CWD内、ロック中）
 *   - rm コマンドによるworktree削除チェック
 *   - 孤立worktreeの削除チェック
 *   - PRマージ時の安全な自動実行
 *
 * Remarks:
 *   - locked-worktree-guard.tsから呼び出されるモジュール
 *   - マージ時は--delete-branchを除去して安全に自動実行
 *   - Issue #855以降、ブロックではなく安全なマージを自動実行
 *
 * Changelog:
 *   - silenvx/dekita#3157: TypeScriptに移植
 */

import { spawn } from "node:child_process";
import { existsSync, realpathSync } from "node:fs";
import { resolve } from "node:path";

import {
  extractAllWorktreePathsFromCommand,
  extractFirstMergeCommand,
  extractUnlockTargetsFromCommand,
  findGitWorktreeRemovePosition,
  getMergePositionalArg,
} from "./command_parser";
import { TIMEOUT_LONG, TIMEOUT_MEDIUM } from "./constants";
import { expandHome, getEffectiveCwd } from "./cwd";
import { formatError } from "./format_error";
import { parseAllGhPrCommands } from "./github";
import { logHookExecution } from "./logging";
import { makeBlockResult } from "./results";
import {
  getAllLockedWorktreePaths,
  getBranchForPr,
  getCurrentBranchName,
  getCurrentWorktree,
  getLockedWorktrees,
  getMainRepoDir,
  getRmTargetOrphanWorktrees,
  getRmTargetWorktrees,
  isCwdInsideWorktree,
} from "./worktree_manager";

// =============================================================================
// Helper: Run command with timeout
// =============================================================================

interface SpawnResult {
  stdout: string;
  stderr: string;
  exitCode: number | null;
}

async function runCommand(
  command: string,
  args: string[],
  options: { timeout?: number; cwd?: string } = {},
): Promise<SpawnResult> {
  const { timeout = TIMEOUT_MEDIUM, cwd } = options;

  return new Promise((resolvePromise) => {
    const proc = spawn(command, args, {
      cwd,
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let killed = false;

    const timer = setTimeout(() => {
      killed = true;
      proc.kill("SIGTERM");
    }, timeout * 1000);

    proc.stdout?.on("data", (data) => {
      stdout += data.toString();
    });

    proc.stderr?.on("data", (data) => {
      stderr += data.toString();
    });

    proc.on("close", (exitCode) => {
      clearTimeout(timer);
      if (killed) {
        resolvePromise({ stdout: "", stderr: "Timeout", exitCode: null });
      } else {
        resolvePromise({ stdout, stderr, exitCode });
      }
    });

    proc.on("error", () => {
      clearTimeout(timer);
      resolvePromise({ stdout: "", stderr: "Error", exitCode: null });
    });
  });
}

// =============================================================================
// Block Result Type
// =============================================================================

interface BlockResult {
  decision?: string;
  reason?: string;
}

// =============================================================================
// PR Merge Functions
// =============================================================================

/**
 * Check if a PR is actually merged.
 *
 * @param prNumber - PR number to check. If null, uses branch to find PR.
 * @param branch - Branch name to find PR if prNumber is not provided.
 * @returns True if PR is merged, False otherwise.
 */
export async function checkPrMerged(
  prNumber?: string | null,
  branch?: string | null,
): Promise<boolean> {
  try {
    const selector = prNumber ?? branch;
    if (!selector) {
      return false;
    }

    const result = await runCommand(
      "gh",
      ["pr", "view", selector, "--json", "state", "--jq", ".state"],
      { timeout: TIMEOUT_MEDIUM },
    );

    if (result.exitCode === 0) {
      const state = result.stdout.trim().toUpperCase();
      return state === "MERGED";
    }
  } catch {
    // On error, assume not merged to avoid false positive reports
  }

  return false;
}

/**
 * Improve gh command error messages for better user experience.
 *
 * @param error - The raw error message from gh command.
 * @param command - The original command that was executed.
 * @returns Improved error message with context.
 */
export function improveGhErrorMessage(error: string, command: string): string {
  const errorLower = error.toLowerCase();

  // Pattern: argument count error
  if (errorLower.includes("accepts at most") && errorLower.includes("arg")) {
    return `コマンド引数エラー: gh pr merge は1つのPR指定のみ受け付けます\n実行コマンド: ${command}`;
  }

  // Pattern: PR/branch not found or could not be resolved
  if (errorLower.includes("no pull requests found") || errorLower.includes("could not resolve")) {
    return (
      "PR/ブランチが見つかりません: " +
      "指定されたPR番号やブランチ名が存在しない、リモートにプッシュされていない、" +
      "または既にクローズ済みの可能性があります。\n" +
      "対処法: PR番号・ブランチ名を再確認し、必要に応じて `git push` や " +
      "PR の再作成を行ってください。"
    );
  }

  // Pattern: not mergeable
  if (errorLower.includes("not mergeable") || errorLower.includes("cannot be merged")) {
    return "マージ不可: PRにコンフリクトがあるか、マージ条件を満たしていません";
  }

  // Pattern: authentication/permission error
  if (
    errorLower.includes("unauthorized") ||
    errorLower.includes("permission") ||
    errorLower.includes("forbidden")
  ) {
    return (
      "認証/権限エラー: GitHub への認証または権限に問題があります\n" +
      "対処法: ターミナルで `gh auth status` を実行して認証状態を確認してください"
    );
  }

  // Default: return original error with command context
  return `${formatError(error)}\n実行コマンド: ${command}`;
}

/**
 * Execute a merge command safely (without --delete-branch).
 *
 * @param command - The original gh pr merge command.
 * @param hookCwd - Current working directory.
 * @returns Tuple of [success, outputMessage].
 */
export async function executeSafeMerge(
  command: string,
  hookCwd?: string | null,
): Promise<[boolean, string]> {
  // Extract only the first merge command - do NOT run chained commands
  const safeCommand = extractFirstMergeCommand(command);

  try {
    const result = await runCommand("bash", ["-c", safeCommand], {
      timeout: TIMEOUT_LONG,
      cwd: hookCwd ?? undefined,
    });

    if (result.exitCode === 0) {
      return [true, result.stdout.trim() || "Merge completed successfully."];
    }
    const rawError = result.stderr.trim() || result.stdout.trim() || "Unknown error";
    const improvedError = improveGhErrorMessage(rawError, safeCommand);
    return [false, improvedError];
  } catch {
    return [false, `Merge command timed out (${TIMEOUT_LONG} seconds).`];
  }
}

/**
 * Try to auto-cleanup the worktree after successful merge.
 *
 * @param mainRepo - Path to the main repository.
 * @param currentWorktree - Path to the current worktree.
 * @param prBranch - The branch name of the merged PR.
 * @returns Tuple of [success, message].
 */
export async function tryAutoCleanupWorktree(
  mainRepo: string,
  currentWorktree: string,
  _prBranch: string,
): Promise<[boolean, string]> {
  // Check if the worktree is locked
  const lockedWorktrees = await getLockedWorktrees();
  let worktreeResolved: string;
  try {
    worktreeResolved = realpathSync(currentWorktree);
  } catch {
    return [false, "worktreeパス解決エラー"];
  }

  for (const [lockedPath] of lockedWorktrees) {
    try {
      if (realpathSync(lockedPath) === worktreeResolved) {
        return [false, "worktreeがロック中（別セッションが作業中の可能性）"];
      }
    } catch {
      // パス解決失敗、スキップ
    }
  }

  // Try to remove the worktree from main repo
  try {
    const result = await runCommand("git", ["worktree", "remove", "--", currentWorktree], {
      timeout: TIMEOUT_MEDIUM,
      cwd: mainRepo,
    });

    if (result.exitCode !== 0) {
      const error = result.stderr.trim() || result.stdout.trim() || "Unknown error";
      return [false, `worktree削除失敗: ${formatError(error)}`];
    }

    return [true, "worktree削除 成功"];
  } catch {
    return [false, "worktree削除タイムアウト"];
  }
}

// =============================================================================
// Guard Check Functions
// =============================================================================

/**
 * Check if gh pr merge --delete-branch would delete the current worktree's branch.
 *
 * This function checks ALL gh pr merge commands in a chain to prevent
 * bypass vulnerabilities like "gh pr merge A && gh pr merge B --delete-branch".
 *
 * @param command - The gh pr merge command (may contain chained commands).
 * @param hookCwd - Current working directory from hook input.
 * @returns Block result dict if should block, null if should approve.
 */
export async function checkSelfBranchDeletion(
  command: string,
  hookCwd?: string | null,
): Promise<BlockResult | null> {
  // Issue #3169: Check ALL gh pr merge commands in the chain
  const allPrCommands = parseAllGhPrCommands(command);

  // Filter to only merge commands
  const mergeCommands = allPrCommands.filter(([subcommand]) => subcommand === "merge");

  if (mergeCommands.length === 0) {
    return null;
  }

  // Issue #3553: Check if any merge command has --delete-branch flag
  // Uses the hasDeleteBranch flag from parseAllGhPrCommands instead of regex on entire command
  // This prevents false positives from flags inside quoted strings (e.g., -b "--delete-branch")
  // or in unrelated commands (e.g., echo "--delete-branch" && gh pr merge 123)
  const mergeCommandsWithDelete = mergeCommands.filter(
    ([, , , , , hasDeleteBranch]) => hasDeleteBranch,
  );
  if (mergeCommandsWithDelete.length === 0) {
    return null;
  }

  // Get PR number from the first merge command (for single command case)
  const [, prNumber] = mergeCommands[0];

  // Get current worktree and branch
  const effectiveCwd = getEffectiveCwd(command, hookCwd);

  const currentWorktree = await getCurrentWorktree(effectiveCwd);
  if (!currentWorktree) {
    return null;
  }

  // Check if we're in a worktree (not main repo)
  const mainRepo = await getMainRepoDir();
  if (!mainRepo) {
    return null;
  }

  try {
    if (realpathSync(currentWorktree) === realpathSync(mainRepo)) {
      // We're in the main repo, not a worktree - safe to proceed
      return null;
    }
  } catch {
    // Continue check on error to prevent accidental deletion
  }

  // Get current branch
  const currentBranch = await getCurrentBranchName(effectiveCwd);
  if (!currentBranch) {
    return null;
  }

  // Issue #3169: For chained commands with multiple merge commands,
  // check ALL commands to prevent bypass like "gh pr merge A && gh pr merge B --delete-branch"
  // Issue #3553: Only check commands that actually have --delete-branch flag
  if (mergeCommands.length > 1) {
    // Check each merge command that has --delete-branch to see if any targets its own worktree's branch
    for (const [, mergePrNumber, , cdTarget, mergeTarget] of mergeCommandsWithDelete) {
      // Issue #3340: Use the cdTarget specific to this merge command
      // If cdTarget is null, use the initial CWD (not effectiveCwd which might be polluted by other cd commands)
      // Issue #3386: Expand ~ in cdTarget before resolving
      const baseCwd = hookCwd ?? process.cwd();
      const cmdEffectiveCwd = cdTarget ? resolve(baseCwd, expandHome(cdTarget)) : baseCwd;
      const cmdCurrentBranch = await getCurrentBranchName(cmdEffectiveCwd);

      // Issue #3539: Determine target branch - use PR lookup, mergeTarget, or current branch
      let targetBranch: string | null = null;
      if (mergePrNumber) {
        targetBranch = await getBranchForPr(mergePrNumber);
      } else if (mergeTarget) {
        // mergeTarget contains the branch name or URL when prNumber is not found
        if (!mergeTarget.startsWith("http")) {
          targetBranch = mergeTarget;
        }
      } else {
        // No PR number and no explicit target - assumes current branch
        targetBranch = cmdCurrentBranch;
      }

      // If any merge command targets its worktree's branch and the command has --delete-branch,
      // we must block because we can't safely execute chained merge commands
      if (cmdCurrentBranch && targetBranch === cmdCurrentBranch) {
        // Issue #3340: Get the worktree for this specific command's cwd
        const cmdWorktree = await getCurrentWorktree(cmdEffectiveCwd);
        const reason = `⚠️ チェーンコマンド内で自己ブランチ削除を検出しました。

複数のPRマージをチェーンで実行しようとしていますが、
その中に現在のworktreeブランチを削除するコマンドが含まれています。

対象ブランチ: ${cmdCurrentBranch}
worktree: ${cmdWorktree ?? cmdEffectiveCwd}

【セキュリティ上の理由】
チェーンコマンドは部分的に実行できないため、安全に自動マージできません。

【対処法】
各PRを個別にマージしてください:
1. cd ${mainRepo}
2. gh pr merge <PR番号> --squash
   (worktreeを削除するPRは最後に実行)

または --delete-branch を除去してください。`;
        return makeBlockResult("locked-worktree-guard", reason);
      }
    }

    // No merge command targets the current branch, but --delete-branch flag exists somewhere
    // This is likely targeting a different branch, allow it to proceed
    return null;
  }

  // Get PR's branch (single merge command case - original logic)
  let prBranch: string | null = null;
  if (prNumber) {
    prBranch = await getBranchForPr(prNumber);
  } else {
    const positionalArg = getMergePositionalArg(command);
    if (positionalArg) {
      if (positionalArg.startsWith("http")) {
        return null;
      }
      if (positionalArg === currentBranch) {
        prBranch = currentBranch;
      } else {
        return null;
      }
    } else {
      prBranch = currentBranch;
    }
  }

  if (!prBranch) {
    return null;
  }

  // Check if PR's branch matches current worktree's branch
  if (prBranch === currentBranch) {
    // Run merge-check --dry-run before auto-merging
    let effectivePrNumber = prNumber;
    if (!effectivePrNumber) {
      try {
        const prViewResult = await runCommand(
          "gh",
          ["pr", "view", "--json", "number", "--jq", ".number"],
          { timeout: TIMEOUT_MEDIUM, cwd: effectiveCwd ?? undefined },
        );
        if (prViewResult.exitCode === 0 && prViewResult.stdout.trim()) {
          effectivePrNumber = prViewResult.stdout.trim();
        }
      } catch {
        await logHookExecution(
          "locked-worktree-guard",
          "warn",
          "gh pr view timed out while getting PR number, skipping merge-check dry-run",
        );
      }
    }

    if (effectivePrNumber) {
      const projectDir = process.env.CLAUDE_PROJECT_DIR ?? "";
      if (!projectDir) {
        await logHookExecution(
          "locked-worktree-guard",
          "warn",
          "CLAUDE_PROJECT_DIR not set, skipping merge-check dry-run",
        );
      } else {
        // Issue #3263: Use TypeScript version of merge_check instead of Python version
        const mergeCheckScript = resolve(
          projectDir,
          ".claude",
          "hooks",
          "ts",
          "hooks",
          "merge_check.ts",
        );

        if (existsSync(mergeCheckScript)) {
          try {
            const dryRunResult = await runCommand(
              "bun",
              ["run", mergeCheckScript, "--dry-run", effectivePrNumber],
              { timeout: TIMEOUT_LONG, cwd: effectiveCwd ?? undefined },
            );

            if (dryRunResult.exitCode !== 0) {
              const stdoutTrimmed = dryRunResult.stdout.trim();
              const stderrTrimmed = dryRunResult.stderr.trim();
              const errorOutput =
                [stdoutTrimmed, stderrTrimmed].filter(Boolean).join("\n") ||
                `(merge-check exited with code ${dryRunResult.exitCode})`;
              const reason = `⚠️ 自動マージをスキップしました: PR #${effectivePrNumber}\n\nworktree内からのマージを検出しましたが、マージ前の安全チェックで問題が見つかりました。\n\n${errorOutput}\n\n問題を解決してから再度マージを実行してください。`;
              return makeBlockResult("locked-worktree-guard", reason);
            }
            await logHookExecution(
              "locked-worktree-guard",
              "info",
              `merge-check --dry-run passed for PR #${effectivePrNumber}`,
            );
          } catch {
            await logHookExecution(
              "locked-worktree-guard",
              "warn",
              `merge-check --dry-run timed out for PR #${effectivePrNumber}`,
            );
          }
        }
      }
    }

    // Execute safe merge
    const [success, output] = await executeSafeMerge(command, effectiveCwd);

    if (success) {
      const actuallyMerged = await checkPrMerged(prNumber, prBranch);

      if (actuallyMerged) {
        // Check if we can auto-cleanup
        let effectiveCwdResolved: string;
        let currentWorktreeResolved: string;
        let isInsideWorktree: boolean;
        try {
          effectiveCwdResolved = realpathSync(effectiveCwd ?? process.cwd());
          currentWorktreeResolved = realpathSync(currentWorktree);
          isInsideWorktree =
            effectiveCwdResolved === currentWorktreeResolved ||
            effectiveCwdResolved.startsWith(`${currentWorktreeResolved}/`);
        } catch {
          isInsideWorktree = true;
        }

        if (isInsideWorktree) {
          const reason = `✅ マージ完了（自動実行）: PR #${prNumber || "(current branch)"}\n\nworktree内からのマージを検出し、--delete-branch なしで自動実行しました。\nブランチ: ${prBranch}\nworktree: ${currentWorktree}\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ **最優先: 振り返りを先に実行してください**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n[IMMEDIATE: /reflecting-sessions]\n振り返り（五省）を行い、教訓をIssue化してください。\n\n【振り返り完了後のステップ】\n1. メインリポジトリに移動: cd ${mainRepo}\n2. worktree削除: git worktree remove ${currentWorktree}\n\n⚠️ 重要（Issue #1002）: 上記のステップは**別々のコマンド**として実行してください。\n\`cd ... && git worktree remove ...\` のように && で連結すると、\nセッションのcwdは変わらず、worktree削除後にセッションが破損します。\n\n（リモートブランチはGitHub設定により自動削除されます）\n\n出力: ${output}`;
          return makeBlockResult("locked-worktree-guard", reason);
        }
        const [cleanupSuccess, cleanupMsg] = await tryAutoCleanupWorktree(
          mainRepo,
          currentWorktree,
          prBranch,
        );

        if (cleanupSuccess) {
          const reason = `✅ マージ完了 + クリーンアップ成功: PR #${prNumber || "(current branch)"}\n\nworktree内からのマージを検出し、--delete-branch なしで自動実行しました。\nブランチ: ${prBranch}\nworktree: ${currentWorktree}\n\n🧹 自動クリーンアップ: ${cleanupMsg}\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ **最優先: 振り返りを先に実行してください**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n[IMMEDIATE: /reflecting-sessions]\n振り返り（五省）を行い、教訓をIssue化してください。\n\n出力: ${output}`;
          return makeBlockResult("locked-worktree-guard", reason);
        }
        const reason = `✅ マージ完了（自動実行）: PR #${prNumber || "(current branch)"}\n\nworktree内からのマージを検出し、--delete-branch なしで自動実行しました。\nブランチ: ${prBranch}\nworktree: ${currentWorktree}\n\n⚠️ 自動クリーンアップ失敗: ${cleanupMsg}\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ **最優先: 振り返りを先に実行してください**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n[IMMEDIATE: /reflecting-sessions]\n振り返り（五省）を行い、教訓をIssue化してください。\n\n【振り返り完了後のステップ】\n1. メインリポジトリに移動: cd ${mainRepo}\n2. worktree削除: git worktree remove ${currentWorktree}\n\n（リモートブランチはGitHub設定により自動削除されます）\n\n出力: ${output}`;
        return makeBlockResult("locked-worktree-guard", reason);
      }
      const reason = `⚠️ マージ未完了: PR #${prNumber || "(current branch)"}\n\nworktree内からのマージを検出しましたが、PRはまだマージされていません。\n他のフック（merge-check等）がブロックした可能性があります。\n\nブランチ: ${prBranch}\nworktree: ${currentWorktree}\n\n【対処法】\n1. 他のフックのエラーメッセージを確認\n2. 問題を解決してから再試行\n3. または手動でマージ:\n   cd ${mainRepo}\n   gh pr merge ${prNumber || currentBranch} --squash`;
      return makeBlockResult("locked-worktree-guard", reason);
    }
    const reason = `❌ マージ失敗: PR #${prNumber || "(current branch)"}\n\nworktree内からのマージを検出しましたが、実行に失敗しました。\nエラー: ${output}\n\n【対処法】\n1. エラー内容を確認\n2. 問題を解決してから再試行\n3. または手動でマージ:\n   cd ${mainRepo}\n   gh pr merge ${prNumber || currentBranch} --squash`;
    return makeBlockResult("locked-worktree-guard", reason);
  }

  return null;
}

/**
 * Check if rm command targets an orphan worktree directory.
 *
 * @param command - The rm command.
 * @param hookCwd - Current working directory from hook input.
 * @returns Block result dict if should block, null if should approve.
 */
export async function checkRmOrphanWorktree(
  command: string,
  hookCwd?: string | null,
): Promise<BlockResult | null> {
  // Allow bypass via environment variable in the command
  if (command.includes("FORCE_RM_ORPHAN=1")) {
    return null;
  }

  const targetOrphans = await getRmTargetOrphanWorktrees(command, hookCwd);
  if (targetOrphans.length === 0) {
    return null;
  }

  const [, orphanPath] = targetOrphans[0];
  const mainRepo = await getMainRepoDir();
  const mainRepoStr = mainRepo ?? "/path/to/main/repo";

  const reason = `⚠️ 孤立worktreeディレクトリの削除をブロックしました。\n\n対象: ${orphanPath}\n\nこのディレクトリは .worktrees/ 内に存在しますが、\ngit worktree list に登録されていません（孤立状態）。\n\n別のセッションが作業中か、git worktree の状態が壊れている可能性があります。\n\n【対処法】以下を**1つずつ順番に**実行してください:\n\n**Step 1**: 内容を確認\n\`\`\`\nls -la ${orphanPath}\n\`\`\`\n\n**Step 2**: git worktree として再登録（推奨）\n\`\`\`\ncd ${mainRepoStr}\n\`\`\`\n\n\`\`\`\ngit worktree repair\n\`\`\`\n\n**Step 3**: 不要な場合は git worktree prune で整理\n\`\`\`\ncd ${mainRepoStr}\n\`\`\`\n\n\`\`\`\ngit worktree prune\n\`\`\`\n\n**最終手段**: それでも削除が必要な場合（データ損失注意）\n\`\`\`\nFORCE_RM_ORPHAN=1 rm -rf ${orphanPath}\n\`\`\`\n\n⚠️ 注意: rm -rf ではなく git worktree repair/prune を優先してください。`;
  return makeBlockResult("locked-worktree-guard", reason);
}

/**
 * Check if rm command targeting worktree is safe to execute.
 *
 * @param command - The rm command.
 * @param hookCwd - Current working directory from hook input.
 * @returns Block result dict if should block, null if should approve.
 */
export async function checkRmWorktree(
  command: string,
  hookCwd?: string | null,
): Promise<BlockResult | null> {
  const targetWorktrees = await getRmTargetWorktrees(command, hookCwd);
  if (targetWorktrees.length === 0) {
    return null;
  }

  for (const [, worktreePath] of targetWorktrees) {
    if (isCwdInsideWorktree(worktreePath, hookCwd, command)) {
      const mainRepo = await getMainRepoDir();
      const mainRepoStr = mainRepo ?? "/path/to/main/repo";

      const reason = `⚠️ rm コマンドでworktreeを削除しようとしています。\n\n対象: ${worktreePath}\nCWD: ${hookCwd || "unknown"}\n\n現在のディレクトリがworktree内にある状態でworktreeを削除すると、\nシェルセッションが破損し、以降のすべてのコマンドが失敗します。\n\n【対処法】\n1. メインリポジトリに移動: cd ${mainRepoStr}\n2. 正しい方法で削除: git worktree remove ${worktreePath}\n   または: ./scripts/cleanup-worktrees.sh --force\n\n【注意】\nrm -rf ではなく git worktree remove を使用してください。`;
      return makeBlockResult("locked-worktree-guard", reason);
    }
  }

  return null;
}

/**
 * Check if worktree remove command is safe to execute.
 *
 * This function checks ALL worktree remove commands in a chain to prevent
 * bypass vulnerabilities like "git worktree remove safe && git worktree remove locked".
 *
 * @param command - The git worktree remove command (may contain chained commands).
 * @param hookCwd - Current working directory from hook input.
 * @returns Block result dict if should block, null if should approve.
 */
export async function checkWorktreeRemove(
  command: string,
  hookCwd?: string | null,
): Promise<BlockResult | null> {
  // Extract ALL worktree paths from the command (Issue #3169)
  const allWorktreePaths = extractAllWorktreePathsFromCommand(command);
  if (allWorktreePaths.length === 0) {
    return null;
  }

  // Check each worktree path for safety
  for (const [worktreePathStr, baseDir] of allWorktreePaths) {
    // Resolve the path, considering -C flag, cd command, and hookCwd
    // Issue #3386: Expand ~ in paths before resolving
    let worktreePath = expandHome(worktreePathStr);
    let resolvedBaseDir: string | null = null;

    if (!resolve(worktreePath).startsWith("/")) {
      try {
        if (baseDir) {
          let baseDirPath = expandHome(baseDir);
          if (!resolve(baseDirPath).startsWith("/")) {
            baseDirPath = hookCwd
              ? resolve(hookCwd, baseDirPath)
              : resolve(process.cwd(), baseDirPath);
          }
          worktreePath = resolve(baseDirPath, worktreePath);
          try {
            resolvedBaseDir = realpathSync(baseDirPath);
          } catch {
            resolvedBaseDir = baseDirPath;
          }
        } else if (hookCwd) {
          worktreePath = resolve(hookCwd, worktreePath);
        } else {
          worktreePath = resolve(process.cwd(), worktreePath);
        }
      } catch {
        continue; // Skip this path if resolution fails
      }
    } else {
      if (baseDir) {
        let baseDirPath = expandHome(baseDir);
        if (!resolve(baseDirPath).startsWith("/")) {
          if (hookCwd) {
            baseDirPath = resolve(hookCwd, baseDirPath);
          } else {
            try {
              baseDirPath = resolve(process.cwd(), baseDirPath);
            } catch {
              resolvedBaseDir = null;
              baseDirPath = "";
            }
          }
        }
        if (baseDirPath) {
          try {
            resolvedBaseDir = realpathSync(baseDirPath);
          } catch {
            resolvedBaseDir = baseDirPath;
          }
        }
      }
    }

    try {
      worktreePath = realpathSync(worktreePath);
    } catch {
      // Fall back to using the path as-is
    }

    // Check 1: CWD inside target worktree
    if (isCwdInsideWorktree(worktreePath, hookCwd, command)) {
      const mainRepo = await getMainRepoDir();
      const mainRepoStr = mainRepo ?? "/path/to/main/repo";

      const reason = `⚠️ 現在のディレクトリがworktree内です。\n\n対象: ${worktreePath}\nCWD: ${hookCwd || process.cwd()}\n\nworktree内でworktreeを削除すると、カレントディレクトリが無効になり、\n以降のすべてのコマンドが失敗します。\n\n【対処法】以下のコマンドを**1つずつ順番に**実行してください:\n\n\`\`\`\ncd ${mainRepoStr}\n\`\`\`\n\n\`\`\`\ngit worktree remove ${worktreePath}\n\`\`\`\n\n⚠️ 重要: \`cd ... && git worktree remove ...\` のように && で連結しないでください。\n連結するとセッションのcwdが変わらず、削除後にセッションが破損します。`;
      return makeBlockResult("locked-worktree-guard", reason);
    }

    // Check 2: Locked worktree
    const lockedPaths = await getAllLockedWorktreePaths(resolvedBaseDir);

    // Check if unlock is part of the same chained command (before the remove command)
    const removePosition = findGitWorktreeRemovePosition(command);
    const unlockTargets = extractUnlockTargetsFromCommand(command, hookCwd, removePosition);
    const unlockTargetsSet = new Set(unlockTargets);

    for (const lockedPath of lockedPaths) {
      try {
        const lockedResolved = realpathSync(lockedPath);
        if (worktreePath === lockedResolved) {
          // Check if this path is being unlocked in the same command
          if (unlockTargetsSet.has(lockedResolved)) {
            continue;
          }

          const mainRepo = await getMainRepoDir();
          const mainRepoStr = mainRepo ?? "/path/to/main/repo";

          const reason = `⚠️ ロックされたworktreeの削除をブロックしました。\n\n対象: ${worktreePath}\n\nこのworktreeは別のセッションが使用中の可能性があります。\n\n【対処法】以下のいずれかを選択:\n\n**オプション1**: 該当セッションが完了するのを待つ\n\n**オプション2**: ロック解除してから削除（以下を**1つずつ順番に**実行）:\n\n\`\`\`\ncd ${mainRepoStr}\n\`\`\`\n\n\`\`\`\ngit worktree unlock ${worktreePath}\n\`\`\`\n\n\`\`\`\ngit worktree remove ${worktreePath}\n\`\`\`\n\n⚠️ 注意:\n- --force オプションでもロックされたworktreeの削除はブロックされます\n- && で連結せず、1コマンドずつ実行してください`;
          return makeBlockResult("locked-worktree-guard", reason);
        }
      } catch {
        // パス解決失敗、スキップ
      }
    }
  }

  return null;
}
