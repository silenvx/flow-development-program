#!/usr/bin/env bun
/**
 * Phase完了時に次Phaseの開始を強制する。
 *
 * Why:
 *   Phase分割タスクでPhase 1完了後にセッションが止まり、
 *   ユーザーが指摘するまで次Phaseが開始されない問題があった。
 *   AGENTS.mdに原則として記載されているが、強制機構がなかった。
 *
 * What:
 *   - 計画ファイル（~/.claude/plans/*.md, .claude/plans/*.md）をスキャン
 *   - フェーズ構造を検出（## フェーズX: または ## Phase X:）
 *   - 完了したフェーズの後に未完了フェーズがある場合、ブロック
 *
 * Remarks:
 *   - Stopフックとして実行
 *   - 直近1時間以内に更新されたファイルのみ対象
 *   - コードブロック内のチェックボックスは除外
 *
 * Changelog:
 *   - silenvx/dekita#2873: フック追加
 */

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { basename, join } from "node:path";
import { logHookExecution } from "../lib/logging";
import { isInIndentedCodeBlock, splitByFencedCodeBlocks } from "../lib/markdown";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "phase-progression-guard";
const ONE_HOUR_SECONDS = 3600;

/** フェーズ情報 */
export interface Phase {
  name: string;
  completedCount: number;
  incompleteCount: number;
}

/** 計画ファイルの分析結果 */
export interface PlanAnalysis {
  file: string;
  phases: Phase[];
  completedPhaseWithNextIncomplete: {
    completed: string;
    next: string;
  } | null;
}

/**
 * フェーズヘッダーを検出する正規表現
 * ## フェーズ1: 基盤構築 or ## Phase 1: Foundation
 */
export const PHASE_HEADER_REGEX = /^##\s+(?:フェーズ|Phase)\s*(\d+)[：:]\s*(.+)$/i;

/**
 * チェックボックスパターン
 */
export const CHECKBOX_COMPLETED = /^\s*[-*+]\s*\[x\]/i;
export const CHECKBOX_INCOMPLETE = /^\s*[-*+]\s*\[\s*\]/;

/**
 * 計画ファイルの内容からフェーズ構造を解析する（テスト用にエクスポート）
 *
 * @param content ファイル内容
 * @returns フェーズ一覧
 */
export function analyzePlanContent(content: string): Phase[] {
  const segments = splitByFencedCodeBlocks(content);

  const phases: Phase[] = [];
  let currentPhase: Phase | null = null;
  let isFirstSegment = true;

  for (const segment of segments) {
    if (segment.isCodeBlock) {
      isFirstSegment = false;
      continue;
    }

    for (let lineIdx = 0; lineIdx < segment.lines.length; lineIdx++) {
      const line = segment.lines[lineIdx];

      // インデントコードブロック内はスキップ
      if (isInIndentedCodeBlock(segment.lines, lineIdx, isFirstSegment)) {
        continue;
      }

      // フェーズヘッダーの検出
      const headerMatch = line.match(PHASE_HEADER_REGEX);
      if (headerMatch) {
        // 前のフェーズを保存
        if (currentPhase) {
          phases.push(currentPhase);
        }
        currentPhase = {
          name: `${headerMatch[1]}: ${headerMatch[2].trim()}`,
          completedCount: 0,
          incompleteCount: 0,
        };
        continue;
      }

      // チェックボックスの検出（フェーズ内のみ）
      if (currentPhase) {
        if (CHECKBOX_COMPLETED.test(line)) {
          currentPhase.completedCount++;
        } else if (CHECKBOX_INCOMPLETE.test(line)) {
          currentPhase.incompleteCount++;
        }
      }
    }

    isFirstSegment = false;
  }

  // 最後のフェーズを保存
  if (currentPhase) {
    phases.push(currentPhase);
  }

  return phases;
}

/**
 * 完了したフェーズの後に未完了フェーズがあればブロックすべきか判定（テスト用にエクスポート）
 *
 * @param phases フェーズ一覧
 * @returns ブロックすべき場合はtrue
 */
export function shouldBlockPhaseProgression(phases: Phase[]): boolean {
  for (let i = 0; i < phases.length - 1; i++) {
    const currentPh = phases[i];
    const nextPh = phases[i + 1];

    // 現在のフェーズが完了（チェックボックスが1つ以上あり、全て完了）
    const isCurrentCompleted = currentPh.completedCount > 0 && currentPh.incompleteCount === 0;

    // 次のフェーズが未完了（未完了チェックボックスがある）
    const isNextIncomplete = nextPh.incompleteCount > 0;

    if (isCurrentCompleted && isNextIncomplete) {
      return true;
    }
  }
  return false;
}

/**
 * 完了したフェーズと次の未完了フェーズの情報を取得
 *
 * @param phases フェーズ一覧
 * @returns 完了フェーズと次の未完了フェーズの情報、またはnull
 */
