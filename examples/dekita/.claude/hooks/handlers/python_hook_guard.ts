#!/usr/bin/env bun
/**
 * Pythonフック新規作成をブロックし、TypeScript使用を推奨する
 *
 * Why:
 *   Issue #2816でPython → TypeScript移行計画があるにもかかわらず、
 *   新規フックをPythonで実装してしまうミスを防止する。
 *
 * What:
 *   - WriteツールでPythonファイル作成を検出
 *   - .claude/hooks/配下への新規.pyファイル作成をブロック
 *   - TypeScript版での実装を推奨
 *
 * Remarks:
 *   - 既存ファイルの編集は許可（移行中のため）
 *   - tests/配下のテストファイルは許可
 *   - lib/配下のユーティリティは許可（段階的移行）
 *
 * Changelog:
 *   - silenvx/dekita#2827: TypeScript版初期実装
 */

import { existsSync } from "node:fs";
import { basename } from "node:path";
import { z } from "zod";
import { formatError } from "../lib/format_error";
import { approveAndExit, blockAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "python-hook-guard";

/** Writeツール入力のスキーマ */
const WriteToolInputSchema = z.object({
  file_path: z.string().optional(),
});

/** Pythonフックファイルのパターン（サブディレクトリも含む） */
// Codex review: [^/]+\.py$ だとサブディレクトリが漏れるため .*\.py$ に修正
const PYTHON_HOOK_PATTERN = /\.claude\/hooks\/.*\.py$/;

/** 除外パターン（これらは許可）
 * 注: .claude/hooks/を含めることで、親ディレクトリ名（/Users/alice/tests/...等）によるバイパスを防止
 * Issue #2831: Codex P2指摘対応
 */
const EXCLUDE_PATTERNS = [
  /\.claude\/hooks\/tests?\//, // .claude/hooks/tests/ or .claude/hooks/test/
  /\.claude\/hooks\/lib\//, // .claude/hooks/lib/
  /\.claude\/hooks\/handlers\//, // フック実装配下
  /\.claude\/hooks\/scripts\//, // フックスクリプト配下
];

/**
 * ファイルパスが除外パターンに該当するかチェック
 */
function isExcludedPath(filePath: string): boolean {
  return EXCLUDE_PATTERNS.some((pattern) => pattern.test(filePath));
}

/**
 * ファイルパスが.claude/hooks/配下のPythonファイルかチェック
 */
function isPythonHookFile(filePath: string): boolean {
  return PYTHON_HOOK_PATTERN.test(filePath);
}

/**
 * メイン処理
 */
async function main(): Promise<void> {
  const hookInput = await parseHookInput();
  const toolName = hookInput.tool_name ?? "";

  // Writeツールのみ対象
  if (toolName !== "Write") {
    approveAndExit(HOOK_NAME);
  }

  // tool_inputをパース
  const parseResult = WriteToolInputSchema.safeParse(hookInput.tool_input);
  const filePath = parseResult.success ? (parseResult.data.file_path ?? "") : "";

  if (!filePath) {
    approveAndExit(HOOK_NAME);
  }

  // .claude/hooks/配下のPythonファイルでない場合は対象外
  if (!isPythonHookFile(filePath)) {
    approveAndExit(HOOK_NAME);
  }

  // 除外パターンに該当する場合は対象外
  if (isExcludedPath(filePath)) {
    approveAndExit(HOOK_NAME);
  }

  // 既存ファイルへの書き込みは許可（編集は移行中なのでOK）
  if (existsSync(filePath)) {
    approveAndExit(HOOK_NAME);
  }

  // 新規Pythonフック作成をブロック
  const fileName = basename(filePath);
  const tsFileName = fileName.replace(/\.py$/, ".ts");
  const tsDir = ".claude/hooks/handlers";

  blockAndExit(
    HOOK_NAME,
    `⚠️ 新規Pythonフックの作成をブロックしました

**作成しようとしたファイル**: \`${filePath}\`

**理由**: Issue #2816のPython → TypeScript移行計画に基づき、新規フックはTypeScriptで実装してください。

**推奨される対応**:
1. TypeScript版で実装: \`${tsDir}/${tsFileName}\`
2. 既存のTypeScriptフックを参考に実装

**参照パターン**:
- \`.claude/hooks/handlers/scope_check.ts\` - PreToolUse:Writeフックの例
- \`.claude/hooks/handlers/issue_creation_detector.ts\` - UserPromptSubmitフックの例

**例外（許可されるケース）**:
- 既存Pythonファイルの編集（移行中のため）
- tests/配下のテストファイル
- lib/配下のユーティリティ`,
  );
}

// 実行（テスト時はスキップ）
if (import.meta.main) {
  main().catch((error) => {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    process.exit(1);
  });
}

// テスト用にエクスポート
export { isPythonHookFile, isExcludedPath };
