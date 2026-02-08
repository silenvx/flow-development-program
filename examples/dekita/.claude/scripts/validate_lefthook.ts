#!/usr/bin/env bun
/**
 * lefthook.yml設定を検証する。
 *
 * Why:
 *   lefthook設定の誤りを事前に検出し、
 *   pre-pushでの{staged_files}使用等のミスを防ぐため。
 *
 * What:
 *   - validate(): 設定を検証
 *   - checkPrePushStagedFiles(): pre-pushでの{staged_files}使用を検出
 *
 * Remarks:
 *   - LEFTHOOK001: pre-pushで{staged_files}は無意味
 *   - ファイル指定なしで./lefthook.ymlをチェック
 *
 * Changelog:
 *   - silenvx/dekita#1100: lefthook設定検証機能を追加
 *   - silenvx/dekita#3636: TypeScriptに移植
 */

import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";

// Lazy-loaded yaml parser to avoid top-level side effects
// that would crash test runners when dependencies are missing.
// States: undefined = not initialized, null = load failed, function = success
let cachedParseYaml: ((content: string) => unknown) | null | undefined = undefined;

/**
 * Get the yaml parser, loading it lazily on first use.
 * Returns null if the package cannot be loaded or parse is not a function.
 */
export function getParseYaml(): ((content: string) => unknown) | null {
  // Already initialized (success or failure)
  if (cachedParseYaml !== undefined) {
    return cachedParseYaml;
  }
  try {
    const require = createRequire(import.meta.url);
    const yaml = require("../hooks/node_modules/yaml");
    if (typeof yaml?.parse === "function") {
      cachedParseYaml = yaml.parse;
      return cachedParseYaml;
    }
    // yaml.parse is not a function
    cachedParseYaml = null;
    return null;
  } catch {
    // Package not installed or other error
    cachedParseYaml = null;
    return null;
  }
}

/**
 * Reset the cached parser (for testing purposes).
 */
export function resetParseYamlCache(): void {
  cachedParseYaml = undefined;
}

export interface LintError {
  file: string;
  line: number;
  code: string;
  message: string;
}

export interface CommandConfig {
  run?: string | string[];
  [key: string]: unknown;
}

export interface HookConfig {
  commands?: Record<string, CommandConfig>;
  [key: string]: unknown;
}

export interface LefthookConfig {
  "pre-push"?: HookConfig;
  [key: string]: unknown;
}

/**
 * Find the line number of a text in content.
 */
export function findLineNumber(content: string, searchText: string): number {
  const lines = content.split("\n");
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes(searchText)) {
      return i + 1;
    }
  }
  return 0;
}

/**
 * Check that pre-push commands don't use {staged_files}.
 */
export function checkStagedFilesInPrePush(
  config: LefthookConfig,
  content: string,
  filepath: string,
): LintError[] {
  const errors: LintError[] = [];

  const prePush = config["pre-push"];
  if (!prePush) {
    return errors;
  }

  const commands = prePush.commands ?? {};

  for (const [cmdName, cmdConfig] of Object.entries(commands)) {
    if (typeof cmdConfig !== "object" || cmdConfig === null) {
      continue;
    }

    const runCommands = [cmdConfig.run]
      .flat()
      .filter((cmd): cmd is string => typeof cmd === "string" && cmd.length > 0);
    for (const runCmd of runCommands) {
      if (runCmd.includes("{staged_files}")) {
        const line = findLineNumber(content, runCmd.slice(0, 50));
        errors.push({
          file: filepath,
          line,
          code: "LEFTHOOK001",
          message: `pre-push command '${cmdName}' uses {staged_files} which is meaningless. In pre-push context, there are no staged files. Consider using {push_files} or removing the variable.`,
        });
      }
    }
  }

  return errors;
}

/**
 * Lint lefthook.yml file.
 */
export function lintLefthook(filepath: string): LintError[] {
  let content: string;
  try {
    content = readFileSync(filepath, "utf-8");
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return [
      {
        file: filepath,
        line: 0,
        code: "LEFTHOOK000",
        message: `Failed to read file: ${message}`,
      },
    ];
  }

  const parseYaml = getParseYaml();
  if (parseYaml === null) {
    return [
      {
        file: filepath,
        line: 0,
        code: "LEFTHOOK000",
        message:
          "Failed to load 'yaml' package or its parse export is invalid. The yaml dependency may be missing or misconfigured. If the package is not installed, run: cd .claude/hooks && bun install",
      },
    ];
  }

  let config: LefthookConfig;
  try {
    config = parseYaml(content) as LefthookConfig;
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return [
      {
        file: filepath,
        line: 0,
        code: "LEFTHOOK000",
        message: `YAML parse error: ${message}`,
      },
    ];
  }

  if (!config) {
    return [];
  }

  const errors: LintError[] = [];
  errors.push(...checkStagedFilesInPrePush(config, content, filepath));

  return errors;
}

function main(): number {
  const args = process.argv.slice(2);
  const filepath = args.length > 0 ? args[0] : "lefthook.yml";

  if (!existsSync(filepath)) {
    console.error(`File not found: ${filepath}`);
    return 1;
  }

  const errors = lintLefthook(filepath);

  for (const error of errors) {
    console.log(`${error.file}:${error.line}: [${error.code}] ${error.message}`);
  }

  if (errors.length > 0) {
    console.error(`\nFound ${errors.length} error(s)`);
    return 1;
  }

  console.log(`Checked ${filepath}, no errors found`);
  return 0;
}

// Only run main when executed directly (not when imported for testing)
if (import.meta.main) {
  process.exit(main());
}
