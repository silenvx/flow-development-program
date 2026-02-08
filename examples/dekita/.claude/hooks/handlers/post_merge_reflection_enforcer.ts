#!/usr/bin/env bun
/**
 * PRマージ成功後に振り返りを即時実行させる。
 *
 * Why:
 *   PRマージ後の振り返りを後回しにすると、コンテキストが失われ、
 *   教訓が得られない。即時実行を促進することで、振り返りの質を高める。
 *
 * What:
 *   - gh pr mergeの成功を検出
 *   - decision: "block" + continue: trueで即時アクションを誘発
 *   - [IMMEDIATE: /reflecting-sessions]をsystemMessageで出力（トランスクリプトに記録）
 *   - immediate-pending-check.pyが次のユーザー入力時に実行を強制
 *   - reflection-completion-check.pyがセッション終了時に最終チェック
 *
 * State:
 *   - writes: /tmp/claude-hooks/immediate-pending-{session_id}.json
 *
 * Remarks:
 *   - reflection-reminderはリマインド表示、本フックは即時実行促進
 *   - guard_rules.tsがworktree内マージも同様に処理
 *
 * Changelog:
 *   - silenvx/dekita#2089: block+continueパターン採用
 *   - silenvx/dekita#2159: ステートレス化（現在はpending状態のみファイル管理）
 *   - silenvx/dekita#2416: worktree削除後の対応
 *   - silenvx/dekita#2690: pending状態をファイルに保存（早期検出用）
 *   - silenvx/dekita#3161: TypeScript移行
 */

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { formatError } from "../lib/format_error";
import { extractPrNumber } from "../lib/github";
import { getExitCode } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { getRepoRoot, isMergeSuccess } from "../lib/repo";
import {
  getBashCommand,
  getToolResultAsObject,
  isValidSessionId,
  parseHookInput,
} from "../lib/session";
import { stripQuotedStrings } from "../lib/strings";
import { createHookContext } from "../lib/types";

const HOOK_NAME = "post-merge-reflection-enforcer";

// Session state directory for immediate pending actions
const SESSION_DIR = join(tmpdir(), "claude-hooks");

/**
 * Get the file path for storing immediate pending action state.
 *
 * @param sessionId - The Claude session ID to scope the file.
 * @returns Path to session-specific immediate pending state file, or null if
 *          session_id is invalid (security: prevents path traversal).
 */
function getImmediatePendingFile(sessionId: string): string | null {
  // Security: Validate session_id to prevent path traversal attacks
  if (!isValidSessionId(sessionId)) {
    return null;
  }
  return join(SESSION_DIR, `immediate-pending-${sessionId}.json`);
}

/**
 * Save immediate pending action state.
 *
 * Issue #2690: Record pending action for early detection by
 * immediate-pending-check.py (UserPromptSubmit hook).
 *
 * @param sessionId - The Claude session ID.
 * @param action - The action to execute (e.g., "/reflecting-sessions").
 * @param context - Context for the action (e.g., "PR #123 merge").
 */
function saveImmediatePendingState(sessionId: string, action: string, context: string): void {
  try {
    const stateFile = getImmediatePendingFile(sessionId);
    if (stateFile === null) {
      return; // Invalid session_id
    }
    mkdirSync(SESSION_DIR, { recursive: true });
    const state = {
      action,
      context,
      timestamp: new Date().toISOString(),
    };
    writeFileSync(stateFile, JSON.stringify(state, null, 2));
  } catch {
    // State persistence is best-effort; failures here should not
    // block the hook or affect Claude Code operation
  }
}

/**
 * Check if CLAUDE_PROJECT_DIR is valid and return repo root path.
 *
 * @returns Tuple of [is_valid, repo_root_path].
 *   - is_valid: True if CLAUDE_PROJECT_DIR exists
 *   - repo_root_path: Repository root path if determinable, else null
 *     - When project dir exists: returns getRepoRoot(project_path)
 *     - When project dir doesn't exist (worktree deleted): returns
 *       original repo path extracted from .worktrees pattern
 *
 * Issue #2416: Worktree may be deleted after merge, causing Skill failures.
 */
function checkProjectDirValid(): [boolean, string | null] {
  const projectDir = process.env.CLAUDE_PROJECT_DIR ?? "";
  if (!projectDir) {
    return [true, null]; // No project dir set, assume valid
  }

  if (existsSync(projectDir)) {
    // Project dir exists, get original repo for reference
    return [true, getRepoRoot(projectDir)];
  }

  // Project dir doesn't exist (worktree deleted)
  // Try to find original repo from parent path pattern
  // Pattern: /path/to/repo/.worktrees/issue-xxx -> /path/to/repo
  const parts = projectDir.split("/").filter((p) => p !== "");
  const worktreesIdx = parts.indexOf(".worktrees");

  // worktreesIdx が 0 の場合、parts[:0] は空配列となり
  // ルートディレクトリを指してしまうため、0 より大きい場合のみ許可する。
  if (worktreesIdx > 0) {
    const originalPath = `/${parts.slice(0, worktreesIdx).join("/")}`;
    if (existsSync(originalPath)) {
      return [false, originalPath];
    }
  }

  return [false, null];
}

