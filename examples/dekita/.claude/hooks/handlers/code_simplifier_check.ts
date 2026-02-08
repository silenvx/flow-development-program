#!/usr/bin/env bun
/**
 * simplifying-code Skill実行をPR作成前に強制する
 *
 * Why:
 *   AI生成コードは無駄に複雑になりがちで、レビューで指摘されてから修正すると
 *   手戻りが発生する。PR作成前にsimplifying-codeを実行することで、
 *   コードの複雑さを事前に検知・修正できる。
 *
 * What:
 *   - gh pr createコマンドを検出
 *   - 現在のブランチでsimplifying-codeが実行済みか確認
 *   - 未実行の場合はSkill実行を促すメッセージを表示
 *   - コードファイルがない変更は自動スキップ（ホワイトリスト方式）
 *
 * State:
 *   - reads: .claude/logs/markers/code-simplifier-{branch}.done
 *
 * Remarks:
 *   - ブロック型フック（未実行時はブロック）
 *   - PreToolUse:Bashで発火（gh pr createコマンド）
 *   - main/masterブランチはスキップ
 *   - コードファイルがない変更はスキップ（simplifying-codeはコード向け）
 *   - SKIP_CODE_SIMPLIFIER=1でバイパス可能
 *   - code_simplifier_logger.tsと連携（マーカーファイル読み込み）
 *
 * Changelog:
 *   - silenvx/dekita#3499: Task agentからSkillに移行、simplifying-code Skillを案内
 *   - silenvx/dekita#3450: コードファイルがない変更を自動スキップ（ホワイトリスト方式）
 *   - silenvx/dekita#3090: /simplifyからTask agent形式に修正
 *   - silenvx/dekita#3021: hasSkipEnvをlib/strings.tsのcheckSkipEnvに統合
 *   - silenvx/dekita#3006: 初期実装
 */

import { existsSync } from "node:fs";
import { formatError } from "../lib/format_error";
import { getCurrentBranch, getOriginDefaultBranch } from "../lib/git";
import { getMarkersDir } from "../lib/markers";
import { approveAndExit, blockAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { asyncSpawn } from "../lib/spawn";
import { checkSkipEnv, sanitizeBranchName, stripQuotedStrings } from "../lib/strings";

/** Code file extensions that code-simplifier should check */
export const CODE_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py"];

/**
 * Check if a filename is a code file based on extension.
 * Returns false for files without extension.
 */
export function isCodeFile(filename: string): boolean {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot === -1) return false;
  const ext = filename.slice(lastDot).toLowerCase();
  return CODE_EXTENSIONS.includes(ext);
}

/** Pattern to detect 'gh pr create' commands */
export const GH_PR_CREATE_PATTERN = /gh\s+pr\s+create\b/;

/** Environment variable to skip check */
const SKIP_ENV = "SKIP_CODE_SIMPLIFIER";

/**
 * Check if command is a gh pr create command
 */
export function isGhPrCreateCommand(command: string): boolean {
  if (!command.trim()) return false;
  const stripped = stripQuotedStrings(command);
  return GH_PR_CREATE_PATTERN.test(stripped) && !command.includes("--help");
}

/**
 * Check if skip environment variable is set.
 * Delegates to lib/strings.ts's checkSkipEnv for consistent SKIP_* handling.
 */
export function hasSkipEnv(command: string): boolean {
  return checkSkipEnv("code-simplifier-check", SKIP_ENV, { input_preview: command });
}

/**
 * Check if code-simplifier was executed for this branch
 */
export function checkSimplifierDone(branch: string): boolean {
  const safeBranch = sanitizeBranchName(branch);
  const logFile = `${getMarkersDir()}/code-simplifier-${safeBranch}.done`;
  return existsSync(logFile);
}

/** Timeout for git commands in milliseconds */
const GIT_TIMEOUT_MS = 10_000;

/**
 * Check if the branch has code file changes.
 * Returns true if any code files are changed, false otherwise.
 * On git failure, returns true to be safe.
 */
export async function hasCodeFiles(): Promise<boolean> {
  try {
    const originBranch = await getOriginDefaultBranch(process.cwd());
    const result = await asyncSpawn("git", ["diff", "--name-only", `${originBranch}...HEAD`], {
      timeout: GIT_TIMEOUT_MS,
    });
    if (!result.success) {
      return true;
    }
    const files = result.stdout.trim().split("\n").filter(Boolean);
    return files.some(isCodeFile);
  } catch {
    return true;
  }
}

/**
 * Generate block reason message
 */
function getBlockReason(branch: string): string {
  return `# simplifying-codeが未実行です

PR作成前にコードの複雑さをチェックしてください。

**ブランチ**: ${branch}

**対処法**:

\`/simplifying-code\` Skillを実行してください:

> 「このブランチの変更をsimplifying-codeでチェックして」

**simplifying-codeとは**:
- AI生成コードの肥大化・複雑化を検出
- 不要な抽象化、冗長なエラーハンドリング等を指摘
- シンプルで保守しやすいコードへの改善を提案
- **スコープ制限**: このPR/ブランチでmainから変更した部分のみ対象
- **機能保持**: テスト結果が変わる変更は禁止

**スキップする場合**（非推奨）:
\`\`\`bash
SKIP_CODE_SIMPLIFIER=1 gh pr create ...
\`\`\`

> [!WARNING]
> **注意**: code-simplifierは動作保証をしません。出力をコミットする前に:
>
> - 変更された関数のエッジケースを手動検証（空文字、特殊文字、境界値等）
> - 特に条件分岐が減った場合は、削除された分岐のケースを確認`;
}

const HOOK_NAME = "code-simplifier-check";

/**
 * メイン処理
 */
async function main(): Promise<void> {
  try {
    const input = await parseHookInput();
    const toolInput = input.tool_input as Record<string, unknown> | undefined;
    const command = (toolInput?.command as string) ?? "";

    // Only check gh pr create command
    if (!isGhPrCreateCommand(command)) {
      approveAndExit(HOOK_NAME);
      return;
    }

    // Check for skip environment variable (only for gh pr create)
    if (hasSkipEnv(command)) {
      approveAndExit(HOOK_NAME);
      return;
    }

    const branch = await getCurrentBranch();

    // Skip check for main/master branches
    if (!branch || branch === "main" || branch === "master") {
      approveAndExit(HOOK_NAME);
      return;
    }

    // Skip if no code files are changed
    if (!(await hasCodeFiles())) {
      console.error(`[${HOOK_NAME}] No code files changed, skipping code-simplifier check`);
      approveAndExit(HOOK_NAME);
      return;
    }

    // Check if code-simplifier was executed
    if (!checkSimplifierDone(branch)) {
      const reason = getBlockReason(branch);
      blockAndExit(HOOK_NAME, reason);
      return;
    }

    // All checks passed
    approveAndExit(HOOK_NAME);
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    approveAndExit(HOOK_NAME);
  }
}

// 実行（テスト時はスキップ）
if (import.meta.main) {
  main();
}
