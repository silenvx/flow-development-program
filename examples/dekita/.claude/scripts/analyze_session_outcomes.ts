#!/usr/bin/env bun
/**
 * セッション成果データを分析する。
 *
 * Why:
 *   セッションの生産性とタスク種別の分布を把握し、
 *   改善ポイントを特定するため。
 *
 * What:
 *   - loadOutcomes(): 成果ログを読み込み
 *   - formatSession(): セッション詳細を表示
 *   - formatSummary(): 統計サマリーを表示
 *
 * State:
 *   - reads: .claude/logs/outcomes/session-outcomes.jsonl
 *
 * Remarks:
 *   - --days N で直近N日間にフィルタリング
 *   - --summary で統計サマリーを表示
 *   - --json でJSON出力
 *
 * Changelog:
 *   - silenvx/dekita#1158: セッション成果分析機能を追加
 *   - silenvx/dekita#3643: TypeScriptに移植
 *   - silenvx/dekita#3644: ストリーム処理に変更（メモリ効率改善）
 */

import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { parseArgs } from "node:util";

/** PR reference in outcome */
interface PRReference {
  number: number;
}

/** Issue reference in outcome */
interface IssueReference {
  number: number;
}

/** Session outcome data */
interface SessionOutcome {
  timestamp?: string;
  session_id?: string;
  task_type?: string;
  prs_merged?: PRReference[];
  prs_created?: PRReference[];
  issues_created?: IssueReference[];
  commits_count?: number;
}

/** Summary statistics */
interface SummaryStats {
  total_sessions: number;
  task_type_distribution: Record<string, number>;
  total_prs_merged: number;
  total_prs_created: number;
  total_issues_created: number;
  total_commits: number;
}

/**
 * Get the outcome log file path.
 */
function getOutcomeLogFile(): string {
  const scriptDir = dirname(import.meta.path);
  return resolve(scriptDir, "..", "logs", "outcomes", "session-outcomes.jsonl");
}

/**
 * Load session outcomes from log file using stream processing.
 * This is more memory-efficient for large files.
 *
 * Uses Bun.file().lines() API for clean, readable stream processing.
 *
 * @param days - If specified, only load outcomes from the last N days
 * @param filePath - Optional custom file path (for testing)
 * @returns Promise resolving to list of outcome objects
 */
export async function loadOutcomes(days?: number, filePath?: string): Promise<SessionOutcome[]> {
  const logFile = filePath ?? getOutcomeLogFile();

  if (!existsSync(logFile)) {
    return [];
  }

  let cutoff: string | null = null;
  if (days !== undefined && days > 0) {
    const cutoffDate = new Date();
    cutoffDate.setDate(cutoffDate.getDate() - days);
    cutoff = cutoffDate.toISOString();
  }

  const outcomes: SessionOutcome[] = [];

  try {
    const file = Bun.file(logFile);
    for await (const line of file.lines()) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      try {
        const entry = JSON.parse(trimmed) as SessionOutcome;
        if (!cutoff || (entry.timestamp ?? "") >= cutoff) {
          outcomes.push(entry);
        }
      } catch {
        // Skip malformed JSON lines
      }
    }
  } catch {
    // File read error - return whatever outcomes we've collected so far
  }

  return outcomes;
}

/**
 * Format a single session outcome for display.
 *
 * @param outcome - Session outcome object
 * @returns Formatted string
 */
function formatSession(outcome: SessionOutcome): string {
  const lines: string[] = [];

  // Header with timestamp and task type
  const timestamp = (outcome.timestamp ?? "").slice(0, 19); // Truncate to seconds
  const taskType = outcome.task_type ?? "unknown";
  const sessionId = (outcome.session_id ?? "").slice(0, 8);

  lines.push(`[${timestamp}] ${taskType} (session: ${sessionId})`);

  // PRs
  const prsMerged = outcome.prs_merged ?? [];
  const prsCreated = outcome.prs_created ?? [];
  if (prsMerged.length > 0) {
    const prList = prsMerged.map((pr) => `#${pr.number}`).join(", ");
    lines.push(`  Merged PRs: ${prList}`);
  }
  if (prsCreated.length > 0) {
    const prList = prsCreated.map((pr) => `#${pr.number}`).join(", ");
    lines.push(`  Created PRs: ${prList}`);
  }

  // Issues
  const issuesCreated = outcome.issues_created ?? [];
  if (issuesCreated.length > 0) {
    const issueList = issuesCreated.map((issue) => `#${issue.number}`).join(", ");
    lines.push(`  Created Issues: ${issueList}`);
  }

  // Commits
  const commitsCount = outcome.commits_count ?? 0;
  if (commitsCount > 0) {
    lines.push(`  Commits: ${commitsCount}`);
  }

  return lines.join("\n");
}

/**
 * Count occurrences of each task type.
 */
function countTaskTypes(outcomes: SessionOutcome[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const outcome of outcomes) {
    const taskType = outcome.task_type ?? "unknown";
    counts.set(taskType, (counts.get(taskType) ?? 0) + 1);
  }
  return counts;
}

/**
 * Format summary statistics for outcomes.
 *
 * @param outcomes - List of session outcome objects
 * @returns Formatted summary string
 */
