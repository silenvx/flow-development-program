#!/usr/bin/env bun
/**
 * フックのテストカバレッジをチェックする。
 *
 * Why:
 *   PRで追加・変更されたフックにテストがあるか確認し、
 *   テスト不足を検出するため。
 *
 * What:
 *   - getChangedFiles(): PR内の変更ファイルを取得
 *   - getPythonHookFiles(): Pythonフックファイル一覧を取得
 *   - getTsHookFiles(): TypeScriptフックファイル一覧を取得
 *   - checkTestCoverage(): テストファイルの存在を確認
 *
 * Remarks:
 *   - 新規フック: テストファイル必須（なければCI失敗）
 *   - 既存フック（テストなし）: 警告のみ
 *   - Python: .claude/hooks/*.py → tests/test_{hook_name}.py
 *   - TypeScript: .claude/hooks/handlers/*.ts → tests/{hook_name}.test.ts
 *
 * Changelog:
 *   - silenvx/dekita#1300: テストカバレッジチェック機能を追加
 *   - silenvx/dekita#2967: TypeScriptフックのカバレッジチェックを追加
 *   - silenvx/dekita#3643: TypeScriptに移植
 *   - silenvx/dekita#3644: Windowsパス正規化をpath.sepベースに改善
 */

import { execFileSync } from "node:child_process";
import { existsSync, readdirSync } from "node:fs";
import { basename, dirname, join, relative, resolve, sep } from "node:path";

/**
 * Normalize path separators to forward slashes for cross-platform compatibility.
 * Uses path.sep for proper Windows detection instead of hardcoded regex.
 */
export function normalizePath(filePath: string): string {
  return sep === "\\" ? filePath.split(sep).join("/") : filePath;
}

/** Get project root directory from script location. */
export function getProjectRoot(): string {
  const scriptDir = dirname(import.meta.path);
  // .claude/scripts → project root
  return resolve(scriptDir, "..", "..");
}

/**
 * Get files changed in this PR compared to base branch.
 *
 * @returns List of changed file paths, or null if git diff failed.
 *          When null is returned, caller should treat all hooks as changed.
 */
export function getChangedFiles(): string[] | null {
  const baseRef = process.env.GITHUB_BASE_REF ?? "main";

  try {
    // Use execFileSync to avoid command injection via environment variables
    const result = execFileSync("git", ["diff", "--name-only", `origin/${baseRef}...HEAD`], {
      encoding: "utf-8",
    });
    const files = result.trim();
    return files ? files.split("\n") : [];
  } catch (error) {
    console.error(`⚠️  git diff failed: ${error}`);
    return null;
  }
}

/**
 * Get all Python hook files (excluding common.py and __init__.py).
 */
export function getPythonHookFiles(): string[] {
  const projectRoot = getProjectRoot();
  const hooksDir = join(projectRoot, ".claude", "hooks");
  const excluded = new Set(["common.py", "__init__.py"]);

  if (!existsSync(hooksDir)) {
    return [];
  }

  return readdirSync(hooksDir)
    .filter((f) => f.endsWith(".py") && !excluded.has(f) && !f.startsWith("test_"))
    .map((f) => join(hooksDir, f));
}

/**
 * Get all TypeScript hook files (excluding .d.ts type definitions).
 */
export function getTsHookFiles(): string[] {
  const projectRoot = getProjectRoot();
  const hooksDir = join(projectRoot, ".claude", "hooks", "handlers");

  if (!existsSync(hooksDir)) {
    return [];
  }

  return readdirSync(hooksDir)
    .filter((f) => f.endsWith(".ts") && !f.endsWith(".d.ts"))
    .map((f) => join(hooksDir, f));
}

/**
 * Get the expected test file path for a Python hook.
 */
export function getTestFileForPythonHook(hookFile: string): string {
  const projectRoot = getProjectRoot();
  const hookName = basename(hookFile, ".py").replace(/-/g, "_");
  return join(projectRoot, ".claude", "hooks", "tests", `test_${hookName}.py`);
}

/**
 * Get the expected test file path for a TypeScript hook.
 */
export function getTestFileForTsHook(hookFile: string): string {
  const projectRoot = getProjectRoot();
  const hookName = basename(hookFile, ".ts");
  return join(projectRoot, ".claude", "hooks", "tests", `${hookName}.test.ts`);
}

/**
 * Check if test files exist for a Python hook.
 *
 * Supports both single test file (test_{hook_name}.py) and
 * split test files (test_{hook_name}_*.py).
 */
