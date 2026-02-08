#!/usr/bin/env bun
/**
 * 特定操作の前にSkill使用を強制。
 *
 * Why:
 *   worktree作成やPR作成時にSkillの手順を確認せずに進めると、
 *   推奨手順を見落とす。操作前にSkill使用を強制することで品質を担保する。
 *
 * What:
 *   - 特定コマンド実行前（PreToolUse:Bash）に発火
 *   - OPERATION_SKILL_MAPで定義されたコマンドパターンを検出
 *   - transcriptから当該Skillの使用履歴を確認
 *   - Skill未使用の場合はブロック
 *
 * Remarks:
 *   - ブロック型フック（Skill未使用時はブロック）
 *   - managing-development: worktree add, gh pr create, gh pr merge
 *   - reviewing-code: レビューコメント操作, スレッド解決
 *   - Python版: skill_usage_reminder.py
 *
 * Changelog:
 *   - silenvx/dekita#2355: フック追加（Skill未使用時のブロック）
 *   - silenvx/dekita#2752: gh pr mergeパターン追加
 *   - silenvx/dekita#2917: TypeScript版初期実装
 *   - silenvx/dekita#3092: fork/compact後のセッションでもSkill検出可能に
 */

import { existsSync, readFileSync } from "node:fs";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { isSafeTranscriptPath } from "../lib/path_validation";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { createContext, parseHookInput } from "../lib/session";
import type { HookContext, HookResult } from "../lib/types";

const HOOK_NAME = "skill-usage-reminder";

/**
 * Operation to required Skill mapping.
 * Each pattern maps to a tuple of [skill_name, operation_description].
 *
 * Issue #3760: Patterns must match command boundaries to avoid false positives.
 * Commands embedded in strings (e.g., Issue body containing "gh pr merge") should NOT match.
 * Use (?:^|[;&|][^\S\n]*) to match command start or after shell operators.
 * Use [^\S\n]+ instead of \s+ to avoid matching newlines (Issue #3777).
 */
const OPERATION_SKILL_MAP: Map<RegExp, [string, string]> = new Map([
  // Match at start of command or after shell operators (;, &, &&, |, ||)
  // Use [^\S\n]+ (non-newline whitespace) to avoid matching across lines (Issue #3777)
  [/(?:^|[;&|][^\S\n]*)git[^\S\n]+worktree[^\S\n]+add\b/, ["managing-development", "worktree作成"]],
  [/(?:^|[;&|][^\S\n]*)gh[^\S\n]+pr[^\S\n]+create\b/, ["managing-development", "PR作成"]],
  [/(?:^|[;&|][^\S\n]*)gh[^\S\n]+pr[^\S\n]+merge\b/, ["managing-development", "PRマージ"]],
  // Review-related commands - gh api with pulls path (not issues)
  // Use non-greedy .*? to skip flags, and [^\/]+ for PR ID to support shell variables
  [
    /(?:^|[;&|][^\S\n]*)gh[^\S\n]+api[^\S\n]+(?:.*?[^\S\n]+)?\/?repos\/[^\/]+\/[^\/]+\/pulls\/[^\/]+\/comments\b/,
    ["reviewing-code", "レビューコメント操作"],
  ],
  // Match batch_resolve_threads.ts with optional path prefix
  [
    /(?:^|[;&|][^\S\n]*)(?:(?:bun[^\S\n]+run|npx)[^\S\n]+)?(?:\S*\/)?batch_resolve_threads\.ts\b/,
    ["reviewing-code", "スレッド解決"],
  ],
]);

interface ToolUseContent {
  type?: string;
  name?: string;
  input?: { skill?: string };
}

interface TranscriptEntry {
  sessionId?: string;
  message?: { content?: ToolUseContent[] };
}

/**
 * Extract Skill names used in the current session and its ancestors from transcript.
 *
 * After fork-session or context compaction, the session ID changes but the transcript
 * retains entries from previous sessions. Since a transcript file represents a single
 * conversation lineage (possibly across multiple sessions), we extract Skills from all
 * entries without session ID filtering.
 *
 * @param transcriptPath - Path to the transcript JSONL file.
 * @param _sessionId - Current session ID (unused, kept for API compatibility).
 * @returns Set of Skill names used in this transcript.
 */