function formatSummary(outcomes: SessionOutcome[]): string {
  if (outcomes.length === 0) {
    return "No session outcomes found.";
  }

  const lines: string[] = ["Session Outcome Summary", "=".repeat(40)];

  // Task type distribution
  const taskTypes = countTaskTypes(outcomes);
  const sortedTaskTypes = [...taskTypes.entries()].sort((a, b) => b[1] - a[1]);

  lines.push("\nTask Type Distribution:");
  for (const [taskType, count] of sortedTaskTypes) {
    const percentage = ((count / outcomes.length) * 100).toFixed(1);
    lines.push(`  ${taskType}: ${count} (${percentage}%)`);
  }

  // Totals
  const totalPrsMerged = outcomes.reduce((sum, o) => sum + (o.prs_merged?.length ?? 0), 0);
  const totalPrsCreated = outcomes.reduce((sum, o) => sum + (o.prs_created?.length ?? 0), 0);
  const totalIssuesCreated = outcomes.reduce((sum, o) => sum + (o.issues_created?.length ?? 0), 0);
  const totalCommits = outcomes.reduce((sum, o) => sum + (o.commits_count ?? 0), 0);

  lines.push("\nTotals:");
  lines.push(`  Sessions: ${outcomes.length}`);
  lines.push(`  PRs Merged: ${totalPrsMerged}`);
  lines.push(`  PRs Created: ${totalPrsCreated}`);
  lines.push(`  Issues Created: ${totalIssuesCreated}`);
  lines.push(`  Commits: ${totalCommits}`);

  // Averages
  if (outcomes.length > 0) {
    lines.push("\nAverages per Session:");
    lines.push(`  PRs Merged: ${(totalPrsMerged / outcomes.length).toFixed(1)}`);
    lines.push(`  Commits: ${(totalCommits / outcomes.length).toFixed(1)}`);
  }

  return lines.join("\n");
}

/**
 * Build summary stats object for JSON output.
 */
function buildSummaryStats(outcomes: SessionOutcome[]): SummaryStats {
  const taskTypes = countTaskTypes(outcomes);
  const taskTypeDistribution: Record<string, number> = {};
  for (const [key, value] of taskTypes) {
    taskTypeDistribution[key] = value;
  }

  return {
    total_sessions: outcomes.length,
    task_type_distribution: taskTypeDistribution,
    total_prs_merged: outcomes.reduce((sum, o) => sum + (o.prs_merged?.length ?? 0), 0),
    total_prs_created: outcomes.reduce((sum, o) => sum + (o.prs_created?.length ?? 0), 0),
    total_issues_created: outcomes.reduce((sum, o) => sum + (o.issues_created?.length ?? 0), 0),
    total_commits: outcomes.reduce((sum, o) => sum + (o.commits_count ?? 0), 0),
  };
}

function printHelp(): void {
  console.log(`Usage: analyze_session_outcomes.ts [options]

セッション成果ログを分析し、統計情報を表示する。

Options:
  -d, --days <n>    直近n日間のデータのみ分析
  -s, --summary     サマリー統計のみ表示
  -l, --limit <n>   表示するセッション数を制限（デフォルト: 10）
  -j, --json        JSON形式で出力
  -h, --help        このヘルプを表示

Examples:
  analyze_session_outcomes.ts
  analyze_session_outcomes.ts --days 7 --summary
  analyze_session_outcomes.ts -d 3 -j`);
}

async function main(): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(2),
    options: {
      days: { type: "string", short: "d" },
      summary: { type: "boolean", short: "s" },
      limit: { type: "string", short: "l" },
      json: { type: "boolean", short: "j" },
      help: { type: "boolean", short: "h" },
    },
    allowPositionals: false,
  });

  if (values.help) {
    printHelp();
    process.exit(0);
  }

  const days = values.days ? Number.parseInt(values.days, 10) : undefined;
  const limit = values.limit ? Number.parseInt(values.limit, 10) : 10;

  // Load outcomes (async for stream processing)
  const outcomes = await loadOutcomes(days);

  if (outcomes.length === 0) {
    if (values.json) {
      // Always return valid JSON when --json flag is used
      console.log(values.summary ? JSON.stringify(buildSummaryStats([]), null, 2) : "[]");
    } else {
      console.log("No session outcomes found.");
      console.log(`Outcome log file: ${getOutcomeLogFile()}`);
    }
    process.exit(0);
  }

  if (values.json) {
    // Output as JSON
    if (values.summary) {
      const summary = buildSummaryStats(outcomes);
      console.log(JSON.stringify(summary, null, 2));
    } else {
      const recent = outcomes.slice(-limit);
      console.log(JSON.stringify(recent, null, 2));
    }
  } else if (values.summary) {
    console.log(formatSummary(outcomes));
  } else {
    // Show individual sessions (most recent first)
    const recent = outcomes.slice(-limit).reverse();

    console.log(`Session Outcomes (showing ${recent.length} of ${outcomes.length})`);
    console.log("-".repeat(50));
    for (const outcome of recent) {
      console.log(formatSession(outcome));
      console.log();
    }
  }
}

if (import.meta.main) {
  main();
}
