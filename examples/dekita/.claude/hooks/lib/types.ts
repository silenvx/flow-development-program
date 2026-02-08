/**
 * Hook入出力の型定義
 *
 * Why:
 *   Python hookとの互換性を維持しながら、TypeScriptの型安全性を活用
 *
 * What:
 *   - HookInput: stdinから受け取るJSON形式の入力
 *   - HookResult: stdoutに出力するJSON形式の結果
 *   - HookContext: セッション情報を保持するコンテキスト
 *   - CI Monitor型定義（Issue #3261で統合）
 *
 * Changelog:
 *   - silenvx/dekita#2814: 初期実装
 *   - silenvx/dekita#3261: ci_monitor型定義を統合
 */

import { z } from "zod";

/**
 * ツール入力のスキーマ（Bashコマンド用）
 */
export const BashToolInputSchema = z.object({
  command: z.string(),
  timeout: z.number().optional(),
});

/**
 * フック入力のスキーマ
 *
 * Claude Codeからstdinに渡されるJSON形式
 */
export const HookInputSchema = z.object({
  /** セッションID（UUID形式） */
  session_id: z.string().optional(),
  /** ツール名（Bash, Read, Write等） */
  tool_name: z.string().optional(),
  /** ツール固有の入力 */
  tool_input: z.record(z.unknown()).optional(),
  /**
   * ツール結果（PostToolUse用）
   * 優先順位: tool_result > tool_response > tool_output
   * Claude Codeバージョンにより使用フィールドが異なるため、3つとも定義
   */
  tool_result: z.union([z.record(z.unknown()), z.string()]).optional(),
  /** ツールレスポンス（PostToolUse用、一部バージョンで使用） */
  tool_response: z.union([z.record(z.unknown()), z.string()]).optional(),
  /** ツール出力（PostToolUse用、互換性フォールバック） */
  tool_output: z.union([z.record(z.unknown()), z.string()]).optional(),
  /** 現在の作業ディレクトリ */
  cwd: z.string().optional(),
  /** セッションソース（fork, resume, compact等） */
  source: z.string().optional(),
  /** transcriptファイルパス */
  transcript_path: z.string().optional(),
  /** フックイベント名 */
  hook_event_name: z.string().optional(),
  /** フックタイプ（PreToolUse, PostToolUse, Stop等） */
  hook_type: z.string().optional(),
  /** Stopフックがアクティブか */
  stop_hook_active: z.boolean().optional(),
  /** 通知データ（Notificationフック用） */
  notification: z.record(z.unknown()).optional(),
  /** 追加のメタデータ */
  metadata: z.record(z.unknown()).optional(),
  /** ユーザープロンプト（UserPromptSubmit用） */
  user_prompt: z.string().optional(),
});

export type HookInput = z.infer<typeof HookInputSchema>;

/**
 * フックタイプの種別
 */
export type HookType =
  | "PreToolUse"
  | "PostToolUse"
  | "Stop"
  | "Notification"
  | "SessionStart"
  | "UserPromptSubmit";

/**
 * フック結果の判定
 */
export type HookDecision = "block";

/**
 * フック結果のスキーマ
 *
 * stdoutに出力するJSON形式
 */
export const HookResultSchema = z.object({
  /** 判定結果 */
  decision: z.literal("block").optional(),
  /** ブロック理由（decisionがblockの場合） */
  reason: z.string().optional(),
  /** ユーザー表示用メッセージ */
  systemMessage: z.string().optional(),
});

export type HookResult = z.infer<typeof HookResultSchema>;

/**
 * セッションコンテキスト
 *
 * 依存性注入パターンでセッション情報を管理
 * Issue #2413: グローバル状態を排除し、テスタビリティを向上
 * Issue #3463: sessionIdとcwdをundefinable型に変更（null→undefined統一）
 */
export interface HookContext {
  /** セッションID */
  sessionId: string | undefined;
  /** 現在の作業ディレクトリ */
  cwd: string | undefined;
  /** フック入力の生データ */
  rawInput: HookInput;
}

/**
 * HookContextを作成
 */
export function createHookContext(input: HookInput): HookContext {
  return {
    sessionId: input.session_id,
    cwd: input.cwd,
    rawInput: input,
  };
}

// =============================================================================
// CI Monitor Types (Issue #3261 - migrated from Python models.py)
// =============================================================================

/**
 * Types of events that can be emitted during monitoring.
 */
export type EventType =
  | "BEHIND_DETECTED"
  | "DIRTY_DETECTED"
  | "REVIEW_COMPLETED"
  | "REVIEW_ERROR"
  | "CI_FAILED"
  | "CI_PASSED"
  | "TIMEOUT"
  | "ERROR";

