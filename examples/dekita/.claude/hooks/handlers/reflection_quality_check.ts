#!/usr/bin/env bun
/**
 * 振り返りの形式的評価を防ぐ（ブロック回数との矛盾検出、改善点Issue化強制）。
 *
 * Why:
 *   形式的・表面的な振り返りは実質的な改善につながらない。
 *   客観的メトリクス（ブロック回数）と主観的評価（「問題なし」）の
 *   矛盾を検出し、根本原因分析を強制する。また、改善点を発見しても
 *   Issue化しなければ忘れられるため、Issue参照を強制する。
 *
 * What:
 *   - フック実行ログからブロック回数をカウント
 *   - トランスクリプトから振り返り内容を分析
 *   - 矛盾検出（3回以上ブロックなのに「問題なし」）時にブロック
 *   - 改善点があるのにIssue参照がない場合にブロック
 *
 * Remarks:
 *   - reflection-completion-checkはキーワード存在確認、本フックは品質検証
 *   - ブロック型フック（警告ではなくブロック）
 *   - 根本原因パターン（なぜ、根本原因、原因は）があれば通過
 *
 * Changelog:
 *   - silenvx/dekita#1945: フック追加（形式的振り返り防止）
 *   - silenvx/dekita#1958: 設計簡素化（レビューコメント数チェック削除）
 *   - silenvx/dekita#2005: 警告からブロックに変更
 *   - silenvx/dekita#2354: 改善点Issue化強制を追加
 *   - silenvx/dekita#2362: Issue参照パターン改善
 *   - silenvx/dekita#3161: TypeScript移行
 */

import { readFileSync } from "node:fs";
import { EXECUTION_LOG_DIR } from "../lib/common";
import { formatError } from "../lib/format_error";
import { logHookExecution, readSessionLogEntries } from "../lib/logging";
import { isSafeTranscriptPath } from "../lib/path_validation";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "reflection_quality_check";

// Threshold for contradiction detection
// Warn if blocked 3+ times but claims "no problem"
const BLOCK_WARNING_THRESHOLD = 3;

// Patterns indicating "no problem" in reflection
// Note: Japanese negation forms include ない/なし/ありません/なかった
const NO_PROBLEM_PATTERNS = [
  "問題なし",
  "特に問題.*(ない|なし|ありません|なかった)", // "特に問題がある"を誤検知しないように否定形を含める
  "問題は.*(ない|なし|ありません)",
  "改善点.*(なし|ない|ありません)",
  "反省点.*(なし|ない|ありません)",
  "[45]/5.*全項目",
  "全項目.*[45]/5",
];

// Pre-compiled pattern for performance
const COMPILED_NO_PROBLEM_PATTERN = new RegExp(NO_PROBLEM_PATTERNS.join("|"));

// Patterns indicating root cause analysis
const ROOT_CAUSE_PATTERNS = [
  "なぜ.{0,50}?(した|ブロック|忘れ|スキップ)", // "なぜブロックされたか" etc.
  "根本原因",
  "原因は",
  "原因として",
  "原因が",
  "問題の本質",
  "本質的な問題",
  "構造的な問題",
  "パターン.{0,50}(検出|発見)", // "パターンを検出"
  "3回自問",
  "他にないか",
];

// Pre-compiled pattern for root cause detection
const COMPILED_ROOT_CAUSE_PATTERN = new RegExp(ROOT_CAUSE_PATTERNS.join("|"));

// Patterns indicating improvement points that need Issue creation
const IMPROVEMENT_PATTERNS = [
  "改善点(?!.*(?:なし|ない|ありません))", // Exclude "改善点なし" etc.
  "問題点(?!.*(?:なし|ない|ありません))", // Exclude "問題点なし" etc.
  "すべきだった",
  "べきだった",
  "対策.{0,20}(必要|検討)",
  "今後.{0,20}(対応|改善|検討)",
  "次回.{0,20}(注意|気をつけ|確認)",
];

// Pre-compiled pattern for improvement detection
const COMPILED_IMPROVEMENT_PATTERN = new RegExp(IMPROVEMENT_PATTERNS.join("|"));

// Pattern for Issue references (e.g., #123, Issue #123, issue-123)
const ISSUE_REFERENCE_PATTERN = /#\d+|Issue\s*#?\d+|issue[/-]\d+/i;

// Patterns indicating an improvement has been addressed without Issue
const ISSUE_NOT_NEEDED_PATTERNS = [
  "Issue不要",
  "Issue化不要",
  "issue不要",
  "ルール再確認で対応",
  "対応済み",
  "解決済み",
  "クローズ済み",
  "軽微.{0,20}対応可能",
  "仕組み.{0,20}既存",
];

