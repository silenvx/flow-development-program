#!/usr/bin/env bun
/**
 * worktree作成時に既存実装の存在を警告し、検証を促す。
 *
 * Why:
 *   「コードが存在する」≠「動作している」。Issueがオープンなのに
 *   関連コードが存在する場合、そのコードが正常に動作していない
 *   可能性が高い。検証せずに「実装済み」と判断すると時間を無駄にする。
 *
 * What:
 *   - git worktree addコマンドからIssue番号を抽出
 *   - コメント・ファイル名からIssue番号やキーワードで関連コードを検索
 *   - 関連コードが見つかった場合は検証を促す警告を表示
 *
 * Remarks:
 *   - ブロックせず警告のみ
 *   - 検索結果は最大5件まで表示
 *
 * Changelog:
 *   - silenvx/dekita#????: フック追加
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { spawnSync } from "node:child_process";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "existing-impl-check";

/**
 * Extract issue number from git worktree add command.
 */
export function extractIssueNumberFromCommand(command: string): number | null {
  if (!command.includes("git worktree add")) {
    return null;
  }

  const patterns = [
    /issue[_-](\d+)/i, // issue-123, issue_123
    /#(\d+)/, // #123
    /\/(\d+)[-_]/, // /123-description
    /[-_](\d+)[-_]/, // feature-123-name
    /[-_](\d+)$/, // feature-123 (at end)
    /[-_](\d+)\s/, // feature-123 (followed by space)
  ];

  for (const pattern of patterns) {
    const match = pattern.exec(command);
    if (match) {
      return Number.parseInt(match[1], 10);
    }
  }

  return null;
}

/**
 * Get issue title from GitHub.
 */
function getIssueTitle(issueNumber: number): string | null {
  try {
    const result = spawnSync("gh", ["issue", "view", String(issueNumber), "--json", "title"], {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
    });

    if (result.status === 0) {
      const data = JSON.parse(result.stdout);
      return data.title || null;
    }
  } catch {
    // Fail-open
  }
  return null;
}

/**
 * Extract potential code-related keywords from issue title.
 */
export function extractKeywordsFromTitle(title: string): string[] {
  const keywords: string[] = [];

  // "feat(hooks): xxx" → extract "hooks"
  const scopeMatch = /\(([^)]+)\)/.exec(title);
  if (scopeMatch) {
    keywords.push(scopeMatch[1]);
  }

  // Extract hyphenated words that look like code names
  const hyphenated = title.toLowerCase().match(/[a-z]+-[a-z]+/g) || [];
  keywords.push(...hyphenated);

  // Extract camelCase or PascalCase words
  const camel = title.match(/[a-zA-Z][a-z]*[A-Z][a-z]*/g) || [];
  keywords.push(...camel.map((w) => w.toLowerCase()));

  // Japanese keywords that might indicate functionality
  const jpKeywords: Record<string, string> = {
    自動: "auto",
    アサイン: "assign",
    チェック: "check",
    検証: "verif",
    作成: "create",
    削除: "delete",
    更新: "update",
    レビュー: "review",
    マージ: "merge",
  };

  for (const [jp, en] of Object.entries(jpKeywords)) {
    if (title.includes(jp)) {
      keywords.push(en);
    }
  }

  return keywords;
}

/**
 * Search for code that might be related to this issue.
 */
function searchRelatedCode(issueNumber: number, issueTitle: string | null): string[] {
  const relatedFiles: string[] = [];

  // Strategy 1: Issue number in comments (e.g., "# Issue #123", "// #123")
  try {
    const result = spawnSync("git", ["grep", "-l", `#${issueNumber}`], {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
    });

    if (result.status === 0 && result.stdout.trim()) {
      relatedFiles.push(...result.stdout.trim().split("\n"));
    }
  } catch {
    // Fail-open
  }

  // Strategy 2: Extract keywords from issue title and search filenames
  if (issueTitle) {
    const keywords = extractKeywordsFromTitle(issueTitle);

    for (const keyword of keywords) {
      if (keyword.length >= 4) {
        try {
          const result = spawnSync("git", ["ls-files", `*${keyword}*`], {
            encoding: "utf-8",
            timeout: TIMEOUT_MEDIUM * 1000,
          });

          if (result.status === 0 && result.stdout.trim()) {
            for (const f of result.stdout.trim().split("\n")) {
              if (f && !relatedFiles.includes(f)) {
                relatedFiles.push(f);
              }
            }
          }
        } catch {
          // Fail-open
        }
      }
    }
  }

  // Deduplicate and filter
  const seen = new Set<string>();
  const uniqueFiles: string[] = [];

  for (const f of relatedFiles) {
    if (f && !seen.has(f)) {
      // Filter out hidden files but keep .claude/ directory
      if (!f.startsWith(".") || f.startsWith(".claude/")) {
        seen.add(f);
        uniqueFiles.push(f);
      }
    }
  }

  return uniqueFiles.slice(0, 5); // Limit to 5 files
}

interface HookResult {
  decision?: string;
  systemMessage?: string;
  reason?: string;
}

async function main(): Promise<void> {
  const result: HookResult = {};
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;
    const toolInput = inputData.tool_input || {};
    const command = (toolInput as { command?: string }).command || "";

    // Only process git worktree add commands
    if (command.includes("git worktree add")) {
      const issueNumber = extractIssueNumberFromCommand(command);

      if (issueNumber) {
        const issueTitle = getIssueTitle(issueNumber);
        const relatedFiles = searchRelatedCode(issueNumber, issueTitle);

        if (relatedFiles.length > 0) {
          const filesList = relatedFiles.map((f) => `   - ${f}`).join("\n");
          result.systemMessage = `⚠️ **既存実装の検証が必要です** (Issue #${issueNumber})\n\n関連する既存コードが見つかりました:\n${filesList}\n\n**重要**: 「コードが存在する」≠「動作している」\nIssueが存在する理由を確認し、既存実装が\n**実際に期待通り動作するか**を検証してください。\n\n検証せずに「実装済み」と判断すると、\n問題の見落としにつながります。`;
        }
      }
    }
  } catch (e) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(e)}`);
  }

  await logHookExecution(HOOK_NAME, "approve", result.reason, undefined, { sessionId });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    // Hooks should not block on internal errors - output approve response
    console.log(JSON.stringify({}));
    process.exit(0);
  });
}
