#!/usr/bin/env bun
/**
 * Git操作メトリクスを追跡してログに記録する。
 *
 * Why:
 *   Update branch回数やConflict発生頻度を記録することで、
 *   開発フローのボトルネック分析や改善ポイントの特定に活用する。
 *
 * What:
 *   - git pull/merge/rebase, gh pr merge/update-branchコマンドを検出
 *   - Conflict発生、Update branch、Rebase解決を記録
 *   - 終了コード、ブランチ名、コンフリクトファイル等をログ出力
 *
 * State:
 *   - writes: .claude/logs/execution/git-operations.log
 *
 * Remarks:
 *   - 情報収集のみでブロックしない
 *   - SRP: Git操作のメトリクス追跡のみを担当
 *
 * Changelog:
 *   - silenvx/dekita#1689: コンフリクトファイルリスト記録、Rebase解決コマンド検出追加
 *   - silenvx/dekita#1706: deleted by us/them パターン対応
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { appendFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { EXECUTION_LOG_DIR } from "../lib/common";
import { getCurrentBranch } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { getToolResult, parseHookInput } from "../lib/session";

const HOOK_NAME = "git-operations-tracker";

// Git operation command patterns
const GIT_OPERATION_PATTERNS = [
  /\bgit\s+(pull|merge|rebase)\b/,
  /\bgh\s+pr\s+(merge|update-branch)\b/,
];

// Conflict detection patterns
// Note: These patterns assume English Git output. For localized environments,
// consider setting LC_ALL=C before Git commands if detection fails.
const CONFLICT_PATTERNS = [
  /CONFLICT/i,
  /Automatic merge failed/i,
  /merge conflict/i,
  /fix conflicts and then commit/i,
  /needs merge/i,
  /Unmerged files/i,
  /both modified:/i,
  /both added:/i,
];

// Update branch detection patterns
const UPDATE_BRANCH_PATTERNS = [
  /Updating\s+[a-f0-9]+\.\.[a-f0-9]+/i,
  /Fast-forward/i,
  /Already up to date/i,
  /Your branch is behind/i,
  /branch.*updated/i,
];

/**
 * Get execution log directory path.
 * EXECUTION_LOG_DIR is already an absolute, worktree-aware path from lib/common.
 */
function getExecutionLogDir(): string {
  return EXECUTION_LOG_DIR;
}

/**
 * Check if command is a git operation.
 */
function isGitOperationCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  return GIT_OPERATION_PATTERNS.some((pattern) => pattern.test(command));
}

/**
 * Detect conflict in output.
 */
function detectConflict(output: string): boolean {
  return CONFLICT_PATTERNS.some((pattern) => pattern.test(output));
}

/**
 * Extract conflict files from output.
 */
function extractConflictFiles(output: string): string[] {
  const files = new Set<string>();

  // Pattern: CONFLICT (content): Merge conflict in <filename>
  for (const match of output.matchAll(/CONFLICT\s*\([^)]+\):\s*.*?in\s+(?:"([^"]+)"|(\S+))/gi)) {
    files.add(match[1] || match[2]);
  }

  // Pattern: both modified/added/deleted: <filename>
  for (const match of output.matchAll(/both (?:modified|added|deleted):\s+(?:"([^"]+)"|(\S+))/gi)) {
    files.add(match[1] || match[2]);
  }

  // Pattern: deleted by us/them: <filename>
  for (const match of output.matchAll(/deleted by (?:us|them):\s+(?:"([^"]+)"|(\S+))/gi)) {
    files.add(match[1] || match[2]);
  }

  return Array.from(files).sort();
}

/**
 * Detect rebase resolution command.
 */
function detectRebaseResolution(command: string): "skip" | "continue" | "abort" | null {
  if (/\bgit\s+rebase\s+--skip\b/.test(command)) {
    return "skip";
  }
  if (/\bgit\s+rebase\s+--continue\b/.test(command)) {
    return "continue";
  }
  if (/\bgit\s+rebase\s+--abort\b/.test(command)) {
    return "abort";
  }
  return null;
}

/**
 * Detect update branch operation.
 */
