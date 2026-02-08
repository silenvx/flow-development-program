#!/usr/bin/env bun
/**
 * セッション終了時に引き継ぎメモを生成。
 *
 * Why:
 *   Claude Codeはセッション間で記憶を保持しない。作業状態、
 *   未対応タスク、教訓を記録して次のセッションに引き継ぐ。
 *
 * What:
 *   - セッション終了時（Stop）に発火
 *   - Git状態、worktree、オープンPRを収集
 *   - セッションサマリー（ブロック回数等）を抽出
 *   - ブロック理由から教訓を自動生成
 *   - .claude/handoff/にセッションIDベースで保存
 *
 * State:
 *   - reads: .claude/logs/execution/hook-execution-*.jsonl
 *   - writes: .claude/handoff/{session_id}.json
 *
 * Remarks:
 *   - 非ブロック型（情報保存のみ）
 *   - session-handoff-readerが読み込み、本フックが生成
 *   - 古いファイルは10個まで保持（自動クリーンアップ）
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#1333: ブロック理由からの教訓抽出
 *   - silenvx/dekita#2545: HookContextパターン移行
 *   - silenvx/dekita#3161: TypeScript移行
 */

import { mkdir, readdir, stat, unlink, writeFile } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { EXECUTION_LOG_DIR, TIMEOUT_HEAVY, TIMEOUT_LIGHT } from "../lib/common";
import { formatError } from "../lib/format_error";
import { getCurrentBranch } from "../lib/git";
import { logHookExecution, readSessionLogEntries } from "../lib/logging";
import { approveAndExit } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";

const HOOK_NAME = "session_handoff_writer";

// 引き継ぎメモの保存先
// Issue #3161: Must match reader path at .claude/handoff (not .claude/hooks/handoff)
// __dirname = .claude/hooks/handlers, so need 2 levels up to reach .claude
const HANDOFF_DIR = resolve(dirname(dirname(__dirname)), "handoff");

// 保持するハンドオフファイルの最大数
const MAX_HANDOFF_FILES = 10;

// =============================================================================
// Types
// =============================================================================

interface GitStatus {
  branch: string | null;
  uncommitted_changes: number;
  untracked_files: number;
}

interface WorktreeInfo {
  path: string;
  branch?: string;
  locked?: boolean;
}

interface PrInfo {
  number: number;
  title: string;
  branch: string;
}

interface SessionSummary {
  hook_executions: number;
  blocks: number;
  block_reasons: string[];
}

interface HandoffMemo {
  generated_at: string;
  session_id: string | null | undefined;
  work_status: string;
  next_action: string;
  git: GitStatus;
  worktrees: WorktreeInfo[];
  open_prs: PrInfo[];
  session_summary: SessionSummary;
  pending_tasks: string[];
  lessons_learned: string[];
}

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Get git status information.
 */
async function getGitStatus(): Promise<GitStatus> {
  const status: GitStatus = {
    branch: await getCurrentBranch(),
    uncommitted_changes: 0,
    untracked_files: 0,
  };

  try {
    const result = await asyncSpawn("git", ["status", "--porcelain"], {
      timeout: TIMEOUT_LIGHT * 1000,
    });

    if (result.exitCode === 0) {
      const lines = result.stdout
        .trim()
        .split("\n")
        .filter((line) => line.length > 0);
      status.uncommitted_changes = lines.filter((line) => !line.startsWith("??")).length;
      status.untracked_files = lines.filter((line) => line.startsWith("??")).length;
    }
  } catch {
    // Best effort - git command may fail
  }

  return status;
}

/**
 * Get active worktrees.
 */
async function getActiveWorktrees(): Promise<WorktreeInfo[]> {
  const worktrees: WorktreeInfo[] = [];

  try {
    const result = await asyncSpawn("git", ["worktree", "list", "--porcelain"], {
      timeout: TIMEOUT_LIGHT * 1000,
    });

    if (result.exitCode === 0) {
      let currentWorktree: WorktreeInfo | null = null;

      for (const line of result.stdout.trim().split("\n")) {
        if (line.startsWith("worktree ")) {
          if (currentWorktree) {
            worktrees.push(currentWorktree);
          }
          currentWorktree = { path: line.slice(9) };
        } else if (line.startsWith("branch ") && currentWorktree) {
          currentWorktree.branch = line.slice(7).replace("refs/heads/", "");
        } else if (line.startsWith("locked") && currentWorktree) {
          // Handle both "locked" and "locked <reason>" formats
          currentWorktree.locked = true;
        }
      }

      if (currentWorktree) {
        worktrees.push(currentWorktree);
      }
    }
  } catch {
    // Best effort - git command may fail
  }

  return worktrees;
}

