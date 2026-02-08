#!/usr/bin/env bun
/**
 * Issue作成依頼時の即時作成を強制するStop hook。
 *
 * Why:
 *   ユーザーから「Issueを作成してください」と依頼されたが、別作業に気を取られて
 *   Issue作成を忘れるケースがあった。Issueは忘れると追跡不可能になるため、
 *   依頼後の作成を強制する。
 *
 * What:
 *   - トランスクリプトからユーザーのIssue作成依頼を検出
 *   - 依頼後に`gh issue create`が実行され、成功したか確認
 *   - 未実行または失敗の場合はセッション終了をブロック
 *
 * State:
 *   - reads: transcript_path (from hook input)
 *
 * Remarks:
 *   - Stop hook（セッション終了時に実行）
 *   - 依頼パターン: 「Issueを作成」「Issue化して」「Issueにして」等
 *   - Issue作成後の依頼は無視（直近の依頼のみチェック）
 *   - JSONL形式とJSON配列形式の両方に対応
 *   - コマンド成功の判定: exit_code === 0 かつ Issue URLが出力に含まれる
 *
 * Changelog:
 *   - silenvx/dekita#3586: 初期実装
 *   - silenvx/dekita#3595: gh issue createの成功を検証
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { type ContentBlock, type TranscriptEntry, loadTranscript } from "../lib/transcript";

const HOOK_NAME = "issue_creation_request_check";

// =============================================================================
// Constants
// =============================================================================

/**
 * ユーザーのIssue作成依頼パターン
 *
 * 以下のような表現を検出:
 * - 「Issueを作成してください」
 * - 「Issue化してください」
 * - 「Issueにしてください」
 * - 「issueを作って」
 *
 * 除外パターン（誤検知防止）:
 * - 過去形: 「作成しました」「作成した」「済み」「完了」
 * - 否定形: 「不要」「しないで」
 * - 曖昧: 「対策」等
 */
const ISSUE_CREATION_REQUEST_PATTERNS = [
  // 「Issueを作成して」- 過去形・否定形を除外（間に最大10文字許容、文境界を越えない）
  /issue\s*を?\s*作成(?![^。！？.!?\n]{0,10}(?:しました|した|済み|完了|不要|しないで|しなくて|やめて))/i,
  // 「Issueを作って」- 過去形・否定形を除外（文境界を越えない）
  /issue\s*を?\s*作って(?![^。！？.!?\n]{0,10}(?:もらいました|頂いた|いただいた|ほしくない))/i,
  // 「Issue化」- 過去形・否定形を除外（間に最大10文字許容、文境界を越えない）
  /issue化(?![^。！？.!?\n]{0,10}(?:しました|した|済み|完了|不要|しないで|しなくて|やめて))/i,
  // 「Issueにして」（文境界を越えない）
  /issue\s*に\s*して(?![^。！？.!?\n]{0,10}(?:もらいました|頂いた|いただいた|ほしくない))/i,
];

/**
 * `gh issue create`コマンドのパターン
 * Note: 行頭にアンカーしてfalse positive（echo "gh issue create"等）を防ぐ
 */
const GH_ISSUE_CREATE_PATTERN = /^gh\s+issue\s+create/;

/**
 * GitHub Issue URLのパターン（成功検証用）
 */
const GITHUB_ISSUE_URL_PATTERN = /github\.com\/[^/]+\/[^/]+\/issues\/\d+/;

// =============================================================================
// Types for Tool Use/Result Matching
// =============================================================================

/**
 * Tool use block with ID for matching with tool_result
 */
interface ToolUseBlock extends ContentBlock {
  type: "tool_use";
  id: string;
  name: string;
  input: Record<string, unknown>;
}

/**
 * Tool result block with tool_use_id for matching
 */
interface ToolResultBlock extends ContentBlock {
  type: "tool_result";
  tool_use_id: string;
  content?: string | unknown[];
  // exit_code may be at block level or in parsed content
}

/**
 * Parsed tool result for Bash commands
 */
interface BashToolResult {
  toolUseId: string;
  exitCode: number | undefined;
  stdout: string;
}

// =============================================================================
// Transcript Entry Helpers
// =============================================================================

/**
 * トランスクリプトエントリからユーザーメッセージのテキストを抽出
 */
function getUserMessageText(entry: TranscriptEntry): string | null {
  if (entry.role !== "user" || !entry.content) return null;

  if (typeof entry.content === "string") {
    return entry.content;
  }

  if (Array.isArray(entry.content)) {
    const texts = (entry.content as ContentBlock[])
      .filter(
        (b): b is ContentBlock & { text: string } =>
          typeof b === "object" && b !== null && "text" in b && typeof b.text === "string",
      )
      .map((b) => b.text);
    return texts.length > 0 ? texts.join("") : null;
  }

  return null;
}

