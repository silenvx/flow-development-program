#!/usr/bin/env bun
/**
 * gh pr merge成功後に計画ファイルのチェックボックスを自動更新する。
 *
 * Why:
 *   PRマージ後に計画ファイルのチェックボックスが未完了のまま残ると、
 *   進捗状況が不明確になる。自動更新で一貫性を維持する。
 *
 * What:
 *   - gh pr mergeの成功を検出
 *   - PRからブランチ名→Issue番号を抽出
 *   - 対応する計画ファイルを検索
 *   - 全チェックボックスを[ ]から[x]に更新
 *
 * State:
 *   - writes: .claude/plans/*.md
 *   - writes: ~/.claude/plans/*.md
 *
 * Remarks:
 *   - 自動化型フック（ブロックしない、ファイル自動更新）
 *   - PostToolUse:Bashで発火（gh pr merge成功時）
 *   - .claude/plans/と~/.claude/plans/の両方を検索
 *   - コードブロック内のチェックボックスは更新しない
 *
 * Changelog:
 *   - silenvx/dekita#1336: フック追加
 *   - silenvx/dekita#1566: インデントコードブロック区別
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { execSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { isInIndentedCodeBlock } from "../lib/markdown";
import { isMergeSuccess } from "../lib/repo";
import { getToolResult, parseHookInput } from "../lib/session";

const HOOK_NAME = "plan-file-updater";

/**
 * Get repository root directory.
 */
function getRepoRoot(): string | null {
  const envDir = process.env.CLAUDE_PROJECT_DIR;
  if (envDir) {
    return envDir;
  }
  try {
    const result = execSync("git rev-parse --show-toplevel", {
      encoding: "utf-8",
      timeout: 5000,
    });
    return result.trim();
  } catch {
    return process.cwd();
  }
}

/**
 * Extract PR number from gh pr merge command.
 * Supports commands with flags (e.g., gh pr merge --squash 123)
 */
