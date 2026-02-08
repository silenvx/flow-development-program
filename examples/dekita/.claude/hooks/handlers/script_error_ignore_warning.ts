#!/usr/bin/env bun
/**
 * スクリプトエラー無視防止フック
 *
 * Why:
 *   Bashコマンドがexit code != 0で終了した際、Claudeが「完了しました」等の
 *   肯定的表現で応答すると、エラーを見落とす可能性がある。
 *
 * What:
 *   - Bash失敗時: 失敗情報を状態ファイルに保存（PostToolUse:Bash）
 *   - 次のツール呼び出し時: transcript末尾のClaude応答を確認
 *   - エラーを正しく認識していない場合は警告を出す
 *
 * State:
 *   - writes: /tmp/claude-hooks/script-error-state/<session_id>/script_error_state.json
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、警告を提示）
 *   - PostToolUse:Bashで発火
 *   - 状態保持型のアプローチで、Bash実行後のClaude応答を確認
 *
 * Changelog:
 *   - silenvx/dekita#3417: 初期実装
 *   - silenvx/dekita#3871: transcript追跡を改善（失敗インデックスを記録）
 */

import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { logHookExecution } from "../lib/logging";
import { getToolResultAsObject, isSafeSessionId, parseHookInput } from "../lib/session";
import { truncate } from "../lib/strings";
import { type TranscriptEntry, loadTranscript } from "../lib/transcript";

const HOOK_NAME = "script-error-ignore-warning";

// 状態ファイルの有効期限（5分）
const STATE_TIMEOUT_MS = 5 * 60 * 1000;

// 状態ディレクトリのベースパス
const STATE_BASE_DIR = join(tmpdir(), "claude-hooks", "script-error-state");

/**
 * 状態ファイルの型定義
 * Issue #3871: failureTranscriptIndexを追加して、失敗後にユーザーが
 * 別の質問をしても正しいassistant応答をチェックできるようにする
 */
interface ScriptErrorState {
  command: string;
  exitCode: number;
  timestamp: number;
  stdout: string;
  stderr: string;
  /** 失敗時のtranscriptインデックス（Issue #3871） */
  failureTranscriptIndex?: number;
}

/**
 * 例外パターン（意図的なエラー無視）
 * これらのパターンを含むコマンドはエラーでも警告しない
 * 注: /dev/nullへのリダイレクトはexit codeを変更しないため除外
 */
const EXCEPTION_PATTERNS: RegExp[] = [
  /\|\|\s*true/,
  /\|\|\s*echo/,
  /\|\|\s*:/,
  /set\s+\+e/,
  /\|\|\s*exit\s+0/,
];

/**
 * エラー認識パターン（正しく認識している場合）
 * Claude応答にこれらが含まれていれば、エラーを認識していると判断
 * 注: "Issue #123" や "issue-3417" のようなGitHub Issue参照/ブランチ名は除外
 */