/**
 * Get open PRs authored by current user.
 */
async function getOpenPrs(): Promise<PrInfo[]> {
  const prs: PrInfo[] = [];

  try {
    const result = await asyncSpawn(
      "gh",
      [
        "pr",
        "list",
        "--author",
        "@me",
        "--state",
        "open",
        "--json",
        "number,title,headRefName",
        "--limit",
        "5",
      ],
      {
        timeout: TIMEOUT_HEAVY * 1000,
      },
    );

    if (result.exitCode === 0 && result.stdout.trim()) {
      const parsed = JSON.parse(result.stdout) as Array<{
        number: number;
        title: string;
        headRefName: string;
      }>;
      for (const pr of parsed) {
        prs.push({
          number: pr.number,
          title: pr.title,
          branch: pr.headRefName,
        });
      }
    }
  } catch {
    // Best effort - gh command may fail
  }

  return prs;
}

/**
 * Get session summary from execution logs.
 */
async function getSessionSummary(sessionId: string): Promise<SessionSummary> {
  const summary: SessionSummary = {
    hook_executions: 0,
    blocks: 0,
    block_reasons: [],
  };

  // EXECUTION_LOG_DIR is already an absolute path from lib/common
  const entries = await readSessionLogEntries(EXECUTION_LOG_DIR, "hook-execution", sessionId);

  for (const entry of entries) {
    summary.hook_executions++;
    if (entry.decision === "block") {
      summary.blocks++;
      const reason = entry.reason as string | undefined;
      if (reason) {
        // 重複を避けつつ最大5件まで
        if (!summary.block_reasons.includes(reason) && summary.block_reasons.length < 5) {
          summary.block_reasons.push(reason.slice(0, 100));
        }
      }
    }
  }

  return summary;
}

/**
 * Extract lessons learned from block reasons.
 *
 * Issue #1333: フックブロックのパターンから学習ポイントを生成。
 */
function extractLessonsLearned(blockReasons: string[]): string[] {
  if (blockReasons.length === 0) {
    return [];
  }

  const lessons: string[] = [];
  const seenPatterns = new Set<string>();

  // ブロック理由からパターンを検出して教訓化
  // キーはパターン（英語・日本語両対応）、値は教訓
  // 注意: より具体的なパターンを先に定義（codex → review, worktree → lock）
  const lessonPatterns: Record<string, string> = {
    codex: "pushの前にcodex reviewを実行してレビューを受ける",
    worktree: "worktreeの操作には注意が必要（パス確認、ロック状態の確認）",
    merge: "マージ前にレビュースレッドの解決とCI通過を確認する",
    マージ: "マージ前にレビュースレッドの解決とCI通過を確認する",
    push: "pushの前にcodex reviewを実行する",
    main: "mainブランチでの直接編集は避け、worktreeで作業する",
    edit: "編集前にファイルの存在と権限を確認する",
    branch: "ブランチ操作の前に現在の状態を確認する",
    lock: "ロックされたworktreeは他セッションが作業中の可能性がある",
    ロック: "ロックされたworktreeは他セッションが作業中の可能性がある",
    review: "レビューコメントには署名を付けて返信する",
    レビュー: "レビューコメントには署名を付けて返信する",
  };

  for (const reason of blockReasons) {
    const reasonLower = reason.toLowerCase();
    for (const [pattern, lesson] of Object.entries(lessonPatterns)) {
      if (reasonLower.includes(pattern) && !seenPatterns.has(lesson)) {
        lessons.push(lesson);
        seenPatterns.add(lesson);
        break; // 1つのブロック理由から1つの教訓のみ
      }
    }
  }

  return lessons.slice(0, 5); // 最大5件まで
}

/**
 * Generate handoff memo.
 *
 * Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。
 */
