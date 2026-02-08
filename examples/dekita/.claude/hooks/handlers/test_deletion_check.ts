#!/usr/bin/env bun
/**
 * Python関数/クラス削除時のテスト参照漏れを検出。
 *
 * Why:
 *   関数やクラスを削除しても、テストファイルに参照が残っているとCIで失敗する。
 *   コミット前に参照漏れを検出してブロックすることで、CI失敗を防止する。
 *
 * What:
 *   - git diffでステージ済みの関数/クラス削除を検出
 *   - 削除されたシンボルへのテストファイル参照をgit grepで検索
 *   - 参照が残っている場合はコミットをブロック
 *
 * Remarks:
 *   - pre-commitフックとして使用
 *   - リファクタリング（同名追加）や移動は除外
 *   - プライベート関数（_prefix）はスキップ
 *   - 削除元モジュールからインポートしているテストのみ対象（Issue #1958）
 *
 * Changelog:
 *   - silenvx/dekita#1868: フック追加
 *   - silenvx/dekita#1915: 別ファイルへの移動検出追加
 *   - silenvx/dekita#1958: インポート元モジュールフィルタリング追加
 *   - silenvx/dekita#3161: TypeScript移行
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { basename, dirname, resolve } from "node:path";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { asyncSpawn } from "../lib/spawn";

const HOOK_NAME = "test-deletion-check";

/**
 * Issue #3614: Skip checking when too many symbols are deleted.
 * Large deletions (e.g., legacy hook migration) would cause massive false positives.
 * In such cases, show a warning but don't block.
 */
const LARGE_DELETION_THRESHOLD = 10;

interface DeletedSymbol {
  type: "function" | "class";
  name: string;
  sourceFile: string;
}

interface Reference {
  filePath: string;
  lineNumber: number;
  content: string;
}

interface Issue {
  symbolType: "function" | "class";
  symbolName: string;
  sourceFile: string;
  references: Reference[];
}

/**
 * Get staged diff.
 */
async function getStagedDiff(): Promise<string> {
  try {
    // Note: --find-renames is intentionally not specified.
    // Function/class renames are treated as "deletion of old name".
    // Tests with old name references should be blocked (Issue #1868).
    const result = await asyncSpawn("git", ["diff", "--cached", "-U0"], {
      timeout: TIMEOUT_MEDIUM * 1000,
    });

    if (!result.success) {
      return "";
    }
    return result.stdout;
  } catch (e) {
    if (e instanceof Error && e.message.includes("timeout")) {
      console.error(`[${HOOK_NAME}] 警告: git diffがタイムアウトしました`);
    } else {
      console.error(`[${HOOK_NAME}] 警告: git diff実行エラー: ${formatError(e)}`);
    }
    return "";
  }
}

/**
 * Extract deleted symbols from diff.
 * Handles refactoring (same name added) and moves to other files.
 */
