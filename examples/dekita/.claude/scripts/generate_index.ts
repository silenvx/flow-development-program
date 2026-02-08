#!/usr/bin/env bun
/**
 * .fdp/index.json と .fdp/README.md を再生成するスクリプト
 *
 * Usage:
 *   bun run .claude/scripts/generate_index.ts
 *   bun run .claude/scripts/generate_index.ts --verbose
 *   bun run .claude/scripts/generate_index.ts --dry-run
 */

import { execSync } from "node:child_process";
import { readFile, readdir, writeFile } from "node:fs/promises";
import { basename, join, relative, resolve } from "node:path";

const PROJECT_DIR = resolve(import.meta.dir, "../..");
const HOOKS_DIR = join(PROJECT_DIR, ".claude/hooks/ts/hooks");
const LIB_DIR = join(PROJECT_DIR, ".claude/hooks/ts/lib");
const SCRIPTS_DIR = join(PROJECT_DIR, ".claude/scripts");
const SKILLS_DIR = join(PROJECT_DIR, ".claude/skills");
const SETTINGS_PATH = join(PROJECT_DIR, ".claude/settings.json");
const INDEX_PATH = join(PROJECT_DIR, ".fdp/index.json");
const README_PATH = join(PROJECT_DIR, ".fdp/README.md");

const args = process.argv.slice(2);
const verbose = args.includes("--verbose");
const dryRun = args.includes("--dry-run");

interface HookEntry {
  name: string;
  path: string;
  summary: string;
  why: string;
  what: string;
  hook_type: string;
  keywords: string[];
  trigger: Array<{ event: string; matcher?: string }>;
}

interface LibEntry {
  name: string;
  path: string;
  summary: string;
}

interface ScriptEntry {
  name: string;
  path: string;
  summary: string;
}

interface SkillEntry {
  name: string;
  summary: string;
  description: string;
}

interface IndexJson {
  generated: string;
  stats: { hooks: number; libs: number; scripts: number; skills: number };
  hooks: HookEntry[];
  libs: LibEntry[];
  scripts: ScriptEntry[];
  skills: SkillEntry[];
}

