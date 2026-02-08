#!/usr/bin/env bun
/**
 * セッション開始時に前回の引き継ぎメモを読み込み表示。
 *
 * Why:
 *   Claude Codeはセッション間で記憶を保持しない。前回の作業状態、
 *   未対応タスク、教訓を引き継ぐことで、継続性を確保する。
 *
 * What:
 *   - セッション開始時（SessionStart）に発火
 *   - .claude/handoff/配下の有効なメモを読み込み
 *   - 自セッションと他セッションのメモを区別して表示
 *   - Git状態、オープンPR、ロック中worktreeも表示
 *
 * State:
 *   - reads: .claude/handoff/*.json
 *
 * Remarks:
 *   - 非ブロック型（情報表示のみ）
 *   - session-handoff-writerが生成、本フックが読み込み
 *   - メモの有効期間は24時間
 *   - Python版: session_handoff_reader.py
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#1333: 教訓抽出機能を追加
 *   - silenvx/dekita#2917: TypeScript版初期実装
 *   - silenvx/dekita#3053: formatAgeの将来日時ハンドリング改善
 */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { createContext, getSessionId, parseHookInput } from "../lib/session";

const HOOK_NAME = "session-handoff-reader";

// 引き継ぎメモの有効期間（24時間以内の場合のみ表示）
export const HANDOFF_VALIDITY_HOURS = 24;

interface GitInfo {
  branch?: string;
  uncommitted_changes?: number;
  untracked_files?: number;
}

interface PRInfo {
  number?: number;
  title?: string;
  branch?: string;
}

interface WorktreeInfo {
  path?: string;
  branch?: string;
  locked?: boolean;
}

interface SessionSummary {
  blocks?: number;
  block_reasons?: string[];
}

export interface HandoffMemo {
  session_id?: string;
  generated_at?: string;
  work_status?: string;
  next_action?: string;
  pending_tasks?: string[];
  lessons_learned?: string[];
  git?: GitInfo;
  open_prs?: PRInfo[];
  worktrees?: WorktreeInfo[];
  session_summary?: SessionSummary;
}

/**
 * Get project directory.
 */
function getProjectDir(): string {
  return process.env.CLAUDE_PROJECT_DIR || process.cwd();
}

/**
 * Get handoff directory.
 */
function getHandoffDir(): string {
  return join(getProjectDir(), ".claude", "handoff");
}

/**
 * Check if memo is within validity period.
 */
export function isMemoValid(memo: HandoffMemo): boolean {
  const generatedAt = memo.generated_at;
  if (!generatedAt) {
    return false;
  }

  try {
    const generatedTime = new Date(generatedAt).getTime();
    const now = Date.now();
    const ageHours = (now - generatedTime) / 1000 / 3600;
    return ageHours < HANDOFF_VALIDITY_HOURS;
  } catch {
    return false;
  }
}

/**
 * Load all valid handoff memos.
 */
function loadAllHandoffMemos(): HandoffMemo[] {
  const memos: HandoffMemo[] = [];
  const handoffDir = getHandoffDir();

  if (!existsSync(handoffDir)) {
    return memos;
  }

  try {
    const files = readdirSync(handoffDir);
    for (const file of files) {
      if (!file.endsWith(".json")) {
        continue;
      }

      try {
        const filePath = join(handoffDir, file);
        const content = readFileSync(filePath, "utf-8");
        const memo = JSON.parse(content) as HandoffMemo;
        if (isMemoValid(memo)) {
          memos.push(memo);
        }
      } catch {
        // 無効なJSONファイル、スキップ
      }
    }
  } catch {
    return memos;
  }

  // Sort by generated_at descending (newest first)
  memos.sort((a, b) => {
    const aTime = a.generated_at ? new Date(a.generated_at).getTime() : 0;
    const bTime = b.generated_at ? new Date(b.generated_at).getTime() : 0;
    return bTime - aTime;
  });

  return memos;
}

/**
 * Format age from generated_at timestamp.
 */
export function formatAge(generatedAt: string): string {
  try {
    if (!generatedAt) {
      return "不明";
    }
    const generatedTime = new Date(generatedAt).getTime();
    // Invalid Date returns NaN for getTime()
    if (Number.isNaN(generatedTime)) {
      return "不明";
    }
    const now = Date.now();
    const diffMs = now - generatedTime;

    // Handle future dates (clock skew, etc.)
    if (diffMs < 0) {
      return "不明";
    }

    const ageMinutes = Math.floor(diffMs / 1000 / 60);

    if (ageMinutes < 1) {
      return "たった今";
    }
    if (ageMinutes < 60) {
      return `${ageMinutes}分前`;
    }
    return `${Math.floor(ageMinutes / 60)}時間前`;
  } catch {
    return "不明";
  }
}

/**
 * Format handoff message from memos.
 */
