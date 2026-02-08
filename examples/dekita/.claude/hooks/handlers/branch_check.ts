#!/usr/bin/env bun
/**
 * セッション開始時にメインリポジトリのブランチ状態を確認する。
 *
 * Why:
 *   メインリポジトリがmain以外のブランチの状態でセッションを開始すると、
 *   worktreeワークフローを無視した作業につながる可能性がある。
 *
 * What:
 *   - 現在のディレクトリがworktree内かどうか確認
 *   - worktree内でなければ、現在のブランチを確認
 *   - mainでない場合はセッション開始をブロック
 *   - mainブランチに戻す手順を提示
 *
 * Remarks:
 *   - ブロック型フック（mainでない場合はブロック）
 *   - worktree内の場合はスキップ（worktreeでは任意ブランチを許可）
 *   - SessionStartで発火
 *
 * Changelog:
 *   - silenvx/dekita#2874: TypeScriptに移植
 *   - silenvx/dekita#2894: isInWorktree/isMainRepositoryをlib/git.tsに集約
 */

import { getCurrentBranch, isInWorktree, isMainRepository } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

async function main(): Promise<void> {
  // Parse hook input for session ID
  const hookInput = await parseHookInput();
  const sessionId = hookInput.session_id;

  // If inside worktree, skip check (worktrees can be on any branch)
  if (isInWorktree()) {
    return;
  }

  // If not main repository (e.g., sub-worktree), skip check
  if (!(await isMainRepository())) {
    return;
  }

  // Check current branch
  const branch = await getCurrentBranch();
  if (branch === null) {
    return;
  }

  // Block if not on main branch
  if (branch !== "main") {
    await logHookExecution(
      "branch-check",
      "block",
      `Main repository is on '${branch}' branch instead of 'main'`,
      { current_branch: branch },
      { sessionId },
    );

    console.log(`🚫 [branch-check] メインリポジトリが '${branch}' ブランチになっています。

メインリポジトリは常にmainブランチに保つ必要があります。
セッション開始前にmainブランチに戻してください:

  git checkout main

未コミットの変更がある場合:
  git stash && git checkout main

別ブランチで作業する場合はworktreeを使用してください:
  git worktree add --lock .worktrees/<name> -b <branch-name>
`);
    process.exit(2); // exit 2 = blocking error
  }
}

if (import.meta.main) {
  main();
}
