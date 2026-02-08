#!/usr/bin/env bun
/**
 * ci_monitor（TypeScript版）を使用すべきコマンドをブロックする。
 *
 * Why:
 *   gh pr checks --watch や手動ポーリングは冗長なログでコンテキストを消費し、
 *   BEHIND検知や自動リベースなどの機能がない。ci_monitorを使用すべき。
 *
 * What:
 *   - gh pr checks --watchをブロック
 *   - gh run watchをブロック（冗長ログ）
 *   - 手動PR状態チェック（gh api /pulls/xxx）をブロック
 *   - 手動ポーリング（sleep && gh ...）をブロック
 *   - ci_monitor（TypeScript版）の使用を案内
 *
 * Remarks:
 *   - ブロック型フック（非推奨コマンドはブロック）
 *   - PreToolUse:Bashで発火
 *   - コメント/メッセージ内のパターンは除外（引用符内は検査しない）
 *
 * Limitations (Issue #3118):
 *   - `bash -c "sleep 5m && gh..."` のようなラップコマンドは検出しない
 *   - 設計判断: 誤検知防止を優先（echo/grep内のパターン誤検知を避ける）
 *   - bash -cで意図的にラップするケースは稀なため許容
 *
 * Changelog:
 *   - silenvx/dekita#1008: コメント内容の誤検知防止
 *   - silenvx/dekita#1508: Noneハンドリング
 *   - silenvx/dekita#2052: gh issue close/comment対応
 *   - silenvx/dekita#2062: git commit -m対応
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "ci-wait-check";

// Patterns for manual PR state check commands
const MANUAL_CHECK_PATTERNS = [
  // gh pr view with --json mergeStateStatus
  /gh\s+pr\s+view\s+(?:.*?\s+)?(\d+)\s+.*?--json\s+mergeStateStatus/,
  /gh\s+pr\s+view\s+--json\s+mergeStateStatus\s+(?:.*?\s+)?(\d+)/,
  // gh api /repos/.../pulls/... (direct PR access only)
  /gh\s+api\s+\/repos\/[^/]+\/[^/]+\/pulls\/(\d+)\/?(?!comments|reviews|requested_reviewers)(?:\s|$)/,
  // gh pr view with --json reviews
  /gh\s+pr\s+view\s+(?:.*?\s+)?(\d+)\s+.*?--json\s+reviews/,
  /gh\s+pr\s+view\s+--json\s+reviews\s+(?:.*?\s+)?(\d+)/,
  // gh pr view with --json requested_reviewers
  /gh\s+pr\s+view\s+(?:.*?\s+)?(\d+)\s+.*?--json\s+requested_reviewers/,
  /gh\s+pr\s+view\s+--json\s+requested_reviewers\s+(?:.*?\s+)?(\d+)/,
];

/**
 * Extract PR number from gh pr checks command.
 */
export function getPrNumberFromChecks(command: string): string | null {
  // Pattern 1: PR number comes immediately after 'checks'
  let match = command.match(/gh\s+pr\s+checks\s+(\d+)/);
  if (match) {
    return match[1];
  }
  // Pattern 2: flags come before the PR number
  match = command.match(/gh\s+pr\s+checks\s+(?:--\w+\s+)+(\d+)/);
  if (match) {
    return match[1];
  }
  return null;
}

/**
 * Detect manual PR state check commands.
 */
export function detectManualPrCheck(command: string): [boolean, string | null] {
  for (const pattern of MANUAL_CHECK_PATTERNS) {
    const match = command.match(pattern);
    if (match) {
      return [true, match[1]];
    }
  }
  return [false, null];
}

/**
 * Extract PR number from a command string.
 */
function extractPrNumberFromCommand(command: string): string | null {
  // Pattern: gh pr <subcommand> <PR number>
  let match = command.match(/gh\s+pr\s+\w+\s+(\d+)/);
  if (match) {
    return match[1];
  }
  // Pattern: gh api .../pulls/<PR number>
  match = command.match(/gh\s+api\s+.*?\/pulls\/(\d+)/);
  if (match) {
    return match[1];
  }
  return null;
}

