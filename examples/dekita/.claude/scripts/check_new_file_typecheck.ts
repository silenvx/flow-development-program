#!/usr/bin/env bun
/**
 * 新規TypeScript/JavaScriptファイルの型エラーをチェックする。
 *
 * Why:
 *   PR内で新規作成されたTypeScript/JavaScriptファイルに型エラーがあるとき、
 *   それを検出してブロックするため。既存ファイルの型エラーは警告のみとし、
 *   段階的に型安全性を向上させる。
 *
 * What:
 *   - getNewFiles(): PRで新規追加されたTS/JSファイルを検出
 *   - getTypecheckErrors(): tscの出力を解析してエラー情報を取得
 *   - checkTypeErrors(): 新規ファイルの型エラーをチェック
 *
 * Remarks:
 *   - 新規ファイルに型エラーがあれば exit 1（ブロック）
 *   - 既存ファイルの型エラーは警告のみ（exit 0）
 *   - CIとpre-pushの両方で使用可能
 *   - 対象拡張子: .ts, .tsx, .mts, .cts, .js, .jsx, .mjs, .cjs
 *   - 除外: .d.ts, .d.mts, .d.cts (宣言ファイル)
 *
 * Design Notes:
 *   - 現在このプロジェクトはtype error 0件を維持している
 *   - --strictフラグ: 型エラーがあれば失敗（全サブプロジェクトをチェック後に判定）
 *   - フラグなし: 新規ファイルのみブロック、既存は警告（段階的エンフォースメント）
 *   - CIとpre-pushでは--strictで実行し、重複実行を回避
 *
 * Path Handling:
 *   - git diff: プロジェクトルート相対パス (例: frontend/src/main.tsx)
 *   - tsc (monorepo): サブプロジェクト相対パス (例: src/main.tsx)
 *   - このスクリプトはサブプロジェクトプレフィックスを付加してマッチング
 *
 * Changelog:
 *   - silenvx/dekita#3464: 新規ファイル型チェック機能を追加
 *   - silenvx/dekita#3941: --strictフラグを追加、CI/pre-push重複実行を回避
 *   - silenvx/dekita#3939: .mts/.cts/.js等の拡張子対応、.d.mts/.d.cts除外
 */

import { execFileSync, spawnSync } from "node:child_process";
import { dirname, isAbsolute, relative, resolve } from "node:path";

/** Get project root directory from script location. */
export function getProjectRoot(): string {
  const scriptDir = dirname(import.meta.path);
  // .claude/scripts → project root
  return resolve(scriptDir, "..", "..");
}

/**
 * TypeScriptエラー情報
 */
export interface TypecheckError {
  file: string;
  line: number;
  column: number;
  code: string;
  message: string;
}

/**
 * Get files added in this PR compared to base branch.
 *
 * @returns List of new file paths (relative to project root), or null if git diff failed.
 */
export function getNewFiles(): string[] | null {
  const baseRef = process.env.GITHUB_BASE_REF ?? "main";

  try {
    // --diff-filter=A: Added files only
    // Use execFileSync to avoid command injection via GITHUB_BASE_REF
    const result = execFileSync(
      "git",
      ["diff", "--name-only", "--diff-filter=A", `origin/${baseRef}...HEAD`],
      {
        encoding: "utf-8",
        cwd: getProjectRoot(),
      },
    );
    const files = result.trim();
    if (!files) return [];

    // Filter for TypeScript/JavaScript files that tsc may check (excluding .d.ts, .d.mts, .d.cts)
    // Supports: .ts, .tsx, .mts, .cts, .js, .jsx, .mjs, .cjs
    return files
      .split(/\r?\n/)
      .filter((f) => /\.[cm]?[tj]sx?$/.test(f) && !/\.d\.[cm]?ts$/.test(f));
  } catch (error) {
    console.error(`\u26a0\ufe0f  git diff failed: ${error}`);
    return null;
  }
}

/**
 * Get all changed files in this PR compared to base branch.
 *
 * @returns List of changed file paths, or null if git diff failed.
 */
