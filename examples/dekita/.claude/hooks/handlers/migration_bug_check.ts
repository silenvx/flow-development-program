#!/usr/bin/env bun
/**
 * 移行PRでの移行先バグ検出フック
 *
 * Why:
 *   Issue #3549, PR #3542の教訓。移行PR（Python→TypeScript等）で移行先コードに
 *   バグがある場合、「既存コードの問題だから別Issue」としてマージすると、
 *   バグ込みで本番に入る。移行先のバグは同じPRで修正必須。
 *
 * What:
 *   - gh pr merge コマンド実行前に発火
 *   - PRの差分からsettings.jsonの参照先変更を検出（.py → .ts への切り替え）
 *   - pending-review マーカーで移行先ファイルへの指摘を確認
 *   - P1以上の指摘があればブロック
 *
 * State:
 *   - reads: .claude/logs/markers/pending-review-{branch}.json
 *   - reads: .claude/settings.json (via git diff)
 *
 * Remarks:
 *   - PreToolUse:Bash で gh pr merge を検出
 *   - 移行パターン: .py → .ts/.mjs/.js への参照先変更
 *   - マージをブロック（警告ではなくブロック）
 *
 * Changelog:
 *   - silenvx/dekita#3550: 初期実装
 *   - silenvx/dekita#3582: mainブランチからのgh pr merge対応（PR番号からhead ref取得）
 */

