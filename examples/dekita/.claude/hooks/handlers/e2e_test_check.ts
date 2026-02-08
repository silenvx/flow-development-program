#!/usr/bin/env bun
/**
 * CI E2E失敗後のローカルテスト実行を強制する。
 *
 * Why:
 *   CI E2Eテストが失敗した状態でプッシュを繰り返すと、CI負荷増加と
 *   開発効率低下を招く。ローカルでテストを通すことで、高品質な
 *   コードのみがプッシュされる。
 *
 * What:
 *   - git pushコマンドを検出
 *   - E2Eテストファイル（tests/**\/*.spec.ts）の変更を確認
 *   - CI失敗記録があり、ローカルテスト成功記録がない場合はブロック
 *
 * State:
 *   reads: .claude/state/markers/ci-e2e-failure-{branch}.log
 *   reads: .claude/state/markers/e2e-test-{branch}.done
 *
 * Remarks:
 *   - ローカルテスト結果は30分間有効
 *   - CI失敗記録は4時間で自動的に無効化される
 *
 * Changelog:
 *   - silenvx/dekita#3160: TypeScriptに移植
 */

import { existsSync, readFileSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import { MARKERS_LOG_DIR, TIMEOUT_MEDIUM } from "../lib/common";
import { formatError } from "../lib/format_error";
import { getCurrentBranch } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { approveAndExit, blockAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";
import { sanitizeBranchName, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "e2e-test-check";

// E2E test results are valid for 30 minutes
const E2E_RESULT_VALIDITY_SECONDS = 30 * 60;
// CI failure is considered stale after 4 hours (new CI run likely)
const CI_FAILURE_STALE_SECONDS = 4 * 60 * 60;

/**
 * Check if command is a git push command.
 */
function isGitPushCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }

  const strippedCommand = stripQuotedStrings(command);
  if (!/git\s+push\b/.test(strippedCommand)) {
    return false;
  }
  if (/--help/.test(strippedCommand)) {
    return false;
  }
  return true;
}

/**
 * Get list of files changed between HEAD and main branch.
 */
async function getChangedFiles(): Promise<string[]> {
  try {
    const result = await asyncSpawn("git", ["diff", "--name-only", "main...HEAD"], {
      timeout: TIMEOUT_MEDIUM * 1000,
    });
    if (result.success && result.stdout.trim()) {
      return result.stdout.trim().split("\n");
    }
  } catch {
    // Ignore errors
  }
  return [];
}

/**
 * Check if any E2E test files were changed.
 */
function hasE2eTestChanges(changedFiles: string[]): boolean {
  return changedFiles.some((file) => file.startsWith("tests/") && file.endsWith(".spec.ts"));
}

/**
 * Get list of changed E2E test files.
 */
function getChangedE2eFiles(changedFiles: string[]): string[] {
  return changedFiles.filter((file) => file.startsWith("tests/") && file.endsWith(".spec.ts"));
}

/**
 * Check if CI E2E tests have failed recently for this branch.
 */
function checkCiE2eFailure(branch: string): [boolean, number | null] {
  const safeBranch = sanitizeBranchName(branch);
  const logFile = join(MARKERS_LOG_DIR, `ci-e2e-failure-${safeBranch}.log`);

  if (!existsSync(logFile)) {
    return [false, null];
  }

  try {
    const content = readFileSync(logFile, "utf-8").trim();
    // Format: branch:timestamp
    const parts = content.split(":");
    if (parts.length >= 2) {
      const timestamp = Number.parseFloat(parts[1]);
      const currentTime = Date.now() / 1000;
      // Consider failure stale after CI_FAILURE_STALE_SECONDS
      const isRecent = currentTime - timestamp < CI_FAILURE_STALE_SECONDS;
      return [isRecent, timestamp];
    }
  } catch {
    // Silent ignore: malformed log files don't block push
  }

  return [false, null];
}

/**
 * Clear CI E2E failure record after local tests pass.
 */
function clearCiE2eFailure(branch: string): void {
  const safeBranch = sanitizeBranchName(branch);
  const logFile = join(MARKERS_LOG_DIR, `ci-e2e-failure-${safeBranch}.log`);
  if (existsSync(logFile)) {
    try {
      unlinkSync(logFile);
    } catch {
      // Ignore errors
    }
  }
}

/**
 * Check if E2E tests passed locally recently for this branch.
 */
function checkLocalE2eTestPass(branch: string): [boolean, number | null] {
  const safeBranch = sanitizeBranchName(branch);
  const logFile = join(MARKERS_LOG_DIR, `e2e-test-${safeBranch}.done`);

  if (!existsSync(logFile)) {
    return [false, null];
  }

  try {
    const content = readFileSync(logFile, "utf-8").trim();
    // Format: branch:commit:timestamp:result
    const parts = content.split(":");
    if (parts.length >= 4) {
      const timestamp = Number.parseFloat(parts[2]);
      const result = parts[3];
      const currentTime = Date.now() / 1000;
      const isValid = currentTime - timestamp < E2E_RESULT_VALIDITY_SECONDS;
      const isPass = result === "pass";
      return [isValid && isPass, timestamp];
    }
  } catch {
    // Silent ignore: malformed log files don't block push
  }

  return [false, null];
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const command = (data.tool_input?.command as string) ?? "";

    // Only check git push commands
    if (!isGitPushCommand(command)) {
      await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      approveAndExit(HOOK_NAME);
    }

    const branch = await getCurrentBranch();

    // Skip check for main/master branches
    if (!branch || branch === "main" || branch === "master") {
      await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      approveAndExit(HOOK_NAME);
    }

    // Check if E2E test files were changed
    const changedFiles = await getChangedFiles();
    if (!hasE2eTestChanges(changedFiles)) {
      await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      approveAndExit(HOOK_NAME);
    }

    // Check if CI has failed recently
    const [hasCiFailure, ciFailureTime] = checkCiE2eFailure(branch);

    if (!hasCiFailure) {
      // No recent CI failure - allow push (CI will verify)
      await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      approveAndExit(HOOK_NAME);
    }

    // CI has failed - require local test pass AFTER the CI failure
    const [hasLocalPass, localPassTime] = checkLocalE2eTestPass(branch);

    if (hasLocalPass && localPassTime !== null && ciFailureTime !== null) {
      // Only accept local pass if it occurred AFTER the CI failure
      if (localPassTime > ciFailureTime) {
        // Local tests passed after CI failure - clear failure record and allow push
        clearCiE2eFailure(branch);
        await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
        approveAndExit(HOOK_NAME);
      }
    }

    // CI failed and no local pass - block
    const changedE2e = getChangedE2eFiles(changedFiles);
    let filesList = changedE2e
      .slice(0, 5)
      .map((f) => `  - ${f}`)
      .join("\n");
    if (changedE2e.length > 5) {
      filesList += `\n  ... and ${changedE2e.length - 5} more`;
    }

    const reason = [
      "CI E2Eテストが失敗しています。ローカルでテストを通してからプッシュしてください。",
      "",
      `変更されたテストファイル:\n${filesList}`,
      "",
      "以下のコマンドでローカルテストを実行してください:",
      "",
      "```bash",
      "npm run test:e2e:chromium -- tests/stories/",
      "```",
      "",
      "テスト成功後、再度プッシュしてください。",
      "（ローカルテストの結果は30分間有効です）",
    ].join("\n");

    await logHookExecution(HOOK_NAME, "block", reason, undefined, { sessionId });
    blockAndExit(HOOK_NAME, reason);
  } catch (e) {
    console.error(`[${HOOK_NAME}] Hook error:`, e);
    await logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(e)}`, undefined, {
      sessionId,
    });
    approveAndExit(HOOK_NAME);
  }
}

if (import.meta.main) {
  main();
}
