#!/usr/bin/env bun
/**
 * gh pr mergeでの--bodyオプション使用をブロックする。
 *
 * Why:
 *   --bodyオプションはPRの詳細な説明（背景、変更内容等）を短い要約で
 *   上書きしてしまい、コミット履歴から有用な情報が失われる。
 *
 * What:
 *   - gh pr mergeコマンドを検出
 *   - --body/-bオプションが使用されていたらブロック
 *   - PRボディ更新後にマージする正しい方法を案内
 *
 * Remarks:
 *   - ブロック型フック（--body使用時はブロック）
 *   - PreToolUse:Bashで発火（gh pr mergeコマンド）
 *   - pr-body-quality-check.tsはPR作成時（責務分離）
 *   - 正しい方法: gh pr edit → gh pr merge --squash
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2874: TypeScript移行
 *   - silenvx/dekita#3234: isSingleGhPrMergeをparseGhPrCommandに統合
 */

import { formatError } from "../lib/format_error";
import { parseGhPrCommand } from "../lib/github";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { splitCommandChain, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "merge-commit-quality-check";

/**
 * Check if a single command segment is `gh pr merge`.
 *
 * Uses parseGhPrCommand from lib/github.ts for robust command parsing.
 * This handles:
 * - Wrapper commands (sudo, env, time, etc.)
 * - Path-invoked gh (/usr/bin/gh)
 * - Quoted strings and heredocs
 *
 * @param command - A single command segment (not a chain)
 * @returns true if the command is gh pr merge
 */
function isSingleGhPrMerge(command: string): boolean {
  try {
    const [subcommand] = parseGhPrCommand(command.trim());
    return subcommand === "merge";
  } catch {
    return false;
  }
}

/**
 * Check if command contains a gh pr merge command.
 *
 * Handles chained commands (&&, ||, ;) and multi-line commands by
 * splitting and checking each part with parseGhPrCommand.
 *
 * @example
 * isGhPrMergeCommand("gh pr merge 123") // true
 * isGhPrMergeCommand("gh pr create --body '...'") // false
 * isGhPrMergeCommand("gh pr view && gh pr merge") // true
 * isGhPrMergeCommand("sudo gh pr merge 123") // true
 * isGhPrMergeCommand("/usr/bin/gh pr merge 123") // true
 */
export function isGhPrMergeCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }

  const strippedForSplit = stripQuotedStrings(command);
  const normalizedCommand = strippedForSplit.replace(/\\\r?\n/g, " ");
  const commands = normalizedCommand.split(/\n/).flatMap((line) => splitCommandChain(line));

  return commands.some(isSingleGhPrMerge);
}

/**
 * Check if command has --body or -b option.
 */
export function hasBodyOption(command: string): boolean {
  // Don't strip quoted strings - we need to check if body option exists
  return /(?:--body\b|-b\b)/.test(command);
}

/**
 * Format the block message for --body usage.
 */
export function formatBlockMessage(): string {
  const lines: string[] = [
    "🚫 Using --body option with gh pr merge is prohibited",
    "",
    "**Reason:**",
    "- `--body` overwrites the PR's detailed description (## Why, ## What, etc.)",
    "- Useful information is lost from commit history",
    "",
    "**Correct approach:**",
    "```bash",
    "# Update PR body (if needed)",
    `gh pr edit {PR} --body "$(cat <<'EOF'`,
    "## Why",
    "Describe the background/motivation",
    "",
    "## What",
    "Summary of changes",
    "",
    "Closes #XXX",
    "EOF",
    `)"`,
    "",
    "# Merge (without --body)",
    "gh pr merge {PR} --squash --delete-branch",
    "```",
    "",
    "**Reference:** managing-development Skill, squash merge section",
  ];
  return lines.join("\n");
}

interface HookResult {
  decision?: string;
  reason?: string;
  systemMessage?: string;
}

async function main(): Promise<void> {
  let result: HookResult = {};
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);
    sessionId = ctx.sessionId;
    const toolInput = data.tool_input || {};
    const command = (toolInput as { command?: string }).command || "";

    if (isGhPrMergeCommand(command)) {
      if (hasBodyOption(command)) {
        const reason = formatBlockMessage();
        result = makeBlockResult(HOOK_NAME, reason);
      } else {
        result.systemMessage =
          "✅ merge-commit-quality-check: No --body option (PR body will be used as commit message)";
      }
    }
  } catch (e) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(e)}`);
    result = {};
  }

  // Log only for non-block decisions (makeBlockResult logs automatically)
  if (result.decision !== "block") {
    await logHookExecution(HOOK_NAME, result.decision ?? "approve", result.reason, undefined, {
      sessionId,
    });
  }
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
