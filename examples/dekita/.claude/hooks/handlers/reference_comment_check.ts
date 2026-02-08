#!/usr/bin/env bun
/**
 * Edit時に「〜と同じ」参照スタイルのコメントを検出して警告。
 *
 * Why:
 *   「〜と同じ」「copied from 〜」などの参照コメントは、
 *   参照先が変更されると嘘になりやすい。コードの整合性が崩れる。
 *
 * What:
 *   - Editツール実行を検出
 *   - new_stringから参照スタイルのコメントパターンを検索
 *   - 検出時はsystemMessageで警告（importや理由説明への変更を推奨）
 *
 * Remarks:
 *   - 非ブロック型（警告のみ）
 *   - PreToolUse:Edit フック
 *   - .py, .ts, .tsx, .js, .jsx ファイルが対象
 *   - Python版: reference_comment_check.py
 *
 * Changelog:
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "reference-comment-check";

// Patterns that indicate reference-style comments (problematic)
const REFERENCE_PATTERNS = [
  // Japanese patterns
  /[#/]\s*[^\n]*と同じ/, // "〜と同じ" (same as ~)
  /[#/]\s*[^\n]*と共通/, // "〜と共通" (shared with ~)
  /[#/]\s*[^\n]*を参照/, // "〜を参照" (refer to ~)
  /[#/]\s*[^\n]*から(?:コピー|流用)/, // "〜からコピー/流用"
  // English patterns
  /[#/]\s*[^\n]*(?:same\s+as)\s+\S+\.(?:py|ts|js|tsx|jsx)/i, // "same as file.py"
  /[#/]\s*[^\n]*(?:copied\s+from)\s+\S+\.(?:py|ts|js|tsx|jsx)/i, // "copied from file.py"
  /[#/]\s*[^\n]*(?:see|refer\s+to)\s+\S+\.(?:py|ts|js|tsx|jsx)/i, // "see file.py"
];

// File extensions to check
const CHECKABLE_EXTENSIONS = new Set([".py", ".ts", ".tsx", ".js", ".jsx"]);

/**
 * Check if the file should be checked for reference comments.
 */
export function shouldCheckFile(filePath: string): boolean {
  const ext = filePath.slice(filePath.lastIndexOf("."));
  return CHECKABLE_EXTENSIONS.has(ext);
}

/**
 * Find reference-style comments in the given text.
 */
export function findReferenceComments(text: string): string[] {
  const matches: string[] = [];

  for (const pattern of REFERENCE_PATTERNS) {
    // Reset lastIndex for global patterns
    const globalPattern = new RegExp(pattern.source, `${pattern.flags}g`);

    for (let match = globalPattern.exec(text); match !== null; match = globalPattern.exec(text)) {
      // Extract the full comment line
      const start = text.lastIndexOf("\n", match.index) + 1;
      let end = text.indexOf("\n", match.index + match[0].length);
      if (end === -1) {
        end = text.length;
      }
      const commentLine = text.slice(start, end).trim();
      if (commentLine && !matches.includes(commentLine)) {
        matches.push(commentLine);
      }
    }
  }

  return matches;
}

/**
 * Format the warning message for detected reference comments.
 */
export function formatWarningMessage(matches: string[], filePath: string): string {
  const lines = [
    "⚠️ **参照スタイルのコメントを検出しました**",
    "",
    `ファイル: \`${filePath}\``,
    "",
    "検出されたコメント:",
  ];

  for (const match of matches.slice(0, 5)) {
    lines.push(`  - \`${match}\``);
  }

  lines.push(
    "",
    "**問題点**: 参照先のコードが変更されると、コメントが嘘になります。",
    "",
    "**推奨される対応**:",
    "1. 同じ値を使うなら `import` で共有する",
    "2. 「なぜこの値か」を説明するコメントに変更する",
    "",
    "例: `# タイムアウトは10秒（ネットワーク遅延を考慮）`",
  );

  return lines.join("\n");
}

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    const ctx = createHookContext(inputData);
    sessionId = ctx.sessionId;
    const toolName = inputData.tool_name ?? "";
    const toolInput = (inputData.tool_input as Record<string, unknown>) ?? {};

    // Only check Edit operations
    if (toolName !== "Edit") {
      console.log(JSON.stringify(result));
      return;
    }

    const filePath = (toolInput.file_path as string) ?? "";
    const newString = (toolInput.new_string as string) ?? "";

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

    // Skip if no new content
    if (!newString) {
      console.log(JSON.stringify(result));
      return;
    }

    // Find reference comments
    const matches = findReferenceComments(newString);

    if (matches.length > 0) {
      result.systemMessage = formatWarningMessage(matches, filePath);
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `warning: found ${matches.length} reference comments`,
        {
          file: filePath,
          matches,
        },
        { sessionId },
      );
    } else {
      await logHookExecution(HOOK_NAME, "approve", "no reference comments found", undefined, {
        sessionId,
      });
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
