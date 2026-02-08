#!/usr/bin/env bun
/**
 * 繰り返し発生する問題を検出し、Issue作成を強制してからマージを許可。
 *
 * Why:
 *   同じフックで何度もブロックされる場合、根本的な改善が必要。
 *   Issue作成を強制することで、問題を放置せず仕組み化を促す。
 *
 * What:
 *   - gh pr merge コマンドを検出
 *   - 過去7日間のフック実行ログを集計
 *   - 3セッション以上で3回以上ブロックされたフックを検出
 *   - 該当する[改善]Issueがなければブロック
 *
 * State:
 *   - reads: .claude/logs/execution/hook-execution-*.jsonl
 *
 * Remarks:
 *   - ブロック型フック
 *   - PROTECTIVE_HOOKSは繰り返しブロック対象外
 *   - Issue作成後はブロック解除
 *   - Python版: recurring_problem_block.py
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { EXECUTION_LOG_DIR, TIMEOUT_MEDIUM } from "../lib/common";
import { formatError } from "../lib/format_error";
import { logHookExecution, readAllSessionLogEntries } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "recurring-problem-block";

// Configuration
const RECURRING_THRESHOLD = 3; // Block if detected in 3+ sessions
const RECURRING_DAYS = 7; // Look back 7 days
const BLOCK_COUNT_THRESHOLD = 3; // Consider it a problem if blocked 3+ times in a session

// Hooks that indicate workflow problems when blocked repeatedly
// Currently empty - all workflow hooks have been moved to PROTECTIVE_HOOKS
const WORKFLOW_PROBLEM_HOOKS = new Set<string>();

// Protective hooks - blocks are expected and not workflow problems
const PROTECTIVE_HOOKS = new Set([
  "codex-review-check",
  "worktree-session-guard",
  "worktree-removal-check",
  "locked-worktree-guard",
  "ci-wait-check",
  "resolve-thread-guard",
  "related-task-check",
  "flow-effect-verifier",
  "planning-enforcement",
  "worktree-warning",
]);

interface BlockingProblem {
  source: string;
  count: number;
}

/**
 * Count sessions where each hook repeatedly blocked.
 */
export async function aggregateRecurringProblems(
  days: number = RECURRING_DAYS,
): Promise<Record<string, number>> {
  const entries = await readAllSessionLogEntries(EXECUTION_LOG_DIR, "hook-execution");
  if (entries.length === 0) {
    return {};
  }

  const cutoff = new Date(Date.now() - days * 24 * 60 * 60 * 1000);

  // Track block counts per session per hook
  const sessionBlockCounts: Map<string, number> = new Map();

  for (const entry of entries) {
    try {
      // Only count blocks
      if (entry.decision !== "block") {
        continue;
      }

      const hookName = (entry.hook as string) || "";

      // Skip protective hooks (expected blocks)
      if (PROTECTIVE_HOOKS.has(hookName)) {
        continue;
      }

      // Only count workflow problem hooks
      if (!WORKFLOW_PROBLEM_HOOKS.has(hookName)) {
        continue;
      }

      // Parse timestamp
      let timestampStr = (entry.timestamp as string) || "";
      if (!timestampStr) {
        continue;
      }

      // Handle timezone
      if (timestampStr.endsWith("Z")) {
        timestampStr = `${timestampStr.slice(0, -1)}+00:00`;
      }
      const timestamp = new Date(timestampStr);

      if (timestamp < cutoff) {
        continue;
      }

      const sessionId = (entry.session_id as string) || "unknown";
      const key = `${hookName}|${sessionId}`;
      sessionBlockCounts.set(key, (sessionBlockCounts.get(key) || 0) + 1);
    } catch {
      // ログエントリ処理エラー、スキップ
    }
  }

  // Count sessions where hook blocked 3+ times (threshold for "repeated")
  const hookSessions: Map<string, Set<string>> = new Map();
  for (const [key, count] of sessionBlockCounts) {
    if (count >= BLOCK_COUNT_THRESHOLD) {
      const [hookName, sessionId] = key.split("|");
      if (!hookSessions.has(hookName)) {
        hookSessions.set(hookName, new Set());
      }
      hookSessions.get(hookName)!.add(sessionId);
    }
  }

  // Return count of unique sessions per hook
  const result: Record<string, number> = {};
  for (const [hook, sessions] of hookSessions) {
    result[hook] = sessions.size;
  }
  return result;
}