/**
 * Detect manual polling patterns.
 */
export function detectManualPolling(command: string): [boolean, string | null] {
  // Strip quoted content to avoid false positives like echo "sleep 0.5 && gh"
  const stripped = stripQuotedContent(command);
  // Pattern: sleep followed by gh command (chained with && or ;)
  // Supports decimal (0.5), time units (5s, 5m, 5h, 5d)
  // Uses strict number pattern to avoid matching invalid formats like ".5", "1.", "..5"
  if (/sleep\s+[0-9]+(?:\.[0-9]+)?[smhd]?\s*(&&|;)\s*gh\s+/.test(stripped)) {
    return [true, extractPrNumberFromCommand(command)];
  }
  // Pattern: while loop with sleep and gh
  if (/while\s+.*\bdo\b.*sleep\s+.*gh\s+/s.test(stripped)) {
    return [true, extractPrNumberFromCommand(command)];
  }
  return [false, null];
}

/**
 * Remove content inside quotes to avoid false positives.
 */
export function stripQuotedContent(command: string): string {
  const result: string[] = [];
  let i = 0;

  while (i < command.length) {
    const char = command[i];
    if (char === "\\" && i + 1 < command.length) {
      // Escaped character outside quotes - preserve both chars
      result.push(command[i]);
      result.push(command[i + 1]);
      i += 2;
    } else if (char === '"' || char === "'") {
      const quoteChar = char;
      result.push(quoteChar);
      i++;
      // Consume until matching unescaped quote or end of string
      while (i < command.length) {
        if (command[i] === "\\" && i + 1 < command.length) {
          // Skip escaped character
          i += 2;
        } else if (command[i] === quoteChar) {
          result.push(quoteChar);
          i++;
          break;
        } else {
          i++;
        }
      }
    } else {
      result.push(char);
      i++;
    }
  }

  return result.join("");
}

/**
 * Check if command has comment/body content that should not be inspected.
 */