async function generateHandoffMemo(sessionId: string | null | undefined): Promise<HandoffMemo> {
  const now = new Date();

  // Normalize session_id to string (handle null case)
  const effectiveSessionId = sessionId ?? "unknown";

  const gitStatus = await getGitStatus();
  const worktrees = await getActiveWorktrees();
  const openPrs = await getOpenPrs();
  const sessionSummary = await getSessionSummary(effectiveSessionId);

  // 作業状態の推測
  let workStatus = "不明";
  let nextAction = "前回の作業を確認してください";

  if (gitStatus.uncommitted_changes > 0) {
    workStatus = "作業途中（未コミットの変更あり）";
    nextAction = "未コミットの変更を確認し、コミットまたは破棄してください";
  } else if (openPrs.length > 0) {
    workStatus = `PR作業中（${openPrs.length}件のオープンPR）`;
    nextAction = "オープンPRのレビュー状態を確認してください";
  } else if (gitStatus.branch && gitStatus.branch !== "main") {
    workStatus = `フィーチャーブランチ '${gitStatus.branch}' で作業中`;
    nextAction = "ブランチの作業を完了するか、mainに戻るか判断してください";
  } else {
    workStatus = "待機状態";
    nextAction = "新しいタスクを開始できます";
  }

  return {
    generated_at: now.toISOString(),
    session_id: sessionId,
    work_status: workStatus,
    next_action: nextAction,
    git: gitStatus,
    worktrees,
    open_prs: openPrs,
    session_summary: sessionSummary,
    pending_tasks: [],
    lessons_learned: extractLessonsLearned(sessionSummary.block_reasons),
  };
}

/**
 * Delete old handoff files (keep only MAX_HANDOFF_FILES).
 */
async function cleanupOldHandoffs(): Promise<void> {
  try {
    await mkdir(HANDOFF_DIR, { recursive: true });
    const files = await readdir(HANDOFF_DIR);

    // Get all .json files with their modification times
    const handoffFiles: Array<{ path: string; mtime: number }> = [];
    for (const file of files) {
      if (!file.endsWith(".json")) continue;

      const filePath = join(HANDOFF_DIR, file);
      try {
        const fileStat = await stat(filePath);
        handoffFiles.push({ path: filePath, mtime: fileStat.mtimeMs });
      } catch {
        // Skip files that can't be stat'd
      }
    }

    // Sort by mtime descending (newest first)
    handoffFiles.sort((a, b) => b.mtime - a.mtime);

    // Delete files beyond MAX_HANDOFF_FILES
    for (const oldFile of handoffFiles.slice(MAX_HANDOFF_FILES)) {
      try {
        await unlink(oldFile.path);
      } catch {
        // 並列セッションで既に削除された場合は無視
      }
    }
  } catch {
    // ファイルシステムエラーは致命的ではないため継続
  }
}

/**
 * Save handoff memo to session ID-based file.
 */
async function saveHandoffMemo(memo: HandoffMemo): Promise<boolean> {
  const sessionId = memo.session_id ?? "unknown";

  try {
    await mkdir(HANDOFF_DIR, { recursive: true });

    // セッションIDベースのファイル名
    const safeSessionId = basename(sessionId);
    const handoffFile = join(HANDOFF_DIR, `${safeSessionId}.json`);
    await writeFile(handoffFile, JSON.stringify(memo, null, 2), "utf-8");

    // 古いファイルをクリーンアップ
    await cleanupOldHandoffs();

    return true;
  } catch {
    return false;
  }
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};

  try {
    const input = await parseHookInput();

    // Stop hookが既にアクティブな場合は即座にapprove
    if (input.stop_hook_active) {
      console.log(JSON.stringify(result));
      return;
    }

    // Issue #2545: HookContextパターンでsession_idを取得
    const ctx = createHookContext(input);
    const sessionId = ctx.sessionId;

    // 引き継ぎメモ生成・保存
    const memo = await generateHandoffMemo(sessionId);
    const success = await saveHandoffMemo(memo);

    await logHookExecution(
      HOOK_NAME,
      "approve",
      success ? "Handoff memo saved" : "Handoff memo save failed",
      {
        work_status: memo.work_status,
        next_action: memo.next_action,
      },
      { sessionId: sessionId ?? undefined },
    );
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", `Error: ${formatError(error)}`);
  }

  // 保存の成否に関わらずapprove
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    approveAndExit(HOOK_NAME);
  });
}
