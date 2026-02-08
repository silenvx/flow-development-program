#!/usr/bin/env bun
/**
 * Pythonフック内の問題のあるsubprocess使用パターンを検出。
 *
 * Why:
 *   shell=Trueはセキュリティリスク、リスト引数でのシェル演算子は動作しない。
 *   コミット前にこれらの問題を検出して修正を強制する。
 *
 * What:
 *   - git commit時（PreToolUse:Bash）に発火
 *   - .claude/hooks/配下のステージ済みPythonファイルを解析
 *   - shell=True使用、リスト引数内のシェル演算子を検出
 *   - 問題がある場合はコミットをブロック
 *
 * Remarks:
 *   - ブロック型フック（問題検出時はコミットをブロック）
 *   - 正規表現ベースでsubprocess.run/call/Popenを検出
 *   - --jq引数やgit --format内の|はスキップ（Issue #1226）
 *
 * Changelog:
 *   - silenvx/dekita#1110: フック追加
 *   - silenvx/dekita#1226: git --format内の|をスキップ
 *   - silenvx/dekita#3161: TypeScript移行
 */

import { existsSync, readFileSync } from "node:fs";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";
import { splitCommandChain, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "subprocess-lint-check";

// Shell operators that don't work with shell=False (list arguments)
// Ordered from most specific to least specific to avoid duplicate detection
const SHELL_OPERATORS = ["2>&1", "2>", ">&", ">>", "&&", "||", ">", "<", "|"];

interface Issue {
  file: string;
  line: number;
  type: string;
  message: string;
}

/**
 * Check if command contains git commit.
 */
export function isGitCommitCommand(command: string): boolean {
  const stripped = stripQuotedStrings(command);
  const subcommands = splitCommandChain(stripped);
  for (const subcmd of subcommands) {
    // Match git commit with optional global flags like -c, -C, --exec-path, etc.
    // Examples: "git commit", "git -c user.name=Bot commit", "git -C /path commit"
    // Issue #3263: Make (=\S+|\s+\S+) optional to handle argument-less flags like -v or --verbose
    // Examples: "git --no-pager commit", "git -v commit"
    if (/^git(\s+(-[a-zA-Z]|-{2}[a-z-]+)(=\S+|\s+\S+)?)*\s+commit(\s|$)/.test(subcmd)) {
      return true;
    }
  }
  return false;
}

/**
 * Get list of staged Python files in .claude/hooks/.
 */
async function getStagedPythonFiles(): Promise<string[]> {
  if (process.env._TEST_NO_STAGED_FILES === "1") {
    return [];
  }

  try {
    const result = await asyncSpawn(
      "git",
      ["diff", "--cached", "--name-only", "--diff-filter=ACM"],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (!result.success) {
      return [];
    }

    const files = result.stdout.trim() ? result.stdout.trim().split("\n") : [];
    // Only check hooks directory Python files
    return files.filter((f) => f.endsWith(".py") && f.startsWith(".claude/hooks/"));
  } catch {
    return [];
  }
}

/**
 * Check for shell=True in a line.
 */
export function checkShellTrue(line: string): boolean {
  // Match shell=True as a keyword argument
  return /shell\s*=\s*True/.test(line);
}

/**
 * Check for shell operators in list arguments.
 * Returns list of found operators.
 */
export function checkListForShellOperators(line: string): string[] {
  // Skip if line contains --jq (jq uses | as its own pipe operator)
  if (line.includes("--jq")) {
    return [];
  }

  // Skip if line contains --format= or --pretty= (git format strings use |)
  if (/--format[=\s]/.test(line) || /--pretty[=\s]/.test(line)) {
    return [];
  }

  // Look for list literals with shell operators
  // Match patterns like ["cmd", "arg", "2>&1", "other"]
  // Use global flag to find all lists on the line (handles nested lists and multiple occurrences)
  const listMatches = line.match(/\[(.*?)\]/g);
  if (!listMatches) {
    return [];
  }

  const foundOps: string[] = [];

  for (const listContent of listMatches) {
    for (const op of SHELL_OPERATORS) {
      // Check if operator appears in a string within the list
      // Match patterns like "2>&1" or '2>&1' within the list
      const opPattern = new RegExp(`["']${op.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}["']`);
      if (opPattern.test(listContent)) {
        // Check if already covered by a more specific operator
        const isCovered = foundOps.some((foundOp) => foundOp.includes(op) && foundOp !== op);
        if (!isCovered) {
          foundOps.push(op);
        }
      }
    }
  }

  return [...new Set(foundOps)];
}

/**
 * Check if a line is a subprocess call.
 */
export function isSubprocessCall(line: string): boolean {
  // Check for subprocess.run, subprocess.call, subprocess.Popen
  // Also check bare names when imported directly
  return (
    /subprocess\.(run|call|Popen)\s*\(/.test(line) || /\b(run|call|Popen)\s*\(\s*\[/.test(line)
  );
}

/**
 * Analyze a Python file for subprocess issues.
 */
function analyzeFile(filepath: string): Issue[] {
  const issues: Issue[] = [];

  try {
    const content = readFileSync(filepath, "utf-8");

    // Issue #3161: Removed multiline pattern that was too aggressive
    // The pattern /subprocess\.(?:run|call|Popen)\s*\([\s\S]*?shell\s*=\s*True/g
    // could match across function definitions, causing false positives.
    // Now relying on single-line checks only for safety.
    const lines = content.split("\n");

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const lineNo = i + 1;

      // Skip comments
      const trimmed = line.trim();
      if (trimmed.startsWith("#")) {
        continue;
      }

      // Check if this is a subprocess call
      if (!isSubprocessCall(line)) {
        continue;
      }

      // Check for shell=True (single line only to avoid false positives)
      // Issue #3263: Strip quoted strings first to find real comments
      // This prevents treating # inside quoted strings as comments
      // e.g., subprocess.run(["echo", "hello#world"], shell=True) - # is not a comment
      // Note: Use strippedLine for both indexOf and substring because stripQuotedStrings
      // changes the string length (replaces quoted strings with empty strings)
      const strippedLine = stripQuotedStrings(line);
      const commentIndex = strippedLine.indexOf("#");
      const codePart = commentIndex >= 0 ? strippedLine.substring(0, commentIndex) : strippedLine;
      if (checkShellTrue(codePart)) {
        issues.push({
          file: filepath,
          line: lineNo,
          type: "shell_true",
          message: `subprocess with shell=True detected at line ${lineNo}. Use list arguments with shell=False instead for security.`,
        });
      }

      // Check for shell operators in list arguments
      const shellOps = checkListForShellOperators(line);
      if (shellOps.length > 0) {
        issues.push({
          file: filepath,
          line: lineNo,
          type: "shell_operator_in_list",
          message: `Shell operator(s) [${shellOps.join(", ")}] in list argument at line ${lineNo}. Shell operators don't work with list arguments (shell=False). Use capture_output=True and stderr=subprocess.STDOUT for stderr handling.`,
        });
      }
    }
  } catch (e) {
    if (e instanceof SyntaxError) {
      issues.push({
        file: filepath,
        line: 0,
        type: "syntax_error",
        message: `Syntax error: ${e.message}`,
      });
    } else {
      issues.push({
        file: filepath,
        line: 0,
        type: "error",
        message: `Could not analyze file: ${formatError(e)}`,
      });
    }
  }

  return issues;
}

async function main(): Promise<void> {
  let result: Record<string, unknown> = {};
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = data.tool_input ?? {};
    const command = typeof toolInput.command === "string" ? toolInput.command : "";

    // Only check git commit commands
    if (!isGitCommitCommand(command)) {
      console.log(JSON.stringify({}));
      return;
    }

    // Get staged Python files in hooks directory
    const pyFiles = await getStagedPythonFiles();
    if (pyFiles.length === 0) {
      console.log(JSON.stringify({}));
      return;
    }

    // Analyze each file
    const allIssues: Issue[] = [];
    for (const filepath of pyFiles) {
      if (existsSync(filepath)) {
        const issues = analyzeFile(filepath);
        allIssues.push(...issues);
      }
    }

    if (allIssues.length > 0) {
      // Format error message
      const errorLines = ["subprocessの使用に問題があります:\n"];
      for (const issue of allIssues) {
        errorLines.push(`  - ${issue.file}:${issue.line}: ${issue.message}`);
      }

      errorLines.push("\n修正方法:");
      errorLines.push("  - shell=True は使用しない（セキュリティリスク）");
      errorLines.push("  - コマンドはリスト形式で指定: ['git', 'status']");
      errorLines.push(
        "  - stderr処理は capture_output=True または stderr=subprocess.STDOUT を使用",
      );
      errorLines.push("  - パイプは複数のsubprocess.runで実現するか、shell=Trueを避ける設計に変更");

      const reason = errorLines.join("\n");
      result = makeBlockResult(HOOK_NAME, reason);
      await logHookExecution(HOOK_NAME, "block", reason, undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // All checks passed
    result = {
      systemMessage: `✅ ${HOOK_NAME}: ${pyFiles.length}個のファイルをチェックOK`,
    };
  } catch (e) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(e)}`);
    result = { reason: `Hook error: ${formatError(e)}` };
  }

  await logHookExecution(
    HOOK_NAME,
    (result.decision as string) ?? "approve",
    result.reason as string | undefined,
    undefined,
    { sessionId },
  );
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({}));
  });
}
