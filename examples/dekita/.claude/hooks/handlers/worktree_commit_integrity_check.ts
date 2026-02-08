#!/usr/bin/env bun
/**
 * セッション開始時にworktree内のコミット整合性をチェック。
 *
 * Why:
 *   セッション引き継ぎ時、worktreeに複数Issueの変更が混在していると
 *   状態が混乱する。開始時にチェックして問題を早期に警告する。
 *
 * What:
 *   - セッション開始時（SessionStart）に発火
 *   - main..HEADのコミット履歴を取得
 *   - コミットメッセージからIssue番号を抽出
 *   - 複数Issue混在やマージ済みコミットがあれば警告
 *
 * State:
 *   - writes: .claude/logs/flow/worktree-integrity-*.jsonl
 *
 * Remarks:
 *   - 警告型フック（ブロックしない）
 *   - cwdがworktree外ならスキップ
 *   - session-worktree-statusは一般警告、本フックはコミット内容分析
 *   - Python版: worktree_commit_integrity_check.py
 *
 * Changelog:
 *   - silenvx/dekita#1691: フック追加
 *   - silenvx/dekita#1840: セッション別ログファイル移行
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { basename } from "node:path";
import { FLOW_LOG_DIR } from "../lib/common";
import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { logToSessionFile } from "../lib/logging";
import { createContext, getSessionId, parseHookInput } from "../lib/session";
import { getLocalTimestamp } from "../lib/timestamp";

const HOOK_NAME = "worktree-commit-integrity-check";
const WORKTREES_DIR = ".worktrees";

// Pattern to extract Issue numbers from commit messages
const ISSUE_PATTERN = /#(\d+)/g;

export interface CommitInfo {
  hash: string;
  subject: string;
  body: string;
}

/**
 * Check if CWD is inside a worktree.
 *
 * @param cwd - Current working directory to check (defaults to process.cwd())
 * @returns Tuple of [is_in_worktree, worktree_name or null]
 */
export function isInWorktree(cwd: string = process.cwd()): [boolean, string | null] {
  try {
    // Normalize Windows backslashes to forward slashes for cross-platform support
    const parts = cwd.replace(/\\/g, "/").split("/");

    for (let i = 0; i < parts.length; i++) {
      if (parts[i] === WORKTREES_DIR) {
        if (i + 1 < parts.length) {
          return [true, parts[i + 1]];
        }
      }
    }
  } catch {
    // CWD access error (deleted directory, etc.)
  }
  return [false, null];
}

/**
 * Get list of commits since diverging from main.
 *
 * @returns Tuple of [commits list, error message or null]
 */
