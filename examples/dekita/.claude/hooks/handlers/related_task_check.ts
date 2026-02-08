#!/usr/bin/env bun
/**
 * セッション終了時にセッション内作成Issueのステータスを確認し、未完了ならブロック。
 *
 * Why:
 *   セッション内で作成したIssueは同セッションで実装まで完遂する必要がある。
 *   Issue作成だけで終了させず、実装を強制する。
 *
 * What:
 *   - セッション終了時（Stopフック）に発火
 *   - session-created-issues-{session_id}.jsonから作成Issue一覧を取得
 *   - GitHubでIssueステータスを確認
 *   - 未完了（OPEN）Issueがあればセッション終了をブロック
 *   - fork-sessionへの委譲（PR/ロック済worktree存在）は許可
 *
 * State:
 *   - reads: .claude/logs/flow/session-created-issues-{session_id}.json
 *
 * Remarks:
 *   - ブロック型フック（回数制限なし、完了まで無限ブロック）
 *   - issue-creation-trackerが記録、本フックが検証
 *   - 見送る場合は `gh issue close --reason "not planned"` で明示
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#1918: セッションファイルをプロジェクトログに移動
 *   - silenvx/dekita#2076: AGENTS.md原則を明示
 *   - silenvx/dekita#2090: 回数制限を削除（完了まで無限ブロック）
 *   - silenvx/dekita#2470: fork-sessionでも自作成Issue実装可能と明記
 *   - silenvx/dekita#2525: fork-sessionへの委譲検出を追加
 *   - silenvx/dekita#2864: 大規模タスク（30分以上）の例外を追加
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { execFileSync, execSync } from "node:child_process";
import { existsSync, readFileSync, unlinkSync } from "node:fs";
import { basename, join } from "node:path";
import { FLOW_LOG_DIR } from "../lib/common";
import { CONTINUATION_HINT, TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { createHookContext, isForkSession, parseHookInput } from "../lib/session";

const HOOK_NAME = "related-task-check";

// Maximum title length for display
const MAX_TITLE_DISPLAY_LENGTH = 50;

// Labels that indicate a large task (30+ minutes) - skip blocking for these
const LARGE_TASK_LABELS = new Set(["long-term", "multi-session", "phase-2", "phase-3", "phase-4"]);

interface IssueInfo {
  number: number;
  title: string;
  state: string;
  labels: Array<{ name: string }>;
  delegatedPr?: string | null;
}

/**
 * Check if a branch name references a specific issue number.
 */
function matchesIssueInBranch(branch: string, issueNumber: number): boolean {
  const pattern = new RegExp(`issue-${issueNumber}(?:[-/]|$)`, "i");
  return pattern.test(branch);
}

/**
 * Check if a PR title references a specific issue number.
 */
function matchesIssueInTitle(title: string, issueNumber: number): boolean {
  const pattern = new RegExp(`#${issueNumber}(?:[\\s\\)\\],:\\.?!;]|$)`);
  return pattern.test(title);
}

/**
 * Check if an issue has been delegated to a fork-session.
 */
function isIssueDelegated(issueNumber: number): { delegated: boolean; prNumber: string | null } {
  // Check 1: Is there an open PR for this issue?
  try {
    const result = execSync("gh pr list --state open --json number,headRefName,title", {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
    });
    const prs = JSON.parse(result);
    for (const pr of prs) {
      const branch = pr.headRefName ?? "";
      const title = pr.title ?? "";
      if (matchesIssueInBranch(branch, issueNumber) || matchesIssueInTitle(title, issueNumber)) {
        return { delegated: true, prNumber: String(pr.number) };
      }
    }
  } catch {
    // Continue to worktree check
  }

  // Check 2: Is there a locked worktree for this issue?
  try {
    const result = execSync("git worktree list --porcelain", {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
    });
    let currentWorktree: string | null = null;
    for (const line of result.split("\n")) {
      if (line.startsWith("worktree ")) {
        currentWorktree = line.slice(9);
      } else if (line === "locked" && currentWorktree) {
        const worktreeName = basename(currentWorktree).toLowerCase();
        if (worktreeName === `issue-${issueNumber}`) {
          return { delegated: true, prNumber: null };
        }
      }
    }
  } catch {
    // Git not found or timeout - fail-open
  }

  return { delegated: false, prNumber: null };
}

/**
 * Truncate title with ellipsis if it exceeds MAX_TITLE_DISPLAY_LENGTH.
 */
