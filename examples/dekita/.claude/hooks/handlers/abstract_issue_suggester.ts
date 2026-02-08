#!/usr/bin/env bun
/**
 * 具体的Issue作成時に抽象的対策Issueを自動提案する
 *
 * PostToolUse:Bash で gh issue create 成功後に実行
 * 具体的Issueと判定した場合、ACTION_REQUIRED出力
 */

import { formatError } from "../lib/format_error";
import { isGhIssueCreateCommand } from "../lib/gh_utils";
import { extractTitleFromCommand } from "../lib/labels";
import { logHookExecution } from "../lib/logging";
import { getToolResult, parseHookInput } from "../lib/session";

const HOOK_NAME = "abstract-issue-suggester";

// 具体的Issueのパターン（prefixまたは修正系キーワードに限定）
const CONCRETE_PATTERNS = [
  /^fix[:(]/i,
  /^修正/,
  /バグ修正/,
  /エラー修正/,
  /型エラー/,
  /テスト失敗/,
];

// 抽象的Issueのパターン（これらにマッチしたら提案不要）
const ABSTRACT_PATTERNS = [
  /^feat.*仕組み/i,
  /^feat.*検出/i,
  /^feat.*防止/i,
  /設計/,
  /リファクタリング/,
  /汎用化/,
];

export function isConcrete(title: string): boolean {
  // 抽象的パターンにマッチしたら具体的ではない
  if (ABSTRACT_PATTERNS.some((p) => p.test(title))) return false;
  // 具体的パターンにマッチしたら具体的
  return CONCRETE_PATTERNS.some((p) => p.test(title));
}

export function extractIssueNumber(output: string): number | null {
  // /issues/123 形式にマッチ（GitHub EnterpriseやURL形式の変化に対応）
  const match = output.match(/\/issues\/(\d+)/);
  return match ? Number.parseInt(match[1], 10) : null;
}

async function main(): Promise<void> {
  const result = { continue: true };
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = (data.tool_input as Record<string, unknown>) || {};
    const command = (toolInput.command as string) || "";

    if (!isGhIssueCreateCommand(command)) {
      console.log(JSON.stringify(result));
      return;
    }

    const toolResult = getToolResult(data) as { stdout?: string; stderr?: string } | null;
    const output = (toolResult?.stdout || "") + (toolResult?.stderr || "");

    if (!output) {
      console.log(JSON.stringify(result));
      return;
    }

    const issueNumber = extractIssueNumber(output);
    if (!issueNumber) {
      console.log(JSON.stringify(result));
      return;
    }

    // コマンド引数からタイトルを取得（ネットワーク呼び出しを回避）
    const title = extractTitleFromCommand(command);
    if (!title || !isConcrete(title)) {
      console.log(JSON.stringify(result));
      return;
    }

    // ACTION_REQUIRED出力
    const message = `[ACTION_REQUIRED: ABSTRACT_SOLUTION]
具体的な修正Issue #${issueNumber} を作成しました。

類似問題を防ぐ抽象的な対策Issueも作成を検討してください:

提案テンプレート:
- タイトル: feat(xxx): [問題カテゴリ]を検出・防止する仕組み
- ラベル: enhancement, P3
- 本文: この問題が再発しないための仕組み（フック/CI/プロセス）

例: Issue #3463「TypeScript型エラー修正」→ Issue #3464「型エラー増加を防ぐCI」`;

    console.error(`[${HOOK_NAME}] ${message}`);
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `ACTION_REQUIRED for issue #${issueNumber}`,
      undefined,
      { sessionId },
    );
    console.log(JSON.stringify(result));
  } catch (error) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    console.log(JSON.stringify(result));
  }
}

if (import.meta.main) {
  main();
}
