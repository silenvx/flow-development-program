#!/usr/bin/env bun
/**
 * フックのlib/依存関係を分析しMermaid図を生成する。
 *
 * Why:
 *   フック間の依存関係を可視化し、
 *   リファクタリングの影響範囲を把握するため。
 *
 * What:
 *   - extractImports(): TypeScriptファイルからlib/*インポートを抽出
 *   - analyzeDependencies(): 全フックの依存関係を分析
 *   - generateMermaid(): Mermaid図を生成
 *
 * Remarks:
 *   - --output オプションでファイル出力
 *   - lib/index.tsは除外される
 *   - TypeScript版フック（.claude/hooks/）を分析
 *
 * Changelog:
 *   - silenvx/dekita#1337: フック依存関係分析機能を追加
 *   - silenvx/dekita#3643: TypeScriptに移植
 *   - silenvx/dekita#3644: バレルインポート（from "../lib"）対応
 *   - silenvx/dekita#4047: 正規表現をTypeScript AST解析に置換
 */

import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { parseArgs } from "node:util";
import ts from "typescript";

interface ModuleInfo {
  lineCount: number;
  dependents: string[];
  dependentCount: number;
  percentage: number;
}

interface DependencyData {
  totalHooks: number;
  modules: Record<string, ModuleInfo>;
}

/**
 * Get the TypeScript hooks directory path.
 */
function getHooksDir(): string {
  const scriptDir = dirname(import.meta.path);
  return resolve(scriptDir, "..", "hooks", "handlers");
}

/**
 * Get the TypeScript lib directory path.
 */
export function getLibDir(): string {
  const scriptDir = dirname(import.meta.path);
  return resolve(scriptDir, "..", "hooks", "lib");
}

/**
 * Parse barrel exports from index.ts to get all exported module names.
 * Uses TypeScript AST to accurately detect `export * from "./xxx"` and
 * `export { ... } from "./xxx"`, ignoring comments and string literals.
 */
export function getBarrelExports(libDir: string): Set<string> {
  const modules = new Set<string>();
  const indexPath = join(libDir, "index.ts");

  try {
    const content = readFileSync(indexPath, "utf-8");
    const sourceFile = ts.createSourceFile(indexPath, content, ts.ScriptTarget.Latest, true);

    for (const stmt of sourceFile.statements) {
      if (
        ts.isExportDeclaration(stmt) &&
        stmt.moduleSpecifier &&
        ts.isStringLiteral(stmt.moduleSpecifier)
      ) {
        const specifier = stmt.moduleSpecifier.text;
        const match = specifier.match(/^\.\/([\w.-]+)$/);
        if (match) {
          modules.add(match[1].replace(/\.(js|ts)$/, ""));
        }
      }
    }
  } catch {
    // Return empty set if index.ts cannot be read
  }

  return modules;
}

/**
 * Extract lib/* imports from a TypeScript file using TypeScript AST.
 * Supports both direct imports (from "../lib/xxx") and barrel imports (from "../lib").
 * Uses AST parsing to correctly ignore imports inside comments and string literals.
 *
 * Note: When a barrel import is detected (from "../lib"), all modules exported from
 * the barrel are counted as dependencies. This is intentional over-approximation
 * because tracking individual named imports from barrel files would require more
 * complex analysis, and the current approach is sufficient for dependency analysis.
 */
export function extractImports(filePath: string, barrelExports?: Set<string>): Set<string> {
  const imports = new Set<string>();
  try {
    const content = readFileSync(filePath, "utf-8");
    const sourceFile = ts.createSourceFile(filePath, content, ts.ScriptTarget.Latest, true);

    for (const stmt of sourceFile.statements) {
      if (
        (ts.isImportDeclaration(stmt) || ts.isExportDeclaration(stmt)) &&
        stmt.moduleSpecifier &&
        ts.isStringLiteral(stmt.moduleSpecifier)
      ) {
        const path = stmt.moduleSpecifier.text;

        // Barrel import: "../lib" or "../lib/index" (with optional extension)
        if (/^\.\.\/lib(?:\/index)?(?:\.(js|ts))?$/.test(path)) {
          if (barrelExports) {
            for (const mod of barrelExports) {
              imports.add(mod);
            }
          }
          continue;
        }

        // Direct import: "../lib/xxx" or "../lib/xxx.js"
        const directMatch = path.match(/^\.\.\/lib\/([\w.-]+)$/);
        if (directMatch) {
          const name = directMatch[1].replace(/\.(js|ts)$/, "");
          if (name !== "index") {
            imports.add(name);
          }
        }
      }
    }
  } catch {
    // Skip files that cannot be read
  }
  return imports;
}

/**
 * Analyze all hook files and their dependencies.
 */
