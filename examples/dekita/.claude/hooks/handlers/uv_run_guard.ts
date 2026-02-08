#!/usr/bin/env bun
/**
 * worktree内でのuv run使用を防止
 *
 * Why:
 *   worktreeにはpyproject.tomlがシンボリックリンクされないため、
 *   uv runはエラーになる。事前にブロックして代替コマンドを提示する。
 *
 * What:
 *   - Bashコマンド実行前（PreToolUse:Bash）に発火
 *   - cwdが.worktrees/内かを確認
 *   - uv runコマンドを検出したらブロック
 *   - 代替としてuvxコマンドを提案
 *
 * Remarks:
 *   - ブロック型フック（worktree内のuv runはブロック）
 *   - ツール名を抽出してuvx推奨コマンドを提示
 *   - Python版: uv_run_guard.py
 *
 * Changelog:
 *   - silenvx/dekita#2814: TypeScript版初期実装
 *   - silenvx/dekita#2894: isInWorktreeをlib/git.tsから使用
 */

import { formatError } from "../lib/format_error";
import { isInWorktree } from "../lib/git";
import { parseHookInput } from "../lib/session";

/** Pattern to detect 'uv run' commands */
export const UV_RUN_PATTERN = /\buv\s+run\b/;

/**
 * Extract the tool name from 'uv run <tool>' command
 *
 * @example
 * extractToolFromUvRun('uv run ruff check .') // 'ruff'
 * extractToolFromUvRun('uv run python -m pytest') // 'python'
 * extractToolFromUvRun('uv run --with foo bar') // 'bar'
 */
export function extractToolFromUvRun(command: string): string | null {
  // Remove 'uv run' prefix (trailing whitespace optional)
  const remaining = command.replace(/^\s*uv\s+run(?:\s+|$)/, "");

  // Options that take a value (the next element is the value)
  const valueOptions = new Set(["--with", "--python"]);

  const parts = remaining.split(/\s+/).filter((p) => p !== "");
  let i = 0;

  while (i < parts.length) {
    const part = parts[i];

    // Handle options
    if (part.startsWith("-")) {
      // --opt=value format: skip this element only
      if (part.includes("=")) {
        i += 1;
        continue;
      }

      // Known options that take a value: skip option and its value
      if (valueOptions.has(part)) {
        i = i + 1 < parts.length ? i + 2 : parts.length;
        continue;
      }

      // Other options: skip option only
      i += 1;
      continue;
    }

    // First non-option is the tool name
    return part || null;
  }

  return null;
}

/**
 * メイン処理
 */
async function main(): Promise<void> {
  const input = await parseHookInput();
  const toolInput = input.tool_input as Record<string, unknown> | undefined;
  const command = (toolInput?.command as string) ?? "";

  // Only check if in worktree and command contains 'uv run'
  if (isInWorktree() && UV_RUN_PATTERN.test(command)) {
    const toolName = extractToolFromUvRun(command);

    let suggestion = "";
    if (toolName) {
      suggestion = `\n\n推奨コマンド: \`uvx ${toolName} ...\``;
    }

    const result = {
      allow: false,
      reason: `# uv run はworktree内で使用できません\n\nworktreeにはpyproject.tomlがシンボリックリンクされていないため、\`uv run\` はエラーになります。\n\n代わりに \`uvx\` を使用してください。${suggestion}`,
    };

    console.log(JSON.stringify(result));
    return;
  }

  console.log(JSON.stringify({ allow: true }));
}

if (import.meta.main) {
  main().catch((error) => {
    console.error(`[uv-run-guard] Error: ${formatError(error)}`);
    // Hooks should not block on internal errors - output allow response
    console.log(JSON.stringify({ allow: true }));
    process.exit(0);
  });
}
