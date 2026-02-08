#!/usr/bin/env bun
/**
 * Pythonの関数シグネチャ変更時にテスト更新漏れを検出。
 *
 * Why:
 *   関数の引数や戻り値の型を変更した場合、対応するテストも更新する必要がある。
 *   テスト更新漏れがあると、CI通過後に実際の動作で問題が発生する。
 *
 * What:
 *   - git diff でPython関数シグネチャ（引数、戻り値）の変更を検出
 *   - 対応するテストファイル（test_xxx.py）がコミットに含まれているか確認
 *   - テストファイル更新がない場合に警告を表示
 *   - .claude/hooks/ と .claude/scripts/ 配下のファイルを対象
 *
 * State:
 *   - reads: git diff output
 *
 * Remarks:
 *   - 非ブロック型（警告のみ、pushは許可）
 *   - pre-pushフックとして使用可能
 *   - ファイル名のハイフンはアンダースコアに変換してテストファイル名を推定
 *   - 制限: 単一行の関数定義のみ検出（複数行定義は未対応）
 *
 * Changelog:
 *   - silenvx/dekita#1108: フック追加（Issue #1102の再発防止）
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { basename } from "node:path";
import { getOriginDefaultBranch } from "../lib/git";
import { asyncSpawn } from "../lib/spawn";

const _HOOK_NAME = "signature-change-check";

export interface SignatureChange {
  functionName: string;
  changeType: "args" | "return" | "both";
  oldArgs: string;
  newArgs: string;
  oldReturn: string | null;
  newReturn: string | null;
}

/**
 * Get the base branch for diff comparison.
 * Uses getOriginDefaultBranch for consistent detection across all hooks.
 */
async function getBaseBranch(): Promise<string> {
  return await getOriginDefaultBranch(process.cwd());
}

/**
 * Get list of Python files modified in this push.
 */
async function getModifiedPythonFiles(): Promise<string[]> {
  try {
    const baseBranch = await getBaseBranch();
    const result = await asyncSpawn("git", ["diff", "--name-only", `${baseBranch}...HEAD`], {
      timeout: 10000,
    });
    if (!result.success) {
      return [];
    }
    return result.stdout
      .trim()
      .split("\n")
      .filter((f) => f.endsWith(".py") && f);
  } catch {
    return [];
  }
}

/**
 * Get the diff for a specific file.
 */
async function getDiffForFile(filepath: string): Promise<string> {
  try {
    const baseBranch = await getBaseBranch();
    const result = await asyncSpawn("git", ["diff", `${baseBranch}...HEAD`, "--", filepath], {
      timeout: 10000,
    });
    if (!result.success) {
      return "";
    }
    return result.stdout;
  } catch {
    return "";
  }
}

/**
 * Extract function signature changes from a diff.
 *
 * Note: This function only detects single-line function definitions.
 * Multi-line definitions (formatted by Black/Ruff) may not be detected.
 * This is a known limitation inherited from the original Python implementation.
 */
export function extractSignatureChanges(diff: string): SignatureChange[] {
  const changes: SignatureChange[] = [];

  // Pattern for function definition lines (added or removed)
  // Matches: [async] def function_name(args) -> return_type:
  // Limitation: Only matches single-line definitions, but handles tuples in default values
  // Note: Uses greedy (.*) to capture args with nested parens (e.g., def func(a=(1, 2)):)
  const funcPattern = /^[-+]\s*(?:async\s+)?def\s+(\w+)\s*\((.*)\)(?:\s*->\s*([^:]+))?\s*:/;

  const lines = diff.split("\n");

  // Track old and new signatures for comparison
  const oldSigs: Map<string, [string, string | null]> = new Map(); // name -> [args, return_type]
  const newSigs: Map<string, [string, string | null]> = new Map();

  for (const line of lines) {
    const match = line.match(funcPattern);
    if (match) {
      const prefix = line[0];
      const funcName = match[1];
      const args = match[2].trim();
      const returnType = match[3]?.trim() ?? null;

      if (prefix === "-") {
        oldSigs.set(funcName, [args, returnType]);
      } else if (prefix === "+") {
        newSigs.set(funcName, [args, returnType]);
      }
    }
  }

  // Find functions with signature changes
  for (const funcName of oldSigs.keys()) {
    if (!newSigs.has(funcName)) {
      continue;
    }

    const [oldArgs, oldReturn] = oldSigs.get(funcName)!;
    const [newArgs, newReturn] = newSigs.get(funcName)!;

    let changeType: "args" | "return" | "both" | null = null;
    if (oldArgs !== newArgs && oldReturn !== newReturn) {
      changeType = "both";
    } else if (oldArgs !== newArgs) {
      changeType = "args";
    } else if (oldReturn !== newReturn) {
      changeType = "return";
    }

    if (changeType) {
      changes.push({
        functionName: funcName,
        changeType,
        oldArgs,
        newArgs,
        oldReturn,
        newReturn,
      });
    }
  }

  return changes;
}