/**
 * Check if the command is a PR merge command.
 *
 * Returns false for:
 * - Commands inside quoted strings (e.g., echo 'gh pr merge 123')
 * - gh pr merge appearing INSIDE heredoc data (after << marker)
 * - Empty commands
 *
 * Issue #2553: Avoid false positives from test data in heredoc/cat commands.
 *
 * Note: Strip quoted strings FIRST, then check positions.
 * If gh pr merge appears BEFORE a heredoc, it's a real command.
 * If gh pr merge appears AFTER a heredoc start, it's likely test data.
 */
function isPrMergeCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }

  // Strip quoted strings first to avoid false negatives
  // e.g., echo 'test <<' && gh pr merge 123 should be detected
  const strippedCommand = stripQuotedStrings(command);

  // Find gh pr merge position
  const mergeMatch = strippedCommand.match(/gh\s+pr\s+merge/);
  if (!mergeMatch || mergeMatch.index === undefined) {
    return false;
  }

  // Find heredoc pattern position (cat/tee/bash/sh/zsh/ksh followed by << or <<-)
  // Use (?<!<)<<-?(?!<) to match << or <<- but exclude <<< (here-string)
  // Negative lookbehind (?<!<) ensures << is not preceded by <
  // Negative lookahead (?!<) ensures << is not followed by <
  const heredocMatch = strippedCommand.match(/\b(cat|tee|bash|sh|zsh|ksh)\b[^\n]*(?<!<)<<-?(?!<)/);

  if (heredocMatch && heredocMatch.index !== undefined) {
    // If merge command appears BEFORE heredoc, it's a real command
    // e.g., "gh pr merge 123 && cat <<EOF" -> merge at 0, heredoc at 18
    // If merge command appears AFTER heredoc start, it's in heredoc data
    // e.g., "cat <<EOF\ngh pr merge 123\nEOF" -> heredoc at 0, merge at 10
    if (mergeMatch.index > heredocMatch.index) {
      return false;
    }
  }

  return true;
}

/**
 * Check if the merge was successful.
 *
 * Wrapper around repo.isMergeSuccess for backward compatibility.
 * Issue #2203: Use getExitCode() for consistent default value.
 */
function checkMergeSuccess(toolResult: Record<string, unknown>, command = ""): boolean {
  const exitCode = getExitCode(toolResult);
  const stdout = typeof toolResult.stdout === "string" ? toolResult.stdout : "";
  const stderr = typeof toolResult.stderr === "string" ? toolResult.stderr : "";
  return isMergeSuccess(exitCode, stdout, command, stderr);
}

async function main(): Promise<void> {
  const result: Record<string, unknown> = { continue: true };

  try {
    const inputData = await parseHookInput();
    const toolName = inputData.tool_name ?? "";

    if (toolName !== "Bash") {
      console.log(JSON.stringify(result));
      return;
    }

    const toolResult = getToolResultAsObject(inputData);
    const command = getBashCommand(inputData);

    // Check if this is a successful PR merge
    if (isPrMergeCommand(command) && checkMergeSuccess(toolResult, command)) {
      const prNumber = extractPrNumber(command);

      // Issue #2416: Check if project directory is still valid
      const [isValid, originalRepo] = checkProjectDirValid();

      // Issue #2690: Get session_id and save pending state for early detection
      const ctx = createHookContext(inputData);
      const sessionId = ctx.sessionId;
      if (sessionId) {
        saveImmediatePendingState(
          sessionId,
          "/reflecting-sessions",
          `PR #${prNumber ?? "?"} merge`,
        );
      }

      await logHookExecution(
        HOOK_NAME,
        "block",
        `Triggering immediate reflection for PR #${prNumber}`,
        { project_dir_valid: isValid, original_repo: originalRepo },
        { sessionId },
      );

      // Issue #2089: Use decision: "block" + continue: true to force action
      // Issue #2364: Output to both reason AND systemMessage
      // - reason: for Claude Code to read (may not be in transcript)
      // - systemMessage: recorded in transcript for detection by
      //   reflection-completion-check.py
      let message: string;
      if (isValid) {
        message = `✅ PR #${prNumber ?? "?"} マージ完了\n\n**動作確認チェックリスト**:\n- [ ] 正常系: 期待動作の確認\n- [ ] 異常系: エラーハンドリングの確認\n- [ ] Dogfooding: 自分で使って問題ないか確認\n\n[IMMEDIATE: /reflecting-sessions]\n振り返り（五省）を行い、教訓をIssue化してください。`;
      } else {
        // Issue #2416: Worktree deleted, guide to original repo
        const originalPath = originalRepo ?? "オリジナルリポジトリ";
        message = `✅ PR #${prNumber ?? "?"} マージ完了\n\n⚠️ **worktreeが削除されています**\n\n振り返りを実行する前に、オリジナルリポジトリに移動してください:\n\`\`\`bash\ncd ${originalPath}\n\`\`\`\n\n**動作確認チェックリスト**:\n- [ ] 正常系: 期待動作の確認\n- [ ] 異常系: エラーハンドリングの確認\n- [ ] Dogfooding: 自分で使って問題ないか確認\n\n移動後、以下を実行:\n[IMMEDIATE: /reflecting-sessions]\n振り返り（五省）を行い、教訓をIssue化してください。`;
      }

      console.log(
        JSON.stringify({
          decision: "block",
          continue: true, // Don't stop, but force Claude to read message
          reason: message,
          systemMessage: message,
        }),
      );
      return;
    }
  } catch (e) {
    await logHookExecution(HOOK_NAME, "error", `Hook error: ${formatError(e)}`);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
  });
}
