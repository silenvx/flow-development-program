#!/usr/bin/env bun
/**
 * Observation Issue自動検証スクリプト
 *
 * Why:
 *   動作確認（observation）Issueが放置されがち。
 *   意図的にオープンIssueを検証し、条件を満たせば自動クローズする。
 *
 * What:
 *   - オープンなobservation issueを取得
 *   - 各issueの「確認コマンド」セクションからコマンドを抽出
 *   - コマンドを実行し、成功ならClaude確認項目をチェック
 *   - 全Claude確認項目がチェック済みになったissueを自動クローズ
 *   - 人間確認項目のみ残るissueはクローズせずに報告
 *
 * Usage:
 *   bun run .claude/scripts/observation_verifier_ts/main.ts            # dry-run (default)
 *   bun run .claude/scripts/observation_verifier_ts/main.ts --execute  # actually run commands
 *
 * Changelog:
 *   - silenvx/dekita#3979: Initial implementation
 */

import { $ } from "bun";

// =============================================================================
// Types
// =============================================================================

interface ObservationIssue {
  number: number;
  title: string;
  body: string;
  createdAt: string;
}

interface VerificationItem {
  command: string;
  description: string;
  isHumanOnly: boolean;
  isChecked: boolean;
}

interface VerificationResult {
  issueNumber: number;
  title: string;
  items: Array<{
    description: string;
    command: string;
    passed: boolean;
    isHumanOnly: boolean;
    isPlaceholderSkipped?: boolean;
    output?: string;
  }>;
  allClaudePassed: boolean;
  hasHumanItems: boolean;
  canAutoClose: boolean;
}

// =============================================================================
// Issue Fetching
// =============================================================================

async function fetchObservationIssues(): Promise<ObservationIssue[]> {
  try {
    const result =
      await $`gh issue list --label observation --state open --json number,title,body,createdAt --limit 50`.quiet();
    return JSON.parse(result.stdout.toString());
  } catch (error) {
    console.error("[observation-verifier] Failed to fetch observation issues:", error);
    throw error;
  }
}

// =============================================================================
// Placeholder Detection
// =============================================================================

/**
 * コマンドにプレースホルダー（<フック名>等）が含まれているか確認する。
 * プレースホルダーを含むコマンドはbash実行時にシェルがリダイレクトと解釈してエラーになるため、
 * 検出してスキップする必要がある。
 */
export function hasPlaceholder(command: string): boolean {
  // Use [^>\s]+ to avoid matching shell redirection patterns like "< input > output"
  // Placeholders are assumed to be single tokens without whitespace (e.g., <token>)
  return /<[^>\s]+>/.test(command);
}

// =============================================================================
// Command Extraction
// =============================================================================

/**
 * Issue本文から確認コマンドセクションを抽出する。
 *
 * 期待するフォーマット:
 * ## 確認コマンド
 * - [ ] [Claude] `pnpm build` - ビルドが成功する
 * - [ ] [人間] ブラウザで動作確認
 */
