#!/usr/bin/env bun
/**
 * コミットメッセージのCloses/Fixesキーワードの整合性をチェックする。
 *
 * Why:
 *   大規模Issueを小さな変更でCloseしようとすると、タスクの一部のみが完了した状態で
 *   Issueがクローズされる。コミット時にサイズ乖離を警告する。
 *
 * What:
 *   - git commitコマンドを検出
 *   - コミットメッセージからCloses/Fixes #xxxを抽出
 *   - Issue情報（タイトル、ラベル、本文）を取得して表示
 *   - コミットサイズとIssueサイズの乖離を警告（ブロックはしない）
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、systemMessageで警告）
 *   - PreToolUse:Bashで発火（git commitコマンド）
 *   - -a/-amフラグ対応（HEADとの差分を確認）
 *   - Issue本文/ラベル/タイトルからサイズを推定
 *
 * Changelog:
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { spawn } from "node:child_process";
import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";
import { stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "closes-validation";

// Closes/Fixes パターン
const CLOSES_PATTERN = /(?:closes?|fixes?|resolves?)\s*#(\d+)/gi;

// サイズ乖離検出の閾値
const SIZE_MISMATCH_LINE_THRESHOLD = 50;
const SIZE_MISMATCH_FILE_THRESHOLD = 3;

// 大規模/中規模/小規模を示すラベル
const LARGE_LABELS = new Set(["enhancement", "feature", "architecture", "refactor", "breaking"]);
const MEDIUM_LABELS = new Set(["bug", "improvement", "performance"]);
const SMALL_LABELS = new Set(["documentation", "docs", "typo", "chore", "style"]);

// 大規模/小規模を示すキーワード
const LARGE_KEYWORDS = [
  "システム",
  "機能",
  "アーキテクチャ",
  "リファクタ",
  "設計",
  "実装",
  "implementation",
  "feature",
];
const SMALL_KEYWORDS = ["typo", "ドキュメント", "docs", "readme", "コメント"];

export interface IssueInfo {
  title: string;
  labels: Array<{ name: string }>;
  body: string | null;
}

export interface DiffStats {
  insertions: number;
  deletions: number;
}

/**
 * Run a command with timeout support.
 */
