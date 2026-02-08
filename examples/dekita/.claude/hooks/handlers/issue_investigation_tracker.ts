#!/usr/bin/env bun
/**
 * gh issue view実行時に別セッションの調査を検知し警告する。
 *
 * Why:
 *   worktree/PR作成前の調査フェーズでも並行セッションの競合が発生する。
 *   Issue閲覧時点で調査開始を記録し、別セッションとの重複を早期検知する。
 *
 * What:
 *   - gh issue viewコマンドを検出しIssue番号を抽出
 *   - Issueコメントから他セッションの調査開始マーカーを検索
 *   - 別セッションが1時間以内に調査中なら警告
 *   - 自身の調査開始をコメントとして記録（重複防止）
 *
 * State:
 *   - writes: GitHub Issueコメント（🔍 調査開始マーカー）
 *
 * Remarks:
 *   - 非ブロック型（警告のみ）
 *   - issue-auto-assignはworktree作成時の競合防止、本フックは調査フェーズの検知
 *   - Python版: issue_investigation_tracker.py
 *
 * Changelog:
 *   - silenvx/dekita#1830: フック追加
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult } from "../lib/results";
import { createContext, getSessionId, parseHookInput } from "../lib/session";

const HOOK_NAME = "issue-investigation-tracker";

// 調査中と判定する時間（1時間、ミリ秒）
const ACTIVE_INVESTIGATION_MS = 60 * 60 * 1000;

// 調査開始コメントのパターン
const INVESTIGATION_PATTERN = /🔍 調査開始 \(session: ([a-zA-Z0-9-]+)\)/;

// gh issue view コマンドのパターン
const GH_ISSUE_VIEW_PATTERN = /\bgh\s+issue\s+view\s+#?(\d+)/;

export interface IssueComment {
  body?: string;
  createdAt?: string;
  author?: {
    login?: string;
  };
}

export interface ActiveInvestigation {
  session_id: string;
  created_at: string;
  author: string;
}

/**
 * Issueのコメントを取得
 */
function getIssueComments(issueNumber: number): IssueComment[] | null {
  try {
    const result = execSync(`gh issue view ${issueNumber} --json comments`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const data = JSON.parse(result);
    return data.comments ?? [];
  } catch {
    return null;
  }
}

/**
 * 活動中の調査セッションを検索
 *
 * @returns 活動中の別セッション情報。自分のセッションまたは活動なしの場合はnull。
 */
export function findActiveInvestigation(
  comments: IssueComment[],
  currentSession: string,
): ActiveInvestigation | null {
  const now = Date.now();
  const threshold = now - ACTIVE_INVESTIGATION_MS;

  // 新しいコメントから検索
  for (let i = comments.length - 1; i >= 0; i--) {
    const comment = comments[i];
    const body = comment.body ?? "";
    const match = INVESTIGATION_PATTERN.exec(body);
    if (!match) {
      continue;
    }

    const sessionId = match[1];

    // 自分のセッションなら無視
    if (sessionId === currentSession) {
      continue;
    }

    // タイムスタンプ確認
    const createdAtStr = comment.createdAt ?? "";
    if (createdAtStr) {
      try {
        const createdAt = new Date(createdAtStr).getTime();
        if (createdAt > threshold) {
          return {
            session_id: sessionId,
            created_at: createdAtStr,
            author: comment.author?.login ?? "unknown",
          };
        }
      } catch {
        // Skip comment with invalid timestamp format
      }
    }
  }

  return null;
}

/**
 * 自分のセッションからの最近のコメントがあるかチェック
 *
 * 重複コメント防止用。1時間以内の自分のコメントがあればtrueを返す。
 */
export function hasRecentOwnComment(comments: IssueComment[], currentSession: string): boolean {
  const now = Date.now();
  const threshold = now - ACTIVE_INVESTIGATION_MS;

  for (let i = comments.length - 1; i >= 0; i--) {
    const comment = comments[i];
    const body = comment.body ?? "";
    const match = INVESTIGATION_PATTERN.exec(body);
    if (!match) {
      continue;
    }

    const sessionId = match[1];
    if (sessionId !== currentSession) {
      continue;
    }

    // タイムスタンプ確認
    const createdAtStr = comment.createdAt ?? "";
    if (createdAtStr) {
      try {
        const createdAt = new Date(createdAtStr).getTime();
        if (createdAt > threshold) {
          return true;
        }
      } catch {
        // Skip comment with invalid timestamp format
      }
    }
  }

  return false;
}

/**
 * 調査開始コメントを追加
 */
function addInvestigationComment(issueNumber: number, sessionId: string): boolean {
  const commentBody = `🔍 調査開始 (session: ${sessionId})`;
  try {
    execSync(`gh issue comment ${issueNumber} --body "${commentBody}"`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return true;
  } catch {
    return false;
  }
}

async function main(): Promise<void> {
  const data = await parseHookInput();
  const ctx = createContext(data);
  const sessionId = getSessionId(ctx) ?? "unknown";

  if (!data || Object.keys(data).length === 0) {
    await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
    return;
  }

  // Bashツールのみを対象
  const toolName = data.tool_name ?? "";
  if (toolName !== "Bash") {
    await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
    return;
  }

  const toolInput = (data.tool_input as Record<string, unknown>) ?? {};
  const command = (toolInput.command as string) ?? "";

  // gh issue view コマンドかチェック
  const match = GH_ISSUE_VIEW_PATTERN.exec(command);
  if (!match) {
    await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
    return;
  }

  const issueNumber = Number.parseInt(match[1], 10);

  // 既存コメントを取得
  const comments = getIssueComments(issueNumber);
  if (comments === null) {
    // コメント取得失敗時は警告なしで続行
    await logHookExecution(HOOK_NAME, "approve", "comments_fetch_failed", undefined, { sessionId });
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
    return;
  }

  // 活動中の別セッションを検索
  const activeInvestigation = findActiveInvestigation(comments, sessionId);

  if (activeInvestigation) {
    // 別セッションが調査中 - 警告
    const otherSession = activeInvestigation.session_id;
    const author = activeInvestigation.author;
    const createdAt = activeInvestigation.created_at;

    const warning = `⚠️ **別セッションが調査中**: Issue #${issueNumber}\n\n- セッション: \`${otherSession}\`\n- 開始者: @${author}\n- 開始時刻: ${createdAt}\n\n同じIssueに取り組むと競合する可能性があります。\n別のIssueに取り組むか、調査のみに留めることを検討してください。`;

    await logHookExecution(
      HOOK_NAME,
      "approve",
      `other_session_active:${otherSession}`,
      undefined,
      { sessionId },
    );

    const result = {
      systemMessage: `[${HOOK_NAME}] ${warning}`,
    };
    console.log(JSON.stringify(result));
  } else {
    // 重複コメント防止: 自分のセッションからの最近のコメントがあればスキップ
    if (hasRecentOwnComment(comments, sessionId)) {
      await logHookExecution(HOOK_NAME, "approve", `already_commented:${issueNumber}`, undefined, {
        sessionId,
      });
    } else if (addInvestigationComment(issueNumber, sessionId)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `investigation_started:${issueNumber}`,
        undefined,
        { sessionId },
      );
    } else {
      await logHookExecution(HOOK_NAME, "approve", "comment_add_failed", undefined, { sessionId });
    }

    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
  }
}

if (import.meta.main) {
  main();
}
