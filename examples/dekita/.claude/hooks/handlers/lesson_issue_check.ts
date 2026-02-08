#!/usr/bin/env bun
/**
 * 振り返り時に発見した教訓がIssue化されているか確認する。
 *
 * Why:
 *   振り返りで発見した教訓をIssue化しないと、問題が放置され
 *   同じ失敗を繰り返す。教訓のIssue化を強制することで改善を促進する。
 *
 * What:
 *   - 振り返りキーワード（五省、振り返り等）の存在を確認
 *   - [lesson]タグまたは教訓キーワードを検出
 *   - Issue参照がない教訓を発見したらブロック
 *
 * Remarks:
 *   - ブロック型フック（[lesson]タグにIssue参照がない場合ブロック）
 *   - Stopで発火（transcript分析）
 *   - [lesson]タグ検出は高精度（ブロック）、キーワード検出は警告のみ
 *   - セッション継続/コードブロック/Read出力等は誤検知防止で除外
 *   - reflection-completion-checkは振り返り実施を確認（責務分離）
 *
 * Changelog:
 *   - silenvx/dekita#2075: フック追加
 *   - silenvx/dekita#2094: 否定パターン除外（誤検知防止）
 *   - silenvx/dekita#2106: コードブロック除外
 *   - silenvx/dekita#2111: メタ議論パターン除外
 *   - silenvx/dekita#2120: セッション継続サマリ除外
 *   - silenvx/dekita#2137: フック自己参照ループ防止
 *   - silenvx/dekita#2155: Read出力除外
 *   - silenvx/dekita#2311: [lesson]タグベース検出に移行
 *   - silenvx/dekita#3160: TypeScript移行
 */