/**
 * Find the corresponding test file for a source file.
 *
 * Maps:
 * - .claude/hooks/foo.py -> .claude/hooks/tests/test_foo.py
 * - .claude/hooks/foo_bar.py -> .claude/hooks/tests/test_foo_bar.py
 * - .claude/scripts/foo.py -> .claude/scripts/tests/test_foo.py
 */
export function findTestFile(sourceFile: string): string | null {
  const fileName = basename(sourceFile);

  // Skip if already a test file
  if (fileName.startsWith("test_")) {
    return null;
  }

  // Normalize filename: convert hyphens to underscores for test file naming
  const normalizedName = fileName.replace(/-/g, "_");

  // Determine test file location
  if (sourceFile.includes(".claude/hooks")) {
    return `.claude/hooks/tests/test_${normalizedName}`;
  }
  if (sourceFile.includes(".claude/scripts")) {
    return `.claude/scripts/tests/test_${normalizedName}`;
  }
  // For other files, assume tests/ directory at same level
  const parts = sourceFile.split("/");
  parts.pop(); // Remove filename
  return `${parts.join("/")}/tests/test_${normalizedName}`;
}

async function main(): Promise<number> {
  const modifiedFiles = await getModifiedPythonFiles();

  if (modifiedFiles.length === 0) {
    return 0;
  }

  // Filter to only .claude/ files (hooks and scripts)
  const claudeFiles = modifiedFiles.filter((f) => f.startsWith(".claude/"));

  if (claudeFiles.length === 0) {
    return 0;
  }

  const warnings: string[] = [];

  for (const filepath of claudeFiles) {
    const fileName = basename(filepath);

    // Skip test files themselves
    if (fileName.startsWith("test_") || filepath.includes("/tests/")) {
      continue;
    }

    const diff = await getDiffForFile(filepath);
    const changes = extractSignatureChanges(diff);

    if (changes.length === 0) {
      continue;
    }

    const testFile = findTestFile(filepath);
    if (!testFile) {
      continue;
    }

    // Check if test file is also modified
    if (!modifiedFiles.includes(testFile)) {
      for (const change of changes) {
        let detail: string;
        if (change.changeType === "return") {
          detail = `  戻り値: ${change.oldReturn} → ${change.newReturn}`;
        } else if (change.changeType === "args") {
          detail = `  引数: ${change.oldArgs} → ${change.newArgs}`;
        } else {
          detail =
            `  引数: ${change.oldArgs} → ${change.newArgs}\n` +
            `  戻り値: ${change.oldReturn} → ${change.newReturn}`;
        }

        warnings.push(
          `⚠️  関数シグネチャ変更を検出:\n  ファイル: ${filepath}\n  関数: ${change.functionName}()\n${detail}\n  テストファイル: ${testFile}\n  → テストファイルが更新されていません！`,
        );
      }
    }
  }

  if (warnings.length > 0) {
    console.log(`\n${"=".repeat(60)}`);
    console.log("🔍 関数シグネチャ変更チェック (Issue #1108)");
    console.log("=".repeat(60));
    for (const warning of warnings) {
      console.log(`\n${warning}`);
    }
    console.log(`\n${"-".repeat(60)}`);
    console.log("💡 対処方法:");
    console.log("  1. テストファイルを確認し、シグネチャ変更に対応する更新を行う");
    console.log("  2. テストが既に正しい場合は、このまま続行しても問題ありません");
    console.log(`${"=".repeat(60)}\n`);

    // Warning only, don't block
    return 0;
  }

  return 0;
}

if (import.meta.main) {
  main()
    .then((code) => process.exit(code))
    .catch((error) => {
      console.error(error);
      process.exit(1);
    });
}
