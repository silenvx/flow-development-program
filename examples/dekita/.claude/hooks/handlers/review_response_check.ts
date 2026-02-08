#!/usr/bin/env bun
/**
 * AIレビューのMEDIUM以上の指摘に対する対応を強制する。
 *
 * Why:
 *   AIレビュー（Codex/Gemini）でMEDIUM以上の指摘があっても、修正せずに
 *   プッシュできてしまう問題がある。対応を強制して品質を担保する。
 *
 * What:
 *   - git pushコマンドを検出
 *   - 未対応指摘マーカーファイル（pending-review-{branch}.json）を確認
 *   - マーカーが存在する場合、対応状況を確認
 *   - 対応がない場合はプッシュをブロック
 *
 * State:
 *   - reads: .claude/logs/markers/pending-review-{branch}.json
 *
 * Remarks:
 *   - ブロック型フック（未対応指摘がある場合はブロック）
 *   - PreToolUse:Bashで発火（git pushコマンド）
 *   - 対応方法: コード修正コミット or Issue参照コミット
 *   - SKIP_REVIEW_RESPONSE_CHECK=1でバイパス可能
 *
 * Changelog:
 *   - silenvx/dekita#3106: 初期実装
 */

import { existsSync, readFileSync } from "node:fs";

import { CONTINUATION_HINT, PENDING_REVIEW_MARKER_PREFIX } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { getCurrentBranch, getHeadCommitFull } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { getMarkersDir } from "../lib/markers";
import { createHookContext, parseHookInput } from "../lib/session";

// =============================================================================
// Types
// =============================================================================

export interface ReviewFinding {
  severity: string;
  source: "codex" | "gemini";
  snippet: string;
}

export interface PendingReviewMarker {
  branch: string;
  commit: string;
  timestamp: string;
  findings: ReviewFinding[];
}

// =============================================================================
// Constants
// =============================================================================

const SKIP_ENV_VAR = "SKIP_REVIEW_RESPONSE_CHECK";

// =============================================================================
// Utility Functions
// =============================================================================

/**
 * Check if command is a git push command.
 */
export function isGitPushCommand(command: string): boolean {
  if (!command.trim()) return false;

  // Strip quoted strings to avoid false positives
  const stripped = command.replace(/'[^']*'|"[^"]*"/g, "");

  // Match "git push" allowing for global flags between git and push
  // e.g. "git --no-pager push", "git -c key=val push"
  if (!/git\s+(?:\S+\s+)*push(?:\s|$)/.test(stripped)) return false;
  if (/--help/.test(stripped)) return false;

  return true;
}

/**
 * Check if SKIP_REVIEW_RESPONSE_CHECK is set.
 */
export function isSkipEnabled(command: string): boolean {
  // Check environment variable
  const envValue = process.env[SKIP_ENV_VAR];
  if (envValue === "1" || envValue === "true" || envValue === "True") {
    return true;
  }

  // Check inline environment variable in command
  const inlinePattern = new RegExp(`${SKIP_ENV_VAR}=["']?(1|true|True)["']?`);
  return inlinePattern.test(command);
}

/**
 * Sanitize branch name for use in filenames.
 */
function sanitizeBranchName(branch: string): string {
  return branch.replace(/[^a-zA-Z0-9._-]/g, "-");
}

/**
 * Read pending review marker if it exists.
 */
function readPendingMarker(branch: string): PendingReviewMarker | null {
  const markersDir = getMarkersDir();
  const safeBranch = sanitizeBranchName(branch);
  const markerPath = `${markersDir}/${PENDING_REVIEW_MARKER_PREFIX}${safeBranch}.json`;

  if (!existsSync(markerPath)) {
    return null;
  }

  try {
    const content = readFileSync(markerPath, "utf-8");
    return JSON.parse(content) as PendingReviewMarker;
  } catch {
    return null;
  }
}

/**
 * Check if the pending marker is still valid (not superseded by new commits).
 */
export function isMarkerStillValid(marker: PendingReviewMarker, currentCommit: string): boolean {
  // If current commit is different from marker commit, user has made new commits
  // This could be a fix commit, so we need to verify
  if (marker.commit !== currentCommit) {
    // New commits made - marker may be stale
    // For now, we trust that new commits address the findings
    // A more sophisticated check could verify that the issues were actually fixed
    return false;
  }

  return true;
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const input = await parseHookInput();
  const ctx = createHookContext(input);
  const sessionId = ctx.sessionId;
  const toolInput = input.tool_input as { command?: string } | undefined;
  const command = toolInput?.command ?? "";

  // Only check git push commands
  if (!isGitPushCommand(command)) {
    return;
  }

  // Check for skip environment variable
  if (isSkipEnabled(command)) {
    await logHookExecution(
      "review-response-check",
      "approve",
      `${SKIP_ENV_VAR} でスキップ`,
      undefined,
      { sessionId },
    );
    return;
  }

  // Get current branch
  const branch = await getCurrentBranch();
  if (!branch || branch === "main" || branch === "master") {
    return; // Skip for main/master branches
  }

  // Read pending review marker
  const marker = readPendingMarker(branch);
  if (!marker) {
    // No pending marker - allow push
    return;
  }

  // Get current HEAD commit (full hash for reliable comparison)
  const currentCommit = await getHeadCommitFull();
  if (!currentCommit) {
    return; // Can't determine commit - allow push
  }

  // Check if marker is still valid
  if (!isMarkerStillValid(marker, currentCommit)) {
    // New commits made after review - assume issues are addressed
    await logHookExecution(
      "review-response-check",
      "approve",
      `新しいコミットがあるため、レビュー指摘は対応済みと判断 (marker: ${marker.commit}, current: ${currentCommit})`,
      undefined,
      { sessionId },
    );
    return;
  }

  // Marker is still valid - block the push
  await logHookExecution(
    "review-response-check",
    "block",
    `MEDIUM以上の指摘が未対応 (${marker.findings.length}件)`,
    { findings: marker.findings },
    { sessionId },
  );

  const findingsList = marker.findings
    .map((f) => `  - [${f.severity.toUpperCase()}] ${f.source}: ${f.snippet}...`)
    .join("\n");

  console.log(`🚫 [review-response-check] MEDIUM以上のAIレビュー指摘が未対応です。

ブランチ: ${branch}
レビュー実行時のコミット: ${marker.commit}
検出された指摘 (${marker.findings.length}件):
${findingsList}

【対応方法】
1. **コード修正**: 指摘を修正してコミット
2. **Issue化**: \`gh issue create\` でIssueを作成し、コミットメッセージに #xxx を含める
3. **スキップ**: 正当な理由がある場合のみ \`${SKIP_ENV_VAR}=1 git push ...\`

【推奨アクション】
\`\`\`bash
# コード修正後
git add .
git commit -m "fix: レビュー指摘に対応"
git push
\`\`\`
${CONTINUATION_HINT}`);

  process.exit(2); // Block
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[review-response-check] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({}));
  });
}
