#!/usr/bin/env bun
/**
 * 実装開始前にIssue存在を確認し、Issue作成を優先させる
 *
 * Why:
 *   AGENTS.mdには「問題発見→Issue作成→実装」の流れがルールとして記載されているが、
 *   強制機構がなかった。Issue作成を忘れて実装を始めてしまうケースを防ぐ。
 *
 * What:
 *   - Edit/Writeツール使用時に発火
 *   - worktreeのブランチ名からIssue番号を抽出
 *   - gh issue viewでIssueの存在・状態を確認
 *   - Issueがない/クローズ済みの場合は警告
 *
 * Remarks:
 *   - 警告型フック（ブロックはしない）
 *   - PreToolUse:Edit|Writeで発火
 *   - mainブランチ、worktree外はスキップ
 *   - .claude/ 配下の編集はスキップ（フック開発時の無限ループ防止）
 *   - セッション内キャッシュ: Issue番号ごとに1回だけgh issue viewを実行
 *
 * Changelog:
 *   - silenvx/dekita#2978: 初期実装
 */

import { getCurrentBranch, isInWorktree } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult } from "../lib/results";
import { checkAndUpdateSessionMarker, parseHookInput } from "../lib/session";

const HOOK_NAME = "issue-existence-check";

/** Issue番号を抽出するパターン */
const ISSUE_NUMBER_PATTERNS = [
  /issue[_-](\d+)/i, // issue-123, issue_123
  /#(\d+)/, // #123
  /[-_](\d+)[-_]/, // feature-123-name
  /[-_](\d+)$/, // feature-123 (at end)
];

/**
 * ブランチ名からIssue番号を抽出
 */
export function extractIssueNumber(branchName: string): number | null {
  for (const pattern of ISSUE_NUMBER_PATTERNS) {
    const match = branchName.match(pattern);
    if (match) {
      return Number.parseInt(match[1], 10);
    }
  }
  return null;
}

/**
 * gh issue viewでIssue状態を確認
 * @returns "open", "closed", or null (not found/error)
 */
async function getIssueState(issueNumber: number): Promise<string | null> {
  try {
    const proc = Bun.spawn(
      ["gh", "issue", "view", String(issueNumber), "--json", "state", "--jq", ".state"],
      {
        stdout: "pipe",
        stderr: "pipe",
      },
    );
    const output = await new Response(proc.stdout).text();
    const exitCode = await proc.exited;
    if (exitCode !== 0) {
      return null;
    }
    return output.trim().toLowerCase();
  } catch {
    return null;
  }
}

async function main(): Promise<void> {
  const hookInput = await parseHookInput();
  const sessionId = hookInput.session_id;
  const toolName = hookInput.tool_name || "";
  const toolInput = hookInput.tool_input || {};
  const filePath = (toolInput as { file_path?: string }).file_path || "";

  // Edit/Write以外はスキップ
  if (toolName !== "Edit" && toolName !== "Write") {
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // .claude/ 配下の編集はスキップ（フック開発時の無限ループ防止）
  if (filePath.includes(".claude/")) {
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // worktree外はスキップ
  if (!isInWorktree()) {
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // ブランチ名取得
  const branch = await getCurrentBranch();
  if (!branch) {
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // main/masterブランチはスキップ
  if (branch === "main" || branch === "master") {
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Issue番号抽出
  const issueNumber = extractIssueNumber(branch);
  if (!issueNumber) {
    // Issue番号がブランチ名にない場合は警告
    // issue_branch_checkでworktree作成時にブロックしているはずだが、念のため警告
    const message = `[${HOOK_NAME}] ⚠️ ブランチ名にIssue番号が含まれていません

**ブランチ**: \`${branch}\`

実装を始める前にIssueを作成してください:
1. \`gh issue create\` でIssueを作成
2. Issue番号を含むブランチ名でworktreeを再作成`;

    await logHookExecution(
      HOOK_NAME,
      "approve",
      `No issue number in branch: ${branch}`,
      undefined,
      {
        sessionId,
      },
    );
    // 警告メッセージを含めてapprove（ブロックはしない）
    console.log(
      JSON.stringify({
        ...makeApproveResult(HOOK_NAME),
        message,
      }),
    );
    return;
  }

  // セッション内で既にこのIssueをチェック済みならスキップ（ネットワーク呼び出し回避）
  const markerName = `${HOOK_NAME}-${issueNumber}`;
  if (!checkAndUpdateSessionMarker(markerName)) {
    // 既にチェック済み - 前回の結果を信頼してapprove
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    return;
  }

  // Issue状態確認（セッション内で初回のみ実行）
  const state = await getIssueState(issueNumber);

  if (state === null) {
    // Issueが見つからない
    const message = `[${HOOK_NAME}] ⚠️ Issue #${issueNumber} が見つかりません

**ブランチ**: \`${branch}\`

実装を始める前にIssueを作成してください:
\`\`\`bash
gh issue create --title "タイトル" --body "本文"
\`\`\`

**重要**: Issue作成を優先してください。実装はIssue作成後に行います。`;

    await logHookExecution(HOOK_NAME, "approve", `Issue #${issueNumber} not found`, undefined, {
      sessionId,
    });
    console.log(
      JSON.stringify({
        ...makeApproveResult(HOOK_NAME),
        message,
      }),
    );
    return;
  }

  if (state === "closed") {
    // Issueがクローズ済み
    const message = `[${HOOK_NAME}] ⚠️ Issue #${issueNumber} は既にクローズされています

**ブランチ**: \`${branch}\`

クローズ済みIssueに対する変更は別Issueを作成してください:
\`\`\`bash
gh issue create --title "Issue #${issueNumber} の追加修正" --body "関連: #${issueNumber}"
\`\`\``;

    await logHookExecution(HOOK_NAME, "approve", `Issue #${issueNumber} is closed`, undefined, {
      sessionId,
    });
    console.log(
      JSON.stringify({
        ...makeApproveResult(HOOK_NAME),
        message,
      }),
    );
    return;
  }

  // Issueがオープン - 正常
  await logHookExecution(HOOK_NAME, "approve", `Issue #${issueNumber} is open`, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
}

if (import.meta.main) {
  main();
}