export function extractPrNumberFromCommand(command: string): number | null {
  // Allow flags between 'merge' and the number
  const match = command.match(/gh\s+pr\s+merge\s+(?:.*?\s+)?#?(\d+)/);
  return match ? Number.parseInt(match[1], 10) : null;
}

/**
 * Get the head branch name for a PR.
 */
function getPrBranch(prNumber: number, repoRoot: string): string | null {
  try {
    const result = execSync(`gh pr view ${prNumber} --json headRefName -q .headRefName`, {
      encoding: "utf-8",
      timeout: 30000,
      cwd: repoRoot,
    });
    return result.trim() || null;
  } catch {
    return null;
  }
}

/**
 * Extract Issue number from branch name.
 */
export function extractIssueNumberFromBranch(branchName: string): string | null {
  const match = branchName.match(/issue-(\d+)/i);
  return match ? match[1] : null;
}

/**
 * Safe mtime getter.
 */
function safeMtime(filePath: string): number {
  try {
    return statSync(filePath).mtimeMs;
  } catch {
    return 0;
  }
}

/**
 * Find plan file for the given Issue number.
 */
function findPlanFile(issueNumber: string, repoRoot: string): string | null {
  const plansDir = join(repoRoot, ".claude", "plans");

  // Try exact match first
  const exactPath = join(plansDir, `issue-${issueNumber}.md`);
  if (existsSync(exactPath)) {
    return exactPath;
  }

  // Search for pattern match in .claude/plans/
  if (existsSync(plansDir)) {
    const pattern = `issue-${issueNumber}`;
    try {
      const files = readdirSync(plansDir);
      const matches = files
        .filter((f) => f.endsWith(".md") && f.toLowerCase().includes(pattern))
        .map((f) => join(plansDir, f));

      if (matches.length > 0) {
        return matches.reduce((a, b) => (safeMtime(a) > safeMtime(b) ? a : b));
      }
    } catch {
      // 意図的に空 - ディレクトリ読み取りエラーは検索続行で対応
    }
  }

  // Search in ~/.claude/plans/
  const homePlansDir = join(homedir(), ".claude", "plans");
  if (existsSync(homePlansDir)) {
    try {
      const files = readdirSync(homePlansDir);
      const sortedFiles = files
        .filter((f) => f.endsWith(".md"))
        .map((f) => join(homePlansDir, f))
        .sort((a, b) => safeMtime(b) - safeMtime(a));

      for (const filePath of sortedFiles) {
        try {
          const content = readFileSync(filePath, "utf-8");
          if (
            content.includes(`Issue #${issueNumber}`) ||
            content.toLowerCase().includes(`issue-${issueNumber}`)
          ) {
            return filePath;
          }
        } catch {
          // 意図的に空 - ファイル読み取りエラーはスキップで対応
        }
      }
    } catch {
      // 意図的に空 - ディレクトリ読み取りエラーはnull返却で対応
    }
  }

  return null;
}

/**
 * Update unchecked checkboxes to checked in the plan file.
 */
function updatePlanCheckboxes(planPath: string): { updated: boolean; count: number } {
  let content: string;
  try {
    content = readFileSync(planPath, "utf-8");
  } catch {
    return { updated: false, count: 0 };
  }

  const listCheckboxPattern = /^(\s*(?:[-*+]|\d+\.)\s+)\[ \]/;

  // Split by fenced code blocks
  const codeBlockPattern =
    /((?:^|(?<=\n))\s*```[\s\S]*?```|(?:^|(?<=\n))\s*~~~[\s\S]*?~~~|(?:^|(?<=\n))\s*```[\s\S]*$|(?:^|(?<=\n))\s*~~~[\s\S]*$)/;
  const segments = content.split(codeBlockPattern);

  let uncheckedCount = 0;
  const updatedSegments: string[] = [];

  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i];

    // Odd indices are fenced code blocks
    if (i % 2 === 1) {
      updatedSegments.push(segment);
    } else {
      const isFirstSegment = i === 0;
      const lines = segment.split("\n");
      const updatedLines: string[] = [];

      for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
        const line = lines[lineIdx];

        if (isInIndentedCodeBlock(lines, lineIdx, isFirstSegment)) {
          updatedLines.push(line);
          continue;
        }

        const match = line.match(listCheckboxPattern);
        if (match) {
          uncheckedCount++;
          const updatedLine = line.replace(listCheckboxPattern, "$1[x]");
          updatedLines.push(updatedLine);
        } else {
          updatedLines.push(line);
        }
      }

      updatedSegments.push(updatedLines.join("\n"));
    }
  }

  if (uncheckedCount === 0) {
    return { updated: false, count: 0 };
  }

  const updatedContent = updatedSegments.join("");

  try {
    writeFileSync(planPath, updatedContent, "utf-8");
    return { updated: true, count: uncheckedCount };
  } catch {
    return { updated: false, count: 0 };
  }
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const hookInput = await parseHookInput();
    sessionId = hookInput.session_id;
    const toolName = hookInput.tool_name ?? "";
    const toolInput = (hookInput.tool_input ?? {}) as Record<string, unknown>;
    const rawResult = getToolResult(hookInput);
    const toolResult =
      typeof rawResult === "object" && rawResult ? (rawResult as Record<string, unknown>) : {};

    // Only handle Bash tool
    if (toolName !== "Bash") {
      return;
    }

    const command = (toolInput.command as string) ?? "";

    // Only handle gh pr merge commands
    if (!command.includes("gh pr merge")) {
      return;
    }

    // Check if merge was successful
    const exitCode = (toolResult.exit_code as number) ?? 0;
    const stdout = (toolResult.stdout as string) ?? "";
    const stderr = (toolResult.stderr as string) ?? "";

    if (!isMergeSuccess(exitCode, stdout, command, stderr)) {
      return;
    }

    const repoRoot = getRepoRoot();
    if (!repoRoot) {
      return;
    }

    const prNumber = extractPrNumberFromCommand(command);
    if (!prNumber) {
      return;
    }

    const branchName = getPrBranch(prNumber, repoRoot);
    if (!branchName) {
      return;
    }

    const issueNumber = extractIssueNumberFromBranch(branchName);
    if (!issueNumber) {
      return;
    }

    const planPath = findPlanFile(issueNumber, repoRoot);
    if (!planPath) {
      return;
    }

    const { updated, count } = updatePlanCheckboxes(planPath);
    if (updated) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "success",
        {
          pr_number: prNumber,
          issue_number: issueNumber,
          plan_file: planPath,
          checkboxes_updated: count,
        },
        { sessionId },
      );

      const fileName = planPath.split("/").pop() ?? planPath;
      const result = {
        continue: true,
        systemMessage: `✅ 計画ファイル更新: ${fileName} (${count}個のチェックボックスを完了)`,
      };
      console.log(JSON.stringify(result));
    }
  } catch {
    // フック実行の失敗でClaude Codeをブロックしない
  }
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({}));
  });
}
