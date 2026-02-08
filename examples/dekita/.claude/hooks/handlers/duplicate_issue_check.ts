#!/usr/bin/env bun
/**
 * Issue作成前に類似Issueを検索して重複を警告する。
 *
 * Why:
 *   同じ問題のIssueが重複作成されると、議論が分散し対応が遅れる。
 *   作成前に類似Issueを表示することで重複を防止する。
 *
 * What:
 *   - gh issue createコマンドを検出
 *   - タイトルからキーワードを抽出して類似Issueを検索
 *   - 類似Issueがあれば警告メッセージを表示
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、systemMessageで警告）
 *   - PreToolUse:Bashで発火（gh issue createコマンド）
 *   - ストップワード除外でキーワード抽出精度を向上
 *   - 最大5件の類似Issueを表示
 *
 * Changelog:
 *   - silenvx/dekita#1980: フック追加
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";
import { splitShellArgs } from "../lib/strings";

const HOOK_NAME = "duplicate-issue-check";

// 検索結果の最大件数
const MAX_SEARCH_RESULTS = 5;

// キーワードの最小文字数
// Issueタイトルは短い傾向があるため、pr_related_issue_check.ts（=3）より短く設定
const MIN_KEYWORD_LENGTH = 2;

// タイトルから除外するストップワード（日本語・英語）
// biome-ignore format: 日本語・英語の品詞や役割ごとにストップワードをグループ化して可読性を保つ
const STOP_WORDS = new Set([
  // 日本語助詞・接続詞
  "の", "を", "に", "は", "が", "で", "と", "も", "や", "へ",
  "から", "まで", "より", "など", "について", "ため", "こと", "もの",
  // 日本語指示詞・動詞
  "これ", "それ", "あれ", "この", "その", "ある", "いる", "する", "なる", "できる",
  // 英語冠詞・be動詞
  "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
  // 英語助動詞
  "have", "has", "had", "do", "does", "did",
  "will", "would", "could", "should", "may", "might", "must", "can",
  // 英語前置詞
  "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
  "into", "through", "during", "before", "after", "above", "below", "between", "under",
  // 英語副詞・その他
  "again", "further", "then", "once", "here", "there", "when", "where", "why", "how",
  "all", "each", "few", "more", "most", "other", "some", "such",
  "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "just",
  // 英語接続詞
  "and", "but", "if", "or", "because", "until", "while",
  // プレフィックス（conventional commits）
  "feat", "fix", "docs", "style", "refactor", "test", "chore", "perf", "ci", "build", "revert",
  // アクション動詞（検索精度向上のため追加）
  "add", "update", "remove", "delete", "change", "modify", "improve", "create", "implement", "enable", "disable",
]);

export interface SimilarIssue {
  number: number;
  title: string;
}

/**
 * gh issue create コマンドからタイトルを抽出する。
 */
export function extractTitleFromCommand(command: string): string | null {
  let args: string[];
  try {
    args = splitShellArgs(command);
  } catch {
    return null;
  }

  // --title または -t オプションを探す
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if ((arg === "--title" || arg === "-t") && i + 1 < args.length) {
      return args[i + 1];
    }
    if (arg.startsWith("--title=")) {
      return arg.slice("--title=".length);
    }
    if (arg.startsWith("-t=")) {
      return arg.slice("-t=".length);
    }
  }

  return null;
}

/**
 * タイトルから検索用キーワードを抽出する。
 */
export function extractKeywords(title: string): string[] {
  // プレフィックス（feat:, fix:, feat(scope): など）を除去
  const cleaned = title.replace(
    /^(feat|fix|docs|style|refactor|test|chore|perf|ci|build|revert)(?:\([^)]*\))?\s*:\s*/i,
    "",
  );

  // 記号を除去してトークン化
  // 日本語文字も含む: \u3040-\u309F (ひらがな), \u30A0-\u30FF (カタカナ), \u4E00-\u9FFF (漢字)
  const tokens = cleaned.toLowerCase().match(/[\w\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]+/g) || [];

  // ストップワードと短すぎるトークンを除外
  const keywords: string[] = [];
  const seen = new Set<string>();

  for (const token of tokens) {
    if (!STOP_WORDS.has(token) && token.length >= MIN_KEYWORD_LENGTH && !seen.has(token)) {
      seen.add(token);
      keywords.push(token);
    }
    if (keywords.length >= 5) break;
  }

  return keywords;
}

