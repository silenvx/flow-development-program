#!/usr/bin/env bun
/**
 * gh pr create 時に関連オープンIssueの確認を促す。
 *
 * Why:
 *   PR作成時に関連するオープンIssueがあることを知らないと、
 *   重複作業や見落としが発生する。キーワードベースで関連Issueを
 *   検索し、確認を促す。
 *
 * What:
 *   - gh pr createコマンドからタイトル・ボディを抽出
 *   - キーワードを抽出してIssue検索
 *   - 関連するオープンIssueを警告表示
 *
 * Remarks:
 *   - 非ブロック型（警告のみ）
 *   - ストップワード（助詞、一般的なGit用語）を除外
 *   - 最大5件のIssueを表示
 *
 * Changelog:
 *   - silenvx/dekita#1849: フック追加
 *   - silenvx/dekita#3160: TypeScript移行
 */

import { extractPrBody, extractPrTitle } from "../lib/command";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";
import { stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "pr-related-issue-check";

// =============================================================================
// Constants
// =============================================================================

// Stop words to exclude from keyword extraction
const STOP_WORDS = new Set([
  // English
  "a",
  "an",
  "the",
  "is",
  "are",
  "was",
  "were",
  "be",
  "been",
  "being",
  "have",
  "has",
  "had",
  "do",
  "does",
  "did",
  "will",
  "would",
  "could",
  "should",
  "may",
  "might",
  "must",
  "shall",
  "can",
  "need",
  "and",
  "or",
  "but",
  "if",
  "then",
  "else",
  "when",
  "where",
  "why",
  "how",
  "what",
  "which",
  "who",
  "whom",
  "this",
  "that",
  "these",
  "those",
  "for",
  "with",
  "from",
  "into",
  "onto",
  "upon",
  "about",
  "after",
  "before",
  "above",
  "below",
  "between",
  "under",
  "over",
  "through",
  "during",
  "until",
  "while",
  "of",
  "at",
  "by",
  "in",
  "on",
  "to",
  "as",
  "it",
  "its",
  "not",
  "no",
  "yes",
  "all",
  "any",
  "both",
  "each",
  "few",
  "more",
  "most",
  "other",
  "some",
  "such",
  "only",
  "own",
  "same",
  "so",
  "than",
  "too",
  "very",
  "just",
  "also",
  "now",
  "new",
  // Japanese particles
  "を",
  "が",
  "に",
  "で",
  "は",
  "の",
  "と",
  "も",
  "や",
  "から",
  "まで",
  "より",
  "へ",
  "など",
  "か",
  "ね",
  "よ",
  "わ",
  // Common PR/Git words
  "fix",
  "feat",
  "feature",
  "add",
  "update",
  "remove",
  "delete",
  "change",
  "modify",
  "refactor",
  "improve",
  "bug",
  "issue",
  "pr",
  "pull",
  "request",
  "merge",
  "branch",
  "commit",
  "push",
  "test",
  "docs",
  "chore",
]);

const MAX_KEYWORDS = 5;
const MAX_ISSUES_TO_DISPLAY = 5;
const MIN_KEYWORD_LENGTH = 3;

// =============================================================================
// Command Detection
// =============================================================================

/**
 * Check if command is a gh pr create command.
 */
function isGhPrCreateCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  const stripped = stripQuotedStrings(command);
  return /gh\s+pr\s+create\b/.test(stripped);
}

// extractPrTitle and extractPrBody are imported from ../lib/command

// =============================================================================
// Keyword Extraction
// =============================================================================

/**
 * Extract keywords from PR title and body.
 */
function extractKeywords(title: string | null, body: string | null): string[] {
  let text = "";
  if (title) {
    text += `${title} `;
  }
  if (body) {
    text += body;
  }

  if (!text.trim()) {
    return [];
  }

  // Extract words: alphanumeric and Japanese characters
  const words = text.match(/[a-zA-Z0-9\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]+/g) ?? [];

  // Filter words
  const keywords: string[] = [];
  const seen = new Set<string>();

  for (const word of words) {
    const wordLower = word.toLowerCase();

    // Skip if too short, is a stop word, or already seen
    if (word.length < MIN_KEYWORD_LENGTH) {
      continue;
    }
    if (STOP_WORDS.has(wordLower)) {
      continue;
    }
    if (seen.has(wordLower)) {
      continue;
    }

    seen.add(wordLower);
    keywords.push(word);
  }

  // Sort by length descending (longer words are more specific)
  keywords.sort((a, b) => b.length - a.length);

  return keywords.slice(0, MAX_KEYWORDS);
}

// =============================================================================
// Issue Search
// =============================================================================

interface RelatedIssue {
  number: number;
  title: string;
}

/**
 * Search for related open Issues using gh CLI.
 */
async function searchRelatedIssues(keywords: string[]): Promise<RelatedIssue[]> {
  if (keywords.length === 0) {
    return [];
  }

  // Build search query with OR-join
  const searchQuery = keywords.join(" OR ");

  try {
    const result = await asyncSpawn(
      "gh",
      [
        "issue",
        "list",
        "--search",
        searchQuery,
        "--state",
        "open",
        "--limit",
        "10",
        "--json",
        "number,title",
      ],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (!result.success) {
      return [];
    }

    const issues = JSON.parse(result.stdout);
    return issues.slice(0, MAX_ISSUES_TO_DISPLAY);
  } catch {
    return [];
  }
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};
  let keywordsUsed: string[] = [];
  let sessionId: string | undefined;

  try {
    const input = await parseHookInput();
    sessionId = input.session_id;
    const toolInput = input.tool_input as Record<string, unknown> | undefined;
    const command = (toolInput?.command as string) ?? "";

    if (isGhPrCreateCommand(command)) {
      // Extract title and body
      const title = extractPrTitle(command);
      const body = extractPrBody(command);

      // Extract keywords
      const keywords = extractKeywords(title, body);
      keywordsUsed = keywords;

      // Search for related Issues if keywords found
      if (keywords.length > 0) {
        const relatedIssues = await searchRelatedIssues(keywords);

        if (relatedIssues.length > 0) {
          const issueList = relatedIssues
            .map((issue) => `  #${issue.number}: ${issue.title}`)
            .join("\n");

          result.systemMessage = `⚠️ 関連するオープンIssueがあります

以下のIssueを確認しましたか？
${issueList}

確認済みの場合は続行してください。

（検索キーワード: ${keywords.join(", ")}）`;

          if (relatedIssues.length >= MAX_ISSUES_TO_DISPLAY) {
            result.systemMessage += "\n\n💡 他にも関連Issueがある可能性があります。";
          }
        }
      }
    }
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
  }

  // Log execution
  await logHookExecution(
    HOOK_NAME,
    result.decision ?? "approve",
    result.systemMessage,
    keywordsUsed.length > 0 ? { keywords: keywordsUsed } : undefined,
    { sessionId },
  );

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
