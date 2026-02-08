#!/usr/bin/env bun
/**
 * 仕組み化Issueクローズ時にフック/ツール実装を検証。
 *
 * Why:
 *   「仕組み化」を謳うIssueがドキュメント追加のみでクローズされると、
 *   強制機構がなく問題が再発する。クローズ前に実装を確認する。
 *
 * What:
 *   - gh issue close時（PreToolUse:Bash）に発火
 *   - Issue内容から「仕組み化」系キーワードを検出
 *   - 関連PRで強制機構ファイル（hooks/workflows/scripts）の変更を確認
 *   - 強制機構がない場合はクローズをブロック
 *
 * Remarks:
 *   - ブロック型フック（強制機構なしの場合はブロック）
 *   - systematization-checkはセッション終了時、本フックはIssueクローズ時
 *   - SKIP_SYSTEMATIZATION_CHECK=1でスキップ可能
 *   - Python版: systematization_issue_close_check.py
 *
 * Changelog:
 *   - silenvx/dekita#1909: フック追加
 *   - silenvx/dekita#2607: HookContextパターン移行
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { extractInlineSkipEnv, isSkipEnvEnabled, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "systematization-issue-close-check";
const SKIP_ENV_NAME = "SKIP_SYSTEMATIZATION_CHECK";

// 「仕組み化」系キーワード
const SYSTEMATIZATION_KEYWORDS = [
  /仕組み化/i,
  /フック(?:を)?(?:作成|追加|実装)/i,
  /hook(?:を)?(?:作成|追加|実装)/i,
  /CI(?:を)?(?:追加|実装)/i,
  /自動(?:化|チェック)/i,
  /強制(?:機構|チェック)/i,
  /再発防止(?:策)?(?:の)?(?:仕組み|フック)/i,
];

// 強制機構ファイルのパターン
const ENFORCEMENT_FILE_PATTERNS = [
  /\.claude\/hooks\/.*\.py$/,
  /\.claude\/hooks\/.*\.ts$/,
  /\.github\/workflows\/.*\.ya?ml$/,
  /\.claude\/scripts\/.*\.(?:py|sh|ts)$/,
];

/**
 * Extract issue number from gh issue close command.
 */
