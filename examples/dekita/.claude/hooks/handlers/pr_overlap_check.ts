#!/usr/bin/env bun
/**
 * git push/gh pr create時に他PRとのファイル重複を警告。
 *
 * Why:
 *   複数PRが同じファイルを変更するとマージ時にコンフリクトが発生する。
 *   push/PR作成時点で重複を検知し、早期に調整できるようにする。
 *
 * What:
 *   - git push / gh pr create コマンドを検出
 *   - 現在のブランチで変更されたファイルを取得
 *   - オープン中の他PRの変更ファイルと比較
 *   - 重複があればsystemMessageで警告
 *
 * Remarks:
 *   - 非ブロック型（警告のみ）
 *   - gh CLI 2.35.0+が必要（files取得のため）
 *   - 最大50件のPRをチェック
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#3160: TypeScript移行
 */

import { TIMEOUT_EXTENDED, TIMEOUT_HEAVY } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { getCurrentBranch, getOriginDefaultBranch } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { approveAndExit } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";
import { stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "pr-overlap-check";

// =============================================================================
// Command Detection
// =============================================================================

/**
 * Check if command is git push or gh pr create.
 */
function isPushOrPrCreate(command: string): boolean {
  if (!command.trim()) {
    return false;
  }

  const stripped = stripQuotedStrings(command);

  // Check for git push
  if (/\bgit\s+push\b/.test(stripped)) {
    return true;
  }

  // Check for gh pr create
  if (/\bgh\s+pr\s+create\b/.test(stripped)) {
    return true;
  }

  return false;
}

// =============================================================================
// File Operations
// =============================================================================

// Cache for current branch files
let cachedCurrentBranchFiles: Set<string> | null = null;

/**
 * Get files changed in current branch compared to origin default branch.
 */
async function getCurrentBranchFiles(): Promise<Set<string>> {
  if (cachedCurrentBranchFiles !== null) {
    return cachedCurrentBranchFiles;
  }

  try {
    const originBranch = await getOriginDefaultBranch(process.cwd());
    const result = await asyncSpawn("git", ["diff", "--name-only", `${originBranch}...HEAD`], {
      timeout: TIMEOUT_HEAVY * 1000,
    });

    if (!result.success) {
      cachedCurrentBranchFiles = new Set();
      return cachedCurrentBranchFiles;
    }

    const files = result.stdout.trim()
      ? new Set(result.stdout.trim().split("\n"))
      : new Set<string>();

    cachedCurrentBranchFiles = files;
    return files;
  } catch {
    cachedCurrentBranchFiles = new Set();
    return cachedCurrentBranchFiles;
  }
}

/**
 * Get files changed in each open PR.
 */
async function getOpenPrFiles(): Promise<Map<string, string[]>> {
  try {
    const result = await asyncSpawn(
      "gh",
      ["pr", "list", "--state", "open", "--json", "number,headRefName,files", "--limit", "50"],
      { timeout: TIMEOUT_EXTENDED * 1000 },
    );

    if (!result.success) {
      return new Map();
    }

    const prs = JSON.parse(result.stdout);
    const currentBranch = await getCurrentBranch();

    const prFiles = new Map<string, string[]>();
    for (const pr of prs) {
      // Skip current branch's PR
      if (pr.headRefName === currentBranch) {
        continue;
      }

      const prNumber = `#${pr.number}`;
      const files = (pr.files ?? []).map((f: { path?: string }) => f.path ?? "");
      if (files.length > 0) {
        prFiles.set(prNumber, files);
      }
    }

    return prFiles;
  } catch {
    return new Map();
  }
}

// =============================================================================
// Overlap Detection
// =============================================================================

/**
 * Find files that overlap between current branch and other PRs.
 */
function findOverlappingFiles(
  currentFiles: Set<string>,
  prFiles: Map<string, string[]>,
): Map<string, string[]> {
  const overlaps = new Map<string, string[]>();

  for (const [prNumber, files] of prFiles) {
    const overlapping = files.filter((f) => currentFiles.has(f));
    if (overlapping.length > 0) {
      overlaps.set(prNumber, overlapping.sort());
    }
  }

  return overlaps;
}

/**
 * Format the overlap warning message.
 */
function formatWarning(overlaps: Map<string, string[]>): string {
  const lines = ["⚠️ File overlap detected with other open PRs:\n"];

  const sortedEntries = [...overlaps.entries()].sort(([a], [b]) => a.localeCompare(b));

  for (const [prNumber, files] of sortedEntries) {
    lines.push(`  ${prNumber}:`);
    for (const f of files.slice(0, 5)) {
      lines.push(`    - ${f}`);
    }
    if (files.length > 5) {
      lines.push(`    ... and ${files.length - 5} more files`);
    }
    lines.push("");
  }

  lines.push("Consider coordinating with these PRs to avoid merge conflicts.");
  lines.push("Tip: Merge or rebase frequently to minimize conflict scope.");

  return lines.join("\n");
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};
  let sessionId: string | undefined;

  try {
    const input = await parseHookInput();
    const ctx = createHookContext(input);
    sessionId = ctx.sessionId;
    const toolInput = input.tool_input as Record<string, unknown> | undefined;
    const command = (toolInput?.command as string) ?? "";

    // Only check push/PR create commands
    if (!isPushOrPrCreate(command)) {
      await logHookExecution(HOOK_NAME, "approve", "Not push/PR create", undefined, { sessionId });
      approveAndExit(HOOK_NAME);
    }

    // Get current branch's changed files
    const currentFiles = await getCurrentBranchFiles();
    if (currentFiles.size === 0) {
      await logHookExecution(HOOK_NAME, "approve", "No changed files", undefined, { sessionId });
      approveAndExit(HOOK_NAME);
    }

    // Get other PRs' changed files
    const prFiles = await getOpenPrFiles();
    if (prFiles.size === 0) {
      await logHookExecution(HOOK_NAME, "approve", "No other PRs", undefined, { sessionId });
      approveAndExit(HOOK_NAME);
    }

    // Find overlaps
    const overlaps = findOverlappingFiles(currentFiles, prFiles);

    if (overlaps.size > 0) {
      const warning = formatWarning(overlaps);
      result.systemMessage = warning;

      let overlapCount = 0;
      for (const files of overlaps.values()) {
        overlapCount += files.length;
      }

      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Warning: ${overlapCount} overlapping file(s) in ${overlaps.size} PR(s)`,
        { overlaps: Object.fromEntries(overlaps) },
        { sessionId },
      );
    } else {
      await logHookExecution(HOOK_NAME, "approve", "No overlaps", undefined, { sessionId });
    }
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(error)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
  process.exit(0);
}

if (import.meta.main) {
  main();
}