export function hasTestFilesForPythonHook(hookFile: string): boolean {
  const projectRoot = getProjectRoot();
  const hookName = basename(hookFile, ".py").replace(/-/g, "_");
  const testsDir = join(projectRoot, ".claude", "hooks", "tests");

  // Check for exact match
  const exactTest = join(testsDir, `test_${hookName}.py`);
  if (existsSync(exactTest)) {
    return true;
  }

  // Check for split test files (test_{hook_name}_*.py)
  if (!existsSync(testsDir)) {
    return false;
  }

  const pattern = `test_${hookName}_`;
  return readdirSync(testsDir).some((f) => f.startsWith(pattern) && f.endsWith(".py"));
}

/**
 * Check if test files exist for a TypeScript hook.
 *
 * Supports both single test file ({hook_name}.test.ts) and
 * split test files ({hook_name}_*.test.ts).
 */
export function hasTestFilesForTsHook(hookFile: string): boolean {
  const projectRoot = getProjectRoot();
  const hookName = basename(hookFile, ".ts");
  const testsDir = join(projectRoot, ".claude", "hooks", "tests");

  // Check for exact match ({hook_name}.test.ts)
  const exactTest = join(testsDir, `${hookName}.test.ts`);
  if (existsSync(exactTest)) {
    return true;
  }

  // Check for split test files ({hook_name}_*.test.ts)
  if (!existsSync(testsDir)) {
    return false;
  }

  const pattern = `${hookName}_`;
  return readdirSync(testsDir).some((f) => f.startsWith(pattern) && f.endsWith(".test.ts"));
}

export interface CheckResult {
  newHooksWithoutTests: string[];
  existingHooksWithoutTests: string[];
}

/**
 * Check test coverage for a list of hooks.
 */
export function checkHooks(
  hookFiles: string[],
  hasTestFunc: (hookFile: string) => boolean,
  changedFiles: string[] | null,
): CheckResult {
  const diffAvailable = changedFiles !== null;
  const newHooksWithoutTests: string[] = [];
  const existingHooksWithoutTests: string[] = [];

  for (const hook of hookFiles) {
    const hasTest = hasTestFunc(hook);

    if (!hasTest) {
      // Convert absolute hook path to relative path matching git output
      const relativeHookPath = normalizePath(relative(getProjectRoot(), hook));
      const isChanged = !diffAvailable || changedFiles.includes(relativeHookPath);

      if (isChanged) {
        newHooksWithoutTests.push(hook);
      } else {
        existingHooksWithoutTests.push(hook);
      }
    }
  }

  return { newHooksWithoutTests, existingHooksWithoutTests };
}

/**
 * Report missing tests for hooks.
 *
 * @returns True if there are critical errors (new hooks without tests).
 */
function reportMissingTests(
  label: string,
  newHooksWithoutTests: string[],
  existingHooksWithoutTests: string[],
  getTestFileFunc: (hookFile: string) => string,
): boolean {
  let hasErrors = false;

  if (newHooksWithoutTests.length > 0) {
    console.log(`❌ 新規/変更された${label}フックにテストがありません:`);
    for (const hook of newHooksWithoutTests) {
      const testFile = getTestFileFunc(hook);
      console.log(`   ${basename(hook)} -> ${testFile} を作成してください`);
    }
    hasErrors = true;
  }

  if (existingHooksWithoutTests.length > 0) {
    console.log(`⚠️  既存${label}フックにテストがありません（警告のみ）:`);
    for (const hook of existingHooksWithoutTests) {
      console.log(`   ${basename(hook)}`);
    }
  }

  return hasErrors;
}

function main(): void {
  const changedFiles = getChangedFiles();

  // Get all hook files
  const pythonHooks = getPythonHookFiles();
  const tsHooks = getTsHookFiles();

  // Check Python hooks
  const { newHooksWithoutTests: pyNewWithout, existingHooksWithoutTests: pyExistingWithout } =
    checkHooks(pythonHooks, hasTestFilesForPythonHook, changedFiles);

  // Check TypeScript hooks
  const { newHooksWithoutTests: tsNewWithout, existingHooksWithoutTests: tsExistingWithout } =
    checkHooks(tsHooks, hasTestFilesForTsHook, changedFiles);

  // Report
  const pyHasErrors = reportMissingTests(
    "Python",
    pyNewWithout,
    pyExistingWithout,
    getTestFileForPythonHook,
  );
  const tsHasErrors = reportMissingTests(
    "TypeScript",
    tsNewWithout,
    tsExistingWithout,
    getTestFileForTsHook,
  );
  const exitCode = pyHasErrors || tsHasErrors ? 1 : 0;

  // Summary
  if (exitCode === 0) {
    const pyTested = pythonHooks.length - pyExistingWithout.length;
    const tsTested = tsHooks.length - tsExistingWithout.length;
    console.log(`✅ Pythonフックテストカバレッジ: ${pyTested}/${pythonHooks.length} フック`);
    console.log(`✅ TypeScriptフックテストカバレッジ: ${tsTested}/${tsHooks.length} フック`);
  }

  process.exit(exitCode);
}

if (import.meta.main) {
  main();
}