function detectUpdateBranch(command: string, output: string): boolean {
  if (command.includes("update-branch")) {
    return true;
  }
  return UPDATE_BRANCH_PATTERNS.some((pattern) => pattern.test(output));
}

/**
 * Detect rebase operation.
 */
function detectRebase(command: string, output: string): boolean {
  if (/rebase/i.test(command)) {
    return true;
  }
  return /rebas(e|ing|ed)/i.test(output);
}

/**
 * Log git operation to file.
 */
async function logGitOperation(
  operationType: string,
  command: string,
  success: boolean,
  details?: Record<string, unknown>,
): Promise<void> {
  const logDir = getExecutionLogDir();
  mkdirSync(logDir, { recursive: true });

  const branch = await getCurrentBranch();

  const logEntry: Record<string, unknown> = {
    timestamp: new Date().toISOString(),
    type: "git_operation",
    operation: operationType,
    command: command.slice(0, 200),
    success,
    branch,
  };

  if (details) {
    logEntry.details = details;
  }

  try {
    const logFile = join(logDir, "git-operations.log");
    appendFileSync(logFile, `${JSON.stringify(logEntry)}\n`);
  } catch {
    // Ignore log write failures
  }
}

async function main(): Promise<void> {
  try {
    const hookInput = await parseHookInput();
    const toolName = hookInput.tool_name ?? "";
    const toolInput = (hookInput.tool_input ?? {}) as Record<string, unknown>;
    const rawResult = getToolResult(hookInput);
    const toolResult =
      typeof rawResult === "object" && rawResult
        ? (rawResult as Record<string, unknown>)
        : typeof rawResult === "string"
          ? { stdout: rawResult }
          : {};

    // Only process Bash tool calls
    if (toolName !== "Bash") {
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    const command = (toolInput.command as string) ?? "";

    // Check if this is a git operation command
    if (!isGitOperationCommand(command)) {
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Get output
    const stdout = String(toolResult.stdout ?? "");
    const stderr = String(toolResult.stderr ?? "");
    const output = `${stdout}\n${stderr}`;
    const exitCode = typeof toolResult.exit_code === "number" ? toolResult.exit_code : 0;
    const success = exitCode === 0;

    const operationsDetected: string[] = [];

    // Conflict detection
    if (detectConflict(output)) {
      const conflictFiles = extractConflictFiles(output);
      await logGitOperation("conflict", command, false, {
        exit_code: exitCode,
        files: conflictFiles,
      });
      operationsDetected.push("conflict");
    }

    // Rebase resolution command detection
    const rebaseResolution = detectRebaseResolution(command);
    if (rebaseResolution) {
      await logGitOperation("rebase_resolution", command, success, {
        exit_code: exitCode,
        method: rebaseResolution,
      });
      operationsDetected.push(`rebase_${rebaseResolution}`);
    }

    // Update branch detection
    if (detectUpdateBranch(command, output)) {
      await logGitOperation("update_branch", command, success, {
        exit_code: exitCode,
      });
      operationsDetected.push("update_branch");
    }

    // Rebase detection (excluding resolution commands)
    if (detectRebase(command, output) && !rebaseResolution) {
      await logGitOperation("rebase", command, success, {
        exit_code: exitCode,
      });
      operationsDetected.push("rebase");
    }

    // If no specific operations detected, log as merge for git pull/merge, gh pr merge
    if (
      operationsDetected.length === 0 &&
      /\bgit\s+(pull|merge)\b|\bgh\s+pr\s+merge\b/.test(command)
    ) {
      await logGitOperation("merge", command, success, {
        exit_code: exitCode,
      });
      operationsDetected.push("merge");
    }

    await logHookExecution(
      HOOK_NAME,
      "approve",
      operationsDetected.length > 0 ? `Detected: ${operationsDetected.join(", ")}` : undefined,
      operationsDetected.length > 0 ? { operations: operationsDetected } : undefined,
      { sessionId: hookInput.session_id },
    );
  } catch {
    // フック実行の失敗でClaude Codeをブロックしない
  }

  console.log(JSON.stringify({ continue: true }));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error:`, e);
    process.exit(0);
  });
}
