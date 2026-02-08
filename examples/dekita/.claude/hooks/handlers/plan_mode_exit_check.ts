#!/usr/bin/env bun
/**
 * planファイル作成後のExitPlanMode呼び出し検証フック
 *
 * Why:
 *   Plan AIレビュー（plan_ai_review.ts）はExitPlanModeのPostToolUseで発火するが、
 *   ExitPlanModeが呼ばれないとレビューが実行されない。planファイルを書いた後に
 *   ExitPlanModeを呼ばずに実装に進むケースを検出して警告する。
 *
 * What:
 *   - セッション終了時（Stop）に発火
 *   - planファイルが新規作成されたかをチェック（transcript内のWriteツール使用）
 *   - ExitPlanModeが呼ばれたかをチェック（transcript内のツール使用）
 *   - planファイルが作成されたがExitPlanModeが呼ばれていない場合にブロック
 *
 * State:
 *   - reads: transcript_path（セッション履歴）
 *   - writes: .claude/logs/plan-mode-exit-check-metrics.jsonl
 *
 * Remarks:
 *   - ブロック型フック（ExitPlanMode未呼び出し時にセッション終了をブロック）
 *   - Stopフックで発火
 *
 * Changelog:
 *   - silenvx/dekita#3454: 初期実装
 *   - silenvx/dekita#3815: 警告からブロックに変更
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { logHookExecution } from "../lib/logging";
import { isSafeTranscriptPath } from "../lib/path_validation";
import { makeApproveResult, makeBlockResult, outputResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { type ToolUse, extractToolUses } from "../lib/transcript";

const HOOK_NAME = "plan-mode-exit-check";
const METRICS_LOG_FILE = ".claude/logs/plan-mode-exit-check-metrics.jsonl";

/** メトリクス結果タイプ */
type MetricsResult = "blocked" | "skipped" | "error";

/** メトリクスログエントリの型 */
interface PlanModeExitCheckMetrics {
  timestamp: string;
  session_id: string | null;
  result: MetricsResult;
  plan_files_written: number;
  exit_plan_mode_called: boolean;
  enter_plan_mode_called: boolean;
}

/**
 * メトリクスをJSONLファイルに記録
 */
function appendMetricsLog(
  projectDir: string,
  sessionId: string | null,
  metrics: Omit<PlanModeExitCheckMetrics, "timestamp" | "session_id">,
): void {
  try {
    const logPath = resolve(projectDir, METRICS_LOG_FILE);
    const logDir = dirname(logPath);

    if (!existsSync(logDir)) {
      mkdirSync(logDir, { recursive: true });
    }

    const entry: PlanModeExitCheckMetrics = {
      timestamp: new Date().toISOString(),
      session_id: sessionId,
      ...metrics,
    };

    appendFileSync(logPath, `${JSON.stringify(entry)}\n`);
  } catch {
    // メトリクス記録の失敗はサイレントに無視
  }
}

/**
 * planファイルパスかどうかを判定
 * @internal テスト用にexport
 */
export function isPlanFilePath(filePath: unknown): boolean {
  if (typeof filePath !== "string") {
    return false;
  }
  // .claude/plans/ または ~/.claude/plans/ への書き込みを検出
  // Windowsパス（バックスラッシュ）にも対応
  const normalizedPath = filePath.replace(/\\/g, "/");
  return normalizedPath.includes(".claude/plans/") && normalizedPath.endsWith(".md");
}

/**
 * planファイルへの書き込み操作かどうかを判定
 * Write, Edit ツールを対象とする
 * @internal テスト用にexport
 */
export function isPlanFileWrite(tool: ToolUse): boolean {
  if (!tool.input) {
    return false;
  }
  // Write/Editツール: file_path をチェック
  if (tool.name === "Write" || tool.name === "Edit") {
    return isPlanFilePath(tool.input.file_path);
  }
  return false;
}

/**
 * トランスクリプトからplanファイルへのWrite操作を検出
 * @internal テスト用にexport
 */
export function countPlanFileWrites(toolUses: ToolUse[]): number {
  let count = 0;
  for (const tool of toolUses) {
    if (isPlanFileWrite(tool)) {
      count++;
    }
  }
  return count;
}

/**
 * 最後のplanファイル書き込みのインデックスを取得
 * @returns インデックス（見つからない場合は-1）
 * @internal テスト用にexport
 */
export function getLastPlanFileWriteIndex(toolUses: ToolUse[]): number {
  let lastIndex = -1;
  for (let i = 0; i < toolUses.length; i++) {
    if (isPlanFileWrite(toolUses[i])) {
      lastIndex = i;
    }
  }
  return lastIndex;
}

/**
 * 最後のExitPlanModeのインデックスを取得
 * @returns インデックス（見つからない場合は-1）
 * @internal テスト用にexport
 */
export function getLastExitPlanModeIndex(toolUses: ToolUse[]): number {
  let lastIndex = -1;
  for (let i = 0; i < toolUses.length; i++) {
    if (toolUses[i].name === "ExitPlanMode") {
      lastIndex = i;
    }
  }
  return lastIndex;
}

/**
 * 最後のEnterPlanModeのインデックスを取得
 * @returns インデックス（見つからない場合は-1）
 */
function getLastEnterPlanModeIndex(toolUses: ToolUse[]): number {
  let lastIndex = -1;
  for (let i = 0; i < toolUses.length; i++) {
    if (toolUses[i].name === "EnterPlanMode") {
      lastIndex = i;
    }
  }
  return lastIndex;
}

/**
 * メイン処理
 */
