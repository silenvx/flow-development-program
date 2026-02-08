#!/usr/bin/env bun
/**
 * E2Eテスト実行結果を記録する。
 *
 * Why:
 *   e2e-test-check.pyがプッシュ前にローカルテスト実行を検証するため、
 *   テスト結果の記録が必要。結果を永続化することで、プッシュ時の
 *   判定が可能になる。
 *
 * What:
 *   - npm run test:e2e等のコマンド完了を検出
 *   - 終了コードと出力からテスト結果（pass/fail）を判定
 *   - ブランチ・コミット情報とともに結果を記録
 *
 * State:
 *   - writes: .claude/state/markers/e2e-test-{branch}.done
 *
 * Remarks:
 *   - 記録型フック（ブロックしない、マーカーファイル書き込み）
 *   - PostToolUse:Bashで発火（npm/pnpm test:e2e、npx playwrightコマンド）
 *   - e2e-test-check.pyと連携（マーカーファイル参照元）
 *   - exit_codeまたは出力パターンでpass/failを判定
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2874: TypeScriptに移植
 *   - silenvx/dekita#2998: getMarkersDir()使用でworktree→メインリポジトリ解決
 */

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { formatError } from "../lib/format_error";
import { getCurrentBranch, getHeadCommit } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { getMarkersDir } from "../lib/markers";
import { getBashCommand, getToolResultAsObject, parseHookInput } from "../lib/session";
import { sanitizeBranchName } from "../lib/strings";

const HOOK_NAME = "e2e-test-recorder";

/**
 * Check if command is an E2E test command.
 */
export function isE2eTestCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }

  // Match npm run test:e2e commands
  if (/npm\s+run\s+test:e2e/.test(command)) {
    return true;
  }

  // Match pnpm test:e2e commands
  if (/pnpm\s+(run\s+)?test:e2e/.test(command)) {
    return true;
  }

  // Match npx playwright test commands
  if (/npx\s+playwright\s+test/.test(command)) {
    return true;
  }

  return false;
}

/**
 * Record E2E test run result.
 */
function recordE2eTestRun(branch: string, commit: string, passed: boolean): void {
  const markersDir = getMarkersDir();
  if (!existsSync(markersDir)) {
    mkdirSync(markersDir, { recursive: true });
  }
  const safeBranch = sanitizeBranchName(branch);
  const logFile = `${markersDir}/e2e-test-${safeBranch}.done`;

  const timestamp = Date.now() / 1000;
  const result = passed ? "pass" : "fail";
  writeFileSync(logFile, `${branch}:${commit}:${timestamp}:${result}`);
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolResult = getToolResultAsObject(data);
    const command = getBashCommand(data);

    // Only process E2E test commands
    if (!isE2eTestCommand(command)) {
      process.exit(0);
    }

    // Check if tests passed based on exit code and output
    const stdout = typeof toolResult.stdout === "string" ? toolResult.stdout : "";
    const stderr = typeof toolResult.stderr === "string" ? toolResult.stderr : "";
    const exitCode = typeof toolResult.exit_code === "number" ? toolResult.exit_code : undefined;

    // Determine if tests passed
    let passed: boolean;
    if (exitCode !== undefined) {
      passed = exitCode === 0;
    } else {
      // Fallback: check output patterns
      const failureMatch = stdout.toLowerCase().match(/(\d+)\s+failed/);
      const hasFailures = failureMatch && Number.parseInt(failureMatch[1], 10) > 0;
      const hasErrors = stderr.toLowerCase().includes("error:") || stdout.includes("FAILED");
      passed = !hasFailures && !hasErrors;
    }

    const branch = await getCurrentBranch();
    const commit = await getHeadCommit();

    if (branch && commit) {
      recordE2eTestRun(branch, commit, passed);
      const status = passed ? "pass" : "fail";
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Recorded E2E test result: ${status} for ${branch}@${commit.slice(0, 7)}`,
        undefined,
        { sessionId },
      );
    }
  } catch (error) {
    // Don't block on errors, just log
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
  }

  process.exit(0);
}

if (import.meta.main) {
  main();
}
