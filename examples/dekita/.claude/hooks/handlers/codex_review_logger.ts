#!/usr/bin/env bun
/**
 * Codex CLIレビュー実行をログ記録する（codex-review-checkと連携）。
 *
 * Why:
 *   codex-review-checkがPR作成/push前にレビュー実行済みかを確認するため、
 *   レビュー実行時にブランチ・コミット情報を記録しておく必要がある。
 *
 * What:
 *   - codex reviewコマンドを検出
 *   - ブランチ名、コミットハッシュ、diffハッシュを記録
 *   - main/masterブランチでは記録しない
 *
 * State:
 *   - writes: .claude/logs/markers/codex-review-{branch}.done
 *
 * Remarks:
 *   - 記録型フック（ブロックしない、マーカーファイル書き込み）
 *   - PreToolUse:Bashで発火（codex reviewコマンド）
 *   - codex-review-check.pyと連携（マーカーファイル参照元）
 *   - diffハッシュ記録によりリベース後のスキップ判定が可能
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2874: TypeScriptに移植
 *   - silenvx/dekita#2998: getMarkersDir()使用でworktree→メインリポジトリ解決
 */

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { formatError } from "../lib/format_error";
import { getCurrentBranch, getDiffHash, getHeadCommit } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { getMarkersDir } from "../lib/markers";
import { parseHookInput } from "../lib/session";
import { sanitizeBranchName, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "codex-review-logger";

/**
 * Check if command is actually a codex review command.
 * Returns false for commands inside quoted strings.
 */
export function isCodexReviewCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  const strippedCommand = stripQuotedStrings(command);
  return /codex\s+review\b/.test(strippedCommand);
}

/**
 * Log that codex review was executed for this branch at specific commit.
 */
function logReviewExecution(branch: string, commit: string | null, diffHash: string | null): void {
  const markersDir = getMarkersDir();
  if (!existsSync(markersDir)) {
    mkdirSync(markersDir, { recursive: true });
  }
  const safeBranch = sanitizeBranchName(branch);
  const logFile = `${markersDir}/codex-review-${safeBranch}.done`;

  // Store branch:commit:diff_hash format
  // diff_hash allows skipping re-review when only commit hash changed (e.g., after rebase)
  let content: string;
  if (commit && diffHash) {
    content = `${branch}:${commit}:${diffHash}`;
  } else if (commit) {
    content = `${branch}:${commit}`;
  } else {
    content = branch;
  }
  writeFileSync(logFile, content);
}

async function main(): Promise<void> {
  let result: {
    decision?: string;
    reason?: string;
  } = {};
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = data.tool_input || {};
    const command = (toolInput.command as string) || "";

    // Detect codex review command (excluding quoted strings)
    if (isCodexReviewCommand(command)) {
      const branch = await getCurrentBranch();
      if (branch && branch !== "main" && branch !== "master") {
        const [commit, diffHash] = await Promise.all([getHeadCommit(), getDiffHash()]);
        logReviewExecution(branch, commit, diffHash);
      }
    }

    // Always approve - this hook only logs
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    result = {};
  }

  logHookExecution(HOOK_NAME, result.decision ?? "approve", result.reason, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
