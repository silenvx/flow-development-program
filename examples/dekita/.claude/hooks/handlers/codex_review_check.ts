#!/usr/bin/env bun
/**
 * Codex CLIレビュー実行をPR作成・push前に強制する。
 *
 * Why:
 *   コードレビューなしでPRを作成・pushすると、品質問題がCIやAIレビューで
 *   初めて発覚し、手戻りが発生する。事前のローカルレビューで品質を担保する。
 *
 * What:
 *   - gh pr create / git pushコマンドを検出
 *   - 現在のブランチ・コミットでcodex reviewが実行済みか確認
 *   - 未実行またはコミット変更後は再レビューを要求
 *   - リベース後も差分が同一ならスキップ
 *
 * State:
 *   - reads: .claude/logs/markers/codex-review-{branch}.done
 *
 * Remarks:
 *   - ブロック型フック（レビュー未実行時はブロック）
 *   - PreToolUse:Bashで発火（gh pr create/git pushコマンド）
 *   - main/masterブランチはスキップ
 *   - SKIP_CODEX_REVIEW=1でバイパス可能
 *   - codex-review-logger.tsと連携（マーカーファイル読み込み）
 *
 * Changelog:
 *   - silenvx/dekita#3310: Codexレート制限時のGeminiフォールバック
 *   - silenvx/dekita#3159: TypeScriptに移植
 *   - silenvx/dekita#2990: ブロックメッセージにGeminiレビューとparallel_review.shを案内
 *   - silenvx/dekita#2972: prefix比較でshort/full両形式のコミットハッシュに対応
 *   - silenvx/dekita#841: リベース後のdiffハッシュ比較でスキップ
 *   - silenvx/dekita#890: マージ済みPRのスキップ
 *   - silenvx/dekita#945: SKIP_CODEX_REVIEW環境変数対応
 *   - silenvx/dekita#956: truthy値の厳格化
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import {
  CODEX_RATE_LIMIT_MARKER_PREFIX,
  GEMINI_REVIEW_MARKER_PREFIX,
  MAX_REVIEW_CYCLES,
  PENDING_REVIEW_MARKER_PREFIX,
} from "../lib/constants";
import { formatError } from "../lib/format_error";
import {
  getCurrentBranch as defaultGetCurrentBranch,
  getDiffHash as defaultGetDiffHash,
  getHeadCommit as defaultGetHeadCommit,
} from "../lib/git";
import {
  getPrNumberForBranch as defaultGetPrNumberForBranch,
  isPrMerged as defaultIsPrMerged,
} from "../lib/github";
import { logHookExecution as defaultLogHookExecution } from "../lib/logging";
import { getMarkersDir, parseCycleCountFromContent } from "../lib/markers";
import { type HookResult, makeApproveResult, makeBlockResult, outputResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import {
  extractInlineSkipEnv,
  isSkipEnvEnabled,
  sanitizeBranchName,
  stripQuotedStrings,
} from "../lib/strings";

/**
 * Dependency interface for checkAndBlockIfNotReviewed function.
 * Allows dependency injection for testing without mock.module (Issue #4061).
 */
export interface CheckDeps {
  getCurrentBranch: typeof defaultGetCurrentBranch;
  getHeadCommit: typeof defaultGetHeadCommit;
  getDiffHash: typeof defaultGetDiffHash;
  getPrNumberForBranch: typeof defaultGetPrNumberForBranch;
  isPrMerged: typeof defaultIsPrMerged;
  logHookExecution: typeof defaultLogHookExecution;
}

/**
 * Default dependencies using actual implementations.
 */
export const defaultDeps: CheckDeps = {
  getCurrentBranch: defaultGetCurrentBranch,
  getHeadCommit: defaultGetHeadCommit,
  getDiffHash: defaultGetDiffHash,
  getPrNumberForBranch: defaultGetPrNumberForBranch,
  isPrMerged: defaultIsPrMerged,
  logHookExecution: defaultLogHookExecution,
};

const SKIP_CODEX_REVIEW_ENV = "SKIP_CODEX_REVIEW";

/**
 * Check if command is actually a gh pr create command.
 *
 * Returns false for:
 * - Commands inside quoted strings (e.g., echo 'gh pr create')
 * - Empty commands
 */
function isGhPrCreateCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }

  // Strip quoted strings to avoid false positives
  const strippedCommand = stripQuotedStrings(command);

  // Check if gh pr create exists in the stripped command
  return /gh\s+pr\s+create\b/.test(strippedCommand);
}

