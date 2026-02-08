#!/usr/bin/env bun
/**
 * セッション終了時に問題報告とIssue作成の整合性を確認。
 *
 * Why:
 *   問題を発見してもIssueを作成せずセッションを終了すると、
 *   問題が放置される。問題報告パターンを検出し、Issue作成を促す。
 *
 * What:
 *   - セッションのトランスクリプトを解析
 *   - Claude発言から問題報告パターン（バグ発見、エラー発生等）を検出
 *   - gh issue createコマンドの実行有無を確認
 *   - 問題報告ありかつIssue作成なしなら警告
 *
 * Remarks:
 *   - 非ブロック型（誤検知リスクのため警告のみ）
 *   - Stopフック
 *   - AGENTS.md「Issue作成が必要なケース」を仕組み化
 *
 * Changelog:
 *   - silenvx/dekita#421: フック追加
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { createHookContext, parseHookInput } from "../lib/session";
import { loadTranscript } from "../lib/transcript";

const HOOK_NAME = "problem-report-check";

// Problem report patterns in Japanese and English
// These indicate Claude has identified a problem
export const PROBLEM_PATTERNS = [
  // Japanese patterns
  /問題があり/,
  /バグを発見/,
  /バグが見つか/,
  /エラーが発生/,
  /動作していません/,
  /不具合を発見/,
  /不具合が見つか/,
  /予期せぬ動作/,
  /想定外の動作/,
  /異常を検知/,
  /障害を検知/,
  /失敗しています/,
  // English patterns
  /found a bug/i,
  /discovered a bug/i,
  /found an issue/i,
  /discovered an issue/i,
  /unexpected behavior/i,
  /not working/i,
  /fails to/i,
  /error occurs/i,
  /malfunction/i,
];

// Issue creation patterns in Bash commands
const ISSUE_CREATION_PATTERN = /gh\s+issue\s+create/i;

// Patterns that indicate false positives (skip these)
export const FALSE_POSITIVE_PATTERNS = [
  // Quoting or referencing patterns
  /["「].*?問題.*?[」"]/, // Quoted problem mentions (non-greedy)
  /#\d+/, // Issue number references like #123
  // Discussion patterns
  /問題ないか/, // "Is there a problem?"
  /問題ありません/, // "No problem"
  /問題なし/, // "No problem"
  /問題は解決/, // "Problem is resolved"
  /問題が解決/, // "Problem was resolved"
];

export interface TranscriptEntry {
  role?: string;
  content?: string | { type: string; text?: string; name?: string; input?: { command?: string } }[];
}

/**
 * Extract text from Claude's messages in the transcript.
 */
export function extractClaudeMessages(transcript: TranscriptEntry[]): string[] {
  const messages: string[] = [];
  for (const entry of transcript) {
    // Check if this is Claude's response (assistant message)
    if (entry.role === "assistant") {
      const content = entry.content;
      if (Array.isArray(content)) {
        for (const block of content) {
          if (typeof block === "object" && block.type === "text" && block.text) {
            messages.push(block.text);
          }
        }
      } else if (typeof content === "string") {
        messages.push(content);
      }
    }
  }
  return messages;
}

/**
 * Extract Bash commands from the transcript.
 */
export function extractBashCommands(transcript: TranscriptEntry[]): string[] {
  const commands: string[] = [];
  for (const entry of transcript) {
    if (entry.role === "assistant") {
      const content = entry.content;
      if (Array.isArray(content)) {
        for (const block of content) {
          if (typeof block === "object" && block.type === "tool_use" && block.name === "Bash") {
            const cmd = block.input?.command;
            if (cmd) {
              commands.push(cmd);
            }
          }
        }
      }
    }
  }
  return commands;
}

/**
 * Check if a problem match overlaps with a false positive pattern.
 */
export function isFalsePositiveMatch(msg: string, matchStart: number, matchEnd: number): boolean {
  // Check context around the match (expand by 10 chars on each side)
  const contextStart = Math.max(0, matchStart - 10);
  const contextEnd = Math.min(msg.length, matchEnd + 10);
  const context = msg.slice(contextStart, contextEnd);

  for (const fpPattern of FALSE_POSITIVE_PATTERNS) {
    if (fpPattern.test(context)) {
      return true;
    }
  }
  return false;
}