import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { BLOCKING_SEVERITIES, CONTINUATION_HINT } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { getCurrentBranch, getHeadCommitFull, getOriginDefaultBranch } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { getMarkersDir } from "../lib/markers";
import { approveAndExit, blockAndExit } from "../lib/results";
import { getBashCommand, parseHookInput } from "../lib/session";
import { isSkipEnvEnabled, sanitizeBranchName, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "migration-bug-check";
const SKIP_ENV = "SKIP_MIGRATION_BUG_CHECK";

/**
 * PR info returned by getPrInfo
 */
interface PrInfo {
  ref: string; // branch name (headRefName)
  oid: string; // commit SHA (headRefOid)
}

/**
 * Extract PR number from gh pr merge command
 * Returns null if no explicit PR number (e.g., "gh pr merge --squash")
 * Note: Currently only supports numeric PR IDs. Branch names (e.g., "gh pr merge feature-branch")
 * are not detected and will fall back to checking HEAD.
 */
export function extractPrNumberFromCommand(command: string): string | null {
  // Match: gh pr merge 123, gh pr merge --squash 123, gh pr merge 123 --squash
  // The number can appear anywhere after "merge"
  const match = command.match(/\bgh\s+pr\s+merge\b.*?\s(\d+)(?:\s|$)/i);
  return match ? match[1] : null;
}

/**
 * Get PR's head ref (branch name) and commit SHA from PR number
 */
async function getPrInfo(prNumber: string): Promise<PrInfo | null> {
  try {
    const json = execSync(`gh pr view ${prNumber} --json headRefName,headRefOid`, {
      encoding: "utf-8",
      timeout: 10000,
    });
    const data = JSON.parse(json) as { headRefName: string; headRefOid: string };
    return { ref: data.headRefName, oid: data.headRefOid };
  } catch (error) {
    console.error(`[${HOOK_NAME}] Error fetching PR info for #${prNumber}:`, error);
    return null;
  }
}

/**
 * Pending review marker data structure (from parallel_review.ts)
 */
interface ReviewFinding {
  severity: string;
  source: "codex" | "gemini";
  snippet: string;
  file?: string;
}

interface PendingReviewMarker {
  branch: string;
  commit: string;
  timestamp: string;
  findings: ReviewFinding[];
}

/**
 * Migration pattern detected in settings.json
 */
interface MigrationPattern {
  fromFile: string; // e.g., "hook.py"
  toFile: string; // e.g., "hook.ts"
}

/**
 * Check if command is a merge command
 */
function isMergeCommand(command: string): boolean {
  // Match: gh pr merge [options]
  return /\bgh\s+pr\s+merge\b/i.test(command);
}

/**
 * Detect migration patterns in settings.json changes
 * Returns list of migration patterns (from -> to file paths)
 * @param targetRef - Optional target ref for diff (commit SHA or branch name for gh pr merge <number> from main)
 */
async function detectMigrationPatterns(targetRef?: string): Promise<MigrationPattern[]> {
  const patterns: MigrationPattern[] = [];

  try {
    // Get default branch dynamically (supports repos with master or different remote names)
    const defaultBranch = await getOriginDefaultBranch(".");
    // Use targetRef if provided (for gh pr merge <number> from main)
    // Otherwise use HEAD (for gh pr merge from PR branch)
    const ref = targetRef ?? "HEAD";
    // Get diff of settings.json between default branch and target ref (committed changes only)
    const diff = execSync(`git diff ${defaultBranch}...${ref} -- .claude/settings.json`, {
      encoding: "utf-8",
      timeout: 10000,
    });

    if (!diff) {
      return patterns;
    }

    // Look for patterns like:
    // -    "command": "python3 ...hooks/xxx.py"
    // +    "command": "bun run ...hooks/xxx.ts"
    // Or:
    // -    "path/to/file.py"
    // +    "path/to/file.ts"

    const lines = diff.split("\n");
    const removedPaths: string[] = [];
    const addedPaths: string[] = [];

    for (const line of lines) {
      // Match removed Python file references
      // Handles: paths with escaped quotes, arguments after filename
      // Pattern: find .py followed by quote, space, or end-of-string
      const removedMatch = line.match(/^-.*?([\w./$\\"-]+\.py)(?:["'\s]|$)/);
      if (removedMatch) {
        // Clean up the path (remove leading quotes/escapes)
        const cleanPath = removedMatch[1].replace(/^["'\\]+/, "").replace(/["'\\]+$/, "");
        if (cleanPath.endsWith(".py")) {
          removedPaths.push(cleanPath);
        }
      }

      // Match added TypeScript/JS file references
      // Handles: paths with escaped quotes, arguments after filename
      const addedMatch = line.match(/^[+].*?([\w./$\\"-]+\.(ts|mjs|js))(?:["'\s]|$)/);
      if (addedMatch) {
        // Clean up the path (remove leading quotes/escapes)
        const cleanPath = addedMatch[1].replace(/^["'\\]+/, "").replace(/["'\\]+$/, "");
        if (/\.(ts|mjs|js)$/.test(cleanPath)) {
          addedPaths.push(cleanPath);
        }
      }
    }

    // Match removed Python files with added TS/JS files by base name
    for (const pyPath of removedPaths) {
      const baseName = pyPath.replace(/\.py$/, "");
      for (const tsPath of addedPaths) {
        const tsBaseName = tsPath.replace(/\.(ts|mjs|js)$/, "");
        // Check if base names match (possibly with different directory structure)
        const pyFileName = baseName.split("/").pop() ?? "";
        const tsFileName = tsBaseName.split("/").pop() ?? "";
        if (pyFileName && tsFileName && pyFileName === tsFileName) {
          patterns.push({
            fromFile: pyPath,
            toFile: tsPath,
          });
        }
      }
    }
  } catch (error) {
    // If we were explicitly checking a PR ref and it failed, re-throw
    // This prevents silent failure when PR ref is not fetched locally
    if (targetRef) {
      console.error(
        `[${HOOK_NAME}] Failed to diff PR ref '${targetRef}'. Ensure it is fetched locally.`,
      );
      throw error;
    }
    // Ignore errors for HEAD case (e.g., not in git repo, no settings.json changes)
  }

  return patterns;
}

/**
 * Check if pending review marker has blocking findings for migration target files
 * @param migrationPatterns - Migration patterns detected
 * @param prHeadRef - Optional PR branch name (for gh pr merge <number> from main)
 * @param prHeadOid - Optional PR commit SHA (for gh pr merge <number> from main)
 */
async function checkMigrationTargetFindings(
  migrationPatterns: MigrationPattern[],
  prHeadRef?: string,
  prHeadOid?: string,
): Promise<{
  hasBlockingFindings: boolean;
  findings: ReviewFinding[];
  targetFiles: string[];
}> {
  if (migrationPatterns.length === 0) {
    return { hasBlockingFindings: false, findings: [], targetFiles: [] };
  }

  // Use prHeadRef if provided (for gh pr merge <number> from main)
  // Otherwise use current branch
  const branch = prHeadRef ?? (await getCurrentBranch());
  if (!branch) {
    return { hasBlockingFindings: false, findings: [], targetFiles: [] };
  }

  const markersDir = getMarkersDir();
  const safeBranch = sanitizeBranchName(branch);
  const markerFile = `${markersDir}/pending-review-${safeBranch}.json`;

  if (!existsSync(markerFile)) {
    return { hasBlockingFindings: false, findings: [], targetFiles: [] };
  }

  try {
    const content = readFileSync(markerFile, "utf-8");
    const marker = JSON.parse(content) as PendingReviewMarker;

    // Check if marker is stale
    // Use prHeadOid if provided (for gh pr merge <number> from main)
    // Otherwise use local HEAD
    const currentCommit = prHeadOid ?? (await getHeadCommitFull());
    if (currentCommit && marker.commit !== currentCommit) {
      return { hasBlockingFindings: false, findings: [], targetFiles: [] };
    }

    // Get target file names (migration destinations)
    const targetFiles = migrationPatterns.map((p) => p.toFile);
    const targetFileNames = targetFiles.map((f) => f.split("/").pop() ?? f);

    // Filter findings that:
    // 1. Are blocking severity
    // 2. Relate to migration target files (by snippet content or file field)
    const blockingFindings = marker.findings.filter((f) => {
      if (!BLOCKING_SEVERITIES.has(f.severity)) {
        return false;
      }

      // Check if finding relates to migration target
      // Option 1: Finding has explicit file field - use full path matching with boundary check
      if (f.file) {
        const fPath = f.file.replace(/\\/g, "/");
        // Check if one path ends with the other, ensuring a path separator boundary
        // This avoids false positives like "auth.ts" matching "oauth.ts"
        const matchWithBoundary = (full: string, suffix: string): boolean => {
          if (!full.endsWith(suffix)) return false;
          const prefix = full.slice(0, -suffix.length);
          return prefix === "" || prefix.endsWith("/");
        };
        const isMatch = targetFiles.some((tf) => {
          const tfPath = tf.replace(/\\/g, "/");
          return matchWithBoundary(tfPath, fPath) || matchWithBoundary(fPath, tfPath);
        });
        if (isMatch) {
          return true;
        }
        // If file is specified but doesn't match target, it's for another file
        // Skip snippet check to avoid false positives (e.g., index.ts vs hooks/index.ts)
        return false;
      }

      // Option 2: Check if snippet mentions target file names (only if no file field)
      for (const targetName of targetFileNames) {
        if (f.snippet.includes(targetName)) {
          return true;
        }
      }

      return false;
    });

    return {
      hasBlockingFindings: blockingFindings.length > 0,
      findings: blockingFindings,
      targetFiles,
    };
  } catch (error) {
    console.error(`[${HOOK_NAME}] Failed to parse marker file:`, error);
    return { hasBlockingFindings: false, findings: [], targetFiles: [] };
  }
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const input = await parseHookInput();
    sessionId = input.session_id;

    // Check skip env
    if (isSkipEnvEnabled(process.env[SKIP_ENV])) {
      approveAndExit(HOOK_NAME);
    }

    // Only process Bash tool
    if (input.tool_name !== "Bash") {
      approveAndExit(HOOK_NAME);
    }

    // Check if command is a merge command (strip quoted strings to avoid false positives like echo "gh pr merge")
    const command = getBashCommand(input);
    if (!command || !isMergeCommand(stripQuotedStrings(command))) {
      approveAndExit(HOOK_NAME);
    }

    // Extract PR number if specified (e.g., gh pr merge 123)
    // Use stripQuotedStrings to avoid matching numbers inside quoted strings (e.g., --title "Fix issue 123")
    const prNumber = extractPrNumberFromCommand(stripQuotedStrings(command));
    let prHeadRef: string | undefined;
    let prHeadOid: string | undefined;

    if (prNumber) {
      // Get head ref and commit SHA from PR (handles "gh pr merge 123" from main)
      const prInfo = await getPrInfo(prNumber);
      if (prInfo) {
        // Sanitize branch name to prevent command injection
        // Git branch names can contain characters like ';' which could be dangerous in shell
        prHeadRef = sanitizeBranchName(prInfo.ref);
        prHeadOid = prInfo.oid;
      } else {
        // Failed to get PR info - cannot safely verify migration patterns
        // This prevents silent bypass when gh command fails (e.g., network error)
        console.error(`[${HOOK_NAME}] Failed to retrieve info for PR #${prNumber}`);
        blockAndExit(HOOK_NAME, `Failed to retrieve PR info for #${prNumber}`);
      }
    }

    // Detect migration patterns using OID (commit SHA) for diffing
    // OID ensures we check the exact commit and eliminates injection risks with branch names
    const migrationPatterns = await detectMigrationPatterns(prHeadOid ?? prHeadRef);
    if (migrationPatterns.length === 0) {
      // No migration detected, approve
      approveAndExit(HOOK_NAME);
    }

    // Check for blocking findings on migration targets
    const { hasBlockingFindings, findings, targetFiles } = await checkMigrationTargetFindings(
      migrationPatterns,
      prHeadRef,
      prHeadOid,
    );

    if (hasBlockingFindings) {
      const severityCounts = new Map<string, number>();
      for (const f of findings) {
        severityCounts.set(f.severity, (severityCounts.get(f.severity) ?? 0) + 1);
      }

      const severityList = Array.from(severityCounts.entries())
        .map(([sev, count]) => `${sev}: ${count}件`)
        .join(", ");

      const migrationList = migrationPatterns
        .map((p) => `  ${p.fromFile} → ${p.toFile}`)
        .join("\n");

      await logHookExecution(
        HOOK_NAME,
        "block",
        `Migration target has blocking findings: ${severityList}`,
        {
          patterns: migrationPatterns.length,
          findings: findings.length,
        },
        { sessionId },
      );

      console.error(`[${HOOK_NAME}] 移行PRで移行先にバグがあります`);
      console.error("");
      console.error("【検出された移行パターン】");
      console.error(migrationList);
      console.error("");
      console.error(`【移行先への指摘】${severityList}`);
      console.error("");
      console.error("移行PRがマージされた瞬間から移行先コードが有効になります。");
      console.error("移行先にバグがあれば、そのバグはマージ直後から本番に影響します。");
      console.error("");
      console.error("【対処方法】");
      console.error("1. このPRで移行先のバグを修正してからマージ");
      console.error("2. 「既存コードの問題」として別Issue化しない");
      console.error("");
      console.error("詳細: AGENTS.md「移行PRのバグ対応」セクションを参照");
      console.error(CONTINUATION_HINT);

      blockAndExit(HOOK_NAME, "Migration target has blocking findings");
    }

    // No blocking findings, approve
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Migration PR with ${migrationPatterns.length} patterns, no blocking findings`,
      {
        patterns: migrationPatterns.length,
        targetFiles,
      },
      { sessionId },
    );

    approveAndExit(HOOK_NAME);
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    approveAndExit(HOOK_NAME);
  }
}

if (import.meta.main) {
  main();
}