export function getChangedFiles(): string[] | null {
  const baseRef = process.env.GITHUB_BASE_REF ?? "main";

  try {
    // Use execFileSync to avoid command injection via GITHUB_BASE_REF
    const result = execFileSync("git", ["diff", "--name-only", `origin/${baseRef}...HEAD`], {
      encoding: "utf-8",
      cwd: getProjectRoot(),
    });
    const files = result.trim();
    if (!files) return [];

    // Filter for TypeScript/JavaScript files that tsc may check (excluding .d.ts, .d.mts, .d.cts)
    // Supports: .ts, .tsx, .mts, .cts, .js, .jsx, .mjs, .cjs
    return files
      .split(/\r?\n/)
      .filter((f) => /\.[cm]?[tj]sx?$/.test(f) && !/\.d\.[cm]?ts$/.test(f));
  } catch (error) {
    console.error(`\u26a0\ufe0f  git diff failed: ${error}`);
    return null;
  }
}

/**
 * Typecheck configurations with their corresponding subproject paths.
 * Each config pairs a typecheck command with its subproject directory prefix.
 * tsc outputs paths relative to the subproject, so we prepend the prefix for root-relative paths.
 *
 * Running commands separately ensures all subprojects are verified even if one fails.
 * (pnpm typecheck uses && which stops on first failure)
 */
const TYPECHECK_CONFIGS = [
  { command: "pnpm typecheck:frontend", prefix: "frontend" },
  { command: "pnpm typecheck:worker", prefix: "worker" },
];

/**
 * Result from getTypecheckErrors including both parsed errors and failure status.
 */
export interface TypecheckResult {
  errors: TypecheckError[];
  /** True if any typecheck command failed (non-zero exit) */
  hadFailure: boolean;
}

/**
 * Run tsc and parse errors from output.
 *
 * @returns TypecheckResult with parsed errors and failure status.
 *          hadFailure is true if any typecheck command exited non-zero,
 *          even if no specific errors were parsed (fail-secure).
 */
export function getTypecheckErrors(): TypecheckResult {
  const projectRoot = getProjectRoot();
  const errors: TypecheckError[] = [];
  let hadFailure = false;

  // Run each typecheck command independently to ensure all subprojects are verified
  // even if one has errors (fixes issue where frontend errors would skip worker check)
  for (const config of TYPECHECK_CONFIGS) {
    const result = spawnSync(config.command, {
      encoding: "utf-8",
      cwd: projectRoot,
      shell: true,
      env: { ...process.env, FORCE_COLOR: "0" },
    });

    // Handle spawn errors (e.g., pnpm not found)
    if (result.error) {
      console.error(`Failed to spawn ${config.command}: ${result.error.message}`);
      process.exit(1);
    }

    // Skip parsing if no errors
    if (result.status === 0) {
      continue;
    }

    // Mark that we had a failure (for fail-secure behavior)
    hadFailure = true;

    const output = result.stdout + result.stderr;
    // Pass the subproject prefix for explicit path construction
    const currentErrors = parseTypecheckOutput(output, config.prefix);

    // If no errors were parsed but command failed, show raw output for debugging
    if (currentErrors.length === 0) {
      console.error(
        `\n\u274c  Command '${config.command}' failed with exit code ${result.status} but no parseable errors were found.`,
      );
      console.error(`Raw output:\n${output}\n`);
    }

    errors.push(...currentErrors);
  }

  return { errors, hadFailure };
}

/**
 * Parse tsc output and extract error information.
 *
 * @param output - Combined stdout and stderr from tsc
 * @param subprojectPrefix - Subproject directory prefix (e.g., "frontend", "worker")
 * @returns List of TypecheckError objects
 */