/**
 * トランスクリプトエントリからBashコマンドを抽出
 */
function getBashCommands(entry: TranscriptEntry): string[] {
  return getBashToolUses(entry).map((tool) => tool.command);
}

/**
 * Bash tool_useブロックを抽出（IDとコマンドのペア）
 */
function getBashToolUses(entry: TranscriptEntry): Array<{ id: string; command: string }> {
  if (entry.role !== "assistant" || !Array.isArray(entry.content)) return [];

  return (entry.content as ContentBlock[])
    .filter(
      (b): b is ToolUseBlock =>
        typeof b === "object" &&
        b !== null &&
        b.type === "tool_use" &&
        b.name === "Bash" &&
        typeof b.id === "string" &&
        !!b.input &&
        typeof b.input.command === "string",
    )
    .map((b) => ({ id: b.id, command: b.input.command as string }));
}

/**
 * Extract stdout from a tool_result content block.
 *
 * Handles multiple formats:
 * - Direct stdout field on block
 * - String content (plain or JSON)
 * - Array of text blocks (Claude API format)
 *
 * Reference: immediate_action_check.ts extractStdout
 */
function extractStdoutFromBlock(block: ToolResultBlock): string {
  const blockAny = block as Record<string, unknown>;

  // 1. Direct stdout field
  if (typeof blockAny.stdout === "string") {
    return blockAny.stdout;
  }

  // 2. Check output field (alternative field name)
  if (typeof blockAny.output === "string") {
    return blockAny.output;
  }

  const content = block.content;

  // 3. String content - may be plain text or JSON
  if (typeof content === "string") {
    try {
      const parsed = JSON.parse(content);
      if (parsed && typeof parsed === "object") {
        if (typeof parsed.stdout === "string") return parsed.stdout;
        if (typeof parsed.output === "string") return parsed.output;
      }
    } catch {
      // Not JSON, return as-is
    }
    return content;
  }

  // 4. Array content - Claude API format with text blocks
  if (Array.isArray(content)) {
    return content
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }
        if (typeof item === "object" && item !== null) {
          const block = item as Record<string, unknown>;
          if (block.type === "text" && typeof block.text === "string") {
            return block.text;
          }
        }
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }

  return "";
}

/**
 * Extract exit_code from a tool_result block.
 *
 * Returns undefined if exit_code is not found (treated as failure).
 */
function extractExitCodeFromBlock(block: ToolResultBlock): number | undefined {
  const blockAny = block as Record<string, unknown>;

  // 1. Direct exit_code/exitCode field on block (support both naming conventions)
  const directExitCode = blockAny.exit_code ?? blockAny.exitCode;
  if (typeof directExitCode === "number") {
    return directExitCode;
  }

  // 2. Try parsing content as JSON
  const content = block.content;
  if (typeof content === "string") {
    try {
      const parsed = JSON.parse(content);
      if (parsed && typeof parsed === "object") {
        const parsedExitCode = parsed.exit_code ?? parsed.exitCode;
        if (typeof parsedExitCode === "number") {
          return parsedExitCode;
        }
      }
    } catch {
      // Not JSON
    }
  }

  // Missing exit_code is treated as failure (undefined)
  return undefined;
}

/**
 * tool_resultブロックを抽出
 *
 * Note: tool_resultはuserロール以外（assistant等）にも存在する可能性があるため、
 * ロールでフィルタしない。
 */
function getToolResults(entry: TranscriptEntry): BashToolResult[] {
  if (!Array.isArray(entry.content)) return [];

  return (entry.content as ContentBlock[])
    .filter(
      (b): b is ToolResultBlock =>
        typeof b === "object" &&
        b !== null &&
        b.type === "tool_result" &&
        typeof b.tool_use_id === "string",
    )
    .map((b) => {
      return {
        toolUseId: b.tool_use_id,
        exitCode: extractExitCodeFromBlock(b),
        stdout: extractStdoutFromBlock(b),
      };
    });
}

/**
 * Issue作成依頼がテキストに含まれるか確認
 */
function hasIssueCreationRequest(text: string): boolean {
  for (const pattern of ISSUE_CREATION_REQUEST_PATTERNS) {
    if (pattern.test(text)) {
      return true;
    }
  }
  return false;
}

/**
 * `gh issue create`コマンドか確認
 */
function isGhIssueCreateCommand(command: string): boolean {
  return GH_ISSUE_CREATE_PATTERN.test(command);
}

/**
 * Issue作成が成功したか確認
 * - exit_code === 0 (undefinedは失敗扱い)
 * - stdoutにGitHub Issue URLが含まれる
 *
 * Note: このプロジェクトでは `gh issue create` は常に標準形式（URL出力）で
 * 使用されるため、URLの存在を成功条件に含めている。
 * `--web` や `--json --jq .number` などURL出力を抑制するオプションは
 * 使用されない前提。
 */