export function extractVerificationItems(body: string): VerificationItem[] {
  const items: VerificationItem[] = [];

  // 「確認コマンド」「確認手順」「検証」セクションを探す
  const sectionPattern = /##\s*(?:確認コマンド|確認手順|検証)[^\n]*\n([\s\S]*?)(?=\n##\s|\n---|$)/i;
  const match = body.match(sectionPattern);
  if (!match) return items;

  const section = match[1];
  const lines = section.split("\n");

  for (const line of lines) {
    const trimmed = line.trim();
    // チェックリスト形式: - [ ] [Claude] `command` - description
    const checklistMatch = trimmed.match(
      /^-\s*\[([ xX])\]\s*(?:\[([^\]]+)\]\s*)?`([^`]+)`\s*[-–—]?\s*(.*)/i,
    );
    if (checklistMatch) {
      const isChecked = checklistMatch[1].toLowerCase() === "x";
      const tag = (checklistMatch[2] || "Claude").trim().toLowerCase();
      const command = checklistMatch[3];
      const description = checklistMatch[4] || command;
      items.push({
        command,
        description,
        isHumanOnly: tag === "人間" || tag === "human",
        isChecked,
      });
      continue;
    }

    // 人間確認項目（コマンドなし）: - [ ] [人間] ブラウザで確認
    const humanMatch = trimmed.match(/^-\s*\[([ xX])\]\s*\[\s*(?:人間|human)\s*\]\s*(.*)/i);
    if (humanMatch) {
      items.push({
        command: "",
        description: humanMatch[2],
        isHumanOnly: true,
        isChecked: humanMatch[1].toLowerCase() === "x",
      });
    }
  }

  return items;
}

// =============================================================================
// Command Execution
// =============================================================================

async function executeVerificationCommand(
  command: string,
): Promise<{ success: boolean; output: string }> {
  try {
    const result = await $`bash -c ${command}`.quiet().nothrow().timeout(300_000);
    const output = [result.stdout.toString(), result.stderr.toString()].join("\n").trim();
    return { success: result.exitCode === 0, output };
  } catch (error: unknown) {
    const output = error instanceof Error ? error.message : "Command execution failed";
    return { success: false, output };
  }
}

// =============================================================================
// Verification
// =============================================================================

async function verifyIssue(issue: ObservationIssue, dryRun: boolean): Promise<VerificationResult> {
  const items = extractVerificationItems(issue.body);

  const results: VerificationResult["items"] = [];
  let allClaudePassed = true;
  let hasHumanItems = false;

  for (const item of items) {
    if (item.isHumanOnly) {
      // Human items already checked ([x]) count as passed
      if (!item.isChecked) {
        hasHumanItems = true;
      }
      results.push({
        description: item.description,
        command: item.command,
        passed: item.isChecked,
        isHumanOnly: true,
      });
      continue;
    }

    if (!item.command) continue;

    // Already checked Claude items ([x]) count as passed without re-execution
    if (item.isChecked) {
      results.push({
        description: item.description,
        command: item.command,
        passed: true,
        isHumanOnly: false,
      });
      continue;
    }

    // Check for placeholders before execution
    if (hasPlaceholder(item.command)) {
      results.push({
        description: item.description,
        command: item.command,
        passed: false,
        isHumanOnly: false,
        isPlaceholderSkipped: true,
        output: "[SKIPPED] Contains placeholder",
      });
      // Placeholder-skipped items don't count as failures for auto-close
      continue;
    }

    if (dryRun) {
      results.push({
        description: item.description,
        command: item.command,
        passed: false,
        isHumanOnly: false,
        output: "[DRY RUN] Command skipped",
      });
      allClaudePassed = false;
      continue;
    }

    const { success, output } = await executeVerificationCommand(item.command);
    results.push({
      description: item.description,
      command: item.command,
      passed: success,
      isHumanOnly: false,
      output: success ? undefined : output,
    });

    if (!success) {
      allClaudePassed = false;
    }
  }

  // Count Claude items excluding placeholder-skipped ones
  const executableClaudeItems = results.filter((r) => !r.isHumanOnly && !r.isPlaceholderSkipped);
  const hasExecutableClaudeItems = executableClaudeItems.length > 0;
  const hasSkippedItems = results.some((r) => r.isPlaceholderSkipped);

  // Auto-close is possible only if:
  // 1. There are executable Claude items (not just placeholders)
  // 2. All executable Claude items passed
  // 3. There are no pending human items
  // 4. There are no skipped placeholder items (skipped items imply incomplete verification)
  const canAutoClose =
    hasExecutableClaudeItems && allClaudePassed && !hasHumanItems && !hasSkippedItems;

  return {
    issueNumber: issue.number,
    title: issue.title,
    items: results,
    allClaudePassed: hasExecutableClaudeItems && allClaudePassed,
    hasHumanItems,
    canAutoClose,
  };
}

// =============================================================================
// Issue Close
// =============================================================================

async function closeIssue(issueNumber: number, reason: string): Promise<boolean> {
  try {
    await $`gh issue close ${issueNumber} --comment ${`[自動検証] 全てのClaude確認項目がパスしました。\n\n${reason}\n\n-- observation-verifier`}`.quiet();
    return true;
  } catch {
    console.error(`[observation-verifier] Failed to close issue #${issueNumber}`);
    return false;
  }
}

