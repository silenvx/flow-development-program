#!/usr/bin/env bun
/**
 * 新規フック作成時に類似コードを検索して参考情報を提供。
 *
 * Why:
 *   フック実装時に既存のパターンを知らずに独自実装すると、一貫性が失われ
 *   レビューで指摘される。類似コードを事前に提示することで品質を向上させる。
 *
 * What:
 *   - フックファイル（.claude/hooks/*.py, .claude/hooks/*.ts）へのWrite/Edit時に発火
 *   - 新しい関数定義（def xxx, function xxx）を抽出
 *   - 既存フックから類似パターン（has_skip_, check_, get_等）を検索
 *   - 見つかった場合はsystemMessageで参照ファイルを提示
 *
 * Remarks:
 *   - 非ブロック型（情報提供のみ）
 *   - existing-impl-checkはworktree作成時、本フックはWrite/Edit時
 *   - 検索パターンは SEARCH_PATTERNS で定義
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2917: TypeScriptに移植
 */

import { spawn } from "node:child_process";
import { TIMEOUT_LIGHT } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { outputResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "similar-code-check";

// Patterns to search for similar implementations
const SEARCH_PATTERNS: Record<string, string> = {
  has_skip_: "スキップ判定関数（環境変数チェック等）",
  "is_.*_command": "コマンド判定関数",
  check_: "検証/チェック関数",
  get_: "データ取得関数",
  extract_: "データ抽出関数",
  format_: "フォーマット関数",
  parse_: "パース関数",
  hasSkip: "スキップ判定関数（TypeScript）",
  "is.*Command": "コマンド判定関数（TypeScript）",
};

interface SpawnResult {
  stdout: string;
  exitCode: number | null;
}

async function runCommand(
  command: string,
  args: string[],
  options: { timeout?: number; cwd?: string } = {},
): Promise<SpawnResult> {
  const { timeout = TIMEOUT_LIGHT, cwd } = options;

  return new Promise((resolve) => {
    const proc = spawn(command, args, {
      cwd,
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let killed = false;

    const timer = setTimeout(() => {
      killed = true;
      proc.kill("SIGTERM");
    }, timeout * 1000);

    proc.stdout?.on("data", (data) => {
      stdout += data.toString();
    });

    proc.on("close", (exitCode) => {
      clearTimeout(timer);
      if (killed) {
        resolve({ stdout: "", exitCode: null });
      } else {
        resolve({ stdout, exitCode });
      }
    });

    proc.on("error", () => {
      clearTimeout(timer);
      resolve({ stdout: "", exitCode: null });
    });
  });
}

/**
 * Get the repository root directory.
 */
function getRepoRoot(): string | null {
  const proj = process.env.CLAUDE_PROJECT_DIR;
  if (proj) {
    return proj;
  }
  return null;
}

/**
 * Check if the file is a hook file (Python or TypeScript).
 */
export function isHookFile(filePath: string): boolean {
  if (!filePath) {
    return false;
  }
  // Match .claude/hooks/*.py or .claude/hooks/**/*.ts but not tests
  return (
    filePath.includes(".claude/hooks/") &&
    (filePath.endsWith(".py") || filePath.endsWith(".ts")) &&
    !filePath.includes("/tests/")
  );
}

/**
 * Extract function definitions from content.
 */
export function extractFunctionNames(content: string): string[] {
  if (!content) {
    return [];
  }

  const names: string[] = [];

  // Python: def function_name(
  const pyPattern = /^def\s+([a-z_][a-z0-9_]*)\s*\(/gm;
  for (let match = pyPattern.exec(content); match !== null; match = pyPattern.exec(content)) {
    names.push(match[1]);
  }

  // TypeScript: function functionName( or async function functionName(
  const tsPattern = /^(?:async\s+)?function\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(/gm;
  for (let match = tsPattern.exec(content); match !== null; match = tsPattern.exec(content)) {
    names.push(match[1]);
  }

  return names;
}

/**
 * Search for similar function patterns in existing hooks.
 */
async function searchSimilarFunctions(functionNames: string[]): Promise<Record<string, string[]>> {
  const results: Record<string, string[]> = {};

  const repoRoot = getRepoRoot();
  if (!repoRoot) {
    return results;
  }

  for (const funcName of functionNames) {
    for (const [pattern, description] of Object.entries(SEARCH_PATTERNS)) {
      const regex = new RegExp(pattern);
      if (regex.test(funcName)) {
        try {
          // Search for existing functions with this pattern
          const grepResult = await runCommand(
            "git",
            ["grep", "-E", "-l", `def ${pattern}|function ${pattern}`, "--", ".claude/hooks/"],
            { cwd: repoRoot, timeout: TIMEOUT_LIGHT },
          );

          if (grepResult.exitCode === 0 && grepResult.stdout.trim()) {
            const files = grepResult.stdout.trim().split("\n");
            const key = `\`${funcName}\` (${description})`;
            if (!results[key]) {
              results[key] = [];
            }
            for (const f of files.slice(0, 5)) {
              if (f && !results[key].includes(f)) {
                results[key].push(f);
              }
            }
          }
        } catch {
          // Fail-open: continue on error
        }
      }
    }
  }

  return results;
}

/**
 * Format search results as a systemMessage.
 */
export function formatSuggestions(similar: Record<string, string[]>): string {
  if (Object.keys(similar).length === 0) {
    return "";
  }

  const lines: string[] = ["💡 **類似コードが見つかりました** - 一貫性のため参考にしてください:\n"];

  for (const [patternDesc, files] of Object.entries(similar)) {
    lines.push(`\n**${patternDesc}**:`);
    for (const f of files) {
      lines.push(`  - \`${f}\``);
    }
  }

  lines.push(
    "\n\n既存実装を参考にすることで、レビュー指摘を事前に防ぎ、一貫性のあるコードベースを維持できます。",
  );

  return lines.join("\n");
}

async function main(): Promise<void> {
  const result: { decision?: "block"; systemMessage?: string } = {};
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;
    const toolInput = (inputData.tool_input as Record<string, unknown>) ?? {};
    const filePath = (toolInput.file_path as string) ?? "";
    // Handle both Write (content) and Edit (new_string) tool inputs
    const content = (toolInput.content as string) ?? (toolInput.new_string as string) ?? "";

    // Only process hook files
    if (isHookFile(filePath)) {
      const funcNames = extractFunctionNames(content);

      if (funcNames.length > 0) {
        const similar = await searchSimilarFunctions(funcNames);

        if (Object.keys(similar).length > 0) {
          result.systemMessage = formatSuggestions(similar);
        }
      }
    }
  } catch (e) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(e)}`);
  }

  await logHookExecution(HOOK_NAME, result.decision ?? "approve", undefined, undefined, {
    sessionId,
  });
  outputResult(result);
}

if (import.meta.main) {
  main();
}