/**
 * Escape special characters for GitHub search query.
 */
export function escapeGithubSearchTerm(term: string): string {
  let escaped = term.replace(/\\/g, "\\\\");
  escaped = escaped.replace(/"/g, '\\"');
  return escaped;
}

/**
 * Check if an Issue (open or closed) exists for this problem.
 */
export function hasIssue(source: string): boolean {
  try {
    const escapedSource = escapeGithubSearchTerm(source);
    const searchTerm = `"[改善] ${escapedSource}"`;

    const result = execSync(
      `gh issue list --state all --search '${searchTerm} in:title' --json number,title`,
      {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );

    const issues = JSON.parse(result || "[]") as Array<{
      number: number;
      title: string;
    }>;

    // Verify title actually contains the pattern
    for (const issue of issues) {
      if (issue.title.includes(`[改善] ${source}`)) {
        return true;
      }
    }

    return false;
  } catch {
    // Fail open: if we can't check, don't block
    return true;
  }
}

/**
 * Check if command is a gh pr merge invocation.
 */
export function checkIsMergeCommand(command: string): boolean {
  // Split by shell operators and check each part
  const parts = command.split(/\s*(?:&&|\|\||;)\s*/);
  for (const part of parts) {
    // Match optional env var assignments followed by gh pr merge
    if (/^\s*(?:\w+=\S*\s+)*gh\s+pr\s+merge\b/.test(part)) {
      return true;
    }
  }
  return false;
}

async function main(): Promise<void> {
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);
    sessionId = ctx.sessionId;
    const toolInput = (data.tool_input as Record<string, unknown>) || {};
    const command = (toolInput.command as string) || "";

    // Only check gh pr merge commands
    if (!checkIsMergeCommand(command)) {
      await logHookExecution(HOOK_NAME, "skip", "Not a merge command", undefined, { sessionId });
      process.exit(0);
    }

    // Aggregate recurring problems
    const sessionCounts = await aggregateRecurringProblems();

    // Find problems exceeding threshold
    const blockingProblems: BlockingProblem[] = [];
    for (const [source, count] of Object.entries(sessionCounts)) {
      if (count >= RECURRING_THRESHOLD) {
        // Check if Issue already exists
        if (hasIssue(source)) {
          continue;
        }
        blockingProblems.push({ source, count });
      }
    }

    // If no blocking problems, approve silently
    if (blockingProblems.length === 0) {
      await logHookExecution(HOOK_NAME, "approve", "No blocking problems", undefined, {
        sessionId,
      });
      process.exit(0);
    }

    // Build block message
    const problemList = blockingProblems
      .slice(0, 5)
      .map((p) => `  - ${p.source}: ${p.count}セッションで検出`)
      .join("\n");
    const moreMsg =
      blockingProblems.length > 5 ? `\n  ... 他 ${blockingProblems.length - 5} 件` : "";

    const firstProblem = blockingProblems[0].source;
    const reason = `⚠️ 繰り返し検出されている問題があります。\n\n検出された問題:\n${problemList}${moreMsg}\n\n対応が必要です:\ngh issue create --title "[改善] ${firstProblem}の対策を検討" --label enhancement,P2\n\nIssueを作成するとブロックが解除されます。`;

    const result = makeBlockResult(HOOK_NAME, reason);
    await logHookExecution(
      HOOK_NAME,
      "block",
      reason,
      {
        blocking_problems: blockingProblems,
      },
      { sessionId },
    );
    console.log(JSON.stringify(result));
    process.exit(0);
  } catch (error) {
    // On error, approve to avoid blocking
    const errorMsg = `Hook error: ${formatError(error)}`;
    console.error(`[${HOOK_NAME}] ${errorMsg}`);
    const result = makeApproveResult(HOOK_NAME, errorMsg);
    await logHookExecution(HOOK_NAME, "approve", errorMsg, undefined, { sessionId });
    console.log(JSON.stringify(result));
    process.exit(0);
  }
}

if (import.meta.main) {
  main();
}
