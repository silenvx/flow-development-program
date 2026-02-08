#!/usr/bin/env bun
/**
 * セッション終了時に教訓が仕組み化されたか確認する。
 *
 * Why:
 *   教訓をドキュメント化しただけでは、同じ問題が再発する。
 *   フック/CI/ツールによる仕組み化を強制することで、再発を防止する。
 *
 * What:
 *   - 教訓パターン検出（教訓、反省点、学び、気づき、lesson learned等）
 *   - 仕組み化ファイル変更検出（.claude/hooks/*.py、.github/workflows/*.yml等）
 *   - 教訓あり＆仕組み化なしの場合にブロック
 *   - 誤検知緩和（複数インジケータ要求、明示的スキップ許可）
 *
 * Remarks:
 *   - problem-report-checkはIssue作成を確認、本フックは仕組み化を確認
 *   - 強パターン（「仕組み化が必要」等）は単独でもトリガー
 *   - 「仕組み化しました」等の完了パターンは誤検知として除外
 *   - Python版: systematization_check.py
 *
 * Changelog:
 *   - silenvx/dekita#468: フック追加
 *   - silenvx/dekita#2986: TypeScript版に移植
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";
import { loadTranscript } from "../lib/transcript";

const HOOK_NAME = "systematization-check";

// Lesson/learning patterns in Japanese
const LESSON_PATTERNS_JA = [
  /教訓として/i,
  /反省点として/i,
  /反省点は/i,
  /学びとして/i,
  /学んだこと/i,
  /気づいた(?:こと|点)/i,
  /今後は.*(?:する|しない)(?:べき|必要)/i,
  /(?:再発)?防止(?:策)?(?:として|は)/i,
  /次回(?:から)?は/i,
  /改善(?:点|策|が必要)/i,
];

// Lesson/learning patterns in English
const LESSON_PATTERNS_EN = [
  /lesson(?:s)?\s+learned/i,
  /key\s+takeaway/i,
  /should\s+have\s+(?!been\s+(?:done|completed))/i, // "should have" but not completion
  /in\s+the\s+future/i,
  /next\s+time/i,
  /going\s+forward/i,
  /to\s+prevent\s+this/i,
  /to\s+avoid\s+this/i,
];

const ALL_LESSON_PATTERNS = [...LESSON_PATTERNS_JA, ...LESSON_PATTERNS_EN];

// Strong indicators that definitely need systematization
const STRONG_LESSON_PATTERNS = [
  /仕組み化(?:する|が必要|すべき)/i,
  /hook(?:を|で|が)/i,
  /フック(?:を|で|が)/i,
  /CI(?:で|に|を)/i,
  /自動化(?:する|が必要|すべき)/i,
];

// Patterns that indicate false positives
const FALSE_POSITIVE_PATTERNS = [
  /仕組み化(?:しました|済み|完了)/i,
  /hook(?:を)?(?:作成|追加|実装)(?:しました|済み|完了)/i,
  /フック(?:を)?(?:作成|追加|実装)(?:しました|済み|完了)/i,
  /対応不要/i,
  /アクション不要/i,
  /no\s+action\s+needed/i,
  /already\s+(?:implemented|done|addressed)/i,
];

// Systematization file patterns
const SYSTEMATIZATION_FILE_PATTERNS = [
  /\.claude\/hooks\/.*\.py$/,
  /\.github\/workflows\/.*\.ya?ml$/,
  /\.claude\/scripts\/.*\.(?:py|sh)$/,
  /\.claude\/hooks\/handlers\/.*\.ts$/,
];

interface ContentBlock {
  type?: string;
  text?: string;
  name?: string;
  input?: Record<string, unknown>;
  [key: string]: unknown;
}

interface TranscriptEntry {
  role?: string;
  content?: string | ContentBlock[];
  [key: string]: unknown;
}

/**
 * Extract text from Claude's messages in the transcript.
 */