/**
 * 類似Issueを検索する。
 */
function searchSimilarIssues(keywords: string[]): SimilarIssue[] {
  if (keywords.length === 0) {
    return [];
  }

  // キーワードをスペース区切りで連結して検索クエリを構築
  const query = keywords.join(" ");

  try {
    const result = Bun.spawnSync(
      [
        "gh",
        "issue",
        "list",
        "--search",
        query,
        "--state",
        "open",
        "--limit",
        String(MAX_SEARCH_RESULTS),
        "--json",
        "number,title",
      ],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (result.exitCode !== 0) {
      return [];
    }

    return JSON.parse(result.stdout.toString()) as SimilarIssue[];
  } catch {
    return [];
  }
}

/**
 * 警告メッセージをフォーマットする。
 */
export function formatWarningMessage(similarIssues: SimilarIssue[]): string {
  const lines = ["⚠️ **類似Issueが存在する可能性があります**:", ""];

  for (const issue of similarIssues) {
    const number = issue.number ?? "?";
    let title = issue.title ?? "No title";
    // タイトルが長すぎる場合は切り詰め
    if (title.length > 60) {
      title = `${title.slice(0, 57)}...`;
    }
    lines.push(`  - #${number}: ${title}`);
  }

  lines.push("");
  lines.push("重複でないことを確認してから作成してください。");

  return lines.join("\n");
}

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;
    const toolName = inputData.tool_name || "";
    const toolInput = inputData.tool_input || {};

    // Only process Bash commands
    if (toolName !== "Bash") {
      console.log(JSON.stringify(result));
      return;
    }

    const command = (toolInput.command as string) || "";

    // Check if this is a gh issue create command
    let tokens: string[];
    try {
      tokens = splitShellArgs(command);
    } catch {
      // コマンドが正しくパースできない場合はスキップ
      console.log(JSON.stringify(result));
      return;
    }

    // tokens 内で "gh", "issue", "create" が連続して出現するか確認
    // チェーンコマンド（cd repo && gh issue create）にも対応
    let found = false;
    for (let i = 0; i < tokens.length - 2; i++) {
      if (tokens[i] === "gh" && tokens[i + 1] === "issue" && tokens[i + 2] === "create") {
        found = true;
        break;
      }
    }

    if (!found) {
      console.log(JSON.stringify(result));
      return;
    }

    // Extract title from command
    const title = extractTitleFromCommand(command);
    if (!title) {
      // タイトルが抽出できない場合はスキップ
      logHookExecution(HOOK_NAME, "approve", "no title found in command", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Extract keywords from title
    const keywords = extractKeywords(title);
    if (keywords.length === 0) {
      logHookExecution(HOOK_NAME, "approve", "no keywords extracted from title", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Search for similar issues
    const similarIssues = searchSimilarIssues(keywords);
    if (similarIssues.length > 0) {
      result.systemMessage = formatWarningMessage(similarIssues);
      logHookExecution(
        HOOK_NAME,
        "approve",
        `found ${similarIssues.length} similar issues`,
        {
          keywords,
          similar_count: similarIssues.length,
        },
        { sessionId },
      );
    } else {
      logHookExecution(
        HOOK_NAME,
        "approve",
        "no similar issues found",
        { keywords },
        { sessionId },
      );
    }
  } catch (error) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    logHookExecution(HOOK_NAME, "approve", `error: ${formatError(error)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

// Only run when executed directly, not when imported for testing
if (import.meta.main) {
  main();
}