/**
 * Find problem report patterns in Claude's messages.
 */
export function findProblemReports(messages: string[]): string[] {
  const problems: string[] = [];
  for (const msg of messages) {
    // Check for problem patterns
    for (const pattern of PROBLEM_PATTERNS) {
      const match = pattern.exec(msg);
      if (match) {
        // Check if this specific match is a false positive
        if (isFalsePositiveMatch(msg, match.index, match.index + match[0].length)) {
          continue;
        }

        // Extract surrounding context (50 chars before and after)
        const start = Math.max(0, match.index - 50);
        const end = Math.min(msg.length, match.index + match[0].length + 50);
        let excerpt = msg.slice(start, end).trim();

        // Skip empty excerpts after trim
        if (!excerpt) {
          continue;
        }

        // Add ellipsis if truncated
        if (start > 0) {
          excerpt = `...${excerpt}`;
        }
        if (end < msg.length) {
          excerpt = `${excerpt}...`;
        }
        problems.push(excerpt);
        break; // Only count once per message
      }
    }
  }
  return problems;
}

/**
 * Count gh issue create commands.
 */
export function findIssueCreations(commands: string[]): number {
  let count = 0;
  for (const cmd of commands) {
    if (ISSUE_CREATION_PATTERN.test(cmd)) {
      count++;
    }
  }
  return count;
}

/**
 * Stop hook to verify problem reports are documented as Issues.
 */
async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    // Read input from stdin
    const inputData = await parseHookInput();
    const ctx = createHookContext(inputData);
    sessionId = ctx.sessionId;

    // Skip if stop hook is already active (prevent infinite loops)
    if (inputData.stop_hook_active) {
      logHookExecution(HOOK_NAME, "approve", "stop_hook_active", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Get transcript path
    const transcriptPath = inputData.transcript_path;
    if (!transcriptPath) {
      logHookExecution(HOOK_NAME, "approve", "no transcript path", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Load transcript
    const transcript = loadTranscript(transcriptPath);
    if (!transcript) {
      logHookExecution(HOOK_NAME, "approve", "transcript load failed", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Extract Claude's messages and Bash commands
    const claudeMessages = extractClaudeMessages(transcript as TranscriptEntry[]);
    const bashCommands = extractBashCommands(transcript as TranscriptEntry[]);

    // Find problem reports and issue creations
    const problemReports = findProblemReports(claudeMessages);
    const issueCount = findIssueCreations(bashCommands);

    // If problems reported but no issues created, warn (but don't block)
    if (problemReports.length > 0 && issueCount === 0) {
      const excerpts = problemReports.slice(0, 3); // Show up to 3 examples
      let excerptText = excerpts.map((e) => `  - ${e.slice(0, 100)}`).join("\n");
      if (problemReports.length > 3) {
        excerptText += `\n  - ... 他 ${problemReports.length - 3} 件`;
      }

      result.systemMessage = `⚠️ [problem-report-check] 問題報告が検出されました（${problemReports.length}件）:\n${excerptText}\n\nIssue作成を確認してください:\n- 問題を発見した場合は \`gh issue create\` でIssue作成\n- AGENTS.md: 「Issue作成が必要なケース」を参照\n- 誤検知の場合は無視してください`;
      logHookExecution(
        HOOK_NAME,
        "approve",
        `problems detected: ${problemReports.length}, issues: ${issueCount}`,
        { problem_count: problemReports.length, issue_count: issueCount },
        { sessionId },
      );
    } else {
      logHookExecution(
        HOOK_NAME,
        "approve",
        undefined,
        {
          problem_count: problemReports.length,
          issue_count: issueCount,
        },
        { sessionId },
      );
    }
  } catch (error) {
    // Don't block on errors, just skip the check
    logHookExecution(HOOK_NAME, "approve", `error: ${formatError(error)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