/**
 * Check if SKIP_CODEX_REVIEW environment variable is set with truthy value.
 *
 * Supports both:
 * - Exported: 環境変数が既に設定されている状態で `git push` を実行
 * - Inline: SKIP_CODEX_REVIEW=1 git push ... (including SKIP_CODEX_REVIEW="1")
 *
 * Only "1", "true", "True" are considered truthy (Issue #956).
 */
function hasSkipCodexReviewEnv(command: string): boolean {
  // Check exported environment variable with value validation
  if (isSkipEnvEnabled(process.env[SKIP_CODEX_REVIEW_ENV])) {
    return true;
  }
  // Check inline environment variable in command (handles quoted values)
  const inlineValue = extractInlineSkipEnv(command, SKIP_CODEX_REVIEW_ENV);
  return isSkipEnvEnabled(inlineValue);
}

/**
 * Check if command is a git push command.
 *
 * Returns false for:
 * - Commands inside quoted strings (e.g., echo 'git push')
 * - Empty commands
 * - git push --help or similar non-push operations
 */
function isGitPushCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }

  // Strip quoted strings to avoid false positives
  const strippedCommand = stripQuotedStrings(command);

  // Check if git push exists in the stripped command
  // Match "git push" but not "git push --help"
  if (!/git\s+push\b/.test(strippedCommand)) {
    return false;
  }

  // Exclude help commands (Issue #3211: also detect -h)
  if (/--help\b|-h\b/.test(strippedCommand)) {
    return false;
  }

  return true;
}

/**
 * Compare commit hashes using prefix matching.
 *
 * This handles the case where marker files may contain either:
 * - Short hash (7-8 chars) from Python's `git rev-parse --short HEAD`
 * - Full hash (40 chars) from TypeScript's `git rev-parse HEAD`
 *
 * By using prefix comparison, we can match regardless of which format was used.
 */
function compareCommitHashes(hash1: string | null, hash2: string | null): boolean {
  if (!hash1 || !hash2 || hash1.length < 7 || hash2.length < 7) {
    return false;
  }
  // Use prefix comparison: shorter hash should be prefix of longer hash (case-insensitive)
  const h1 = hash1.toLowerCase();
  const h2 = hash2.toLowerCase();
  if (h1.length > h2.length) {
    return h1.startsWith(h2);
  }
  return h2.startsWith(h1);
}

/**
 * Check if codex review was executed for this branch at current commit or same diff.
 *
 * @returns Tuple of [is_reviewed, reviewed_commit, diff_matched, cycleCount].
 */
function checkReviewDone(
  branch: string,
  commit: string | null,
  currentDiffHash: string | null,
): [boolean, string | null, boolean, number] {
  const safeBranch = sanitizeBranchName(branch);
  const logFile = join(getMarkersDir(), `codex-review-${safeBranch}.done`);

  if (!existsSync(logFile)) {
    return [false, null, false, 0];
  }

  const content = readFileSync(logFile, "utf-8").trim();

  // Parse branch:commit:diff_hash:cycleCount format
  const parts = content.split(":");
  if (parts.length >= 2) {
    const reviewedCommit = parts[1];
    const reviewedDiffHash = parts.length >= 3 && parts[2] ? parts[2] : null;
    // Issue #3984: Parse cycle count from marker (4th field)
    const cycleCount = parseCycleCountFromContent(content);

    // Check if reviewed commit matches current HEAD using prefix comparison
    if (compareCommitHashes(commit, reviewedCommit)) {
      return [true, reviewedCommit, false, cycleCount];
    }

    // If commit doesn't match, check if diff hash matches (Issue #841)
    if (currentDiffHash && reviewedDiffHash && currentDiffHash === reviewedDiffHash) {
      return [true, reviewedCommit, true, cycleCount];
    }

    return [false, reviewedCommit, false, cycleCount];
  }

  // Invalid format (only branch name) - treat as not reviewed
  return [false, null, false, 0];
}

/**
 * Check if Codex rate limit marker exists for this branch (Issue #3310).
 *
 * @returns True if rate limit marker exists.
 */
function isCodexRateLimited(branch: string): boolean {
  const safeBranch = sanitizeBranchName(branch);
  const markerFile = join(getMarkersDir(), `${CODEX_RATE_LIMIT_MARKER_PREFIX}${safeBranch}.marker`);
  return existsSync(markerFile);
}

/**
 * Check if Gemini review is completed for this branch and commit (Issue #3310).
 *
 * @returns True if Gemini review is done for current commit or same diff.
 */
