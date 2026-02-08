#!/usr/bin/env bun
/**
 * セッション終了時に未レビューの変更を検出しレビュー実行を促す。
 *
 * Why:
 *   実装完了後にレビューせずセッションを終了すると、動作不備を見逃す。
 *   セッション終了時に未レビューを検出してレビュー実行を強制する。
 *
 * What:
 *   - セッション終了時（Stop）に発火
 *   - main以外のブランチで未プッシュコミットまたは未コミット変更を検出
 *   - codex reviewの実行履歴を確認
 *   - 未レビューの場合はセッション終了をブロック
 *
 * State:
 *   - reads: .claude/logs/markers/codex-review-*.done
 *   - writes: {TMPDIR}/claude-hooks/stop-auto-review-*.json（リトライカウント）
 *
 * Remarks:
 *   - ブロック型フック（未レビュー時はセッション終了をブロック）
 *   - MAX_REVIEW_RETRIES（2回）を超えると自動許可
 *   - SKIP_STOP_AUTO_REVIEW=1でスキップ可能
 *
 * Changelog:
 *   - silenvx/dekita#2166: フック追加
 *   - silenvx/dekita#3159: TypeScriptに移植
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { MARKERS_LOG_DIR } from "../lib/constants";
import { getCurrentBranch, getDiffHash, getHeadCommit } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { type HookResult, makeApproveResult, makeBlockResult, outputResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";
import { sanitizeBranchName } from "../lib/strings";

// =============================================================================
// Constants
// =============================================================================

/** Session state directory */
const SESSION_DIR = join(tmpdir(), "claude-hooks");

/** Maximum retry count to prevent infinite loop */
const MAX_REVIEW_RETRIES = 2;

/** Environment variable to skip this hook */
const SKIP_STOP_AUTO_REVIEW_ENV = "SKIP_STOP_AUTO_REVIEW";

// =============================================================================
// Types
// =============================================================================

interface State {
  retry_count: number;
}

// =============================================================================
// State Management
// =============================================================================

/**
 * Get the file path for storing stop-auto-review state.
 */
function getStateFile(sessionId: string): string {
  return join(SESSION_DIR, `stop-auto-review-${sessionId}.json`);
}

/**
 * Load stop-auto-review state.
 */
function loadState(sessionId: string): State {
  const stateFile = getStateFile(sessionId);
  if (existsSync(stateFile)) {
    try {
      const content = readFileSync(stateFile, "utf-8");
      return JSON.parse(content) as State;
    } catch {
      // Best effort - corrupted state is ignored
    }
  }
  return { retry_count: 0 };
}

/**
 * Save stop-auto-review state.
 */
function saveState(sessionId: string, state: State): void {
  try {
    mkdirSync(SESSION_DIR, { recursive: true });
    const stateFile = getStateFile(sessionId);
    writeFileSync(stateFile, JSON.stringify(state), "utf-8");
  } catch {
    // Best effort
  }
}

// =============================================================================
// Git Checks
// =============================================================================

/**
 * Check if there are unpushed commits on the current branch.
 */
async function hasUnpushedCommits(): Promise<boolean> {
  try {
    // First check if branch has an upstream
    const upstreamResult = await asyncSpawn("git", ["rev-parse", "--abbrev-ref", "@{upstream}"]);

    if (upstreamResult.exitCode !== 0) {
      // No upstream - check if there are any local commits
      // Compare with main branch to see if we have local work
      const logResult = await asyncSpawn("git", ["log", "main..HEAD", "--oneline"]);
      if (logResult.exitCode === 0 && logResult.stdout.trim()) {
        return true; // Has commits not in main
      }
      return false;
    }

    // Has upstream - check if we're ahead
    const result = await asyncSpawn("git", ["status", "-sb"]);
    if (result.exitCode === 0) {
      // Look for "[ahead N]" pattern
      return result.stdout.includes("ahead");
    }
  } catch {
    // Git command may fail in non-git directories or timeout
  }
  return false;
}

/**
 * Check if there are uncommitted changes.
 */
async function hasUncommittedChangesSimple(): Promise<boolean> {
  try {
    const result = await asyncSpawn("git", ["status", "--porcelain"]);
    if (result.exitCode === 0) {
      return result.stdout.trim().length > 0;
    }
  } catch {
    // Git command may fail in non-git directories or timeout
  }
  return false;
}

// =============================================================================
// Review Check
// =============================================================================

/**
 * Check if codex review was executed for this branch at current commit or same diff.
 */