// Pre-compiled pattern for "Issue not needed" detection
const COMPILED_ISSUE_NOT_NEEDED_PATTERN = new RegExp(ISSUE_NOT_NEEDED_PATTERNS.join("|"));

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Get block count for the current session from local logs.
 */
async function getBlockCount(sessionId: string | null | undefined): Promise<number> {
  if (!sessionId) {
    return 0;
  }

  // EXECUTION_LOG_DIR is already an absolute path from lib/common
  const entries = await readSessionLogEntries(EXECUTION_LOG_DIR, "hook-execution", sessionId);

  let count = 0;
  for (const entry of entries) {
    if (entry.decision === "block") {
      count++;
    }
  }

  return count;
}

/**
 * Check if transcript contains "no problem" patterns in reflection context.
 */
function checkTranscriptForNoProblem(transcriptContent: string): boolean {
  let reflectionContext = false;

  for (const line of transcriptContent.split("\n")) {
    const isReflectionLine = /五省|振り返り|反省/.test(line);
    const isSectionHeader = /^#{1,3}\s/.test(line);

    if (isReflectionLine) {
      reflectionContext = true;
    }

    if (reflectionContext) {
      if (COMPILED_NO_PROBLEM_PATTERN.test(line)) {
        return true;
      }

      if (isSectionHeader && !isReflectionLine) {
        reflectionContext = false;
      }
    }
  }

  return false;
}

/**
 * Check if transcript contains root cause analysis patterns.
 */
function checkRootCauseAnalysis(transcriptContent: string): boolean {
  let reflectionContext = false;

  for (const line of transcriptContent.split("\n")) {
    const isReflectionLine = /五省|振り返り|反省|問題|分析/.test(line);
    const isSectionHeader = /^#{1,3}\s/.test(line);

    if (isReflectionLine) {
      reflectionContext = true;
    }

    if (reflectionContext) {
      if (COMPILED_ROOT_CAUSE_PATTERN.test(line)) {
        return true;
      }

      if (isSectionHeader && !isReflectionLine) {
        reflectionContext = false;
      }
    }
  }

  return false;
}

/**
 * Check if reflection contains high scores (4-5) for all items.
 */
function checkHighScores(transcriptContent: string): boolean {
  const scorePattern = /[45]\s*\/\s*5/g;
  let reflectionContext = false;
  let scoreCount = 0;

  for (const line of transcriptContent.split("\n")) {
    const isReflectionLine = /五省|振り返り|反省/.test(line);
    const isSectionHeader = /^#{1,3}\s/.test(line);

    if (isReflectionLine) {
      reflectionContext = true;
    }

    if (reflectionContext) {
      const matches = line.match(scorePattern);
      if (matches) {
        scoreCount += matches.length;
      }

      if (isSectionHeader && !isReflectionLine) {
        reflectionContext = false;
      }
    }
  }

  return scoreCount >= 5;
}

/**
 * Check if transcript contains improvement points without Issue references.
 */
function checkImprovementsWithoutIssues(transcriptContent: string): string[] {
  const improvementsWithoutIssues: string[] = [];
  let reflectionContext = false;
  let firstImprovementFound = false;
  let followupAddressedCount = 0;

  const lines = transcriptContent.split("\n");

  for (const line of lines) {
    const isReflectionLine = /五省|振り返り|反省|改善|問題/.test(line);
    const isSectionHeader = /^\s*#{1,3}\s/.test(line);

    const isImprovementLine = COMPILED_IMPROVEMENT_PATTERN.test(line);
    const hasIssueRef = ISSUE_REFERENCE_PATTERN.test(line);
    const hasNotNeeded = COMPILED_ISSUE_NOT_NEEDED_PATTERN.test(line);

    if (firstImprovementFound) {
      if (!isImprovementLine) {
        if (hasIssueRef || hasNotNeeded) {
          followupAddressedCount++;
        }
      } else if (!reflectionContext && (hasIssueRef || hasNotNeeded)) {
        followupAddressedCount++;
      }
    }

    if (isReflectionLine) {
      reflectionContext = true;
    }

    if (reflectionContext) {
      if (isSectionHeader) {
        if (!isReflectionLine) {
          reflectionContext = false;
        }
        continue;
      }

      if (isImprovementLine) {
        firstImprovementFound = true;
        if (!hasIssueRef && !hasNotNeeded) {
          let displayLine = line.trim().slice(0, 80);
          if (line.trim().length > 80) {
            displayLine += "...";
          }
          improvementsWithoutIssues.push(displayLine);
        }
      }
    }
  }

  // Issue #2362: If follow-up addressed count >= unaddressed improvement count,
  // consider all improvements addressed
  if (
    followupAddressedCount >= improvementsWithoutIssues.length &&
    improvementsWithoutIssues.length > 0
  ) {
    return [];
  }

  return improvementsWithoutIssues;
}

