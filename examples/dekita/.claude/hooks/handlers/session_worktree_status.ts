#!/usr/bin/env bun
/**
 * セッション開始時に既存worktreeの状況を確認し警告する。
 *
 * Why:
 *   別セッションが作業中のworktreeに介入すると、競合やコンフリクトが発生する。
 *   セッション開始時に既存worktreeの状況を把握することで、問題を未然に防ぐ。
 *
 * What:
 *   - CWDがworktree内かどうかをチェック
 *   - 既存worktreeの一覧を取得（git worktree list）
 *   - 各worktreeの.claude-sessionマーカーを確認
 *   - 別セッションIDのマーカーがある場合、警告表示
 *   - 直近1時間以内のコミットがあるworktreeも警告
 *   - fork-sessionの場合、祖先セッションのworktreeへの介入を禁止警告
 *
 * State:
 *   reads: .worktrees/{name}/.claude-session
 *   reads: .claude/logs/flow/session-created-issues-{session}.json
 *
 * Remarks:
 *   - ブロックせず警告のみ（実際のブロックはworktree-session-guard.tsが担当）
 *   - fork-session-collaboration-advisorは提案、これは警告
 *   - Python版: session_worktree_status.py
 *
 * Changelog:
 *   - silenvx/dekita#1383: CWDがworktree内かどうかの検出追加
 *   - silenvx/dekita#1416: フック追加
 *   - silenvx/dekita#2466: fork-sessionの祖先worktree介入警告追加
 *   - silenvx/dekita#2475: fork-sessionで自作Issue許可
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { basename, join } from "node:path";
import { FLOW_LOG_DIR } from "../lib/common";
import {
  RECENT_COMMIT_THRESHOLD_SECONDS,
  SESSION_GAP_THRESHOLD,
  SESSION_MARKER_FILE,
  TIMEOUT_LIGHT,
  TIMEOUT_MEDIUM,
} from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import {
  createContext,
  getSessionAncestry,
  getSessionId,
  isForkSession,
  parseHookInput,
} from "../lib/session";

const HOOK_NAME = "session-worktree-status";
const WORKTREES_DIR = ".worktrees";

interface WorktreeInfo {
  path: string;
  locked: boolean;
}

interface SessionMarker {
  session_id: string;
  created_at: string;
}

/**
 * Sanitize session ID to prevent path traversal.
 */
function sanitizeSessionId(sessionId: string): string {
  return basename(sessionId);
}

/**
 * Load list of issue numbers created in this session.
 */
function loadSessionCreatedIssues(sessionId: string): number[] {
  const safeSessionId = sanitizeSessionId(sessionId);
  const issuesFile = join(FLOW_LOG_DIR, `session-created-issues-${safeSessionId}.json`);

  if (existsSync(issuesFile)) {
    try {
      const content = readFileSync(issuesFile, "utf-8");
      const data = JSON.parse(content);
      if (typeof data === "object" && data !== null && !Array.isArray(data)) {
        const issues = data.issues;
        if (Array.isArray(issues) && issues.every((n: unknown) => typeof n === "number")) {
          return issues;
        }
      }
    } catch {
      // Best effort - corrupted data is ignored
    }
  }
  return [];
}

/**
 * Check if current working directory is inside a worktree.
 */
function getCwdWorktreeInfo(): { worktreeName: string; mainRepoPath: string } | null {
  try {
    const cwd = process.cwd();
    const parts = cwd.split("/");

    for (let i = 0; i < parts.length; i++) {
      if (parts[i] === WORKTREES_DIR) {
        // Next part after .worktrees is the worktree name
        if (i + 1 < parts.length) {
          const worktreeName = parts[i + 1];
          // Main repo path is everything before .worktrees
          const mainRepoPath = parts.slice(0, i).join("/") || "/";
          return { worktreeName, mainRepoPath };
        }
      }
    }
  } catch {
    // CWD access error (deleted directory, etc.)
  }
  return null;
}

/**
 * Get list of worktree directories and their lock status from git.
 */