export function extractIssueNumber(command: string): string | null {
  const cmd = stripQuotedStrings(command);

  if (!/gh\s+issue\s+close\b/.test(cmd)) {
    return null;
  }

  const match = cmd.match(/gh\s+issue\s+close\s+(.+)/);
  if (!match) {
    return null;
  }

  const args = match[1];

  for (const part of args.split(/\s+/)) {
    if (part.startsWith("-")) {
      continue;
    }
    const numMatch = part.match(/^#?(\d+)$/);
    if (numMatch) {
      return numMatch[1];
    }
  }

  return null;
}

/**
 * Get issue title and body from GitHub.
 */
function getIssueContent(issueNumber: string): { title: string; body: string } | null {
  try {
    const result = execSync(`gh issue view ${issueNumber} --json title,body`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    const data = JSON.parse(result);
    return {
      title: data.title || "",
      body: data.body || "",
    };
  } catch {
    return null;
  }
}

/**
 * Check if title or body contains systematization keywords.
 */
export function hasSystematizationKeyword(title: string, body: string): boolean {
  const text = `${title}\n${body}`;
  for (const pattern of SYSTEMATIZATION_KEYWORDS) {
    if (pattern.test(text)) {
      return true;
    }
  }
  return false;
}

/**
 * Get list of files changed in a PR.
 */
function getPrFiles(prNumber: number | string): string[] {
  const files: string[] = [];
  try {
    const result = execSync(`gh pr view ${prNumber} --json files --jq '.files[].path'`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    for (const line of result.trim().split("\n")) {
      if (line) {
        files.push(line);
      }
    }
  } catch {
    // gh CLI failure - return empty list (best-effort)
  }
  return files;
}

/**
 * Search PRs by issue number (open/merged both).
 */
function searchPrsByIssue(issueNumber: string): number[] {
  const prNumbers: number[] = [];
  try {
    const result = execSync(
      `gh pr list --state all --search ${issueNumber} --json number --jq '.[] | .number'`,
      {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );

    for (const line of result.trim().split("\n")) {
      if (line && /^\d+$/.test(line)) {
        prNumbers.push(Number.parseInt(line, 10));
      }
    }
  } catch {
    // gh CLI failure - return empty list (best-effort)
  }
  return prNumbers;
}

/**
 * Get files changed in PRs linked to the issue.
 */
function getLinkedPrFiles(issueNumber: string): string[] {
  const files: string[] = [];
  let prNumbers: number[] = [];

  try {
    // 1. Get officially linked PRs
    const result = execSync(`gh issue view ${issueNumber} --json linkedPullRequests`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    const data = JSON.parse(result);
    const prs = data.linkedPullRequests || [];
    for (const pr of prs) {
      if (pr.number) {
        prNumbers.push(pr.number);
      }
    }
  } catch {
    // Continue to fallback search
  }

  // 2. If no linked PRs found, search by issue number
  if (prNumbers.length === 0) {
    prNumbers = searchPrsByIssue(issueNumber);
  }

  // 3. Get files from each PR
  for (const prNumber of prNumbers) {
    const prFiles = getPrFiles(prNumber);
    files.push(...prFiles);
  }

  return files;
}

/**
 * Find enforcement files from the list of changed files.
 */
export function hasEnforcementFile(files: string[]): string[] {
  const enforcementFiles: string[] = [];
  for (const filePath of files) {
    for (const pattern of ENFORCEMENT_FILE_PATTERNS) {
      if (pattern.test(filePath)) {
        enforcementFiles.push(filePath);
        break;
      }
    }
  }
  return enforcementFiles;
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolName = (data.tool_name as string) || "";

    // Only check Bash commands
    if (toolName !== "Bash") {
      await logHookExecution(HOOK_NAME, "approve", `not Bash: ${toolName}`, undefined, {
        sessionId,
      });
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    const toolInput = (data.tool_input as Record<string, unknown>) || {};
    const command = (toolInput.command as string) || "";

    // Check for skip environment variable
    if (isSkipEnvEnabled(process.env[SKIP_ENV_NAME])) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `${SKIP_ENV_NAME} でスキップ（環境変数）`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    const inlineValue = extractInlineSkipEnv(command, SKIP_ENV_NAME);
    if (isSkipEnvEnabled(inlineValue)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `${SKIP_ENV_NAME} でスキップ（インライン）`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // Check if this is a gh issue close command
    const issueNumber = extractIssueNumber(command);
    if (!issueNumber) {
      await logHookExecution(HOOK_NAME, "approve", "no issue number found", undefined, {
        sessionId,
      });
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // Get issue content
    const content = getIssueContent(issueNumber);
    if (!content) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Issue #${issueNumber} の内容取得失敗`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    const { title, body } = content;

    // Document update Issues are excluded (docs: or docs(...) prefix)
    if (/^docs[:\(]/i.test(title)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Issue #${issueNumber} はドキュメント更新Issue（対象外）`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // Check for systematization keywords
    if (!hasSystematizationKeyword(title, body)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Issue #${issueNumber} は仕組み化Issueではない`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // Get files changed in linked PRs
    const prFiles = getLinkedPrFiles(issueNumber);

    // Check for enforcement files
    const enforcementFiles = hasEnforcementFile(prFiles);

    if (enforcementFiles.length > 0) {
      let filesList = enforcementFiles.slice(0, 3).join(", ");
      if (enforcementFiles.length > 3) {
        filesList += ` 他${enforcementFiles.length - 3}件`;
      }
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Issue #${issueNumber} に強制機構ファイルあり: ${filesList}`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // No enforcement files - block
    const reasonLines = [
      `Issue #${issueNumber} は「仕組み化」を謳っていますが、`,
      "強制機構（フック/CI/ツール）の実装が確認できません。",
      "",
      "**仕組み化の定義**:",
      "  - ドキュメント追加だけでは不十分",
      "  - 違反を**ブロック**するフック/CI/ツールが必要",
      "",
      "**確認された変更ファイル**:",
    ];

    if (prFiles.length > 0) {
      for (const f of prFiles.slice(0, 5)) {
        reasonLines.push(`  - ${f}`);
      }
      if (prFiles.length > 5) {
        reasonLines.push(`  ... 他 ${prFiles.length - 5} 件`);
      }
    } else {
      reasonLines.push("  (関連PRが見つからないか、変更ファイルがありません)");
    }

    reasonLines.push(
      "",
      "**対応方法**:",
      "  1. フック/CI/スクリプトを実装してからクローズ",
      "  2. 強制機構が不要と判断した場合はコメントで理由を説明",
      "",
      "**スキップ方法（確認済みの場合）**:",
      `  SKIP_SYSTEMATIZATION_CHECK=1 gh issue close ${issueNumber}`,
    );

    const blockMessage = reasonLines.join("\n");

    await logHookExecution(
      HOOK_NAME,
      "block",
      `Issue #${issueNumber} に強制機構ファイルなし`,
      undefined,
      { sessionId },
    );

    const result = makeBlockResult(HOOK_NAME, blockMessage);
    console.log(JSON.stringify(result));
    process.exit(2);
  } catch (error) {
    // Don't block on errors - approve silently
    await logHookExecution(HOOK_NAME, "error", `フックエラー: ${formatError(error)}`, undefined, {
      sessionId,
    });
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
  }
}

if (import.meta.main) {
  main();
}
