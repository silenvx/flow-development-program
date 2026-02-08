#!/usr/bin/env bun
/**
 * Gemini CLIレビュー実行をPR作成・push前に強制する
 *
 * Why:
 *   コードレビューなしでPRを作成・pushすると、品質問題がCIやAIレビューで
 *   初めて発覚し、手戻りが発生する。事前のローカルレビューで品質を担保する。
 *
 * What:
 *   - gh pr create / git pushコマンドを検出
 *   - 現在のブランチ・コミットでgemini /code-reviewが実行済みか確認
 *   - 未実行またはコミット変更後は再レビューを要求
 *   - リベース後も差分が同一ならスキップ
 *
 * State:
 *   - reads: .claude/logs/markers/gemini-review-{branch}.done
 *
 * Remarks:
 *   - ブロック型フック（レビュー未実行時はブロック）
 *   - PreToolUse:Bashで発火（gh pr create/git pushコマンド）
 *   - main/masterブランチはスキップ
 *   - SKIP_GEMINI_REVIEW=1でバイパス可能
 *   - gemini_review_logger.tsと連携（マーカーファイル読み込み）
 *
 * Changelog:
 *   - silenvx/dekita#3021: hasSkipEnvをlib/strings.tsのcheckSkipEnvに統合
 *   - silenvx/dekita#2990: ブロックメッセージにCodexレビューとparallel_review.shを案内
 *   - silenvx/dekita#2972: prefix比較でshort/full両形式のコミットハッシュに対応
 *   - silenvx/dekita#2856: TypeScript版初期実装
 */

