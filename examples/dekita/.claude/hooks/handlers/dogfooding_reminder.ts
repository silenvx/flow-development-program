#!/usr/bin/env bun
/**
 * スクリプト作成・変更時に実データでのテストを促す（Dogfooding）。
 *
 * Why:
 *   データ処理スクリプトをテストなしでコミットすると、実データで初めて
 *   バグが発覚する。自分で使って問題を体験してから完了とする習慣を促進。
 *
 * What:
 *   - .claude/scripts/*.pyへのWrite/Editを検出
 *   - データ処理パターン（subprocess, json.loads等）を含む場合に警告
 *   - Dogfoodingチェックリストを表示
 *
 * State:
 *   - writes: .claude/logs/dogfooding/reminded-{session}.txt
 *
 * Remarks:
 *   - リマインド型フック（ブロックしない、systemMessageで提案）
 *   - PreToolUse:Write/Editで発火
 *   - .claude/scripts/*.pyが対象（tests/は除外）
 *   - データ処理パターン（subprocess, json.loads等）を含む場合のみ
 *
 * Changelog:
 *   - silenvx/dekita#1937: 発端となった問題（テストなしでのスクリプト作成）
 *   - silenvx/dekita#1942: フック追加
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { basename, join } from "node:path";
import { logHookExecution } from "../lib/logging";
import { createContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "dogfooding-reminder";

// Directory for session-based tracking files
const PROJECT_DIR = process.env.CLAUDE_PROJECT_DIR || ".";
const TRACKING_DIR = join(PROJECT_DIR, ".claude", "logs", "dogfooding");

// Data processing patterns that trigger the reminder
const DATA_PROCESSING_PATTERNS = [
  // API/HTTP calls
  "requests.",
  "httpx.",
  "urllib",
  "fetch(",
  // Subprocess/command execution
  "subprocess.",
  "run_gh_command",
  "run_git_command",
  "Bun.spawn",
  "spawnSync",
  // JSON/data parsing
  "json.loads",
  "json.load",
  "JSON.parse",
  ".split(",
  ".parse(",
  // File reading
  "open(",
  "Path(",
  "read_text(",
  "read_bytes(",
  "readFileSync",
];

/**
 * Check if this is a new script creation.
 */
function isNewScript(filePath: string, toolName: string, oldString: string): boolean {
  if (toolName === "Write") {
    // Write tool always creates/overwrites a file
    // Check if file didn't exist before
    return !existsSync(filePath);
  }

  // For Edit tool, if old_string is empty or very short, it might be initial content
  return oldString.trim().length < 50;
}

/**
 * Check if the script contains data processing patterns.
 */
export function hasDataProcessingPatterns(content: string): boolean {
  return DATA_PROCESSING_PATTERNS.some((pattern) => content.includes(pattern));
}

/**
 * Get the session-specific tracking file path.
 */
function getSessionTrackingFile(sessionId: string | null | undefined): string {
  // Sanitize session_id to prevent path traversal attacks
  const safeSessionId = basename(sessionId ?? "unknown");
  return join(TRACKING_DIR, `reminded-${safeSessionId}.txt`);
}

/**
 * Check if we already showed a reminder for this file in this session.
 */
function wasAlreadyReminded(filePath: string, sessionId: string | null | undefined): boolean {
  const trackingFile = getSessionTrackingFile(sessionId);
  if (!existsSync(trackingFile)) {
    return false;
  }
  try {
    const content = readFileSync(trackingFile, "utf-8");
    const remindedFiles = content.trim().split("\n");
    return remindedFiles.includes(filePath);
  } catch {
    return false;
  }
}

/**
 * Mark a file as reminded for this session.
 */
function markAsReminded(filePath: string, sessionId: string | null | undefined): void {
  const trackingFile = getSessionTrackingFile(sessionId);
  try {
    mkdirSync(TRACKING_DIR, { recursive: true });
    // Append to the file (create if doesn't exist)
    appendFileSync(trackingFile, `${filePath}\n`);
  } catch {
    // Silently fail - reminder deduplication is best-effort
  }
}

/**
 * Build the Dogfooding reminder message.
 */
export function buildReminderMessage(filePath: string, isNew: boolean): string {
  const action = isNew ? "新規スクリプト作成" : "スクリプト変更";
  return `💡 [${action}] Dogfoodingチェックリスト

ファイル: ${filePath}

コミット前に以下を確認してください:
□ 実際のデータで動作確認しましたか？
□ エッジケース（空、改行含む、大量データ）をテストしましたか？
□ 対応するテストファイルを作成/更新しましたか？

ヒント: このスクリプトが解決する問題を、自分で再現・体験してから完了としてください。
参考: Issue #1942, AGENTS.md「Dogfooding原則」`;
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };

  try {
    const inputData = await parseHookInput();
    const ctx = createContext(inputData);
    const sessionId = ctx.sessionId;
    const toolName = inputData.tool_name || "";
    const toolInput = inputData.tool_input || {};

    // Only target Write and Edit tools
    if (toolName !== "Write" && toolName !== "Edit") {
      logHookExecution(HOOK_NAME, "skip", `not Write/Edit: ${toolName}`);
      console.log(JSON.stringify(result));
      return;
    }

    const filePath = (toolInput.file_path as string) || "";

    // Only target .claude/scripts/*.py files
    if (!filePath.includes(".claude/scripts/") || !filePath.endsWith(".py")) {
      logHookExecution(HOOK_NAME, "skip", "not a script file");
      console.log(JSON.stringify(result));
      return;
    }

    // Exclude files in tests directory
    if (filePath.includes("/tests/")) {
      logHookExecution(HOOK_NAME, "skip", "test file excluded");
      console.log(JSON.stringify(result));
      return;
    }

    // Check if already reminded for this file
    if (wasAlreadyReminded(filePath, sessionId)) {
      logHookExecution(HOOK_NAME, "skip", "already reminded");
      console.log(JSON.stringify(result));
      return;
    }

    // Get content to check for data processing patterns
    const content = (toolInput.content as string) || (toolInput.new_string as string) || "";
    const oldString = (toolInput.old_string as string) || "";

    // Only show reminder for scripts with data processing patterns
    if (!hasDataProcessingPatterns(content)) {
      logHookExecution(HOOK_NAME, "skip", "no data processing patterns");
      console.log(JSON.stringify(result));
      return;
    }

    // Determine if this is a new script
    const isNew = isNewScript(filePath, toolName, oldString);

    // Build and set reminder message
    result.systemMessage = buildReminderMessage(filePath, isNew);

    // Mark as reminded
    markAsReminded(filePath, sessionId);

    logHookExecution(HOOK_NAME, "remind", `${isNew ? "New" : "Modified"} script: ${filePath}`, {
      file: filePath,
      is_new: isNew,
    });
  } catch {
    // Never fail the hook - just skip reminder
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
