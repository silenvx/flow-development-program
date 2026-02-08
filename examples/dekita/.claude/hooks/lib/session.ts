/**
 * セッション識別・追跡機能
 *
 * Why:
 *   セッション単位でログをグループ化し、状態を追跡するために
 *   セッションID管理機能が必要
 *
 * What:
 *   - parseHookInput(): stdinからフック入力をパース
 *   - HookContext: 依存性注入パターンによるセッション情報管理
 *   - checkAndUpdateSessionMarker(): セッション毎に1回だけ実行するためのマーカー
 *
 * Remarks:
 *   - Python lib/session.py との互換性を維持
 *   - Bunのstdin読み込みを使用
 *
 * Changelog:
 *   - silenvx/dekita#2814: 初期実装
 *   - silenvx/dekita#2874: checkAndUpdateSessionMarker追加
 */

import {
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  readSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { basename, join, resolve } from "node:path";
import { SESSION_DIR, SESSION_GAP_THRESHOLD } from "./constants";
import { formatError } from "./format_error";
import { type HookContext, type HookInput, HookInputSchema, createHookContext } from "./types";

/**
 * Re-export HookContext and HookResult from types.ts
 *
 * Some existing hooks import these from ../lib/session instead of ../lib/types.
 * This re-export maintains backward compatibility.
 */
export type { HookContext, HookResult } from "./types";

/**
 * createHookContext の再export
 *
 * 一部の既存フックは歴史的経緯により `./types` ではなく `./session` から
 * `createHookContext` を import している。そのため、`createHookContext` の
 * 定義自体は `types.ts` に集約しつつも、ここからも再export することで
 * それらのフックを変更せずに動作させる（破壊的変更を避ける）目的がある。
 *
 * 新規コードでは、型定義と実装を見通しよく保つため `./types` からの import
 * を推奨するが、既存コードとの互換性のためこの再export を残している。
 */
export { createHookContext };

/**
 * PostToolUseフック入力からツール実行結果を取得
 *
 * 優先順位: tool_result > tool_response > tool_output
 *
 * Changelog:
 *   - silenvx/dekita#3103: 複数フックから共通化
 */
export function getToolResult(input: HookInput): unknown {
  return input.tool_result ?? input.tool_response ?? input.tool_output;
}

/**
 * ToolResultをオブジェクトとして取得するヘルパー
 *
 * tool_resultが文字列の場合は空オブジェクトを返す。
 * これにより、プロパティアクセス時の型エラーを防ぐ。
 *
 * @param input HookInput
 * @returns tool_resultがオブジェクトならそのオブジェクト、それ以外は空オブジェクト
 */
export function getToolResultAsObject(input: HookInput): Record<string, unknown> {
  const result = getToolResult(input);
  if (result && typeof result === "object" && !Array.isArray(result)) {
    return result as Record<string, unknown>;
  }
  return {};
}

/**
 * ツール入力を取得するヘルパー
 *
 * @param input HookInput
 * @returns tool_inputがオブジェクトならそのオブジェクト、それ以外は空オブジェクト
 */
export function getToolInput(input: HookInput): Record<string, unknown> {
  return input.tool_input ?? {};
}

/**
 * Bashコマンドを取得するヘルパー
 *
 * @param input HookInput
 * @returns commandがあればその文字列、なければ空文字列
 */
export function getBashCommand(input: HookInput): string {
  const toolInput = getToolInput(input);
  return typeof toolInput.command === "string" ? toolInput.command : "";
}

/**
 * デバッグログ出力
 *
 * CLAUDE_DEBUG=1 の場合にstderrに出力
 */
function debugLog(message: string): void {
  if (process.env.CLAUDE_DEBUG === "1") {
    console.error(message);
  }
}

/**
 * stdinからフック入力を読み取り、パース
 *
 * @returns パースされたフック入力
 * @throws JSONパースエラー（stdin読み取りエラー・タイムアウト時は空入力として処理）
 */
const STDIN_TIMEOUT_MS = 3000;

export async function parseHookInput(): Promise<HookInput> {
  // Bunのstdin読み込み（タイムアウト付き: stdinが閉じられない場合のハング防止）
  let timer: ReturnType<typeof setTimeout>;
  const text = await Promise.race([
    Bun.stdin
      .text()
      .catch((e) => {
        debugLog(`[parseHookInput] Stdin read error: ${formatError(e)}`);
        return "";
      })
      .finally(() => clearTimeout(timer)),
    new Promise<string>((resolve) => {
      timer = setTimeout(() => {
        debugLog(`[parseHookInput] Stdin read timed out after ${STDIN_TIMEOUT_MS}ms`);
        resolve("");
      }, STDIN_TIMEOUT_MS);
      timer.unref();
    }),
  ]);

  if (!text.trim()) {
    debugLog("[parseHookInput] Empty stdin, returning default input");
    return {};
  }

  try {
    const parsed = JSON.parse(text);
    const result = HookInputSchema.parse(parsed);
    debugLog(
      `[parseHookInput] Parsed input with session_id: ${result.session_id?.slice(0, 16) ?? "none"}`,
    );
    return result;
  } catch (error) {
    debugLog(`[parseHookInput] Parse error: ${formatError(error)}`);
    throw error;
  }
}

/**
 * フック入力からHookContextを作成するファクトリ関数
 *
 * @param input パースされたフック入力
 * @returns HookContext
 */
export function createContext(input: HookInput): HookContext {
  return createHookContext(input);
}

/**
 * HookContextからセッションIDを取得
 *
 * @param ctx HookContext
 * @returns セッションID、または未設定の場合はnull
 */
export function getSessionId(ctx: HookContext): string | null {
  if (ctx.sessionId) {
    const truncated = ctx.sessionId.slice(0, 16);
    const suffix = ctx.sessionId.length > 16 ? "..." : "";
    debugLog(`[session_id] source=hook_input, value=${truncated}${suffix}`);
    return ctx.sessionId;
  }
  debugLog("[session_id] source=None, session_id not provided in hook input");
  return null;
}

/**
 * 環境変数からプロジェクトディレクトリを取得
 *
 * @returns CLAUDE_PROJECT_DIR、または未設定の場合はnull
 */
export function getProjectDir(): string | null {
  return process.env.CLAUDE_PROJECT_DIR ?? null;
}

/**
 * セッションマーカーディレクトリのデフォルトパスを取得
 *
 * @returns セッションマーカーディレクトリのパス
 */
export function getSessionMarkerDir(): string {
  const projectDir = getProjectDir() ?? process.cwd();
  return join(projectDir, ".claude", "session");
}

/**
 * Validate that a session ID is safe to use in file paths.
 *
 * Session IDs should be UUID format (e.g., "3f03a042-a9ef-44a2-839a-d17badc44b0a").
 * This prevents path traversal attacks when session IDs are used in file names.
 *
 * @param sessionId - The session ID to validate
 * @returns True if the session ID is safe, false otherwise
 */
export function isSafeSessionId(sessionId: string): boolean {
  if (!sessionId || !sessionId.trim()) {
    return false;
  }

  // UUID format: 8-4-4-4-12 hex characters
  const uuidPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
  if (uuidPattern.test(sessionId)) {
    return true;
  }

  // Also accept simple alphanumeric session IDs (no path traversal characters)
  const safePattern = /^[a-zA-Z0-9_-]+$/;
  if (safePattern.test(sessionId) && sessionId.length <= 100) {
    return true;
  }

  return false;
}

/**
 * Alias for isSafeSessionId for backward compatibility.
 *
 * Some hooks import isValidSessionId instead of isSafeSessionId.
 */
export const isValidSessionId = isSafeSessionId;

/**
 * Check and update a session marker.
 *
 * This is used to ensure certain actions only happen once per session.
 *
 * @param markerName - The name of the marker
 * @param sessionDir - Optional directory for marker files
 * @returns True if this is a new session, false otherwise
 */
export function checkAndUpdateSessionMarker(markerName: string, sessionDir?: string): boolean {
  const dir = sessionDir ?? getSessionMarkerDir();
  const markerFile = join(dir, `${markerName}.marker`);

  try {
    // ディレクトリを作成
    mkdirSync(dir, { recursive: true });

    // マーカーファイルが存在する場合、最終チェック時刻を確認
    let isNew = true;
    if (existsSync(markerFile)) {
      try {
        const content = readFileSync(markerFile, "utf-8").trim();
        const lastCheck = Number.parseFloat(content);
        const currentTime = Date.now() / 1000;
        isNew = currentTime - lastCheck > SESSION_GAP_THRESHOLD;
      } catch (error) {
        // パースエラーは新セッションとして扱う
        console.error(`[session] Failed to parse session marker file ${markerFile}:`, error);
      }
    }

    if (isNew) {
      // マーカーを更新
      writeFileSync(markerFile, String(Date.now() / 1000));
    }

    return isNew;
  } catch (error) {
    // エラー時はスキップ（ブロックしない）
    console.error(`[session] Failed to check/update session marker ${markerName}:`, error);
    return false;
  }
}

/**
 * Check if an action has already been performed in the current session.
 * If not, mark it as performed.
 *
 * This ensures that specific hooks (like session start reminders) run only once
 * per session ID, even if the hook is triggered multiple times (e.g. in UserPromptSubmit).
 *
 * @param sessionId - The current session ID
 * @param actionName - Unique name for the action
 * @returns True if the action should run (first time), false if it should be skipped
 */
export function checkAndMarkSessionAction(sessionId: string, actionName: string): boolean {
  if (!sessionId || !actionName) {
    return true; // Can't track without ID, so assume run
  }

  // Validate sessionId to prevent path traversal attacks
  if (!isSafeSessionId(sessionId)) {
    console.error(`[session] Invalid session ID format: ${sessionId}`);
    return true; // Fail safe: run the action if check fails
  }

  // Validate actionName to prevent path traversal attacks
  // Only allow alphanumeric, hyphens, and underscores
  const safeActionPattern = /^[a-zA-Z0-9_-]+$/;
  if (!safeActionPattern.test(actionName) || actionName.length > 100) {
    console.error(`[session] Invalid action name format: ${actionName}`);
    return true; // Fail safe: run the action if check fails
  }

  // Use a directory structure for atomic locking: session-actions/<sessionId>/<actionName>
  // This prevents race conditions between parallel hook executions
  const sessionDir = join(SESSION_DIR, "session-actions", sessionId);
  const actionMarker = join(sessionDir, actionName);

  try {
    // recursive: true handles existing dirs, eliminating race conditions
    mkdirSync(sessionDir, { recursive: true });

    // Atomic check-and-create using 'wx' flag (fail if exists)
    // writeFileSync with 'wx' is simpler than openSync + closeSync
    writeFileSync(actionMarker, "", { flag: "wx" });
    return true; // Successfully marked (first time)
  } catch (error) {
    // If file exists, it's not the first time
    if ((error as { code?: string }).code === "EEXIST") {
      return false;
    }
    console.error(`[session] Failed to check/mark session action ${actionName}:`, error);
    return true; // Fail safe: run the action if check fails
  }
}

/**
 * Validate that transcript_path is in an allowed location.
 *
 * Allowed locations:
 * 1. Within CLAUDE_PROJECT_DIR
 * 2. Within ~/.claude/projects/
 */
function isValidTranscriptPath(transcriptFile: string): boolean {
  try {
    const resolvedPath = resolve(transcriptFile);

    // Allow paths within CLAUDE_PROJECT_DIR
    const projectDir = resolve(process.env.CLAUDE_PROJECT_DIR || process.cwd());
    if (resolvedPath.startsWith(`${projectDir}/`)) {
      return true;
    }

    // Allow paths within ~/.claude/projects/
    const claudeProjectsDir = join(homedir(), ".claude", "projects");
    if (resolvedPath.startsWith(`${claudeProjectsDir}/`)) {
      return true;
    }

    debugLog(`[session] Rejecting transcript_path outside allowed locations: ${transcriptFile}`);
    return false;
  } catch {
    return false;
  }
}

/**
 * Extract session ID from transcript file path.
 *
 * Claude Code stores transcripts with the session ID as the filename:
 * ~/.claude/projects/<project-hash>/<session-id>.jsonl
 */
export function extractSessionIdFromTranscriptPath(transcriptPath: string | null): string | null {
  if (!transcriptPath) {
    return null;
  }

  try {
    const filename = basename(transcriptPath);
    // Remove .jsonl extension
    const sessionId = filename.replace(/\.jsonl$/, "");
    // Validate it looks like a UUID
    if (isSafeSessionId(sessionId)) {
      return sessionId;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Detect fork by inspecting own transcript for a different session ID.
 *
 * If the transcript contains entries with a sessionId different from
 * currentSessionId, this session is a fork (it inherited history from
 * a parent session). Returns the parent's sessionId, or null.
 */
function getParentSessionIdFromTranscript(
  transcriptPath: string | null,
  currentSessionId: string,
): string | null {
  if (!transcriptPath || !currentSessionId) {
    return null;
  }

  // Read only the first 8KB to check the header, avoiding full file load.
  // Fork history is always at the beginning of the transcript.
  const HEADER_BYTES = 8192;

  try {
    const resolvedPath = resolve(transcriptPath);

    if (!isValidTranscriptPath(resolvedPath)) {
      return null;
    }

    if (!existsSync(resolvedPath)) {
      return null;
    }

    const fd = openSync(resolvedPath, "r");
    let content: string;
    try {
      const buffer = Buffer.alloc(HEADER_BYTES);
      const bytesRead = readSync(fd, buffer, 0, HEADER_BYTES, 0);
      content = buffer.toString("utf-8", 0, bytesRead);
    } finally {
      closeSync(fd);
    }
    const lines = content.split("\n");

    for (const line of lines) {
      const trimmedLine = line.trim();
      if (!trimmedLine) continue;

      try {
        const entry = JSON.parse(trimmedLine);
        if (
          entry.sessionId &&
          isSafeSessionId(entry.sessionId) &&
          entry.sessionId !== currentSessionId
        ) {
          return entry.sessionId;
        }
      } catch {
        // 無効なJSON行（truncated last line等）、スキップ
      }
    }

    return null;
  } catch (error) {
    debugLog(
      `[session] Error reading transcript for fork detection: ${transcriptPath}: ${formatError(error)}`,
    );
    return null;
  }
}

/**
 * Cache for isForkSession results.
 *
 * Key: `${sessionId}:${normalizedTranscriptPath}`, Value: result.
 * Fork status is determined at session start and never changes,
 * so caching by session ID + transcript path is safe.
 *
 * Note: No eviction needed because hooks run as short-lived Bun processes.
 * The cache is cleared when the process exits.
 */
const forkSessionCache = new Map<string, boolean>();

/**
 * Clear the fork session cache. For testing only.
 */
export function clearForkSessionCache(): void {
  forkSessionCache.clear();
}

/**
 * Detect if this is a fork-session (new session_id with conversation history).
 *
 * A fork-session occurs when --fork-session flag is used, creating a new
 * session_id while preserving conversation history from a parent session.
 *
 * Results are cached per session ID + transcript path to avoid repeated file I/O.
 */
export function isForkSession(
  currentSessionId: string,
  source: string,
  transcriptPath?: string | null | undefined,
): boolean {
  // source="compact" is context compression, not a fork
  if (source === "compact") {
    return false;
  }

  // Check cache
  const normalizedPath = transcriptPath ? resolve(transcriptPath) : "";
  const cacheKey = `${currentSessionId}:${normalizedPath}`;
  const cached = forkSessionCache.get(cacheKey);
  if (cached !== undefined) {
    return cached;
  }

  const cacheAndReturn = (result: boolean): boolean => {
    forkSessionCache.set(cacheKey, result);
    return result;
  };

  // Fork-session detection (runs regardless of source)
  if (transcriptPath) {
    if (!isValidTranscriptPath(transcriptPath)) {
      debugLog(`[session] Skipping fork detection: invalid transcript path: ${transcriptPath}`);
      return cacheAndReturn(false);
    }
  }

  // Primary detection: Compare hook's session_id with transcript filename
  const transcriptSessionId = extractSessionIdFromTranscriptPath(transcriptPath ?? null);
  // Only compare when currentSessionId is a valid UUID (not a placeholder like "unknown")
  const uuidPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
  if (
    transcriptSessionId &&
    uuidPattern.test(currentSessionId) &&
    transcriptSessionId !== currentSessionId
  ) {
    debugLog(
      `[session] Fork detected: hook_id=${currentSessionId.slice(0, 8)}... != transcript_id=${transcriptSessionId.slice(0, 8)}...`,
    );
    return cacheAndReturn(true);
  }

  // Secondary detection: check own transcript for a different sessionId
  // Only run for valid UUID session IDs (same guard as primary detection)
  if (!uuidPattern.test(currentSessionId)) {
    return cacheAndReturn(false);
  }
  const parentSessionId = getParentSessionIdFromTranscript(
    transcriptPath ?? null,
    currentSessionId,
  );
  if (parentSessionId) {
    debugLog(
      `[session] Fork detected via content: current_id=${currentSessionId.slice(0, 8)}... has parent_id=${parentSessionId.slice(0, 8)}...`,
    );
    return cacheAndReturn(true);
  }

  return cacheAndReturn(false);
}

/**
 * ツール実行結果がエラーかどうかを判定
 *
 * 以下のいずれかの場合にtrueを返す:
 * - result.errorが真値（null/undefined/false以外）
 * - result.blockedが真値（null/undefined/false以外）
 * - result.exit_codeが0以外の数値
 *
 * 注: `"error" in result`ではなく`result.error`で真偽値チェックすることで、
 * `{ error: null }`や`{ blocked: false }`を誤検知しない。
 *
 * Changelog:
 *   - silenvx/dekita#3207: plan_ai_review.tsから共通化
 *   - silenvx/dekita#3236: 真偽値チェックに変更（error: null誤検知防止）
 */
export function isToolResultError(toolResult: unknown): boolean {
  if (!toolResult || typeof toolResult !== "object") {
    return false;
  }
  const result = toolResult as Record<string, unknown>;
  if (result.error || result.blocked) {
    return true;
  }
  return typeof result.exit_code === "number" && result.exit_code !== 0;
}

/**
 * Return all distinct session IDs found in a transcript in order of appearance.
 */
export function getSessionAncestry(transcriptPath: string | null): string[] {
  if (!transcriptPath) {
    return [];
  }

  try {
    const resolvedPath = resolve(transcriptPath);

    if (!isValidTranscriptPath(resolvedPath)) {
      return [];
    }

    if (!existsSync(resolvedPath)) {
      return [];
    }

    const seenSessionIds = new Set<string>();
    const orderedSessionIds: string[] = [];

    const content = readFileSync(resolvedPath, "utf-8");
    const lines = content.split("\n");

    for (const line of lines) {
      const trimmedLine = line.trim();
      if (!trimmedLine) continue;

      try {
        const entry = JSON.parse(trimmedLine);
        const sessionId = entry.sessionId;
        // Only include valid (non-null, non-empty) sessionIds
        if (sessionId && !seenSessionIds.has(sessionId)) {
          seenSessionIds.add(sessionId);
          orderedSessionIds.push(sessionId);
        }
      } catch {
        // 無効なJSON行、スキップ
      }
    }

    return orderedSessionIds;
  } catch {
    debugLog(`[session] Error while reading transcript for ancestry: ${transcriptPath}`);
    return [];
  }
}

// =============================================================================
// CI Monitor Session Management
// =============================================================================

/**
 * Module-level session ID storage for ci-monitor.
 *
 * This is separate from HookContext-based session management.
 * Used by ci_monitor_ts scripts to track the session ID passed via --session-id.
 */
let _ciMonitorSessionId: string | null = null;

/**
 * Set the session ID for ci-monitor logging.
 *
 * This should be called once at startup with the session ID from --session-id argument.
 *
 * @param sessionId - Session ID string (UUID format) or null
 */
export function setCiMonitorSessionId(sessionId: string | null): void {
  _ciMonitorSessionId = sessionId;
}

/**
 * Get the current ci-monitor session ID.
 *
 * @returns Session ID string or null if not set
 */
export function getCiMonitorSessionId(): string | null {
  return _ciMonitorSessionId;
}