function truncateTitle(title: string): string {
  if (title.length > MAX_TITLE_DISPLAY_LENGTH) {
    return `${title.slice(0, MAX_TITLE_DISPLAY_LENGTH - 3)}...`;
  }
  return title;
}

/**
 * Get the file path for storing session-created issues.
 */
function getSessionIssuesFile(sessionId: string): string {
  const safeSessionId = basename(sessionId);
  return join(FLOW_LOG_DIR, `session-created-issues-${safeSessionId}.json`);
}

/**
 * Load list of issue numbers created in this session.
 */
function loadSessionIssues(sessionId: string): number[] {
  const issuesFile = getSessionIssuesFile(sessionId);
  if (existsSync(issuesFile)) {
    try {
      const data = JSON.parse(readFileSync(issuesFile, "utf-8"));
      // Validate that all items are strictly numbers to prevent command injection
      return (data.issues ?? []).filter((i: unknown): i is number => typeof i === "number");
    } catch {
      // Best effort - corrupted data is ignored
    }
  }
  return [];
}

/**
 * Clear session files for this session only.
 */
function clearSessionFiles(sessionId: string): void {
  const issuesFile = getSessionIssuesFile(sessionId);
  try {
    if (existsSync(issuesFile)) {
      unlinkSync(issuesFile);
    }
  } catch {
    // Silently ignore file deletion errors
  }
}

/**
 * Get status of specified issues from GitHub.
 */
function getIssueStatus(issueNumbers: number[]): IssueInfo[] {
  if (issueNumbers.length === 0) {
    return [];
  }

  const issues: IssueInfo[] = [];
  for (const number of issueNumbers) {
    try {
      // Use execFileSync to avoid shell injection
      const result = execFileSync(
        "gh",
        ["issue", "view", String(number), "--json", "number,title,state,labels"],
        {
          encoding: "utf-8",
          timeout: TIMEOUT_MEDIUM * 1000,
        },
      );
      const issue = JSON.parse(result);
      issues.push(issue);
    } catch {
      // Skip issues that fail to fetch
    }
  }
  return issues;
}

/**
 * Check if an issue is a large task based on labels.
 */
function isLargeTask(issue: IssueInfo): boolean {
  const labels = issue.labels ?? [];
  const labelNames = new Set(labels.map((l) => l.name.toLowerCase()));
  for (const label of LARGE_TASK_LABELS) {
    if (labelNames.has(label)) {
      return true;
    }
  }
  return false;
}

/**
 * Format the block reason message.
 */
function formatBlockReason(openIssues: IssueInfo[], isFork: boolean): string {
  const nextIssue = openIssues[0];
  const number = nextIssue.number ?? "?";
  const title = truncateTitle(nextIssue.title ?? "No title");

  const remaining = openIssues.length;
  const remainingText = remaining > 1 ? `（残り${remaining}件）` : "";

  let forkNote = "";
  if (isFork) {
    forkNote =
      "\n\n**重要（fork-session）**: このIssueは**あなた自身がこのセッションで作成**しました。\n" +
      "fork-sessionでも、自分で作成したIssueへの作業は許可されています。\n" +
      "元セッションのworktreeとは関係のない、**新しいworktree**を作成して実装してください。";
  }

  return `**このセッションで作成した未完了Issue${remainingText}**\n\n次のIssueが未完了です。\n**AGENTS.md原則**: 「セッション内で作成したIssueは実装まで完遂」\n  #${number}: ${title}\n\n**今すぐ実装を開始してください**（ユーザー確認不要）:\n  1. \`gh issue view ${number}\` でIssue内容を確認\n  2. worktreeを作成して作業開始\n  3. 実装・PR作成・マージまで完了\n\n**終了条件**: Issueがクローズされるまでブロックし続けます。\n見送る場合は \`gh issue close ${number} --reason "not planned"\` を実行してください。${forkNote}${CONTINUATION_HINT}`;
}

