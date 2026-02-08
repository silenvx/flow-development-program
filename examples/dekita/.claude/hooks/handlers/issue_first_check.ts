#!/usr/bin/env bun
/**
 * 問題発見時にIssue作成を先に促すフック
 *
 * Why:
 *   PR #3542で発生した問題。問題を発見→修正作業を開始→Issue作成を後回し、
 *   という流れだと、途中で中断した場合に問題が忘れられる。
 *
 * What:
 *   - Edit/Writeツール呼び出し時にpending-review-{branch}.jsonマーカーをチェック
 *   - MEDIUM以上の指摘がある場合、「まずIssueを作成してください」と警告
 *   - ブロックではなく警告（作業継続可能）
 *
 * State:
 *   - reads: .claude/logs/markers/pending-review-{branch}.json
 *
 * Remarks:
 *   - 警告型フック（ブロックしない）
 *   - PreToolUse:Edit, PreToolUse:Writeで発火
 *   - parallel_review.tsが作成するマーカーを参照
 *   - 新しいコミットでマーカーがstaleになると警告停止
 *
 * Changelog:
 *   - silenvx/dekita#3551: 初期実装
 */

import { existsSync, readFileSync } from "node:fs";
import { BLOCKING_SEVERITIES } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { getCurrentBranch, getHeadCommitFull } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { getMarkersDir } from "../lib/markers";
import { approveAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { isSkipEnvEnabled, sanitizeBranchName } from "../lib/strings";

const HOOK_NAME = "issue-first-check";
const SKIP_ENV = "SKIP_ISSUE_FIRST_CHECK";

/**
 * Pending review marker data structure (from parallel_review.ts)
 */
interface ReviewFinding {
  severity: string;
  source: "codex" | "gemini";
  snippet: string;
}

interface PendingReviewMarker {
  branch: string;
  commit: string;
  timestamp: string;
  findings: ReviewFinding[];
}

/**
 * Check if pending review marker exists and has blocking findings
 */
async function checkPendingReviewMarker(): Promise<{
  hasBlockingFindings: boolean;
  findings: ReviewFinding[];
}> {
  const branch = await getCurrentBranch();
  if (!branch) {
    return { hasBlockingFindings: false, findings: [] };
  }

  const markersDir = getMarkersDir();
  const safeBranch = sanitizeBranchName(branch);
  const markerFile = `${markersDir}/pending-review-${safeBranch}.json`;

  if (!existsSync(markerFile)) {
    return { hasBlockingFindings: false, findings: [] };
  }

  try {
    const content = readFileSync(markerFile, "utf-8");
    const marker = JSON.parse(content) as PendingReviewMarker;

    // Check if marker is stale (new commits made since review)
    // If commit differs, assume findings are addressed (consistent with review_response_check)
    const currentCommit = await getHeadCommitFull();
    if (currentCommit && marker.commit !== currentCommit) {
      // Marker is stale - new commits made after review
      return { hasBlockingFindings: false, findings: [] };
    }

    // Filter blocking findings
    const blockingFindings = marker.findings.filter((f) => BLOCKING_SEVERITIES.has(f.severity));

    return {
      hasBlockingFindings: blockingFindings.length > 0,
      findings: blockingFindings,
    };
  } catch (error) {
    // Log parse errors for debugging but don't block
    console.error(`[${HOOK_NAME}] Failed to parse marker file ${markerFile}:`, error);
    return { hasBlockingFindings: false, findings: [] };
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

    // Only process Edit and Write tools
    const toolName = input.tool_name;
    if (toolName !== "Edit" && toolName !== "Write") {
      approveAndExit(HOOK_NAME);
    }

    const { hasBlockingFindings, findings } = await checkPendingReviewMarker();

    if (hasBlockingFindings) {
      // Group findings by severity for display
      const severityCounts = new Map<string, number>();
      for (const f of findings) {
        severityCounts.set(f.severity, (severityCounts.get(f.severity) ?? 0) + 1);
      }

      const severityList = Array.from(severityCounts.entries())
        .map(([sev, count]) => `${sev}: ${count}件`)
        .join(", ");

      // Warning message (not blocking)
      console.error(`[${HOOK_NAME}] AIレビューで指摘があります (${severityList})`);
      console.error("");
      console.error("修正を始める前に、まずIssueを作成することを推奨します:");
      console.error("  gh issue create --title '...' --body '...' --label P1");
      console.error("");
      console.error("理由: Issue作成を後回しにすると、中断時に問題が忘れ去られます。");
      console.error("新しいコミットを作成すると、このメッセージは表示されなくなります。");
      console.error("");

      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Warning: ${severityList}`,
        {
          findings: findings.length,
        },
        { sessionId },
      );
    }

    // Always approve (warning only, not blocking)
    approveAndExit(HOOK_NAME);
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    approveAndExit(HOOK_NAME);
  }
}

if (import.meta.main) {
  main();
}
