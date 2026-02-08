#!/usr/bin/env bun
/**
 * SKIP_CODEX_REVIEW/SKIP_GEMINI_REVIEW環境変数の使用を禁止する。
 *
 * Why:
 *   SKIP環境変数でレビューチェックをバイパスすると、コード品質チェックが
 *   欠落したままPRがマージされる可能性がある。これはAIレビューを
 *   「安全網」として期待する悪習慣を助長する。
 *
 * What:
 *   - git push / gh pr createコマンドを検出
 *   - SKIP_CODEX_REVIEW または SKIP_GEMINI_REVIEW が設定されていたらブロック
 *   - 環境変数とインラインの両方をチェック
 *
 * Remarks:
 *   - ブロック型フック（SKIP使用時はブロック）
 *   - PreToolUse:Bashで発火（git push / gh pr createコマンド）
 *   - 緊急時の回避策は提供しない（レビュー実行が正しい対応）
 *
 * Changelog:
 *   - silenvx/dekita#2942: 初期実装
 */

import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { extractInlineSkipEnv, isSkipEnvEnabled, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "skip-review-env-check";

/** Pattern to detect 'gh pr create' commands */
export const GH_PR_CREATE_PATTERN = /gh\s+pr\s+create\b/;

/** Pattern to detect 'git push' commands */
export const GIT_PUSH_PATTERN = /git\s+push\b/;

/** Environment variables to block */
export const SKIP_ENV_VARS = ["SKIP_CODEX_REVIEW", "SKIP_GEMINI_REVIEW"] as const;

/**
 * Check if command is a target command (gh pr create or git push).
 */
export function isTargetCommand(command: string): boolean {
  if (!command.trim()) return false;
  const stripped = stripQuotedStrings(command);
  const isTarget =
    (GH_PR_CREATE_PATTERN.test(stripped) || GIT_PUSH_PATTERN.test(stripped)) &&
    !stripped.includes("--help");
  return isTarget;
}

/**
 * Check if skip environment variable is set (either exported or inline).
 * Returns the name of the detected variable, or null if none found.
 *
 * Uses lib/strings.ts functions for robust parsing:
 * - isSkipEnvEnabled(): Check exported env vars
 * - extractInlineSkipEnv(): Handle quoted and unquoted inline values
 */
export function detectSkipEnv(command: string): string | null {
  for (const envVar of SKIP_ENV_VARS) {
    // Check exported env
    if (isSkipEnvEnabled(process.env[envVar])) {
      return envVar;
    }

    // Check inline env using robust parser from lib
    const inlineValue = extractInlineSkipEnv(command, envVar);
    if (isSkipEnvEnabled(inlineValue)) {
      return envVar;
    }
  }

  return null;
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  const data = await parseHookInput();
  sessionId = data.session_id;
  const toolInput = data.tool_input || {};
  const command = (toolInput.command as string) || "";

  // Only check git push and gh pr create commands
  if (!isTargetCommand(command)) {
    console.log(JSON.stringify(makeApproveResult()));
    return;
  }

  // Check for skip environment variables
  const detectedEnv = detectSkipEnv(command);
  if (detectedEnv) {
    const reason = `${detectedEnv}の使用は禁止されています。

**なぜブロックするか:**
SKIP環境変数でレビューチェックをバイパスすると、コード品質チェックが
欠落したままPRがマージされる可能性があります。

**正しい対応:**
1. \`codex review --base main\` を実行してレビューを完了させる
2. \`gemini "/code-review" --yolo -e code-review\` を実行してレビューを完了させる
3. その後、通常通り \`git push\` または \`gh pr create\` を実行する

**背景:**
Issue #2942: SKIP環境変数でバイパスしてマージされた例があり、
品質チェックが欠落した。

**参照:**
- AGENTS.md「AIレビューは最後の砦」セクション`;

    await logHookExecution(HOOK_NAME, "block", `${detectedEnv} detected`, undefined, { sessionId });
    console.log(JSON.stringify(makeBlockResult(HOOK_NAME, reason)));
    process.exit(2);
  }

  // No skip env detected
  console.log(JSON.stringify(makeApproveResult()));
}

// Only run main when executed directly (not when imported in tests)
if (import.meta.main) {
  main().catch((error) => {
    console.error(`[${HOOK_NAME}] Error:`, error);
    // Don't block on hook errors
    console.log(JSON.stringify(makeApproveResult()));
  });
}