function checkGeminiReviewDone(
  branch: string,
  commit: string | null,
  currentDiffHash: string | null,
): boolean {
  // Use same sanitization as gemini_review_logger.ts
  const safeBranch = sanitizeBranchName(branch);
  const logFile = join(getMarkersDir(), `${GEMINI_REVIEW_MARKER_PREFIX}${safeBranch}.done`);

  if (!existsSync(logFile)) {
    return false;
  }

  const content = readFileSync(logFile, "utf-8").trim();

  // Parse branch:commit:diff_hash (3 parts) or branch:commit (2 parts) format
  const parts = content.split(":");
  if (parts.length >= 2) {
    const reviewedCommit = parts[1];
    const reviewedDiffHash = parts.length >= 3 ? parts[2] : null;

    // Check if reviewed commit matches current HEAD using prefix comparison
    if (compareCommitHashes(commit, reviewedCommit)) {
      return true;
    }

    // If commit doesn't match, check if diff hash matches
    if (currentDiffHash && reviewedDiffHash && currentDiffHash === reviewedDiffHash) {
      return true;
    }
  }

  return false;
}

/**
 * Generate block reason message based on review state.
 */
function getBlockReason(
  branch: string,
  commit: string | null,
  reviewedCommit: string | null,
  commandType: "pr_create" | "git_push",
): string {
  const action = commandType === "pr_create" ? "PRを作成する" : "プッシュする";

  // 共通の推奨コマンド案内
  const recommendedCommands = `**推奨: 並列レビュー（Codex + Gemini を同時実行）**

\`\`\`bash
bun run .claude/scripts/parallel_review.ts
\`\`\`

**個別実行:**

\`\`\`bash
# Codex
codex review --base main

# Gemini（非対話モード）
gemini "/code-review" --yolo -e code-review
\`\`\``;

  if (reviewedCommit && commit) {
    // Review was done but for a different commit
    return `Codex CLIレビュー後に新しいコミットがあります。
- ブランチ: ${branch}
- レビュー済みコミット: ${reviewedCommit}
- 現在のHEAD: ${commit}

新しいコミットに対してレビューを再実行してください:

${recommendedCommands}`;
  }
  // No review record found
  return `Codex CLIレビューが実行されていません（ブランチ: ${branch}）。

${action}前に、以下のコマンドでローカルレビューを実行してください:

${recommendedCommands}

レビュー完了後、再度${action}してください。`;
}

/**
 * Check if review is done for current branch and return block result if not.
 *
 * @internal Exported for testing. Do not use directly.
 * @param commandType - The type of command being executed
 * @param sessionId - Optional session ID for logging
 * @param deps - Optional dependencies for testing (Issue #4061)
 */
