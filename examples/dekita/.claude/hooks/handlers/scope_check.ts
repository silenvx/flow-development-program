#!/usr/bin/env bun
/**
 * 作業中Issueへのスコープ外タスク混入を検出する
 *
 * Why:
 *   Issue #Xの作業中に関係のないタスク（Issue #Y相当）を同じPRに混入すると：
 *   - PRのスコープが曖昧になる
 *   - レビューが困難になる
 *   - 問題発生時の切り分けが難しくなる
 *
 * What:
 *   - Writeツールで新規ファイル作成を検出
 *   - worktree/ブランチ名からIssue番号を抽出
 *   - 新規機能ファイル（フック、コンポーネント等）作成時に警告
 *   - 「作業中のIssue #Xのスコープ内ですか？」と確認
 *
 * Remarks:
 *   - ブロックではなく警告のみ（approve + message）
 *   - 既存ファイルの編集は対象外
 *   - テストファイルや設定ファイルは警告対象外
 *
 * Changelog:
 *   - silenvx/dekita#2816: TypeScript版初期実装
 */

import { existsSync } from "node:fs";
import { basename } from "node:path";
import { z } from "zod";
import { formatError } from "../lib/format_error";
import { approveAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";

/** Writeツール入力のスキーマ */
const WriteToolInputSchema = z.object({
  file_path: z.string().optional(),
});

const HOOK_NAME = "scope-check";

/** 新規作成時に警告する対象パターン */
const FEATURE_FILE_PATTERNS = [
  /\.claude\/hooks\/[^/]+\.(py|ts)$/, // フック
  /\.claude\/scripts\/[^/]+\.(py|sh|ts)$/, // スクリプト
  /frontend\/src\/components\/[^/]+\.(tsx|ts)$/, // コンポーネント
  /frontend\/src\/routes\/[^/]+\.(tsx|ts)$/, // ルート
  /worker\/src\/[^/]+\.ts$/, // Worker
];

/** 警告対象外のパターン */
const EXCLUDE_PATTERNS = [
  /\/tests?\//, // テストファイル
  /\.test\.(ts|tsx|py)$/, // テストファイル
  /_test\.py$/, // テストファイル
  /\.config\.(ts|js)$/, // 設定ファイル
  /\.json$/, // 設定ファイル
  /\.md$/, // ドキュメント
];

/**
 * 現在のworktree/ブランチからIssue番号を抽出する
 *
 * @returns Issue番号、または抽出できない場合はnull
 */
async function getCurrentIssueNumber(): Promise<number | null> {
  // まずworktree名から取得を試みる
  const cwd = process.cwd();
  const worktreeMatch = cwd.match(/\/\.worktrees\/issue-(\d+)/);
  if (worktreeMatch) {
    return Number.parseInt(worktreeMatch[1], 10);
  }

  // ブランチ名から取得を試みる
  try {
    const proc = Bun.spawn(["git", "rev-parse", "--abbrev-ref", "HEAD"], {
      stdout: "pipe",
      stderr: "pipe",
    });
    const output = await new Response(proc.stdout).text();
    const exitCode = await proc.exited;

    if (exitCode === 0) {
      const branch = output.trim();
      // feat/issue-123-desc, fix/issue-456 等のパターン
      const branchMatch = branch.match(/issue-(\d+)/i);
      if (branchMatch) {
        return Number.parseInt(branchMatch[1], 10);
      }
    }
  } catch {
    // git command failed, return null
  }

  return null;
}

/**
 * ファイルが機能ファイル（フック、コンポーネント等）かどうか判定する
 *
 * @param filePath チェックするファイルパス
 * @returns 機能ファイルの場合true
 */
export function isFeatureFile(filePath: string): boolean {
  // 除外パターンに該当する場合はfalse
  for (const pattern of EXCLUDE_PATTERNS) {
    if (pattern.test(filePath)) {
      return false;
    }
  }

  // 機能ファイルパターンに該当する場合はtrue
  for (const pattern of FEATURE_FILE_PATTERNS) {
    if (pattern.test(filePath)) {
      return true;
    }
  }

  return false;
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

  // Zodを使った型安全なパース（AIレビュー対応）
  const parseResult = WriteToolInputSchema.safeParse(hookInput.tool_input);
  const filePath = parseResult.success ? (parseResult.data.file_path ?? "") : "";

  if (!filePath) {
    approveAndExit(HOOK_NAME);
  }

  // 既存ファイルへの書き込みは対象外（編集はスコープ内と見なす）
  if (existsSync(filePath)) {
    approveAndExit(HOOK_NAME);
  }

  // 機能ファイルでない場合は対象外
  if (!isFeatureFile(filePath)) {
    approveAndExit(HOOK_NAME);
  }

  // Issue番号を取得
  const issueNumber = await getCurrentIssueNumber();

  if (issueNumber === null) {
    // Issue番号が不明な場合は警告なし（mainブランチ等）
    approveAndExit(HOOK_NAME);
  }

  // 新規機能ファイル作成を検出 → 警告
  const fileName = basename(filePath);

  // systemMessageを使用（AIレビュー対応: messageではなくsystemMessageが正しい）
  const result = {
    systemMessage: `[${HOOK_NAME}] ⚠️ 新規機能ファイルを作成しようとしています。

**作成するファイル:** \`${fileName}\`
**作業中のIssue:** #${issueNumber}

**確認事項:**
- このファイルはIssue #${issueNumber}のスコープ内ですか？
- 別のIssue相当の機能を混入していませんか？

**スコープ外の場合:**
1. 作業を一時中断
2. 新しいIssueを作成（\`gh issue create\`）
3. 別のworktreeで作業

Issue #2816: スコープ外タスク混入防止のための警告です。`,
  };

  console.log(JSON.stringify(result));
  process.exit(0);
}

// 実行（テスト時はスキップ）
if (import.meta.main) {
  main().catch((error) => {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    process.exit(1);
  });
}
