#!/usr/bin/env bun
/**
 * セッション終了時に成果物（PR、Issue、コミット）を収集。
 *
 * Why:
 *   セッションの成果を定量的に記録することで、フック有効性の測定や
 *   事後分析が可能になる。リアルタイム追跡ではなく結果ベースで評価する。
 *
 * What:
 *   - セッション終了時（Stop）に発火
 *   - セッション開始時刻以降のPR作成・マージを収集
 *   - Issue作成、コミット数を収集
 *   - 成果物からタスクタイプを推定
 *   - outcomes/にセッション別で保存
 *
 * State:
 *   - reads: .claude/logs/flow/state-*.json（セッション開始時刻）
 *   - writes: .claude/logs/outcomes/session-outcomes-*.jsonl
 *
 * Remarks:
 *   - 非ブロック型（Stopフック）
 *   - GitHub API経由でPR/Issueを取得
 *   - flow_definitions.tsのestimateTaskType()でタスクタイプ推定
 *
 * Changelog:
 *   - silenvx/dekita#1158: フック追加（成果物ベース評価）
 *   - silenvx/dekita#1840: セッション別ファイル出力
 *   - silenvx/dekita#2545: HookContextパターン移行
 *   - silenvx/dekita#3161: TypeScript移行
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { FLOW_LOG_DIR } from "../lib/common";
import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { estimateTaskType } from "../lib/flow_definitions";
import { formatError } from "../lib/format_error";
import { logHookExecution, logToSessionFile } from "../lib/logging";
import { approveAndExit } from "../lib/results";
import { createHookContext, isSafeSessionId, parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";

const HOOK_NAME = "session_outcome_collector";

// Outcome log directory
// Issue #3161: Must be .claude/logs/outcomes (not .claude/hooks/logs/outcomes)
// __dirname = .claude/hooks/handlers, so need 2 levels up to reach .claude
const OUTCOME_LOG_DIR = resolve(dirname(dirname(__dirname)), "logs", "outcomes");

// Batch size for parallel PR commit checking (Issue #3300)
const BATCH_SIZE = 5;

// =============================================================================
// Types
// =============================================================================

interface PrInfo {
  number: number;
  title: string;
  state?: string;
  url: string;
}

interface IssueInfo {
  number: number;
  title: string;
  url: string;
}

interface SessionOutcome {
  timestamp: string;
  session_id: string | null | undefined;
  session_start: string;
  task_type: string;
  prs_created: PrInfo[];
  prs_merged: PrInfo[];
  prs_pushed: number[];
  issues_created: IssueInfo[];
  commits_count: number;
}

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Get session start time from flow state file.
 *
 * Reads the state-{session_id}.json file and extracts global.session_start_time.
 */
function getSessionStartTime(sessionId: string | null | undefined): Date | null {
  if (!sessionId || !isSafeSessionId(sessionId)) {
    return null;
  }

  const stateFile = join(FLOW_LOG_DIR, `state-${sessionId}.json`);

  try {
    if (!existsSync(stateFile)) {
      return null;
    }

    const content = readFileSync(stateFile, "utf-8");
    const state = JSON.parse(content) as {
      global?: { session_start_time?: string };
    };

    const startTimeStr = state?.global?.session_start_time;
    if (!startTimeStr) {
      return null;
    }

    const parsed = new Date(startTimeStr);
    if (Number.isNaN(parsed.getTime())) {
      return null;
    }

    return parsed;
  } catch {
    return null;
  }
}

/**
 * Format date to ISO string for GitHub API comparison (UTC).
 */
