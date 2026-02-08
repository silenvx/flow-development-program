/**
 * フック入力からのコンテキスト抽出ユーティリティ
 *
 * Why:
 *   ログ・分析のため、フック入力から一貫した形式で
 *   コンテキスト情報（ツール名、プレビュー等）を抽出する。
 *
 * What:
 *   - extractInputContext(): フック入力からコンテキストを抽出
 *   - mergeDetailsWithContext(): detailsとコンテキストをマージ
 *
 * Remarks:
 *   - hook_typeはtool_name/stop_hook_active等から自動推論
 *   - 入力プレビューは80文字で切り詰め
 *   - 空入力時はhook_type="Unknown"（SessionStartと区別）
 *
 * Changelog:
 *   - silenvx/dekita#1312: hook_typeを常に設定するよう修正
 *   - silenvx/dekita#1758: common.pyから分離
 *   - silenvx/dekita#2874: TypeScriptに移植
 *   - silenvx/dekita#3708: 切り詰め時に `...` を含めて maxLen 以内に収まるよう修正
 */

import type { HookInput, HookType } from "./types";

/**
 * 文字列を指定された最大長に切り詰める。
 *
 * `...` を追加しても maxLen を超えないようにする。
 * Issue #3708: 切り詰め後に `...` を追加すると maxLen を超える問題を修正
 *
 * @param str - 切り詰める文字列
 * @param maxLen - 最大長（`...` を含む）
 * @returns 切り詰められた文字列
 */
function truncateWithEllipsis(str: string, maxLen: number): string {
  if (str.length <= maxLen) {
    return str;
  }
  // maxLen が 3 以下の場合は `...` を追加せず切り詰めのみ
  if (maxLen <= 3) {
    return str.slice(0, maxLen);
  }
  return `${str.slice(0, maxLen - 3)}...`;
}

/**
 * Input context extracted from hook input for logging
 */
export interface InputContext {
  tool_name?: string;
  input_preview?: string;
  hook_type: HookType | "Unknown";
}

/**
 * Extract input context from hook input for logging.
 *
 * Extracts tool name and a preview of the input to help analyze
 * which operations triggered the hook.
 *
 * @param inputData - The JSON input received by the hook from stdin
 * @param maxPreviewLen - Maximum length for command/input preview (default: 80)
 * @returns Extracted context with tool_name, input_preview, and hook_type
 *
 * @example
 * ```ts
 * const data = { tool_name: "Bash", tool_input: { command: "gh pr create" } };
 * const ctx = extractInputContext(data);
 * // { tool_name: "Bash", input_preview: "gh pr create", hook_type: "PreToolUse" }
 * ```
 */
export function extractInputContext(inputData: HookInput, maxPreviewLen = 80): InputContext {
  const context: Partial<InputContext> = {};

  // Extract tool name
  const toolName = inputData.tool_name;
  if (toolName) {
    context.tool_name = toolName;
  }

  // Extract input preview based on tool type
  const toolInput = inputData.tool_input;
  if (toolInput && typeof toolInput === "object") {
    // Bash command
    if ("command" in toolInput && typeof toolInput.command === "string") {
      context.input_preview = truncateWithEllipsis(toolInput.command, maxPreviewLen);
    }
    // Edit/Write file path
    else if ("file_path" in toolInput && typeof toolInput.file_path === "string") {
      context.input_preview = toolInput.file_path;
    }
    // Read file path
    else if ("path" in toolInput && typeof toolInput.path === "string") {
      context.input_preview = toolInput.path;
    }
    // Generic: try to get first string value
    else {
      for (const value of Object.values(toolInput)) {
        if (typeof value === "string" && value) {
          context.input_preview = truncateWithEllipsis(value, maxPreviewLen);
          break;
        }
      }
    }
  }

  // Infer hook type
  // Issue #1312: Ensure hook_type is always set for proper logging
  if (toolName) {
    // Has tool_name = PreToolUse or PostToolUse
    if ("tool_output" in inputData) {
      context.hook_type = "PostToolUse";
    } else {
      context.hook_type = "PreToolUse";
    }
  } else if ("stop_hook_active" in inputData) {
    context.hook_type = "Stop";
  } else if ("notification" in inputData) {
    context.hook_type = "Notification";
  } else if ("user_prompt" in inputData) {
    context.hook_type = "UserPromptSubmit";
    if (typeof inputData.user_prompt === "string") {
      context.input_preview = truncateWithEllipsis(inputData.user_prompt, maxPreviewLen);
    }
  } else if (Object.keys(inputData).length === 0) {
    // Empty input (e.g., from JSON decode error) - use Unknown
    // parseHookInput() returns {} on decode errors, which should not
    // be conflated with a real SessionStart event
    context.hook_type = "Unknown";
  } else {
    // Has some data but no tool/stop/notification indicators = SessionStart
    // Real SessionStart events have at least session_id or other context
    context.hook_type = "SessionStart";
  }

  return context as InputContext;
}

/**
 * Merge existing details with input context for logging.
 *
 * Combines hook-specific details with extracted input context,
 * ensuring input context is always included in logs.
 *
 * @param details - Existing details dict from the hook (may be null/undefined)
 * @param inputContext - Context extracted via extractInputContext()
 * @returns Merged object with both details and input context
 *
 * @note If both details and inputContext contain the same key,
 *       the value from details takes precedence.
 */
export function mergeDetailsWithContext(
  details: Record<string, unknown> | null | undefined,
  inputContext: InputContext,
): Record<string, unknown> {
  const result: Record<string, unknown> = { ...inputContext };
  if (details) {
    Object.assign(result, details);
  }
  return result;
}

/**
 * PostToolUseフック用: ツール結果を取得する。
 *
 * 複数のフィールド名に対応（優先順位順）:
 * 1. tool_result (標準フィールド)
 * 2. tool_response (一部バージョンで使用)
 * 3. tool_output (互換性のためのフォールバック)
 *
 * @param hookInput - PostToolUseフックからの入力オブジェクト
 * @returns ツール結果。exit_code, stdout, stderrを含むオブジェクトの場合が多い。
 */
export function getToolResult(
  hookInput: Record<string, unknown>,
): Record<string, unknown> | string | null | undefined {
  if ("tool_result" in hookInput) {
    return hookInput.tool_result as Record<string, unknown> | string | null | undefined;
  }
  if ("tool_response" in hookInput) {
    return hookInput.tool_response as Record<string, unknown> | string | null | undefined;
  }
  return hookInput.tool_output as Record<string, unknown> | string | null | undefined;
}

/**
 * ツール結果からexit_codeを一貫した方法で取得する。
 *
 * デフォルト値は0（成功）:
 * - Claude Codeは通常、成功したコマンドではexit_codeを省略する
 * - api-operation-loggerやflow-progress-trackerの動作と一致
 *
 * @param toolResult - ツール結果辞書（またはnull/その他の型）
 * @param defaultValue - デフォルト値（オプション、デフォルト: 0）
 * @returns exit_codeの整数値（0は通常成功を意味する）
 */
export function getExitCode(
  toolResult: Record<string, unknown> | string | null | undefined,
  defaultValue = 0,
): number {
  if (!toolResult || typeof toolResult !== "object") {
    return defaultValue;
  }
  const exitCode = (toolResult as Record<string, unknown>).exit_code;
  if (typeof exitCode === "number") {
    return exitCode;
  }
  return defaultValue;
}
