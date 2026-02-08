#!/usr/bin/env bun
/**
 * Phase Issueクローズ後に残作業があれば次Phase Issueを自動作成。
 *
 * Why:
 *   Phase分割タスク（TypeScript移行 Phase 3等）がクローズされた際、
 *   外部の残作業（Pythonフック等）があっても次Phaseが作成されず、
 *   作業が中断してしまう問題があった（Issue #2917）。
 *
 * What:
 *   - gh issue close 成功後（PostToolUse）に発火
 *   - Issue titleに「Phase N」が含まれるか確認
 *   - 残作業検出（settings.jsonのPythonフック数等）
 *   - 残作業があれば次Phase Issueを自動作成
 *
 * Remarks:
 *   - PostToolUseフック（Issueクローズ成功後に実行）
 *   - 残作業検出ロジックはTypeScript移行に特化（拡張可能）
 *   - 作成されたIssue番号をsystemMessageで通知
 *
 * Changelog:
 *   - silenvx/dekita#2929: 初期実装
 */

import { spawnSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { getExitCode, getToolResult } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { outputResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "phase-issue-auto-continuation";

/** Phase番号を抽出する正規表現 */
const PHASE_PATTERN = /Phase\s*(\d+)/i;

/**
 * gh issue close コマンドからIssue番号を抽出
 */
function extractIssueNumber(command: string): string | null {
  // stripQuotedStringsはコマンド検出のみに使用（echo偽陽性防止）
  if (!/gh\s+issue\s+close\b/.test(stripQuotedStrings(command))) {
    return null;
  }

  // 引数抽出は元のコマンドから行う（引用符付きIssue番号を検出するため）
  const match = command.match(/gh\s+issue\s+close\s+(.+)/);
  if (!match) {
    return null;
  }

  const args = match[1];
  // 引用符内の空白を保持してトークン分割
  const parts = args.match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g) || [];
  // フラグ引数からの誤抽出を防ぐためのスキップ対象フラグ
  const flagsWithArgs = ["-c", "--comment", "-r", "--reason"];

  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];

    if (part.startsWith("-")) {
      // 引数を取るフラグの場合、次の要素もスキップ
      if (flagsWithArgs.includes(part)) {
        i++;
      }
      continue;
    }

    // 引用符を除去してからパース
    const cleanPart = part.replace(/^['"]|['"]$/g, "");

    // Handle GitHub URLs (.../issues/123)
    const urlMatch = cleanPart.match(/\/issues\/(\d+)$/);
    if (urlMatch) {
      return urlMatch[1];
    }
    // Handle numbers (#123, 123)
    const numMatch = cleanPart.match(/^#?(\d+)$/);
    if (numMatch) {
      return numMatch[1];
    }
  }

  return null;
}

/**
 * `gh issue view` を安全に実行し、結果を文字列で取得するヘルパー関数
 * spawnSyncを使用してシェルインジェクションを防止
 */
function executeGhIssueView(issueNumber: string, args: string[]): string | null {
  try {
    const result = spawnSync("gh", ["issue", "view", issueNumber, ...args], {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    if (result.status !== 0) {
      return null;
    }
    return result.stdout.trim();
  } catch {
    return null;
  }
}

/** Issue詳細情報の型 */
interface IssueDetails {
  title: string;
  stateReason: string | null;
  labels: string[];
}

/**
 * Issueの詳細（タイトル, 状態理由, ラベル）を一括取得
 * 3回のgh呼び出しを1回に統合してパフォーマンスを改善
 */
function getIssueDetails(issueNumber: string): IssueDetails | null {
  const json = executeGhIssueView(issueNumber, ["--json", "title,stateReason,labels"]);

  if (!json) return null;

  try {
    const data = JSON.parse(json) as {
      title?: string;
      stateReason?: string | null;
      labels?: Array<{ name: string }>;
    };
    return {
      title: data.title ?? "",
      stateReason: data.stateReason ?? null,
      labels: (data.labels ?? []).map((l) => l.name),
    };
  } catch {
    return null;
  }
}

/**
 * Pythonフックの残数をカウント（settings.jsonをパースして正確にカウント）
 * @returns Pythonフック数。エラー時は-1を返す（0と区別するため）
 */
function countRemainingPythonHooks(): number {
  try {
    const projectDir = process.env.CLAUDE_PROJECT_DIR ?? process.cwd();
    const settingsPath = join(projectDir, ".claude", "settings.json");
    const content = readFileSync(settingsPath, "utf-8");
    const settings = JSON.parse(content) as Record<string, unknown>;

    let count = 0;
    const walk = (obj: unknown): void => {
      if (!obj || typeof obj !== "object") return;
      const record = obj as Record<string, unknown>;
      // Count if it looks like a command hook using a Python file
      if (typeof record.command === "string" && /\.py\b/.test(record.command)) {
        count++;
      }
      Object.values(record).forEach(walk);
    };
    walk(settings);
    return count;
  } catch {
    // Return -1 on error to distinguish from "0 remaining" (migration complete)
    return -1;
  }
}

/**
 * TypeScriptフックの数をカウント
 */
function countTypeScriptHooks(): number {
  try {
    const projectDir = process.env.CLAUDE_PROJECT_DIR ?? process.cwd();
    const hooksDir = join(projectDir, ".claude", "hooks", "ts", "hooks");
    const files = readdirSync(hooksDir);
    return files.filter((f) => f.endsWith(".ts")).length;
  } catch {
    return 0;
  }
}

/**
 * 次のPhase Issueを作成
 */
function createNextPhaseIssue(
  currentTitle: string,
  currentPhase: number,
  remainingCount: number,
  tsHookCount: number,
  labels: string[],
  closedIssueNumber: string,
): string | null {
  const nextPhase = currentPhase + 1;

  // タイトルの Phase N を Phase N+1 に置換
  const nextTitle = currentTitle.replace(PHASE_PATTERN, `Phase ${nextPhase}`);

  const body = `Continuation from Phase #${closedIssueNumber}

## Why

Phase ${currentPhase} is complete, but ${remainingCount} Python hooks remain.
Continue migrating the remaining hooks to TypeScript to complete the overall migration.

## What

- Remaining Python hooks: ${remainingCount}
- TypeScript hooks: ${tsHookCount}

## How

At Phase ${nextPhase} completion:
- More hooks converted to TypeScript
- Improved migration rate

### Approach

Continue migration following the same procedure as previous phase:
1. Prioritize complex hooks for migration
2. Update settings.json
3. Verify tests

## References

- Phase ${currentPhase}: #${closedIssueNumber} (completed)

Note: No research needed - this is an auto-generated continuation issue.
`;

  try {
    // spawnSyncを使用してシェルインジェクションを防止
    const args = [
      "issue",
      "create",
      "--title",
      nextTitle,
      ...labels.flatMap((l) => ["--label", l]),
      "--body",
      body,
    ];

    const result = spawnSync("gh", args, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    if (result.status !== 0) {
      throw new Error(result.stderr || "Unknown error");
    }

    // Extract issue URL from output
    const urlMatch = result.stdout.match(/https:\/\/github\.com\/[^\s]+\/issues\/(\d+)/);
    return urlMatch ? urlMatch[1] : null;
  } catch (error) {
    console.error(`[${HOOK_NAME}] Failed to create next phase issue:`, error);
    return null;
  }
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);
    sessionId = ctx.sessionId;
    const toolName = data.tool_name ?? "";

    // PostToolUse: Bashのみ
    if (toolName !== "Bash") {
      outputResult({});
      return;
    }

    const toolInput = (data.tool_input as Record<string, unknown>) ?? {};
    const command = (toolInput.command as string) ?? "";

    // gh issue close コマンドか確認
    const issueNumber = extractIssueNumber(command);
    if (!issueNumber) {
      outputResult({});
      return;
    }

    // コマンドが成功したか確認（PostToolUse）
    const toolResult = getToolResult(data as Record<string, unknown>);
    // PreToolUseではtoolResultがundefinedになるため、早期リターン
    if (!toolResult) {
      outputResult({});
      return;
    }
    const exitCode = getExitCode(toolResult);
    if (exitCode !== 0) {
      // コマンド失敗時は何もしない
      outputResult({});
      return;
    }

    // Issue情報を一括取得（3回のgh呼び出しを1回に統合）
    const issueDetails = getIssueDetails(issueNumber);
    if (!issueDetails) {
      outputResult({});
      return;
    }

    // クローズ理由を確認（"COMPLETED"の場合のみ次Phaseを作成）
    if (issueDetails.stateReason !== "COMPLETED") {
      // "NOT_PLANNED", "DUPLICATE"等の場合は次Phaseを作成しない
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Issue #${issueNumber} closed with reason '${issueDetails.stateReason}' - skipping auto-continuation`,
        undefined,
        { sessionId },
      );
      outputResult({});
      return;
    }

    // Phase N パターンを検出
    const phaseMatch = issueDetails.title.match(PHASE_PATTERN);
    if (!phaseMatch) {
      // Phase Issueではない
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Issue #${issueNumber} is not a Phase issue`,
        undefined,
        { sessionId },
      );
      outputResult({});
      return;
    }

    const currentPhase = Number.parseInt(phaseMatch[1], 10);

    // Check if this is a TypeScript migration (must contain TypeScript/TS AND migration keyword)
    // Require TypeScript/TS to avoid false positives on unrelated Phase Issues like "Database Migration"
    const isTsMigration =
      /TypeScript|TS/i.test(issueDetails.title) && /Migration/i.test(issueDetails.title);
    if (!isTsMigration) {
      // Skip if not a TypeScript migration (other patterns can be added in the future)
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Issue #${issueNumber} is not a TypeScript migration`,
        undefined,
        { sessionId },
      );
      outputResult({});
      return;
    }

    // 残りのPythonフック数を確認
    const remainingPythonHooks = countRemainingPythonHooks();
    if (remainingPythonHooks < 0) {
      // settings.json読み取りエラー時は何もしない（誤検知を防ぐ）
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "Failed to count Python hooks - skipping auto-continuation",
        undefined,
        { sessionId },
      );
      outputResult({});
      return;
    }
    if (remainingPythonHooks === 0) {
      // 全て移行完了
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "TypeScript migration complete - no remaining Python hooks",
        undefined,
        { sessionId },
      );
      outputResult({
        systemMessage: "TypeScript migration complete: All Python hooks have been migrated.",
      });
      return;
    }

    // Create the next Phase Issue
    const tsHookCount = countTypeScriptHooks();
    const nextIssueNumber = createNextPhaseIssue(
      issueDetails.title,
      currentPhase,
      remainingPythonHooks,
      tsHookCount,
      issueDetails.labels,
      issueNumber,
    );

    if (nextIssueNumber) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Created Phase ${currentPhase + 1} issue: #${nextIssueNumber}`,
        undefined,
        { sessionId },
      );
      outputResult({
        systemMessage: `Phase ${currentPhase + 1} Issue created: #${nextIssueNumber}

Remaining Python hooks: ${remainingPythonHooks}

[IMMEDIATE: gh issue view ${nextIssueNumber}]
Continue working on the next phase.`,
      });
    } else {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "Failed to create next phase issue, but not blocking",
        undefined,
        { sessionId },
      );
      outputResult({
        systemMessage: `Failed to create next Phase Issue automatically. Please create it manually.
Remaining Python hooks: ${remainingPythonHooks}`,
      });
    }
  } catch (error) {
    // Fail-open: continue on error
    await logHookExecution(HOOK_NAME, "error", `Hook error: ${formatError(error)}`, undefined, {
      sessionId,
    });
    outputResult({});
  }
}

// 実行（テスト時はスキップ）
if (import.meta.main) {
  main();
}

// テスト用にエクスポート
export { extractIssueNumber, countRemainingPythonHooks, countTypeScriptHooks };