import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { MAX_REVIEW_CYCLES, PENDING_REVIEW_MARKER_PREFIX } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { getCurrentBranch, getHeadCommitFull, getOriginDefaultBranch } from "../lib/git";
import { getMarkersDir, parseCycleCountFromContent } from "../lib/markers";
import { approveAndExit, blockAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { checkSkipEnv, sanitizeBranchName, stripQuotedStrings } from "../lib/strings";

/** Pattern to detect 'gh pr create' commands */
export const GH_PR_CREATE_PATTERN = /gh\s+pr\s+create\b/;

/** Pattern to detect 'git push' commands */
export const GIT_PUSH_PATTERN = /git\s+push\b/;

/** Environment variable to skip check */
const SKIP_ENV = "SKIP_GEMINI_REVIEW";

/**
 * Check if command is a gh pr create command
 * Returns false for commands inside quoted strings
 */
export function isGhPrCreateCommand(command: string): boolean {
  if (!command.trim()) return false;
  // Strip quoted strings to avoid false positives (e.g., echo "gh pr create")
  const stripped = stripQuotedStrings(command);
  return GH_PR_CREATE_PATTERN.test(stripped) && !command.includes("--help");
}

/**
 * Check if command is a git push command
 * Returns false for commands inside quoted strings
 */
export function isGitPushCommand(command: string): boolean {
  if (!command.trim()) return false;
  // Strip quoted strings to avoid false positives (e.g., echo "git push")
  const stripped = stripQuotedStrings(command);
  return GIT_PUSH_PATTERN.test(stripped) && !command.includes("--help");
}

/**
 * Check if skip environment variable is set.
 * Delegates to lib/strings.ts's checkSkipEnv for consistent SKIP_* handling.
 */
export function hasSkipEnv(command: string): boolean {
  return checkSkipEnv("gemini-review-check", SKIP_ENV, { input_preview: command });
}

/**
 * Get diff hash for comparing with logged hash
 */
export async function getDiffHash(): Promise<string | null> {
  try {
    const originBranch = await getOriginDefaultBranch(process.cwd());
    const proc = Bun.spawn(["git", "diff", `${originBranch}...HEAD`], {
      stdout: "pipe",
      stderr: "pipe",
    });
    const output = await new Response(proc.stdout).text();
    await proc.exited;
    if (!output.trim()) {
      return null;
    }
    return createHash("sha256").update(output).digest("hex").slice(0, 16);
  } catch {
    return null;
  }
}

/**
 * Compare commit hashes using prefix matching.
 *
 * This handles the case where marker files may contain either:
 * - Short hash (7-8 chars) from Python's `git rev-parse --short HEAD`
 * - Full hash (40 chars) from TypeScript's `git rev-parse HEAD`
 *
 * By using prefix comparison, we can match regardless of which format was used.
 *
 * @param hash1 - First commit hash (may be short or full)
 * @param hash2 - Second commit hash (may be short or full)
 * @returns true if one hash is a prefix of the other (or they're equal)
 */
export function compareCommitHashes(
  hash1: string | null | undefined,
  hash2: string | null | undefined,
): boolean {
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
 * Check if review was done for this branch
 * @returns [isReviewed, reviewedCommit, diffMatched, cycleCount]
 */
export function checkReviewDone(
  branch: string,
  commit: string | null,
  currentDiffHash: string | null,
): [boolean, string | null, boolean, number] {
  const safeBranch = sanitizeBranchName(branch);
  const logFile = `${getMarkersDir()}/gemini-review-${safeBranch}.done`;

  if (!existsSync(logFile)) {
    return [false, null, false, 0];
  }

  const content = readFileSync(logFile, "utf-8").trim();
  const parts = content.split(":");

  if (parts.length >= 2) {
    const reviewedCommit = parts[1];
    const reviewedDiffHash = parts.length >= 3 && parts[2] ? parts[2] : null;
    // Issue #3984: Parse cycle count from marker (4th field)
    const cycleCount = parseCycleCountFromContent(content);

    // Check if reviewed commit matches current HEAD using prefix comparison
    // This handles both short (7-8 chars) and full (40 chars) hash formats
    if (compareCommitHashes(reviewedCommit, commit)) {
      return [true, reviewedCommit, false, cycleCount];
    }

    // If commit doesn't match, check if diff hash matches (rebase case)
    if (currentDiffHash && reviewedDiffHash && currentDiffHash === reviewedDiffHash) {
      return [true, reviewedCommit, true, cycleCount];
    }

    return [false, reviewedCommit, false, cycleCount];
  }

  return [false, null, false, 0];
}

/**
 * Generate block reason message
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
    return `Gemini CLIレビュー後に新しいコミットがあります。
- ブランチ: ${branch}
- レビュー済みコミット: ${reviewedCommit}
- 現在のHEAD: ${commit}

新しいコミットに対してレビューを再実行してください:

${recommendedCommands}`;
  }

  return `Gemini CLIレビューが実行されていません（ブランチ: ${branch}）。

${action}前に、以下のコマンドでローカルレビューを実行してください:

${recommendedCommands}

レビュー完了後、再度${action}してください。`;
}

/**
 * Check and block if review not done
 * Returns the block reason if review is required, null otherwise
 */
async function checkAndBlockIfNotReviewed(
  commandType: "pr_create" | "git_push",
): Promise<string | null> {
  const branch = await getCurrentBranch();

  // Skip check for main/master branches
  if (!branch || branch === "main" || branch === "master") {
    return null;
  }

  const commit = await getHeadCommitFull();
  const currentDiffHash = await getDiffHash();
  const [isReviewed, reviewedCommit, , cycleCount] = checkReviewDone(
    branch,
    commit,
    currentDiffHash,
  );

  if (!isReviewed) {
    // Issue #3984: Check cycle count for infinite loop prevention
    if (cycleCount >= MAX_REVIEW_CYCLES) {
      // Check if pending-review marker exists (MEDIUM+ findings)
      const safeBranch = sanitizeBranchName(branch);
      const markersDir = getMarkersDir();
      const pendingMarker = join(markersDir, `${PENDING_REVIEW_MARKER_PREFIX}${safeBranch}.json`);
      if (!existsSync(pendingMarker)) {
        // No MEDIUM+ findings after MAX_REVIEW_CYCLES - allow with warning
        console.error(
          `⚠️ レビューサイクル上限（${MAX_REVIEW_CYCLES}回）に到達。MEDIUM以上の指摘がないため許可します。`,
        );
        return null;
      }
      // MEDIUM+ findings still present - continue blocking
      console.error(
        `⚠️ レビューサイクル上限（${MAX_REVIEW_CYCLES}回）に到達しましたが、MEDIUM以上の指摘が残っています。手動で確認してください。`,
      );
    }

    const reason = getBlockReason(branch, commit, reviewedCommit, commandType);
    return `# Gemini CLIレビューが必要です\n\n${reason}`;
  }

  return null;
}

const HOOK_NAME = "gemini-review-check";

/**
 * メイン処理
 */
async function main(): Promise<void> {
  try {
    const input = await parseHookInput();
    const toolInput = input.tool_input as Record<string, unknown> | undefined;
    const command = (toolInput?.command as string) ?? "";

    // Check for skip environment variable
    if (hasSkipEnv(command)) {
      approveAndExit(HOOK_NAME);
    }

    // Check gh pr create command
    if (isGhPrCreateCommand(command)) {
      const blockReason = await checkAndBlockIfNotReviewed("pr_create");
      if (blockReason) {
        blockAndExit(HOOK_NAME, blockReason);
      }
    }

    // Check git push command
    if (isGitPushCommand(command)) {
      const blockReason = await checkAndBlockIfNotReviewed("git_push");
      if (blockReason) {
        blockAndExit(HOOK_NAME, blockReason);
      }
    }

    // All checks passed
    approveAndExit(HOOK_NAME);
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    approveAndExit(HOOK_NAME);
  }
}

// 実行（テスト時はスキップ）
if (import.meta.main) {
  main();
}
