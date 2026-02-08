#!/usr/bin/env bun
/**
 * 保守的行動パターンを検出してブロック。
 *
 * Why:
 *   ユーザーが「大規模変更OK・後方互換性不要・コスト度外視・理想追求」と明示したにもかかわらず、
 *   Claudeが不必要にスコープ縮小・後方互換性考慮・コスト削減・妥協案提案する傾向がある。
 *
 * What:
 *   - セッション終了時（Stop）に発火
 *   - transcriptからassistantメッセージを抽出
 *   - 4カテゴリ（スコープ縮小/後方互換性/コスト削減/妥協）の保守的パターンを検出
 *   - 否定文脈（「互換性は不要」等）を除外
 *   - 直近3メッセージのみチェック（修正後の再ブロック回避）
 *   - 検出時はブロック（exit 2）
 *
 * Remarks:
 *   - ブロック型フック（exit 2）
 *   - 否定文脈・引用文脈は除外して誤検知を防止
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { createContext, getSessionId, parseHookInput } from "../lib/session";
import type { ContentBlock, TranscriptEntry } from "../lib/transcript";
import { loadTranscript } from "../lib/transcript";

const HOOK_NAME = "conservative-behavior-check";

// --- Detection Patterns (4 categories) ---

/** A. スコープ縮小パターン */
export const SCOPE_REDUCTION_PATTERNS = [
  /最小限の変更/,
  /影響範囲を(?:抑|小さく)/,
  /小さな変更に留め/,
  /変更を最小化/,
  /スコープを(?:絞|限定)/,
  /段階的に(?:進|実装|移行|導入)/,
  /minimal changes?/i,
  /keep.*change.*(?:small|minimal)/is,
  /limit.*scope.*(?:change|work)/is,
  /incremental.*(?:approach|delivery)/is,
];

/** B. 後方互換性パターン */
export const BACKWARD_COMPAT_PATTERNS = [
  /後方互換/,
  /既存の動作を壊さない/,
  /互換性を維持/,
  /下位互換/,
  /破壊的変更を避け/,
  /既存.*影響.*最小/s,
  /backward[-_ ]?compat/i,
  /breaking change.*avoid/is,
  /avoid.*breaking change/is,
  /maintain.*compat/is,
];

/** C. コスト削減パターン */
export const COST_REDUCTION_PATTERNS = [
  /コストを(?:考慮|抑え)/,
  /簡易的に/,
  /簡略化して/,
  /手軽に/,
  /軽量な/,
  /オーバーヘッドを(?:避|抑)/,
  /lightweight.*alternative/is,
  /development.*overhead/is,
];

/** D. 妥協パターン */
export const COMPROMISE_PATTERNS = [
  /現実的には/,
  /妥協案/,
  /暫定的に/,
  /とりあえず/,
  /practical.*approach/is,
  /compromise/i,
  /interim/i,
  /good enough/i,
  /pragmatic/i,
];

export const ALL_PATTERNS: { category: string; patterns: RegExp[] }[] = [
  { category: "スコープ縮小", patterns: SCOPE_REDUCTION_PATTERNS },
  { category: "後方互換性", patterns: BACKWARD_COMPAT_PATTERNS },
  { category: "コスト削減", patterns: COST_REDUCTION_PATTERNS },
  { category: "妥協", patterns: COMPROMISE_PATTERNS },
];

/** カテゴリ別の否定キーワード */
const NEGATION_KEYWORDS_BY_CATEGORY: Record<string, string> = {
  スコープ縮小: "最小|範囲|変更|スコープ|段階|minimal|small|limit|scope|incremental",
  後方互換性: "互換|破壊|既存|backward|breaking|compat|maintain",
  コスト削減: "コスト|簡易|簡略|手軽|軽量|オーバーヘッド|lightweight|overhead|cost",
  妥協: "妥協|現実|暫定|とりあえず|compromise|practical|interim|pragmatic",
};

const JA_NEGATION = "しない|不要|必要(?:は)?ない|気にしない|度外視|無視|考慮(?:外|しない)";
// Word boundaries prevent matching "no" in "Now", "normal", etc.
const EN_NEGATION = "\\bnot\\b|\\bno\\b|\\bnever\\b|\\bignore\\b|\\bskip\\b|\\bunnecessary\\b|n't";

const NEGATION_PATTERN_CACHE = new Map<string, RegExp[]>();

/** Generate negation patterns for a specific category (cached) */
export function getNegationPatterns(category: string): RegExp[] {
  const cached = NEGATION_PATTERN_CACHE.get(category);
  if (cached) return cached;
  const keywords = NEGATION_KEYWORDS_BY_CATEGORY[category];
  if (!keywords) return [];
  const patterns = [
    new RegExp(`(?:${JA_NEGATION})[\\s\\S]{0,60}(?:${keywords})`, "i"),
    new RegExp(`(?:${keywords})[\\s\\S]{0,60}(?:${JA_NEGATION})`, "i"),
    new RegExp(`(?:${EN_NEGATION})[\\s\\S]{0,60}(?:${keywords})`, "i"),
    new RegExp(`(?:${keywords})[\\s\\S]{0,60}(?:${EN_NEGATION})`, "i"),
  ];
  NEGATION_PATTERN_CACHE.set(category, patterns);
  return patterns;
}

