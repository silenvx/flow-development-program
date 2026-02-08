#!/usr/bin/env bun
/**
 * 開発ワークフローの開始を追跡する。
 *
 * Why:
 *   Issue単位の開発ワークフロー（worktree作成→実装→レビュー→マージ）を
 *   追跡することで、ステップのスキップを検出し、品質を担保できる。
 *
 * What:
 *   - git worktree addコマンドを検出
 *   - Issue番号を抽出してワークフローを開始
 *   - worktree_createdステップを完了としてマーク
 *
 * State:
 *   writes: .claude/logs/flow/flow-progress-{session_id}.jsonl
 *
 * Remarks:
 *   - ステップ完了の追跡はflow-progress-tracker.tsが担当
 *   - Python版からTypeScriptへ移行（Issue #3051）
 *
 * Changelog:
 *   - silenvx/dekita#2534: コマンドマッチングパターン改善（Python版）
 *   - silenvx/dekita#3051: TypeScriptに移行
 */

import { FLOW_LOG_DIR } from "../lib/common";
import { completeFlowStep, registerFlowDefinition, startFlow } from "../lib/flow";
import { getExitCode, getToolResult } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { createContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "development-workflow-tracker";

// Register the development-workflow flow definition
// This is required for startFlow to work correctly
registerFlowDefinition("development-workflow", {
  name: "開発ワークフロー",
  description: "Issue対応の開発ワークフロー全体を追跡",
  steps: [
    { id: "worktree_created", name: "Worktree作成", phase: "setup" },
    { id: "implementation", name: "実装", phase: "implementation" },
    { id: "committed", name: "コミット", phase: "implementation" },
    { id: "pushed", name: "プッシュ", phase: "implementation" },
    { id: "pr_created", name: "PR作成", phase: "review" },
    { id: "ci_passed", name: "CI通過", phase: "review" },
    { id: "review_addressed", name: "レビュー対応", phase: "review" },
    { id: "merged", name: "マージ", phase: "completion" },
    { id: "cleaned_up", name: "クリーンアップ", phase: "completion" },
  ],
  completion_step: "merged",
});

/**
 * Extract issue number from git worktree add command.
 *
 * Looks for patterns like:
 * - git worktree add ../.worktrees/issue-123
 * - git worktree add /path/to/issue-456 -b issue-456
 *
 * @param command - The git command string
 * @returns Issue number if found, null otherwise.
 */
export function extractIssueNumberFromWorktree(command: string): number | null {
  // Use word boundary to avoid matching partial strings
  const match = command.match(/\bissue-(\d+)\b/i);
  if (match) {
    return Number.parseInt(match[1], 10);
  }
  return null;
}

/**
 * Check if command is a git worktree add command.
 *
 * Supports both `git worktree add` and `cd /path && git worktree add` patterns.
 * Issue #2534: Use (?:^|&&\s*) to match start of line or after &&
 * This avoids false positives from echo/comments while supporting cd prefix.
 *
 * @param command - The command string to check
 * @returns True if command is a git worktree add command.
 */
export function isWorktreeAddCommand(command: string): boolean {
  const worktreeAddPattern = /(?:^|&&\s*)\s*git\s+worktree\s+add\b/;
  return worktreeAddPattern.test(command);
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = {
    continue: true,
  };

  // Define sessionId outside try block so it's available in catch
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    const ctx = createContext(inputData);

    sessionId = ctx.sessionId ?? undefined;

    // Only process Bash tool
    const toolName = inputData.tool_name;
    if (toolName !== "Bash") {
      await logHookExecution(HOOK_NAME, "approve", `not Bash tool: ${toolName}`, undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Get the command that was executed
    const toolInput = inputData.tool_input ?? {};
    const command = typeof toolInput.command === "string" ? toolInput.command : "";
    if (!command) {
      await logHookExecution(HOOK_NAME, "approve", "no command", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Only process if command succeeded (exit_code == 0)
    // getExitCode defaults to 0 for missing exit_code (success), but we want to
    // treat missing as failure (-1) to be conservative about tracking
    const toolResult = getToolResult(inputData as Record<string, unknown>);
    const exitCode = getExitCode(toolResult, -1);
    if (exitCode !== 0) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `command failed: exit_code=${exitCode}`,
        undefined,
        {
          sessionId,
        },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Check for git worktree add command
    if (isWorktreeAddCommand(command)) {
      const issueNumber = extractIssueNumberFromWorktree(command);

      if (issueNumber) {
        // Start development workflow with issue context
        const flowLogDir = FLOW_LOG_DIR;
        const context = { issue_number: issueNumber };
        const flowInstanceId = await startFlow(
          flowLogDir,
          "development-workflow",
          context,
          sessionId,
        );

        if (flowInstanceId) {
          // Mark worktree_created step as completed
          await completeFlowStep(
            flowLogDir,
            flowInstanceId,
            "worktree_created",
            "development-workflow",
            sessionId,
          );

          await logHookExecution(
            HOOK_NAME,
            "approve",
            `Development workflow started for issue #${issueNumber}`,
            { flow_instance_id: flowInstanceId },
            { sessionId },
          );

          result.systemMessage = `[development-workflow] Issue #${issueNumber} の開発ワークフローを開始しました。`;
        } else {
          await logHookExecution(
            HOOK_NAME,
            "approve",
            `Failed to start flow for issue #${issueNumber}`,
            undefined,
            {
              sessionId,
            },
          );
        }
      } else {
        await logHookExecution(
          HOOK_NAME,
          "approve",
          "Worktree created without issue number pattern",
          {
            command: command.slice(0, 100),
          },
          { sessionId },
        );
      }
    }
  } catch (error) {
    // フック実行の失敗でClaude Codeをブロックしない
    await logHookExecution(
      HOOK_NAME,
      "approve",
      "Hook execution error (non-blocking)",
      { error: String(error) },
      { sessionId },
    );
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
