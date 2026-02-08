#!/usr/bin/env bun
/**
 * PRマージ後にコードベース内の類似パターンを検索し修正漏れを防ぐ。
 *
 * Why:
 *   共通パターン（json.dumps等）を修正する際、同様のパターンが他ファイルに
 *   存在すると修正漏れが発生する。マージ後に自動検索して警告する。
 *
 * What:
 *   - PRマージ成功後（PostToolUse:Bash）に発火
 *   - PR diffから関数呼び出しパターンを抽出
 *   - 変更されたファイル以外で同パターンを検索
 *   - 見つかった場合はsystemMessageで通知
 *
 * State:
 *   - reads: GitHub API (PR diff, changed files)
 *
 * Remarks:
 *   - 非ブロック型（情報提供のみ）
 *   - duplicate-issue-checkはIssue重複、本フックはコードパターン重複
 *   - 一般的すぎる関数（print, len等）はCOMMON_FUNCTIONSで除外
 *
 * Changelog:
 *   - silenvx/dekita#2103: フック追加（Issue #2054/2065の再発防止）
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { execFileSync, execSync } from "node:child_process";
import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { getToolResult, parseHookInput } from "../lib/session";

const HOOK_NAME = "similar-pattern-search";

// Maximum number of results to display
const MAX_RESULTS = 5;

// Patterns to exclude from search
const EXCLUDE_PATTERNS = [
  "*.pyc",
  "__pycache__",
  "node_modules",
  ".git",
  "*.min.js",
  "*.min.css",
  "pnpm-lock.yaml",
  "package-lock.json",
];

// Common functions to exclude (too generic)
export const COMMON_FUNCTIONS = new Set([
  "print",
  "len",
  "str",
  "int",
  "float",
  "list",
  "dict",
  "set",
  "tuple",
  "range",
  "enumerate",
  "zip",
  "map",
  "filter",
  "sorted",
  "reversed",
  "open",
  "type",
  "isinstance",
  "hasattr",
  "getattr",
  "setattr",
  "self",
  "super",
  "return",
  "if",
  "for",
  "while",
  "with",
  "assert",
  "raise",
  "except",
  "import",
  "from",
  "class",
  "def",
  "async",
  "await",
  "lambda",
  "get",
  "add",
  "remove",
  "pop",
  "append",
  "extend",
  "update",
  "items",
  "keys",
  "values",
  "join",
  "split",
  "strip",
  "replace",
  "format",
  "lower",
  "upper",
  "startswith",
  "endswith",
  "find",
  "index",
  "count",
]);

export interface SearchResult {
  file: string;
  line: string;
  content: string;
}

/**
 * Check if the command is a PR merge command.
 */
export function isPrMergeCommand(command: string): boolean {
  return command.includes("gh pr merge");
}

/**
 * Check if merge was successful.
 */
export function isMergeSuccess(exitCode: number, output: string): boolean {
  if (exitCode !== 0) {
    return false;
  }

  // Check for merge success patterns
  const successPatterns = [/merged/i, /pull request.*merged/i];
  const failurePatterns = [/failed/i, /error/i, /not mergeable/i];

  // Check for explicit failure
  for (const pattern of failurePatterns) {
    if (pattern.test(output)) {
      return false;
    }
  }

  // Check for explicit success
  for (const pattern of successPatterns) {
    if (pattern.test(output)) {
      return true;
    }
  }

  // Default: assume success if exit code is 0
  return true;
}

/**
 * Extract PR number from merge command or current branch PR.
 */
function extractPrNumber(command: string): number | null {
  // Match patterns like: gh pr merge 123, gh pr merge #123
  const match = command.match(/gh\s+pr\s+merge\s+.*?#?(\d+)/);
  if (match) {
    return Number.parseInt(match[1], 10);
  }

  // If no PR number in command, get PR for current branch
  try {
    const result = execSync("gh pr view --json number", {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT * 1000,
    });
    const data = JSON.parse(result);
    return data.number ?? null;
  } catch {
    return null;
  }
}

/**
 * Get the diff of a PR.
 */
function getPrDiff(prNumber: number): string | null {
  try {
    const result = execSync(`gh pr diff ${prNumber}`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
    });
    return result;
  } catch {
    return null;
  }
}

/**
 * Get list of files changed in the PR.
 */
function getChangedFiles(prNumber: number): string[] {
  try {
    const result = execSync(`gh pr view ${prNumber} --json files`, {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT * 1000,
    });
    const data = JSON.parse(result);
    const files = data.files ?? [];
    return files.map((f: { path?: string }) => f.path ?? "").filter(Boolean);
  } catch {
    return [];
  }
}

/**
 * Extract function call patterns from diff.
 *
 * Focuses on added/modified lines (lines starting with +).
 */
