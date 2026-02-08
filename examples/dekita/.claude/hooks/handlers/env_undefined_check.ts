#!/usr/bin/env bun
/**
 * Edit時に process.env への undefined 代入を検出して警告。
 *
 * Why:
 *   Node.js では process.env の値は常に文字列であり、undefined を代入すると
 *   文字列 "undefined" が設定される。環境変数を削除する意図なら
 *   Reflect.deleteProperty(process.env, "KEY") を使用すべき。
 *
 * What:
 *   - Edit/Write ツール実行を検出
 *   - new_string/content から process.env.X = undefined パターンを検索
 *   - 検出時は systemMessage で警告（Reflect.deleteProperty への変更を推奨）
 *
 * Remarks:
 *   - 非ブロック型（警告のみ）
 *   - PreToolUse:Edit, PreToolUse:Write フック
 *   - .ts, .tsx, .js, .jsx ファイルが対象
 *
 * Changelog:
 *   - silenvx/dekita#3280: 初期実装
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "env-undefined-check";

// Patterns that detect process.env.X = undefined/null (problematic)
// Using \b after undefined/null to avoid false positives like `undefinedValue`
const ENV_UNDEFINED_PATTERNS = [
  // process.env.KEY = undefined / null
  /\bprocess\.env\.(\w+)\s*=\s*(?:undefined|null)\b/g,
  // process.env["KEY"] = undefined / null
  /\bprocess\.env\["([^"]+)"\]\s*=\s*(?:undefined|null)\b/g,
  // process.env['KEY'] = undefined / null
  /\bprocess\.env\['([^']+)'\]\s*=\s*(?:undefined|null)\b/g,
  // process.env[`KEY`] = undefined / null
  /\bprocess\.env\[`([^`]+)`\]\s*=\s*(?:undefined|null)\b/g,
];

// File extensions to check
const CHECKABLE_EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]);

/**
 * Check if the file should be checked.
 */
export function shouldCheckFile(filePath: string): boolean {
  const ext = filePath.slice(filePath.lastIndexOf("."));
  return CHECKABLE_EXTENSIONS.has(ext);
}

/**
 * Find process.env = undefined patterns in the given text.
 * Returns an array of { match: string, key: string } objects.
 */
export function findEnvUndefinedAssignments(text: string): Array<{ match: string; key: string }> {
  const results: Array<{ match: string; key: string }> = [];
  const seen = new Set<string>();

  for (const pattern of ENV_UNDEFINED_PATTERNS) {
    // Reset lastIndex
    pattern.lastIndex = 0;

    for (let match = pattern.exec(text); match !== null; match = pattern.exec(text)) {
      const fullMatch = match[0];
      const key = match[1];

      if (!seen.has(fullMatch)) {
        seen.add(fullMatch);
        results.push({ match: fullMatch, key });
      }
    }
  }

  return results;
}

/**
 * Format the warning message for detected patterns.
 */
export function formatWarningMessage(
  matches: Array<{ match: string; key: string }>,
  filePath: string,
): string {
  const lines = [
    "⚠️ **process.env への undefined 代入を検出しました**",
    "",
    `ファイル: \`${filePath}\``,
    "",
    "**問題点**: Node.js では `process.env` の値は常に文字列です。",
    "`undefined` を代入すると、環境変数が削除されるのではなく、",
    '文字列 `"undefined"` が設定されます。',
    "",
    "検出されたパターン:",
  ];

  for (const { match, key } of matches.slice(0, 5)) {
    lines.push(`  - \`${match}\``);
    lines.push(`    → \`Reflect.deleteProperty(process.env, "${key}")\``);
  }

  lines.push(
    "",
    "**推奨される修正**:",
    "```typescript",
    '// ❌ 誤り: 文字列 "undefined" が設定される',
    "process.env.KEY = undefined;",
    "",
    "// ✅ 正解: 環境変数を正しく削除",
    'Reflect.deleteProperty(process.env, "KEY");',
    "```",
  );

  return lines.join("\n");
}

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};

  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;
    const toolName = inputData.tool_name ?? "";
    const toolInput = (inputData.tool_input as Record<string, unknown>) ?? {};

    // Only check Edit and Write operations
    if (toolName !== "Edit" && toolName !== "Write") {
      console.log(JSON.stringify(result));
      return;
    }

    const filePath = (toolInput.file_path as string) ?? (toolInput.path as string) ?? "";

    // Skip if in .claude/ directory (avoid processing hook files)
    if (filePath.includes(".claude/")) {
      console.log(JSON.stringify(result));
      return;
    }

    // Get content to check (new_string for Edit, content for Write)
    const contentToCheck = (toolInput.new_string as string) ?? (toolInput.content as string) ?? "";

    // Skip if not a checkable file
    if (!shouldCheckFile(filePath)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `skipped: not a checkable file (${filePath})`,
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Skip if no content
    if (!contentToCheck) {
      console.log(JSON.stringify(result));
      return;
    }

    // Find process.env = undefined patterns
    const matches = findEnvUndefinedAssignments(contentToCheck);

    if (matches.length > 0) {
      result.systemMessage = formatWarningMessage(matches, filePath);
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `warning: found ${matches.length} env undefined assignments`,
        {
          file: filePath,
          matches: matches.map((m) => m.match),
        },
        { sessionId },
      );
    } else {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "no env undefined assignments found",
        undefined,
        { sessionId },
      );
    }
  } catch (error) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", `error: ${formatError(error)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

// Only run when executed directly, not when imported for testing
if (import.meta.main) {
  main();
}
