#!/usr/bin/env bun
/**
 * gh pr merge --delete-branch のworktree起因エラー検出フック
 *
 * Why:
 *   `gh pr merge --delete-branch`でPRマージは成功したが、worktree使用中のため
 *   ローカルブランチ削除に失敗した場合、exit code 1が返される。
 *   Claude Codeはこれをマージ失敗と誤認識し、不要なリトライや混乱を起こす可能性がある。
 *
 * What:
 *   - PostToolUse:Bashで`gh pr merge`の結果を確認
 *   - exit code 1 かつ "failed to delete local branch" を含む場合
 *   - PRの実際の状態を確認し、マージ済みならworktree削除を案内
 *
 * Remarks:
 *   - ブロックはしない（情報提供のみ）
 *   - PR番号が取得できない場合はスキップ
 *   - ネットワークエラー時は安全にスキップ（fail-open）
 *
 * Changelog:
 *   - silenvx/dekita#3587: 初期実装
 */

import { formatError } from "../lib/format_error";
import { getCurrentBranch } from "../lib/git";
import {
  extractPrNumberFromUrl,
  getCommandName,
  getPrNumberForBranch,
  isPrMerged,
  normalizeShellSeparators,
  parseAllGhPrCommands,
  tokenize,
} from "../lib/github";
import { getExitCode, getToolResult } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "merge-result-check";

/**
 * Check if command is a gh pr merge command with --delete-branch option.
 * Uses parseAllGhPrCommands to correctly handle global flags like -R/--repo.
 * Scopes the delete-branch flag detection to the merge command segment only.
 */
