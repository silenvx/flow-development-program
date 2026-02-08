#!/usr/bin/env bun
/**
 * settings.json内のフックファイル参照を検証する。
 *
 * Why:
 *   削除されたフックへの参照やtypoを検出し、
 *   実行時エラーを事前に防ぐため。
 *
 * What:
 *   - extractHookPaths(): settings.jsonからフックパスを抽出
 *   - validatePaths(): ファイル存在を確認
 *
 * State:
 *   - reads: .claude/settings.json
 *   - reads: .claude/hooks/*.py, .claude/hooks/handlers/*.ts
 *
 * Remarks:
 *   - Exit 0: 全参照が有効、Exit 1: 欠落ファイル検出
 *   - Claude Codeは設定をキャッシュするため、削除後もエラーが継続する問題を防止
 *
 * Changelog:
 *   - silenvx/dekita#1300: フック設定検証機能を追加
 *   - silenvx/dekita#3636: TypeScriptに移植
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

export interface HookConfig {
  type?: string;
  command?: string;
}

export interface HookGroup {
  hooks?: HookConfig[];
}

export interface Settings {
  hooks?: {
    PreToolUse?: HookGroup[];
    PostToolUse?: HookGroup[];
    Stop?: HookGroup[];
    SessionStart?: HookGroup[];
    UserPromptSubmit?: HookGroup[];
  };
}

/**
 * Extract all hook file paths from settings.json.
 */
export function extractHookPaths(settings: Settings, projectDir: string): Array<[string, string]> {
  const hookPaths: Array<[string, string]> = [];
  const hooksConfig = settings.hooks ?? {};

  const hookTypes = [
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "SessionStart",
    "UserPromptSubmit",
  ] as const;

  for (const hookType of hookTypes) {
    const hookList = hooksConfig[hookType] ?? [];
    for (const hookGroup of hookList) {
      for (const hook of hookGroup.hooks ?? []) {
        if (hook.type === "command" && hook.command) {
          const command = hook.command;

          // Pattern: python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/xxx.py
          // Supports paths with spaces when quoted
          const pythonMatch = command.match(/python3\s+"?\$CLAUDE_PROJECT_DIR"?\/([^"]+\.py)/);
          if (pythonMatch) {
            const relativePath = pythonMatch[1];
            const fullPath = join(projectDir, relativePath);
            hookPaths.push([command, fullPath]);
            continue;
          }

          // Pattern: bun run "$CLAUDE_PROJECT_DIR"/.claude/hooks/handlers/xxx.ts
          // Supports paths with spaces when quoted
          const bunMatch = command.match(/bun\s+run\s+"?\$CLAUDE_PROJECT_DIR"?\/([^"]+\.ts)/);
          if (bunMatch) {
            const relativePath = bunMatch[1];
            const fullPath = join(projectDir, relativePath);
            hookPaths.push([command, fullPath]);
            continue;
          }

          // Fallback: handle direct paths without $CLAUDE_PROJECT_DIR
          // Supports paths with spaces when quoted
          const directMatch = command.match(/(python3|bun\s+run)\s+"?([^"]+\.(py|ts))/);
          if (directMatch) {
            let pathStr = directMatch[2];
            if (pathStr.startsWith("$CLAUDE_PROJECT_DIR")) {
              pathStr = pathStr.replace("$CLAUDE_PROJECT_DIR", projectDir);
            }
            const fullPath = pathStr.startsWith("/") ? pathStr : join(projectDir, pathStr);
            hookPaths.push([command, fullPath]);
          }
        }
      }
    }
  }

  return hookPaths;
}

function main(): number {
  // Find project directory (where settings.json is located)
  const scriptDir = __dirname;
  const projectDir = resolve(scriptDir, "..", ".."); // .claude/scripts -> .claude -> project root

  const settingsPath = join(projectDir, ".claude", "settings.json");

  if (!existsSync(settingsPath)) {
    console.log(`⚠️  No settings.json found at ${settingsPath}`);
    return 0; // Not an error - settings might not exist
  }

  let settings: Settings;
  try {
    const content = readFileSync(settingsPath, "utf-8");
    settings = JSON.parse(content) as Settings;
  } catch (e) {
    if (e instanceof SyntaxError) {
      console.log(`❌ Invalid JSON in settings.json: ${e.message}`);
      return 1;
    }
    throw e;
  }

  const hookPaths = extractHookPaths(settings, projectDir);

  if (hookPaths.length === 0) {
    console.log("✅ No hook file references found in settings.json");
    return 0;
  }

  const missingFiles: Array<[string, string]> = [];
  for (const [command, path] of hookPaths) {
    if (!existsSync(path)) {
      missingFiles.push([command, path]);
    }
  }

  if (missingFiles.length > 0) {
    console.log("❌ Missing hook files detected!");
    console.log("");
    console.log("The following hook references in settings.json point to non-existent files:");
    console.log("");
    for (const [command, path] of missingFiles) {
      console.log(`  File: ${path}`);
      console.log(`  Command: ${command}`);
      console.log("");
    }
    console.log("To fix:");
    console.log("  1. Remove the reference from .claude/settings.json, OR");
    console.log("  2. Create the missing file");
    console.log("");
    console.log("Note: After fixing, you may need to restart Claude Code session");
    console.log("      to clear the cached hook settings.");
    return 1;
  }

  console.log(`✅ All ${hookPaths.length} hook file references are valid`);
  return 0;
}

// Only run main when executed directly (not when imported for testing)
if (import.meta.main) {
  process.exit(main());
}
