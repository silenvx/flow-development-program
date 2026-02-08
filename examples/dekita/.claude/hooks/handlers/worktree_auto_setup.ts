#!/usr/bin/env bun
/**
 * worktree作成成功後にsetup_worktree.shを自動実行。
 *
 * Why:
 *   worktree作成後に依存インストールを忘れると、pre-pushフック等が失敗する。
 *   自動実行することで、依存インストール漏れを防ぐ。
 *
 * What:
 *   - git worktree add成功後（PostToolUse:Bash）に発火
 *   - コマンドからworktreeパスを抽出
 *   - .claude/scripts/setup_worktree.shを実行
 *   - 結果をsystemMessageで通知
 *
 * Remarks:
 *   - 自動化型フック（worktree作成成功後に即座に実行）
 *   - setup_worktree.shがプロジェクト種別を自動検出（pnpm/npm等）
 *   - 失敗時は警告のみ（ブロックしない）
 *
 * Changelog:
 *   - silenvx/dekita#1299: フック追加（pre-pushフック失敗防止）
 *   - silenvx/dekita#2607: HookContextパターン移行
 *   - silenvx/dekita#3516: TypeScript版に移植
 */

import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { TIMEOUT_EXTENDED } from "../lib/constants";
import { getEffectiveCwd } from "../lib/cwd";
import { formatError } from "../lib/format_error";
import { getToolResult } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { createHookContext, parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";

const HOOK_NAME = "worktree-auto-setup";

/**
 * Extract worktree path from git worktree add command.
 *
 * Handles:
 * - Double-quoted paths: git worktree add ".worktrees/foo bar" main
 * - Single-quoted paths: git worktree add '.worktrees/foo bar' main
 * - Unquoted paths: git worktree add .worktrees/foo-bar main
 *
 * @param command - The command string containing git worktree add.
 * @param cwd - Current working directory.
 * @returns Absolute path to worktree if found, null otherwise.
 */
export function extractWorktreePath(command: string, cwd: string): string | null {
  // Match .worktrees/ pattern with support for quoted paths
  // Pattern handles:
  // 1. Double-quoted: ".worktrees/foo bar"
  // 2. Single-quoted: '.worktrees/foo bar'
  // 3. Unquoted: .worktrees/foo-bar (no spaces)
  // Support quoted paths to handle spaces correctly
  const pattern = /(?:"(\.worktrees\/[^"]+)"|'(\.worktrees\/[^']+)'|(\.worktrees\/[^\s;|&'"()`]+))/;
  const match = command.match(pattern);

  if (match) {
    // Return the first non-undefined capture group (quoted or unquoted path)
    const worktreeRel = match[1] ?? match[2] ?? match[3];
    if (worktreeRel) {
      const worktreePath = resolve(cwd, worktreeRel);
      if (existsSync(worktreePath)) {
        return worktreePath;
      }
    }
  }

  return null;
}

/**
 * Run setup_worktree.sh for the given worktree.
 *
 * @param worktreePath - Absolute path to the worktree.
 * @returns Tuple of [success, message].
 */
export async function runSetupWorktree(worktreePath: string): Promise<[boolean, string]> {
  // Find setup_worktree.sh script
  const projectDir = process.env.CLAUDE_PROJECT_DIR ?? "";
  if (!projectDir) {
    return [false, "CLAUDE_PROJECT_DIR not set"];
  }

  const scriptPath = join(projectDir, ".claude", "scripts", "setup_worktree.sh");
  if (!existsSync(scriptPath)) {
    return [false, `setup_worktree.sh not found at ${scriptPath}`];
  }

  try {
    const result = await asyncSpawn("bash", [scriptPath, worktreePath], {
      timeout: TIMEOUT_EXTENDED * 1000, // 60 seconds for pnpm install
      cwd: projectDir,
    });

    if (result.success) {
      return [true, "Dependencies installed successfully"];
    }
    const errorOutput = result.stderr.trim() || result.stdout.trim() || "Unknown error";
    return [false, `setup_worktree.sh failed: ${errorOutput.slice(0, 200)}`];
  } catch (e) {
    if (e instanceof Error && e.message.includes("timeout")) {
      return [false, "setup_worktree.sh timed out"];
    }
    return [false, `Failed to run setup_worktree.sh: ${formatError(e)}`];
  }
}

/**
 * Print continue result and log skip reason.
 */
function printContinueAndLogSkip(reason: string, sessionId?: string | null): void {
  logHookExecution(HOOK_NAME, "skip", reason, undefined, {
    sessionId: sessionId ?? undefined,
  });
  console.log(JSON.stringify({ continue: true }));
}

async function main(): Promise<void> {
  const result: Record<string, unknown> = { continue: true };

  try {
    const inputData = await parseHookInput();
    // Issue #2607: Create context for session_id logging
    const ctx = createHookContext(inputData);
    const toolInput = inputData.tool_input ?? {};
    // Use standardized utility for tool_result/tool_response/tool_output handling
    const toolResult = (getToolResult(inputData as Record<string, unknown>) ?? {}) as Record<
      string,
      unknown
    >;

    const command = typeof toolInput.command === "string" ? toolInput.command : "";
    // Default to 0 (success) if exit_code not provided
    const exitCodeValue = toolResult.exit_code ?? toolResult.exitCode ?? 0;
    const exitCode = typeof exitCodeValue === "number" ? exitCodeValue : 0;

    // Check if this is a git worktree add command
    if (!command.match(/\bgit\s+worktree\s+add\b/)) {
      printContinueAndLogSkip("not a git worktree add command", ctx.sessionId);
      return;
    }

    // Only run on success
    if (exitCode !== 0) {
      printContinueAndLogSkip(`command failed: exit_code=${exitCode}`, ctx.sessionId);
      return;
    }

    // Get effective cwd - use CLAUDE_PROJECT_DIR as base since .worktrees/ is at project root
    const cwd = getEffectiveCwd(command);
    const projectDir = process.env.CLAUDE_PROJECT_DIR ?? cwd;

    // Extract worktree path (resolve against projectDir, not cwd)
    const worktreePath = extractWorktreePath(command, projectDir);
    if (!worktreePath) {
      await logHookExecution(HOOK_NAME, "skip", "Could not extract worktree path from command", {
        command: command.slice(0, 100),
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Run setup_worktree.sh
    // Note: setup_worktree.sh handles project type detection (package.json, pyproject.toml)
    // so we don't duplicate that check here
    const [success, message] = await runSetupWorktree(worktreePath);
    const worktreeName = worktreePath.split("/").pop() ?? "";

    if (success) {
      result.systemMessage = `✅ worktree自動セットアップ完了: ${worktreeName}\n   node_modules がインストールされました。`;
      await logHookExecution(HOOK_NAME, "approve", message, { worktree: worktreePath });
    } else {
      // Don't block, just warn
      result.systemMessage =
        `⚠️ worktree自動セットアップ失敗: ${worktreeName}\n` +
        `   ${message}\n` +
        `   手動で実行してください: .claude/scripts/setup_worktree.sh .worktrees/${worktreeName}`;
      await logHookExecution(HOOK_NAME, "warn", message, { worktree: worktreePath });
    }
  } catch (e) {
    // Don't block on errors
    await logHookExecution(HOOK_NAME, "error", String(e));
  }

  console.log(JSON.stringify(result));
}

// Only run main when executed directly, not when imported
if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
  });
}
