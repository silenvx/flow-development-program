#!/usr/bin/env bun
/**
 * フック環境の整合性をチェックする。
 *
 * Why:
 *   settings.jsonに登録されたフックファイルが存在しないと、
 *   他のフックが期待通りに動作しない。セッション開始時に
 *   不足を検出し、早期に問題を認識させる。
 *
 * What:
 *   - settings.jsonから登録済みスクリプトパスを抽出
 *   - 各ファイルの存在を確認
 *   - 不足ファイルがあれば復旧手順を含む警告を表示
 *
 * Remarks:
 *   - ブロックはせず警告のみ（他の作業は継続可能）
 *   - SessionStartで発火
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "environment-integrity-check";

const HOOKS_DIR = dirname(import.meta.dir);
const PROJECT_DIR = dirname(dirname(HOOKS_DIR));
const SETTINGS_FILE = join(dirname(HOOKS_DIR), "settings.json");

/**
 * Extract all script paths from settings JSON content (relative to project root).
 * This is a pure function for easy testing.
 */
export function extractScriptPathsFromSettings(settingsContent: string): string[] {
  try {
    const settings = JSON.parse(settingsContent);
    const hooks = settings.hooks || {};
    const scriptPaths: Set<string> = new Set();

    // Pattern to match paths after $CLAUDE_PROJECT_DIR (quoted or unquoted)
    // Matches: "$CLAUDE_PROJECT_DIR"/path or $CLAUDE_PROJECT_DIR/path
    const pattern = /"?\$CLAUDE_PROJECT_DIR"?\/([^\s"]+)/g;

    for (const hookType of Object.values(hooks)) {
      if (Array.isArray(hookType)) {
        for (const entry of hookType) {
          const entryObj = entry as { hooks?: { command?: string }[] };
          for (const hook of entryObj.hooks || []) {
            const cmd = hook.command || "";
            if (!cmd) continue;

            // Find all script paths in the command
            pattern.lastIndex = 0;
            let match: RegExpExecArray | null = pattern.exec(cmd);
            while (match !== null) {
              let path = match[1];
              if (path) {
                // Clean up the path (remove trailing quotes, etc.)
                path = path.replace(/["']/g, "").trim();
                // Only include actual script files (not directories)
                if (path.endsWith(".py") || path.endsWith(".sh") || path.endsWith(".ts")) {
                  scriptPaths.add(path);
                }
              }
              match = pattern.exec(cmd);
            }
          }
        }
      }
    }

    return Array.from(scriptPaths);
  } catch {
    return [];
  }
}

/**
 * Extract all script paths from settings.json (relative to project root).
 */
export function getRegisteredScripts(): string[] {
  if (!existsSync(SETTINGS_FILE)) {
    return [];
  }

  try {
    const content = readFileSync(SETTINGS_FILE, "utf-8");
    return extractScriptPathsFromSettings(content);
  } catch {
    return [];
  }
}

/**
 * Check which script files exist and which are missing.
 */
function checkScriptFiles(): { found: string[]; missing: string[] } {
  const registered = getRegisteredScripts();
  const found: string[] = [];
  const missing: string[] = [];

  for (const scriptPath of registered) {
    const fullPath = join(PROJECT_DIR, scriptPath);
    if (existsSync(fullPath)) {
      found.push(scriptPath);
    } else {
      missing.push(scriptPath);
    }
  }

  return { found, missing };
}

async function main(): Promise<void> {
  // Parse hook input to get session ID
  const hookInput = await parseHookInput();
  const sessionId = hookInput.session_id;

  const { found, missing } = checkScriptFiles();

  if (missing.length > 0) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Missing hook files detected: ${missing.length} files`,
      undefined,
      { sessionId },
    );
    const fileList = missing
      .sort()
      .map((f) => `  - ${f}`)
      .join("\n");
    const warning = `[${HOOK_NAME}] フック環境に問題があります\n\n**不足ファイル** (${missing.length}件):\n${fileList}\n\n**復旧方法**:\n\`\`\`bash\n# メインリポジトリを最新に同期\ngit checkout main\ngit pull origin main\n\n# または不足ファイルを復元\ngit restore .claude/hooks/ scripts/\n\`\`\`\n\n**原因**: 別セッションで追加されたフックがマージされていない可能性があります。`;
    console.error(warning);
  } else {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `All ${found.length} hook files present`,
      undefined,
      {
        sessionId,
      },
    );
  }

  // Always continue (don't block) - just warn
  console.log(JSON.stringify({ continue: true }));
}

if (import.meta.main) {
  main();
}