import { readFileSync } from "node:fs";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { blockAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "lesson-issue-check";

// =============================================================================
// Pattern Constants
// =============================================================================

// [lesson] tag for high-precision detection
const LESSON_TAG_PATTERN = /\[lesson\]/gi;

// Keywords that indicate a lesson or improvement was found (fallback, lower precision)
const LESSON_KEYWORDS = ["教訓", "反省点", "改善点", "次回への引き継ぎ", "問題点", "要改善"];

const LESSON_PATTERN = new RegExp(LESSON_KEYWORDS.join("|"), "g");

// Negation patterns indicating no actual lesson was found
const NEGATION_PATTERNS = [
  "発見され(?:ませんでした|なかった)",
  "発見して(?:いません|いない|おりません)",
  "見つかりませんでした",
  "見つからなかった",
  "見当たり(?:ません|ませんでした)",
  "見当たら(?:ない|なかった)",
  "ありませんでした",
  "特になし",
  "なし(?:[\\s。、．，.,]|$)",
  "特にありません",
  "確認され(?:ませんでした|なかった)",
  "認められ(?:ませんでした|なかった)",
  "問題なし(?:[\\s。、．，.,]|$)",
  "問題(?:ない|ありません|はありません)(?:[\\s。、．，.,]|$)",
];

const NEGATION_PATTERN = new RegExp(NEGATION_PATTERNS.join("|"));

// Meta-discussion patterns (discussing hook/keywords, not actual lessons)
const META_DISCUSSION_PATTERNS = [
  "<!-- HOOK_BLOCK_MESSAGE:[a-z-]+ -->",
  "誤検知",
  "フックが.*?トリガー",
  "フックの.*?検知",
  "キーワード.*?検出",
  "これは.*?(?:教訓|反省点|改善点|問題点).*?ではなく",
  "新しい(?:教訓|反省点|改善点|問題点).*?ではない",
  "会話履歴",
  "トランスクリプト",
  "(?:教訓|反省点|改善点|問題点|要改善).{0,5}(?:や|を).{0,10}(?:発見したら|洗い出し)",
  "(?:教訓|反省点|改善点|問題点|要改善).{0,5}の洗い出し",
  "(?:教訓|反省点|改善点|問題点)キーワード",
  "発見されたキーワード",
  "lesson-issue-check",
  "振り返りで発見した(?:教訓|反省点|改善点|問題点)がIssue化されていません",
  "教訓や反省点を発見したら",
  "##\\s*\\d+\\.\\s*(?:教訓|反省点|改善点|問題点|要改善)",
  "\\|\\s*\\*\\*(?:教訓|反省点|改善点|問題点|要改善)\\*\\*",
  "conversation is summarized",
  "session is being continued",
  "summarized below",
  "Previous session",
  "セッション継続",
  "要約され",
  "/reflecting-sessions.*?実行",
  "振り返りを実行",
  "Issue #\\d+.*?(?:完了|マージ|実装済)",
  "(?:完了|マージ|実装済).*?Issue #\\d+",
];

const META_DISCUSSION_PATTERN = new RegExp(META_DISCUSSION_PATTERNS.join("|"), "s");

// Work/review context patterns
const WORK_CONTEXT_PATTERNS = [
  "を修正",
  "に対応",
  "対応済",
  "のレビュー",
  "レビューコメント",
  "コメント対応",
  "を実装",
  "として登録",
  "をIssue化(?!していな|してな|しな|してませ|していませ|しておりませ|しておら)",
  "誤検知",
];

const WORK_CONTEXT_PATTERN = new RegExp(WORK_CONTEXT_PATTERNS.join("|"));

// Resolved keywords
const RESOLVED_KEYWORDS = [
  "済み",
  "完了",
  "解決",
  "クローズ",
  "マージ",
  "実装済",
  "対応済",
  "仕組み化済",
];

const RESOLVED_PATTERN = new RegExp(RESOLVED_KEYWORDS.join("|"));

// Reflection keywords
const REFLECTION_KEYWORDS = [
  "五省",
  "振り返り",
  "要件理解.*悖",
  "実装.*恥",
  "検証.*欠",
  "対応.*憾",
  "効率.*欠",
];

const REFLECTION_PATTERN = new RegExp(REFLECTION_KEYWORDS.join("|"));

// Reflection exclusion patterns
const REFLECTION_EXCLUSION_PATTERNS = [
  "振り返り(?:プロンプト|テンプレート|フォーマット|ツール|機能|フック)",
  "振り返り.{0,20}誤検知",
  "振り返り.{0,20}(?:Issue|PR|修正|対応)",
  "(?:Issue|PR).{0,30}振り返り(?:プロンプト|テンプレート|フォーマット|ツール|機能|フック)",
  "「振り返り」",
];

const REFLECTION_EXCLUSION_PATTERN = new RegExp(REFLECTION_EXCLUSION_PATTERNS.join("|"));

// Global session continuation patterns
const GLOBAL_SESSION_CONTINUATION_PATTERNS = [
  "session is being continued",
  "conversation is summarized",
  "summarized below",
  "<!-- HOOK_BLOCK_MESSAGE:",
];

const GLOBAL_SESSION_CONTINUATION_PATTERN = new RegExp(
  GLOBAL_SESSION_CONTINUATION_PATTERNS.join("|"),
  "i",
);

// Issue reference pattern
const ISSUE_REFERENCE_PATTERN = /(?:Issue\s*)?#(\d+)/i;

// Code block pattern
const CODE_BLOCK_PATTERN = /```[\s\S]*?```/g;

// System reminder pattern
const SYSTEM_REMINDER_PATTERN = /<system-reminder>[\s\S]*?<\/system-reminder>/g;

// Read tool output pattern
const READ_TOOL_OUTPUT_PATTERN =
  /Result of calling the Read tool: "[\s\S]*?"(?=\n\n|\n<|\n[^<\n]|\n$|$)/g;

// Summary section pattern
const SUMMARY_SECTION_PATTERN =
  /This session is being continued from a previous conversation[\s\S]*?Please continue the conversation[^\n]*/g;

const CONTEXT_CHARS = 200;

// =============================================================================
// Text Processing
// =============================================================================

function stripCodeBlocks(text: string): string {
  return text.replace(CODE_BLOCK_PATTERN, "");
}

function stripSystemReminders(text: string): string {
  return text.replace(SYSTEM_REMINDER_PATTERN, "");
}

function stripReadToolOutput(text: string): string {
  return text.replace(READ_TOOL_OUTPUT_PATTERN, "");
}

function stripSummarySection(text: string): string {
  return text.replace(SUMMARY_SECTION_PATTERN, "");
}

// =============================================================================
// Detection Functions
// =============================================================================

function shouldSkipLessonCheckGlobally(text: string): boolean {
  return GLOBAL_SESSION_CONTINUATION_PATTERN.test(text);
}

function hasReflectionKeywords(text: string): boolean {
  const match = REFLECTION_PATTERN.exec(text);
  if (!match) {
    return false;
  }

  // Check if ALL occurrences are within exclusion pattern ranges
  const exclusionRanges: Array<[number, number]> = [];
  const exclPattern = new RegExp(REFLECTION_EXCLUSION_PATTERN.source, "g");
  let exclMatch = exclPattern.exec(text);
  while (exclMatch !== null) {
    exclusionRanges.push([exclMatch.index, exclMatch.index + exclMatch[0].length]);
    exclMatch = exclPattern.exec(text);
  }

  // Check all reflection keyword occurrences
  const reflPattern = new RegExp(REFLECTION_PATTERN.source, "g");
  let reflMatch = reflPattern.exec(text);
  while (reflMatch !== null) {
    const pos = reflMatch.index;
    const isExcluded = exclusionRanges.some(([start, end]) => pos >= start && pos < end);
    if (!isExcluded) {
      return true;
    }
    reflMatch = reflPattern.exec(text);
  }

  return false;
}

function hasNegationContext(context: string): boolean {
  return NEGATION_PATTERN.test(context);
}

function hasMetaDiscussionContext(context: string): boolean {
  return META_DISCUSSION_PATTERN.test(context);
}

function hasWorkContext(context: string): boolean {
  return WORK_CONTEXT_PATTERN.test(context);
}

function hasIssueReference(context: string): boolean {
  return ISSUE_REFERENCE_PATTERN.test(context);
}

function hasResolvedIssueReference(context: string): boolean {
  const combined = new RegExp(
    `(${ISSUE_REFERENCE_PATTERN.source}).{0,30}(${RESOLVED_PATTERN.source})|` +
      `(${RESOLVED_PATTERN.source}).{0,30}(${ISSUE_REFERENCE_PATTERN.source})`,
    "is",
  );
  return combined.test(context);
}

interface TagMatch {
  tag: string;
  context: string;
}

function findLessonTags(text: string): TagMatch[] {
  const lessons: TagMatch[] = [];
  const pattern = new RegExp(LESSON_TAG_PATTERN.source, "gi");
  let match = pattern.exec(text);

  while (match !== null) {
    const start = Math.max(0, match.index - CONTEXT_CHARS);
    const end = Math.min(text.length, match.index + match[0].length + CONTEXT_CHARS);
    const context = text.slice(start, end);
    lessons.push({ tag: match[0], context });
    match = pattern.exec(text);
  }

  return lessons;
}

function findLessonMentions(text: string): TagMatch[] {
  const lessons: TagMatch[] = [];
  const pattern = new RegExp(LESSON_PATTERN.source, "g");
  let match = pattern.exec(text);

  while (match !== null) {
    const start = Math.max(0, match.index - CONTEXT_CHARS);
    const end = Math.min(text.length, match.index + match[0].length + CONTEXT_CHARS);
    const context = text.slice(start, end);
    lessons.push({ tag: match[0], context });
    match = pattern.exec(text);
  }

  return lessons;
}

function getTagsWithoutIssues(text: string): string[] {
  const tags = findLessonTags(text);
  const unissued: string[] = [];

  for (const { tag, context } of tags) {
    if (hasNegationContext(context)) continue;
    if (hasMetaDiscussionContext(context)) continue;
    if (hasResolvedIssueReference(context)) continue;
    if (hasWorkContext(context)) continue;
    if (!hasIssueReference(context)) {
      unissued.push(tag);
    }
  }

  return unissued;
}

function getLessonsWithoutIssues(text: string): string[] {
  const lessons = findLessonMentions(text);
  const unissued = new Set<string>();

  for (const { tag, context } of lessons) {
    if (hasNegationContext(context)) continue;
    if (hasResolvedIssueReference(context)) continue;
    if (hasMetaDiscussionContext(context)) continue;
    if (hasWorkContext(context)) continue;
    if (!hasIssueReference(context)) {
      unissued.add(tag);
    }
  }

  return Array.from(unissued);
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: { decision?: string } = {};
  let sessionId: string | undefined;

  try {
    const input = await parseHookInput();
    sessionId = input.session_id;

    // Get transcript content
    const transcriptPath = input.transcript_path ?? "";
    if (!transcriptPath) {
      await logHookExecution(HOOK_NAME, "approve", "No transcript path provided", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    let transcriptContent: string;
    try {
      transcriptContent = readFileSync(transcriptPath, "utf-8");
    } catch {
      await logHookExecution(HOOK_NAME, "approve", "Could not read transcript", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Global check for session continuation markers
    if (shouldSkipLessonCheckGlobally(transcriptContent)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "Session continuation detected (global check)",
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Strip various content to avoid false positives
    transcriptContent = stripSystemReminders(transcriptContent);
    transcriptContent = stripCodeBlocks(transcriptContent);
    transcriptContent = stripSummarySection(transcriptContent);
    transcriptContent = stripReadToolOutput(transcriptContent);

    // Only check if reflection was performed
    if (!hasReflectionKeywords(transcriptContent)) {
      await logHookExecution(HOOK_NAME, "approve", "No reflection detected in session", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Check for [lesson] tags without Issue references (high precision)
    const unissuedTags = getTagsWithoutIssues(transcriptContent);

    if (unissuedTags.length > 0) {
      const reason = `**振り返りで発見した教訓がIssue化されていません**
<!-- HOOK_BLOCK_MESSAGE:lesson-issue-check -->

検出: \`[lesson]\` タグ（${unissuedTags.length}件）にIssue参照がありません

教訓を発見したら、必ずIssue化してください:
\`\`\`bash
gh issue create --title "fix: [教訓の内容]" --label "bug,P2" --body "[詳細]"
\`\`\`

**重要**: AGENTS.mdの「仕組み化 = ドキュメント + 強制機構」に従い、
教訓はドキュメント追加だけでなく、フック/CI等の強制機構まで実装してください。`;

      await logHookExecution(
        HOOK_NAME,
        "block",
        `[lesson] tags without Issue: ${unissuedTags.length}`,
        undefined,
        { sessionId },
      );
      blockAndExit(HOOK_NAME, reason);
    }

    // Check for keyword mentions without Issue references (low precision - warning only)
    const unissuedLessons = getLessonsWithoutIssues(transcriptContent);

    if (unissuedLessons.length > 0) {
      const keywordsStr =
        unissuedLessons.slice(0, 3).join("、") +
        (unissuedLessons.length > 3 ? ` 他${unissuedLessons.length - 3}件` : "");

      const warningMsg = `[${HOOK_NAME}] 警告: キーワード検出（${keywordsStr}）
教訓がある場合は振り返りで \`[lesson]\` タグを使用してください。
例: [lesson] 根本原因を調査せずに表面的な対応をした → Issue #xxx`;

      console.error(warningMsg);

      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Keyword warning (not block): ${keywordsStr}`,
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    await logHookExecution(HOOK_NAME, "approve", "All lessons have Issue references", undefined, {
      sessionId,
    });
    console.log(JSON.stringify(result));
  } catch (error) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "error", `Hook error: ${formatError(error)}`, undefined, {
      sessionId,
    });
    // Don't block on errors
    console.log(JSON.stringify({}));
  }
}

if (import.meta.main) {
  main();
}