async function main(): Promise<void> {
  const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
  const result = makeApproveResult(HOOK_NAME);

  try {
    const input = await parseHookInput();
    const sessionId = input.session_id || null;

    // Stopフック再帰防止
    if (input.stop_hook_active) {
      outputResult(result);
      return;
    }

    // transcriptが取得できない場合はスキップ
    const transcriptPath = input.transcript_path;
    if (!transcriptPath) {
      appendMetricsLog(projectDir, sessionId, {
        result: "skipped",
        plan_files_written: 0,
        exit_plan_mode_called: false,
        enter_plan_mode_called: false,
      });
      outputResult(result);
      return;
    }

    // セキュリティ: パストラバーサル攻撃を防止
    if (!isSafeTranscriptPath(transcriptPath)) {
      appendMetricsLog(projectDir, sessionId, {
        result: "skipped",
        plan_files_written: 0,
        exit_plan_mode_called: false,
        enter_plan_mode_called: false,
      });
      outputResult(result);
      return;
    }

    if (!existsSync(transcriptPath)) {
      appendMetricsLog(projectDir, sessionId, {
        result: "skipped",
        plan_files_written: 0,
        exit_plan_mode_called: false,
        enter_plan_mode_called: false,
      });
      outputResult(result);
      return;
    }

    // トランスクリプトを読み込み
    let transcriptContent: string;
    try {
      transcriptContent = readFileSync(transcriptPath, "utf-8");
    } catch {
      appendMetricsLog(projectDir, sessionId, {
        result: "error",
        plan_files_written: 0,
        exit_plan_mode_called: false,
        enter_plan_mode_called: false,
      });
      outputResult(result);
      return;
    }

    // ツール使用を抽出
    const allToolUses = extractToolUses(transcriptContent);

    // planファイルへのWrite回数をカウント
    const planFilesWritten = countPlanFileWrites(allToolUses);

    // ExitPlanModeとEnterPlanModeの呼び出しをチェック
    const exitPlanModeCalled = allToolUses.some((t) => t.name === "ExitPlanMode");
    const enterPlanModeCalled = allToolUses.some((t) => t.name === "EnterPlanMode");

    // planファイルが書かれていない場合はスキップ
    if (planFilesWritten === 0) {
      appendMetricsLog(projectDir, sessionId, {
        result: "skipped",
        plan_files_written: 0,
        exit_plan_mode_called: exitPlanModeCalled,
        enter_plan_mode_called: enterPlanModeCalled,
      });
      outputResult(result);
      return;
    }

    // EnterPlanModeが呼ばれていない場合はスキップ
    // （planモード外でのplanファイル編集は正常なワークフロー）
    if (!enterPlanModeCalled) {
      appendMetricsLog(projectDir, sessionId, {
        result: "skipped",
        plan_files_written: planFilesWritten,
        exit_plan_mode_called: exitPlanModeCalled,
        enter_plan_mode_called: false,
      });
      outputResult(result);
      return;
    }

    // インデックスで順序をチェック
    const lastPlanWriteIndex = getLastPlanFileWriteIndex(allToolUses);
    const lastExitPlanModeIndex = getLastExitPlanModeIndex(allToolUses);
    const lastEnterPlanModeIndex = getLastEnterPlanModeIndex(allToolUses);

    // planモードが現在アクティブかどうかを判定
    // アクティブ = 最後のEnterが最後のExitより後（または一度もExitされていない）
    const isPlanModeActive = !exitPlanModeCalled || lastEnterPlanModeIndex > lastExitPlanModeIndex;

    // plan書き込みが現在のplanモードセッション内かどうかを判定
    const writeInCurrentSession = lastPlanWriteIndex > lastEnterPlanModeIndex;

    // スキップするケース:
    // 1. planモードがアクティブでない（最後のExitが最後のEnterより後）
    // 2. plan書き込みが現在のplanモードセッション外（最後のEnterより前）
    //
    // 警告するケース:
    // - planモードがアクティブ かつ plan書き込みがそのセッション内にある
    if (!isPlanModeActive || !writeInCurrentSession) {
      appendMetricsLog(projectDir, sessionId, {
        result: "skipped",
        plan_files_written: planFilesWritten,
        exit_plan_mode_called: exitPlanModeCalled,
        enter_plan_mode_called: enterPlanModeCalled,
      });
      outputResult(result);
      return;
    }

    // planモード中に書き込みがあったが、ExitPlanModeが呼ばれていない場合にブロック
    // （上記条件を通過した時点で、planモードがアクティブかつ書き込みがセッション内）
    appendMetricsLog(projectDir, sessionId, {
      result: "blocked",
      plan_files_written: planFilesWritten,
      exit_plan_mode_called: exitPlanModeCalled,
      enter_plan_mode_called: enterPlanModeCalled,
    });

    await logHookExecution(HOOK_NAME, "block", "ExitPlanMode not called after plan file write", {
      plan_files_written: planFilesWritten,
      enter_plan_mode_called: enterPlanModeCalled,
      last_plan_write_index: lastPlanWriteIndex,
      last_exit_plan_mode_index: lastExitPlanModeIndex,
      last_enter_plan_mode_index: lastEnterPlanModeIndex,
    });

    // ブロック
    const blockResult = makeBlockResult(
      HOOK_NAME,
      `planファイルが ${planFilesWritten} 件作成されましたが、ExitPlanModeが呼ばれていません。

Plan AIレビューはExitPlanMode時に実行されます。planモードを正しく使用すると、
実装前に設計の問題点を検出できます。

**対処法**: ExitPlanModeを呼び出してください。`,
    );
    outputResult(blockResult);
  } catch (error) {
    appendMetricsLog(projectDir, null, {
      result: "error",
      plan_files_written: 0,
      exit_plan_mode_called: false,
      enter_plan_mode_called: false,
    });
    console.error(`[${HOOK_NAME}] Hook error:`, error);
    outputResult(result);
  }
}

if (import.meta.main) {
  main();
}
