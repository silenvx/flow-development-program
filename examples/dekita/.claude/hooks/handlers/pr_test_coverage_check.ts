#!/usr/bin/env bun
/**
 * gh pr create時に変更されたフックのテストカバレッジを確認。
 *
 * Why:
 *   フック変更時にテストがないとエッジケースの見落としが発生する。
 *   PR作成前にテスト不足を警告し、品質維持を促す。
 *
 * What:
 *   - gh pr create コマンドを検出
 *   - mainブランチとの差分から変更ファイルを取得
 *   - .claude/hooks/*.py に対応する tests/test_{name}.py の存在確認
 *   - テストがないフックについて警告
 *
 * Remarks:
 *   - 非ブロック型（警告のみ、fail-open設計）
 *   - __init__.py, common.py はスキップ
 *
 * Changelog:
 *   - silenvx/dekita#3160: TypeScriptに移植
 */

import { existsSync } from "node:fs";
import { basename, join } from "node:path";
import { PROJECT_DIR, TIMEOUT_MEDIUM } from "../lib/common";
import { logHookExecution } from "../lib/logging";
import { approveAndExit } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";
import { stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "pr-test-coverage-check";
const HOOKS_DIR = ".claude/hooks";
const TESTS_DIR = ".claude/hooks/tests";

/**
 * Check if command is a gh pr create command.
 */
function isGhPrCreateCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  const strippedCommand = stripQuotedStrings(command);
  return /gh\s+pr\s+create\b/.test(strippedCommand);
}

/**
 * Get list of changed files compared to base branch.
 */
async function getChangedFiles(baseBranch = "main"): Promise<string[]> {
  try {
    const result = await asyncSpawn("git", ["diff", "--name-only", baseBranch], {
      timeout: TIMEOUT_MEDIUM * 1000,
    });
    if (result.success && result.stdout.trim()) {
      return result.stdout
        .trim()
        .split("\n")
        .filter((f) => f.trim());
    }
  } catch (e) {
    console.error(`[${HOOK_NAME}] Failed to get changed files:`, e);
  }
  return [];
}

/**
 * Find hook files that don't have corresponding test files.
 */
function getHookFilesWithoutTests(
  changedFiles: string[],
): Array<{ hookFile: string; expectedTest: string }> {
  const missingTests: Array<{ hookFile: string; expectedTest: string }> = [];

  for (const filePath of changedFiles) {
    // Only check .claude/hooks/*.py or .claude/hooks/handlers/*.ts files
    if (!filePath.startsWith(HOOKS_DIR)) {
      continue;
    }
    const isPython = filePath.endsWith(".py");
    const isTypeScript = filePath.endsWith(".ts") && filePath.includes("/handlers/");
    if (!isPython && !isTypeScript) {
      continue;
    }
    // Skip test files themselves
    if (filePath.includes("/tests/")) {
      continue;
    }
    // Skip utility files
    const filename = basename(filePath);
    if (isPython && (filename === "__init__.py" || filename === "common.py")) {
      continue;
    }

    // Check for corresponding test file
    // Python: .claude/hooks/my_hook.py -> .claude/hooks/tests/test_my_hook.py
    // TypeScript: .claude/hooks/handlers/my_hook.ts -> .claude/hooks/tests/my_hook.test.ts
    let expectedTest: string;
    if (isPython) {
      const stem = basename(filePath, ".py").replace(/-/g, "_");
      expectedTest = `${TESTS_DIR}/test_${stem}.py`;
    } else {
      const stem = basename(filePath, ".ts").replace(/-/g, "_");
      expectedTest = `.claude/hooks/tests/${stem}.test.ts`;
    }

    // Check if test file exists
    const fullTestPath = join(PROJECT_DIR, expectedTest);
    if (!existsSync(fullTestPath)) {
      missingTests.push({ hookFile: filePath, expectedTest });
    }
  }

  return missingTests;
}

/**
 * Format warning message for missing tests.
 */
function formatWarningMessage(
  missingTests: Array<{ hookFile: string; expectedTest: string }>,
): string {
  const lines = [
    "⚠️ **テストファイル不足の警告**",
    "",
    "以下のhookファイルに対応するテストが見つかりません:",
    "",
  ];

  for (const { hookFile, expectedTest } of missingTests) {
    lines.push(`  - \`${hookFile}\``);
    lines.push(`    → 期待されるテスト: \`${expectedTest}\``);
  }

  lines.push("");
  lines.push("テストを追加することで、エッジケースの見落としを防げます。");
  lines.push("（この警告はブロックしません）");

  return lines.join("\n");
}

async function main(): Promise<void> {
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);
    sessionId = ctx.sessionId;
    const command = (data.tool_input?.command as string) ?? "";

    if (isGhPrCreateCommand(command)) {
      const changedFiles = await getChangedFiles();
      const missingTests = getHookFilesWithoutTests(changedFiles);

      if (missingTests.length > 0) {
        const systemMessage = formatWarningMessage(missingTests);
        await logHookExecution(
          HOOK_NAME,
          "approve",
          `Missing tests for ${missingTests.length} hooks`,
          undefined,
          { sessionId },
        );
        // Approve with warning message
        console.log(JSON.stringify({ systemMessage }));
        process.exit(0);
      }
    }
  } catch (e) {
    // Don't block on errors - fail-open design
    console.error(`[${HOOK_NAME}] Error:`, e);
  }

  await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
  approveAndExit(HOOK_NAME);
}

if (import.meta.main) {
  main();
}
