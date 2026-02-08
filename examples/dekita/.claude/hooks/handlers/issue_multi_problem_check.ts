#!/usr/bin/env bun
/**
 * Issue作成時に複数問題を1Issueにまとめていないかチェックする。
 *
 * Why:
 *   1つのIssueに複数の問題を含めると、議論が分散し解決が遅れる。
 *   1Issue1問題の原則を強制することで、追跡性と解決速度を向上させる。
 *
 * What:
 *   - gh issue createコマンドからタイトルを抽出
 *   - 複数問題パターン（「AとBの実装」等）を検出
 *   - 検出時はブロックして分離を促す
 *
 * Remarks:
 *   - ブロック型フック（複数問題検出時はブロック）
 *   - PreToolUse:Bashで発火（gh issue createコマンド）
 *   - issue-scope-check.pyはIssue編集時のみ対象（責務分離）
 *   - 除外パターンで「検出と警告」等の関連動作は許可
 *
 * Changelog:
 *   - silenvx/dekita#1981: フック追加
 *   - silenvx/dekita#1991: 重複警告防止
 *   - silenvx/dekita#2240: ブロック型に変更
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { formatError } from "../lib/format_error";
import { tokenize } from "../lib/github";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "issue-multi-problem-check";

// 複数問題を示すパターン（日本語）
// CUSTOMIZE: 言語に合わせてパターンを調整
const MULTI_PROBLEM_PATTERNS_JA: [RegExp, string][] = [
  // 「AとBの改善」「AとBを実装」のようなパターン
  // ただし「検出と警告」のような関連動作は除外
  [/(.+)と(.+)の(実装|改善|修正|追加|削除|対応)/, "「{0}」と「{1}」を分離すべき可能性"],
  // 「A、Bを実装」のようなパターン
  [/(.+)、(.+)を(実装|改善|修正|追加|削除)/, "「{0}」と「{1}」を分離すべき可能性"],
  // 「AおよびB」のようなパターン
  [/(.+)および(.+)/, "「{0}」と「{1}」を分離すべき可能性"],
];

// 複数問題を示すパターン（英語）
const MULTI_PROBLEM_PATTERNS_EN: [RegExp, string][] = [
  // "A and B implementation" pattern
  [
    /(.+) and (.+) (implementation|improvement|fix|addition)/i,
    "'{0}' and '{1}' should be separate issues",
  ],
];

// 除外パターン（誤検知防止）
// CUSTOMIZE: プロジェクト固有の用語を追加
const EXCLUDE_PATTERNS = [
  /検出.*警告/, // 関連動作
  /作成.*削除/, // 対になる操作
  /追加.*更新/, // 関連操作
  /読み.*書き/, // 対になる操作
  /入力.*出力/, // 対になる操作
  /開始.*終了/, // 対になる操作
  /create.*delete/i, // 対になる操作（英語）
  /read.*write/i, // 対になる操作（英語）
  /start.*stop/i, // 対になる操作（英語）
];

/**
 * gh issue create コマンドからタイトルを抽出
 *
 * Uses tokenization for robust parsing of command-line arguments.
 * This handles edge cases better than regex:
 * - Properly handles quoted strings with spaces
 * - Handles --title=value format
 */
export function extractTitleFromCommand(command: string): string | null {
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
 * タイトルに複数問題パターンが含まれているかチェック
 *
 * 最初にマッチしたパターンのみを使用する（重複警告防止）。
 * Issue #1991: 複数パターンが同じタイトルにマッチした場合の重複を防ぐ。
 */
export function checkMultiProblemPatterns(title: string): string[] {
  // 除外パターンに該当する場合はスキップ
  for (const excludePattern of EXCLUDE_PATTERNS) {
    if (excludePattern.test(title)) {
      return [];
    }
  }

  // 日本語パターンをチェック（最初のマッチで終了）
  for (const [pattern, messageTemplate] of MULTI_PROBLEM_PATTERNS_JA) {
    const match = pattern.exec(title);
    if (match && match.length >= 3) {
      return [messageTemplate.replace("{0}", match[1]).replace("{1}", match[2])];
    }
  }

  // 英語パターンをチェック（最初のマッチで終了）
  for (const [pattern, messageTemplate] of MULTI_PROBLEM_PATTERNS_EN) {
    const match = pattern.exec(title);
    if (match && match.length >= 3) {
      return [messageTemplate.replace("{0}", match[1]).replace("{1}", match[2])];
    }
  }

  return [];
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = data.tool_input || {};
    const command = (toolInput as { command?: string }).command || "";

    // gh issue create コマンドを検出
    if (!command.includes("gh issue create")) {
      process.exit(0);
    }

    // タイトルを抽出
    const title = extractTitleFromCommand(command);
    if (!title) {
      process.exit(0);
    }

    // 複数問題パターンをチェック
    const warnings = checkMultiProblemPatterns(title);

    if (warnings.length > 0) {
      const blockMessage = `🚫 このIssueは複数の問題を含んでいる可能性があります。

タイトル: ${title}

検出されたパターン:
${warnings.map((w) => `  - ${w}`).join("\n")}

**1つのIssue = 1つの問題** を徹底してください。
分離が必要な場合は、別々のIssueを作成してください。

【対応方法】
1. 問題を分離して複数のIssueを作成
2. 誤検知の場合: ユーザーに確認してから続行
`;
      await logHookExecution(HOOK_NAME, "block", blockMessage, undefined, { sessionId });
      const result = makeBlockResult(HOOK_NAME, blockMessage);
      console.log(JSON.stringify(result));
      process.exit(2);
    }

    // パターンに該当しない場合は何も出力しない
    process.exit(0);
  } catch (e) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(e)}`);
    process.exit(0);
  }
}

if (import.meta.main) {
  main();
}
