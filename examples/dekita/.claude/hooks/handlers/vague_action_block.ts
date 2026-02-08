#!/usr/bin/env bun
/**
 * 曖昧な対策表現（精神論）を検出してACTION_REQUIRED警告。
 *
 * Why:
 *   「注意する」「気をつける」「徹底する」といった精神論は再発防止策にならない。
 *   具体的な仕組み化（フック/CI/スクリプト）を強制することで実効性を担保する。
 *
 * What:
 *   - セッション終了時（Stop）に発火
 *   - transcriptから対策文脈の曖昧表現を検出
 *   - 「守る」「注意」「心がけ」等のパターンをマッチング
 *   - 曖昧表現があればACTION_REQUIREDで警告
 *
 * Remarks:
 *   - 警告型フック（ACTION_REQUIRED、ブロックはしない）
 *   - 具体的アクション（Issue作成、フック実装等）があればスキップ
 *   - systematization-checkは教訓→仕組み化要求、本hookは表現自体を検出
 *   - Python版: vague_action_block.py
 *
 * Changelog:
 *   - silenvx/dekita#1959: フック追加
 *   - silenvx/dekita#2026: exit code 0でACTION_REQUIRED形式に変更
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { createContext, getSessionId, parseHookInput } from "../lib/session";
import { loadTranscript } from "../lib/transcript";

const HOOK_NAME = "vague-action-block";

// Vague action patterns in countermeasure context
export const VAGUE_ACTION_PATTERNS = [
  // 対策/改善/今後 + 守る/遵守/徹底
  /(?:対策|改善|今後).*(?:守[るりっ]|遵守|徹底)/,
  // 対策/改善/今後 + 注意/気をつけ/意識/心がけ
  /(?:対策|改善|今後).*(?:注意|気をつけ|意識|心がけ)/,
  // ガイド/ルール + 守る/遵守/徹底/従う
  /(?:ガイド|ルール|規約|方針).*(?:守[るりっ]|遵守|徹底|従[うい])/,
  // 〜を意識する/心がける (standalone)
  /(?:を|に)(?:意識|心がけ|注意)(?:する|します|していく)/,
  // 確認を徹底する
  /確認.*徹底/,
];

// Countermeasure context indicators
export const COUNTERMEASURE_CONTEXT_PATTERNS = [
  /対策/,
  /改善(?:点|策)?/,
  /今後(?:は|の)/,
  /再発防止/,
  /防止策/,
  /反省点/,
];

// Patterns that indicate concrete actions (NOT vague)
export const CONCRETE_ACTION_PATTERNS = [
  /Issue\s*(?:#|\d|を)/i, // Issue作成
  /フック(?:を|作成|追加)/, // フック作成
  /hook(?:を|作成|追加)/i,
  /CI(?:を|に|で)(?:追加|チェック)/, // CI追加
  /スクリプト(?:を|作成)/, // スクリプト作成
  /テスト(?:を|追加|作成)/, // テスト追加
  /コード(?:を)?(?:修正|変更)/, // コード修正
  /実装(?:する|します|しました)/, // 実装
  /作成(?:する|します|しました)/, // 作成
  /修正(?:する|します|しました)/, // 修正
];

export interface TranscriptEntry {
  role?: string;
  content?: string | ContentBlock[];
  [key: string]: unknown;
}

export interface ContentBlock {
  type?: string;
  text?: string;
  [key: string]: unknown;
}

/**
 * Extract text from Claude's messages in the transcript.
 */