function getCommitsSinceMain(): [CommitInfo[], string | null] {
  try {
    // Get commit hashes and subjects
    const result = execSync("git log --oneline main..HEAD", {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    const commits: CommitInfo[] = [];
    for (const line of result.trim().split("\n")) {
      if (!line) {
        continue;
      }
      const parts = line.split(" ");
      const commitHash = parts[0];
      const subject = parts.slice(1).join(" ");

      // Get full commit message body for Issue extraction
      let body = "";
      try {
        const bodyResult = execSync(`git log -1 --format=%b ${commitHash}`, {
          encoding: "utf-8",
          timeout: TIMEOUT_LIGHT * 1000,
          stdio: ["pipe", "pipe", "pipe"],
        });
        body = bodyResult.trim();
      } catch {
        // Fail-open
      }

      commits.push({ hash: commitHash, subject, body });
    }

    return [commits, null];
  } catch (error) {
    if (error instanceof Error) {
      // Check if it's a timeout
      if (error.message.includes("TIMEOUT")) {
        return [[], "git command timed out"];
      }
      // Check for stderr in execSync error
      const execError = error as { stderr?: string };
      if (execError.stderr) {
        return [[], execError.stderr.trim() || "git log failed"];
      }
      return [[], error.message || "git log failed"];
    }
    return [[], "git log failed"];
  }
}

/**
 * Extract Issue numbers from commits.
 *
 * @returns Map of Issue number to list of commit hashes referencing it
 */
export function extractIssueNumbers(commits: CommitInfo[]): Map<number, string[]> {
  const issueToCommits = new Map<number, string[]>();

  for (const commit of commits) {
    // Search in both subject and body
    const text = `${commit.subject} ${commit.body}`;
    const matches = text.matchAll(ISSUE_PATTERN);

    for (const match of matches) {
      const issueNum = Number.parseInt(match[1], 10);
      if (!issueToCommits.has(issueNum)) {
        issueToCommits.set(issueNum, []);
      }
      const commitList = issueToCommits.get(issueNum)!;
      if (!commitList.includes(commit.hash)) {
        commitList.push(commit.hash);
      }
    }
  }

  return issueToCommits;
}

/**
 * Check if any commits are already merged to main.
 *
 * @returns List of commits that are already merged
 */
function checkMergedCommits(commits: CommitInfo[]): CommitInfo[] {
  const merged: CommitInfo[] = [];

  for (const commit of commits) {
    try {
      // Check if commit is in origin/main
      const result = execSync(`git branch --contains ${commit.hash} -r`, {
        encoding: "utf-8",
        timeout: TIMEOUT_LIGHT * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      });
      if (result.includes("origin/main")) {
        merged.push(commit);
      }
    } catch {
      // Error ignored - fail-open pattern
    }
  }

  return merged;
}

/**
 * Get git status output.
 */
function getGitStatus(): string {
  try {
    const result = execSync("git status --short", {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return result.trim();
  } catch {
    return "";
  }
}

/**
 * Log worktree state to flow log directory.
 */
async function logWorktreeState(
  sessionId: string | null,
  worktreeName: string,
  commits: CommitInfo[],
  issueMap: Map<number, string[]>,
  mergedCommits: CommitInfo[],
  gitStatus: string,
): Promise<void> {
  if (!sessionId) {
    return;
  }

  const entry = {
    timestamp: getLocalTimestamp(),
    worktree: worktreeName,
    commit_count: commits.length,
    commits: commits.slice(0, 10).map((c) => ({
      hash: c.hash,
      subject: c.subject.slice(0, 80),
    })),
    issue_numbers: Array.from(issueMap.keys()),
    multiple_issues: issueMap.size > 1,
    merged_commit_count: mergedCommits.length,
    merged_commits: mergedCommits.map((c) => c.hash),
    has_uncommitted_changes: Boolean(gitStatus),
    git_status_lines: gitStatus ? gitStatus.split("\n").length : 0,
  };

  await logToSessionFile(FLOW_LOG_DIR, "worktree-integrity", sessionId, entry);
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };

  try {
    const data = await parseHookInput();
    const ctx = createContext(data);
    const sessionId = getSessionId(ctx);
    // Sanitize sessionId early to prevent path traversal
    const safeSessionId = sessionId ? basename(sessionId) : null;

    // Check if in worktree
    const [inWorktree, worktreeName] = isInWorktree();
    if (!inWorktree) {
      await logHookExecution(HOOK_NAME, "skip", "Not in worktree");
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Get commits since main
    const [commits, gitError] = getCommitsSinceMain();
    if (gitError) {
      // Git error - warn user instead of silently approving
      await logHookExecution(HOOK_NAME, "warn", `Git error in ${worktreeName}: ${gitError}`);
      const warningMsg = `[${HOOK_NAME}] **${worktreeName}** gitエラー\n\nコミット履歴の取得に失敗しました: ${gitError}\n\n整合性チェックが実行できません。以下を確認してください:\n- \`git fetch origin main\` でmainブランチを取得\n- \`git log main..HEAD\` が正常に動作するか確認`;
      console.log(JSON.stringify({ continue: true, systemMessage: warningMsg }));
      return;
    }

    if (commits.length === 0) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Worktree ${worktreeName}: no commits since main`,
      );
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Extract Issue numbers
    const issueMap = extractIssueNumbers(commits);

    // Check for merged commits
    const mergedCommits = checkMergedCommits(commits);

    // Get git status
    const gitStatus = getGitStatus();

    // Log state to flow logs
    await logWorktreeState(
      safeSessionId,
      worktreeName || "unknown",
      commits,
      issueMap,
      mergedCommits,
      gitStatus,
    );

    // Build warning message
    const warnings: string[] = [];

    // Multiple Issues warning
    if (issueMap.size > 1) {
      const issueList = Array.from(issueMap.keys())
        .sort((a, b) => a - b)
        .map((num) => `#${num}`)
        .join(", ");
      warnings.push(
        `**複数のIssueが検出されました**: ${issueList}\n  - worktree内に複数Issueの変更が混在しています\n  - リベース時にコンフリクトが発生する可能性があります\n  - 関係のないコミットを \`git rebase -i\` で除外することを検討してください`,
      );
    }

    // Merged commits warning
    if (mergedCommits.length > 0) {
      const mergedList = mergedCommits
        .slice(0, 3)
        .map((c) => c.hash)
        .join(", ");
      warnings.push(
        `**既にマージ済みのコミットがあります**: ${mergedList}\n  - これらのコミットはmainに既にマージされています\n  - \`git rebase main\` でコンフリクトが発生する可能性が高いです\n  - \`git rebase -i main\` で該当コミットをdropすることを検討してください`,
      );
    }

    if (warnings.length > 0) {
      const messageParts = [
        `[${HOOK_NAME}] **${worktreeName}** の状態確認`,
        "",
        `コミット数: ${commits.length} (main..HEAD)`,
      ];

      if (issueMap.size > 0) {
        const issueList = Array.from(issueMap.keys())
          .sort((a, b) => a - b)
          .map((n) => `#${n}`)
          .join(", ");
        messageParts.push(`関連Issue: ${issueList}`);
      }

      messageParts.push("");
      messageParts.push(...warnings);

      await logHookExecution(HOOK_NAME, "warn", `Integrity issues found in ${worktreeName}`, {
        commit_count: commits.length,
        issue_count: issueMap.size,
        merged_count: mergedCommits.length,
      });
      result.systemMessage = messageParts.join("\n");
    } else {
      // No warnings but log the state
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Worktree ${worktreeName}: ${commits.length} commits, ${issueMap.size} issues`,
        {
          commit_count: commits.length,
          issue_count: issueMap.size,
          issues: Array.from(issueMap.keys()),
        },
      );
    }
  } catch (error) {
    // Fail open - don't block on errors
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

// Only run when executed directly (not when imported for tests)
if (import.meta.main) {
  main();
}
