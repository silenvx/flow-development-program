#!/usr/bin/env bun
/**
 * PRマージ後のreflect実行を強制する。
 *
 * Why:
 *   PRマージ後に/reflectを実行しないと、学習機会を逃し、同じ問題を繰り返す。
 *   トランスクリプトを解析してマージ後のreflect実行を検証する。
 *
 * What:
 *   - トランスクリプトから成功したgh pr mergeコマンドを検出
 *   - 各マージ後にSkill(reflect)呼び出しがあるか確認
 *   - 対応するreflect呼び出しがないマージがあればブロック
 *
 * State:
 *   - reads: transcript file
 *
 * Remarks:
 *   - post-merge-reflection-enforcerはreflect指示、本フックは実行検証
 *   - [IMMEDIATE]タグはトランスクリプトに記録されないため直接マージ検出を使用
 *
 * Changelog:
 *   - silenvx/dekita#2219: フック追加
 *   - silenvx/dekita#2269: 検出方法を直接マージ検出に変更
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { logHookExecution } from "../lib/logging";
import { isMergeSuccess } from "../lib/repo";
import { parseHookInput } from "../lib/session";
import { type ContentBlock, type TranscriptEntry, loadTranscript } from "../lib/transcript";

const HOOK_NAME = "immediate-action-check";

// Pattern to match gh pr merge command
const PR_MERGE_PATTERN = /gh\s+pr\s+merge/;

/**
 * Extract stdout from a tool_result content block.
 */
function extractStdout(block: ContentBlock): string {
  if (typeof block.stdout === "string") {
    return block.stdout;
  }

  const content = block.content;
  if (typeof content === "string") {
    return content;
  }

  if (!Array.isArray(content)) {
    return "";
  }

  return content
    .map((item) => {
      if (typeof item === "string") {
        return item;
      }
      if (typeof item === "object" && item !== null && item.type === "text") {
        return (item.text as string) ?? "";
      }
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

/**
 * Extract stderr from a tool_result content block.
 */
function extractStderr(block: ContentBlock): string {
  return typeof block.stderr === "string" ? block.stderr : "";
}

/**
 * Extract PR number from merge command or stdout.
 */
function extractPrNumber(command: string, stdout: string): string {
  // Allow flags between 'merge' and the number
  const prMatch = command.match(/gh\s+pr\s+merge\s+(?:.*?\s+)?(\d+)/);
  if (prMatch) {
    return prMatch[1];
  }

  // gh pr merge without explicit number shows PR as "#123" in stdout
  const stdoutPrMatch = stdout.match(/#(\d+)/);
  if (stdoutPrMatch) {
    return stdoutPrMatch[1];
  }

  return "?";
}

/**
 * Find successful PR merge commands in transcript.
 */
export function findPrMerges(transcript: TranscriptEntry[]): Array<[number, string]> {
  const merges: Array<[number, string]> = [];
  const toolUseMap = new Map<string, [number, string]>();

  for (let idx = 0; idx < transcript.length; idx++) {
    const content = transcript[idx].content;
    if (!Array.isArray(content)) {
      continue;
    }

    for (const block of content as ContentBlock[]) {
      if (typeof block !== "object" || block === null) {
        continue;
      }

      // Track Bash tool_use with gh pr merge
      if (block.type === "tool_use" && block.name === "Bash") {
        const command = (block.input?.command as string) ?? "";
        const toolUseId = (block.id as string) ?? "";
        if (PR_MERGE_PATTERN.test(command) && toolUseId) {
          toolUseMap.set(toolUseId, [idx, command]);
        }
      }

      // Check tool_result for merge success
      if (block.type === "tool_result") {
        const toolUseId = (block.tool_use_id as string) ?? "";
        const mapped = toolUseMap.get(toolUseId);
        if (!mapped) {
          continue;
        }

        const [mergeIdx, command] = mapped;
        const stdout = extractStdout(block);
        const stderr = extractStderr(block);
        const exitCode = (block.exit_code as number) ?? 0;

        if (isMergeSuccess(exitCode, stdout, command, stderr)) {
          merges.push([mergeIdx, extractPrNumber(command, stdout)]);
        }
      }
    }
  }

  return merges;
}

/**
 * Check if a Skill tool call for the action exists after startIdx.
 */
export function findSkillCallsAfter(
  transcript: TranscriptEntry[],
  startIdx: number,
  action: string,
): boolean {
  for (let idx = startIdx + 1; idx < transcript.length; idx++) {
    const entry = transcript[idx];
    if (entry.role !== "assistant") {
      continue;
    }

    const content = entry.content;
    if (!Array.isArray(content)) {
      continue;
    }

    for (const block of content as ContentBlock[]) {
      if (typeof block !== "object" || block === null) {
        continue;
      }

      if (block.type === "tool_use" && block.name === "Skill") {
        const skillName = (block.input?.skill as string) ?? "";
        if (skillName === action) {
          return true;
        }
      }
    }
  }

  return false;
}

/**
 * Check if all PR merges have corresponding reflect calls.
 */
function checkPostMergeReflection(transcript: TranscriptEntry[]): string[] {
  const merges = findPrMerges(transcript);
  if (merges.length === 0) {
    return [];
  }

  const unreflected: string[] = [];
  for (const [mergeIdx, prNumber] of merges) {
    if (!findSkillCallsAfter(transcript, mergeIdx, "reflecting-sessions")) {
      unreflected.push(prNumber);
    }
  }

  return unreflected;
}

async function main(): Promise<void> {
  const defaultResult = {};
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;

    // Skip if stop hook is already active (prevents infinite loops)
    if (inputData.stop_hook_active) {
      await logHookExecution(HOOK_NAME, "approve", "stop_hook_active", undefined, { sessionId });
      console.log(JSON.stringify(defaultResult));
      return;
    }

    const transcriptPath = inputData.transcript_path as string | undefined;
    if (!transcriptPath) {
      await logHookExecution(HOOK_NAME, "approve", "no transcript path provided", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(defaultResult));
      return;
    }

    const transcript = loadTranscript(transcriptPath);
    if (!transcript) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `transcript load failed (skipping check): ${transcriptPath}`,
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(defaultResult));
      return;
    }

    const unreflected = checkPostMergeReflection(transcript);

    if (unreflected.length > 0) {
      const prsStr = unreflected.map((pr) => `#${pr}`).join(", ");
      await logHookExecution(HOOK_NAME, "block", `unreflected PRs: ${prsStr}`, undefined, {
        sessionId,
      });

      const message = `PRマージ後の振り返りが未実行です: ${prsStr}\n\nPRをマージしましたが、/reflecting-sessions が呼び出されていません。\n\nセッション終了前に /reflecting-sessions を実行してください。\n[IMMEDIATE: /reflecting-sessions]`;

      console.log(
        JSON.stringify({
          decision: "block",
          reason: message,
        }),
      );
      return;
    }

    await logHookExecution(HOOK_NAME, "approve", "all merges reflected", undefined, { sessionId });
    console.log(JSON.stringify(defaultResult));
  } catch (error) {
    // フック実行の失敗でClaude Codeをブロックしない
    const errorMessage = error instanceof Error ? (error.stack ?? error.message) : String(error);
    await logHookExecution(HOOK_NAME, "error", `Hook error: ${errorMessage}`, undefined, {
      sessionId,
    }).catch(() => {
      // 意図的に空 - ログ記録のエラーは致命的ではないため
    });
    console.log(JSON.stringify(defaultResult));
  }
}

if (import.meta.main) {
  main().catch((e) => {
    console.error("Unhandled error in immediate_action_check:", e);
    console.log(JSON.stringify({}));
  });
}
