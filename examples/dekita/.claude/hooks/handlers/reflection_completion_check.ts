#!/usr/bin/env bun
/**
 * PRマージ後または/reflecting-sessions skill invoke後の振り返り完了を検証する。
 *
 * Why:
 *   PRマージ後に振り返りをせずにセッションを終了すると、学習機会を逃し、
 *   同じ問題を繰り返す可能性がある。振り返りの完了をブロックで強制する。
 *
 * What:
 *   - reflection_requiredフラグの確認（post-merge-reflection-enforcerが設定）
 *   - flow stateからマージ完了フェーズを検知
 *   - /reflecting-sessions skillの呼び出し確認
 *   - 五省分析が実行されたか検証
 *   - [IMMEDIATE: action]タグの実行検証
 *   - 要件未達成時はセッション終了をブロック
 *
 * State:
 *   - reads: /tmp/claude-hooks/reflection-required-{session_id}.json
 *   - reads: .claude/logs/flow/*.jsonl
 *
 * Remarks:
 *   - post-merge-reflection-enforcer.pyがフラグ設定、本フックが検証
 *   - Issue作成強制はsystematization-check.pyが担当
 *   - 振り返り完了条件: 五省分析キーワード検出、または「振り返り完了」明示
 *   - lib/reflection.tsの共通関数を使用（Issue #2694）
 *
 * Changelog:
 *   - silenvx/dekita#2140: /reflecting-sessions skill呼び出し確認追加
 *   - silenvx/dekita#2172: flow stateからmerge完了検知
 *   - silenvx/dekita#2186: [IMMEDIATE]タグ実行検証追加
 *   - silenvx/dekita#2545: HookContextパターン移行
 *   - silenvx/dekita#3161: TypeScript移行
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { FLOW_LOG_DIR } from "../lib/common";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { isSafeTranscriptPath } from "../lib/path_validation";
import {
  checkImmediateActionExecuted,
  checkSkillInvocation,
  checkTranscriptForReflection,
  extractImmediateTags,
} from "../lib/reflection";
import { makeBlockResult } from "../lib/results";
import { createHookContext, isSafeSessionId, parseHookInput } from "../lib/session";

const HOOK_NAME = "reflection_completion_check";

// Session state directory
const SESSION_DIR = join(tmpdir(), "claude-hooks");

// =============================================================================
// Types
// =============================================================================

interface ReflectionState {
  reflection_required: boolean;
  merged_prs: number[];
  reflection_done: boolean;
}

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Get the reflection state file path for the current session.
 */
function getReflectionStateFile(sessionId: string | null | undefined): string {
  const effectiveSessionId = sessionId && isSafeSessionId(sessionId) ? sessionId : "unknown";
  return join(SESSION_DIR, `reflection-required-${effectiveSessionId}.json`);
}

/**
 * Load reflection state from session file.
 */
function loadReflectionState(sessionId: string | null | undefined): ReflectionState {
  try {
    const stateFile = getReflectionStateFile(sessionId);
    if (existsSync(stateFile)) {
      const content = readFileSync(stateFile, "utf-8");
      return JSON.parse(content) as ReflectionState;
    }
  } catch {
    // Best effort - corrupted state is ignored
  }
  return {
    reflection_required: false,
    merged_prs: [],
    reflection_done: false,
  };
}

/**
 * Save reflection state to session file.
 */
function saveReflectionState(sessionId: string | null | undefined, state: ReflectionState): void {
  try {
    mkdirSync(SESSION_DIR, { recursive: true });
    const stateFile = getReflectionStateFile(sessionId);
    writeFileSync(stateFile, JSON.stringify(state, null, 2), "utf-8");
  } catch {
    // Best effort - state save may fail
  }
}

/**
 * Check if any workflow has completed the merge phase in this session.
 *
 * Issue #2172: Read the flow state file to detect merge completions.
 * This replaces the old state-file-based detection that was removed in #2159.
 *
 * @returns List of workflow IDs that have completed the merge phase.
 */
function checkMergePhaseCompleted(sessionId: string | null | undefined): string[] {
  const effectiveSessionId = sessionId && isSafeSessionId(sessionId) ? sessionId : "unknown";
  const stateFile = join(FLOW_LOG_DIR, `state-${effectiveSessionId}.json`);

  try {
    if (!existsSync(stateFile)) {
      return [];
    }

    const content = readFileSync(stateFile, "utf-8");
    const state = JSON.parse(content) as {
      workflows?: Record<
        string,
        {
          phases?: Record<string, { status?: string }>;
        }
      >;
    };

    const workflows = state.workflows ?? {};
    const mergedWorkflows: string[] = [];

    for (const [workflowId, workflowState] of Object.entries(workflows)) {
      const phases = workflowState.phases ?? {};
      const mergePhase = phases.merge ?? {};

      // Check if merge phase is completed
      if (mergePhase.status === "completed") {
        mergedWorkflows.push(workflowId);
      }
    }

    return mergedWorkflows;
  } catch {
    return [];
  }
}

/**
 * Read transcript content from file path.
 * Issue #3263: Validates path with isSafeTranscriptPath for security.
 */