/** JSDocの最初の説明行を抽出 */
function extractSummary(content: string): string {
  // 複数行JSDoc: /** \n * summary
  const multiLine = content.match(/\/\*\*\s*\n\s*\*\s*(.+)/);
  if (multiLine) return multiLine[1].trim();
  // 単行JSDoc: /** summary */
  const singleLine = content.match(/\/\*\*\s+(.+?)\s*\*\//);
  return singleLine ? singleLine[1].trim() : "";
}

/** JSDocからWhy/Whatセクションを抽出 */
function extractSection(content: string, section: string): string {
  // 同一行: `* Why: 理由` または 次行以降: `* Why:\n *   理由`
  const sameLineRegex = new RegExp(`\\*\\s*${section}:\\s+(.+)`, "m");
  const sameLineMatch = content.match(sameLineRegex);
  if (sameLineMatch) {
    // 同一行の後に続く行も収集
    const startIdx = sameLineMatch.index!;
    const afterMatch = content.slice(startIdx + sameLineMatch[0].length);
    const continuationLines: string[] = [sameLineMatch[1].trim()];
    for (const line of afterMatch.split("\n")) {
      const trimmed = line.replace(/^\s*\*\s?/, "").trim();
      if (/^[A-Z][a-z]+:/.test(trimmed) || trimmed === "/") break;
      if (!trimmed) continue;
      if (trimmed.startsWith("-") || trimmed.startsWith("・")) {
        continuationLines.push(trimmed);
      }
    }
    return continuationLines.join(" ");
  }

  const multiLineRegex = new RegExp(`\\*\\s*${section}:\\s*\\n((?:\\s*\\*\\s+.*\\n)*)`, "m");
  const match = content.match(multiLineRegex);
  if (!match) return "";
  return match[1]
    .split("\n")
    .map((line) => line.replace(/^\s*\*\s*/, "").trim())
    .filter(Boolean)
    .join(" ");
}

/** フック種別を推定 */
function detectHookType(content: string): string {
  if (/exit\s*2|process\.exit\(2\)/.test(content)) return "blocking";
  if (/ACTION_REQUIRED/.test(content)) return "warning";
  if (/console\.error|warn/i.test(content) && !/exit\s*2/.test(content)) return "warning";
  if (/logHookExecution/.test(content)) return "logging";
  return "info";
}

/** ファイル名からキーワードを生成 */
function extractKeywords(name: string): string[] {
  return name.split("_").filter((w) => w.length > 2);
}

/** settings.jsonからフック→trigger/matcherマッピングを構築 */
function buildTriggerMap(
  settings: Record<string, unknown>,
): Map<string, Array<{ event: string; matcher?: string }>> {
  const map = new Map<string, Array<{ event: string; matcher?: string }>>();
  const hooks = settings.hooks as Record<string, unknown[]> | undefined;
  if (!hooks) return map;

  for (const [event, groups] of Object.entries(hooks)) {
    if (!Array.isArray(groups)) continue;
    for (const group of groups) {
      const g = group as { matcher?: string; hooks?: Array<{ command?: string }> };
      const hookList = g.hooks;
      if (!Array.isArray(hookList)) continue;
      const matcher = g.matcher;

      for (const hook of hookList) {
        if (!hook.command) continue;
        const hookFileMatch = hook.command.match(/hooks\/([a-z0-9_-]+)\.ts/);
        if (!hookFileMatch) continue;
        const hookName = hookFileMatch[1];

        if (!map.has(hookName)) map.set(hookName, []);
        const entry: { event: string; matcher?: string } = { event };
        if (matcher && matcher !== ".*") entry.matcher = matcher;
        map.get(hookName)!.push(entry);
      }
    }
  }
  return map;
}

/** .tsファイル一覧を取得（ソート済み） */
async function listTsFiles(dir: string): Promise<string[]> {
  try {
    const files = await readdir(dir);
    return files.filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts")).sort();
  } catch {
    return [];
  }
}

/** ディレクトリを再帰的に走査し、スクリプトファイルを収集 */
async function listScriptsRecursive(
  dir: string,
  baseDir: string,
): Promise<Array<{ relPath: string; absPath: string }>> {
  const results: Array<{ relPath: string; absPath: string }> = [];
  try {
    const entries = await readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = join(dir, entry.name);
      if (entry.isDirectory() && !entry.name.startsWith("__")) {
        results.push(...(await listScriptsRecursive(fullPath, baseDir)));
      } else if (
        entry.isFile() &&
        (entry.name.endsWith(".ts") || entry.name.endsWith(".sh")) &&
        !entry.name.endsWith(".test.ts") &&
        !entry.name.startsWith("__")
      ) {
        results.push({ relPath: relative(baseDir, fullPath), absPath: fullPath });
      }
    }
  } catch {
    // skip
  }
  return results.sort((a, b) => a.relPath.localeCompare(b.relPath));
}

/** ディレクトリ一覧を取得 */
async function listDirs(dir: string): Promise<string[]> {
  try {
    const entries = await readdir(dir, { withFileTypes: true });
    return entries
      .filter((e) => e.isDirectory())
      .map((e) => e.name)
      .sort();
  } catch {
    return [];
  }
}

