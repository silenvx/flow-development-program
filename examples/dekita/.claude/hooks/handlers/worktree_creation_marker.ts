#!/usr/bin/env bun
/**
 * worktree作成時にセッションIDをマーカーファイルとして記録する。
 *
 * Why:
 *   worktreeの所有者（作成セッション）を記録することで、
 *   別セッションによる誤介入を防止できる。
 *
 * What:
 *   - git worktree addコマンドの成功を検出
 *   - 作成されたworktreeにセッションIDを.claude-sessionとして記録
 *   - worktree-session-guard.pyがこのマーカーを参照
 *
 * When:
 *   - PostToolUse（Bashコマンド実行後）
 *
 * State:
 *   - writes: .worktrees/<name>/.claude-session
 *
 * Remarks:
 *   - ブロックせず情報記録のみ
 *   - worktree-session-guard.pyと連携（マーカー作成→マーカー検証）
 *   - JSON形式でsession_idとcreated_atを記録
 *   - Python版: worktree_creation_marker.py
 *
 * Changelog:
 *   - silenvx/dekita#1396: フック追加
 *   - silenvx/dekita#1842: get_tool_result()ヘルパー使用
 *   - silenvx/dekita#2986: TypeScript版に移植
 */

import { existsSync, renameSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { SESSION_MARKER_FILE } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { getExitCode, getToolResult } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";
import { splitShellArgs, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "worktree-creation-marker";

// Common command wrappers that may precede git
export const COMMAND_WRAPPERS = new Set([
  "sudo",
  "env",
  "time",
  "nice",
  "ionice",
  "timeout",
  "strace",
  "ltrace",
  "nohup",
  "command",
  "builtin",
  "exec",
]);

// Options that take an argument
export const OPTIONS_WITH_ARG = new Set(["-b", "-B", "--orphan", "--reason"]);

// Options without argument
export const OPTIONS_NO_ARG = new Set([
  "-f",
  "--force",
  "-d",
  "--detach",
  "--checkout",
  "--no-checkout",
  "--lock",
  "-q",
  "--quiet",
  "--track",
  "--no-track",
  "--guess-remote",
  "--no-guess-remote",
]);

/**
 * Extract worktree path from git worktree add command.
 */
export function extractWorktreeAddPath(command: string): string | null {
  // Strip quoted strings to avoid false positives
  const stripped = stripQuotedStrings(command);

  // Check if this contains "git worktree add"
  if (
    !/\bgit\b.*\bworktree\s+add\b/.test(stripped) &&
    !/\/git\s+.*\bworktree\s+add\b/.test(stripped)
  ) {
    return null;
  }

  let tokens: string[];
  try {
    tokens = splitShellArgs(command);
  } catch {
    // Fall back to simple split if splitShellArgs fails
    tokens = command.split(/\s+/);
  }

  // Shell command separators
  const shellSeparators = new Set(["&&", "||", ";", "|"]);

  // Find "git", "worktree", and "add" positions
  let gitIdx: number | null = null;
  let worktreeIdx: number | null = null;
  let addIdx: number | null = null;

  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];

    // Reset when we hit a shell separator
    if (shellSeparators.has(token) || token.endsWith(";")) {
      gitIdx = null;
      worktreeIdx = null;
      addIdx = null;
      continue;
    }

    // Skip command wrappers and env var assignments
    if (COMMAND_WRAPPERS.has(token) || /^\w+=/.test(token)) {
      continue;
    }

    // Find git command (handle absolute paths like /usr/bin/git)
    if (token === "git" || token.endsWith("/git")) {
      gitIdx = i;
      worktreeIdx = null;
      addIdx = null;
    } else if (token === "worktree" && gitIdx !== null) {
      worktreeIdx = i;
    } else if (token === "add" && worktreeIdx !== null) {
      addIdx = i;
      break;
    }
  }

  if (addIdx === null) {
    return null;
  }

  // Find the path argument (skip options)
  let i = addIdx + 1;
  while (i < tokens.length) {
    const token = tokens[i];
    if (token.startsWith("-")) {
      if (OPTIONS_WITH_ARG.has(token)) {
        // Skip this option and its argument
        i += 2;
      } else if (OPTIONS_NO_ARG.has(token) || token.startsWith("--")) {
        // Skip this option (including unknown --options)
        i += 1;
      } else {
        // Unknown short option, skip
        i += 1;
      }
    } else {
      // This should be the path
      return token;
    }
    if (i >= tokens.length) {
      break;
    }
  }

  return null;
}

