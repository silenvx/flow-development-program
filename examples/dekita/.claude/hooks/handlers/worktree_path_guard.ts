#!/usr/bin/env bun
/**
 * worktree作成先が.worktrees/内かを検証。
 *
 * Why:
 *   worktreeがファイルシステム全体に散らばると管理が困難になる。
 *   .worktrees/に集約することで一覧確認が容易になり、他エージェントとの競合も避けられる。
 *
 * What:
 *   - git worktree addコマンド実行前（PreToolUse:Bash）に発火
 *   - パス引数を抽出して.worktrees/配下かを検証
 *   - 絶対パスや..を使った迂回パスもブロック
 *   - リポジトリルート以外からの相対パス指定もブロック
 *   - 正しい使い方を提示
 *
 * Remarks:
 *   - ブロック型フック（.worktrees/外への作成はブロック）
 *   - worktree-main-freshness-checkはmain最新確認、本フックはパス検証
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2815: リポジトリルート以外からの作成をブロック
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { normalize } from "node:path";
import { getEffectiveCwd } from "../lib/cwd";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "worktree-path-guard";

/**
 * Get the repository root directory.
 *
 * Returns the path from CLAUDE_PROJECT_DIR, or null if not set.
 */
function getRepoRoot(): string | null {
  const envDir = process.env.CLAUDE_PROJECT_DIR;
  return envDir || null;
}

/**
 * Check if the effective working directory is the repository root.
 */
function isInRepoRoot(cwd: string | null, command: string | null = null): boolean {
  const repoRoot = getRepoRoot();
  if (!repoRoot) {
    // Cannot determine repo root without CLAUDE_PROJECT_DIR, fail-open
    return true;
  }

  if (!cwd) {
    return true; // If no cwd, assume repo root (conservative)
  }

  // Use getEffectiveCwd to handle cd patterns in command
  const effectiveCwd = getEffectiveCwd(command || undefined, cwd);

  return effectiveCwd === repoRoot;
}

/**
 * Check if the worktree path is under .worktrees/.
 *
 * This function handles path traversal attacks like `.worktrees/../foo`
 * by normalizing the path before checking.
 */
function isValidWorktreePath(pathArg: string): boolean {
  // Reject absolute paths immediately
  if (pathArg.startsWith("/")) {
    return false;
  }

  // Normalize the path to resolve .. and .
  // Use normalize to handle path traversal
  const normalized = normalize(pathArg);

  // After normalization, check if it still starts with .worktrees/
  // normalize will collapse .worktrees/../foo to just foo
  const parts = normalized.split("/").filter((p) => p !== "");
  if (parts.length >= 2 && parts[0] === ".worktrees") {
    // Must have at least .worktrees/something
    return true;
  }

  return false;
}

/**
 * Extract worktree path from git worktree add command.
 *
 * Handles various forms:
 * - git worktree add <path>
 * - git worktree add --lock <path>
 * - git worktree add <path> -b <branch>
 */
function extractWorktreeAddPath(command: string): string | null {
  // Pattern to match git worktree add with various options
  // Captures the path argument which comes after "worktree add" and optional flags
  const patterns = [
    // git worktree add [--lock] <path> [-b branch]
    /git\s+worktree\s+add\s+(?:--lock\s+)?([^\s-][^\s]*)/,
  ];

  for (const pattern of patterns) {
    const match = command.match(pattern);
    if (match) {
      return match[1];
    }
  }

  return null;
}

async function main(): Promise<void> {
  let result: {
    decision?: string;
    reason?: string;
    systemMessage?: string;
  } = {};

  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = data.tool_input || {};
    const command = (toolInput.command as string) || "";

    const worktreePath = extractWorktreeAddPath(command);

    if (worktreePath === null) {
      // Not a worktree add command, approve
      await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Check if path format is valid (.worktrees/xxx)
    if (!isValidWorktreePath(worktreePath)) {
      const reason = `worktreeは \`.worktrees/\` ディレクトリ内に作成してください。\n\n**検出されたパス:** \`${worktreePath}\`\n\n**正しい使い方:**\n\`\`\`bash\n# Issue番号を使った命名規則\ngit worktree add .worktrees/issue-123 feature/issue-123-description\n\n# または任意の名前\ngit worktree add .worktrees/my-feature feature/my-feature\n\`\`\`\n\n**理由:**\n- worktreeを一箇所に集約することで管理が容易になります\n- \`git worktree list\`で一覧を確認しやすくなります\n- 他のエージェントとの競合を避けられます`;
      result = makeBlockResult(HOOK_NAME, reason);
      await logHookExecution(HOOK_NAME, "block", `invalid path: ${worktreePath}`, undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Check if we're in repo root when using relative path
    if (!worktreePath.startsWith("/")) {
      const hookCwd = data.cwd as string | undefined;
      if (!isInRepoRoot(hookCwd || null, command)) {
        const effectiveCwd = getEffectiveCwd(command, hookCwd);
        const repoRoot = getRepoRoot();
        const reason = `リポジトリルート以外から相対パスでworktreeを作成しようとしています。\n\n**効果的なディレクトリ:** \`${effectiveCwd}\`\n**リポジトリルート:** \`${repoRoot}\`\n**指定されたパス:** \`${worktreePath}\`\n**実際の作成先:** \`${effectiveCwd}/${worktreePath}\`\n\n**対処方法:**\n\`\`\`bash\n# リポジトリルートに移動してから実行\ncd ${repoRoot}\ngit worktree add ${worktreePath} <branch>\n\`\`\`\n\n**理由:**\n相対パス \`.worktrees/xxx\` は現在のディレクトリからの相対で解決されます。\nリポジトリルート以外で実行すると、意図しない場所にworktreeが作成されます。`;
        result = makeBlockResult(HOOK_NAME, reason);
        await logHookExecution(
          HOOK_NAME,
          "block",
          `not in repo root: effective_cwd=${effectiveCwd}, repo_root=${repoRoot}`,
          undefined,
          { sessionId },
        );
        console.log(JSON.stringify(result));
        return;
      }
    }
  } catch (error) {
    console.error(`[worktree-path-guard] Hook error: ${formatError(error)}`);
    result = { reason: `Hook error: ${formatError(error)}` };
  }

  const decision = result.decision ?? (result.reason ? "error" : "approve");
  await logHookExecution(HOOK_NAME, decision, result.reason, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(result));
}

// Only run main when executed directly, not when imported
if (import.meta.main) {
  main();
}

// Export for testing
export { isValidWorktreePath, extractWorktreeAddPath, isInRepoRoot };