/**
 * Extract text from Claude's assistant messages.
 * Each assistant entry is merged into a single message to ensure slice(-3)
 * operates on transcript entries, not individual text blocks.
 */
export function extractClaudeMessages(transcript: TranscriptEntry[]): string[] {
  const messages: string[] = [];
  for (const entry of transcript) {
    if (entry.role !== "assistant") continue;
    const content = entry.content;
    if (!content) continue;
    if (Array.isArray(content)) {
      // Merge all text blocks within this entry into a single message
      const merged = content
        .filter(
          (block): block is ContentBlock & { type: "text"; text: string } =>
            block != null &&
            typeof block === "object" &&
            block.type === "text" &&
            typeof block.text === "string" &&
            block.text.length > 0,
        )
        .map((block) => block.text)
        .join("\n")
        .trim();
      if (merged) messages.push(merged);
    } else if (typeof content === "string" && content.length > 0) {
      messages.push(content);
    }
  }
  return messages;
}

/**
 * Check if the text around the match is in a negation context for the given category.
 */
export function isNegationContext(
  text: string,
  matchStart: number,
  matchLength: number,
  category: string,
): boolean {
  const windowStart = Math.max(0, matchStart - 60);
  const windowEnd = Math.min(text.length, matchStart + matchLength + 60);
  const window = text.slice(windowStart, windowEnd);

  for (const pattern of getNegationPatterns(category)) {
    if (pattern.test(window)) return true;
  }
  return false;
}

export interface Detection {
  category: string;
  excerpt: string;
}

/**
 * Find conservative behavior patterns in messages.
 */
export function findConservativePatterns(messages: string[]): Detection[] {
  const detections: Detection[] = [];

  // Only check recent messages to allow for corrections during the session
  const recentMessages = messages.slice(-3);

  for (const msg of recentMessages) {
    // Remove quoted lines (lines starting with >) to prevent false positives from quoting
    const cleanMsg = msg
      .split("\n")
      .filter((line) => !line.trim().startsWith(">"))
      .join("\n");

    if (!cleanMsg.trim()) continue;

    for (const { category, patterns } of ALL_PATTERNS) {
      let categoryDetected = false;
      for (const pattern of patterns) {
        // Create a global version of the regex to find all matches
        const globalPattern = new RegExp(
          pattern.source,
          pattern.flags.includes("g") ? pattern.flags : `${pattern.flags}g`,
        );
        let match: RegExpExecArray | null = globalPattern.exec(cleanMsg);

        while (match !== null) {
          if (isNegationContext(cleanMsg, match.index, match[0].length, category)) {
            match = globalPattern.exec(cleanMsg);
            continue;
          }

          const start = Math.max(0, match.index - 20);
          const end = Math.min(cleanMsg.length, match.index + match[0].length + 20);
          let excerpt = cleanMsg.slice(start, end).trim();
          if (start > 0) excerpt = `...${excerpt}`;
          if (end < cleanMsg.length) excerpt = `${excerpt}...`;

          detections.push({ category, excerpt });
          categoryDetected = true;
          break; // One detection per pattern is enough
        }
        if (categoryDetected) break; // One detection per pattern category per message
      }
    }
  }

  return detections;
}

async function main(): Promise<void> {
  const result: Record<string, unknown> = { decision: "approve" };

  try {
    const data = await parseHookInput();
    const ctx = createContext(data);
    const sessionId = getSessionId(ctx) ?? "unknown";

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
    const detections = findConservativePatterns(claudeMessages);

    if (detections.length > 0) {
      const byCategory = new Map<string, string[]>();
      for (const d of detections) {
        const list = byCategory.get(d.category) ?? [];
        list.push(d.excerpt);
        byCategory.set(d.category, list);
      }

      const lines: string[] = [];
      for (const [cat, excerpts] of byCategory) {
        lines.push(`【${cat}】`);
        for (const e of excerpts.slice(0, 2)) {
          lines.push(`  - ${e.slice(0, 80)}`);
        }
        if (excerpts.length > 2) {
          lines.push(`  - ... 他 ${excerpts.length - 2} 件`);
        }
      }

      const reason = `保守的行動パターンを検出しました (${detections.length}件)。

${lines.join("\n")}

このプロジェクトでは:
- 大規模変更OK
- 後方互換性不要
- 実装コスト度外視
- 理想を追求

【対処法】
保守的な提案を撤回し、理想的な実装に修正してください。`;

      await logHookExecution(HOOK_NAME, "block", `detected: ${detections.length}`, undefined, {
        sessionId,
      });

      result.decision = "block";
      result.reason = reason;
      console.log(JSON.stringify(result));
      process.exit(2);
    }

    await logHookExecution(HOOK_NAME, "approve", "no conservative patterns", undefined, {
      sessionId,
    });
  } catch (error) {
    await logHookExecution(HOOK_NAME, "approve", `error: ${formatError(error)}`);
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
