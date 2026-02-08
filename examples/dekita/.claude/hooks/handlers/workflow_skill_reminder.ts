#!/usr/bin/env bun
/**
 * worktree作成・PR作成時にmanaging-development Skillを参照するようリマインド。
 *
 * Why:
 *   AIエージェントはセッション間で学習しないため「手順は身についている」は誤り。
 *   常にSkillを参照することで、手順の見落としを防ぐ。
 *
 * What:
 *   - Bashコマンド実行前（PreToolUse:Bash）に発火
 *   - git worktree add / gh pr create を検出
 *   - managing-development Skill参照のリマインダーを表示
 *   - チェックリスト付きのメッセージで確認事項を提示
 *
 * Remarks:
 *   - 警告型フック（systemMessage、ブロックしない）
 *   - hook-change-detectorはフックファイル変更、本フックはワークフロー操作
 *   - Issue #2387: 「手順が身についている」思考を防止
 *
 * Changelog:
 *   - silenvx/dekita#2387: フック追加
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";
import { splitCommandChain, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "workflow-skill-reminder";

/**
 * Check if command contains git worktree add.
 *
 * Handles command chains like:
 * - git worktree add .worktrees/xxx -b branch
 * - SKIP_PLAN=1 git worktree add ...
 */
export function isWorktreeAddCommand(command: string): boolean {
  const stripped = stripQuotedStrings(command);
  const subcommands = splitCommandChain(stripped);
  for (const subcmd of subcommands) {
    // Match: optional env vars, then git worktree add
    if (/(?:^|\s)git\s+worktree\s+add(?:\s|$)/.test(subcmd)) {
      return true;
    }
  }
  return false;
}

/**
 * Check if command contains gh pr create.
 *
 * Handles command chains like:
 * - gh pr create --title "..."
 * - git push && gh pr create
 */
export function isPrCreateCommand(command: string): boolean {
  const stripped = stripQuotedStrings(command);
  const subcommands = splitCommandChain(stripped);
  for (const subcmd of subcommands) {
    if (/(?:^|\s)gh\s+pr\s+create(?:\s|$)/.test(subcmd)) {
      return true;
    }
  }
  return false;
}

function buildWorktreeSkillReminder(): string {
  return (
    "📚 workflow-skill-reminder: worktree作成が検出されました。\n\n" +
    "【managing-development Skill 参照リマインダー】\n" +
    "worktree作成時は `managing-development` Skill を参照してください。\n\n" +
    "**確認すべき内容:**\n" +
    "□ worktree作成直後のチェック（main最新との差分確認）\n" +
    "□ `--lock` オプションの使用（他エージェントの削除防止）\n" +
    "□ ブランチ命名規則（`feat/issue-123-desc`）\n" +
    "□ setup_worktree.sh の実行\n\n" +
    "**Skill呼び出し方法:**\n" +
    "  /managing-development\n\n" +
    "💡 「単純な作業だからSkill不要」は誤った判断です。\n" +
    "   AIエージェントはセッション間で学習しないため、常にSkillを参照してください。"
  );
}

function buildPrCreateSkillReminder(): string {
  return (
    "📚 workflow-skill-reminder: PR作成が検出されました。\n\n" +
    "【managing-development Skill 参照リマインダー】\n" +
    "PR作成時は `managing-development` Skill を参照してください。\n\n" +
    "**確認すべき内容:**\n" +
    "□ ローカルテスト・Lintの実行（PR作成前必須）\n" +
    "□ Codexレビューの実行（`codex review --base main`）\n" +
    "□ コミットメッセージ規約（背景/Whyを含める）\n" +
    "□ UI変更時はスクリーンショット必須\n\n" +
    "**Skill呼び出し方法:**\n" +
    "  /managing-development\n\n" +
    "💡 「単純な変更だからSkill不要」は誤った判断です。\n" +
    "   既存パターンを見落とすリスクを回避するため、常に参照してください。"
  );
}

interface HookResult {
  decision?: string;
  reason?: string;
  systemMessage?: string;
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = data.tool_input || {};
    const command = (toolInput as { command?: string }).command || "";

    if (!command) {
      // No command, nothing to check
      console.log(JSON.stringify({}));
      return;
    }

    const warnings: string[] = [];

    // Check for worktree add
    if (isWorktreeAddCommand(command)) {
      warnings.push(buildWorktreeSkillReminder());
      await logHookExecution(
        HOOK_NAME,
        "approve",
        undefined,
        {
          command_type: "worktree_add",
          warning: "skill_reminder",
        },
        { sessionId },
      );
    }

    // Check for PR create
    if (isPrCreateCommand(command)) {
      warnings.push(buildPrCreateSkillReminder());
      await logHookExecution(
        HOOK_NAME,
        "approve",
        undefined,
        {
          command_type: "pr_create",
          warning: "skill_reminder",
        },
        { sessionId },
      );
    }

    // Return with warnings if any
    if (warnings.length > 0) {
      const combinedWarning = warnings.join("\n\n---\n\n");
      const result: HookResult = {
        systemMessage: combinedWarning,
      };
      console.log(JSON.stringify(result));
      return;
    }

    // No relevant commands detected
    console.log(JSON.stringify({}));
  } catch (e) {
    // On error, approve to avoid blocking
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(e)}`);
    const result: HookResult = {
      reason: `Hook error: ${formatError(e)}`,
    };
    console.log(JSON.stringify(result));
  }
}

if (import.meta.main) {
  main();
}