function readTranscript(transcriptPath: string | null | undefined): string {
  if (!transcriptPath || !isSafeTranscriptPath(transcriptPath)) {
    return "";
  }

  try {
    return readFileSync(transcriptPath, "utf-8");
  } catch {
    return "";
  }
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};

  try {
    const input = await parseHookInput();
    const ctx = createHookContext(input);
    const sessionId = ctx.sessionId;

    // Load reflection state
    const state = loadReflectionState(sessionId);

    // Check transcript for reflection indicators
    // Stop hooks receive transcript_path in input_data
    const transcriptPath = input.transcript_path ?? "";
    const transcriptContent = readTranscript(transcriptPath);

    // Issue #2140: Check if /reflecting-sessions skill was invoked
    const skillInvoked = checkSkillInvocation(transcriptContent);

    // Issue #2172: Check flow state for completed merge phases
    const mergedWorkflows = checkMergePhaseCompleted(sessionId);
    const flowMergeRequired = mergedWorkflows.length > 0;

    // Issue #2186: Check for [IMMEDIATE] tags and verify execution
    const immediateActions = extractImmediateTags(transcriptContent);

    // Check all [IMMEDIATE] actions for execution
    const unexecutedImmediateActions = immediateActions.filter(
      (action) => !checkImmediateActionExecuted(action, transcriptContent),
    );

    // If there are unexecuted [IMMEDIATE] actions, block session end
    if (unexecutedImmediateActions.length > 0) {
      const actionsStr = unexecutedImmediateActions.join(", ");
      const reason = `\`[IMMEDIATE]\` タグで指定されたアクションが未実行です: ${actionsStr}\n\n**指定されたアクションを今すぐ実行してください。**\n\nこれは強制力のある指示です。無視するとセッション終了がブロックされ続けます。`;

      const blockResult = makeBlockResult(HOOK_NAME, reason);
      await logHookExecution(
        HOOK_NAME,
        "block",
        `Unexecuted [IMMEDIATE] actions: ${actionsStr}`,
        undefined,
        { sessionId: sessionId ?? undefined },
      );
      console.log(JSON.stringify(blockResult));
      return;
    }

    // Check if /reflecting-sessions was requested via [IMMEDIATE] tag (already verified above)
    const immediateReflectRequired = immediateActions.some((action) =>
      action.toLowerCase().includes("/reflecting-sessions"),
    );

    // Determine if reflection is required
    // Either from PR merge (state flag), flow state, skill invocation, or [IMMEDIATE] tag
    const prMergeRequired = state.reflection_required;
    const reflectionRequired =
      prMergeRequired || flowMergeRequired || skillInvoked || immediateReflectRequired;

    // If no reflection required from any source, allow continuation
    if (!reflectionRequired) {
      await logHookExecution(HOOK_NAME, "approve", "No reflection required", undefined, {
        sessionId: sessionId ?? undefined,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Check if reflection was already marked as done
    // Note: For skill invocation, we must verify reflection content exists in transcript
    // because the skill may have been invoked after a previous reflection was completed.
    // The reflection_done flag from state only applies to PR merge case (not flow merge).
    // Issue #2172: Don't bypass for flow_merge_required to ensure new merges are checked.
    if (state.reflection_done && !skillInvoked && !flowMergeRequired) {
      await logHookExecution(HOOK_NAME, "approve", "Reflection already completed", undefined, {
        sessionId: sessionId ?? undefined,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Get merged PRs/workflows for the message (may be empty if skill-invoked)
    let prList: string | null = null;
    const mergedPrs = state.merged_prs ?? [];

    // Issue #2172: Also include workflow IDs from flow state
    if (mergedPrs.length === 0 && mergedWorkflows.length > 0) {
      // Use workflow IDs (e.g., "issue-123") as identifiers
      prList = mergedWorkflows.join(", ");
    } else if (mergedPrs.length > 0) {
      prList = mergedPrs.map((pr) => `#${pr}`).join(", ");
    }

    // Check for reflection in transcript or explicit completion phrase
    const hasReflection = checkTranscriptForReflection(transcriptContent);

    // Also check for explicit completion acknowledgment
    const explicitCompletion = /振り返り完了|振り返りが完了|reflection complete/i.test(
      transcriptContent,
    );

    if (hasReflection || explicitCompletion) {
      // Reflection was done - mark as complete and allow
      state.reflection_done = true;
      // Only save state for PR merge case, not skill invocation
      // (skill invocation should require reflection each time)
      if (!skillInvoked) {
        saveReflectionState(sessionId, state);
      }
      const completionReason = prList
        ? `Reflection completed for PRs: ${prList}`
        : "Reflection completed (skill invoked)";
      await logHookExecution(HOOK_NAME, "approve", completionReason, undefined, {
        sessionId: sessionId ?? undefined,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // If reflection not done, block
    // Build appropriate message based on trigger
    let reason: string;
    let blockReason: string;

    if (prList) {
      reason = `PRマージ後の振り返りが未完了です（対象PR: ${prList}）。\n\n**\`/reflecting-sessions\` スキルを実行してください。**\n\n振り返りが不要な場合は「振り返り完了」と明示してください。`;
      blockReason = `Reflection required for PRs: ${prList}`;
    } else {
      reason =
        "`/reflecting-sessions` スキルが呼び出されましたが、振り返りが未完了です。\n\n" +
        "**五省を実施してください。**\n\n" +
        "振り返りが不要な場合は「振り返り完了」と明示してください。";
      blockReason = "Reflection required (skill invoked but not completed)";
    }

    const blockResult = makeBlockResult(HOOK_NAME, reason);
    await logHookExecution(HOOK_NAME, "block", blockReason, undefined, {
      sessionId: sessionId ?? undefined,
    });
    console.log(JSON.stringify(blockResult));
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    await logHookExecution(
      HOOK_NAME,
      "error",
      `Hook error: ${formatError(error)}`,
      undefined,
      undefined,
    );
    // Don't block on errors
    console.log(JSON.stringify(result));
  }
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({}));
  });
}
