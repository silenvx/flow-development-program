#!/usr/bin/env bun
/**
 * PostToolUse hook to auto-pull main after PR merge.
 *
 * Why:
 *   PRマージ後にmainブランチを最新化しないと、次のworktree作成時に
 *   古いmainをベースにしてしまう問題を防ぐ。
 *
 * What:
 *   - gh pr merge 成功を検出
 *   - mainリポジトリのmainブランチを自動pull
 *   - worktree内でも親リポジトリのmainをpull
 *
 * Remarks:
 *   - PostToolUse:Bash フック
 *   - 非ブロック型（自動pull実行、失敗時はリマインド表示）
 *   - mainブランチ以外の場合は手動pullをリマインド
 *   - Python版: pr_merge_pull_reminder.py
 *
 * Changelog:
 *   - silenvx/dekita#727: リマインダーから自動実行に変更
 *   - silenvx/dekita#2203: get_exit_code()使用に統一
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { getExitCode, getToolResult } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { getRepoRoot, isMergeSuccess } from "../lib/repo";
import { createContext, getSessionId, parseHookInput } from "../lib/session";

const HOOK_NAME = "pr-merge-pull-reminder";
const TIMEOUT_LIGHT = 5000;
const TIMEOUT_MEDIUM = 30000;

/**
 * Get the current branch name.
 */
export function getCurrentBranch(repoRoot: string): string | null {
  try {
    const result = execSync("git branch --show-current", {
      cwd: repoRoot,
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return result.trim() || null;
  } catch {
    return null;
  }
}

/**
 * Check if current directory is in a worktree.
 */
export function isInWorktree(): boolean {
  const cwd = process.cwd();
  return cwd.includes("/.worktrees/") || cwd.endsWith("/.worktrees");
}

/**
 * Check if the command is a PR merge command.
 * Uses regex to avoid false positives from echo/comments.
 */
export function isPrMergeCommand(command: string): boolean {
  return /^\s*gh\s+pr\s+merge\b/.test(command);
}

/**
 * Pull main branch in the repository root.
 */
function pullMain(repoRoot: string): { success: boolean; output: string } {
  try {
    const result = execSync("git pull origin main", {
      cwd: repoRoot,
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return { success: true, output: result.trim() };
  } catch (error) {
    if (error instanceof Error) {
      const execError = error as { stderr?: string; message: string };
      return {
        success: false,
        output: execError.stderr || execError.message,
      };
    }
    return { success: false, output: "Unknown error during pull" };
  }
}

async function main(): Promise<void> {
  const result: Record<string, unknown> = { continue: true };

  try {
    const data = await parseHookInput();
    const ctx = createContext(data);
    const sessionId = getSessionId(ctx) ?? "unknown";

    const toolName = data.tool_name ?? "";
    if (toolName !== "Bash") {
      console.log(JSON.stringify(result));
      return;
    }

    const toolInput = (data.tool_input as Record<string, unknown>) ?? {};
    const command = (toolInput.command as string) ?? "";

    if (!isPrMergeCommand(command)) {
      console.log(JSON.stringify(result));
      return;
    }

    // Get tool result for exit code and output
    const rawResult = getToolResult(data);
    const toolResult: Record<string, unknown> =
      typeof rawResult === "object" && rawResult !== null
        ? (rawResult as Record<string, unknown>)
        : {};

    const exitCode = getExitCode(rawResult);
    const stdout = String(toolResult.stdout ?? "");
    const stderr = String(toolResult.stderr ?? "");

    if (!isMergeSuccess(exitCode, stdout, command, stderr)) {
      console.log(JSON.stringify(result));
      return;
    }

    const repoRoot = getRepoRoot();
    if (!repoRoot) {
      console.log(JSON.stringify(result));
      return;
    }

    // Handle worktree case
    if (isInWorktree()) {
      const mainRepoBranch = getCurrentBranch(repoRoot);
      if (mainRepoBranch !== "main") {
        // Main repo is not on main branch - show reminder instead
        const message = `PRがマージされました。\nメインリポジトリが${mainRepoBranch}ブランチのため自動pullをスキップ。\n手動でpullしてください:\n  cd ${repoRoot}\n  git checkout main\n  git pull origin main`;

        result.systemMessage = `[${HOOK_NAME}] ${message}`;
        await logHookExecution(
          HOOK_NAME,
          "approve",
          `skipped: main repo on ${mainRepoBranch}, not main`,
          undefined,
          { sessionId },
        );
        console.log(JSON.stringify(result));
        return;
      }

      // Pull main in the main repo
      const { success, output } = pullMain(repoRoot);
      if (success) {
        result.systemMessage = `[${HOOK_NAME}] メインリポジトリでmainを自動pullしました: ${output}`;
        await logHookExecution(
          HOOK_NAME,
          "approve",
          `auto_pull from worktree: ${output}`,
          undefined,
          { sessionId },
        );
      } else {
        result.systemMessage = `[${HOOK_NAME}] main pullに失敗しました: ${output}`;
        await logHookExecution(
          HOOK_NAME,
          "approve",
          `auto_pull failed from worktree: ${output}`,
          undefined,
          { sessionId },
        );
      }
      console.log(JSON.stringify(result));
      return;
    }

    // Not in worktree - check current branch
    const currentBranch = getCurrentBranch(repoRoot);
    if (currentBranch !== "main") {
      const message =
        "PRがマージされました。" +
        "mainブランチに切り替えてpullしてください: git checkout main && git pull origin main";
      result.systemMessage = `[${HOOK_NAME}] ${message}`;
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `reminder: not on main (current: ${currentBranch})`,
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // On main branch - auto pull
    const { success, output } = pullMain(repoRoot);
    if (success) {
      result.systemMessage = `[${HOOK_NAME}] mainブランチを自動pullしました: ${output}`;
      await logHookExecution(HOOK_NAME, "approve", `auto_pull on main: ${output}`, undefined, {
        sessionId,
      });
    } else {
      result.systemMessage = `[${HOOK_NAME}] main pullに失敗しました: ${output}`;
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `auto_pull failed on main: ${output}`,
        undefined,
        { sessionId },
      );
    }
  } catch {
    // Don't block Claude Code on hook failures
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
