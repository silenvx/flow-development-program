#!/usr/bin/env bun
/**
 * gh issue create時に優先度ラベル（P0-P3）の指定を強制する。
 *
 * Why:
 *   GitHubのIssueテンプレートでdropdownの優先度を必須にしても、
 *   選択した値はラベルとして自動付与されない。gh CLI経由での
 *   作成時はテンプレート自体が適用されない。優先度ラベルを
 *   強制することでIssueの優先順位管理を徹底する。
 *
 * What:
 *   - gh issue createコマンドを検出
 *   - --labelオプションから優先度ラベル（P0-P3）を確認
 *   - 優先度ラベルがない場合はブロック
 *
 * Remarks:
 *   - issue-label-checkはラベル有無のみ確認、これは優先度ラベル専用
 *   - P0: Critical、P1: High、P2: Medium、P3: Low
 *   - Python版: issue_priority_label_check.py
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2917: TypeScript版初期実装
 *   - silenvx/dekita#3079: option_parser完全移行、gh_utilsへ共通関数抽出
 */

import { formatError } from "../lib/format_error";
import { isGhIssueCreateCommand } from "../lib/gh_utils";
import { hasPriorityLabel } from "../lib/labels";
import { logHookExecution } from "../lib/logging";
import { type OptionDef, getOptionValues, parseOptions, tokenize } from "../lib/option_parser";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "issue-priority-label-check";

/** --label オプション定義（複数指定可能） */
const LABEL_OPTION_DEFS: OptionDef[] = [
  { long: "label", short: "l", hasValue: true, multiple: true },
];

/**
 * Extract labels from command using option_parser.
 * Returns an array of label values (may contain comma-separated labels).
 */
export function extractLabels(command: string): string[] {
  const tokens = tokenize(command);
  if (tokens.length === 0) {
    // Empty or whitespace-only command
    return [];
  }
  const options = parseOptions(tokens, LABEL_OPTION_DEFS);
  return getOptionValues(options, "label");
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = (data.tool_input as Record<string, unknown>) || {};
    const command = (toolInput.command as string) || "";

    // Only check gh issue create commands
    if (!isGhIssueCreateCommand(command)) {
      process.exit(0);
    }

    // Extract labels and check for priority
    const labels = extractLabels(command);
    if (hasPriorityLabel(labels)) {
      await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      process.exit(0);
    }

    // No priority label: block
    const reasonLines = [
      "優先度ラベル（P0-P3）が指定されていません。",
      "",
      "Issueには必ず優先度を指定してください:",
      "",
      "| 優先度 | 説明 |",
      "|--------|------|",
      "| P0 | Critical - ビジネス上必須、即座に対応 |",
      "| P1 | High - 早急に対応が必要 |",
      "| P2 | Medium - 通常の優先度 |",
      "| P3 | Low - 時間があれば対応 |",
      "",
      "例:",
      "```bash",
      'gh issue create --title "..." --body "..." --label "enhancement,P2"',
      "```",
      "",
      "迷ったら P2 を選択してください。",
    ];
    const reason = reasonLines.join("\n");

    const result = makeBlockResult(HOOK_NAME, reason);
    await logHookExecution(HOOK_NAME, "block", "priority label missing", undefined, { sessionId });
    console.log(JSON.stringify(result));
    process.exit(0);
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    const result = makeApproveResult(HOOK_NAME, `Hook error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(error)}`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify(result));
    process.exit(0);
  }
}

if (import.meta.main) {
  main();
}
