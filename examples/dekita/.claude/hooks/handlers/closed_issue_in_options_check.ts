#!/usr/bin/env bun
/**
 * AskUserQuestionの選択肢にクローズ済みIssueが含まれていないか確認する。
 *
 * Why:
 *   セッション826ab20cで、`gh issue list --state open`の出力で「動作確認: ... #3872」というタイトルを見て、
 *   #3872自体がオープンだと誤認し、クローズ済みのIssueを選択肢に提案してしまった。
 *   クローズ済みIssueへの作業は競合やスコープ外の問題を引き起こす。
 *
 * What:
 *   - AskUserQuestionツールの呼び出しを検出
 *   - 選択肢のlabel/descriptionからIssue番号（#123形式）を抽出
 *   - gh issue viewで各Issueの状態を確認
 *   - クローズ済みIssueが含まれていたらブロック
 *
 * Remarks:
 *   - ブロック型フック（クローズ済みIssue参照時はブロック）
 *   - PreToolUse:AskUserQuestionで発火
 *   - GitHub API失敗時はfail-open（ブロックしない）
 *   - 最大10件程度のIssueを確認（許容範囲の遅延）
 *
 * Changelog:
 *   - silenvx/dekita#3928: 初期実装
 */

import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult, outputResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import type { HookInput } from "../lib/types";

const HOOK_NAME = "closed-issue-in-options-check";

/** Issue番号を抽出するパターン（#123形式） */
const ISSUE_REF_PATTERN = /#(\d+)/g;

interface Option {
  label?: string;
  description?: string;
}

interface Question {
  question?: string;
  options?: Option[];
}

interface AskUserQuestionInput {
  questions?: Question[];
}

/**
 * テキストからIssue番号を抽出
 * @returns 重複なしのIssue番号配列
 */
export function extractIssueNumbersFromText(text: string): number[] {
  const matches = text.matchAll(ISSUE_REF_PATTERN);
  return [...new Set(Array.from(matches, (m) => Number.parseInt(m[1], 10)))];
}

/**
 * gh issue viewでIssue状態を確認
 * @returns "open", "closed", or null (not found/error)
 */
async function getIssueState(issueNumber: number): Promise<string | null> {
  try {
    const proc = Bun.spawn(
      ["gh", "issue", "view", String(issueNumber), "--json", "state", "--jq", ".state"],
      {
        stdout: "pipe",
        stderr: "pipe",
      },
    );
    const output = await new Response(proc.stdout).text();
    const exitCode = await proc.exited;
    if (exitCode !== 0) {
      return null;
    }
    return output.trim().toLowerCase();
  } catch {
    return null;
  }
}

async function main(): Promise<void> {
  let inputData: HookInput;
  let sessionId: string | undefined;
  try {
    inputData = await parseHookInput();
    sessionId = inputData.session_id;
  } catch {
    // Invalid input - approve silently
    outputResult({});
    return;
  }

  const toolName = inputData.tool_name ?? "";

  // Only check AskUserQuestion
  if (toolName !== "AskUserQuestion") {
    outputResult({});
    return;
  }

  const toolInput = (inputData.tool_input as AskUserQuestionInput) ?? {};
  const questions = toolInput.questions ?? [];

  if (questions.length === 0) {
    outputResult({});
    return;
  }

  // 選択肢とquestionフィールドからIssue番号を抽出
  const issueNumbers: number[] = [];
  for (const q of questions) {
    // questionフィールドからも抽出（greptileレビュー指摘対応）
    issueNumbers.push(...extractIssueNumbersFromText(q.question ?? ""));
    for (const opt of q.options ?? []) {
      issueNumbers.push(...extractIssueNumbersFromText(opt.label ?? ""));
      issueNumbers.push(...extractIssueNumbersFromText(opt.description ?? ""));
    }
  }

  if (issueNumbers.length === 0) {
    outputResult({});
    return;
  }

  // 重複除去
  const uniqueIssues = [...new Set(issueNumbers)];

  // 各Issueの状態を並列で確認（遅延を最小化）
  const checkPromises = uniqueIssues.map(async (num) => {
    const state = await getIssueState(num);
    // state === null: API失敗（fail-open: ブロックしない）
    // state === "open": オープン（OK）
    // state !== "open": クローズ済み（ブロック）
    return state && state !== "open" ? num : null;
  });

  const results = await Promise.all(checkPromises);
  const closedIssues = results.filter((n): n is number => n !== null);

  if (closedIssues.length > 0) {
    const result = makeBlockResult(
      HOOK_NAME,
      `🚫 選択肢にクローズ済みのIssueが含まれています: ${closedIssues.map((n) => `#${n}`).join(", ")}

クローズ済みIssueへの作業は以下の問題を引き起こします:
- 既に対応済みのタスクへの重複作業
- スコープ外の変更によるリグレッション
- 別セッションの完了済み作業との競合

**対応方法**:
1. 選択肢からクローズ済みIssueを除外してください
2. オープンなIssueのみを提案してください
3. 新規の問題であれば、新しいIssueを作成してください

💡 \`gh issue view <番号>\` でIssue状態を確認できます`,
    );
    outputResult(result);
    return;
  }

  await logHookExecution(
    HOOK_NAME,
    "approve",
    `Checked ${uniqueIssues.length} issues, all open`,
    undefined,
    { sessionId },
  );
  const result = makeApproveResult(HOOK_NAME);
  outputResult(result);
}

if (import.meta.main) {
  main();
}
