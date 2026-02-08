#!/usr/bin/env bun
/**
 * mainブランチでの編集をブロックし、worktreeでの作業を強制する。
 *
 * Why:
 *   mainで直接編集すると競合やレビューなしの変更が発生するリスクがある。
 *   ロック中のworktreeは別セッションが作業中の可能性がある。
 *
 * What:
 *   - main/masterブランチでEdit/Write時にブロック
 *   - ロック中worktreeでの編集時に警告
 *   - worktree作成手順を提示
 *
 * Remarks:
 *   - ブロック型フック（mainでの編集はブロック）
 *   - .claude/plans/は例外として許可（Issue #844）
 *   - worktree-session-guardはセッション間競合、本フックはブランチ保護
 *
 * Changelog:
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { spawn } from "node:child_process";
import { existsSync, realpathSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult, outputResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "worktree-warning";

// Branches that should block editing
const PROTECTED_BRANCHES = new Set(["main", "master"]);

/**
 * Normalize path separators to forward slashes (for Windows compatibility).
 */
function normalizePath(path: string): string {
  return path.replace(/\\/g, "/");
}

// Paths allowed to edit even on protected branches
const ALLOWLIST_PATH_PREFIXES = [".claude/plans/"];

/**
 * Run a command with timeout support.
 */
async function runCommand(
  command: string,
  args: string[],
  timeout: number = TIMEOUT_LIGHT,
  cwd?: string,
): Promise<{ stdout: string; exitCode: number | null }> {
  return new Promise((resolve) => {
    const options: { stdio: ["pipe", "pipe", "pipe"]; cwd?: string } = {
      stdio: ["pipe", "pipe", "pipe"],
    };
    if (cwd) {
      options.cwd = cwd;
    }

    const proc = spawn(command, args, options);

    let stdout = "";
    let killed = false;

    const timer = setTimeout(() => {
      killed = true;
      proc.kill("SIGTERM");
    }, timeout * 1000);

    proc.stdout?.on("data", (data) => {
      stdout += data.toString();
    });

    proc.on("close", (exitCode) => {
      clearTimeout(timer);
      if (killed) {
        resolve({ stdout: "", exitCode: null });
      } else {
        resolve({ stdout, exitCode });
      }
    });

    proc.on("error", () => {
      clearTimeout(timer);
      resolve({ stdout: "", exitCode: null });
    });
  });
}

/**
 * Check if a file path is in the allowlist for editing on protected branches.
 */
export function isPathInAllowlist(filePath: string, projectRoot: string): boolean {
  if (!projectRoot || !filePath) {
    return false;
  }

  // Normalize path separators for Windows compatibility
  const filePathNorm = normalizePath(filePath);
  const projectRootNorm = normalizePath(projectRoot).replace(/\/+$/, "");

  let relPath: string;
  if (filePathNorm.startsWith(`${projectRootNorm}/`)) {
    relPath = filePathNorm.slice(projectRootNorm.length + 1);
  } else if (filePathNorm === projectRootNorm) {
    return false;
  } else {
    return false;
  }

  return ALLOWLIST_PATH_PREFIXES.some((prefix) => relPath.startsWith(prefix));
}

/**
 * Detect if a file path is a misplaced plan file (e.g. written to a subdirectory
 * instead of the project root's .claude/plans/).
 * Returns the correct path if misplaced, or null if not a plan file issue.
 */
export function detectMisplacedPlanPath(filePath: string, projectRoot: string): string | null {
  if (!filePath || !projectRoot) {
    return null;
  }

  const filePathNorm = normalizePath(filePath);
  const projectRootNorm = normalizePath(projectRoot).replace(/\/+$/, "");

  // Must be inside the project
  if (!filePathNorm.startsWith(`${projectRootNorm}/`)) {
    return null;
  }

  // Check if path contains .claude/plans/ but is NOT in the allowlist
  if (!filePathNorm.includes(".claude/plans/")) {
    return null;
  }

  // If it's already in the correct location, no issue
  if (isPathInAllowlist(filePath, projectRoot)) {
    return null;
  }

  // Extract the relative path within .claude/plans/
  const marker = ".claude/plans/";
  const idx = filePathNorm.lastIndexOf(marker);
  const planSubPath = filePathNorm.slice(idx + marker.length);
  return `${projectRootNorm}/.claude/plans/${planSubPath}`;
}