export function isMergeWithDeleteBranch(command: string): boolean {
  // Use parseAllGhPrCommands to quickly check if a merge command exists
  // This avoids manual token iteration if there's no merge command
  const allCommands = parseAllGhPrCommands(command);
  const hasMergeCommand = allCommands.some(([subcommand]) => subcommand === "merge");
  if (!hasMergeCommand) {
    return false;
  }

  // Normalize shell separators to handle operators without spaces (e.g. `cmd1&&cmd2`)
  // This is consistent with parseAllGhPrCommands behavior
  const normalized = normalizeShellSeparators(command);

  // Tokenize properly to handle quotes and shell operators
  // This prevents issues like `gh pr merge --title "Updates & fixes"` being split at &
  let tokens: string[];
  try {
    tokens = tokenize(normalized);
  } catch {
    // Fallback to simple split if tokenize fails
    tokens = normalized.split(/\s+/);
  }

  const separators = new Set([";", "&&", "||", "&", "|"]);
  const segments: string[][] = [];
  let currentSegment: string[] = [];

  for (const token of tokens) {
    if (separators.has(token)) {
      if (currentSegment.length > 0) {
        segments.push(currentSegment);
      }
      currentSegment = [];
    } else {
      currentSegment.push(token);
    }
  }
  if (currentSegment.length > 0) {
    segments.push(currentSegment);
  }

  for (const segment of segments) {
    // Check if this segment looks like a gh pr merge command
    // Use getCommandName to handle absolute paths like /usr/bin/gh
    const ghIdx = segment.findIndex((t) => getCommandName(t) === "gh");
    const prIdx = segment.indexOf("pr");
    const mergeIdx = segment.indexOf("merge");

    // Must have gh, pr, merge in correct order
    if (ghIdx !== -1 && prIdx !== -1 && mergeIdx !== -1 && ghIdx < prIdx && prIdx < mergeIdx) {
      // Check for delete flag in this specific segment
      if (segment.includes("-d") || segment.includes("--delete-branch")) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Check if the output indicates a local branch deletion failure.
 */
export function hasBranchDeletionFailure(stdout: string, stderr: string): boolean {
  const output = `${stdout}\n${stderr}`.toLowerCase();
  // Various patterns for branch deletion failure
  // Include Japanese localized messages for users with localized Git/CLI
  return (
    output.includes("failed to delete local branch") ||
    output.includes("could not delete local branch") ||
    output.includes("cannot delete branch") ||
    output.includes("error deleting branch") ||
    output.includes("ブランチを削除できません") ||
    output.includes("ブランチの削除に失敗")
  );
}

async function main(): Promise<void> {
  const result: Record<string, unknown> = { continue: true };
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    const ctx = createHookContext(inputData);
    sessionId = ctx.sessionId;
    const toolName = inputData.tool_name ?? "";

    if (toolName !== "Bash") {
      console.log(JSON.stringify(result));
      return;
    }

    const toolInput = (inputData.tool_input ?? {}) as Record<string, unknown>;
    const rawToolResult = getToolResult(inputData as Record<string, unknown>);

    // Validate toolResult is an object before use
    if (!rawToolResult || typeof rawToolResult !== "object") {
      console.log(JSON.stringify(result));
      return;
    }
    const toolResult = rawToolResult as Record<string, unknown>;

    const command = String(toolInput.command ?? "");
    const exitCode = getExitCode(toolResult);
    const stdout = typeof toolResult.stdout === "string" ? toolResult.stdout : "";
    const stderr = typeof toolResult.stderr === "string" ? toolResult.stderr : "";

    // Skip if not a merge command with --delete-branch
    if (!isMergeWithDeleteBranch(command)) {
      console.log(JSON.stringify(result));
      return;
    }

    // Skip unless exit code is 1 (delete-branch failure signal)
    // Other exit codes indicate different types of errors
    if (exitCode !== 1) {
      console.log(JSON.stringify(result));
      return;
    }

    // Check for branch deletion failure pattern
    if (!hasBranchDeletionFailure(stdout, stderr)) {
      console.log(JSON.stringify(result));
      return;
    }

    // Extract PR number from merge command specifically
    // Issue: extractPrNumber returns the first PR in chained commands,
    // which may not be the merge command. Use parseAllGhPrCommands to find
    // the actual merge command's PR number.
    // Note: In `gh pr merge A ; gh pr merge B`, exit code is from B. `findLast` is correct.
    // Limitation: In `gh pr merge A && gh pr merge B`, if A fails, B doesn't run,
    // but `findLast` still picks B, potentially checking the wrong PR.
    // We accept this risk as "branch deletion failure" is the primary trigger and
    // chained merges are rare.
    const allCommands = parseAllGhPrCommands(command);
    const mergeCommand = allCommands.findLast(([subcommand]) => subcommand === "merge");
    if (!mergeCommand) {
      await logHookExecution(HOOK_NAME, "skip", "No merge command found", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    const [, prNumberArg, repo, , mergeTarget] = mergeCommand;
    let prNumber = prNumberArg;

    // If PR number is not explicitly provided, try to resolve from mergeTarget or current branch
    // This handles:
    // 1. `gh pr merge <branch> --delete-branch` - resolve via branch name
    // 2. `gh pr merge <url> --delete-branch` - extract from URL
    // 3. `gh pr merge --delete-branch` - resolve via current branch
    if (!prNumber && mergeTarget) {
      // Try to extract PR number from URL first
      const fromUrl = extractPrNumberFromUrl(mergeTarget);
      if (fromUrl) {
        prNumber = fromUrl;
      } else {
        // mergeTarget is a branch name, resolve via gh pr view
        // Pass repo if --repo option was specified in the command
        prNumber = await getPrNumberForBranch(mergeTarget, repo);
      }
    }
    if (!prNumber) {
      // Fallback to current branch
      const currentBranch = await getCurrentBranch();
      if (currentBranch) {
        prNumber = await getPrNumberForBranch(currentBranch);
      }
    }

    if (!prNumber) {
      await logHookExecution(
        HOOK_NAME,
        "skip",
        "PR number not found in merge command or current branch",
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Check if PR is actually merged
    const merged = await isPrMerged(prNumber, repo);

    if (merged) {
      await logHookExecution(
        HOOK_NAME,
        "info",
        `PR #${prNumber} is merged despite exit code 1`,
        {
          pr_number: prNumber,
          exit_code: exitCode,
          repo: repo,
        },
        { sessionId },
      );

      // Provide informational message to Claude Code
      const message = `✅ **PR #${prNumber} はマージ済みです**

exit code 1はworktree使用中のためローカルブランチ削除に失敗したことを示しています。
PRのマージ自体は成功しています。

**対処方法**:
1. \`git worktree remove .worktrees/<worktree-name>\` でworktreeを削除
2. その後、\`git branch -d <branch-name>\` でローカルブランチを削除`;

      console.log(
        JSON.stringify({
          continue: true,
          systemMessage: message,
        }),
      );
      return;
    }

    // PR is not merged - this is a real failure
    await logHookExecution(
      HOOK_NAME,
      "info",
      `PR #${prNumber} merge failed`,
      {
        pr_number: prNumber,
        exit_code: exitCode,
        repo: repo,
      },
      { sessionId },
    );
  } catch (e) {
    await logHookExecution(HOOK_NAME, "error", `Hook error: ${formatError(e)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
  });
}