function isSuccessfulIssueCreation(result: BashToolResult): boolean {
  if (result.exitCode === undefined || result.exitCode !== 0) {
    return false;
  }
  return GITHUB_ISSUE_URL_PATTERN.test(result.stdout);
}

// =============================================================================
// Main Logic
// =============================================================================

/**
 * 未対応のIssue作成依頼を検出
 *
 * @param transcriptPath トランスクリプトファイルのパス
 * @returns 未対応の依頼テキスト、または null
 */
function findUnhandledIssueCreationRequest(transcriptPath: string): string | null {
  const entries = loadTranscript(transcriptPath);
  if (!entries) return null;

  return findUnhandledRequest(entries);
}

/**
 * 未対応のIssue作成依頼を検出（entries配列から）
 *
 * @param entries トランスクリプトエントリ配列
 * @returns 未対応の依頼テキスト、または null
 */
function findUnhandledRequest(entries: TranscriptEntry[]): string | null {
  // 最後のIssue作成依頼を探す
  let lastRequestIndex = -1;
  let lastRequestText = "";

  entries.forEach((entry, index) => {
    const text = getUserMessageText(entry);
    if (text && hasIssueCreationRequest(text)) {
      lastRequestIndex = index;
      lastRequestText = text;
    }
  });

  if (lastRequestIndex === -1) {
    // Issue作成依頼なし
    return null;
  }

  // 依頼後に`gh issue create`が成功したか確認
  // tool_use_idとtool_resultをマッチングする
  const pendingToolUses: Map<string, string> = new Map(); // id -> command

  for (let i = lastRequestIndex + 1; i < entries.length; i++) {
    const entry = entries[i];

    // Collect tool_use blocks from assistant
    for (const toolUse of getBashToolUses(entry)) {
      if (isGhIssueCreateCommand(toolUse.command)) {
        pendingToolUses.set(toolUse.id, toolUse.command);
      }
    }

    // Check tool_results from user entries
    for (const result of getToolResults(entry)) {
      if (pendingToolUses.has(result.toolUseId)) {
        // This is a result for a gh issue create command
        if (isSuccessfulIssueCreation(result)) {
          // 依頼後にIssue作成が成功した
          return null;
        }
        // Command was executed but failed - remove from pending
        pendingToolUses.delete(result.toolUseId);
      }
    }
  }

  // 依頼後にIssue作成が成功していない
  return lastRequestText;
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

    // Get transcript path
    const transcriptPath = input.transcript_path ?? "";

    if (!transcriptPath) {
      await logHookExecution(HOOK_NAME, "approve", "No transcript path available", undefined, {
        sessionId: sessionId ?? undefined,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Check for unhandled issue creation requests
    const unhandledRequest = findUnhandledIssueCreationRequest(transcriptPath);

    if (unhandledRequest) {
      // Truncate request text for display
      const displayText =
        unhandledRequest.length > 100 ? `${unhandledRequest.slice(0, 100)}...` : unhandledRequest;

      const message = `⚠️  Issue作成依頼が未対応です
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ユーザーからIssue作成を依頼されましたが、成功していません。
\`gh issue create\` が未実行、または実行したが失敗しています。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

依頼内容: "${displayText}"

対応方法:
1. \`gh issue create\` でIssueを作成してください
2. コマンドが成功し、Issue URLが出力されることを確認してください
3. Issue作成成功後にセッション終了できます

AGENTS.md: 「Issue作成依頼の即時対応（必須）」`;

      result = makeBlockResult(HOOK_NAME, message, ctx);
      await logHookExecution(
        HOOK_NAME,
        "block",
        "Unhandled issue creation request detected",
        undefined,
        {
          sessionId: sessionId ?? undefined,
        },
      );
      console.log(JSON.stringify(result));
      process.exit(2);
    }

    await logHookExecution(
      HOOK_NAME,
      "approve",
      "No unhandled issue creation requests",
      undefined,
      {
        sessionId: sessionId ?? undefined,
      },
    );
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    await logHookExecution(HOOK_NAME, "error", `Hook error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

// Only run main if this file is the entry point
if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
  });
}

// Export for testing
export {
  ISSUE_CREATION_REQUEST_PATTERNS,
  getUserMessageText,
  getBashCommands,
  getBashToolUses,
  getToolResults,
  hasIssueCreationRequest,
  isGhIssueCreateCommand,
  isSuccessfulIssueCreation,
  findUnhandledIssueCreationRequest,
  findUnhandledRequest,
};

// Export types for testing
export type { ToolUseBlock, ToolResultBlock, BashToolResult };
