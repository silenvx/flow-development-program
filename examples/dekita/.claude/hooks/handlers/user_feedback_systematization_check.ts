#!/usr/bin/env bun
/**
 * ユーザーフィードバック検出時の仕組み化を確認する。
 *
 * Why:
 *   ユーザーが問題を指摘した場合、その問題の修正だけでなく、
 *   類似問題を将来検出できる仕組み化が必要。セッション終了時に
 *   仕組み化されていなければ警告する。
 *
 * What:
 *   - セッション状態から `user_feedback_detected` フラグを確認
 *   - フィードバック検出時、仕組み化ファイル変更を検出
 *   - 仕組み化なしの場合にACTION_REQUIREDを出力
 *
 * Remarks:
 *   - feedback_detector.py がセッション状態に記録
 *   - systematization_check.py とは別の観点（ユーザー指摘への対応）
 *   - ブロックではなく警告（exit 0）
 *
 * Changelog:
 *   - silenvx/dekita#2754: 新規作成
 *   - silenvx/dekita#3161: TypeScript移行
 */

import { existsSync, readFileSync } from "node:fs";
import { basename, join } from "node:path";
import { FLOW_LOG_DIR } from "../lib/common";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { createHookContext, parseHookInput } from "../lib/session";
import { type TranscriptEntry, loadTranscript } from "../lib/transcript";

const HOOK_NAME = "user-feedback-systematization-check";

// Systematization file patterns (same as systematization_check.ts)
const SYSTEMATIZATION_PATTERNS = [
  /\.claude\/hooks\/.*\.(py|ts)$/,
  /\.github\/workflows\/.*\.ya?ml$/,
  /\.claude\/scripts\/.*\.(?:py|sh|ts)$/,
  /\.claude\/skills\/.*\/SKILL\.md$/,
];

// Patterns indicating add-perspective was executed
const ADD_PERSPECTIVE_PATTERNS = [
  /\/adding-perspectives/i,
  /\/add-perspective/i, // Legacy fallback
  /adding-perspectives/i,
  /add-perspective/i, // Legacy fallback
  /振り返り観点.*追加/i,
  /perspective.*追加/i,
];

interface FlowState {
  user_feedback_detected?: boolean;
  [key: string]: unknown;
}

/**
 * Load session state from flow state file.
 */
function loadSessionState(sessionId: string): FlowState {
  if (!sessionId) {
    return {};
  }

  const safeSessionId = basename(sessionId);
  const stateFile = join(FLOW_LOG_DIR, `state-${safeSessionId}.json`);
  try {
    if (existsSync(stateFile)) {
      const content = readFileSync(stateFile, "utf-8");
      return JSON.parse(content) as FlowState;
    }
  } catch {
    // Best effort - invalid/missing state file returns empty dict
  }
  return {};
}

/**
 * Extract file paths from Edit/Write tool uses in transcript.
 */
function extractFileOperations(transcript: TranscriptEntry[]): string[] {
  const files: string[] = [];

  for (const entry of transcript) {
    if (entry.role === "assistant") {
      const content = entry.content;
      if (Array.isArray(content)) {
        for (const block of content) {
          if (
            typeof block === "object" &&
            block !== null &&
            "type" in block &&
            block.type === "tool_use"
          ) {
            const toolBlock = block as {
              type: "tool_use";
              name?: string;
              input?: Record<string, unknown>;
            };
            const toolName = toolBlock.name ?? "";
            if (toolName === "Edit" || toolName === "Write") {
              const filePath = toolBlock.input?.file_path;
              if (typeof filePath === "string" && filePath) {
                files.push(filePath);
              }
            }
          }
        }
      }
    }
  }

  return files;
}

/**
 * Extract Skill invocations from transcript.
 */
function extractSkillInvocations(transcript: TranscriptEntry[]): string[] {
  const skills: string[] = [];

  for (const entry of transcript) {
    if (entry.role === "assistant") {
      const content = entry.content;
      if (Array.isArray(content)) {
        for (const block of content) {
          if (
            typeof block === "object" &&
            block !== null &&
            "type" in block &&
            block.type === "tool_use"
          ) {
            const toolBlock = block as {
              type: "tool_use";
              name?: string;
              input?: Record<string, unknown>;
            };
            if (toolBlock.name === "Skill") {
              const skillName = toolBlock.input?.skill;
              if (typeof skillName === "string" && skillName) {
                skills.push(skillName);
              }
            }
          }
        }
      }
    }
  }

  return skills;
}

/**
 * Find files that indicate systematization.
 */
function findSystematizationFiles(files: string[]): string[] {
  const systematized: string[] = [];

  for (const filePath of files) {
    for (const pattern of SYSTEMATIZATION_PATTERNS) {
      if (pattern.test(filePath)) {
        systematized.push(filePath);
        break;
      }
    }
  }

  return systematized;
}

