#!/usr/bin/env bun
/**
 * PRマージや一定アクション後に振り返りをリマインド。
 *
 * Why:
 *   タスク完了後やセッションが長時間続いた際に振り返りを促し、
 *   学習機会を逃さないようにする。
 *
 * What:
 *   - gh pr merge / git merge 成功を検出しリマインド
 *   - 10アクションごとに定期リマインド
 *   - セッション状態ファイルでアクション回数を追跡
 *
 * State:
 *   - writes: /tmp/claude-hooks/reflection-state-{session_id}.json
 *
 * Remarks:
 *   - 非ブロック型（リマインダー表示のみ）
 *   - PostToolUse:Bash フック
 *   - PRマージリマインドと定期リマインドは排他（マージ優先）
 *   - Python版: reflection_reminder.py
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#1842: get_tool_result()ヘルパー使用に統一
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { SESSION_DIR } from "../lib/constants";
import { getToolResult } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { createContext, getSessionId, parseHookInput } from "../lib/session";

const HOOK_NAME = "reflection-reminder";

// 振り返りリマインドの間隔（アクション回数）
const REMINDER_INTERVAL_ACTIONS = 10;

interface ReflectionState {
  action_count: number;
  last_reminder_action: number;
  pr_merged_count: number;
}

/**
 * Get the file path for storing reflection state.
 */
function getReflectionStateFile(sessionId: string): string {
  return `${SESSION_DIR}/reflection-state-${sessionId || "unknown"}.json`;
}

/**
 * Load reflection state from session file.
 */
function loadReflectionState(sessionId: string): ReflectionState {
  try {
    const stateFile = getReflectionStateFile(sessionId);
    if (existsSync(stateFile)) {
      const data = JSON.parse(readFileSync(stateFile, "utf-8"));
      return {
        action_count: data.action_count ?? 0,
        last_reminder_action: data.last_reminder_action ?? 0,
        pr_merged_count: data.pr_merged_count ?? 0,
      };
    }
  } catch {
    // Best effort - corrupted state is ignored
  }
  return { action_count: 0, last_reminder_action: 0, pr_merged_count: 0 };
}

/**
 * Save reflection state to session file.
 */
function saveReflectionState(sessionId: string, state: ReflectionState): void {
  try {
    mkdirSync(SESSION_DIR, { recursive: true });
    const stateFile = getReflectionStateFile(sessionId);
    writeFileSync(stateFile, JSON.stringify(state), "utf-8");
  } catch {
    // State persistence is best-effort
  }
}

/**
 * Check if command is a PR merge command.
 */
export function isPrMergeCommand(command: string): boolean {
  // gh pr merge pattern
  if (/gh\s+pr\s+merge/.test(command)) {
    return true;
  }
  // git merge with PR branch pattern
  if (/git\s+merge.*(?:feat|fix|docs|refactor|test)\//.test(command)) {
    return true;
  }
  return false;
}

/**
 * Check if PR merge was successful.
 */
export function checkPrMergeResult(toolResult: Record<string, unknown>): boolean {
  // Exit code must be 0
  const exitCode = toolResult.exit_code;
  if (exitCode !== 0) {
    return false;
  }

  const stdout = String(toolResult.stdout ?? "");
  // Check for merge success indicators
  const mergeIndicators = ["Merged", "merged", "Pull request", "Merge made by", "Fast-forward"];
  return mergeIndicators.some((indicator) => stdout.includes(indicator));
}

async function main(): Promise<void> {
  const result: Record<string, unknown> = { continue: true };

  try {
    const data = await parseHookInput();
    const ctx = createContext(data);
    const sessionId = getSessionId(ctx) ?? "unknown";

    const toolName = data.tool_name ?? "";
    const toolInput = (data.tool_input as Record<string, unknown>) ?? {};

    // Use standardized helper for tool result extraction
    const rawResult = getToolResult(data);
    const toolResult: Record<string, unknown> =
      typeof rawResult === "object" && rawResult !== null
        ? (rawResult as Record<string, unknown>)
        : {};

    // Skip non-Bash tools
    if (toolName !== "Bash") {
      console.log(JSON.stringify(result));
      return;
    }

    const command = (toolInput.command as string) ?? "";

    // Load state (file is scoped by session ID)
    const state = loadReflectionState(sessionId);

    let reminderMessage: string | null = null;

    // 1. PR merge detection
    if (isPrMergeCommand(command) && checkPrMergeResult(toolResult)) {
      state.pr_merged_count = (state.pr_merged_count ?? 0) + 1;
      reminderMessage =
        "🎉 PRがマージされました！\n" +
        "タスク完了後は振り返り（五省）を行うと効果的です:\n" +
        "- 要件を正確に理解できたか\n" +
        "- 実装品質は十分か\n" +
        "- 検証は適切に行ったか\n" +
        "- 効率的に作業できたか";
    }

    // 2. Periodic reminder (after certain number of actions)
    state.action_count = (state.action_count ?? 0) + 1;
    const currentActionCount = state.action_count;
    const lastReminderCount = state.last_reminder_action ?? 0;

    // Remind every REMINDER_INTERVAL_ACTIONS actions
    if (
      Math.floor(currentActionCount / REMINDER_INTERVAL_ACTIONS) >
      Math.floor(lastReminderCount / REMINDER_INTERVAL_ACTIONS)
    ) {
      state.last_reminder_action = currentActionCount;
      if (!reminderMessage) {
        // Only if no PR merge message
        reminderMessage = `📊 セッション進行中（${currentActionCount}回のアクション）\n定期的な振り返りを推奨します。\nログ: .claude/logs/execution/hook-execution-*.jsonl, .claude/logs/metrics/*.jsonl`;
      }
    }

    // Save state
    saveReflectionState(sessionId, state);

    // Show reminder message if any
    if (reminderMessage) {
      result.systemMessage = `[${HOOK_NAME}] ${reminderMessage}`;
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "Reflection reminder shown",
        { trigger: reminderMessage.includes("PR") ? "pr_merge" : "periodic" },
        { sessionId },
      );
    } else {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "No reminder triggered",
        { type: "no_reminder" },
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
