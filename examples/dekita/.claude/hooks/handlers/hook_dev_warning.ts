#!/usr/bin/env bun
/**
 * worktree内でのフック開発時に変更が反映されない問題を警告する。
 *
 * Why:
 *   worktree内でフックを修正しても、CLAUDE_PROJECT_DIRがmainリポジトリを
 *   指すため、修正が反映されない。この問題を開発者に警告する。
 *
 * What:
 *   - cwdがworktree内かどうかをチェック
 *   - worktree内で.claude/hooks/や.claude/scripts/に変更があれば警告
 *   - mainにマージするまで変更が反映されないことを通知
 *
 * Remarks:
 *   - ブロックせず警告のみ
 *   - CLAUDE_PROJECT_DIRがmainを指す問題に特化
 *
 * Changelog:
 *   - silenvx/dekita#1132: フック追加
 *   - silenvx/dekita#2917: TypeScriptに移植
 */

import { spawn } from "node:child_process";
import { TIMEOUT_LIGHT } from "../lib/constants";
import { isInWorktree } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

interface SpawnResult {
  stdout: string;
  stderr: string;
  exitCode: number | null;
}

/**
 * Run a command with timeout support.
 */
async function runCommand(
  command: string,
  args: string[],
  options: { timeout?: number; cwd?: string } = {},
): Promise<SpawnResult> {
  const { timeout = TIMEOUT_LIGHT, cwd } = options;

  return new Promise((resolve) => {
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
        resolve({ stdout: "", stderr: "Timeout", exitCode: null });
      } else {
        resolve({ stdout, stderr, exitCode });
      }
    });

    proc.on("error", () => {
      clearTimeout(timer);
      resolve({ stdout: "", stderr: "Error", exitCode: null });
    });
  });
}

/**
 * Extract worktree root from a given path if in a worktree.
 * This is a pure function that can be tested without mocking process.cwd().
 */
export function extractWorktreeRootFromPath(path: string): string | null {
  if (!path.includes("/.worktrees/")) {
    return null;
  }
  const idx = path.indexOf("/.worktrees/");
  const after = path.slice(idx + "/.worktrees/".length);
  const worktreeName = after.split("/")[0];
  if (!worktreeName) {
    return null;
  }
  return path.slice(0, idx + "/.worktrees/".length) + worktreeName;
}

/**
 * Extract worktree root from cwd if in a worktree.
 */
function getWorktreeRoot(): string | null {
  return extractWorktreeRootFromPath(process.cwd());
}

/**
 * Get list of modified files under .claude/hooks/ and .claude/scripts/.
 */
async function getModifiedHookFiles(worktreeRoot: string): Promise<string[]> {
  try {
    const result = await runCommand(
      "git",
      ["-C", worktreeRoot, "status", "--porcelain", ".claude/hooks/", ".claude/scripts/"],
      { timeout: TIMEOUT_LIGHT },
    );

    if (result.exitCode !== 0) {
      return [];
    }

    const stdout = result.stdout.trim();
    if (!stdout) {
      return [];
    }

    const lines = stdout.split("\n");
    // git status --porcelain format: 2 status chars + 1 space = 3 chars prefix
    return lines.filter((line) => line.trim()).map((line) => line.slice(3));
  } catch {
    return [];
  }
}

/**
 * Output result and exit with logging for skip case.
 */
async function outputContinueAndLogSkip(
  hookName: string,
  reason: string,
  sessionId?: string,
): Promise<void> {
  await logHookExecution(hookName, "approve", reason, {}, { sessionId });
  console.log(JSON.stringify({ continue: true }));
  process.exit(0);
}

async function main(): Promise<void> {
  // Get sessionId from hook input
  const hookInput = await parseHookInput();
  const sessionId = hookInput.session_id;

  if (!isInWorktree()) {
    await outputContinueAndLogSkip("hook-dev-warning", "not in worktree", sessionId);
    return;
  }

  const worktreeRoot = getWorktreeRoot();
  if (!worktreeRoot) {
    await outputContinueAndLogSkip("hook-dev-warning", "worktree root not found", sessionId);
    return;
  }

  const modifiedHooks = await getModifiedHookFiles(worktreeRoot);
  if (modifiedHooks.length === 0) {
    await outputContinueAndLogSkip("hook-dev-warning", "no modified hooks", sessionId);
    return;
  }

  // Build warning message
  const projectDir = process.env.CLAUDE_PROJECT_DIR ?? "";
  let fileList = modifiedHooks
    .slice(0, 5)
    .map((f) => `  - ${f}`)
    .join("\n");
  if (modifiedHooks.length > 5) {
    fileList += `\n  ... 他 ${modifiedHooks.length - 5} 件`;
  }

  // Log the warning
  await logHookExecution(
    "hook-dev-warning",
    "warn",
    `Modified hooks in worktree not active: ${modifiedHooks.length} files`,
    { modified_hooks: modifiedHooks, worktree_root: worktreeRoot },
    { sessionId },
  );

  const message = `⚠️ **Worktree内でフックを開発中ですが、メインリポジトリのフックが使用されます**

変更されたフックファイル:
${fileList}

CLAUDE_PROJECT_DIRがメインリポジトリを指しているため、これらの変更は反映されません:
  CLAUDE_PROJECT_DIR = ${projectDir}

【対処法】
1. PRをマージして変更を本番反映
2. またはClaude Codeを以下で再起動:
   \`\`\`
   CLAUDE_PROJECT_DIR=${worktreeRoot} claude
   \`\`\`

**影響**: merge-check等のフックが期待通りに動作しない可能性があります。
`;

  console.log(JSON.stringify({ continue: true, message }));
  process.exit(0);
}

if (import.meta.main) {
  main();
}