export function findCompletedWithNextIncomplete(
  phases: Phase[],
): PlanAnalysis["completedPhaseWithNextIncomplete"] {
  for (let i = 0; i < phases.length - 1; i++) {
    const currentPh = phases[i];
    const nextPh = phases[i + 1];

    const isCurrentCompleted = currentPh.completedCount > 0 && currentPh.incompleteCount === 0;
    const isNextIncomplete = nextPh.incompleteCount > 0;

    if (isCurrentCompleted && isNextIncomplete) {
      return {
        completed: currentPh.name,
        next: nextPh.name,
      };
    }
  }
  return null;
}

/**
 * 計画ファイルからフェーズ構造を解析する
 */
function analyzePlanFile(filePath: string): PlanAnalysis | null {
  try {
    const content = readFileSync(filePath, "utf-8");
    const phases = analyzePlanContent(content);

    // フェーズがない場合はnull
    if (phases.length === 0) {
      return null;
    }

    const completedPhaseWithNextIncomplete = findCompletedWithNextIncomplete(phases);

    return {
      file: filePath,
      phases,
      completedPhaseWithNextIncomplete,
    };
  } catch (error) {
    // ファイル読み取りエラーはスキップ
    const errorMsg = error instanceof Error ? error.message : String(error);
    console.error(`[${HOOK_NAME}] Error reading ${filePath}: ${errorMsg}`);
    return null;
  }
}

/**
 * 計画ディレクトリからファイルを検索し分析する
 */
function findAndAnalyzePlanFiles(planDir: string): PlanAnalysis[] {
  const results: PlanAnalysis[] = [];

  if (!existsSync(planDir)) {
    return results;
  }

  const now = Date.now();
  const oneHourAgo = now - ONE_HOUR_SECONDS * 1000;

  try {
    const files = readdirSync(planDir);

    for (const file of files) {
      if (!file.endsWith(".md")) {
        continue;
      }

      const filePath = join(planDir, file);

      try {
        const stat = statSync(filePath);
        // 直近1時間以内に更新されたファイルのみ
        if (stat.mtimeMs < oneHourAgo) {
          continue;
        }

        const analysis = analyzePlanFile(filePath);
        if (analysis?.completedPhaseWithNextIncomplete) {
          results.push(analysis);
        }
      } catch (error) {
        const errorMsg = error instanceof Error ? error.message : String(error);
        console.error(`[${HOOK_NAME}] Error processing ${filePath}: ${errorMsg}`);
      }
    }
  } catch (error) {
    // ディレクトリ読み取りエラー
    const errorMsg = error instanceof Error ? error.message : String(error);
    console.error(`[${HOOK_NAME}] Error reading ${planDir}: ${errorMsg}`);
  }

  return results;
}

/**
 * Main entry point
 */
async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const hookInput = await parseHookInput();
    const ctx = createHookContext(hookInput);
    sessionId = ctx.sessionId;

    // Stop hookでのみ実行
    const hookType = hookInput.hook_event_name ?? "";
    if (hookType !== "Stop") {
      console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
      return;
    }

    // ~/.claude/plans/ と .claude/plans/ を検索
    const homePlansDir = join(homedir(), ".claude", "plans");
    const projectPlansDir = join(process.cwd(), ".claude", "plans");

    const analyses = [
      ...findAndAnalyzePlanFiles(homePlansDir),
      ...findAndAnalyzePlanFiles(projectPlansDir),
    ];

    if (analyses.length === 0) {
      console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
      logHookExecution(HOOK_NAME, "approve", "No phase progression needed", undefined, {
        sessionId,
      });
      return;
    }

    // ブロックメッセージを構築
    const blockMessages: string[] = ["🚫 Phase完了後に次Phaseを開始してください:", ""];

    for (const analysis of analyses) {
      const fileName = basename(analysis.file);
      const info = analysis.completedPhaseWithNextIncomplete!;

      blockMessages.push(`📋 ${fileName}:`);
      blockMessages.push(`  ✅ フェーズ${info.completed} は完了しています`);
      blockMessages.push(`  ⏳ フェーズ${info.next} を開始してください`);
      blockMessages.push("");
    }

    blockMessages.push(
      "AGENTS.md「Phase分割タスクの自動進行」に従い、次Phaseを開始してからセッションを終了してください。",
    );

    // ブロック
    const result = makeBlockResult(HOOK_NAME, blockMessages.join("\n"));
    console.log(JSON.stringify(result));
    logHookExecution(
      HOOK_NAME,
      "block",
      `Completed phase with next incomplete: ${analyses.map((a) => a.file).join(", ")}`,
      undefined,
      { sessionId },
    );
  } catch (error) {
    // Fail-open: エラー時は承認
    const errorMsg = error instanceof Error ? error.message : String(error);
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
    logHookExecution(HOOK_NAME, "approve", `Error: ${errorMsg}`, undefined, { sessionId });
  }
}

if (import.meta.main) {
  main();
}