export function extractClaudeMessages(transcript: TranscriptEntry[]): string[] {
  const messages: string[] = [];

  for (const entry of transcript) {
    if (entry.role !== "assistant") continue;

    const content = entry.content;
    if (!content) continue;

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

  return messages;
}

/**
 * Check if the match is within countermeasure context.
 */
export function isInCountermeasureContext(
  text: string,
  matchStart: number,
  matchEnd: number,
): boolean {
  // Check context before the match (100 chars)
  const contextStart = Math.max(0, matchStart - 100);
  const contextBefore = text.slice(contextStart, matchStart);

  for (const pattern of COUNTERMEASURE_CONTEXT_PATTERNS) {
    if (pattern.test(contextBefore)) {
      return true;
    }
  }

  // Check within the matched text
  const matchedText = text.slice(matchStart, matchEnd);
  for (const pattern of COUNTERMEASURE_CONTEXT_PATTERNS) {
    if (pattern.test(matchedText)) {
      return true;
    }
  }

  return false;
}

/**
 * Check if the text contains concrete action patterns.
 */
export function hasConcreteAction(text: string): boolean {
  for (const pattern of CONCRETE_ACTION_PATTERNS) {
    if (pattern.test(text)) {
      return true;
    }
  }
  return false;
}

/**
 * Find vague action patterns in messages within countermeasure context.
 */
export function findVaguePatterns(messages: string[]): string[] {
  const vagueExcerpts: string[] = [];

  for (const msg of messages) {
    // Skip if message contains concrete actions
    if (hasConcreteAction(msg)) {
      continue;
    }

    for (const pattern of VAGUE_ACTION_PATTERNS) {
      const match = pattern.exec(msg);
      if (!match) continue;

      // Only flag if in countermeasure context
      const matchStart = match.index;
      const matchEnd = matchStart + match[0].length;

      if (!isInCountermeasureContext(msg, matchStart, matchEnd)) {
        continue;
      }

      // Extract surrounding context
      const start = Math.max(0, matchStart - 20);
      const end = Math.min(msg.length, matchEnd + 20);
      let excerpt = msg.slice(start, end).trim();
      if (start > 0) excerpt = `...${excerpt}`;
      if (end < msg.length) excerpt = `${excerpt}...`;

      vagueExcerpts.push(excerpt);
      break; // Only count once per message
    }
  }

  return vagueExcerpts;
}

async function main(): Promise<void> {
  const result: Record<string, unknown> = {};

  try {
    const data = await parseHookInput();
    const ctx = createContext(data);
    const sessionId = getSessionId(ctx) ?? "unknown";

    // Skip if stop hook is already active
    if (data.stop_hook_active) {
      await logHookExecution(HOOK_NAME, "approve", "stop_hook_active", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    const transcriptPath = data.transcript_path;
    if (!transcriptPath) {
      await logHookExecution(HOOK_NAME, "approve", "no transcript path", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    const transcript = loadTranscript(transcriptPath);
    if (!transcript) {
      await logHookExecution(HOOK_NAME, "approve", "transcript load failed", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    const claudeMessages = extractClaudeMessages(transcript);
    const vagueExcerpts = findVaguePatterns(claudeMessages);

    if (vagueExcerpts.length > 0) {
      const excerptLines = vagueExcerpts.slice(0, 3).map((e) => `  - ${e.slice(0, 80)}`);
      if (vagueExcerpts.length > 3) {
        excerptLines.push(`  - ... 他 ${vagueExcerpts.length - 3} 件`);
      }
      const excerptText = excerptLines.join("\n");

      const reason = `[ACTION_REQUIRED: CONCRETE_ACTION]\n曖昧な対策表現を検出しました。\n\n**検出された表現** (${vagueExcerpts.length}件):\n${excerptText}\n\n「ガイドを守る」「注意する」は対策ではありません。\nClaude Codeは以下のいずれかを実行してください:\n1. フック作成（違反をブロック）\n2. CI追加（自動チェック）\n3. スクリプト作成（自動化）\n4. コード修正（根本対応）\n5. Issue作成（追跡可能な形で記録）\n\n精神論（「注意する」「気をつける」「徹底する」）は禁止です。`;

      await logHookExecution(
        HOOK_NAME,
        "warn",
        `vague patterns: ${vagueExcerpts.length}`,
        undefined,
        { sessionId },
      );

      // Print ACTION_REQUIRED to stderr for Claude Code to see
      console.error(`[${HOOK_NAME}] ${reason}`);
    } else {
      await logHookExecution(HOOK_NAME, "approve", "no vague patterns", undefined, { sessionId });
    }
  } catch (error) {
    await logHookExecution(HOOK_NAME, "approve", `error: ${formatError(error)}`);
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