function extractDeletedSymbols(diffContent: string): DeletedSymbol[] {
  const deletedSymbols: DeletedSymbol[] = [];
  // Symbol names without file path (type, name) to detect moves
  const addedSymbolNames = new Set<string>();
  let currentFile = "";
  // Issue #3263: Track whether current file is Python to avoid false positives
  // when non-Python file diffs (e.g., .ts) follow Python files
  let isPythonFile = false;

  // Pattern to extract file path from diff header (any file type)
  // Issue #3263: Match both --- and +++ lines to handle new/renamed files
  // For new files, --- is /dev/null and +++ contains the actual path
  const filePattern = /^[+-]{3} (?:[ab]\/)?(.+)$/;

  // Deletion line patterns
  const funcDelPattern = /^-\s*(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(/;
  const classDelPattern = /^-\s*class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[:\(]/;

  // Addition line patterns
  const funcAddPattern = /^\+\s*(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(/;
  const classAddPattern = /^\+\s*class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[:\(]/;

  const seenDeleted = new Set<string>();

  for (const line of diffContent.split("\n")) {
    // Track current file
    const fileMatch = line.match(filePattern);
    if (fileMatch) {
      const matchedPath = fileMatch[1];
      // Issue #3263: Skip /dev/null to avoid losing deleted file's path
      // For deleted files: --- a/path/to/file.py then +++ /dev/null
      // We want to keep the original path, not overwrite with /dev/null
      if (matchedPath !== "/dev/null") {
        currentFile = matchedPath;
        // Issue #3263: Only process Python files to avoid false positives
        isPythonFile = currentFile.endsWith(".py");
      }
      continue;
    }

    // Issue #3263: Skip non-Python files entirely
    if (!isPythonFile) {
      continue;
    }

    // Ignore test file changes
    if (currentFile?.includes("tests/")) {
      continue;
    }

    // Record added functions/classes (for refactoring/move detection)
    // Record without file path so we can detect moves to other files
    const funcAddMatch = line.match(funcAddPattern);
    if (funcAddMatch && currentFile) {
      addedSymbolNames.add(`function:${funcAddMatch[1]}`);
      continue;
    }

    const classAddMatch = line.match(classAddPattern);
    if (classAddMatch && currentFile) {
      addedSymbolNames.add(`class:${classAddMatch[1]}`);
      continue;
    }

    // Check for deleted functions
    const funcDelMatch = line.match(funcDelPattern);
    if (funcDelMatch && currentFile) {
      const key = `function:${funcDelMatch[1]}:${currentFile}`;
      if (!seenDeleted.has(key)) {
        deletedSymbols.push({
          type: "function",
          name: funcDelMatch[1],
          sourceFile: currentFile,
        });
        seenDeleted.add(key);
      }
      continue;
    }

    // Check for deleted classes
    const classDelMatch = line.match(classDelPattern);
    if (classDelMatch && currentFile) {
      const key = `class:${classDelMatch[1]}:${currentFile}`;
      if (!seenDeleted.has(key)) {
        deletedSymbols.push({
          type: "class",
          name: classDelMatch[1],
          sourceFile: currentFile,
        });
        seenDeleted.add(key);
      }
    }
  }

  // Exclude refactoring or moves (same name symbol added elsewhere)
  return deletedSymbols.filter((s) => !addedSymbolNames.has(`${s.type}:${s.name}`));
}

/**
 * Get module name from file path.
 */
function getModuleNameFromFile(filePath: string): string {
  const base = basename(filePath);
  if (base.endsWith(".py")) {
    return base.slice(0, -3);
  }
  return base;
}

/**
 * Common function names that may appear across many modules.
 * Issue #3356: These names require additional verification via qualified call pattern.
 */
const COMMON_FUNCTION_NAMES = new Set([
  "main",
  "setup",
  "teardown",
  "run",
  "init",
  "start",
  "stop",
  "close",
  "cleanup",
  "process",
  "handle",
  "execute",
]);

/**
 * Get file content from staged area or working tree.
 * Returns null if file cannot be read.
 */
async function getFileContent(filePath: string): Promise<string | null> {
  try {
    const result = await asyncSpawn("git", ["show", `:${filePath}`], {
      timeout: TIMEOUT_MEDIUM * 1000,
    });

    if (result.success) {
      return result.stdout;
    }

    // File not staged, read directly
    try {
      return readFileSync(filePath, "utf-8");
    } catch {
      return null;
    }
  } catch {
    return null;
  }
}

/**
 * Escape special regex characters.
 * @exported for testing
 */
export function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Normalize module name (hyphens to underscores).
 * @exported for testing
 */
export function normalizeModuleName(moduleName: string): string {
  return moduleName.replace(/-/g, "_");
}

/**
 * Check if content imports from specified module.
 *
 * Note: This function checks both Python-style imports (from/import statements)
 * and TypeScript-style imports (import/export ... from "module").
 * TypeScript support was added in Issue #3749 to detect TypeScript test files
 * that reference the specified module.
 * @exported for testing
 */
export function checkContentImportsFromModule(content: string, moduleName: string): boolean {
  const escaped = escapeRegex(normalizeModuleName(moduleName));
  // Issue #3161: Support qualified imports (e.g., "from src.module import" or "import src.module")
  const importRegex = new RegExp(
    `^\\s*(from\\s+(?:[\\w.]+\\.)?${escaped}\\s+import\\b|import\\s+(?:[\\w.]+\\.)?${escaped}\\b(?:\\s+as\\b)?)`,
    "m",
  );
  const assignRegex = new RegExp(`^\\s*${escaped}\\s*=`, "m");
  // TypeScript: import { ... } from "module", export { ... } from "module"
  // Supports multiline imports, hyphenated module names, matched quotes, and submodule paths
  // Note: dynamic imports (import("module")) are not covered; see Issue #3749 for scope
  const escapedRaw = escapeRegex(moduleName);
  const modulePath = `(['"])(?:[^'"]*[/])?(?:${escaped}|${escapedRaw})(?:(?:\\.[a-zA-Z0-9]+)+|/[^'"]*)?\\1`;
  const tsImportRegex = new RegExp(`^\\s*import\\s+[^'"]*?${modulePath}`, "m");
  const tsReExportRegex = new RegExp(`^\\s*export\\s+[^'"]*?\\bfrom\\s+${modulePath}`, "m");

  return (
    importRegex.test(content) ||
    tsImportRegex.test(content) ||
    tsReExportRegex.test(content) ||
    assignRegex.test(content)
  );
}

/**
 * Resolve a module import path to an actual file path.
 * Tries .ts, .tsx, .js, .jsx extensions and index files.
 * @exported for testing
 */
export function resolveModulePath(importPath: string, sourceFilePath: string): string | null {
  const dir = dirname(sourceFilePath);
  const base = resolve(dir, importPath);

  // 1. Try with multiple extensions
  const extensions = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"];
  for (const ext of extensions) {
    const candidate = base + ext;
    if (existsSync(candidate)) return candidate;
  }
  // 2. Try index files (directory imports)
  for (const indexFile of [
    "index.ts",
    "index.tsx",
    "index.js",
    "index.jsx",
    "index.mjs",
    "index.cjs",
    "index.mts",
    "index.cts",
  ]) {
    const candidate = resolve(base, indexFile);
    if (existsSync(candidate)) return candidate;
  }
  // 3. Handle explicit extensions (e.g., "./utils.ts") - checked last to avoid
  //    returning a directory path which would cause EISDIR in readFileSync
  if (existsSync(base)) {
    try {
      if (statSync(base).isFile()) return base;
    } catch {
      return null;
    }
  }

  return null;
}

/**
 * Check if a module file exports a specific symbol.
 * Detects: export function/const/class/type sym, export { sym }, export { sym } from.
 * Returns true on read failure (fail-open).
 * @exported for testing
 */
export function checkModuleExportsSymbol(modulePath: string, symbolName: string): boolean {
  let content: string;
  try {
    content = readFileSync(modulePath, "utf-8");
  } catch {
    return true; // fail-open
  }
  // Strip comments (handle strings first to avoid corrupting string content containing /*)
  const cleanContent = content.replace(
    /("[^"\\]*(?:\\.[^"\\]*)*"|'[^'\\]*(?:\\.[^'\\]*)*'|`[^`\\]*(?:\\.[^`\\]*)*`)|\/\/[^\n]*|\/\*[\s\S]*?\*\//g,
    (_match, str) => (str ? str : ""),
  );

  const escaped = escapeRegex(symbolName);

  // export default (for namespace.default access)
  if (symbolName === "default" && /^\s*export\s+default\b/m.test(cleanContent)) {
    return true;
  }
  // export function/const/class/type/interface/enum sym
  if (
    new RegExp(
      `^\\s*export\\s+(?:declare\\s+)?(?:async\\s+)?(?:abstract\\s+)?(?:function\\*?|const|let|var|class|type|interface|enum|namespace)\\s+${escaped}(?![a-zA-Z0-9_$])`,
      "m",
    ).test(cleanContent)
  ) {
    return true;
  }
  // Fallback: export const/let/var with destructuring or multiple declarations (fail-open)
  if (
    new RegExp(
      `^\\s*export\\s+(?:const|let|var)\\s+.*(?<![a-zA-Z0-9_$])${escaped}(?![a-zA-Z0-9_$])`,
      "m",
    ).test(cleanContent)
  ) {
    return true;
  }
  // export { sym } or export { ..., sym, ... } or export { sym } from
  if (
    new RegExp(
      `^\\s*export(?:\\s+type)?\\s*\\{[^}]*(?:(?<!\\bas\\s+)(?<![a-zA-Z0-9_$])${escaped}(?![a-zA-Z0-9_$])(?!\\s+as\\b)|\\bas\\s+${escaped}(?![a-zA-Z0-9_$]))[^}]*\\}`,
      "m",
    ).test(cleanContent)
  ) {
    return true;
  }
  // export * from (re-exports everything, symbol could exist)
  if (/^\s*export\s*\*\s*from\s/m.test(cleanContent)) {
    return true; // fail-open: can't resolve transitively
  }
  // export * as Name from "..." (namespace re-export)
  if (new RegExp(`^\\s*export\\s*\\*\\s*as\\s+${escaped}\\s+from\\s`, "m").test(cleanContent)) {
    return true;
  }
  return false;
}

/**
 * Check if content contains qualified calls to a function from a specific module.
 * Issue #3356: For common function names, verify via module.function() pattern.
 * Issue #3401: Added TypeScript named import and dynamic import support.
 *
 * Checks:
 * 1) module.function (Python/TS)
 * 2) from module import function (Python)
 * 3) import module as alias (Python)
 * 4) import { function } from "module" (TypeScript)
 * 5) Dynamic import: importlib.import_module("module") (Python)
 * @exported for testing
 */
export function checkContentQualifiedCallPattern(
  content: string,
  moduleName: string,
  symbolName: string,
  sourcePath?: string,
): boolean {
  const escapedModule = escapeRegex(normalizeModuleName(moduleName));
  const escapedSymbol = escapeRegex(symbolName);

  // 1. Direct qualified usage: module.function or module.function()
  if (new RegExp(`\\b${escapedModule}\\.${escapedSymbol}\\b`, "m").test(content)) {
    return true;
  }

  // 2. Explicit import: from module import function (Python)
  // Simplified: match import line(s) containing the symbol
  // Issue #3402: Use [^#\\n]* to exclude Python comments
  const explicitImportRegex = new RegExp(
    `^\\s*from\\s+(?:[\\w.]+\\.)?${escapedModule}\\s+import\\s+[^#\\n]*\\b${escapedSymbol}\\b`,
    "m",
  );
  if (explicitImportRegex.test(content)) {
    return true;
  }

  // 2b. Multiline explicit import: from module import (\n    func1,\n    func2,\n) (Python)
  // Issue #3406: Support parenthesized multiline imports (formatted by Black, etc.)
  // Gemini review: Strip all strings/docstrings/comments to avoid false positives
  // Codex review: Handle prefixed strings (r"...", f"...", etc.) as well
  const contentNoStringsOrComments = content.replace(
    /[rRbBuUfF]{0,2}("""[\s\S]*?"""|'''[\s\S]*?''')|[rRbBuUfF]{0,2}("[^"\\]*(?:\\.[^"\\]*)*"|'[^'\\]*(?:\\.[^'\\]*)*')|#[^\n]*/g,
    () => "",
  );
  // Match all "from module import (...)" blocks using matchAll to handle multiple imports
  const multilineImportRegex = new RegExp(
    `^\\s*from\\s+(?:[\\w.]+\\.)?${escapedModule}\\s+import\\s*\\(([^)]+)\\)`,
    "gm",
  );
  // Copilot review: Create RegExp outside loop for performance
  const symbolRegex = new RegExp(`\\b${escapedSymbol}\\b`);
  for (const multilineImportMatch of contentNoStringsOrComments.matchAll(multilineImportRegex)) {
    if (symbolRegex.test(multilineImportMatch[1])) {
      return true;
    }
  }

  // 3. Aliased module usage: import module as alias -> alias.function (Python)
  const aliasImportRegex = new RegExp(
    `^\\s*import\\s+(?:[\\w.]+\\.)?${escapedModule}\\s+as\\s+(\\w+)`,
    "gm",
  );
  for (const aliasMatch of content.matchAll(aliasImportRegex)) {
    const escapedAlias = escapeRegex(aliasMatch[1]);
    if (new RegExp(`\\b${escapedAlias}\\.${escapedSymbol}\\b`, "m").test(content)) {
      return true;
    }
  }

  // 4. TypeScript named import: import { symbol } from "module" or import { symbol } from './module'
  // Also supports: import type { symbol } from "module"
  // Issue #3401: Support TypeScript import syntax for .claude/hooks/tests
  // Note: This detects TS test files importing Python modules (e.g., import { main } from "./merge_check")
  //       The moduleName comes from getModuleNameFromFile which strips .py extension for Python files.
  //       TypeScript file deletion detection is not supported (getTestDirsForSourceFile returns []).
  // Codex review: Anchor module name to path segment boundary to avoid substring matches
  // (e.g., "os" should not match "./cosmos")
  // Match: "module", "./module", "../module", "/path/to/module", "./module.js"
  // Pattern: module name must be preceded by quote, slash, or nothing after from clause
  // Gemini review: Use backreference to ensure matching quotes (not mixed)
  // Gemini review: Support "import type { ... }" syntax
  // Gemini review: Allow optional file extension (e.g. .js, .ts)
  // Gemini review: Use raw module name for TS imports (preserve hyphens/kebab-case)
  // Gemini review: Remove comments to avoid false positives
  // Gemini review: Preserve strings containing // (e.g., URLs like "https://example.com")
  const tsCleanContent = content
    .replace(/\/\*[\s\S]*?\*\//g, "") // Remove block comments
    .replace(
      /("[^"\\]*(?:\\.[^"\\]*)*"|'[^'\\]*(?:\\.[^'\\]*)*'|`[^`\\]*(?:\\.[^`\\]*)*`)|\/\/[^\n]*/g,
      (_match, str) => (str ? str : ""),
    ); // Remove single-line comments but preserve strings
  const escapedRawModule = escapeRegex(moduleName);
  // Issue #3712: Support both import and re-export patterns
  // - import { symbol } from "module"
  // - export { symbol } from "module"
  // - import type { symbol } from "module"
  // - export type { symbol } from "module"
  // Gemini review: Use negative lookbehind to exclude alias targets (e.g., { other as symbol })
  // We want to match { symbol } or { symbol as alias }, but NOT { other as symbol }
  // Use \s+ to handle multiple spaces/newlines between "as" and the alias
  // Issue #3733: Use \s* around "from" and after import/export to support minified code
  // Note: import/type requires \s+ between them (identifier boundary), but type/{ can have \s*
  const tsNamedImportRegex = new RegExp(
    `^\\s*(?:import|export)(?:\\s+type\\s*)?\\s*\\{[^}]*(?<!\\bas\\s+)\\b${escapedSymbol}\\b[^}]*\\}\\s*from\\s*(['"])(?:[^'"]*[/])?${escapedRawModule}(?:\\.[a-zA-Z0-9]+)?\\1`,
    "m",
  );
  if (tsNamedImportRegex.test(tsCleanContent)) {
    return true;
  }

  // 4c. TypeScript default + named import: import defaultExport, { symbol } from "module"
  // Issue #3712: Support combined default and named imports
  // Issue #3728: Also detect when the deleted symbol is the default import itself
  // Issue #3755: Support "import type" syntax for default imports
  // Gemini review: Use negative lookbehind to exclude alias targets (use \s+ for multiple spaces)
  // Pattern 1: import symbol, { ... } - symbol is the default import (Issue #3728)
  //   - デフォルト位置 (symbol) のみを検出対象とし、波括弧内の named imports は有無だけを確認する
  //   - そのため `{[^}]*}` には負の後読みを入れておらず、named imports 内の `as` エイリアスターゲットとの衝突は検証しない仕様
  // Pattern 2: import xxx, { symbol } - symbol is in named imports (Issue #3712)
  //   - こちらは named imports 内の symbol を検出するため、`(?<!\\bas\\s+)` で alias ターゲットを除外している
  // Pattern 3: import symbol from "module" - symbol is solo default import (Gemini review)
  //   - named imports なしのデフォルトインポートを検出
  // Issue #3731: Use [a-zA-Z_$][\\w$]* to support $ in default import names
  // Issue #3733: Use \s* around "from" to support minified code
  const tsDefaultNamedImportRegex = new RegExp(
    `^\\s*import\\s+(?:type\\s+)?(?:\\b${escapedSymbol}\\b\\s*,\\s*\\{[^}]*\\}|[a-zA-Z_$][\\w$]*\\s*,\\s*\\{[^}]*(?<!\\bas\\s+)\\b${escapedSymbol}\\b[^}]*\\}|\\b${escapedSymbol}\\b)\\s*from\\s*(['"])(?:[^'"]*[/])?${escapedRawModule}(?:\\.[a-zA-Z0-9]+)?\\1`,
    "m",
  );
  if (tsDefaultNamedImportRegex.test(tsCleanContent)) {
    return true;
  }

  // 4b. TypeScript Namespace import/export: import [type] * as alias from "module" or export [type] * as alias from "module"
  // Gemini review: Detect usage via namespace alias (e.g., Utils.deletedSymbol())
  // Issue #3732: Use stricter pattern to match $ in alias names (e.g., $lib)
  // Gemini review: Use [a-zA-Z_$][\w$]* to enforce valid identifier start
  // Issue #3733: Use \s* around "from" and between import/*/as to support minified code
  // Issue #3764: Support export * as syntax in addition to import * as
  // Gemini review: Support import type * as syntax (TypeScript 3.8+)
  // Gemini review: Support export type * as syntax (TypeScript 5.0+)
  // Note: alias and "from" must have \s+ between them (identifier boundary required, unchanged)
  const tsNamespaceImportRegex = new RegExp(
    `^\\s*(?:import|export)(?:\\s+type\\s*)?\\s*\\*\\s*as\\s+([a-zA-Z_$][\\w$]*)\\s+from\\s*(['"])(?:[^'"]*[/])?${escapedRawModule}(?:\\.[a-zA-Z0-9]+)?\\2`,
    "gm",
  );
  for (const match of tsCleanContent.matchAll(tsNamespaceImportRegex)) {
    // Gemini/Codex review: export * as does NOT create a local binding (it's a re-export).
    // If the module is re-exported, consider the symbol as "used" (exposed in public API).
    // Checking for Alias.symbol usage would be semantically invalid for exports.
    if (match[0].trim().startsWith("export")) {
      if (!sourcePath) return true; // fail-open
      const fromMatch = match[0].match(/from\s*(['"])(.+?)\1/);
      if (!fromMatch) return true; // fail-open
      const resolved = resolveModulePath(fromMatch[2], sourcePath);
      if (!resolved) return true; // fail-open
      if (checkModuleExportsSymbol(resolved, symbolName)) {
        return true;
      }
      continue;
    }

    // Issue #3732: Escape alias to handle special regex characters (e.g., $)
    // Note: \b doesn't work correctly with $ (it's not a word character),
    // so we use a custom word boundary pattern that explicitly handles $
    // Copilot review: Use negative lookbehind for consistency with other patterns
    const escapedAlias = escapeRegex(match[1]);
    if (
      new RegExp(`(?<![a-zA-Z0-9_$])${escapedAlias}\\.${escapedSymbol}\\b`, "m").test(
        tsCleanContent,
      )
    ) {
      return true;
    }
  }

  // 5. Dynamic import: importlib.import_module("module") (Python)
  // Issue #3401: Check for dynamic imports after removing comments
  // Note: Uses escapedModule (snake_case) to match other Python patterns (1-3).
  //       Kebab-case modules (my-module.py) are rare in Python and not supported
  //       by standard import statements, so we use normalized names here.
  // Codex review: Match importlib.import_module("module") specifically to avoid false positives
  // Gemini review: Use backreference for consistent quote matching
  // Gemini review: Robustly handle strings containing '#' to avoid stripping valid code
  // Gemini review: Handle triple-quoted strings (docstrings) - remove them as they are often used as comments
  // Remove Python comments and docstrings, preserve regular strings containing '#'
  const cleanContent = content.replace(
    /("""[\s\S]*?"""|'''[\s\S]*?''')|("[^"\\]*(?:\\.[^"\\]*)*"|'[^'\\]*(?:\\.[^'\\]*)*')|#[^\n]*/g,
    (_match, tripleQuote, str) => {
      if (tripleQuote) return ""; // Remove docstrings
      if (str) return str; // Preserve regular strings
      return ""; // Remove comments
    },
  );
  // Match: importlib.import_module("module") or importlib.import_module('module')
  // Gemini review: Also match qualified paths like importlib.import_module("pkg.module")
  const dynamicImportRegex = new RegExp(
    `importlib\\.import_module\\s*\\(\\s*(['"])(?:[\\w.]+\\.)?${escapedModule}\\1\\s*\\)`,
  );
  if (dynamicImportRegex.test(cleanContent)) {
    return true;
  }

  return false;
}

/**
 * Get appropriate test directories based on source file type.
 * Issue #3614: Since extractDeletedSymbols only processes Python files,
 * this function only returns Python test directories.
 * TypeScript support can be added when extractDeletedSymbols is updated.
 */
function getTestDirsForSourceFile(sourceFile: string): string[] {
  if (sourceFile.endsWith(".py")) {
    // Issue #3614: Include both hooks tests and scripts tests
    return [".claude/hooks/tests", ".claude/scripts/tests"];
  }
  // Return empty array for unsupported file types to skip checking
  return [];
}

/**
 * Find test references to a symbol.
 * Issue #3614: Only searches tests that match the source file language.
 */
async function findTestReferences(
  symbolName: string,
  sourceFile: string,
  testDirs?: string[],
): Promise<Reference[]> {
  // Use language-appropriate test directories if not specified
  const effectiveTestDirs = testDirs ?? getTestDirsForSourceFile(sourceFile);
  // Issue #3614: Skip if no test directories (e.g., unsupported file types)
  if (effectiveTestDirs.length === 0) {
    return [];
  }
  const references: Reference[] = [];
  const moduleName = getModuleNameFromFile(sourceFile);

  try {
    // Use git grep for fast search (tracked files only)
    // -w for word boundary to prevent partial matches
    // Issue #3161: Use --cached to check staged content, not working tree
    // Issue #3614: Use language-appropriate test directories
    const testDirArgs = effectiveTestDirs.map((dir) => `${dir}/`);
    const result = await asyncSpawn(
      "git",
      ["grep", "--cached", "-n", "-w", symbolName, "--", ...testDirArgs],
      {
        timeout: TIMEOUT_MEDIUM * 1000,
      },
    );

    // git grep returns 0 for match, 1 for no match
    if (result.success && result.stdout.trim()) {
      // Group by file
      const filesWithRefs: Map<string, Array<{ lineNum: number; content: string }>> = new Map();

      for (const line of result.stdout.trim().split("\n")) {
        // Format: file:line_number:content
        const parts = line.split(":", 3);
        if (parts.length >= 3) {
          const filePath = parts[0];
          const lineNum = Number.parseInt(parts[1], 10);
          if (Number.isNaN(lineNum)) {
            continue;
          }
          const content = parts[2];

          if (!filesWithRefs.has(filePath)) {
            filesWithRefs.set(filePath, []);
          }
          filesWithRefs.get(filePath)!.push({ lineNum, content });
        }
      }

      // Only include files that import from the deleted module
      // Issue #3356: For common function names, also verify via qualified call pattern
      const isCommonName = COMMON_FUNCTION_NAMES.has(symbolName.toLowerCase());

      for (const [filePath, refs] of filesWithRefs) {
        const fileContent = await getFileContent(filePath);

        // Fail open: if we can't read, include all references
        const shouldInclude =
          fileContent === null ||
          (checkContentImportsFromModule(fileContent, moduleName) &&
            (!isCommonName ||
              checkContentQualifiedCallPattern(fileContent, moduleName, symbolName, filePath)));

        if (shouldInclude) {
          for (const { lineNum, content } of refs) {
            references.push({ filePath, lineNumber: lineNum, content });
          }
        }
      }
    }
  } catch (e) {
    if (e instanceof Error && e.message.includes("timeout")) {
      console.error(`[${HOOK_NAME}] 警告: git grepがタイムアウトしました`);
    } else {
      console.error(`[${HOOK_NAME}] 警告: git grep実行エラー: ${formatError(e)}`);
    }
  }

  return references;
}

async function main(): Promise<number> {
  // Get staged diff
  const diff = await getStagedDiff();
  if (!diff) {
    return 0;
  }

  // Extract deleted symbols
  const deletedSymbols = extractDeletedSymbols(diff);
  if (deletedSymbols.length === 0) {
    return 0;
  }

  // Issue #3614: Filter out private functions before threshold check
  // Private functions (single underscore prefix) are skipped, but not __init__ or __private
  const checkableSymbols = deletedSymbols.filter(
    (s) => !(s.name.startsWith("_") && !s.name.startsWith("__")),
  );
  if (checkableSymbols.length === 0) {
    return 0;
  }

  // Issue #3614: Skip checking for large deletions (e.g., migration PRs)
  if (checkableSymbols.length > LARGE_DELETION_THRESHOLD) {
    console.log(`[${HOOK_NAME}] 警告: ${checkableSymbols.length}個のシンボルが削除されています。`);
    console.log(
      `[${HOOK_NAME}] 大量削除のためチェックをスキップします（閾値: ${LARGE_DELETION_THRESHOLD}）。`,
    );
    console.log(`[${HOOK_NAME}] テスト参照が残っていないか手動で確認してください。`);
    return 0;
  }

  // Check for stale references in test files
  const issues: Issue[] = [];

  for (const symbol of checkableSymbols) {
    // Issue #1958: Only check tests that import from the deleted module
    const references = await findTestReferences(symbol.name, symbol.sourceFile);
    if (references.length > 0) {
      issues.push({
        symbolType: symbol.type,
        symbolName: symbol.name,
        sourceFile: symbol.sourceFile,
        references,
      });
    }
  }

  if (issues.length === 0) {
    return 0;
  }

  // Report issues
  console.log("エラー: 削除されたシンボルがテストファイルに残っています。");
  console.log("");
  console.log("以下の削除された関数/クラスがテストで参照されています:");
  console.log("");

  for (const issue of issues) {
    const typeJa = issue.symbolType === "function" ? "関数" : "クラス";
    console.log(`  ${typeJa} '${issue.symbolName}' (${issue.sourceFile}から削除):`);
    for (const ref of issue.references.slice(0, 3)) {
      // Max 3 refs
      console.log(`    - ${ref.filePath}:${ref.lineNumber}: ${ref.content.trim().slice(0, 60)}`);
    }
    if (issue.references.length > 3) {
      console.log(`    ... 他${issue.references.length - 3}件`);
    }
    console.log("");
  }

  console.log("削除されたシンボルへの参照をテストファイルから削除してください。");
  console.log("");
  console.log("このチェックをスキップするには（非推奨）:");
  console.log("  git commit --no-verify");

  return 1;
}

// Only run main when executed directly (not when imported for testing)
if (import.meta.main) {
  main()
    .then((exitCode) => {
      process.exit(exitCode);
    })
    .catch((e) => {
      console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
      process.exit(0); // Fail open
    });
}