export function isCommandWithCommentContent(command: string): boolean {
  // Commands that may contain arbitrary text in --body/--title/--comment/-m
  const commentCommandPattern =
    /(gh\s+(issue\s+(create|close|comment)\b|pr\s+(create|comment|review|close)\b)|git\s+commit\s+.*?(--message\b|-a?m\b))/;

  if (!commentCommandPattern.test(command)) {
    return false;
  }

  // Strip quoted content to avoid false positives
  const stripped = stripQuotedContent(command);

  // If command contains chained gh commands, don't early approve
  if (/(gh\s+.*?(&&|\|\||[;|])|(&&|\|\||[;|])\s*gh\s+)/.test(stripped)) {
    return false;
  }

  return true;
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const inputJson = await parseHookInput();
    sessionId = inputJson.session_id;
    const toolInput = inputJson.tool_input || {};
    const command = (toolInput.command as string) || "";

    // Early approve: commands with comment/body content (standalone only)
    if (isCommandWithCommentContent(command)) {
      const result = makeApproveResult(HOOK_NAME);
      logHookExecution(HOOK_NAME, "approve", "command with comment content", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Block: gh pr checks [PR] --watch
    if (/gh\s+pr\s+checks\s+.*--watch/.test(command)) {
      const prNumber = getPrNumberFromChecks(command);
      const prDisplay = prNumber || "{PR番号}";
      const reason = `gh pr checks --watch は使用禁止です。\nci_monitor（TypeScript版）を使用してください:\n\nbun run "$CLAUDE_PROJECT_DIR"/.claude/scripts/ci_monitor_ts/main.ts ${prDisplay} --session-id <SESSION_ID>\n\n※ <SESSION_ID>はUserPromptSubmit hookで提供されるセッションID\n\nci_monitorは以下を自動処理します:\n  - BEHIND検知→自動リベース\n  - レビュー完了検知→コメント取得\n  - CI失敗→即座に通知`;
      const result = makeBlockResult(HOOK_NAME, reason);
      logHookExecution(
        HOOK_NAME,
        "block",
        reason,
        { command: command.slice(0, 100) },
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Block: gh run watch (verbose output)
    const strippedCommand = stripQuotedContent(command);
    if (/gh\s+run\s+watch\b/.test(strippedCommand)) {
      const reason =
        "gh run watch は使用禁止です（ログが冗長）。\n\n" +
        "【PR関連のCI監視の場合】\n" +
        "ci_monitor（TypeScript版）を使用してください:\n" +
        '  bun run "$CLAUDE_PROJECT_DIR"/.claude/scripts/ci_monitor_ts/main.ts {PR番号} ' +
        "--session-id <SESSION_ID>\n\n" +
        "※ <SESSION_ID>はUserPromptSubmit hookで提供されるセッションID\n\n" +
        "【PR不要のワークフロー監視の場合】\n" +
        "(例: workflow_dispatch, 手動トリガー)\n" +
        "gh run view を繰り返し使用してください:\n" +
        "  gh run view {run_id} --json status,conclusion\n\n" +
        "gh run watch を使うと大量のログが出力され、\n" +
        "コンテキストを消費します。";
      const result = makeBlockResult(HOOK_NAME, reason);
      logHookExecution(
        HOOK_NAME,
        "block",
        reason,
        { command: command.slice(0, 100) },
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Block: Manual PR state check commands
    const [isManualCheck, manualPrNumber] = detectManualPrCheck(command);
    if (isManualCheck) {
      const prDisplay = manualPrNumber || "{PR番号}";
      const reason = `手動のPR状態チェックは使用禁止です。\nci_monitor（TypeScript版）を使用してください:\n\nbun run "$CLAUDE_PROJECT_DIR"/.claude/scripts/ci_monitor_ts/main.ts ${prDisplay} --session-id <SESSION_ID>\n\n※ <SESSION_ID>はUserPromptSubmit hookで提供されるセッションID\n\nci_monitorは以下を自動処理します:\n  - マージ状態チェック（BEHIND/DIRTY検知）\n  - レビュー完了検知→コメント自動取得\n  - CI完了待機→結果通知`;
      const result = makeBlockResult(HOOK_NAME, reason);
      logHookExecution(
        HOOK_NAME,
        "block",
        reason,
        { command: command.slice(0, 100) },
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Block: Manual polling patterns
    const [isManualPolling, pollingPrNumber] = detectManualPolling(command);
    if (isManualPolling) {
      const prDisplay = pollingPrNumber || "{PR番号}";
      const reason = `手動ポーリングパターン（sleep + gh）を検出しました。\n\n【PR関連のCI監視の場合】\nci_monitor（TypeScript版）を使用してください:\n  bun run "$CLAUDE_PROJECT_DIR"/.claude/scripts/ci_monitor_ts/main.ts ${prDisplay} --session-id <SESSION_ID>\n\n※ <SESSION_ID>はUserPromptSubmit hookで提供されるセッションID\n\n【PR不要のワークフロー監視の場合】\n(例: workflow_dispatch, 手動トリガー)\nsleepなしで gh run view を繰り返し使用してください:\n  gh run view {run_id} --json status,conclusion\n\n手動ポーリング（sleep + gh）はコンテキストを消費します。`;
      const result = makeBlockResult(HOOK_NAME, reason);
      logHookExecution(
        HOOK_NAME,
        "block",
        reason,
        { command: command.slice(0, 100) },
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // All other commands: approve
    const result = makeApproveResult(HOOK_NAME);
    logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
    console.log(JSON.stringify(result));
  } catch (error) {
    // On error, approve to avoid blocking legitimate commands
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    const result = makeApproveResult(HOOK_NAME, `Hook error: ${formatError(error)}`);
    logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(error)}`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify(result));
  }
}

if (import.meta.main) {
  main();
}
