#!/usr/bin/env bun
/**
 * gh issue create時に--labelオプションの指定を強制する。
 *
 * Why:
 *   ラベルなしのIssueは分類・検索・優先度管理が困難になる。
 *   Issue作成時にラベルを強制することで、Issue管理の質を維持する。
 *
 * What:
 *   - gh issue createコマンドを検出
 *   - --labelオプションの有無をチェック
 *   - ラベルがない場合、タイトル/ボディから適切なラベルを自動提案
 *   - ブロックし、推奨コマンドを表示
 *
 * Remarks:
 *   - ブロック型フック
 *   - issue-priority-label-checkは優先度ラベル専用、本フックはラベル有無の確認
 *   - Python版: issue_label_check.py
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2451: タイトル/ボディからラベル自動提案機能を追加
 *   - silenvx/dekita#2917: TypeScript版初期実装
 *   - silenvx/dekita#3079: option_parser完全移行、gh_utilsへ共通関数抽出
 */

import { formatError } from "../lib/format_error";
import { isGhIssueCreateCommand } from "../lib/gh_utils";
import {
  extractBodyFromCommand,
  extractTitleFromCommand,
  suggestLabelsFromText,
} from "../lib/labels";
import { logHookExecution } from "../lib/logging";
import { type OptionDef, hasOption, parseOptions, tokenize } from "../lib/option_parser";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "issue-label-check";

/** --label オプション定義（複数指定可能） */
const LABEL_OPTION_DEFS: OptionDef[] = [
  { long: "label", short: "l", hasValue: true, multiple: true },
];

/**
 * Check if command has --label option using option_parser.
 */
export function hasLabelOption(command: string): boolean {
  const tokens = tokenize(command);
  if (tokens.length === 0) {
    // Empty or whitespace-only command: use simple split as conservative fallback
    // Only detect = forms to avoid false positives with incomplete commands like "gh issue create -l"
    const parts = command.split(/\s+/);
    return parts.some((p) => p.startsWith("--label=") || p.startsWith("-l="));
  }
  const options = parseOptions(tokens, LABEL_OPTION_DEFS);
  return hasOption(options, "label");
}

/**
 * Escape a string for shell use (simple quoting).
 */
export function shellQuote(str: string): string {
  // If string contains single quotes, use double quotes with escaping
  if (str.includes("'")) {
    return `"${str.replace(/"/g, '\\"')}"`;
  }
  // Otherwise use single quotes
  return `'${str}'`;
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
      // Not a target command, exit silently
      process.exit(0);
    }

    // Check if --label option is specified
    if (hasLabelOption(command)) {
      // Has label, approve
      await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      process.exit(0);
    }

    // No label specified, suggest labels from title/body
    const title = extractTitleFromCommand(command);
    const body = extractBodyFromCommand(command);
    const suggestions = suggestLabelsFromText(title || "", body);

    const reasonLines: string[] = ["Issue作成時に --label オプションが指定されていません。", ""];

    if (suggestions.length > 0) {
      const suggestedLabels = suggestions.map((s) => s.label);
      reasonLines.push("**📝 内容から検出したラベル候補:**");
      reasonLines.push("");
      for (const { label, description } of suggestions) {
        reasonLines.push(`- \`${label}\`: ${description}`);
      }
      reasonLines.push("");
      reasonLines.push("**推奨コマンド（優先度ラベルを追加してください）:**");
      reasonLines.push("");
      reasonLines.push("```bash");
      // Generate recommended command with P2 priority
      const allLabels = [...suggestedLabels, "P2"].join(",");
      if (title) {
        const escapedTitle = shellQuote(title);
        reasonLines.push(
          `gh issue create --title ${escapedTitle} --body "..." --label "${allLabels}"`,
        );
      } else {
        reasonLines.push(`gh issue create --title "..." --body "..." --label "${allLabels}"`);
      }
      reasonLines.push("```");
      reasonLines.push("");
      reasonLines.push("**優先度の選択:**");
    } else {
      // No suggestions, show available labels
      reasonLines.push("利用可能なラベルを確認してください:");
      reasonLines.push("");
      reasonLines.push("```");
      reasonLines.push("gh label list");
      reasonLines.push("```");
      reasonLines.push("");
      reasonLines.push("**主なラベル:**");
      reasonLines.push("");
      reasonLines.push("- `bug`: バグ報告");
      reasonLines.push("- `enhancement`: 新機能");
      reasonLines.push("- `documentation`: ドキュメント改善");
      reasonLines.push("");
      reasonLines.push("**優先度（必須）:**");
    }

    reasonLines.push("");
    reasonLines.push("| 優先度 | 説明 |");
    reasonLines.push("|--------|------|");
    reasonLines.push("| P0 | Critical - 即座に対応 |");
    reasonLines.push("| P1 | High - 早急に対応 |");
    reasonLines.push("| P2 | Medium - 通常の優先度（迷ったらこれ） |");
    reasonLines.push("| P3 | Low - 時間があれば対応 |");

    const reason = reasonLines.join("\n");
    const result = makeBlockResult(HOOK_NAME, reason);
    await logHookExecution(HOOK_NAME, "block", "label option missing", undefined, { sessionId });
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
