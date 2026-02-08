#!/usr/bin/env bun
/**
 * 新規Pythonフック追加をブロックする
 *
 * Why:
 *   新規フックはTypeScript（Bun）で統一し、型安全性と高速起動を確保する。
 *   既存Pythonフックは保守的に維持するが、新規追加は禁止。
 *
 * What:
 *   - .claude/hooks/ 以下の全ての新規 .py ファイル作成を検出
 *   - 除外: ts/, tests/, test/, lib/ ディレクトリ
 *   - 既存ファイルの編集は許可
 *   - 新規ファイル作成のみをブロック
 *
 * State:
 *   - reads: file system (existsSync)
 *
 * Remarks:
 *   - ブロック型フック（新規Pythonフック作成時にブロック）
 *   - PreToolUse:Writeで発火
 *   - 既存Pythonフックの編集は許可（レガシーコードの保守）
 *
 * Changelog:
 *   - silenvx/dekita#2858: TypeScript版初期実装
 */

import { existsSync } from "node:fs";
import { z } from "zod";
import { formatError } from "../lib/format_error";
import { approveAndExit, blockAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "new-python-hook-check";

/** Writeツール入力のスキーマ */
const WriteToolInputSchema = z.object({
  file_path: z.string().optional(),
});

/** Pythonフックファイルのパターン */
const PYTHON_HOOK_PATTERN = /\.claude\/hooks\/.*\.py$/;

/**
 * 除外パターン（これらは許可）
 * 注: .claude/hooks/を含めることで、親ディレクトリ名によるバイパスを防止
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
  blockAndExit(
    HOOK_NAME,
    `⚠️ 新規Pythonフックの作成をブロックしました

**作成しようとしたファイル**: \`${filePath}\`

**理由**: 新規フックはTypeScriptで実装してください。

**推奨される対応**:
1. TypeScript版で実装: \`.claude/hooks/handlers/<hook_name>.ts\`
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