/**
 * Determine if reflection should be blocked due to contradiction.
 */
function shouldBlockReflection(
  blockCount: number,
  hasNoProblem: boolean,
  hasHighScores: boolean,
  hasRootCause: boolean,
): string | null {
  const hasPositiveSubjective = hasNoProblem || hasHighScores;
  if (!hasPositiveSubjective) {
    return null;
  }

  if (blockCount < BLOCK_WARNING_THRESHOLD) {
    return null;
  }

  if (hasRootCause) {
    return null;
  }

  return `[reflection-quality-check] 振り返りの矛盾を検出 - セッション終了をブロック\n\nブロック ${blockCount}回 なのに「問題なし」/高評価で、根本原因の分析がありません。\n\n**「本当に問題なかったですか？」**\n\n以下のいずれかを実行してください:\n  1. 各ブロックについて「なぜその行動をしたか」を分析する\n  2. 根本原因を特定してIssue化する\n  3. 「他にないか？」を3回自問した結果を記述する\n\nヒント: execute.md の「0. 必須チェック」セクションを参照\n`;
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  let result = makeApproveResult(HOOK_NAME);

  try {
    const input = await parseHookInput();
    const ctx = createHookContext(input);
    const sessionId = ctx.sessionId;

    // Get objective metric (block count only - no external API)
    const blockCount = await getBlockCount(sessionId);

    // Get transcript content
    const transcriptPath = input.transcript_path ?? "";
    let transcriptContent = "";

    if (transcriptPath && isSafeTranscriptPath(transcriptPath)) {
      try {
        transcriptContent = readFileSync(transcriptPath, "utf-8");
      } catch {
        // Best effort - transcript read failure should not break hook
      }
    }

    // Check for contradictions and root cause analysis
    const hasNoProblem = checkTranscriptForNoProblem(transcriptContent);
    const hasHighScores = checkHighScores(transcriptContent);
    const hasRootCause = checkRootCauseAnalysis(transcriptContent);

    const blockMessage = shouldBlockReflection(
      blockCount,
      hasNoProblem,
      hasHighScores,
      hasRootCause,
    );

    // Issue #2354: Check for improvements without Issue references
    const improvementsWithoutIssues = checkImprovementsWithoutIssues(transcriptContent);

    if (blockMessage) {
      // BLOCK session end if contradiction without root cause analysis
      result = makeBlockResult(HOOK_NAME, blockMessage, ctx);
      await logHookExecution(
        HOOK_NAME,
        "block",
        `Contradiction detected without root cause: ${blockCount} blocks`,
        undefined,
        { sessionId: sessionId ?? undefined },
      );
    } else if (improvementsWithoutIssues.length > 0) {
      // BLOCK session end if improvements found without Issue references
      let improvementList = improvementsWithoutIssues
        .slice(0, 5)
        .map((line) => `  - ${line}`)
        .join("\n");

      if (improvementsWithoutIssues.length > 5) {
        improvementList += `\n  ... 他 ${improvementsWithoutIssues.length - 5} 件`;
      }

      const blockMsg = `[reflection-quality-check] 改善点のIssue化漏れを検出 - セッション終了をブロック\n\n振り返りで改善点を発見しましたが、Issue参照がありません。\n\n**該当箇所:**\n${improvementList}\n\n**execute.md原則**: 「改善点が見つかった場合、severity に関わらず全てIssue化が必須」\n\n以下のいずれかを実行してください:\n  1. 各改善点についてIssueを作成し、振り返りに Issue #番号 を追記\n  2. 改善点が軽微でルール再確認で対応可能な場合、その旨を明記\n\n`;

      result = makeBlockResult(HOOK_NAME, blockMsg, ctx);
      await logHookExecution(
        HOOK_NAME,
        "block",
        `Improvements without Issue references: ${improvementsWithoutIssues.length} found`,
        undefined,
        { sessionId: sessionId ?? undefined },
      );
    } else {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `No contradiction or substantive analysis: blocks=${blockCount}, root_cause=${hasRootCause}`,
        undefined,
        { sessionId: sessionId ?? undefined },
      );
    }
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    await logHookExecution(HOOK_NAME, "error", `Hook error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
  });
}
