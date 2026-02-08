#!/usr/bin/env bun
/**
 * git commit後にCodexレビューマーカーを更新する。
 *
 * Why:
 *   レビュー済みブランチで追加コミットをした場合、マーカーを更新しないと
 *   codex-review-checkが「レビュー後にコミットあり」と誤検知する。
 *   コミット後にマーカーを更新することで、不要な再レビューを防ぐ。
 *
 * What:
 *   - git commitコマンドを検出
 *   - 既存マーカーファイルがあれば新しいコミット情報で更新
 *   - main/masterブランチでは更新しない
 *   - HEADが変わっていない場合はスキップ
 *
 * State:
 *   - reads: .claude/logs/markers/codex-review-{branch}.done
 *   - writes: .claude/logs/markers/codex-review-{branch}.done
 *
 * Remarks:
 *   - 記録型フック（ブロックしない、マーカー更新）
 *   - PostToolUse:Bashで発火（git commitコマンド）
 *   - 既存マーカーがない場合は何もしない（新規作成しない）
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2874: TypeScriptに移植
 *   - silenvx/dekita#2998: getMarkersDir()使用でworktree→メインリポジトリ解決
 */

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { getCurrentBranch, getDiffHash, getHeadCommit } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { getMarkersDir } from "../lib/markers";
import { parseHookInput } from "../lib/session";
import { sanitizeBranchName, splitCommandChain, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "commit-marker-update";

/**
 * Check if command contains git commit.
 */
export function isGitCommitCommand(command: string): boolean {
  const stripped = stripQuotedStrings(command);
  const subcommands = splitCommandChain(stripped);
  return subcommands.some((subcmd) => /^git\s+commit(\s|$)/.test(subcmd));
}

/**
 * Update marker file if it exists.
 */
function updateMarker(branch: string, commit: string, diffHash: string): boolean {
  const markersDir = getMarkersDir();
  const safeBranch = sanitizeBranchName(branch);
  const markerFile = `${markersDir}/codex-review-${safeBranch}.done`;

  if (!existsSync(markerFile)) {
    return false;
  }

  const content = `${branch}:${commit}:${diffHash}`;
  writeFileSync(markerFile, content);
  return true;
}

async function main(): Promise<void> {
  const hookInput = await parseHookInput();
  const sessionId = hookInput?.session_id;

  if (!hookInput) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  const toolName = hookInput.tool_name || "";
  const toolInput = hookInput.tool_input || {};

  // Only process Bash tool
  if (toolName !== "Bash") {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  const command = (toolInput.command as string) || "";

  // Only process git commit commands
  if (!isGitCommitCommand(command)) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // Skip main/master
  const branch = await getCurrentBranch();
  if (!branch || branch === "main" || branch === "master") {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // Get current HEAD
  const [commit, diffHash] = await Promise.all([getHeadCommit(), getDiffHash()]);

  if (!commit || !diffHash) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // Check if marker exists
  const markersDir = getMarkersDir();
  const safeBranch = sanitizeBranchName(branch);
  const markerFile = `${markersDir}/codex-review-${safeBranch}.done`;

  if (!existsSync(markerFile)) {
    logHookExecution(HOOK_NAME, "approve", `No marker file for branch: ${branch}`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // Read marker and check if HEAD changed
  const markerContent = readFileSync(markerFile, "utf-8").trim();
  const markerParts = markerContent.split(":");
  if (markerParts.length >= 2) {
    const markerCommit = markerParts[1];
    if (markerCommit === commit) {
      // HEAD hasn't changed, no new commit
      logHookExecution(HOOK_NAME, "approve", `HEAD unchanged: ${commit.slice(0, 8)}`, undefined, {
        sessionId,
      });
      console.log(JSON.stringify({ continue: true }));
      return;
    }
  }

  // HEAD changed, update marker
  const updated = updateMarker(branch, commit, diffHash);
  if (updated) {
    logHookExecution(
      HOOK_NAME,
      "approve",
      `Marker updated: ${branch}:${commit.slice(0, 8)}:${diffHash.slice(0, 8)}`,
      undefined,
      { sessionId },
    );
  }

  console.log(JSON.stringify({ continue: true }));
}

if (import.meta.main) {
  main();
}
