#!/usr/bin/env bun
/**
 * フロントエンド変更時にブラウザ確認を強制。
 *
 * Why:
 *   フロントエンドファイル変更後にブラウザ確認せずコミットすると、
 *   ランタイムエラーやUI崩れに気づかないまま本番に反映される。
 *
 * What:
 *   - git commit時（PreToolUse:Bash）に発火
 *   - frontend/src/配下のTS/TSX/JSON/CSS変更を検出
 *   - ブラウザ確認マーカーファイルがなければブロック
 *   - confirm_ui_check.ts実行後にコミット可能
 *
 * State:
 *   - reads: .claude/logs/markers/ui-check-*.done
 *
 * Remarks:
 *   - ブロック型フック（確認なしはコミット不可）
 *   - main/masterブランチはスキップ
 *   - .ts/.tsxはlib/hooks/workersも含む（Issue #209）
 *   - Python版: ui_check_reminder.py
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#209: .ts/.tsxファイル対象を拡大
 *   - silenvx/dekita#2917: TypeScript版初期実装
 *   - silenvx/dekita#2998: getMarkersDir()使用でworktree→メインリポジトリ解決
 *   - silenvx/dekita#3032: hasAutoStageFlag()の正規表現修正（--amend誤検出）
 */

import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { getCurrentBranch } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { getMarkersDir } from "../lib/markers";
import { makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { sanitizeBranchName, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "ui-check-reminder";

/**
 * File patterns that require browser verification.
 * Expanded to cover ALL frontend source files (see Issue #209 for background).
 */
const FRONTEND_FILE_PATTERNS = [
  "frontend/src/**/*.ts", // All TypeScript files (lib, hooks, workers, etc.)
  "frontend/src/**/*.tsx", // All React components
  "frontend/src/i18n/locales/*.json", // i18n translations
  "frontend/src/index.css", // Global CSS
];

/**
 * Check if a file path matches any frontend file pattern.
 */
export function matchesFrontendPattern(filepath: string): boolean {
  for (const pattern of FRONTEND_FILE_PATTERNS) {
    // fnmatch doesn't support **, so we need to handle it manually
    if (pattern.includes("**")) {
      // Split pattern at **
      const parts = pattern.split("**");
      if (parts.length === 2) {
        const prefix = parts[0];
        let suffix = parts[1];
        // Check if file starts with prefix and ends with suffix pattern
        if (filepath.startsWith(prefix)) {
          const remaining = filepath.slice(prefix.length);
          // suffix might have a leading /, remove it
          if (suffix.startsWith("/")) {
            suffix = suffix.slice(1);
          }
          // Simple glob match for suffix
          if (
            simpleGlobMatch(remaining, `*${suffix}`) ||
            simpleGlobMatch(remaining, `*/${suffix}`)
          ) {
            return true;
          }
        }
      }
    } else {
      if (simpleGlobMatch(filepath, pattern)) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Simple glob matching (supports * wildcard).
 */
function simpleGlobMatch(str: string, pattern: string): boolean {
  // Escape special regex chars except *
  const regexPattern = pattern
    .split("*")
    .map((s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&"))
    .join(".*");
  const regex = new RegExp(`^${regexPattern}$`);
  return regex.test(str);
}

/**
 * Check if command is a git commit command.
 */
export function isGitCommitCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }
  const strippedCommand = stripQuotedStrings(command);
  return /git\s+commit\b/.test(strippedCommand);
}

/**
 * Check if command has -a or -am flag (auto-staging modified files).
 * Note: Only detects -a, -am, -ma, --all explicitly.
 * Combined flags like -av, -as are not detected (uncommon usage).
 */
export function hasAutoStageFlag(command: string): boolean {
  const strippedCommand = stripQuotedStrings(command);
  // Match git commit with -a, -am, -ma, or --all flags explicitly
  // Note: Previous regex was too greedy and matched --amend incorrectly
  // Use non-greedy .*? and require space before flag
  return /git\s+commit\b.*?\s(-a\b|-am\b|-ma\b|--all\b)/.test(strippedCommand);
}

/**
 * Get list of staged frontend files.
 */
function getStagedFrontendFiles(): string[] {
  try {
    const result = execSync("git diff --cached --name-only", {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    const output = result.trim();
    if (!output) {
      return [];
    }
    const files = output.split("\n");
    return files.filter((f) => matchesFrontendPattern(f));
  } catch {
    return [];
  }
}

/**
 * Get list of modified (unstaged) frontend files.
 * Used to detect files that would be staged by `git commit -a`.
 */
function getModifiedFrontendFiles(): string[] {
  try {
    const result = execSync("git diff --name-only", {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    const output = result.trim();
    if (!output) {
      return [];
    }
    const files = output.split("\n");
    return files.filter((f) => matchesFrontendPattern(f));
  } catch {
    return [];
  }
}

/**
 * Check if UI verification was confirmed for this branch.
 */
function checkUiVerificationDone(branch: string): boolean {
  const markersDir = getMarkersDir();
  const safeBranch = sanitizeBranchName(branch);
  const logFile = `${markersDir}/ui-check-${safeBranch}.done`;
  return existsSync(logFile);
}

async function main(): Promise<void> {
  let result: { decision?: string; reason?: string } = {};
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = data.tool_input ?? {};
    const command = (toolInput.command as string) ?? "";

    // Only check git commit commands
    if (isGitCommitCommand(command)) {
      // Check for staged frontend files
      let frontendFiles = getStagedFrontendFiles();

      // Also check modified files if -a flag is used (git commit -a stages modified files)
      if (hasAutoStageFlag(command)) {
        const modifiedFiles = getModifiedFrontendFiles();
        // Combine and deduplicate
        const allFiles = new Set([...frontendFiles, ...modifiedFiles]);
        frontendFiles = Array.from(allFiles);
      }

      if (frontendFiles.length > 0) {
        // Get current branch
        const branch = await getCurrentBranch();
        if (branch !== null && branch !== "main" && branch !== "master") {
          if (!checkUiVerificationDone(branch)) {
            // Block: frontend files staged but no browser verification confirmation
            const filesList = frontendFiles
              .sort()
              .map((f) => `  - ${f}`)
              .join("\n");
            const reason = `フロントエンドファイルが変更されていますが、ブラウザ確認が完了していません。\n\n変更されたファイル:\n${filesList}\n\n**必須手順**:\n1. 開発サーバーを起動: \`pnpm dev:frontend\` (+ \`pnpm dev:worker\` if needed)\n2. Chrome DevTools MCPで実際の動作を確認\n   - \`mcp__chrome-devtools__navigate_page\` でアプリにアクセス\n   - \`mcp__chrome-devtools__take_snapshot\` でDOM状態を確認\n   - \`mcp__chrome-devtools__list_console_messages\` でエラーがないか確認\n   - 必要に応じて \`mcp__chrome-devtools__list_network_requests\` でAPI/Analytics確認\n3. 確認完了後、以下を実行:\n\n\`\`\`bash\nbun run .claude/scripts/confirm_ui_check.ts\n\`\`\`\n\nその後、再度コミットを実行してください。`;
            result = makeBlockResult(HOOK_NAME, reason);
          }
        }
      }
    }
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    result = {};
  }

  // Always log execution for accurate statistics
  await logHookExecution(HOOK_NAME, result.decision ?? "approve", result.reason, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((err) => {
    console.error(`[${HOOK_NAME}] Unhandled error:`, err);
    console.log(JSON.stringify({}));
    process.exit(1);
  });
}