export async function checkAndBlockIfNotReviewed(
  commandType: "pr_create" | "git_push",
  sessionId?: string,
  deps: CheckDeps = defaultDeps,
): Promise<HookResult | null> {
  const branch = await deps.getCurrentBranch();

  // Skip check for main/master branches
  if (branch === "main" || branch === "master") {
    return null;
  }

  // Issue #890: Skip check if the branch's PR is already merged
  // This prevents false positives when another hook (e.g., locked-worktree-guard)
  // has already completed the merge before this hook runs.
  if (branch) {
    const prNumber = await deps.getPrNumberForBranch(branch);
    if (prNumber && (await deps.isPrMerged(prNumber))) {
      await deps.logHookExecution(
        "codex-review-check",
        "approve",
        `PR #${prNumber} for branch '${branch}' is already merged, skipping check`,
        undefined,
        { sessionId },
      );
      return makeApproveResult("codex-review-check", `PR #${prNumber} is already merged.`);
    }
  }

  // Block if branch is None (git error)
  if (branch === null) {
    await deps.logHookExecution(
      "codex-review-check",
      "block",
      "Failed to get current branch",
      { type: commandType },
      { sessionId },
    );
    return makeBlockResult(
      "codex-review-check",
      `ブランチ名を取得できませんでした。
gitリポジトリ内で実行しているか確認してください。

【対処法】
1. カレントディレクトリがgitリポジトリ内か確認: git status
2. .gitディレクトリが存在するか確認: ls -la .git
3. リポジトリルートに移動してから再実行`,
    );
  }

  const commit = await deps.getHeadCommit();
  const currentDiffHash = await deps.getDiffHash();
  const [isReviewed, reviewedCommit, diffMatched, cycleCount] = checkReviewDone(
    branch,
    commit,
    currentDiffHash,
  );

  if (!isReviewed) {
    // Issue #3984: Check cycle count for infinite loop prevention
    if (cycleCount >= MAX_REVIEW_CYCLES) {
      const safeBranch = sanitizeBranchName(branch);
      const pendingMarker = join(
        getMarkersDir(),
        `${PENDING_REVIEW_MARKER_PREFIX}${safeBranch}.json`,
      );
      if (!existsSync(pendingMarker)) {
        // No MEDIUM+ findings after MAX_REVIEW_CYCLES - allow with warning
        await deps.logHookExecution(
          "codex-review-check",
          "approve",
          `Review cycle limit reached (${cycleCount} >= ${MAX_REVIEW_CYCLES}), no MEDIUM+ findings (branch=${branch})`,
          undefined,
          { sessionId },
        );
        console.error(
          `⚠️ レビューサイクル上限（${MAX_REVIEW_CYCLES}回）に到達。MEDIUM以上の指摘がないため許可します。`,
        );
        return makeApproveResult("codex-review-check");
      }
      // MEDIUM+ findings still present - continue blocking
      console.error(
        `⚠️ レビューサイクル上限（${MAX_REVIEW_CYCLES}回）に到達しましたが、MEDIUM以上の指摘が残っています。手動で確認してください。`,
      );
    }

    // Issue #3310: Check for Codex rate limit and Gemini fallback
    if (isCodexRateLimited(branch)) {
      const geminiDone = checkGeminiReviewDone(branch, commit, currentDiffHash);
      if (geminiDone) {
        // Codex is rate limited but Gemini review is complete - allow with warning
        await deps.logHookExecution(
          "codex-review-check",
          "approve",
          `Codex rate limited, but Gemini review complete (branch=${branch})`,
          undefined,
          { sessionId },
        );
        return makeApproveResult(
          "codex-review-check",
          "⚠️ Codexレート制限中のため、Geminiレビュー結果で許可します。\n" +
            "Codexのレート制限がリセットされたら、`codex review --base main` を実行してください。",
        );
      }
      // Rate limited but Gemini not done - still block
      await deps.logHookExecution(
        "codex-review-check",
        "block",
        `Codex rate limited and Gemini review not complete (branch=${branch})`,
        { type: commandType },
        { sessionId },
      );
    } else {
      // No rate limit marker - log block for standard review not done case
      await deps.logHookExecution(
        "codex-review-check",
        "block",
        `Review not done for branch=${branch}, commit=${commit}`,
        { type: commandType },
        { sessionId },
      );
    }

    const reason = getBlockReason(branch, commit, reviewedCommit, commandType);
    return makeBlockResult("codex-review-check", reason);
  }

  // Log if review was approved due to diff hash match (Issue #841)
  if (diffMatched) {
    await deps.logHookExecution(
      "codex-review-check",
      "approve",
      `Diff hash match: リベース後も差分が同一のためスキップ (branch=${branch}, reviewed_commit=${reviewedCommit})`,
      undefined,
      { sessionId },
    );
    return makeApproveResult("codex-review-check", "Diff hash match: skipping review.");
  }

  // Review done for current commit - no logging needed (implicit approve)
  return null;
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = data.tool_input ?? {};
    const command = (toolInput as { command?: string }).command ?? "";

    // Check for SKIP_CODEX_REVIEW environment variable (Issue #945)
    if (hasSkipCodexReviewEnv(command)) {
      await defaultLogHookExecution(
        "codex-review-check",
        "approve",
        "SKIP_CODEX_REVIEW でスキップ",
        undefined,
        { sessionId },
      );
      const result = makeApproveResult("codex-review-check", "SKIP_CODEX_REVIEW でスキップ");
      outputResult(result);
      process.exit(0);
    }

    // Detect gh pr create command (excluding quoted strings)
    if (isGhPrCreateCommand(command)) {
      const checkResult = await checkAndBlockIfNotReviewed("pr_create", sessionId);
      if (checkResult) {
        outputResult(checkResult);
        process.exit(0);
      }
    }

    // Detect git push command (excluding quoted strings)
    if (isGitPushCommand(command)) {
      const checkResult = await checkAndBlockIfNotReviewed("git_push", sessionId);
      if (checkResult) {
        outputResult(checkResult);
        process.exit(0);
      }
    }

    // All checks passed
    const result = makeApproveResult("codex-review-check");
    await defaultLogHookExecution(
      "codex-review-check",
      result.decision ?? "approve",
      undefined,
      undefined,
      { sessionId },
    );
    outputResult(result);
  } catch (e) {
    console.error(`[codex-review-check] Hook error: ${formatError(e)}`);
    const result = makeApproveResult("codex-review-check", `Hook error: ${formatError(e)}`);
    await defaultLogHookExecution(
      "codex-review-check",
      result.decision ?? "approve",
      undefined,
      undefined,
      { sessionId },
    );
    outputResult(result);
  }
}

if (import.meta.main) {
  main();
}
