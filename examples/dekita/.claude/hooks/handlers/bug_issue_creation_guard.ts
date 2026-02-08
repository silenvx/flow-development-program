#!/usr/bin/env bun
/**
 * PRスコープの問題に対する別Issue作成をブロックする。
 *
 * Why:
 *   PRで導入した問題（バグ、テスト不足、エッジケース等）は同じPRで修正すべき。
 *   別Issueを作成すると問題が残ったままマージされるリスクがある。
 *
 * What:
 *   - gh issue createコマンドを検出
 *   - タイトルからPRスコープのパターン（fix:, test:, バグ等）を検出
 *   - 現在のブランチにオープンPRがある場合はブロック
 *   - PR内での修正を案内
 *
 * Remarks:
 *   - ブロック型フック（PRスコープの問題Issue作成はブロック）
 *   - オープンPRがない場合はスキップ
 *   - PreToolUse:Bashで発火
 *
 * Changelog:
 *   - silenvx/dekita#1130: フック追加
 *   - silenvx/dekita#1175, #1176: このルール違反の事例
 *   - reviewing-code Skill「範囲内/範囲外の判断基準」参照
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { spawnSync } from "node:child_process";
import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { tokenize } from "../lib/github";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "bug-issue-creation-guard";

// Keywords that indicate an Issue that should be handled in the current PR
const PR_SCOPE_ISSUE_PATTERNS = [
  // Bug-related patterns
  /\bfix[:(]/i,
  /\bbug[:(]/i,
  /バグ/,
  /修正/,
  /不具合/,
  // Test-related patterns
  /\btests?[:(]/i,
  /テスト.*追加/,
  /テスト.*不足/,
  /テストカバレッジ/,
  /test\s*coverage/i,
  // Edge case patterns
  /エッジケース/,
  /edge\s*case/i,
];

/**
 * Extract Issue title from gh issue create command.
 * Handles --title value, -t value, --title=value, and -t=value formats.
 */
export function extractIssueTitle(command: string): string | null {
  try {
    const tokens = tokenize(command);
    let i = 0;
    while (i < tokens.length) {
      const token = tokens[i];

      // --title value or -t value
      if ((token === "--title" || token === "-t") && i + 1 < tokens.length) {
        return tokens[i + 1];
      }

      // --title=value
      if (token.startsWith("--title=")) {
        return token.slice("--title=".length);
      }

      // -t=value
      if (token.startsWith("-t=")) {
        return token.slice("-t=".length);
      }

      i++;
    }
  } catch {
    return null;
  }

  return null;
}

/**
 * Check if the title indicates an Issue that should be handled in the PR.
 */
export function isPrScopeIssue(title: string): boolean {
  for (const pattern of PR_SCOPE_ISSUE_PATTERNS) {
    if (pattern.test(title)) {
      return true;
    }
  }
  return false;
}

interface PrInfo {
  number: number;
  title: string;
  headRefName: string;
}

/**
 * Get the current branch's open PR if it exists.
 */
function getCurrentPr(): PrInfo | null {
  try {
    // Get current branch
    const branchResult = spawnSync("git", ["branch", "--show-current"], {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT * 1000,
    });

    if (branchResult.status !== 0) {
      return null;
    }

    const currentBranch = branchResult.stdout.trim();
    if (!currentBranch || currentBranch === "main") {
      return null;
    }

    // Check if there's an open PR for this branch
    const prResult = spawnSync(
      "gh",
      [
        "pr",
        "list",
        "--head",
        currentBranch,
        "--state",
        "open",
        "--json",
        "number,title,headRefName",
        "--limit",
        "1",
      ],
      { encoding: "utf-8", timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (prResult.status !== 0) {
      return null;
    }

    const prs: PrInfo[] = JSON.parse(prResult.stdout);
    if (prs.length > 0) {
      return prs[0];
    }
    return null;
  } catch {
    return null;
  }
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = data.tool_input || {};
    const command = (toolInput as { command?: string }).command || "";

    // Only check gh issue create commands
    if (!/\bgh\s+issue\s+create\b/.test(command)) {
      logHookExecution(HOOK_NAME, "skip", "Not an issue create command", undefined, { sessionId });
      process.exit(0);
    }

    // Extract title
    const title = extractIssueTitle(command);
    if (!title) {
      logHookExecution(HOOK_NAME, "skip", "No title found", undefined, { sessionId });
      process.exit(0);
    }

    // Check if title indicates a PR-scope issue
    if (!isPrScopeIssue(title)) {
      logHookExecution(HOOK_NAME, "skip", "Not a PR-scope issue", undefined, { sessionId });
      process.exit(0);
    }

    // Check if there's an open PR for current branch
    const currentPr = getCurrentPr();
    if (!currentPr) {
      logHookExecution(HOOK_NAME, "skip", "No open PR for current branch", undefined, {
        sessionId,
      });
      process.exit(0);
    }

    // Block creating PR-scope Issue while PR is open
    const prNumber = currentPr.number || "?";
    const prTitle = currentPr.title || "";

    const blockMsg = `🚫 PRスコープの可能性があるIssue作成をブロック

作成しようとしているIssue: "${title}"
現在のPR: #${prNumber} (${prTitle})

【検出方法】
Issueタイトルのパターン（test:, テスト追加, エッジケース等）から検出。

【reviewing-code Skillのルール】
- このPRで導入した問題 → このPRで修正（別Issueにしない）
- 既存コードの問題 → Issue作成を続行してOK

【対応方法】
1. このPRで導入した問題の場合: PRで直接修正してください
2. 既存コードの問題の場合: ユーザーに確認してからIssue作成を続行

背景: Issue #1175, #1176 でこのルール違反が発生。
`;

    const result = makeBlockResult(HOOK_NAME, blockMsg);
    console.log(JSON.stringify(result));
    process.exit(2);
  } catch (e) {
    const result = makeApproveResult(HOOK_NAME, `Error: ${formatError(e)}`);
    console.log(JSON.stringify(result));
    process.exit(0);
  }
}

if (import.meta.main) {
  main();
}
