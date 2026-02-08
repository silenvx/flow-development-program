#!/usr/bin/env bun
/**
 * git branch -m/-Mコマンド（ブランチリネーム）をブロックする。
 *
 * Why:
 *   ブランチリネームはmain/masterのgit設定破損、リモートとの不整合、
 *   他のセッションやCIとの競合を引き起こす可能性がある。
 *
 * What:
 *   - git branch -m/-M/--moveコマンドを検出
 *   - 検出時にブロック
 *   - 意図的なリネーム用のスキップ方法を提示
 *
 * Remarks:
 *   - ブロック型フック（ブランチリネームはブロック）
 *   - SKIP_BRANCH_RENAME=1で意図的なリネームを許可
 *   - PreToolUse:Bashで発火
 *
 * Changelog:
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import {
  checkSkipEnv,
  createHookContext,
  extractInputContext,
  logHookExecution,
  makeBlockResult,
  mergeDetailsWithContext,
  parseHookInput,
  stripQuotedStrings,
} from "../lib";

const HOOK_NAME = "branch-rename-guard";

// Pattern to match git global options that can appear between 'git' and the subcommand
// Examples: -C <path>, -C<path>, --git-dir=<path>, --git-dir <path>, -c <key>=<value>
const GIT_GLOBAL_OPTIONS =
  /(?:\s+(?:-[CcOo]\s*\S+|--[\w-]+=\S+|--[\w-]+\s+(?!branch\b)\S+|--[\w-]+|-[pPhv]|-\d+))*/;

// Pattern to match branch options that can appear between 'branch' and '-m/-M/--move'
// Examples: --color, --no-color, --list, -v, -vv, -f, --force, --color=always, --sort=-date
const BRANCH_OPTIONS = /(?:\s+(?:--[\w-]+=\S*|--[\w-]+|-[vVqarlf]+))*/;

// Pattern to match the rename flag itself
// Supports:
// - '-m', '-M' (simple case)
// - '-fm', '-fM', '-afm' (combined flags like 'git branch -fm old new')
// - '--move' (long form)
const RENAME_FLAG = /(?:-[a-zA-Z]*[mM]|--move)/;

/**
 * Check if command contains a branch rename operation.
 *
 * @param command - Command to check
 * @returns Tuple of [isRename, targetBranch]
 */
export function checkBranchRename(command: string): [boolean, string | null] {
  // Remove quoted strings to avoid false positives
  const stripped = stripQuotedStrings(command);

  // Build the full pattern
  const pattern = new RegExp(
    `\\bgit${GIT_GLOBAL_OPTIONS.source}\\s+branch${BRANCH_OPTIONS.source}\\s+${RENAME_FLAG.source}`,
  );

  if (pattern.test(stripped)) {
    // Extract target branch name if possible
    const extractPattern = new RegExp(
      `\\bgit${GIT_GLOBAL_OPTIONS.source}\\s+branch${BRANCH_OPTIONS.source}\\s+${RENAME_FLAG.source}\\s+(\\S+)`,
    );
    const match = stripped.match(extractPattern);
    const target = match ? match[1] : null;
    return [true, target];
  }

  return [false, null];
}

async function main(): Promise<void> {
  const hookInput = await parseHookInput();
  const inputContext = extractInputContext(hookInput);
  const ctx = createHookContext(hookInput);

  // Only check Bash commands
  if (hookInput.tool_name !== "Bash") {
    console.log(JSON.stringify({}));
    return;
  }

  const toolInput = hookInput.tool_input || {};
  const command = (toolInput.command as string) || "";

  // Check skip environment variable
  if (checkSkipEnv(HOOK_NAME, "SKIP_BRANCH_RENAME_GUARD", inputContext)) {
    console.log(JSON.stringify({}));
    return;
  }

  const [isRename, targetBranch] = checkBranchRename(command);

  if (!isRename) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      "Not a branch rename command",
      mergeDetailsWithContext(null, inputContext),
      { sessionId: ctx.sessionId ?? undefined },
    );
    console.log(JSON.stringify({}));
    return;
  }

  // Block the rename
  const targetInfo = targetBranch ? `（対象: ${targetBranch}）` : "";
  const reason = `ブランチリネーム操作をブロックしました${targetInfo}

**理由:**
ブランチリネームは以下の問題を引き起こす可能性があります:
- main/masterのリネームによるgit設定の破損
- リモートとの不整合
- 他のセッションやCIとの競合

**意図的にリネームする場合:**
\`\`\`bash
SKIP_BRANCH_RENAME_GUARD=1 ${command}
\`\`\`

詳細: Issue #996`;

  await logHookExecution(
    HOOK_NAME,
    "block",
    `Branch rename blocked: ${targetBranch || "unknown"}`,
    mergeDetailsWithContext({ target_branch: targetBranch }, inputContext),
    { sessionId: ctx.sessionId ?? undefined },
  );

  const result = makeBlockResult(HOOK_NAME, reason, ctx);
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
