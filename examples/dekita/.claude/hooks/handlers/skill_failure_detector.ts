#!/usr/bin/env bun
/**
 * Skill呼び出し失敗を検出して調査・Issue化を促す。
 *
 * Why:
 *   Skillツールが失敗した場合（ファイル不在等）、手動で回避するだけでは
 *   根本問題が解決されない。失敗を検出して問題のIssue化を強制する。
 *
 * What:
 *   - Skillツール実行後（PostToolUse:Skill）に発火
 *   - ツール結果からエラーパターンを検出
 *   - 失敗検出時は警告メッセージを表示し、Issue作成を促す
 *   - worktree削除後の失敗ケースへのヒントも提供
 *
 * Remarks:
 *   - 警告型（ブロックせず、情報提供と行動促進）
 *   - エラーパターンは isSkillFailure() で定義
 *   - 問題を手動回避せず、必ずIssue化することを要求
 *
 * Changelog:
 *   - silenvx/dekita#2417: フック追加（Skill失敗時の自動検出）
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { getToolResult, parseHookInput } from "../lib/session";

const HOOK_NAME = "skill-failure-detector";

export interface FailureResult {
  isFailure: boolean;
  reason: string;
}

/**
 * Check if the Skill tool result indicates a failure.
 */
export function isSkillFailure(toolResult: unknown): FailureResult {
  if (!toolResult || typeof toolResult !== "object") {
    return { isFailure: false, reason: "" };
  }

  // Check for common error patterns in Skill results
  const resultText = JSON.stringify(toolResult);

  const errorPatterns: Array<[RegExp, string]> = [
    [/File does not exist/i, "ファイルが見つかりません"],
    [/Directory does not exist/i, "ディレクトリが見つかりません"],
    [/tool_use_error/i, "ツール実行エラー"],
    [/error.*reading file/i, "ファイル読み込みエラー"],
    [/No such file or directory/i, "ファイル/ディレクトリが存在しません"],
  ];

  for (const [pattern, reason] of errorPatterns) {
    if (pattern.test(resultText)) {
      return { isFailure: true, reason };
    }
  }

  return { isFailure: false, reason: "" };
}

interface HookResult {
  continue: boolean;
  decision?: string;
  reason?: string;
  systemMessage?: string;
}

async function main(): Promise<void> {
  let result: HookResult = { continue: true };

  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;
    const toolName = inputData.tool_name || "";

    if (toolName !== "Skill") {
      console.log(JSON.stringify(result));
      return;
    }

    const toolResult = getToolResult(inputData);
    const toolInput = inputData.tool_input || {};
    const skillName = (toolInput as { skill?: string }).skill || "";

    const { isFailure, reason } = isSkillFailure(toolResult);

    if (isFailure) {
      await logHookExecution(
        HOOK_NAME,
        "block",
        `Skill '${skillName}' failed: ${reason}`,
        {
          skill: skillName,
          reason,
        },
        { sessionId },
      );

      const message = `⚠️ **Skill呼び出しが失敗しました**\n\n- Skill: \`${skillName}\`\n- 原因: ${reason}\n\n**必須アクション**:\n1. 失敗の根本原因を調査してください\n2. 問題をIssue化してください（手動で回避しないでください）\n3. Issueを作成してから、代替手段で作業を続行してください\n\n💡 ヒント: worktree削除後にSkillが失敗する場合は、\n   オリジナルリポジトリに移動してから再試行してください。`;

      result = {
        decision: "block",
        continue: true, // Don't stop, but force investigation
        reason: message,
        systemMessage: message,
      };
      console.log(JSON.stringify(result));
      return;
    }
  } catch (e) {
    await logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(e)}`, undefined, {
      sessionId,
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
