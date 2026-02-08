#!/usr/bin/env bun
/**
 * 正規表現パターン実装時にAGENTS.mdチェックリストを表示。
 *
 * Why:
 *   正規表現実装でよくあるミス（成功条件確認漏れ、フラグ不一致等）を
 *   防ぐため、編集時にチェックリストを提示する。
 *
 * What:
 *   - Edit操作でPythonファイルを検出
 *   - new_stringに正規表現パターン（re.compile, PATTERN= 等）があるか確認
 *   - 検出時はAGENTS.mdのチェックリストをsystemMessageで表示
 *   - 同一ファイルへのリマインドは1セッション1回
 *
 * State:
 *   - writes: /tmp/claude-hooks/regex-pattern-reminded-{session_id}.json
 *
 * Remarks:
 *   - 非ブロック型（情報提供のみ）
 *   - PreToolUse:Edit フック
 *   - AGENTS.md「パターンマッチング実装（P1）」を仕組み化
 *   - Python版: regex_pattern_reminder.py
 *
 * Changelog:
 *   - silenvx/dekita#2375: フック追加（パターンマッチング実装チェック漏れ防止）
 *   - silenvx/dekita#2529: ppidフォールバック廃止
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { relative, resolve } from "node:path";
import { SESSION_DIR } from "../lib/constants";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult } from "../lib/results";
import { createContext, getSessionId, parseHookInput } from "../lib/session";

const HOOK_NAME = "regex-pattern-reminder";

// Regex patterns to detect in new_string
const REGEX_DETECTION_PATTERNS = [
  /re\.compile\s*\(/,
  /re\.search\s*\(/,
  /re\.match\s*\(/,
  /re\.findall\s*\(/,
  /re\.sub\s*\(/,
  /re\.split\s*\(/,
  /[A-Z_]*PATTERN\s*=/, // PATTERN = , _PATTERN = , SOME_PATTERN =
  /[A-Z_]*PATTERNS\s*=/, // PATTERNS = , _PATTERNS =
];

/**
 * Get the confirmation file path for tracking reminded files.
 */
function getConfirmationFilePath(sessionId: string): string {
  return `${SESSION_DIR}/regex-pattern-reminded-${sessionId || "unknown"}.json`;
}

/**
 * Load reminded files from session file.
 */
function loadRemindedFiles(sessionId: string): Set<string> {
  try {
    const confFile = getConfirmationFilePath(sessionId);
    if (existsSync(confFile)) {
      const data = JSON.parse(readFileSync(confFile, "utf-8"));
      return new Set(data.files ?? []);
    }
  } catch {
    // File doesn't exist or is corrupted - treat as empty
  }
  return new Set();
}

/**
 * Save reminded files to session file.
 */
function saveRemindedFiles(sessionId: string, files: Set<string>): void {
  try {
    mkdirSync(SESSION_DIR, { recursive: true });
    const confFile = getConfirmationFilePath(sessionId);
    writeFileSync(confFile, JSON.stringify({ files: [...files] }), "utf-8");
  } catch {
    // Best effort - don't fail on I/O errors
  }
}

/**
 * Check if file is a Python file.
 */
export function isPythonFile(filePath: string): boolean {
  return filePath.endsWith(".py");
}

/**
 * Check if new_string contains regex pattern definitions.
 */
export function containsRegexPattern(newString: string): boolean {
  if (!newString) {
    return false;
  }
  return REGEX_DETECTION_PATTERNS.some((pattern) => pattern.test(newString));
}

/**
 * Check if file has been reminded in current session.
 */
export function isRemindedInSession(sessionId: string, filePath: string): boolean {
  const normalizedPath = resolve(filePath);
  const reminded = loadRemindedFiles(sessionId);
  return reminded.has(normalizedPath);
}

/**
 * Mark file as reminded in current session.
 */
function markAsReminded(sessionId: string, filePath: string): void {
  const normalizedPath = resolve(filePath);
  const reminded = loadRemindedFiles(sessionId);
  reminded.add(normalizedPath);
  saveRemindedFiles(sessionId, reminded);
}

/**
 * Get project root directory.
 */
function getProjectRoot(): string {
  return process.env.CLAUDE_PROJECT_DIR ?? process.cwd();
}

/**
 * Get relative path from project root.
 */
function getRelativePath(filePath: string): string | null {
  const projectRoot = resolve(getProjectRoot());
  const resolvedPath = resolve(filePath);
  try {
    const rel = relative(projectRoot, resolvedPath);
    // Check if path is within project
    if (rel.startsWith("..") || rel.startsWith("/")) {
      return null;
    }
    return rel;
  } catch {
    return null;
  }
}

async function main(): Promise<void> {
  const data = await parseHookInput();
  const ctx = createContext(data);
  const sessionId = getSessionId(ctx) ?? "unknown";

  const toolInput = (data.tool_input as Record<string, unknown>) ?? {};
  const filePath = (toolInput.file_path as string) ?? "";
  const newString = (toolInput.new_string as string) ?? "";

  // Default result
  let result = makeApproveResult(HOOK_NAME);

  try {
    if (!filePath) {
      // No file path provided, skip
    } else if (!isPythonFile(filePath)) {
      // Not a Python file, skip
    } else if (!containsRegexPattern(newString)) {
      // No regex patterns in new_string, skip
    } else if (isRemindedInSession(sessionId, filePath)) {
      // Already reminded in this session
    } else {
      // First regex pattern edit - show checklist
      const relPath = getRelativePath(filePath) ?? filePath;

      const systemMessage = `⚠️ パターンマッチング実装チェックリスト (${relPath})

**AGENTS.md「パターンマッチング実装（P1）」より:**

| チェック項目 | 説明 |
|-------------|------|
| **複数条件の組み合わせ** | 「成功条件の存在」を積極的に確認し、「失敗条件の不在」のみで成功と判断しない |
| **フラグの一貫性** | \`re.IGNORECASE\` 等のフラグは全ての検索で統一する |
| **テストのリアリティ** | 実際の出力を模倣（stdout/stderr両方を考慮） |
| **距離制限** | \`.*\` を使用する場合、\`.{0,N}\` のように距離制限を検討 |

💡 実装前にエッジケースを洗い出し、テストケースを先に書くと見落としを防げます。`;

      result = {
        systemMessage: `[${HOOK_NAME}] ${systemMessage}`,
      };

      // Mark as reminded for this session
      markAsReminded(sessionId, filePath);
    }
  } catch (e) {
    // Don't block on errors
    const errorMsg = e instanceof Error ? e.message : String(e);
    console.error(`[${HOOK_NAME}] Hook error: ${errorMsg}`);
    result = makeApproveResult(HOOK_NAME);
  }

  await logHookExecution(
    HOOK_NAME,
    result.decision === "block" ? "block" : "approve",
    result.systemMessage,
    filePath ? { file_path: filePath } : undefined,
    { sessionId },
  );
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
