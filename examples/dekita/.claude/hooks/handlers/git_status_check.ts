#!/usr/bin/env bun
/**
 * セッション開始時にGitの状態を確認し、未コミット変更を警告する。
 *
 * Why:
 *   mainブランチに未コミット変更がある状態でセッションを開始すると、
 *   意図しない変更の混入や競合が発生するリスクがある。
 *
 * What:
 *   - Gitワーキングツリーの状態を確認
 *   - mainブランチに未コミット変更がある場合に警告
 *   - featureブランチの未コミット変更は情報として表示
 *
 * Remarks:
 *   - ブロックはしない（警告のみ）
 *   - stop_hook_active時はスキップ
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2874: TypeScript移行
 *   - silenvx/dekita#3094: async化（spawnSync → asyncSpawn）
 */

import { TIMEOUT_LIGHT } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";
import { asyncSpawnAll } from "../lib/spawn";

const HOOK_NAME = "git-status-check";

export interface GitStatus {
  isClean: boolean;
  branch: string;
  statusOutput: string;
}

/**
 * Check git status and branch (async, parallel execution).
 * @internal Exported for testing
 */
export async function getGitStatus(): Promise<GitStatus> {
  try {
    // Execute both commands in parallel
    const [branchResult, statusResult] = await asyncSpawnAll([
      {
        command: "git",
        args: ["branch", "--show-current"],
        options: { timeout: TIMEOUT_LIGHT * 1000 },
      },
      {
        command: "git",
        args: ["status", "--porcelain"],
        options: { timeout: TIMEOUT_LIGHT * 1000 },
      },
    ]);

    // gitコマンド失敗時はエラーとして扱う（isClean: falseでエラーを隠蔽しない）
    if (!branchResult.success) {
      return {
        isClean: false,
        branch: "",
        statusOutput: `git branch --show-current failed: ${branchResult.stderr || "Unknown error"}`,
      };
    }
    if (!statusResult.success) {
      return {
        isClean: false,
        branch: "",
        statusOutput: `git status --porcelain failed: ${statusResult.stderr || "Unknown error"}`,
      };
    }

    const branch = branchResult.stdout?.trim() || "";
    const statusOutput = statusResult.stdout?.trim() || "";
    const isClean = statusOutput.length === 0;

    return { isClean, branch, statusOutput };
  } catch (e) {
    // エラー時もisClean: falseとし、エラーを隠蔽しない
    return { isClean: false, branch: "", statusOutput: `error: ${formatError(e)}` };
  }
}

interface HookResult {
  ok: boolean;
  decision?: string;
  reason?: string;
  systemMessage?: string;
}

async function main(): Promise<void> {
  let result: HookResult;
  let sessionId: string | undefined;

  try {
    const inputJson = await parseHookInput();
    sessionId = inputJson.session_id;

    // If stop_hook_active is set, approve immediately
    if (inputJson.stop_hook_active) {
      result = {
        ok: true,

        reason: "stop_hook_active is set; skipping git status check.",
      };
    } else {
      const { isClean, branch, statusOutput } = await getGitStatus();

      if (isClean) {
        result = {
          ok: true,

          reason: `Git working tree is clean on branch '${branch}'`,
          systemMessage: `✅ git-status-check: クリーン (${branch})`,
        };
      } else if (branch === "main") {
        // Warning: uncommitted changes on main branch
        result = {
          ok: true,
          // Don't block, just warn
          reason: `Uncommitted changes on main branch:\n${statusOutput}`,
          systemMessage: `⚠️ git-status-check: mainブランチに未コミット変更があります\n${statusOutput}`,
        };
      } else {
        // Changes on feature branch - OK
        result = {
          ok: true,

          reason: `Uncommitted changes on branch '${branch}' (not main, OK)`,
          systemMessage: `ℹ️ git-status-check: 未コミット変更あり (${branch})`,
        };
      }
    }
  } catch (e) {
    // On error, approve to avoid blocking
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(e)}`);
    result = { ok: true, reason: `Hook error: ${formatError(e)}` };
  }

  await logHookExecution(HOOK_NAME, result.decision ?? "approve", result.reason, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