/**
 * Get the current git branch for the given file path.
 */
async function getCurrentBranch(filePath: string): Promise<string> {
  // Allow override for testing
  const testBranch = process.env.CLAUDE_TEST_BRANCH;
  if (testBranch !== undefined) {
    return testBranch;
  }

  // Try to find a valid directory to run git from
  let cwd: string | undefined;

  if (filePath) {
    let parent = dirname(filePath);
    while (parent && !existsSync(parent)) {
      const newParent = dirname(parent);
      if (newParent === parent) break;
      parent = newParent;
    }
    if (parent && existsSync(parent)) {
      try {
        const stat = statSync(parent);
        if (stat.isDirectory()) {
          cwd = parent;
        }
      } catch {
        // Ignore
      }
    }
  }

  // Fall back to project root
  if (!cwd) {
    cwd = process.env.CLAUDE_PROJECT_DIR;
  }

  if (!cwd || !existsSync(cwd)) {
    return "";
  }

  const result = await runCommand("git", ["rev-parse", "--abbrev-ref", "HEAD"], TIMEOUT_LIGHT, cwd);

  return result.exitCode === 0 ? result.stdout.trim() : "";
}

/**
 * Get the git repository root for the given file path.
 */
async function getProjectRoot(filePath: string): Promise<string> {
  const proj = process.env.CLAUDE_PROJECT_DIR;
  if (proj) {
    return proj;
  }

  if (!filePath) {
    return "";
  }

  const cwd = dirname(filePath);
  if (!existsSync(cwd)) {
    return "";
  }

  const result = await runCommand("git", ["rev-parse", "--show-toplevel"], TIMEOUT_LIGHT, cwd);

  return result.exitCode === 0 ? result.stdout.trim() : "";
}

/**
 * Extract the worktree root directory from a file path.
 */
export function extractWorktreeRoot(filePath: string): string | null {
  // Normalize path separators for Windows compatibility
  const filePathNorm = normalizePath(filePath);
  const marker = ".worktrees/";
  if (!filePathNorm.includes(marker)) {
    return null;
  }

  const idx = filePathNorm.indexOf(marker);
  const afterMarker = filePathNorm.slice(idx + marker.length);
  const worktreeName = afterMarker.includes("/") ? afterMarker.split("/")[0] : afterMarker;

  return filePathNorm.slice(0, idx + marker.length) + worktreeName;
}

/**
 * Check if a worktree is locked and get the lock reason.
 */
async function getWorktreeLockInfo(worktreePath: string): Promise<[boolean, string | null]> {
  try {
    // Get git common dir to run worktree list from main repo
    const commonResult = await runCommand(
      "git",
      ["rev-parse", "--git-common-dir"],
      TIMEOUT_LIGHT,
      worktreePath,
    );

    if (commonResult.exitCode !== 0) {
      return [false, null];
    }

    let gitCommon = commonResult.stdout.trim();
    if (!gitCommon.startsWith("/")) {
      gitCommon = resolve(worktreePath, gitCommon);
    }
    const mainRepo = dirname(gitCommon);

    // List all worktrees with porcelain format
    const listResult = await runCommand(
      "git",
      ["worktree", "list", "--porcelain"],
      TIMEOUT_MEDIUM,
      mainRepo,
    );

    if (listResult.exitCode !== 0) {
      return [false, null];
    }

    // Parse porcelain output
    let worktreePathResolved: string;
    try {
      worktreePathResolved = realpathSync(worktreePath);
    } catch {
      worktreePathResolved = resolve(worktreePath);
    }

    let currentWorktree: string | null = null;
    let isLocked = false;
    let lockReason: string | null = null;

    for (const line of listResult.stdout.split("\n")) {
      if (line.startsWith("worktree ")) {
        // Check previous worktree
        if (currentWorktree === worktreePathResolved && isLocked) {
          return [true, lockReason];
        }

        // Start tracking new worktree
        let wt = line.slice(9);
        try {
          wt = realpathSync(wt);
        } catch {
          try {
            wt = resolve(wt);
          } catch {
            // Keep as is
          }
        }
        currentWorktree = wt;
        isLocked = false;
        lockReason = null;
      } else if (line === "locked") {
        isLocked = true;
        lockReason = null;
      } else if (line.startsWith("locked ")) {
        isLocked = true;
        lockReason = line.slice(7);
      }
    }

    // Check the last worktree
    if (currentWorktree === worktreePathResolved && isLocked) {
      return [true, lockReason];
    }

    return [false, null];
  } catch {
    return [false, null];
  }
}