function getWorktreesInfo(): WorktreeInfo[] {
  try {
    const result = execSync("git worktree list --porcelain", {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    const worktreesInfo: WorktreeInfo[] = [];
    // Split by double newline to get blocks for each worktree
    const blocks = result.trim().split("\n\n");

    for (const block of blocks) {
      const lines = block.split("\n");
      if (!lines[0]?.startsWith("worktree ")) {
        continue;
      }

      const path = lines[0].slice(9).trim();

      // Only include worktrees in .worktrees directory
      if (!path.includes(`/${WORKTREES_DIR}/`)) {
        continue;
      }

      // Check if locked
      const isLocked = lines.some((line) => line.startsWith("locked"));
      worktreesInfo.push({ path, locked: isLocked });
    }

    return worktreesInfo;
  } catch {
    // Fail-open
    return [];
  }
}

/**
 * Read session marker from worktree marker file.
 */
function readSessionMarker(worktreePath: string): SessionMarker | null {
  const markerPath = join(worktreePath, SESSION_MARKER_FILE);
  try {
    if (existsSync(markerPath)) {
      const content = readFileSync(markerPath, "utf-8").trim();
      const data = JSON.parse(content);
      return {
        session_id: data.session_id || "",
        created_at: data.created_at || "",
      };
    }
  } catch {
    // Fail-open
  }
  return null;
}

/**
 * Get age of marker in seconds from created_at timestamp.
 */
function getMarkerAgeSeconds(marker: SessionMarker): number | null {
  const createdAt = marker.created_at;
  if (!createdAt) {
    return null;
  }
  try {
    const createdTime = new Date(createdAt).getTime();
    const now = Date.now();
    return Math.floor((now - createdTime) / 1000);
  } catch {
    return null;
  }
}

/**
 * Get seconds since the most recent commit in the worktree.
 */
function getRecentCommitTime(worktreePath: string): number | null {
  try {
    const result = execSync(`git -C "${worktreePath}" log -1 --format=%ct`, {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    const trimmed = result.trim();
    if (trimmed) {
      const commitTime = Number.parseInt(trimmed, 10);
      return Math.floor(Date.now() / 1000) - commitTime;
    }
  } catch {
    // Fail-open
  }
  return null;
}

/**
 * Check if worktree has uncommitted changes.
 */
function hasUncommittedChanges(worktreePath: string): boolean {
  try {
    const result = execSync(`git -C "${worktreePath}" status --porcelain`, {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return result.trim().length > 0;
  } catch {
    // Fail-open
    return false;
  }
}

/**
 * Escape a path for shell command in markdown code block.
 */
export function shellQuote(path: string): string {
  // If the path contains single quote, use double quotes, otherwise use single quotes
  if (path.includes("'")) {
    return `"${path.replace(/"/g, '\\"')}"`;
  }
  return `'${path}'`;
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };

  try {
    const data = await parseHookInput();
    const ctx = createContext(data);
    const currentSession = getSessionId(ctx) || "";

    // Issue #2466: Detect fork-session and get ancestor sessions
    const source = data.source || "";
    const transcriptPath = data.transcript_path;
    const isFork = currentSession ? isForkSession(currentSession, source, transcriptPath) : false;
    let ancestorSessions: string[] = [];
    if (isFork && transcriptPath) {
      ancestorSessions = getSessionAncestry(transcriptPath);
      // Remove current session from ancestors if present
      ancestorSessions = ancestorSessions.filter((s) => s !== currentSession);
    }

    const warnings: string[] = [];
    const forkSessionWarnings: string[] = [];
    let cwdWarningPrefix = "";

    // Check if CWD is inside a worktree (Issue #1383)
    const cwdWorktreeInfo = getCwdWorktreeInfo();
    if (cwdWorktreeInfo) {
      const { worktreeName, mainRepoPath } = cwdWorktreeInfo;
      const quotedPath = shellQuote(mainRepoPath);
      cwdWarningPrefix = `**CWDがworktree内です: ${worktreeName}**\n\nセッション継続後もCWDがworktree内のままになっています。\nworktree削除がブロックされる可能性があるため、mainリポジトリに移動してください:\n\n\`\`\`\ncd ${quotedPath}\n\`\`\`\n\n`;
      await logHookExecution(HOOK_NAME, "warn", `CWD is inside worktree: ${worktreeName}`);
    }

    // Get list of worktrees with lock status (single git call)
    const worktreesInfo = getWorktreesInfo();
    if (worktreesInfo.length === 0 && !cwdWarningPrefix) {
      await logHookExecution(HOOK_NAME, "skip", "No worktrees found");
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Check each worktree for issues
    for (const info of worktreesInfo) {
      const worktreePath = info.path;
      const worktreeName = basename(worktreePath);
      const issues: string[] = [];

      // Check session marker
      const marker = readSessionMarker(worktreePath);
      if (marker) {
        const markerSession = marker.session_id;
        const markerAge = getMarkerAgeSeconds(marker);

        if (markerSession && markerSession !== currentSession) {
          // Different session ID
          const sessionDisplay =
            markerSession.length > 16 ? `${markerSession.slice(0, 16)}...` : markerSession;

          // Issue #2466: Check if this is an ancestor session's worktree
          if (isFork && ancestorSessions.includes(markerSession)) {
            forkSessionWarnings.push(`- **${worktreeName}**: 元セッション（fork元）のworktree`);
            await logHookExecution(
              HOOK_NAME,
              "warn",
              `Fork-session detected ancestor worktree: ${worktreeName}`,
            );
          } else {
            issues.push(`別セッション: ${sessionDisplay}`);
          }
        } else if (markerSession === currentSession && markerAge !== null) {
          // Same session ID but check if marker is stale
          if (markerAge > SESSION_GAP_THRESHOLD) {
            const minutes = Math.floor(markerAge / 60);
            issues.push(`古いセッションマーカー: ${minutes}分前`);
          }
        }
      }

      // Check if locked
      if (info.locked) {
        issues.push("ロック中");
      }

      // Check uncommitted changes
      if (hasUncommittedChanges(worktreePath)) {
        issues.push("未コミット変更あり");
      }

      // Check recent commits
      const secondsSinceCommit = getRecentCommitTime(worktreePath);
      if (secondsSinceCommit !== null) {
        if (secondsSinceCommit < RECENT_COMMIT_THRESHOLD_SECONDS) {
          const minutes = Math.floor(secondsSinceCommit / 60);
          if (minutes < 1) {
            issues.push("直近1分未満にコミット");
          } else {
            issues.push(`直近${minutes}分前にコミット`);
          }
        }
      }

      if (issues.length > 0) {
        warnings.push(`- **${worktreeName}**: ${issues.join(", ")}`);
      }
    }

    // Build final warning message
    if (warnings.length > 0 || cwdWarningPrefix || forkSessionWarnings.length > 0) {
      const warningParts: string[] = ["[session-worktree-status]"];

      // Add CWD warning if present
      if (cwdWarningPrefix) {
        warningParts.push(cwdWarningPrefix);
      }

      // Issue #2466: Add fork-session warning
      // Issue #2475: 自作Issueリストを表示
      if (forkSessionWarnings.length > 0) {
        const selfCreatedIssues = currentSession ? loadSessionCreatedIssues(currentSession) : [];
        let selfCreatedNote: string;
        if (selfCreatedIssues.length > 0) {
          const issueList = selfCreatedIssues.map((n) => `#${n}`).join(", ");
          selfCreatedNote = `\n**このセッションで作成したIssue**: ${issueList}\nこれらのIssueへの作業は**警告なしで許可**されています。\n`;
        } else {
          selfCreatedNote =
            "\n**このセッションで作成したIssue**: " +
            "（このセッション中に作成したIssueはまだありません）\n" +
            "今後このセッションで新しく作成したIssueへの作業は" +
            "**警告なしで許可**されます。\n";
        }

        warningParts.push(
          [
            "⚠️ **fork-session検出**: 元セッション（fork元）のworktreeがあります:",
            "",
            ...forkSessionWarnings,
            "",
            "**これらのworktreeへの介入は禁止です。**",
            "元セッションがまだ作業中の可能性があります。",
            selfCreatedNote,
            "その場合は、元セッションのworktreeとは**別の新しいworktree**を作成してください。",
            "",
          ].join("\n"),
        );
      }

      // Add worktree warnings if present
      if (warnings.length > 0) {
        warningParts.push(
          `以下のworktreeに注意が必要です:\n\n${warnings.join("\n")}\n\nこれらのworktreeに関連するIssueは作業中の可能性があります。\nAGENTS.mdのルールに従い、以下を確認してください:\n- worktreeがロック中 or 未コミット変更あり → 作業開始しない\n- 直近1時間以内のコミット → ユーザーに確認\n- 別セッションマーカー → 引き継がず他のIssueへ\n- 古いセッションマーカー → コンテキスト継続の可能性。ユーザーに確認\n`,
        );
      }

      let warningMsg = warningParts.slice(0, 2).join(" ");
      if (warningParts.length > 2) {
        warningMsg += `\n\n${warningParts.slice(2).join("\n\n")}`;
      }

      await logHookExecution(
        HOOK_NAME,
        "warn",
        `Found ${warnings.length} worktrees requiring attention, ` +
          `fork-session warnings: ${forkSessionWarnings.length}, ` +
          `CWD in worktree: ${Boolean(cwdWarningPrefix)}`,
      );
      result.systemMessage = warningMsg;
    } else {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Checked ${worktreesInfo.length} worktrees, all clear`,
      );
    }
  } catch (error) {
    // Fail open - don't block on errors
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

// Only run main when executed directly, not when imported
if (import.meta.main) {
  main();
}