const ERROR_RECOGNITION_PATTERNS: RegExp[] = [
  /失敗/,
  /エラー/,
  /問題/,
  /うまくいかな/,
  /動作しな/,
  /できな[いかっ]/,
  /完了し(?:ていません|なかった|ません)/,
  /成功し(?:ていません|なかった|ません)/,
  /\bfails?\b|\bfailed\b|\bfailure\b/i,
  /\berror/i,
  /\b(?:an|the|this)\s+issue\b/i, // "an issue", "the issue", "this issue" のみマッチ
  /\bproblem/i,
  /\bdid(?:n'?t|\s+not)\s*(?:work|succeed)/i,
  /\bcan(?:'t|not)\b/i,
  /exit\s*code\s*[1-9]/i,
  /ステータス\s*[1-9]/,
  /終了コード\s*[1-9]/,
];

/**
 * 肯定的表現パターン（警告対象）
 * Claude応答にエラー認識がなく、これらが含まれる場合は警告
 * 注: 「しました」は事実報告に使われることも多いため除外
 * 注: 「完了」「成功」は助動詞付きの形式に限定（「完了していません」等の誤検知防止）
 */
const POSITIVE_EXPRESSION_PATTERNS: RegExp[] = [
  /完了し(?:ました|ています)/,
  /成功し(?:ました|ています)/,
  /できました/,
  /終わりました/,
  /済みました/,
  /\bcompleted?\b/i,
  /\bsuccessfully?\b/i,
  /\b(?:is |was |has been )?done\b/i,
  /\bfinished\b/i,
];

/**
 * コマンドが例外パターンに該当するかチェック
 */
export function isExceptionCommand(command: string): boolean {
  return EXCEPTION_PATTERNS.some((pattern) => pattern.test(command));
}

/**
 * テキストがエラーを認識しているかチェック
 */
export function hasErrorRecognition(text: string): boolean {
  return ERROR_RECOGNITION_PATTERNS.some((pattern) => pattern.test(text));
}

/**
 * テキストが肯定的表現を含むかチェック
 */
export function hasPositiveExpression(text: string): boolean {
  return POSITIVE_EXPRESSION_PATTERNS.some((pattern) => pattern.test(text));
}

/**
 * 警告メッセージを生成
 * Issue #3873: 重複した警告メッセージ生成ロジックを共通関数に抽出
 */
function createWarningMessage(state: ScriptErrorState): string {
  return `⚠️ [${HOOK_NAME}] エラーを無視していませんか？

前回のコマンドが exit code ${state.exitCode} で終了しましたが、
肯定的な表現で応答しています。

コマンド: ${truncate(state.command, 100)}
${state.stderr ? `エラー出力: ${truncate(state.stderr, 200)}` : ""}

確認してください:
- 本当に処理は成功していますか？
- エラーを意図的に無視する場合は \`|| true\` を使用してください`;
}

/**
 * 状態ファイルのディレクトリを取得
 */
function getStateDir(sessionId: string): string {
  return join(STATE_BASE_DIR, sessionId);
}

/**
 * 状態ファイルのパスを取得
 */
function getStateFilePath(sessionId: string): string {
  return join(getStateDir(sessionId), "script_error_state.json");
}

/**
 * 状態を保存
 */
function saveState(sessionId: string, state: ScriptErrorState): void {
  const stateDir = getStateDir(sessionId);
  mkdirSync(stateDir, { recursive: true });
  const stateFile = getStateFilePath(sessionId);
  writeFileSync(stateFile, JSON.stringify(state, null, 2));
}

/**
 * 状態を読み込み
 */
function loadState(sessionId: string): ScriptErrorState | null {
  const stateFile = getStateFilePath(sessionId);
  if (!existsSync(stateFile)) {
    return null;
  }

  try {
    const content = readFileSync(stateFile, "utf-8");
    return JSON.parse(content) as ScriptErrorState;
  } catch {
    return null;
  }
}

/**
 * 状態をクリア
 */
function clearState(sessionId: string): void {
  const stateFile = getStateFilePath(sessionId);
  if (existsSync(stateFile)) {
    try {
      unlinkSync(stateFile);
    } catch {
      // 削除失敗は無視
    }
  }
}

/**
 * 失敗インデックス以降の最初のassistant応答を取得
 * Issue #3871: 失敗後にユーザーが別の質問をしても、失敗に対応する応答を
 * 正しくチェックできるようにする
 *
 * @param transcript 読み込み済みのtranscript配列（nullの場合はnullを返す）
 * @param failureIndex 失敗時のtranscriptインデックス（0の場合は最新を返す）
 */
export function getAssistantResponseAfterFailure(
  transcript: TranscriptEntry[] | null,
  failureIndex: number,
): string | null {
  if (!transcript || transcript.length === 0) {
    return null;
  }

  // failureIndexが0または未指定の場合は、後方互換性のため最新を探す
  if (failureIndex === 0) {
    for (let i = transcript.length - 1; i >= 0; i--) {
      const entry = transcript[i];
      if (entry.role === "assistant") {
        return extractAssistantText(entry);
      }
    }
    return null;
  }

  // 失敗インデックス以降の最初のassistant応答を探す
  for (let i = failureIndex; i < transcript.length; i++) {
    const entry = transcript[i];
    if (entry.role === "assistant") {
      return extractAssistantText(entry);
    }
  }

  return null;
}

/**
 * transcript entryからテキストを抽出
 */
export function extractAssistantText(entry: { content?: unknown }): string {
  if (!entry.content) {
    return "";
  }
  if (typeof entry.content === "string") {
    return entry.content;
  }
  // contentが配列の場合、textブロックを抽出
  if (Array.isArray(entry.content)) {
    const texts = entry.content
      .filter(
        (block): block is { text: string } =>
          typeof block === "object" && block !== null && "text" in block,
      )
      .map((block) => block.text);
    return texts.join("\n");
  }
  return "";
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };

  try {
    const inputData = await parseHookInput();
    const sessionId = inputData.session_id;
    const toolName = inputData.tool_name;
    const transcriptPath = inputData.transcript_path;

    // セッションIDがない場合はスキップ
    if (!sessionId || !isSafeSessionId(sessionId)) {
      console.log(JSON.stringify(result));
      return;
    }

    // Issue #3871: transcriptを一度だけ読み込む（パフォーマンス改善）
    const transcript = transcriptPath ? loadTranscript(transcriptPath) : null;

    // Bashツールの場合: まず前回の失敗状態をチェックしてから、新しい状態を保存
    if (toolName === "Bash") {
      // 前回のBash失敗があれば、まずそれをチェック（連続Bash対応）
      const previousState = loadState(sessionId);
      let shouldClearPreviousState = false;
      if (previousState && Date.now() - previousState.timestamp <= STATE_TIMEOUT_MS) {
        // Issue #3871: 失敗インデックス以降の最初のassistant応答を取得
        const failureIndex = previousState.failureTranscriptIndex ?? 0;
        const latestResponse = getAssistantResponseAfterFailure(transcript, failureIndex);
        // 応答がある場合のみチェック（空や取得できない場合は状態を維持）
        if (latestResponse && latestResponse.length > 0) {
          shouldClearPreviousState = true;
          // エラーを認識していない && 肯定的表現がある場合は警告
          if (!hasErrorRecognition(latestResponse) && hasPositiveExpression(latestResponse)) {
            result.systemMessage = createWarningMessage(previousState);
            await logHookExecution(
              HOOK_NAME,
              "warn",
              "positive_expression_after_error",
              {
                command: previousState.command.slice(0, 100),
                exitCode: previousState.exitCode,
              },
              { sessionId },
            );
          }
        }
      } else if (previousState) {
        // タイムアウトの場合はクリア
        shouldClearPreviousState = true;
      }
      // 前回の状態はチェック完了した場合のみクリア
      if (shouldClearPreviousState) {
        clearState(sessionId);
      }

      const toolResult = getToolResultAsObject(inputData);
      const exitCode = typeof toolResult.exit_code === "number" ? toolResult.exit_code : undefined;

      if (exitCode === 0 || exitCode === undefined) {
        // 成功または不明な場合は終了
        await logHookExecution(
          HOOK_NAME,
          "approve",
          undefined,
          { reason: "success" },
          { sessionId },
        );
        console.log(JSON.stringify(result));
        return;
      }

      // エラー発生
      const command =
        typeof inputData.tool_input?.command === "string" ? inputData.tool_input.command : "";
      const stdout = typeof toolResult.stdout === "string" ? toolResult.stdout : "";
      const stderr = typeof toolResult.stderr === "string" ? toolResult.stderr : "";

      // 例外パターンに該当する場合はスキップ
      if (isExceptionCommand(command)) {
        await logHookExecution(
          HOOK_NAME,
          "approve",
          undefined,
          { reason: "exception_pattern" },
          { sessionId },
        );
        console.log(JSON.stringify(result));
        return;
      }

      // 状態を保存
      // Issue #3871: 現在のtranscriptの長さを記録して、
      // 次回チェック時に失敗後のassistant応答を特定できるようにする
      const currentTranscriptLength = transcript?.length ?? 0;
      const state: ScriptErrorState = {
        command,
        exitCode,
        timestamp: Date.now(),
        stdout: stdout.slice(0, 500),
        stderr: stderr.slice(0, 500),
        failureTranscriptIndex: currentTranscriptLength,
      };
      saveState(sessionId, state);
      await logHookExecution(
        HOOK_NAME,
        "approve",
        undefined,
        { reason: "state_saved", exitCode },
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Bash以外のツールの場合: 前回のBash失敗を確認
    const state = loadState(sessionId);
    if (!state) {
      // 保存された状態がない場合はスキップ
      console.log(JSON.stringify(result));
      return;
    }

    // タイムアウトチェック
    if (Date.now() - state.timestamp > STATE_TIMEOUT_MS) {
      clearState(sessionId);
      await logHookExecution(
        HOOK_NAME,
        "approve",
        undefined,
        { reason: "state_timeout" },
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Issue #3871: 失敗インデックス以降の最初のassistant応答を取得
    const failureIndex = state.failureTranscriptIndex ?? 0;
    const latestResponse = getAssistantResponseAfterFailure(transcript, failureIndex);
    if (!latestResponse) {
      // 応答が取得できない場合はスキップ（状態は維持）
      console.log(JSON.stringify(result));
      return;
    }

    // 状態をクリア（一度チェックしたらクリア）
    clearState(sessionId);

    // エラーを認識しているかチェック
    if (hasErrorRecognition(latestResponse)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        undefined,
        { reason: "error_recognized" },
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // 肯定的表現を含むかチェック
    if (hasPositiveExpression(latestResponse)) {
      // 警告を出す
      result.systemMessage = createWarningMessage(state);
      await logHookExecution(
        HOOK_NAME,
        "warn",
        "positive_expression_after_error",
        {
          command: state.command.slice(0, 100),
          exitCode: state.exitCode,
        },
        { sessionId },
      );
    } else {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        undefined,
        { reason: "no_positive_expression" },
        { sessionId },
      );
    }
  } catch (error) {
    // フック実行の失敗でClaude Codeをブロックしない
    console.error(`[${HOOK_NAME}] Error:`, error);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
