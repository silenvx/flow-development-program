#!/usr/bin/env bun
/**
 * セッション再開時に競合状況警告を表示。
 *
 * Why:
 *   --resume/--continue/--fork-sessionでセッションを再開すると、
 *   元セッションと重複作業してしまうリスクがある。既存worktreeや
 *   オープンPRの一覧を表示して、競合を早期に認識させる。
 *
 * What:
 *   - セッション開始時（SessionStart）に発火
 *   - sourceがresume/compactの場合のみ処理
 *   - 既存worktree一覧を取得
 *   - オープンPR一覧を取得
 *   - 競合リスクの警告メッセージを表示
 *
 * Remarks:
 *   - 非ブロック型（情報表示のみ）
 *   - session-handoff-readerは引き継ぎ情報、本フックは競合警告
 *   - fork-session判定はClaudeがコンテキスト内で実施
 *   - Python版: session_resume_warning.py
 *
 * Changelog:
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { spawnSync } from "node:child_process";
import { basename } from "node:path";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "session-resume-warning";

/**
 * Get list of existing worktrees (excluding main).
 * Returns worktrees in .worktrees directory, including detached HEAD state.
 */
function getWorktreeList(): string[] {
  try {
    const result = spawnSync("git", ["worktree", "list", "--porcelain"], {
      encoding: "utf-8",
      timeout: 5000,
    });

    if (result.status !== 0 || !result.stdout) {
      return [];
    }

    const output = result.stdout.trim();
    if (!output) {
      return [];
    }

    const worktrees: string[] = [];
    let currentWorktree: string | null = null;
    let currentBranch: string | null = null;

    for (const line of output.split("\n")) {
      if (line.startsWith("worktree ")) {
        // Save previous worktree if it was in .worktrees/
        if (currentWorktree?.includes(".worktrees/")) {
          const worktreeName = basename(currentWorktree);
          const branchInfo = currentBranch ?? "HEAD detached";
          worktrees.push(`  - ${worktreeName} (${branchInfo})`);
        }
        // Start tracking new worktree
        currentWorktree = line.slice(9);
        currentBranch = null;
      } else if (line.startsWith("branch refs/heads/")) {
        // refs/heads/ プレフィックスを除去してブランチ名のみを取得
        currentBranch = line.slice(18);
      }
    }

    // Handle last worktree
    if (currentWorktree?.includes(".worktrees/")) {
      const worktreeName = basename(currentWorktree);
      const branchInfo = currentBranch ?? "HEAD detached";
      worktrees.push(`  - ${worktreeName} (${branchInfo})`);
    }

    return worktrees;
  } catch {
    return [];
  }
}

/**
 * Get list of open PRs.
 */
function getOpenPRs(): string[] {
  try {
    const result = spawnSync(
      "gh",
      [
        "pr",
        "list",
        "--state",
        "open",
        "--json",
        "number,headRefName,title",
        "--jq",
        '.[] | "  - #\\(.number) \\(.headRefName): \\(.title)"',
      ],
      {
        encoding: "utf-8",
        timeout: 10000,
      },
    );

    if (result.status !== 0 || !result.stdout) {
      return [];
    }

    return result.stdout
      .trim()
      .split("\n")
      .filter((line) => line);
  } catch {
    return [];
  }
}

/**
 * Format the session resume warning message with context.
 */
export function formatResumeSessionMessage(worktrees: string[], openPRs: string[]): string {
  const messageParts = [
    "🔄 **セッション再開検出**\n",
    "このセッションは以前の会話から再開されました。",
    "**作業開始前に競合状況を確認してください**:\n",
  ];

  // Add worktree information
  if (worktrees.length > 0) {
    messageParts.push("**既存Worktree** (別セッションが作業中の可能性):");
    messageParts.push(...worktrees);
    messageParts.push("");
  } else {
    messageParts.push("**既存Worktree**: なし");
    messageParts.push("");
  }

  // Add open PR information
  if (openPRs.length > 0) {
    messageParts.push("**オープンPR** (介入禁止):");
    messageParts.push(...openPRs);
    messageParts.push("");
  } else {
    messageParts.push("**オープンPR**: なし");
    messageParts.push("");
  }

  // Add reminder
  messageParts.push(
    "⚠️ **AGENTS.md原則**:",
    "- Issue作業開始前に既存worktree/PRを確認",
    "- オープンPRがあるIssueには介入禁止",
    "- 競合リスクがある場合はユーザーに確認",
  );

  return messageParts.join("\n");
}

async function main(): Promise<void> {
  const result: { continue: boolean; message?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    const hookInput = await parseHookInput();
    sessionId = hookInput.session_id;
    const source = hookInput.source ?? "";

    // source が "resume" または "compact" の場合に警告を表示
    if (source === "resume" || source === "compact") {
      const worktrees = getWorktreeList();
      const openPRs = getOpenPRs();

      result.message = formatResumeSessionMessage(worktrees, openPRs);

      await logHookExecution(
        HOOK_NAME,
        "approve",
        `resume warning displayed (worktrees=${worktrees.length}, prs=${openPRs.length})`,
        {
          source,
          worktree_count: worktrees.length,
          open_pr_count: openPRs.length,
        },
        { sessionId },
      );
    } else {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Not a resume session (source=${source})`,
        undefined,
        { sessionId },
      );
    }
  } catch (error) {
    await logHookExecution(HOOK_NAME, "approve", `Error: ${formatError(error)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

// Only run main when executed directly, not when imported
if (import.meta.main) {
  main();
}