function toGitHubIsoString(date: Date): string {
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/**
 * Collect PRs created by current user since the given time.
 */
async function collectPrsCreated(since: Date): Promise<PrInfo[]> {
  const sinceStr = toGitHubIsoString(since);

  try {
    const result = await asyncSpawn(
      "gh",
      [
        "pr",
        "list",
        "--author",
        "@me",
        "--json",
        "number,title,state,url,createdAt",
        "--limit",
        "50",
      ],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (result.exitCode !== 0 || !result.stdout.trim()) {
      return [];
    }

    const prs = JSON.parse(result.stdout) as Array<{
      number: number;
      title: string;
      state: string;
      url: string;
      createdAt: string;
    }>;

    // Filter by creation time
    return prs
      .filter((pr) => pr.createdAt >= sinceStr)
      .map((pr) => ({
        number: pr.number,
        title: pr.title,
        state: pr.state,
        url: pr.url,
      }));
  } catch {
    return [];
  }
}

/**
 * Collect PRs merged by current user since the given time.
 */
async function collectPrsMerged(since: Date): Promise<PrInfo[]> {
  const sinceStr = toGitHubIsoString(since);

  try {
    const result = await asyncSpawn(
      "gh",
      [
        "pr",
        "list",
        "--author",
        "@me",
        "--state",
        "merged",
        "--json",
        "number,title,url,mergedAt",
        "--limit",
        "50",
      ],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (result.exitCode !== 0 || !result.stdout.trim()) {
      return [];
    }

    const prs = JSON.parse(result.stdout) as Array<{
      number: number;
      title: string;
      url: string;
      mergedAt: string;
    }>;

    // Filter by merge time
    return prs
      .filter((pr) => pr.mergedAt && pr.mergedAt >= sinceStr)
      .map((pr) => ({
        number: pr.number,
        title: pr.title,
        url: pr.url,
      }));
  } catch {
    return [];
  }
}

/**
 * Collect Issues created by current user since the given time.
 */
async function collectIssuesCreated(since: Date): Promise<IssueInfo[]> {
  const sinceStr = toGitHubIsoString(since);

  try {
    const result = await asyncSpawn(
      "gh",
      ["issue", "list", "--author", "@me", "--json", "number,title,url,createdAt", "--limit", "50"],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (result.exitCode !== 0 || !result.stdout.trim()) {
      return [];
    }

    const issues = JSON.parse(result.stdout) as Array<{
      number: number;
      title: string;
      url: string;
      createdAt: string;
    }>;

    // Filter by creation time
    return issues
      .filter((issue) => issue.createdAt >= sinceStr)
      .map((issue) => ({
        number: issue.number,
        title: issue.title,
        url: issue.url,
      }));
  } catch {
    return [];
  }
}

/**
 * Get current git user email.
 */
async function getGitUserEmail(): Promise<string> {
  try {
    const result = await asyncSpawn("git", ["config", "user.email"], {
      timeout: TIMEOUT_LIGHT * 1000,
    });

    if (result.exitCode === 0) {
      return result.stdout.trim();
    }
  } catch {
    // git config may fail, fallback to empty
  }

  return "";
}

/**
 * Count commits made by current user since the given time.
 */
async function collectCommitsCount(since: Date): Promise<number> {
  // Use isoformat() to preserve timezone info for accurate filtering
  const sinceStr = since.toISOString();

  try {
    const userEmail = await getGitUserEmail();
    const cmd = ["git", "log", `--since=${sinceStr}`, "--oneline"];
    if (userEmail) {
      cmd.push(`--author=${userEmail}`);
    }

    const result = await asyncSpawn(cmd[0], cmd.slice(1), {
      timeout: TIMEOUT_MEDIUM * 1000,
    });

    if (result.exitCode !== 0) {
      return 0;
    }

    const lines = result.stdout
      .trim()
      .split("\n")
      .filter((line) => line.length > 0);
    return lines.length;
  } catch {
    return 0;
  }
}

/**
 * Get commits for a PR that were committed after the given time.
 */
async function getPrCommitsSince(prNumber: number, since: Date): Promise<string[]> {
  const sinceStr = toGitHubIsoString(since);

  try {
    const result = await asyncSpawn("gh", ["pr", "view", String(prNumber), "--json", "commits"], {
      timeout: TIMEOUT_MEDIUM * 1000,
    });

    if (result.exitCode !== 0 || !result.stdout.trim()) {
      return [];
    }

    const data = JSON.parse(result.stdout) as {
      commits?: Array<{ committedDate: string; oid: string }>;
    };

    const commits = data.commits ?? [];

    // Filter commits by committedDate (not authoredDate) to catch rebased commits
    return commits
      .filter((commit) => commit.committedDate >= sinceStr)
      .map((commit) => commit.oid)
      .filter((oid): oid is string => Boolean(oid));
  } catch {
    return [];
  }
}

/**
 * Process items in batches with parallel execution within each batch.
 *
 * @throws Error if batchSize is not a positive number
 */
export async function processBatched<T, R>(
  items: T[],
  batchSize: number,
  processor: (item: T) => Promise<R>,
): Promise<R[]> {
  if (batchSize <= 0) {
    throw new Error(`batchSize must be positive, got ${batchSize}`);
  }
  const results: R[] = [];
  for (let i = 0; i < items.length; i += batchSize) {
    const batch = items.slice(i, i + batchSize);
    const batchResults = await Promise.all(batch.map(processor));
    results.push(...batchResults);
  }
  return results;
}

/**
 * Identify PRs that received pushes but were not created in this session.
 *
 * Issue #3300: Parallelized with batching to avoid N+1 problem and resource exhaustion.
 */
async function collectPrsPushed(since: Date, prsCreated: PrInfo[]): Promise<number[]> {
  const createdNumbers = new Set(prsCreated.map((pr) => pr.number));

  try {
    const result = await asyncSpawn(
      "gh",
      ["pr", "list", "--author", "@me", "--state", "open", "--json", "number", "--limit", "50"],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (result.exitCode !== 0 || !result.stdout.trim()) {
      return [];
    }

    const prs = JSON.parse(result.stdout) as Array<{ number: number }>;
    const prsToCheck = prs.filter((pr) => !createdNumbers.has(pr.number));

    const checkResults = await processBatched(prsToCheck, BATCH_SIZE, async (pr) => {
      const recentCommits = await getPrCommitsSince(pr.number, since);
      return recentCommits.length > 0 ? pr.number : null;
    });

    return checkResults.filter((n): n is number => n !== null);
  } catch {
    return [];
  }
}

/**
 * Save session outcome to JSONL log file.
 */
async function saveOutcome(sessionId: string, outcome: SessionOutcome): Promise<boolean> {
  return logToSessionFile(
    OUTCOME_LOG_DIR,
    "session-outcomes",
    sessionId,
    outcome as unknown as Record<string, unknown>,
  );
}

/**
 * Format outcome for display in session end message.
 */
function formatOutcomeSummary(outcome: SessionOutcome): string {
  const taskType = outcome.task_type ?? "unknown";
  const prsMerged = outcome.prs_merged ?? [];
  const prsCreated = outcome.prs_created ?? [];
  const issuesCreated = outcome.issues_created ?? [];
  const commitsCount = outcome.commits_count ?? 0;

  const lines = ["\n[session-outcome] セッション成果物:"];
  lines.push(`  タスクタイプ: ${taskType}`);

  if (prsMerged.length > 0) {
    const prList = prsMerged.map((pr) => `#${pr.number}`).join(", ");
    lines.push(`  マージ済みPR: ${prList}`);
  }

  if (prsCreated.length > 0) {
    const prList = prsCreated.map((pr) => `#${pr.number}`).join(", ");
    lines.push(`  作成したPR: ${prList}`);
  }

  if (issuesCreated.length > 0) {
    const issueList = issuesCreated.map((issue) => `#${issue.number}`).join(", ");
    lines.push(`  作成したIssue: ${issueList}`);
  }

  lines.push(`  コミット数: ${commitsCount}`);

  return lines.join("\n");
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};

  try {
    const input = await parseHookInput();

    // Prevent infinite loops in Stop hooks
    if (input.stop_hook_active) {
      console.log(JSON.stringify(result));
      return;
    }

    const ctx = createHookContext(input);
    const sessionId = ctx.sessionId;
    const sessionStart = getSessionStartTime(sessionId);

    if (!sessionStart) {
      // No session start time available, skip collection
      console.log(JSON.stringify(result));
      return;
    }

    // Collect session outcomes in parallel (Issue #3300)
    const [prsCreated, prsMerged, issuesCreated, commitsCount] = await Promise.all([
      collectPrsCreated(sessionStart),
      collectPrsMerged(sessionStart),
      collectIssuesCreated(sessionStart),
      collectCommitsCount(sessionStart),
    ]);

    // Depends on prsCreated, so run after the parallel batch
    const prsPushed = await collectPrsPushed(sessionStart, prsCreated);

    // Build outcomes dict for task type estimation
    const outcomesForEstimation = {
      prs_merged: prsMerged.map((pr) => pr.number),
      prs_created: prsCreated.map((pr) => pr.number),
      prs_pushed: prsPushed,
      issues_created: issuesCreated.map((issue) => issue.number),
      commits_count: commitsCount,
    };

    // Estimate task type
    const taskType = estimateTaskType(outcomesForEstimation);

    // Build full outcome record
    const outcome: SessionOutcome = {
      timestamp: new Date().toISOString(),
      session_id: sessionId,
      session_start: sessionStart.toISOString(),
      task_type: taskType,
      prs_created: prsCreated,
      prs_merged: prsMerged,
      prs_pushed: prsPushed,
      issues_created: issuesCreated,
      commits_count: commitsCount,
    };

    // Save to log
    if (sessionId) {
      await saveOutcome(sessionId, outcome);
    }

    // Log outcome collection
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Session outcomes collected: ${taskType}`,
      {
        task_type: taskType,
        prs_created: prsCreated.length,
        prs_merged: prsMerged.length,
        issues_created: issuesCreated.length,
        commits_count: commitsCount,
      },
      { sessionId: sessionId ?? undefined },
    );

    // Format summary for display
    const summary = formatOutcomeSummary(outcome);
    result.systemMessage = summary;
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", `Error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    approveAndExit(HOOK_NAME);
  });
}