async function main(): Promise<void> {
  const hookInput = await parseHookInput();
  const ctx = createHookContext(hookInput);

  // Get session ID
  const sessionId = ctx.sessionId ?? "unknown";

  // Detect fork-session
  const source = hookInput.source ?? "";
  const transcriptPath = hookInput.transcript_path ?? null;
  const isFork = isForkSession(sessionId, source, transcriptPath);

  let result: { continue: boolean; reason?: string; systemMessage?: string } = { continue: true };

  try {
    // Load issues created in this session
    const sessionIssues = loadSessionIssues(sessionId);

    if (sessionIssues.length === 0) {
      await logHookExecution(HOOK_NAME, "approve", "no session issues", undefined, {
        sessionId: ctx.sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Get current status of these issues
    const issues = getIssueStatus(sessionIssues);

    if (issues.length === 0) {
      await logHookExecution(HOOK_NAME, "approve", "could not fetch issue status", undefined, {
        sessionId: ctx.sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Separate open and closed issues
    const openIssues = issues.filter((i) => i.state === "OPEN");
    const closedIssues = issues.filter((i) => i.state === "CLOSED");

    // Check for delegated and large task issues
    const delegatedIssues: IssueInfo[] = [];
    const largeTaskIssues: IssueInfo[] = [];
    const actionableIssues: IssueInfo[] = [];

    for (const issue of openIssues) {
      // Large tasks skip blocking
      if (isLargeTask(issue)) {
        largeTaskIssues.push(issue);
        continue;
      }

      const { delegated, prNumber } = isIssueDelegated(issue.number);
      if (delegated) {
        issue.delegatedPr = prNumber;
        delegatedIssues.push(issue);
      } else {
        actionableIssues.push(issue);
      }
    }

    // If all open issues are delegated or large tasks, approve with info message
    if (actionableIssues.length === 0) {
      const infoLines: string[] = [];

      if (largeTaskIssues.length > 0) {
        infoLines.push("**大規模タスク（ブロックしない）**:");
        infoLines.push("AGENTS.md例外: 「30分以上かかる大規模タスクはユーザー確認必須」");
        for (const issue of largeTaskIssues) {
          const number = issue.number ?? "?";
          const title = truncateTitle(issue.title ?? "No title");
          const labels = issue.labels ?? [];
          const labelNames = labels
            .map((l) => l.name)
            .filter((n) => LARGE_TASK_LABELS.has(n.toLowerCase()));
          const labelStr = labelNames.length > 0 ? ` [${labelNames.join(", ")}]` : "";
          infoLines.push(`  - #${number}: ${title}${labelStr}`);
        }
        infoLines.push("");
        infoLines.push("別セッションで計画的に対応してください。");
      }

      if (delegatedIssues.length > 0) {
        if (infoLines.length > 0) {
          infoLines.push("");
        }
        infoLines.push("**fork-sessionに委譲済みのIssue**:");
        for (const issue of delegatedIssues) {
          const number = issue.number ?? "?";
          const title = truncateTitle(issue.title ?? "No title");
          const prNumber = issue.delegatedPr;
          if (prNumber) {
            infoLines.push(`  - #${number}: ${title} → PR #${prNumber} が対応中`);
          } else {
            infoLines.push(`  - #${number}: ${title} → worktreeがロック中（対応中）`);
          }
        }
        infoLines.push("");
        infoLines.push("介入せず、fork-sessionの完了を待ちます。");
      }

      if (closedIssues.length > 0) {
        if (infoLines.length > 0) {
          infoLines.push("");
        }
        infoLines.push("**このセッションで作成・解決したIssue**:");
        for (const issue of closedIssues) {
          const number = issue.number ?? "?";
          const title = truncateTitle(issue.title ?? "No title");
          infoLines.push(`  - ✅ #${number}: ${title}`);
        }
      }

      if (infoLines.length > 0) {
        result.systemMessage = infoLines.join("\n");
      }

      // Only clear files if all issues are actually closed
      if (delegatedIssues.length === 0 && largeTaskIssues.length === 0) {
        clearSessionFiles(sessionId);
      }

      const logReason =
        `actionable: 0, large_task: ${largeTaskIssues.length}, ` +
        `delegated: ${delegatedIssues.length}, closed: ${closedIssues.length}`;
      await logHookExecution(HOOK_NAME, "approve", logReason, undefined, {
        sessionId: ctx.sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // There are actionable open issues - block until closed
    result = {
      continue: false,
      reason: formatBlockReason(actionableIssues, isFork),
    };
    await logHookExecution(
      HOOK_NAME,
      "block",
      `open=${actionableIssues.length}, large_task=${largeTaskIssues.length}, ` +
        `delegated=${delegatedIssues.length}, is_fork=${isFork}`,
      undefined,
      { sessionId: ctx.sessionId },
    );
  } catch (error) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", `error: ${formatError(error)}`, undefined, {
      sessionId: ctx.sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
