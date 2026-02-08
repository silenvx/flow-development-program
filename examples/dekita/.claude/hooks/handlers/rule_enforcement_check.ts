#!/usr/bin/env bun
/**
 * AGENTS.mdにルール追加時、対応する強制機構の存在を検証する。
 *
 * Why:
 *   「禁止」「必須」等のルールが追加されても、対応するhookやCIがなければ
 *   ルールは形骸化する。「仕組み化 = ドキュメント + 強制機構」原則の自動検知。
 *
 * What:
 *   - git commitでAGENTS.mdが含まれる場合に発火
 *   - 追加行から「禁止」「必須」「ブロック」「強制」キーワードを検出
 *   - 開発者に強制機構（hook/CI）の実装状況を確認する警告を表示
 *
 * Remarks:
 *   - PostToolUse:Bash（git commit時）で発火
 *   - 警告型フック（exit 0 + systemMessage）
 *   - ヒューリスティック検知のため、誤検知・見落としあり
 *
 * Changelog:
 *   - silenvx/dekita#3976: 初期実装
 */

import { ENFORCEMENT_KEYWORDS } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { getExitCode, getToolResult } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { approveAndExit } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { splitCommandChain, stripQuotedStrings } from "../lib/strings";

export { ENFORCEMENT_KEYWORDS };

const HOOK_NAME = "rule-enforcement-check";

/**
 * Check if command is a git commit.
 */
function isGitCommitCommand(command: string): boolean {
  const stripped = stripQuotedStrings(command);
  const subcommands = splitCommandChain(stripped);
  return subcommands.some((subcmd) => /^git\s+commit(\s|$)/.test(subcmd));
}

/**
 * Get added lines from AGENTS.md in the last commit (HEAD vs HEAD~1).
 * Since this hook fires after git commit (PostToolUse), changes are
 * already committed and no longer in the staging area.
 */
async function getAgentsMdAddedLines(): Promise<string[]> {
  const testLines = process.env._TEST_AGENTS_ADDED_LINES;
  if (testLines !== undefined) {
    return testLines ? testLines.split("\n") : [];
  }

  try {
    const proc = Bun.spawn(
      ["git", "diff", "HEAD~1", "HEAD", "--diff-filter=ACM", "-U0", "--", "AGENTS.md"],
      {
        stdout: "pipe",
        stderr: "pipe",
      },
    );
    const output = await new Response(proc.stdout).text();
    const exitCode = await proc.exited;
    if (exitCode !== 0) return [];

    return output
      .split("\n")
      .filter((line) => line.startsWith("+") && !line.startsWith("+++"))
      .map((line) => line.slice(1));
  } catch {
    return [];
  }
}

/**
 * Check if AGENTS.md was modified in the last commit (HEAD).
 * Since this hook fires after git commit (PostToolUse), we check
 * the committed diff rather than the staging area.
 */
async function isAgentsMdInLastCommit(): Promise<boolean> {
  const testVal = process.env._TEST_AGENTS_STAGED;
  if (testVal !== undefined) return testVal === "true";

  try {
    const proc = Bun.spawn(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], {
      stdout: "pipe",
      stderr: "pipe",
    });
    const output = await new Response(proc.stdout).text();
    const exitCode = await proc.exited;
    if (exitCode !== 0) return false;
    return output.split("\n").some((f) => f.trim() === "AGENTS.md");
  } catch {
    return false;
  }
}

/**
 * Extract lines containing enforcement keywords from added lines.
 */
export function findEnforcementLines(lines: string[]): string[] {
  const pattern = new RegExp(ENFORCEMENT_KEYWORDS.join("|"), "i");
  return lines.filter((line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || /^[-|:\s]+$/.test(trimmed)) return false;
    return pattern.test(trimmed);
  });
}

async function main(): Promise<void> {
  let sessionId: string | undefined;

  try {
    const input = await parseHookInput();
    const ctx = createHookContext(input);
    sessionId = ctx.sessionId;
    const toolInput = input.tool_input as Record<string, unknown> | undefined;
    const command = (toolInput?.command as string) ?? "";

    if (!isGitCommitCommand(command)) {
      approveAndExit(HOOK_NAME);
    }

    // Check if git commit actually succeeded via tool_result exit code
    const toolResult = getToolResult(input);
    const exitCode = getExitCode(toolResult);
    if (exitCode !== 0) {
      approveAndExit(HOOK_NAME);
    }

    if (!(await isAgentsMdInLastCommit())) {
      approveAndExit(HOOK_NAME);
    }

    const addedLines = await getAgentsMdAddedLines();
    const enforcementLines = findEnforcementLines(addedLines);

    if (enforcementLines.length === 0) {
      approveAndExit(HOOK_NAME);
    }

    const linesSummary = enforcementLines
      .slice(0, 5)
      .map((l) => `  - ${l.trim().substring(0, 80)}`)
      .join("\n");
    const moreCount =
      enforcementLines.length > 5 ? `\n  ... and ${enforcementLines.length - 5} more` : "";

    const systemMessage = `⚠️ rule-enforcement-check: AGENTS.mdに強制ルール（禁止/必須等）が追加されています。

【確認】対応する強制機構（hook/CIチェック）は存在しますか？

検出されたルール行:
${linesSummary}${moreCount}

💡 「仕組み化 = ドキュメント + 強制機構」原則:
  - hook: .claude/hooks/handlers/ にフックを作成
  - CI: .claude/scripts/ または .github/workflows/ にチェックを追加
  - 強制機構が不要な場合はその理由をIssueに記録`;

    await logHookExecution(
      HOOK_NAME,
      "approve",
      undefined,
      {
        enforcement_lines_count: enforcementLines.length,
      },
      { sessionId },
    );

    console.log(JSON.stringify({ systemMessage }));
    process.exit(0);
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(error)}`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify({ reason: `Hook error: ${formatError(error)}` }));
    process.exit(0);
  }
}

if (import.meta.main) {
  main();
}