async function main(): Promise<void> {
  let result: {
    decision?: "block";
    reason?: string;
    systemMessage?: string;
  };
  let filePath = "";
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    filePath = (data.tool_input?.file_path as string) || "";

    if (!filePath) {
      result = {
        systemMessage: "✅ worktree-warning: ファイルパスなし（スキップ）",
      };
    } else {
      const projectRoot = await getProjectRoot(filePath);
      if (!projectRoot) {
        result = {
          systemMessage: "✅ worktree-warning: プロジェクト外（スキップ）",
        };
      } else {
        // Normalize paths for Windows compatibility
        const filePathNorm = normalizePath(filePath);
        const projectRootNorm = normalizePath(projectRoot).replace(/\/+$/, "");
        const inProject =
          filePathNorm === projectRootNorm || filePathNorm.startsWith(`${projectRootNorm}/`);
        const inWorktree = filePathNorm.includes(".worktrees/");
        const currentBranch = await getCurrentBranch(filePath);

        // Block editing on protected branches
        if (PROTECTED_BRANCHES.has(currentBranch) && inProject) {
          if (isPathInAllowlist(filePath, projectRoot)) {
            result = {
              systemMessage: `✅ worktree-warning: ${currentBranch}ブランチですが、許可リスト内のファイルのため編集可能`,
            };
          } else {
            const correctPath = detectMisplacedPlanPath(filePath, projectRoot);
            let reason: string;
            if (correctPath) {
              reason = `🚫 planファイルのパスが正しくありません。\n\n書き込み先: ${filePath}\n正しいパス: ${correctPath}\n\n原因: cwdがサブディレクトリにあるため、相対パスが誤って解決されています。\n対処: 正しいパスでファイルを作成してください。`;
            } else {
              reason = `🚫 ${currentBranch}ブランチでの編集はブロックされました。\n\n【対処法】以下のコマンドを**1つずつ順番に**実行してください:\n\n**Step 1**: worktreeを作成\n\`\`\`\ngit worktree add --lock .worktrees/<issue-番号> -b <branch-name>\n\`\`\`\n\n**Step 2**: worktreeに移動\n\`\`\`\ncd .worktrees/<issue-番号>\n\`\`\`\n\n**Step 3**: 再度編集を実行\n\n⚠️ 注意:\n- \`<issue-番号>\` は対象のIssue番号に置き換えてください（例: issue-123）\n- \`<branch-name>\` は適切なブランチ名に置き換えてください`;
            }
            result = makeBlockResult(HOOK_NAME, reason);
          }
        } else if (inProject && !inWorktree) {
          result = {
            systemMessage:
              "⚠️ WARNING: オリジナルディレクトリで編集中。 " +
              "AGENTS.mdのworktreeルールを確認してください。",
          };
        } else if (inWorktree) {
          // In a worktree - check if it's locked
          const worktreeRoot = extractWorktreeRoot(filePath) || filePath;
          const [isLocked, lockReason] = await getWorktreeLockInfo(worktreeRoot);

          if (isLocked) {
            const reasonMsg = lockReason ? `\nロック理由: ${lockReason}` : "";
            result = {
              systemMessage: `⚠️ WARNING: このworktreeはロック中です。${reasonMsg}\n別セッションが作業中の可能性があります。\n競合に注意して作業を続行してください。`,
            };
          } else {
            result = {
              systemMessage: "✅ worktree-warning: worktree内で編集中",
            };
          }
        } else {
          result = {
            systemMessage: "✅ worktree-warning: OK",
          };
        }
      }
    }
  } catch (error) {
    console.error(`[worktree-warning] Hook error: ${formatError(error)}`);
    result = { reason: `Hook error: ${formatError(error)}` };
  }

  await logHookExecution(
    HOOK_NAME,
    result.decision ?? "approve",
    result.reason,
    filePath ? { file_path: filePath } : undefined,
    { sessionId },
  );
  outputResult(result);
}

// Only run when executed directly (not when imported for tests)
if (import.meta.main) {
  main();
}