export function getSkillUsageFromTranscript(
  transcriptPath: string | undefined,
  _sessionId: string | null | undefined,
): Set<string> {
  const skillsUsed = new Set<string>();

  if (!transcriptPath || !isSafeTranscriptPath(transcriptPath)) {
    return skillsUsed;
  }

  try {
    if (!existsSync(transcriptPath)) {
      return skillsUsed;
    }

    const content = readFileSync(transcriptPath, "utf-8");

    // Extract Skills from all entries in the transcript
    // A transcript file represents a single conversation lineage, so all entries are relevant
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      try {
        const entry = JSON.parse(trimmed) as TranscriptEntry;

        // Check for Skill tool_use
        const contentArr = entry.message?.content;
        if (!Array.isArray(contentArr)) continue;

        for (const item of contentArr) {
          if (item?.type === "tool_use" && item?.name === "Skill") {
            const skillName = item.input?.skill;
            if (typeof skillName === "string" && skillName) {
              skillsUsed.add(skillName);
            }
          }
        }
      } catch {
        // Error ignored - fail-open pattern
      }
    }
  } catch {
    // Best effort - transcript read failure should not break hook
  }

  return skillsUsed;
}

/**
 * Check if command requires a specific Skill to be used first.
 *
 * @param command - The bash command being executed.
 * @returns Tuple of [skill_name, operation_description] if Skill is required, null otherwise.
 */
export function checkCommandForSkillRequirement(command: string): [string, string] | null {
  for (const [pattern, skillInfo] of OPERATION_SKILL_MAP) {
    if (pattern.test(command)) {
      return skillInfo;
    }
  }
  return null;
}

async function main(): Promise<void> {
  let result: HookResult = makeApproveResult(HOOK_NAME);
  let ctx: HookContext | null = null;

  try {
    const inputData = await parseHookInput();
    ctx = createContext(inputData);
    const sessionId = ctx.sessionId;

    // Only check Bash tool
    const toolName = inputData.tool_name ?? "";
    if (toolName !== "Bash") {
      console.log(JSON.stringify(result));
      return;
    }

    // Get command from tool input
    const toolInput = inputData.tool_input ?? {};
    const command = (toolInput.command as string) ?? "";
    if (!command) {
      console.log(JSON.stringify(result));
      return;
    }

    // Check if command requires a Skill
    const skillRequirement = checkCommandForSkillRequirement(command);
    if (!skillRequirement) {
      console.log(JSON.stringify(result));
      return;
    }

    const [requiredSkill, operationDesc] = skillRequirement;

    // Get transcript path and check Skill usage
    const transcriptPath = inputData.transcript_path;

    // Skip check if transcript path is not available
    // This can happen when the hook input doesn't include transcript_path
    if (!transcriptPath) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "transcript_path not available, skipping Skill usage check",
        undefined,
        { sessionId: sessionId ?? undefined },
      );
      console.log(JSON.stringify(result));
      return;
    }

    const skillsUsed = getSkillUsageFromTranscript(transcriptPath, sessionId);

    if (skillsUsed.has(requiredSkill)) {
      // Skill was used, allow operation
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Skill '${requiredSkill}' already used for '${operationDesc}'`,
        undefined,
        { sessionId: sessionId ?? undefined },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Skill was not used, BLOCK
    const blockMsg = `${operationDesc}の前にSkill使用が必要 - ブロック\n\n**必要なSkill**: \`${requiredSkill}\`\n\nこのSkillを使用すると、手順を確認しながら安全に作業を進められます。\n\n**対処法**:\n  1. \`/skill ${requiredSkill}\` を実行してSkillの手順を確認\n  2. 手順に従って操作を再実行\n\n**ヒント**: Skillには推奨される手順とチェックリストが含まれています。\n`;

    result = makeBlockResult(HOOK_NAME, blockMsg, ctx);
  } catch (error) {
    await logHookExecution(HOOK_NAME, "error", `Hook error: ${formatError(error)}`, undefined, {
      sessionId: ctx?.sessionId ?? undefined,
    });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error:`, e);
    process.exit(0);
  });
}