async function checkReviewDone(
  branch: string,
  commit: string | null,
  currentDiffHash: string | null,
): Promise<boolean> {
  const projectDir = process.env.CLAUDE_PROJECT_DIR ?? process.cwd();
  const safeBranch = sanitizeBranchName(branch);
  const logFile = join(projectDir, MARKERS_LOG_DIR, `codex-review-${safeBranch}.done`);

  if (!existsSync(logFile)) {
    return false;
  }

  try {
    const content = readFileSync(logFile, "utf-8").trim();

    // Parse branch:commit:diff_hash (3 parts) or branch:commit (2 parts) format
    const parts = content.split(":");
    if (parts.length >= 2) {
      const reviewedCommit = parts[1];
      const reviewedDiffHash = parts.length >= 3 ? parts[2] : null;

      // Check if reviewed commit matches current HEAD
      if (commit && reviewedCommit === commit) {
        return true;
      }

      // If commit doesn't match, check if diff hash matches
      // This allows skipping re-review after rebase when actual diff is unchanged
      if (currentDiffHash && reviewedDiffHash && currentDiffHash === reviewedDiffHash) {
        return true;
      }
    }
  } catch {
    // Best effort
  }

  return false;
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  let result: HookResult = makeApproveResult("stop-auto-review");

  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);

    // Skip if Stop hook is already active (prevent recursion)
    if (data.stop_hook_active) {
      outputResult(result);
      return;
    }

    // Check skip environment variable
    if (process.env[SKIP_STOP_AUTO_REVIEW_ENV] === "1") {
      await logHookExecution(
        "stop-auto-review",
        "approve",
        "Skipped via SKIP_STOP_AUTO_REVIEW environment variable",
        undefined,
        { sessionId: ctx.sessionId },
      );
      outputResult(result);
      return;
    }

    // Get current branch
    const branch = await getCurrentBranch();

    // Skip for main/master branches
    if (branch === "main" || branch === "master" || !branch) {
      await logHookExecution(
        "stop-auto-review",
        "approve",
        `Skipped for branch: ${branch}`,
        undefined,
        { sessionId: ctx.sessionId },
      );
      outputResult(result);
      return;
    }

    // Check session state for retry count
    const sessionId = ctx.sessionId ?? "unknown";
    const state = loadState(sessionId);
    const retryCount = state.retry_count;

    // If max retries reached, allow session to end
    if (retryCount >= MAX_REVIEW_RETRIES) {
      await logHookExecution(
        "stop-auto-review",
        "approve",
        `Max retries (${MAX_REVIEW_RETRIES}) reached, allowing session end`,
        { retry_count: retryCount },
        { sessionId: ctx.sessionId },
      );
      outputResult(result);
      return;
    }

    // Check if there are changes to review
    const uncommitted = await hasUncommittedChangesSimple();
    const unpushed = await hasUnpushedCommits();

    if (!uncommitted && !unpushed) {
      await logHookExecution(
        "stop-auto-review",
        "approve",
        "No unpushed commits or uncommitted changes",
        undefined,
        { sessionId: ctx.sessionId },
      );
      outputResult(result);
      return;
    }

    // If there are uncommitted changes, always require review
    // (even if the last commit was reviewed, new changes need review)
    if (!uncommitted) {
      // Only unpushed commits - check if review is already done
      const commit = await getHeadCommit();
      const currentDiffHash = await getDiffHash();
      if (await checkReviewDone(branch, commit, currentDiffHash)) {
        await logHookExecution(
          "stop-auto-review",
          "approve",
          `Review already done for ${branch}@${commit?.slice(0, 7) ?? "unknown"}`,
          undefined,
          { sessionId: ctx.sessionId },
        );
        outputResult(result);
        return;
      }
    }

    const commit = await getHeadCommit();

    // Increment retry count and save
    state.retry_count = retryCount + 1;
    saveState(sessionId, state);

    // Block and suggest review
    const reason = `セッション終了前にコードレビューを実行してください。\n\n**ブランチ**: ${branch}\n**コミット**: ${commit?.slice(0, 7) ?? "unknown"}\n**試行回数**: ${retryCount + 1}/${MAX_REVIEW_RETRIES}\n\n以下のコマンドでローカルレビューを実行:\n\n\`\`\`bash\ncodex review --base main\n\`\`\`\n\nレビュー完了後、問題があれば修正してください。\n（${MAX_REVIEW_RETRIES}回試行後は自動的にセッション終了を許可します）`;

    await logHookExecution(
      "stop-auto-review",
      "block",
      `Review not done for ${branch}@${commit?.slice(0, 7) ?? "unknown"}`,
      { retry_count: retryCount + 1, branch },
      { sessionId: ctx.sessionId },
    );

    result = makeBlockResult("stop-auto-review", reason, ctx);
  } catch (e) {
    // Hook failures should not block session
    const errorMessage = e instanceof Error ? e.message : String(e);
    await logHookExecution("stop-auto-review", "approve", `Hook error: ${errorMessage}`);
  }

  outputResult(result);
}

if (import.meta.main) {
  main();
}
