#!/usr/bin/env bun
/**
 * 大きすぎるファイルの読み込み時にリファクタリングを促す警告を表示する。
 *
 * Why:
 *   AIがファイルを読み込む際、大きすぎるファイルは認知負荷が高く、
 *   凝集度・結合度・責務の観点から分割を検討すべき場合がある。
 *
 * What:
 *   - PreToolUse (Read) でファイル読み込み時に行数をチェック
 *   - 閾値超過時に警告メッセージを表示（ブロックはしない）
 *   - 言語別の閾値: TS/JS 400行、Python 500行、その他 500行
 *
 * Remarks:
 *   - 警告のみ（approve with systemMessage）でブロックしない
 *   - テストファイル、型定義、生成ファイル、設定ファイルは除外
 *   - AGENTS.md, CLAUDE.md等の意図的に長いドキュメントも除外
 *
 * Changelog:
 *   - silenvx/dekita#2625: フック追加
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { basename, relative } from "node:path";
import {
  FILE_SIZE_THRESHOLD_DEFAULT,
  FILE_SIZE_THRESHOLD_PY,
  FILE_SIZE_THRESHOLD_TS,
} from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "file-size-warning";

// 除外パターン（種類別）

// 拡張子・接尾辞パターン（endswith）
export const SUFFIX_PATTERNS = [
  ".test.ts",
  ".test.tsx",
  ".spec.ts",
  ".spec.tsx",
  "_test.py",
  ".d.ts",
  ".json",
  ".yaml",
  ".yml",
  ".toml",
  ".lock",
];

// 接頭辞パターン（startswith）
export const PREFIX_PATTERNS = ["test_"];

// ディレクトリパターン（contains）
export const DIRECTORY_PATTERNS = ["generated", "dist", "node_modules", "__pycache__", "build"];

// 完全一致パターン
export const EXACT_PATTERNS = ["AGENTS.md", "CLAUDE.md", "SKILL.md"];

/**
 * 除外対象のファイルかどうかを判定する。
 */
export function shouldExclude(filePath: string): boolean {
  if (!filePath) {
    return true;
  }

  // パスを正規化
  const normalized = filePath.replace(/\\/g, "/");
  const name = basename(normalized);

  // 接尾辞パターン（拡張子など）
  for (const pattern of SUFFIX_PATTERNS) {
    if (normalized.endsWith(pattern)) {
      return true;
    }
  }

  // 接頭辞パターン（test_など）
  for (const pattern of PREFIX_PATTERNS) {
    if (name.startsWith(pattern)) {
      return true;
    }
  }

  // ディレクトリパターン
  const pathParts = normalized.split("/");
  for (const dirName of DIRECTORY_PATTERNS) {
    if (pathParts.includes(dirName)) {
      return true;
    }
  }

  // 完全一致パターン
  for (const pattern of EXACT_PATTERNS) {
    if (name === pattern) {
      return true;
    }
  }

  return false;
}

/**
 * ファイルの拡張子に基づいて閾値を返す。
 */
export function getThreshold(filePath: string): number {
  if (!filePath) {
    return FILE_SIZE_THRESHOLD_DEFAULT;
  }

  const lowerPath = filePath.toLowerCase();

  // TypeScript/JavaScript
  if (
    lowerPath.endsWith(".ts") ||
    lowerPath.endsWith(".tsx") ||
    lowerPath.endsWith(".js") ||
    lowerPath.endsWith(".jsx") ||
    lowerPath.endsWith(".mjs") ||
    lowerPath.endsWith(".cjs")
  ) {
    return FILE_SIZE_THRESHOLD_TS;
  }

  // Python
  if (lowerPath.endsWith(".py")) {
    return FILE_SIZE_THRESHOLD_PY;
  }

  return FILE_SIZE_THRESHOLD_DEFAULT;
}

/**
 * ファイルの行数をカウントする。エラー時はnullを返す。
 */
function countLines(filePath: string): number | null {
  try {
    const content = readFileSync(filePath, "utf-8");
    return content.split("\n").length;
  } catch {
    return null;
  }
}

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolName = data.tool_name || "";
    const toolInput = data.tool_input || {};

    // Readツールのみ対象
    if (toolName !== "Read") {
      console.log(JSON.stringify(result));
      return;
    }

    const filePath = (toolInput.file_path as string) || "";

    // 除外対象はスキップ
    if (shouldExclude(filePath)) {
      await logHookExecution(HOOK_NAME, "skip", `Excluded: ${filePath}`, undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // ファイルが存在しない場合はスキップ
    if (!existsSync(filePath)) {
      console.log(JSON.stringify(result));
      return;
    }

    try {
      const stat = statSync(filePath);
      if (!stat.isFile()) {
        console.log(JSON.stringify(result));
        return;
      }
    } catch {
      console.log(JSON.stringify(result));
      return;
    }

    // 行数カウント
    const lineCount = countLines(filePath);
    if (lineCount === null) {
      console.log(JSON.stringify(result));
      return;
    }

    // 閾値チェック
    const threshold = getThreshold(filePath);
    if (lineCount > threshold) {
      // 相対パス表示用
      let displayPath: string;
      try {
        displayPath = relative(process.cwd(), filePath);
      } catch {
        displayPath = filePath;
      }

      const warningMessage = `このファイルは大きいです（${lineCount}行 > ${threshold}行閾値）\n\n${displayPath}\n\nリファクタリングを検討:\n- 単一責任: このファイルは複数の責務を持っていませんか？\n- 凝集度: 関連する機能がまとまっていますか？\n- 結合度: 他モジュールへの依存が多すぎませんか？`;

      result.systemMessage = warningMessage;

      await logHookExecution(
        HOOK_NAME,
        "warn",
        `${displayPath}: ${lineCount} lines > ${threshold} threshold`,
        undefined,
        { sessionId },
      );
    } else {
      await logHookExecution(HOOK_NAME, "approve", `${filePath}: ${lineCount} lines`, undefined, {
        sessionId,
      });
    }
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