/**
 * Write session marker to worktree.
 */
export function writeSessionMarker(sessionId: string, worktreePath: string): boolean {
  try {
    const markerPath = join(worktreePath, SESSION_MARKER_FILE);
    const markerData = {
      session_id: sessionId,
      created_at: new Date().toISOString(),
    };

    // Write atomically: write to temp file then rename
    const tempPath = `${markerPath}.tmp`;
    writeFileSync(tempPath, JSON.stringify(markerData));
    renameSync(tempPath, markerPath);
    return true;
  } catch {
    return false;
  }
}

async function main(): Promise<void> {
  const result = { continue: true };

  try {
    const data = await parseHookInput();
    const toolName = data.tool_name;
    const toolInput = (data.tool_input as Record<string, unknown>) ?? {};
    const toolResult = getToolResult(data) ?? {};

    // Only process Bash commands
    if (toolName !== "Bash") {
      console.log(JSON.stringify(result));
      return;
    }

    const command = (toolInput.command as string) ?? "";

    // Only process git worktree add commands
    if (!command.includes("worktree add")) {
      console.log(JSON.stringify(result));
      return;
    }

    // Check if command succeeded
    const exitCode = getExitCode(toolResult, 0);
    if (exitCode !== 0) {
      const msg = `worktree add failed with exit code ${exitCode}`;
      await logHookExecution(HOOK_NAME, "approve", msg);
      console.log(JSON.stringify(result));
      return;
    }

    // Extract worktree path from command
    const worktreePathStr = extractWorktreeAddPath(command);
    if (!worktreePathStr) {
      const msg = "Could not extract worktree path from command";
      await logHookExecution(HOOK_NAME, "approve", msg);
      console.log(JSON.stringify(result));
      return;
    }

    // Resolve to absolute path
    let worktreePath: string;
    if (worktreePathStr.startsWith("/")) {
      worktreePath = worktreePathStr;
    } else {
      const projectDir = process.env.CLAUDE_PROJECT_DIR;
      if (projectDir) {
        worktreePath = resolve(projectDir, worktreePathStr);
      } else {
        worktreePath = resolve(process.cwd(), worktreePathStr);
      }
    }

    // Check if worktree exists
    if (!existsSync(worktreePath)) {
      const msg = `Worktree path does not exist: ${worktreePath}`;
      await logHookExecution(HOOK_NAME, "approve", msg);
      console.log(JSON.stringify(result));
      return;
    }

    // Get session ID
    const sessionId = (data.session_id as string) ?? "unknown";
    const worktreeName = worktreePath.split("/").pop() ?? worktreePath;

    // Write session marker
    if (writeSessionMarker(sessionId, worktreePath)) {
      const msg = `Recorded session marker in ${worktreeName}: ${sessionId.slice(0, 16)}...`;
      await logHookExecution(HOOK_NAME, "approve", msg);
    } else {
      const msg = `Failed to write session marker to ${worktreeName}`;
      await logHookExecution(HOOK_NAME, "approve", msg);
    }
  } catch (error) {
    // Fail open - don't affect the operation
    const msg = `Hook error: ${formatError(error)}`;
    console.error(`[${HOOK_NAME}] ${msg}`);
    await logHookExecution(HOOK_NAME, "approve", msg);
  }

  console.log(JSON.stringify(result));
}

// Only run main when executed directly, not when imported
if (import.meta.main) {
  main();
}
