#!/usr/bin/env bun
/**
 * Dependabot PR操作時にmanaging-developmentスキルの参照を促す。
 *
 * Why:
 *   Dependabot PRのマージはリスク順序・E2Eテストなど特別な手順が必要。
 *   スキルを参照せずに操作すると、本番障害につながる可能性がある。
 *
 * What:
 *   - gh pr merge/rebase/checkout等のDependabot PR操作を検出
 *   - managing-developmentスキルの参照を促す警告を表示
 *   - ブロックはせず、情報提供のみ
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、stderrで警告）
 *   - PreToolUse:Bashで発火（gh prコマンド）
 *   - gh api呼び出しでDependabot PRを判定
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { spawnSync } from "node:child_process";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { parseGhPrCommand } from "../lib/github";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "dependabot-skill-reminder";

// Dependabot PR操作として検出するサブコマンド
const DEPENDABOT_OP_SUBCOMMANDS = new Set(["merge", "rebase", "checkout"]);

/**
 * Check if PR is a Dependabot PR.
 */
function isDependabotPr(prNumber: string): boolean {
  try {
    const result = spawnSync("gh", ["pr", "view", prNumber, "--json", "author,headRefName"], {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM,
    });
    if (result.status !== 0) {
      return false;
    }

    const data = JSON.parse(result.stdout);
    const author = data.author?.login || "";
    const branch = data.headRefName || "";

    return author === "dependabot[bot]" || branch.startsWith("dependabot/");
  } catch {
    return false;
  }
}

/**
 * Check if command is a Dependabot PR operation.
 */
export function isDependabotOperation(command: string): {
  isDependabotOp: boolean;
  prNumber: string | null;
} {
  const [subcommand, prNumber] = parseGhPrCommand(command);
  if (subcommand && DEPENDABOT_OP_SUBCOMMANDS.has(subcommand)) {
    return { isDependabotOp: true, prNumber };
  }
  return { isDependabotOp: false, prNumber: null };
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

    // Check if this is a Dependabot PR operation
    const { isDependabotOp, prNumber } = isDependabotOperation(command);
    if (!isDependabotOp) {
      result = makeApproveResult(HOOK_NAME);
      logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // No PR number, approve
    if (!prNumber) {
      result = makeApproveResult(HOOK_NAME);
      logHookExecution(HOOK_NAME, "approve", "no PR number", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Check if it's a Dependabot PR
    if (!isDependabotPr(prNumber)) {
      result = makeApproveResult(HOOK_NAME);
      logHookExecution(HOOK_NAME, "approve", "not a Dependabot PR", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Dependabot PR operation detected - warn and approve
    const message = `[${HOOK_NAME}] Dependabot PR #${prNumber} への操作を検出しました。\n\nmanaging-development スキルを参照してください:\n\nSkill managing-development\n\nスキルには以下の手順が含まれています:\n  - BEHIND検知時の対応手順\n  - 複数PR処理のベストプラクティス\n  - ci-monitor.py の活用方法\n\n効率的なDependabot PR処理には、スキルの手順に従うことを推奨します。`;

    // Output warning (don't block)
    console.error(message);

    result = makeApproveResult(HOOK_NAME);
    logHookExecution(
      HOOK_NAME,
      "approve",
      `Dependabot PR warning shown for PR #${prNumber}`,
      undefined,
      { sessionId },
    );
    console.log(JSON.stringify(result));
  } catch (error) {
    // On error, approve
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    result = makeApproveResult(HOOK_NAME, `Hook error: ${formatError(error)}`);
    logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(error)}`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify(result));
  }
}

// 実行（直接実行時のみ）
if (import.meta.main) {
  main();
}
