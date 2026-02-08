#!/usr/bin/env bun
/**
 * 仕様ドキュメント編集時に関連コード・Issueの確認を促す。
 *
 * Why:
 *   ドキュメントを根拠なしに編集すると、誤記や実装との矛盾が生じる。
 *   「状態確認ファースト原則」をドキュメント編集にも適用する。
 *
 * What:
 *   - .claude/skills/, AGENTS.md等の編集を検出
 *   - 関連コード・Issue確認を促すメッセージを表示
 *   - セッション内で同一ファイルへの重複警告を防止
 *
 * State:
 *   - writes: {TMPDIR}/claude-hooks/doc-edit-confirmed-{session}.json
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、systemMessageで警告）
 *   - PreToolUse:Edit/Writeで発火
 *   - セッション内で同一ファイルへの重複警告防止
 *   - .claude/skills/, AGENTS.md等を対象
 *
 * Changelog:
 *   - silenvx/dekita#1848: フック追加
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { relative, resolve } from "node:path";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "doc-edit-check";

// Target path prefixes for specification documents (with .md extension check)
const TARGET_PREFIXES = [".claude/skills/", ".claude/prompts/"];

// Exact match files
const TARGET_EXACT = ["AGENTS.md"];

/**
 * Get project root directory.
 */
function getProjectRoot(): string {
  return process.env.CLAUDE_PROJECT_DIR || process.cwd();
}

/**
 * Get path to session confirmation tracking file.
 */
function getConfirmationFilePath(sessionId: string): string {
  const baseDir = `${tmpdir()}/claude-hooks`;
  if (!existsSync(baseDir)) {
    mkdirSync(baseDir, { recursive: true });
  }
  return `${baseDir}/doc-edit-confirmed-${sessionId}.json`;
}

/**
 * Load confirmed files from session file.
 */
function loadConfirmedFiles(sessionId: string): Set<string> {
  try {
    const confFile = getConfirmationFilePath(sessionId);
    if (existsSync(confFile)) {
      const data = JSON.parse(readFileSync(confFile, "utf-8"));
      return new Set(data.files || []);
    }
  } catch {
    // File doesn't exist or is corrupted - treat as empty
  }
  return new Set();
}

/**
 * Save confirmed files to session file.
 */
function saveConfirmedFiles(sessionId: string, files: Set<string>): void {
  try {
    const confFile = getConfirmationFilePath(sessionId);
    writeFileSync(confFile, JSON.stringify({ files: Array.from(files) }));
  } catch {
    // Best effort - don't fail on I/O errors
  }
}

/**
 * Get relative path from project root.
 */
export function getRelativePath(filePath: string): string | null {
  const projectRoot = resolve(getProjectRoot());
  const resolvedPath = resolve(filePath);
  try {
    const rel = relative(projectRoot, resolvedPath);
    // Check if path is outside project (starts with ..)
    if (rel.startsWith("..") || rel.startsWith("/")) {
      return null;
    }
    return rel;
  } catch {
    return null;
  }
}

/**
 * Check if file path matches any target pattern.
 */
export function matchesTargetPattern(filePath: string): boolean {
  const relPath = getRelativePath(filePath);
  if (!relPath) {
    return false;
  }

  // Check exact matches first
  if (TARGET_EXACT.includes(relPath)) {
    return true;
  }

  // Check prefix matches (must be .md files)
  if (!relPath.endsWith(".md")) {
    return false;
  }

  for (const prefix of TARGET_PREFIXES) {
    if (relPath.startsWith(prefix)) {
      return true;
    }
  }

  return false;
}

/**
 * Check if file has been confirmed in current session.
 */
function isConfirmedInSession(sessionId: string, filePath: string): boolean {
  const normalizedPath = resolve(filePath);
  const confirmed = loadConfirmedFiles(sessionId);
  return confirmed.has(normalizedPath);
}

/**
 * Mark file as confirmed in current session.
 */
function markAsConfirmed(sessionId: string, filePath: string): void {
  const normalizedPath = resolve(filePath);
  const confirmed = loadConfirmedFiles(sessionId);
  confirmed.add(normalizedPath);
  saveConfirmedFiles(sessionId, confirmed);
}

async function main(): Promise<void> {
  let result: {
    decision?: string;
    reason?: string;
    systemMessage?: string;
  } = {};
  let filePath = "";

  try {
    const data = await parseHookInput();
    const sessionId = data.session_id || "unknown";
    const toolInput = data.tool_input || {};
    filePath = (toolInput.file_path as string) || "";

    if (!filePath) {
      // No file path provided, skip
      result.systemMessage = "✅ doc-edit-check: パス未指定（スキップ）";
    } else if (!matchesTargetPattern(filePath)) {
      // Not a target document, skip
    } else if (isConfirmedInSession(sessionId, filePath)) {
      // Already confirmed in this session
      result.systemMessage = "✅ doc-edit-check: セッション内で確認済み";
    } else {
      // First edit to a spec document - show warning
      const relPath = getRelativePath(filePath) || filePath;

      result.systemMessage = `⚠️ 仕様ドキュメント編集の確認 (${relPath})\n\nこのファイルで言及するコード/仕様の根拠を確認しましたか？\n\n**確認すべき項目:**\n- 関連コード: Grep/Read で実装を確認\n- 関連Issue: gh issue list --search "キーワード"\n- 既存ドキュメント: 類似の記載がないか確認\n\n💡 根拠を確認してから編集すると、誤記や矛盾を防げます。`;

      // Mark as confirmed for this session
      markAsConfirmed(sessionId, filePath);
    }
  } catch (error) {
    // Don't block on errors
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    result = { reason: `Hook error: ${formatError(error)}` };
  }

  logHookExecution(HOOK_NAME, result.decision ?? "approve", result.systemMessage);
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