/**
 * Check if add-perspective was executed.
 */
function checkAddPerspectiveExecuted(transcript: TranscriptEntry[], skills: string[]): boolean {
  // Check if add-perspective skill was invoked
  if (skills.includes("adding-perspectives")) {
    return true;
  }

  // Check transcript content for add-perspective patterns
  for (const entry of transcript) {
    if (entry.role === "assistant") {
      const content = entry.content;
      if (Array.isArray(content)) {
        for (const block of content) {
          if (
            typeof block === "object" &&
            block !== null &&
            "type" in block &&
            block.type === "text"
          ) {
            const textBlock = block as { type: "text"; text?: string };
            const text = textBlock.text ?? "";
            for (const pattern of ADD_PERSPECTIVE_PATTERNS) {
              if (pattern.test(text)) {
                return true;
              }
            }
          }
        }
      }
    }
  }

  return false;
}

async function main(): Promise<void> {
  const result: Record<string, unknown> = {};

  try {
    const inputData = await parseHookInput();
    const ctx = createHookContext(inputData);

    // Skip if stop hook is already active (prevents infinite loops)
    if (inputData.stop_hook_active) {
      await logHookExecution(HOOK_NAME, "approve", "stop_hook_active");
      console.log(JSON.stringify(result));
      return;
    }

    const sessionId = ctx.sessionId;
    if (!sessionId) {
      await logHookExecution(HOOK_NAME, "approve", "no session_id");
      console.log(JSON.stringify(result));
      return;
    }

    // Check session state for user feedback flag
    const state = loadSessionState(sessionId);
    if (!state.user_feedback_detected) {
      await logHookExecution(HOOK_NAME, "approve", "no user feedback");
      console.log(JSON.stringify(result));
      return;
    }

    // User feedback was detected - check for systematization
    const transcriptPath = inputData.transcript_path;
    if (!transcriptPath) {
      await logHookExecution(HOOK_NAME, "approve", "no transcript path");
      console.log(JSON.stringify(result));
      return;
    }

    const transcript = loadTranscript(transcriptPath);
    if (!transcript || transcript.length === 0) {
      await logHookExecution(HOOK_NAME, "approve", "transcript load failed");
      console.log(JSON.stringify(result));
      return;
    }

    const fileOperations = extractFileOperations(transcript);
    const skillInvocations = extractSkillInvocations(transcript);
    const systematizedFiles = findSystematizationFiles(fileOperations);

    // Check if add-perspective was executed
    const addPerspectiveExecuted = checkAddPerspectiveExecuted(transcript, skillInvocations);

    // Decision logic:
    // - If systematization files were created -> approve
    // - If add-perspective was executed -> approve with note
    // - Otherwise -> warn with ACTION_REQUIRED
    if (systematizedFiles.length > 0) {
      result.systemMessage =
        `✅ [${HOOK_NAME}] ` +
        `ユーザーフィードバック対応: 仕組み化ファイル作成 (${systematizedFiles.length}件)`;
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `systematized: ${systematizedFiles.length} files`,
      );
    } else if (addPerspectiveExecuted) {
      result.systemMessage = `✅ [${HOOK_NAME}] ユーザーフィードバック対応: /adding-perspectives 実行済み`;
      await logHookExecution(HOOK_NAME, "approve", "add-perspective executed");
    } else {
      // ACTION_REQUIRED format for Claude Code to take autonomous action
      const reason =
        "[ACTION_REQUIRED: FEEDBACK_SYSTEMATIZATION]\n" +
        "ユーザーフィードバックが検出されましたが、仕組み化されていません。\n\n" +
        "ユーザーが問題を指摘した場合、類似問題を将来検出できる仕組み化が必要です。\n\n" +
        "以下のいずれかを実行してください:\n" +
        "1. `/adding-perspectives` で振り返り観点を追加\n" +
        "2. `.claude/hooks/` にフックを作成して検出機構を実装\n" +
        "3. `.github/workflows/` にCIチェックを追加\n" +
        "4. 仕組み化が不要な理由をIssueに記録\n\n" +
        "ドキュメント（AGENTS.md等）への追記だけでは不十分です。";
      await logHookExecution(HOOK_NAME, "warn", "feedback detected but not systematized");
      // Print to stderr for Claude Code to see
      console.error(`[${HOOK_NAME}] ${reason}`);
    }
  } catch (e) {
    await logHookExecution(HOOK_NAME, "approve", `error: ${formatError(e)}`);
    console.error(`[${HOOK_NAME}] Error: ${formatError(e)}`);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    // Issue #3263: Output JSON to avoid Claude Code hang
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({}));
  });
}