/**
 * CI check status values.
 */
export type CheckStatus = "pending" | "success" | "failure" | "cancelled";

/**
 * PR merge state values.
 */
export type MergeState = "CLEAN" | "BEHIND" | "DIRTY" | "BLOCKED" | "UNKNOWN";

/**
 * Status for retry wait operations.
 * Issue #1463: Enum for retry wait status (type safety improvement).
 */
export type RetryWaitStatus = "CONTINUE" | "TIMEOUT";

/**
 * Types of rate limit events for logging.
 * Issue #1427: Using string union type allows direct use in JSON.
 * Issue #1360: Added REST_PRIORITY_ENTERED/EXITED for proactive fallback.
 */
export type RateLimitEventType =
  | "warning"
  | "limit_reached"
  | "adjusted_interval"
  | "recovered"
  | "rest_priority_entered"
  | "rest_priority_exited";

/**
 * Direction of polling interval adjustment.
 * Issue #1427: Used with RateLimitEventType.ADJUSTED_INTERVAL.
 */
export type IntervalDirection = "increase" | "decrease";

/**
 * Detailed reason why a PR is blocked.
 * Issue #3634: Report specific BLOCKED reason instead of just "merge_state: BLOCKED"
 */
export interface BlockedReason {
  /** Raw mergeStateStatus from GitHub API */
  mergeStateStatus: MergeState;
  /** Whether the branch is behind main */
  isBehind: boolean;
  /** Number of unresolved review threads */
  unresolvedThreadCount: number;
  /** Review decision (APPROVED, CHANGES_REQUESTED, REVIEW_REQUIRED, or empty) */
  reviewDecision: string;
  /** Whether there are pending required reviewers */
  hasPendingRequiredReviewers: boolean;
  /** Human-readable explanation of why the PR is blocked */
  explanation: string;
  /** Suggested action to unblock */
  suggestedAction: string;
  /**
   * Ruleset information from GitHub API (Issue #3748)
   * Only populated when diagnosing BLOCKED state with unknown reason
   */
  rulesetInfo?: {
    /** Number of required approving reviews (0 = no approval required) */
    requiredApprovingReviewCount: number;
    /** Whether review thread resolution is required */
    requiredReviewThreadResolution: boolean;
    /** Whether strict status checks are required (branch must be up to date) */
    strictRequiredStatusChecks: boolean;
  };
}

/**
 * State of a PR at a point in time.
 */
export interface PRState {
  mergeState: MergeState;
  pendingReviewers: string[];
  checkStatus: CheckStatus;
  checkDetails: Record<string, unknown>[];
  reviewComments: Record<string, unknown>[];
  unresolvedThreads: Record<string, unknown>[];
  /** Detailed reason if mergeState is BLOCKED (Issue #3634) */
  blockedReason?: BlockedReason;
  /** Review decision from GitHub (Issue #3663) */
  reviewDecision?: string;
  /** Whether the PR is a draft (Issue #3663) */
  isDraft?: boolean;
  /** Base branch name (Issue #3663) */
  baseRefName?: string;
}

/**
 * An event emitted by the monitor.
 */
export interface MonitorEvent {
  eventType: EventType;
  prNumber: string;
  timestamp: string;
  message: string;
  details: Record<string, unknown>;
  suggestedAction: string;
}

/**
 * Result of a monitoring session.
 */
export interface MonitorResult {
  success: boolean;
  message: string;
  rebase_count: number;
  final_state: PRState | null;
  review_completed: boolean;
  ci_passed: boolean;
  details?: Record<string, unknown>;
}

/**
 * Comments classified by whether they're within PR scope.
 */
export interface ClassifiedComments {
  inScope: Record<string, unknown>[];
  outOfScope: Record<string, unknown>[];
}

/**
 * Result of a PR rebase operation.
 * Issue #1348: Enhanced logging for rebase operations.
 */
export interface RebaseResult {
  success: boolean;
  conflict: boolean;
  errorMessage: string | null;
}

/**
 * Information about an @codex review request comment.
 */
export interface CodexReviewRequest {
  commentId: number;
  createdAt: string;
  hasEyesReaction: boolean;
}

/**
 * Event from multi-PR monitoring.
 */
export interface MultiPREvent {
  prNumber: string;
  event: MonitorEvent | null;
  state: PRState | null;
}

/**
 * Rate limit information for GitHub API.
 */
export interface RateLimitInfo {
  remaining: number;
  limit: number;
  resetTime: number;
  resource: "core" | "graphql";
}

/**
 * Check if a monitor result has unresolved review threads.
 */
export function hasUnresolvedThreads(result: MonitorResult): boolean {
  return Boolean(result.final_state && result.final_state.unresolvedThreads.length > 0);
}