function analyzeDependencies(): DependencyData {
  const hooksDir = getHooksDir();
  const libDir = getLibDir();

  // Get barrel exports from index.ts for detecting barrel imports
  const barrelExports = getBarrelExports(libDir);

  // Get all lib modules
  const libModules: Record<string, ModuleInfo> = {};
  if (existsSync(libDir)) {
    for (const file of readdirSync(libDir)) {
      if (file.endsWith(".ts") && file !== "index.ts") {
        const moduleName = basename(file, ".ts");
        try {
          const content = readFileSync(join(libDir, file), "utf-8");
          const lineCount = content.split("\n").length;
          libModules[moduleName] = {
            lineCount,
            dependents: [],
            dependentCount: 0,
            percentage: 0,
          };
        } catch {
          libModules[moduleName] = {
            lineCount: 0,
            dependents: [],
            dependentCount: 0,
            percentage: 0,
          };
        }
      }
    }
  }

  // Get all hook files
  const hookFiles: string[] = [];
  if (existsSync(hooksDir)) {
    for (const file of readdirSync(hooksDir)) {
      if (file.endsWith(".ts")) {
        hookFiles.push(join(hooksDir, file));
      }
    }
  }
  const totalHooks = hookFiles.length;

  // Analyze each hook
  for (const hookFile of hookFiles) {
    const imports = extractImports(hookFile, barrelExports);
    for (const moduleName of imports) {
      if (Object.prototype.hasOwnProperty.call(libModules, moduleName)) {
        libModules[moduleName].dependents.push(basename(hookFile, ".ts"));
      }
    }
  }

  // Calculate statistics
  for (const info of Object.values(libModules)) {
    info.dependentCount = info.dependents.length;
    info.percentage = totalHooks > 0 ? Math.round((info.dependentCount / totalHooks) * 100) : 0;
  }

  return {
    totalHooks,
    modules: libModules,
  };
}

/**
 * Generate Mermaid diagram from dependency data.
 */
function generateMermaid(data: DependencyData): string {
  const { modules, totalHooks } = data;

  // Sort by dependency count
  const sortedModules = Object.entries(modules).sort(
    (a, b) => b[1].dependentCount - a[1].dependentCount,
  );

  // Categorize modules
  const coreModules = sortedModules.filter(([, d]) => d.percentage >= 50);
  const supportModules = sortedModules.filter(([, d]) => d.percentage >= 10 && d.percentage < 50);
  const utilityModules = sortedModules.filter(([, d]) => d.percentage < 10);

  const lines = [
    "```mermaid",
    "graph TD",
    `    subgraph "Hooks (${totalHooks}個)"`,
    "        H[hooks/*.ts]",
    "    end",
    "",
  ];

  if (coreModules.length > 0) {
    lines.push('    subgraph "Core Libraries (50%+依存)"');
    for (const [moduleName, info] of coreModules) {
      const pct = info.percentage;
      lines.push(`        ${moduleName.toUpperCase()}[${moduleName}.ts<br/>${pct}%依存]`);
    }
    lines.push("    end");
    lines.push("");
  }

  if (supportModules.length > 0) {
    lines.push('    subgraph "Support Libraries (10-50%依存)"');
    for (const [moduleName, info] of supportModules) {
      const pct = info.percentage;
      lines.push(`        ${moduleName.toUpperCase()}[${moduleName}.ts<br/>${pct}%依存]`);
    }
    lines.push("    end");
    lines.push("");
  }

  if (utilityModules.length > 0) {
    lines.push('    subgraph "Utility Libraries (<10%依存)"');
    for (const [moduleName, info] of utilityModules) {
      const pct = info.percentage;
      lines.push(`        ${moduleName.toUpperCase()}[${moduleName}.ts<br/>${pct}%依存]`);
    }
    lines.push("    end");
    lines.push("");
  }

  // Add edges
  for (const [moduleName, info] of sortedModules) {
    if (info.dependentCount > 0) {
      lines.push(`    H --> ${moduleName.toUpperCase()}`);
    }
  }

  lines.push("```");
  return lines.join("\n");
}

/**
 * Print dependency statistics.
 */
function printStatistics(data: DependencyData): void {
  const { modules, totalHooks } = data;

  console.log(`\n## 依存関係統計 (全${totalHooks}個のhook)\n`);
  console.log("| モジュール | 依存hook数 | 割合 | 行数 |");
  console.log("| ---------- | ---------- | ---- | ---- |");

  const sortedModules = Object.entries(modules).sort(
    (a, b) => b[1].dependentCount - a[1].dependentCount,
  );

  for (const [moduleName, info] of sortedModules) {
    const count = info.dependentCount;
    const pct = info.percentage;
    const lineCount = info.lineCount;
    console.log(`| ${moduleName}.ts | ${count}/${totalHooks} | ${pct}% | ${lineCount} |`);
  }
}

function printHelp(): void {
  console.log(`Usage: analyze_hook_dependencies.ts [options]

フックのlib/依存関係を分析しMermaid図を生成する。

Options:
  -o, --output <file>  Mermaid図をファイルに出力
  --stats-only         統計情報のみ表示（図は生成しない）
  -h, --help           このヘルプを表示

Examples:
  analyze_hook_dependencies.ts
  analyze_hook_dependencies.ts --stats-only
  analyze_hook_dependencies.ts -o deps.mmd`);
}

function main(): void {
  const { values } = parseArgs({
    args: process.argv.slice(2),
    options: {
      output: { type: "string", short: "o" },
      "stats-only": { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
    allowPositionals: false,
  });

  if (values.help) {
    printHelp();
    process.exit(0);
  }

  const data = analyzeDependencies();

  if (values["stats-only"]) {
    printStatistics(data);
    return;
  }

  printStatistics(data);
  console.log("\n## 依存関係図\n");

  const mermaid = generateMermaid(data);
  if (values.output) {
    writeFileSync(values.output, mermaid, "utf-8");
    console.log(`Mermaid diagram written to ${values.output}`);
  } else {
    console.log(mermaid);
  }
}

if (import.meta.main) {
  main();
}