// =============================================================================
// Report
// =============================================================================

function printReport(results: VerificationResult[], dryRun: boolean): void {
  console.log(`\n${"=".repeat(60)}`);
  console.log("[observation-verifier] 検証結果サマリー");
  console.log("=".repeat(60));

  if (results.length === 0) {
    console.log("\nオープンなobservation issueはありません。");
    return;
  }

  const closed = results.filter((r) => r.canAutoClose);
  const humanOnly = results.filter((r) => r.allClaudePassed && r.hasHumanItems);
  const failed = results.filter((r) => !r.allClaudePassed);
  const noItems = results.filter((r) => r.items.length === 0);

  if (closed.length > 0) {
    console.log(
      `\n${dryRun ? "[DRY-RUN] " : ""}自動クローズ${dryRun ? "対象" : "済み"} (${closed.length}件):`,
    );
    for (const r of closed) {
      console.log(`  #${r.issueNumber}: ${r.title}`);
    }
  }

  if (humanOnly.length > 0) {
    console.log(`\n人間確認待ち (${humanOnly.length}件):`);
    for (const r of humanOnly) {
      console.log(`  #${r.issueNumber}: ${r.title}`);
      for (const item of r.items.filter((i) => i.isHumanOnly)) {
        console.log(`      - ${item.description}`);
      }
    }
  }

  if (failed.length > 0) {
    console.log(`\n検証失敗 (${failed.length}件):`);
    for (const r of failed) {
      console.log(`  #${r.issueNumber}: ${r.title}`);
      // Exclude placeholder-skipped items from failure report
      for (const item of r.items.filter(
        (i) => !i.passed && !i.isHumanOnly && !i.isPlaceholderSkipped,
      )) {
        console.log(`      - ${item.description}: ${item.command}`);
        if (item.output) {
          console.log(`        出力: ${item.output.slice(0, 1000)}`);
        }
      }
    }
  }

  // Report placeholder-skipped items separately
  const skippedItems = results.flatMap((r) =>
    r.items
      .filter((i) => i.isPlaceholderSkipped)
      .map((i) => ({ issueNumber: r.issueNumber, item: i })),
  );
  if (skippedItems.length > 0) {
    console.log(`\nプレースホルダースキップ (${skippedItems.length}件):`);
    for (const { issueNumber, item } of skippedItems) {
      console.log(`  #${issueNumber}: ${item.command}`);
    }
  }

  if (noItems.length > 0) {
    console.log(`\n確認項目なし (${noItems.length}件):`);
    for (const r of noItems) {
      console.log(`  #${r.issueNumber}: ${r.title}`);
    }
  }

  console.log(`\n${"=".repeat(60)}`);
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const execute = process.argv.includes("--execute");
  const dryRun = !execute;

  console.log("[observation-verifier] オープンなobservation issueを検証中...");
  if (dryRun) {
    console.log(
      "[observation-verifier] DRY-RUN mode: コマンドは実行されません。実行するには --execute を付けてください。",
    );
  }

  const issues = await fetchObservationIssues();
  if (issues.length === 0) {
    console.log("[observation-verifier] オープンなobservation issueはありません。");
    return;
  }

  console.log(`[observation-verifier] ${issues.length}件のissueを検証します。`);

  const results: VerificationResult[] = [];
  for (const issue of issues) {
    const result = await verifyIssue(issue, dryRun);
    results.push(result);

    if (result.canAutoClose && !dryRun) {
      const passedItems = result.items
        .filter((i) => i.passed)
        .map((i) => `- ${i.description}`)
        .join("\n");
      const closed = await closeIssue(result.issueNumber, passedItems);
      if (!closed) {
        result.canAutoClose = false;
      }
    }
  }

  printReport(results, dryRun);
}

if (import.meta.main) {
  main();
}