export function extractClaudeMessages(transcript: TranscriptEntry[]): string[] {
  const messages: string[] = [];

  for (const entry of transcript) {
    if (entry.role === "assistant") {
      const content = entry.content;
      if (Array.isArray(content)) {
        for (const block of content) {
          if (block && typeof block === "object" && block.type === "text" && block.text) {
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
 * Extract file paths from Edit/Write tool uses.
 */
export function extractFileOperations(transcript: TranscriptEntry[]): string[] {
  const files: string[] = [];

  for (const entry of transcript) {
    if (entry.role === "assistant") {
      const content = entry.content;
      if (Array.isArray(content)) {
        for (const block of content) {
          if (block && typeof block === "object" && block.type === "tool_use") {
            const toolName = block.name ?? "";
            if (toolName === "Edit" || toolName === "Write") {
              const input = block.input as Record<string, unknown> | undefined;
              const filePath = input?.file_path;
              if (typeof filePath === "string") {
                files.push(filePath);
              }
            }
          }
        }
      }
    }
  }

  return files;
}

/**
 * Check if any message indicates a false positive.
 */
export function hasFalsePositive(messages: string[]): boolean {
  for (const msg of messages) {
    for (const pattern of FALSE_POSITIVE_PATTERNS) {
      if (pattern.test(msg)) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Find lesson patterns in Claude's messages.
 */
export function findLessonPatterns(messages: string[]): { lessons: string[]; hasStrong: boolean } {
  const lessons: string[] = [];
  let hasStrong = false;

  for (const msg of messages) {
    // Check strong patterns first
    for (const pattern of STRONG_LESSON_PATTERNS) {
      if (pattern.test(msg)) {
        hasStrong = true;
        break;
      }
    }

    // Check normal lesson patterns
    for (const pattern of ALL_LESSON_PATTERNS) {
      const match = msg.match(pattern);
      if (match?.index !== undefined) {
        // Extract surrounding context
        const start = Math.max(0, match.index - 30);
        const end = Math.min(msg.length, match.index + match[0].length + 30);
        let excerpt = msg.slice(start, end).trim();
        if (start > 0) {
          excerpt = `...${excerpt}`;
        }
        if (end < msg.length) {
          excerpt = `${excerpt}...`;
        }
        lessons.push(excerpt);
        break; // Only count once per message
      }
    }
  }

  return { lessons, hasStrong };
}

/**
 * Find files that indicate systematization.
 */
export function findSystematizationFiles(files: string[]): string[] {
  const systematized: string[] = [];

  for (const filePath of files) {
    for (const pattern of SYSTEMATIZATION_FILE_PATTERNS) {
      if (pattern.test(filePath)) {
        systematized.push(filePath);
        break;
      }
    }
  }

  return systematized;
}

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};

  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;

    // Skip if stop hook is already active (prevents infinite loops)
    if (inputData.stop_hook_active) {
      await logHookExecution(HOOK_NAME, "approve", "stop_hook_active", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    const transcriptPath = inputData.transcript_path as string | undefined;
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
    const fileOperations = extractFileOperations(transcript);

    // Check for false positives first
    if (hasFalsePositive(claudeMessages)) {
      await logHookExecution(HOOK_NAME, "approve", "false positive detected", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Find lessons and systematization
    const { lessons, hasStrong } = findLessonPatterns(claudeMessages);
    const systematizedFiles = findSystematizationFiles(fileOperations);

    // Decision logic:
    // - If strong lesson pattern AND no systematization -> warn with ACTION_REQUIRED
    // - If 2+ normal lesson patterns AND no systematization -> warn with ACTION_REQUIRED
    // - Otherwise -> approve with message if lessons detected

    let shouldWarn = false;
    if (hasStrong && systematizedFiles.length === 0) {
      shouldWarn = true;
    } else if (lessons.length >= 2 && systematizedFiles.length === 0) {
      shouldWarn = true;
    }

    if (shouldWarn) {
      const excerpts = lessons.slice(0, 3);
      let excerptText = excerpts.map((e) => `  - ${e.slice(0, 80)}`).join("\n");
      if (lessons.length > 3) {
        excerptText += `\n  - ... 他 ${lessons.length - 3} 件`;
      }

      // ACTION_REQUIRED format for Claude Code to take autonomous action
      const reason = `[ACTION_REQUIRED: SYSTEMATIZATION]\n教訓が見つかりましたが、仕組み化されていません。\n\n**検出された教訓** (${lessons.length}件):\n${excerptText}\n\nClaude Codeは以下のいずれかを実行してください:\n1. \`.claude/hooks/\` にフックを作成してブロック機構を実装\n2. \`.github/workflows/\` にCIチェックを追加\n3. \`.claude/scripts/\` にスクリプトを作成\n4. 上記が不要な場合はIssueを作成して理由を記録\n\nドキュメント（AGENTS.md等）への追記だけでは不十分です。`;

      await logHookExecution(
        HOOK_NAME,
        "warn",
        `lessons: ${lessons.length}, strong: ${hasStrong}, files: ${systematizedFiles.length}`,
        undefined,
        { sessionId },
      );

      // Print ACTION_REQUIRED to stderr for Claude Code to see and act on
      console.error(`[${HOOK_NAME}] ${reason}`);
      // Continue with approve instead of blocking (Issue #2026)
    } else if (lessons.length > 0) {
      // Lessons detected but systematization also detected
      result.systemMessage = `✅ [${HOOK_NAME}] 教訓検出: ${lessons.length}件, 仕組み化ファイル: ${systematizedFiles.length}件`;
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `lessons: ${lessons.length}, files: ${systematizedFiles.length}`,
        undefined,
        { sessionId },
      );
    } else {
      await logHookExecution(HOOK_NAME, "approve", "no lessons detected", undefined, { sessionId });
    }
  } catch (error) {
    await logHookExecution(HOOK_NAME, "approve", `error: ${formatError(error)}`, undefined, {
      sessionId,
    });
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
