#!/usr/bin/env bun
/**
 * 振り返り中のIssue作成を検出して進捗を追跡。
 *
 * Why:
 *   振り返りで発見した改善点はIssue化が必要。Issue作成を追跡し、
 *   reflection-completion-checkが振り返り完了を判定できるようにする。
 *
 * What:
 *   - gh issue create コマンドの成功を検出
 *   - 作成されたIssue番号を抽出
 *   - セッション状態ファイルにIssue番号を記録
 *
 * State:
 *   - writes: /tmp/claude-hooks/reflection-required-{session_id}.json
 *
 * Remarks:
 *   - 非ブロック型（PostToolUse）
 *   - post-merge-reflection-enforcerがフラグ設定、本フックは進捗追跡
 *   - reflection-completion-checkがセッション終了時に検証
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2203: get_exit_code()で終了コード取得を統一
 *   - silenvx/dekita#2545: HookContextパターン移行
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { createHookContext, getToolResult, parseHookInput } from "../lib/session";

const HOOK_NAME = "reflection-progress-tracker";

// Session state directory
const SESSION_DIR = join(tmpdir(), "claude-hooks");

interface ReflectionState {
  reflection_required: boolean;
  merged_prs: string[];
  reflection_done: boolean;
  issues_created: string[];
}

/**
 * Get exit code from tool result with consistent default.
 */
export function getExitCode(toolResult: unknown, defaultVal = 0): number {
  if (!toolResult || typeof toolResult !== "object") {
    return defaultVal;
  }
  const result = toolResult as Record<string, unknown>;
  const exitCode = result.exit_code;
  if (typeof exitCode === "number") {
    return exitCode;
  }
  return defaultVal;
}

/**
 * Get the reflection state file path for the current session.
 */
function getReflectionStateFile(sessionId: string): string {
  const safeSessionId = basename(sessionId);
  return join(SESSION_DIR, `reflection-required-${safeSessionId}.json`);
}

/**
 * Load reflection state from session file.
 */
function loadReflectionState(sessionId: string): ReflectionState {
  try {
    const stateFile = getReflectionStateFile(sessionId);
    if (existsSync(stateFile)) {
      const content = readFileSync(stateFile, "utf-8");
      return JSON.parse(content);
    }
  } catch {
    // Best effort - corrupted state is ignored
  }
  return {
    reflection_required: false,
    merged_prs: [],
    reflection_done: false,
    issues_created: [],
  };
}

/**
 * Save reflection state to session file.
 */
function saveReflectionState(sessionId: string, state: ReflectionState): void {
  try {
    mkdirSync(SESSION_DIR, { recursive: true });
    const stateFile = getReflectionStateFile(sessionId);
    writeFileSync(stateFile, JSON.stringify(state, null, 2));
  } catch {
    // Best effort - state save may fail
  }
}

/**
 * Check if the command is a GitHub Issue creation command.
 */
export function isIssueCreateCommand(command: string): boolean {
  return /gh\s+issue\s+create/.test(command);
}

/**
 * Extract issue number from gh issue create output.
 */
export function extractIssueNumber(output: string): string | null {
  // gh issue create outputs URL like: https://github.com/owner/repo/issues/123
  const match = /\/issues\/(\d+)/.exec(output);
  return match ? match[1] : null;
}

interface HookResult {
  continue: boolean;
}

async function main(): Promise<void> {
  const result: HookResult = { continue: true };
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    const ctx = createHookContext(inputData);
    sessionId = ctx.sessionId;
    const toolName = inputData.tool_name || "";

    if (toolName !== "Bash") {
      console.log(JSON.stringify(result));
      return;
    }

    const sessionIdForState = sessionId || process.env.CLAUDE_SESSION_ID || "unknown";
    const toolInput = inputData.tool_input || {};
    const toolResult = getToolResult(inputData);
    const command = (toolInput as { command?: string }).command || "";

    // Check if this is an issue creation
    if (isIssueCreateCommand(command)) {
      const resultObj =
        toolResult && typeof toolResult === "object" && !Array.isArray(toolResult)
          ? (toolResult as Record<string, unknown>)
          : null;
      const stdout = String(resultObj?.stdout ?? "");
      const exitCode = getExitCode(toolResult);

      if (exitCode === 0) {
        const issueNumber = extractIssueNumber(stdout);
        if (issueNumber) {
          const state = loadReflectionState(sessionIdForState);

          // Track the created issue
          if (!state.issues_created.includes(issueNumber)) {
            state.issues_created.push(issueNumber);
            saveReflectionState(sessionIdForState, state);

            logHookExecution(
              HOOK_NAME,
              "approve",
              `Issue #${issueNumber} created, tracking for reflection`,
              undefined,
              { sessionId },
            );
          }
        }
      }
    }
  } catch (e) {
    logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(e)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