async function runCommand(
  command: string,
  args: string[],
  timeout: number = TIMEOUT_LIGHT,
): Promise<{ stdout: string; exitCode: number | null }> {
  return new Promise((resolve) => {
    const proc = spawn(command, args, {
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let killed = false;

    const timer = setTimeout(() => {
      killed = true;
      proc.kill("SIGTERM");
    }, timeout * 1000);

    proc.stdout?.on("data", (data) => {
      stdout += data.toString();
    });

    proc.on("close", (exitCode) => {
      clearTimeout(timer);
      if (killed) {
        resolve({ stdout: "", exitCode: null });
      } else {
        resolve({ stdout, exitCode });
      }
    });

    proc.on("error", () => {
      clearTimeout(timer);
      resolve({ stdout: "", exitCode: null });
    });
  });
}

/**
 * GitHub APIでIssue情報を取得
 */
async function getIssueInfo(issueNumber: number): Promise<IssueInfo | null> {
  try {
    const result = await runCommand(
      "gh",
      ["issue", "view", String(issueNumber), "--json", "title,labels,body"],
      TIMEOUT_MEDIUM,
    );

    if (result.exitCode === 0) {
      return JSON.parse(result.stdout) as IssueInfo;
    }
  } catch {
    // Best effort - gh command may fail
  }
  return null;
}

/**
 * Check if command has -a or --all flag (auto-staging modified files).
 */
export function hasAllFlag(command: string): boolean {
  const strippedCommand = stripQuotedStrings(command);
  return /git\s+commit\s+.*(-a\b|--all\b|-[a-z]*a[a-z]*\b)/.test(strippedCommand);
}

/**
 * 現在の変更ファイル一覧を取得
 */
async function getChangedFiles(useHead = false): Promise<string[]> {
  try {
    const args = ["diff", "--name-only"];
    if (useHead) {
      args.push("HEAD");
    } else {
      args.push("--cached");
    }

    const result = await runCommand("git", args);

    if (result.exitCode === 0) {
      return result.stdout
        .trim()
        .split("\n")
        .filter((f) => f);
    }
  } catch {
    // Best effort - git command may fail
  }
  return [];
}

/**
 * 変更の統計情報を取得
 */
async function getDiffStats(useHead = false): Promise<DiffStats> {
  try {
    const args = ["diff", "--stat"];
    if (useHead) {
      args.push("HEAD");
    } else {
      args.push("--cached");
    }

    const result = await runCommand("git", args);

    if (result.exitCode === 0) {
      const lines = result.stdout.trim().split("\n");
      if (lines.length > 0) {
        const lastLine = lines[lines.length - 1];
        let insertions = 0;
        let deletions = 0;

        const insertMatch = lastLine.match(/(\d+)\s+insertion/);
        if (insertMatch) {
          insertions = Number.parseInt(insertMatch[1], 10);
        }

        const deleteMatch = lastLine.match(/(\d+)\s+deletion/);
        if (deleteMatch) {
          deletions = Number.parseInt(deleteMatch[1], 10);
        }

        return { insertions, deletions };
      }
    }
  } catch {
    // Best effort - git command may fail
  }
  return { insertions: 0, deletions: 0 };
}

/**
 * コマンドからコミットメッセージを抽出
 */
export function extractCommitMessage(command: string): string | null {
  // -m "message" パターン
  const match = command.match(/-m\s+["'](.+?)["']/);
  if (match) {
    return match[1];
  }

  // -m "$(cat <<'EOF' ... EOF)" パターン（HEREDOC）
  if (command.includes("<<") && command.includes("EOF")) {
    const heredocMatch = command.match(/<<['"]?EOF['"]?\s*\n?(.*?)EOF/s);
    if (heredocMatch) {
      return heredocMatch[1];
    }
  }

  return null;
}

/**
 * Issueのサイズを推定する
 */
export function estimateIssueSize(issueInfo: IssueInfo): "large" | "medium" | "small" {
  const labels = issueInfo.labels.map((label) => label.name.toLowerCase());
  const title = issueInfo.title.toLowerCase();
  const body = issueInfo.body || "";

  // ラベルによる判定
  if (labels.some((label) => LARGE_LABELS.has(label))) {
    return "large";
  }
  if (labels.some((label) => SMALL_LABELS.has(label))) {
    return "small";
  }
  if (labels.some((label) => MEDIUM_LABELS.has(label))) {
    return "medium";
  }

  // タイトルによる判定
  if (LARGE_KEYWORDS.some((kw) => title.includes(kw))) {
    return "large";
  }
  if (SMALL_KEYWORDS.some((kw) => title.includes(kw))) {
    return "small";
  }

  // 本文の長さによる推定
  if (body.length > 1000) {
    return "large";
  }
  if (body.length > 300) {
    return "medium";
  }

  return "medium";
}

/**
 * Issue サイズとコミットサイズの乖離をチェック
 */
export function checkSizeMismatch(
  issueInfo: IssueInfo,
  diffStats: DiffStats,
  fileCount: number,
): string | null {
  const issueSize = estimateIssueSize(issueInfo);
  const totalChanges = diffStats.insertions + diffStats.deletions;

  // 大規模Issueを小さな変更でCloseしようとしている
  if (
    issueSize === "large" &&
    totalChanges < SIZE_MISMATCH_LINE_THRESHOLD &&
    fileCount < SIZE_MISMATCH_FILE_THRESHOLD
  ) {
    return `⚠️ **サイズ乖離の警告**: このIssueは大規模な変更を示唆していますが、コミットは ${totalChanges} 行/${fileCount} ファイルのみです。\nIssueの一部のみを実装した場合は、部分的なCloseではなく進捗報告として コミットメッセージから \`Closes\` を削除することを検討してください。`;
  }

  return null;
}

async function main(): Promise<void> {
  const result: {
    decision?: string;
    systemMessage?: string;
  } = {};

  const data = await parseHookInput();
  const sessionId = data?.session_id;

  if (!data) {
    logHookExecution(HOOK_NAME, "approve", "parse_error", undefined, { sessionId });
    console.log(JSON.stringify(result));
    return;
  }

  // Bashツールのみを対象
  if (data.tool_name !== "Bash") {
    logHookExecution(HOOK_NAME, "approve", "not_bash", undefined, { sessionId });
    console.log(JSON.stringify(result));
    return;
  }

  const toolInput = data.tool_input || {};
  const command = (toolInput.command as string) || "";

  // git commit コマンドかチェック
  if (!command.includes("git commit")) {
    logHookExecution(HOOK_NAME, "approve", "not_git_commit", undefined, { sessionId });
    console.log(JSON.stringify(result));
    return;
  }

  // コミットメッセージを抽出
  const commitMessage = extractCommitMessage(command);
  if (!commitMessage) {
    logHookExecution(HOOK_NAME, "approve", "no_commit_message", undefined, { sessionId });
    console.log(JSON.stringify(result));
    return;
  }

  // Closes/Fixes パターンを検索
  const matches = [...commitMessage.matchAll(CLOSES_PATTERN)].map((m) => m[1]);
  if (matches.length === 0) {
    logHookExecution(HOOK_NAME, "approve", "no_closes_pattern", undefined, { sessionId });
    console.log(JSON.stringify(result));
    return;
  }

  // 変更内容の統計を取得
  const useHead = hasAllFlag(command);
  const changedFiles = await getChangedFiles(useHead);
  const diffStats = await getDiffStats(useHead);

  // 各Issueの情報を取得して確認
  const warnings: string[] = [];
  const sizeWarnings: string[] = [];

  for (const issueNum of matches) {
    const issueInfo = await getIssueInfo(Number.parseInt(issueNum, 10));
    if (issueInfo) {
      const title = issueInfo.title || "（タイトル取得失敗）";
      const labels = issueInfo.labels.map((label) => label.name);
      const labelsStr = labels.length > 0 ? labels.join(", ") : "なし";

      // Issue本文の最初の200文字を表示
      const body = issueInfo.body || "";
      let bodyPreview = body.length > 200 ? `${body.slice(0, 200)}...` : body;
      bodyPreview = bodyPreview.replace(/\n/g, " ");

      warnings.push(`Issue #${issueNum}: ${title}`);
      warnings.push(`  ラベル: ${labelsStr}`);
      if (bodyPreview) {
        warnings.push(`  概要: ${bodyPreview}`);
      }

      // サイズ乖離チェック
      const mismatchWarning = checkSizeMismatch(issueInfo, diffStats, changedFiles.length);
      if (mismatchWarning) {
        sizeWarnings.push(mismatchWarning);
      }
    }
  }

  warnings.push("");
  warnings.push("【現在のコミット内容】");
  warnings.push(`  変更ファイル数: ${changedFiles.length}`);
  warnings.push(`  変更行数: +${diffStats.insertions} / -${diffStats.deletions}`);
  if (changedFiles.length > 0) {
    warnings.push(`  ファイル: ${changedFiles.slice(0, 5).join(", ")}`);
    if (changedFiles.length > 5) {
      warnings.push(`    ... 他 ${changedFiles.length - 5} ファイル`);
    }
  }

  // サイズ乖離警告を追加
  if (sizeWarnings.length > 0) {
    warnings.push("");
    warnings.push(...sizeWarnings);
  }

  warnings.push("");
  warnings.push("⚠️ 上記のIssueをこのコミットでCloseしようとしています。");
  warnings.push("Issue内容とコミット内容が一致していることを確認してください。");

  // systemMessage として出力（ブロックはしない）
  result.systemMessage = `[closes-validation] Closes/Fixes キーワードを検出\n\n${warnings.join("\n")}`;
  logHookExecution(HOOK_NAME, "approve", "closes_keyword_detected", undefined, { sessionId });
  console.log(JSON.stringify(result));
}

// Only run when executed directly, not when imported for testing
if (import.meta.main) {
  main();
}