function parseTypecheckOutput(output: string, subprojectPrefix: string): TypecheckError[] {
  const errors: TypecheckError[] = [];

  // Parse tsc output format: path/to/file.ts(line,column): error TSxxxx: message
  // Also handles format without parentheses: path/to/file.ts:line:column - error TSxxxx: message
  // Note: pnpm may prefix lines with package names (e.g., "@dekita/frontend: src/main.tsx...")
  // The regex allows for optional non-path prefixes before the filename.
  const lines = output.split(/\r?\n/);

  const projectRoot = getProjectRoot();

  // Helper to convert raw path to root-relative path, handling:
  // 1. Absolute paths (e.g., /repo/frontend/src/main.tsx)
  // 2. Already-prefixed paths (e.g., frontend/src/main.tsx)
  // 3. Subproject-relative paths (e.g., src/main.tsx)
  // 4. Parent references (e.g., ../shared/types.ts)
  const toRootRelativePath = (rawPath: string): string => {
    const normalized =
      isAbsolute(rawPath) || rawPath.startsWith(`${subprojectPrefix}/`)
        ? rawPath
        : `${subprojectPrefix}/${rawPath}`;
    const absPath = isAbsolute(normalized) ? normalized : resolve(projectRoot, normalized);
    // Normalize to POSIX separators for consistent matching with git diff output
    return relative(projectRoot, absPath).replaceAll("\\", "/");
  };

  for (const line of lines) {
    // Format 1: file.ts(line,column): error TSxxxx: message
    // Allow optional prefix (package name) before the path
    // Pattern: [cm]?[tj]sx? matches .ts, .tsx, .mts, .cts, .js, .jsx, .mjs, .cjs
    // Note: [^\s(]+ excludes spaces to avoid capturing pnpm package name prefixes
    const match1 = line.match(
      /(?:^|\s)([^\s(]+\.[cm]?[tj]sx?)\((\d+),(\d+)\):\s*error\s+(TS\d+):\s*(.+)$/,
    );
    if (match1) {
      const fullPath = toRootRelativePath(match1[1]);
      errors.push({
        file: fullPath,
        line: Number.parseInt(match1[2], 10),
        column: Number.parseInt(match1[3], 10),
        code: match1[4],
        message: match1[5],
      });
      continue;
    }

    // Format 2: file.ts:line:column - error TSxxxx: message
    // Allow optional prefix (package name) before the path
    // Pattern: [cm]?[tj]sx? matches .ts, .tsx, .mts, .cts, .js, .jsx, .mjs, .cjs
    // Note: [^\s:]+ excludes spaces and colons to avoid capturing pnpm package name prefixes
    const match2 = line.match(
      /(?:^|\s)([^\s:]+\.[cm]?[tj]sx?):(\d+):(\d+)\s*-\s*error\s+(TS\d+):\s*(.+)$/,
    );
    if (match2) {
      const fullPath = toRootRelativePath(match2[1]);
      errors.push({
        file: fullPath,
        line: Number.parseInt(match2[2], 10),
        column: Number.parseInt(match2[3], 10),
        code: match2[4],
        message: match2[5],
      });
    }
  }

  return errors;
}

/**
 * Check result structure
 */
export interface CheckResult {
  newFileErrors: TypecheckError[];
  existingFileErrors: TypecheckError[];
  unchangedFileErrors: TypecheckError[];
  /** True if any typecheck command failed (non-zero exit), even if no errors were parsed */
  hadFailure: boolean;
}

/**
 * Check type errors and categorize by file status (new/existing/unchanged).
 */
export function checkTypeErrors(): CheckResult {
  const newFiles = getNewFiles();
  const changedFiles = getChangedFiles();

  // If git diff failed, we cannot safely determine new files.
  // Fail secure (blocking) instead of fail open to prevent broken code from merging.
  if (newFiles === null || changedFiles === null) {
    console.error("\u274c Could not determine new files (git diff failed). Aborting check.");
    process.exit(1);
  }

  // Get all type errors
  const { errors, hadFailure } = getTypecheckErrors();

  const newFileSet = new Set(newFiles);
  const changedFileSet = new Set(changedFiles);

  const newFileErrors: TypecheckError[] = [];
  const existingFileErrors: TypecheckError[] = [];
  const unchangedFileErrors: TypecheckError[] = [];

  for (const error of errors) {
    if (newFileSet.has(error.file)) {
      newFileErrors.push(error);
    } else if (changedFileSet.has(error.file)) {
      existingFileErrors.push(error);
    } else {
      unchangedFileErrors.push(error);
    }
  }

  return { newFileErrors, existingFileErrors, unchangedFileErrors, hadFailure };
}

/**
 * Format error for display
 */
function formatError(error: TypecheckError): string {
  return `  ${error.file}:${error.line}:${error.column} - ${error.code}: ${error.message}`;
}

/**
 * Main entry point
 *
 * @param strictMode - If true, all type errors block (any error causes exit 1).
 *                     If false, only new file errors block (default, gradual enforcement).
 */
function main(strictMode = false): number {
  if (strictMode) {
    console.log("Checking TypeScript type errors (strict mode)...\n");
  } else {
    console.log("Checking TypeScript type errors for new files...\n");
  }

  const { newFileErrors, existingFileErrors, unchangedFileErrors, hadFailure } = checkTypeErrors();

  let hasBlockingErrors = false;

  // Report new file errors (always blocking)
  if (newFileErrors.length > 0) {
    console.log(`\u274c  New files with type errors (${newFileErrors.length} error(s)):`);
    for (const error of newFileErrors) {
      console.log(formatError(error));
    }
    console.log("\nNew files must not have type errors. Please fix them before committing.\n");
    hasBlockingErrors = true;
  }

  // Report existing (changed) file errors
  if (existingFileErrors.length > 0) {
    const severity = strictMode ? "\u274c " : "\u26a0\ufe0f ";
    const suffix = strictMode ? "" : ", warning only";
    console.log(
      `${severity} Changed files with type errors (${existingFileErrors.length} error(s)${suffix}):`,
    );
    for (const error of existingFileErrors) {
      console.log(formatError(error));
    }
    if (strictMode) {
      console.log("\nChanged files must not have type errors. Please fix them.\n");
      hasBlockingErrors = true;
    } else {
      console.log("\nConsider fixing these errors to improve code quality.\n");
    }
  }

  // Report unchanged file errors
  if (unchangedFileErrors.length > 0) {
    const severity = strictMode ? "\u274c " : "\u2139\ufe0f ";
    const suffix = strictMode ? "" : ", pre-existing";
    console.log(
      `${severity} Unchanged files with type errors (${unchangedFileErrors.length} error(s)${suffix}):`,
    );
    if (strictMode) {
      // In strict mode, show full error details since they block the build
      for (const error of unchangedFileErrors) {
        console.log(formatError(error));
      }
      console.log("\nType errors must be fixed before merging.\n");
      hasBlockingErrors = true;
    } else {
      // Only show count, not details, for unchanged files in gradual mode
      const uniqueFiles = [...new Set(unchangedFileErrors.map((e) => e.file))];
      const MAX_FILES_DISPLAY = 10;
      const displayFiles =
        uniqueFiles.length > MAX_FILES_DISPLAY
          ? `${uniqueFiles.slice(0, MAX_FILES_DISPLAY).join(", ")} and ${uniqueFiles.length - MAX_FILES_DISPLAY} more`
          : uniqueFiles.join(", ");
      console.log(`   Files affected: ${displayFiles}`);
      console.log("\nThese are pre-existing errors not introduced by this PR.\n");
    }
  }

  // Fail-secure: If tsc failed but we couldn't parse specific errors, fail in ALL modes.
  // This indicates a crash or unparseable output, so we cannot verify safety.
  const anyErrorsFound =
    newFileErrors.length > 0 || existingFileErrors.length > 0 || unchangedFileErrors.length > 0;
  if (hadFailure && !anyErrorsFound) {
    console.log("\u274c  Typecheck command failed but no specific errors were parsed.");
    console.log("This may indicate an unexpected tsc output format. Failing to be safe.\n");
    hasBlockingErrors = true;
  }

  // Summary
  const totalErrors = newFileErrors.length + existingFileErrors.length + unchangedFileErrors.length;
  if (totalErrors === 0 && !hadFailure) {
    console.log("\u2705 No type errors found");
  } else {
    if (strictMode) {
      console.log(`Summary: ${totalErrors} error(s) total - all must be fixed`);
    } else {
      console.log(
        `Summary: ${newFileErrors.length} blocking, ${existingFileErrors.length} warning, ${unchangedFileErrors.length} pre-existing`,
      );
    }
  }

  return hasBlockingErrors ? 1 : 0;
}

// Execute
if (import.meta.main) {
  const strictMode = process.argv.includes("--strict");
  const exitCode = main(strictMode);
  process.exit(exitCode);
}

// Export for testing
export { formatError, main };