export function extractFunctionPatterns(diff: string): Set<string> {
  const patterns = new Set<string>();

  // Pattern for function calls
  const funcPattern =
    /(?<!def )(?<!class )\b([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\s*\(/g;

  for (const line of diff.split("\n")) {
    // Focus on added/modified lines
    if (!line.startsWith("+")) {
      continue;
    }
    // Skip diff headers
    if (line.startsWith("+++")) {
      continue;
    }

    // Extract function calls
    for (const match of line.matchAll(funcPattern)) {
      const fullMatch = match[1];
      if (!fullMatch) continue;
      // Get last part for method calls
      const funcName = fullMatch.split(".").pop()?.toLowerCase() ?? "";

      // Skip common functions
      if (!COMMON_FUNCTIONS.has(funcName)) {
        patterns.add(fullMatch);
      }
    }
  }

  return patterns;
}

/**
 * Search for a pattern in the codebase using ripgrep.
 */
function searchPatternInCodebase(pattern: string, excludeFiles: string[]): SearchResult[] {
  const results: SearchResult[] = [];

  // Build exclude arguments
  const excludeArgs: string[] = [];
  for (const excl of EXCLUDE_PATTERNS) {
    excludeArgs.push("-g", `!${excl}`);
  }
  for (const f of excludeFiles) {
    excludeArgs.push("-g", `!${f}`);
  }

  try {
    // Escape pattern for regex
    const escapedPattern = `${pattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\(`;

    const args = [
      "--line-number",
      "--no-heading",
      "--max-count",
      "10",
      ...excludeArgs,
      escapedPattern,
    ];

    // Use execFileSync to avoid shell interpretation of regex special chars
    const result = execFileSync("rg", args, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      cwd: process.cwd(),
    });

    if (result.trim()) {
      for (const line of result.trim().split("\n").slice(0, MAX_RESULTS)) {
        // Parse rg output: file:line:content
        const parts = line.split(":");
        if (parts.length >= 3) {
          results.push({
            file: parts[0],
            line: parts[1],
            content: parts.slice(2).join(":").trim().slice(0, 80),
          });
        }
      }
    }
  } catch {
    // rg not available or no matches - return empty
  }

  return results;
}

/**
 * Format the informational message.
 */
export function formatInfoMessage(patternResults: Map<string, SearchResult[]>): string {
  const lines = [
    "🔍 **修正漏れの可能性があります**",
    "",
    "PRで変更されたパターンと類似のコードが他のファイルにあります:",
    "",
  ];

  for (const [pattern, results] of patternResults) {
    lines.push(`**\`${pattern}\`**:`);
    for (const r of results.slice(0, 3)) {
      lines.push(`  - \`${r.file}:${r.line}\` - ${r.content}`);
    }
    if (results.length > 3) {
      lines.push(`  - ... 他 ${results.length - 3} 件`);
    }
    lines.push("");
  }

  lines.push("同様の修正が必要ないか確認してください。");

  return lines.join("\n");
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    const hookInput = await parseHookInput();
    sessionId = hookInput.session_id;
    if (!hookInput) {
      console.log(JSON.stringify(result));
      return;
    }

    const toolName = hookInput.tool_name ?? "";
    if (toolName !== "Bash") {
      console.log(JSON.stringify(result));
      return;
    }

    const toolInput = (hookInput.tool_input ?? {}) as Record<string, unknown>;
    const command = (toolInput.command as string) ?? "";

    if (!isPrMergeCommand(command)) {
      console.log(JSON.stringify(result));
      return;
    }

    const toolOutput = (hookInput.tool_output as string) ?? "";
    const rawResult = getToolResult(hookInput);
    const toolResult =
      typeof rawResult === "object" && rawResult ? (rawResult as Record<string, unknown>) : {};
    const exitCode = (toolResult.exit_code as number) ?? 0;

    if (!isMergeSuccess(exitCode, toolOutput)) {
      console.log(JSON.stringify(result));
      return;
    }

    const prNumber = extractPrNumber(command);
    if (!prNumber) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "skipped: could not extract PR number",
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Get PR diff
    const diff = getPrDiff(prNumber);
    if (!diff) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `skipped: could not get diff for PR #${prNumber}`,
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Get changed files to exclude from search
    const changedFiles = getChangedFiles(prNumber);

    // Extract function patterns from diff
    const patterns = extractFunctionPatterns(diff);
    if (patterns.size === 0) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `skipped: no patterns extracted from PR #${prNumber}`,
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Search for each pattern (limit to 5)
    const limitedPatterns = Array.from(patterns).slice(0, 5);
    const patternResults = new Map<string, SearchResult[]>();

    for (const pattern of limitedPatterns) {
      const results = searchPatternInCodebase(pattern, changedFiles);
      if (results.length > 0) {
        patternResults.set(pattern, results);
      }
    }

    if (patternResults.size > 0) {
      result.systemMessage = formatInfoMessage(patternResults);
      let totalMatches = 0;
      for (const results of patternResults.values()) {
        totalMatches += results.length;
      }
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `found similar patterns for PR #${prNumber}`,
        {
          patterns: Array.from(patternResults.keys()),
          total_matches: totalMatches,
        },
        { sessionId },
      );
    } else {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `no similar patterns found for PR #${prNumber}`,
        {
          patterns_checked: limitedPatterns,
        },
        { sessionId },
      );
    }
  } catch (error) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", `error: ${formatError(error)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