export function formatHandoffMessage(
  memos: HandoffMemo[],
  currentSessionId: string | null,
): string {
  if (memos.length === 0) {
    return "";
  }

  const lines: string[] = ["📝 **セッション引き継ぎ情報**", ""];

  // Separate own session memos and other session memos
  const ownSessionMemos = memos.filter((m) => m.session_id === currentSessionId);
  const otherSessionMemos = memos.filter((m) => m.session_id !== currentSessionId);

  // Use own session memo if available, otherwise use the latest
  let latest: HandoffMemo;
  let isOwnSession: boolean;

  if (ownSessionMemos.length > 0) {
    latest = ownSessionMemos[0];
    isOwnSession = true;
  } else {
    latest = memos[0];
    isOwnSession = false;
  }

  const sessionLabel = isOwnSession ? "前回のセッション" : "別セッション";

  lines.push(`**${sessionLabel}からの引き継ぎ** (${formatAge(latest.generated_at || "")})`);
  lines.push("");

  // Work status
  const workStatus = latest.work_status || "不明";
  lines.push(`**状態**: ${workStatus}`);

  // Next action
  const nextAction = latest.next_action;
  if (nextAction) {
    lines.push(`**次にすべきこと**: ${nextAction}`);
  }

  // Pending tasks
  const pendingTasks = latest.pending_tasks || [];
  if (pendingTasks.length > 0) {
    lines.push("");
    lines.push("**⚠️ 未対応タスク**:");
    for (const task of pendingTasks.slice(0, 5)) {
      lines.push(`  - ${task}`);
    }
  }

  // Lessons learned
  const lessons = latest.lessons_learned || [];
  if (lessons.length > 0) {
    lines.push("");
    lines.push("**💡 前回の教訓**:");
    for (const lesson of lessons.slice(0, 3)) {
      lines.push(`  - ${lesson}`);
    }
  }

  lines.push("");

  // Git status
  const git = latest.git;
  if (git) {
    const branch = git.branch || "不明";
    const uncommitted = git.uncommitted_changes || 0;
    const untracked = git.untracked_files || 0;

    lines.push("**Git状態**:");
    lines.push(`  - ブランチ: \`${branch}\``);
    if (uncommitted > 0) {
      lines.push(`  - 未コミットの変更: ${uncommitted}件 ⚠️`);
    }
    if (untracked > 0) {
      lines.push(`  - 未追跡ファイル: ${untracked}件`);
    }
  }

  // Open PRs
  const openPrs = latest.open_prs || [];
  if (openPrs.length > 0) {
    lines.push("");
    lines.push("**オープンPR**:");
    for (const pr of openPrs.slice(0, 3)) {
      lines.push(`  - #${pr.number}: ${pr.title || ""} (\`${pr.branch || ""}\`)`);
    }
  }

  // Active worktrees
  const worktrees = latest.worktrees || [];
  const activeWorktrees = worktrees.filter((wt) => wt.locked);
  if (activeWorktrees.length > 0) {
    lines.push("");
    lines.push("**ロック中のworktree** (別セッションが作業中かも):");
    for (const wt of activeWorktrees.slice(0, 3)) {
      lines.push(`  - \`${wt.branch || "?"}\` @ ${wt.path || "?"}`);
    }
  }

  // Session summary
  const summary = latest.session_summary;
  if (summary && (summary.blocks || 0) > 0) {
    lines.push("");
    lines.push(`**前回のセッション**: ${summary.blocks}回ブロックされました`);
    const blockReasons = summary.block_reasons || [];
    if (blockReasons.length > 0) {
      lines.push("  最近のブロック理由:");
      for (const reason of blockReasons.slice(0, 2)) {
        const truncated = reason.slice(0, 60);
        const suffix = reason.length > 60 ? "..." : "";
        lines.push(`    - ${truncated}${suffix}`);
      }
    }
  }

  // Other session memos
  if (otherSessionMemos.length > 0) {
    lines.push("");
    lines.push("---");
    lines.push(`_他に${otherSessionMemos.length}件の並列セッションの引き継ぎがあります_`);

    // Show important tasks or lessons from other sessions
    for (const memo of otherSessionMemos.slice(0, 2)) {
      const pending = memo.pending_tasks || [];
      const memoLessons = memo.lessons_learned || [];
      if (pending.length > 0 || memoLessons.length > 0) {
        const age = formatAge(memo.generated_at || "");
        lines.push(`  (${age}):`);
        for (const task of pending.slice(0, 2)) {
          lines.push(`    - ⚠️ ${task}`);
        }
        for (const lesson of memoLessons.slice(0, 1)) {
          lines.push(`    - 💡 ${lesson}`);
        }
      }
    }
  }

  return lines.join("\n");
}

async function main(): Promise<void> {
  const result: { continue: boolean; message?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;
    const ctx = createContext(inputData);
    const currentSessionId = (inputData.session_id as string) || getSessionId(ctx);

    // Load all valid memos
    const memos = loadAllHandoffMemos();

    if (memos.length > 0) {
      const message = formatHandoffMessage(memos, currentSessionId);
      if (message) {
        result.message = message;
      }

      await logHookExecution(
        HOOK_NAME,
        "approve",
        "Handoff memos displayed",
        {
          memo_count: memos.length,
          latest_work_status: memos[0].work_status,
          has_pending_tasks: memos.some((m) => m.pending_tasks && m.pending_tasks.length > 0),
          has_lessons: memos.some((m) => m.lessons_learned && m.lessons_learned.length > 0),
        },
        { sessionId },
      );
    } else {
      await logHookExecution(HOOK_NAME, "approve", "No valid handoff memos found", undefined, {
        sessionId,
      });
    }
  } catch (error) {
    // Continue even on error
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Error loading handoff memos: ${formatError(error)}`,
      undefined,
      { sessionId },
    );
  }

  console.log(JSON.stringify(result));
}

// Only run main when executed directly, not when imported
if (import.meta.main) {
  main();
}