async function main(): Promise<void> {
  // settings.json読み込み
  const settingsContent = await readFile(SETTINGS_PATH, "utf-8");
  const settings = JSON.parse(settingsContent);
  const triggerMap = buildTriggerMap(settings);

  // フック一覧
  const hookFiles = await listTsFiles(HOOKS_DIR);
  const hooks: HookEntry[] = [];

  for (const file of hookFiles) {
    const name = basename(file, ".ts");
    const content = await readFile(join(HOOKS_DIR, file), "utf-8");
    const summary = extractSummary(content);
    const why = extractSection(content, "Why");
    const what = extractSection(content, "What");
    const hook_type = detectHookType(content);
    const keywords = extractKeywords(name);
    const trigger = triggerMap.get(name) ?? [];

    const path = `.claude/hooks/ts/hooks/${file}`;
    hooks.push({ name, path, summary, why, what, hook_type, keywords, trigger });
    if (verbose) console.log(`  hook: ${name} (${hook_type}) triggers: ${trigger.length}`);
  }

  // ライブラリ一覧
  const libFiles = await listTsFiles(LIB_DIR);
  const libs: LibEntry[] = [];
  for (const file of libFiles) {
    const name = basename(file, ".ts");
    const content = await readFile(join(LIB_DIR, file), "utf-8");
    const summary = extractSummary(content);
    const path = `.claude/hooks/ts/lib/${file}`;
    libs.push({ name, path, summary });
  }

  // スクリプト一覧（サブディレクトリ含む、テストファイル除外）
  const scriptEntries = await listScriptsRecursive(SCRIPTS_DIR, SCRIPTS_DIR);
  const scripts: ScriptEntry[] = [];
  for (const { relPath, absPath } of scriptEntries) {
    const name = relPath.replace(/\.(ts|sh)$/, "");
    let summary = "";
    try {
      const content = await readFile(absPath, "utf-8");
      summary = extractSummary(content) || "";
    } catch {
      // skip
    }
    scripts.push({ name, path: `.claude/scripts/${relPath}`, summary });
  }

  // スキル一覧
  const skillDirs = await listDirs(SKILLS_DIR);
  const skills: SkillEntry[] = [];
  for (const dir of skillDirs) {
    const indexPath = join(SKILLS_DIR, dir, "SKILL.md");
    let summary = "";
    let description = "";
    try {
      const content = await readFile(indexPath, "utf-8");
      const titleMatch = content.match(/^#\s+(.+)/m);
      summary = titleMatch ? titleMatch[1].trim() : dir;
      const descMatch = content.match(/^#\s+.+\n+(.+)/m);
      description = descMatch ? descMatch[1].trim() : "";
    } catch {
      summary = dir;
    }
    skills.push({ name: dir, summary, description });
  }

  const index: IndexJson = {
    generated: `${new Date().toISOString().split("T")[0]}T00:00:00.000Z`,
    stats: {
      hooks: hooks.length,
      libs: libs.length,
      scripts: scripts.length,
      skills: skills.length,
    },
    hooks,
    libs,
    scripts,
    skills,
  };

  // README.md生成
  const readmeLines: string[] = [
    "# dekita 機能カタログ",
    "",
    `生成日時: ${new Date().toISOString().split("T")[0]}`,
    "",
    "## 概要",
    "このプロジェクトの開発フロー構成要素。",
    "",
    "## 統計",
    "| カテゴリ | 数 |",
    "|---------|---|",
    `| フック | ${hooks.length} |`,
    `| ライブラリ | ${libs.length} |`,
    `| スクリプト | ${scripts.length} |`,
    `| スキル | ${skills.length} |`,
    "",
    `## フック一覧（全${hooks.length}件）`,
    "| フック | 説明 | 種別 |",
    "|-------|------|------|",
    ...hooks.map((h) => `| \`${h.name}\` | ${h.summary} | ${h.hook_type} |`),
    "",
    `## ライブラリ一覧（全${libs.length}件）`,
    "| ライブラリ | 説明 |",
    "|-----------|------|",
    ...libs.map((l) => `| \`${l.name}\` | ${l.summary} |`),
    "",
    `## スクリプト一覧（全${scripts.length}件）`,
    "| スクリプト | 説明 |",
    "|-----------|------|",
    ...scripts.map((s) => `| \`${s.name}\` | ${s.summary} |`),
    "",
    `## スキル一覧（全${skills.length}件）`,
    "| スキル | 説明 |",
    "|--------|------|",
    ...skills.map((s) => `| \`${s.name}\` | ${s.summary} |`),
    "",
  ];

  console.log(
    `Stats: ${hooks.length} hooks, ${libs.length} libs, ${scripts.length} scripts, ${skills.length} skills`,
  );

  if (dryRun) {
    console.log("[dry-run] Would write:");
    console.log(`  ${INDEX_PATH}`);
    console.log(`  ${README_PATH}`);
    return;
  }

  await writeFile(INDEX_PATH, `${JSON.stringify(index, null, 2)}\n`, "utf-8");
  await writeFile(README_PATH, readmeLines.join("\n"), "utf-8");

  // biome formatで整形（pre-commit hookのts-formatと一貫性を保つ）
  try {
    execSync(`pnpm exec biome check --fix --unsafe ${INDEX_PATH}`, {
      cwd: PROJECT_DIR,
      stdio: "pipe",
    });
  } catch {
    // biomeが利用できない環境では無視
  }

  console.log(`Written: ${INDEX_PATH}`);
  console.log(`Written: ${README_PATH}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
