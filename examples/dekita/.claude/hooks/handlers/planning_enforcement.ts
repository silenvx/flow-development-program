#!/usr/bin/env bun
/**
 * PreToolUse hook: Enforce plan file before Issue work.
 *
 * Why:
 *   Issue作業を開始する前にプラン作成を強制することで、
 *   設計なしの実装による手戻りを防ぐ。
 *   全IssueでPlan modeを必須とし、他AIレビュー→実装のワークフローを強制。
 *
 * What:
 *   - git worktree add コマンドを検出
 *   - issue-XXX パターンからIssue番号を抽出
 *   - ブランチ存在チェック（競合リスク検出）
 *   - 既に解決済みかチェック（merged PRs）
 *   - plan file存在チェック
 *
 * Remarks:
 *   - ブロック型フック（PreToolUse:Bash）
 *   - 全IssueでPlan file必須（Issue #3807）
 *   - 緊急時のみ SKIP_PLAN=1 でバイパス可能（P0障害対応等）
 *   - SKIP_ALREADY_FIXED, SKIP_BRANCH_CHECK環境変数も利用可能
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#857: タイトルプレフィックスバイパス追加
 *   - silenvx/dekita#1173: P2/P3ラベルバイパス追加
 *   - silenvx/dekita#2175: enhancementラベルバイパス追加
 *   - silenvx/dekita#2917: TypeScript版初期実装
 *   - silenvx/dekita#3807: 全Issueでplan file必須化（バイパス条件削除）
 */

import { execSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { extractIssueNumberFromBranch } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { extractInlineSkipEnv, isSkipEnvEnabled, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "planning-enforcement";

// Environment variable names
// SKIP_PLAN: 緊急時のバイパス（P0障害対応等）- Issue #3807で運用条件を厳格化
const SKIP_PLAN_ENV = "SKIP_PLAN";
const SKIP_ALREADY_FIXED_ENV = "SKIP_ALREADY_FIXED";
const SKIP_BRANCH_CHECK_ENV = "SKIP_BRANCH_CHECK";

/**
 * Check if command is git worktree add.
 */
export function isWorktreeAddCommand(command: string): boolean {
  const stripped = stripQuotedStrings(command);
  return /\bgit\s+worktree\s+add\b/.test(stripped);
}

/**
 * Check if a plan file exists for the issue.
 *
 * Looks for (in order):
 * 1. .claude/plans/issue-{number}.md (exact match in project)
 * 2. Any .md file in .claude/plans/ containing "issue-{number}" in filename (project)
 * 3. ~/.claude/plans/ - checks both filename and file content for "issue-{number}"
 */
function checkPlanFileExists(issueNumber: string): boolean {
  const projectDir = process.env.CLAUDE_PROJECT_DIR ?? process.cwd();
  const plansDir = join(projectDir, ".claude", "plans");
  const pattern = `issue-${issueNumber}`;

  // Check project .claude/plans/ directory
  if (existsSync(plansDir)) {
    // Check for exact match first (most common case)
    if (existsSync(join(plansDir, `issue-${issueNumber}.md`))) {
      return true;
    }

    // Check for any file containing issue-{number} pattern (case-insensitive)
    try {
      const files = readdirSync(plansDir);
      for (const file of files) {
        if (file.endsWith(".md") && file.toLowerCase().includes(pattern)) {
          return true;
        }
      }
    } catch {
      // Directory read error - continue to next check
    }
  }

  // Check user ~/.claude/plans/ directory (EnterPlanMode saves here)
  const userPlansDir = join(homedir(), ".claude", "plans");
  if (existsSync(userPlansDir)) {
    try {
      const files = readdirSync(userPlansDir);
      for (const file of files) {
        if (!file.endsWith(".md")) continue;

        const filePath = join(userPlansDir, file);

        // Check filename for issue number
        if (file.toLowerCase().includes(pattern)) {
          return true;
        }

        // Also check file content for issue reference
        try {
          const content = readFileSync(filePath, "utf-8").slice(0, 2000);
          if (
            content.toLowerCase().includes(`issue #${issueNumber}`) ||
            content.toLowerCase().includes(`issue-${issueNumber}`)
          ) {
            return true;
          }
        } catch {
          // File read error - continue checking other files
        }
      }
    } catch {
      // Directory read error - return false
    }
  }

  return false;
}

/**
 * Check if SKIP_PLAN environment variable is set.
 */
function hasSkipPlanEnv(command: string): boolean {
  if (isSkipEnvEnabled(process.env[SKIP_PLAN_ENV])) {
    return true;
  }
  const inlineValue = extractInlineSkipEnv(command, SKIP_PLAN_ENV);
  return isSkipEnvEnabled(inlineValue);
}

/**
 * Check if SKIP_ALREADY_FIXED environment variable is set.
 */
function hasSkipAlreadyFixedEnv(command: string): boolean {
  if (isSkipEnvEnabled(process.env[SKIP_ALREADY_FIXED_ENV])) {
    return true;
  }
  const inlineValue = extractInlineSkipEnv(command, SKIP_ALREADY_FIXED_ENV);
  return isSkipEnvEnabled(inlineValue);
}

/**
 * Check if SKIP_BRANCH_CHECK environment variable is set.
 */
function hasSkipBranchCheckEnv(command: string): boolean {
  if (isSkipEnvEnabled(process.env[SKIP_BRANCH_CHECK_ENV])) {
    return true;
  }
  const inlineValue = extractInlineSkipEnv(command, SKIP_BRANCH_CHECK_ENV);
  return isSkipEnvEnabled(inlineValue);
}

/**
 * Extract the branch name from a git worktree add command.
 */
export function extractBranchNameFromCommand(command: string): string | null {
  // Match -b or --branch flag followed by branch name
  let match = command.match(/\s-b\s+(\S+)/);
  if (match) {
    return match[1];
  }

  match = command.match(/\s--branch\s+(\S+)/);
  if (match) {
    return match[1];
  }

  // If no -b flag, check for existing branch pattern after path
  match = command.match(/\bworktree\s+add\s+\S+\s+(?!-)([\w/.-]+)\s*$/);
  if (match) {
    return match[1];
  }

  return null;
}

/**
 * Check if the command has -b or --branch flag for creating a new branch.
 */
export function hasCreateBranchFlag(command: string): boolean {
  if (/\s-b\s+\S+/.test(command)) {
    return true;
  }
  if (/\s--branch\s+\S+/.test(command)) {
    return true;
  }
  return false;
}

/**
 * Check if a local branch exists.
 */
function checkLocalBranchExists(branchName: string): boolean {
  try {
    const result = execSync(`git branch --list ${branchName}`, {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return Boolean(result.trim());
  } catch {
    return false;
  }
}

interface BranchInfo {
  branch: string;
  commits_ahead?: number;
  last_commit_time?: string;
  last_commit_msg?: string;
  worktree_path?: string;
  open_pr?: { number: number; title: string; url: string };
}

/**
 * Get information about an existing branch.
 */
function getBranchInfo(branchName: string): BranchInfo | null {
  const info: BranchInfo = { branch: branchName };

  try {
    // Get commits ahead of main
    try {
      const result = execSync(`git rev-list --count main..${branchName}`, {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      });
      info.commits_ahead = Number.parseInt(result.trim(), 10);
    } catch {
      // Ignore error
    }

    // Get last commit info
    try {
      const result = execSync(`git log -1 --format=%ar:::%s ${branchName}`, {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      });
      const parts = result.trim().split(":::", 2);
      if (parts.length === 2) {
        info.last_commit_time = parts[0];
        info.last_commit_msg = parts[1].slice(0, 50);
      }
    } catch {
      // Ignore error
    }

    // Check if there's a worktree for this branch
    try {
      const result = execSync("git worktree list --porcelain", {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      });
      let currentWorktree: string | null = null;
      for (const line of result.trim().split("\n")) {
        if (line.startsWith("worktree ")) {
          currentWorktree = line.split(" ", 2)[1];
        } else if (line.startsWith("branch ") && currentWorktree) {
          const wtBranch = line.split(" ", 2)[1];
          const wtBranchShort = wtBranch.replace("refs/heads/", "");
          if (wtBranchShort === branchName) {
            info.worktree_path = currentWorktree;
            break;
          }
        }
      }
    } catch {
      // Ignore error
    }

    // Check if there's an open PR for this branch
    try {
      const result = execSync(
        `gh pr list --head ${branchName} --state open --json number,title,url`,
        {
          encoding: "utf-8",
          timeout: TIMEOUT_MEDIUM * 1000,
          stdio: ["pipe", "pipe", "pipe"],
        },
      );
      if (result.trim()) {
        const prs = JSON.parse(result);
        if (prs.length > 0) {
          info.open_pr = prs[0];
        }
      }
    } catch {
      // Ignore error
    }

    return info;
  } catch {
    return null;
  }
}

interface MergedPR {
  number: number;
  title: string;
}

/**
 * Get merged PRs that reference this issue.
 */
function getMergedPrsForIssue(issueNumber: string): MergedPR[] {
  try {
    const result = execSync(
      `gh pr list --state merged --search "Fixes #${issueNumber} OR Closes #${issueNumber} OR Fix #${issueNumber} OR Close #${issueNumber}" --json number,title --limit 5`,
      {
        encoding: "utf-8",
        timeout: TIMEOUT_MEDIUM * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    if (result.trim()) {
      return JSON.parse(result);
    }
  } catch {
    // Best-effort check; failures silently return empty list
  }
  return [];
}

/**
 * Search for Issue #XXX references in .claude/ directory.
 */
function searchIssueInCode(issueNumber: string): string[] {
  const projectDir = process.env.CLAUDE_PROJECT_DIR ?? process.cwd();
  const claudeDir = join(projectDir, ".claude");

  if (!existsSync(claudeDir)) {
    return [];
  }

  const refs: string[] = [];
  const pattern = new RegExp(`Issue\\s*#?\\s*${issueNumber}\\b`, "i");

  try {
    const searchDir = (dir: string): void => {
      const entries = readdirSync(dir, { withFileTypes: true });
      for (const entry of entries) {
        const fullPath = join(dir, entry.name);

        if (entry.isDirectory()) {
          // Skip tests directory
          if (entry.name.toLowerCase() === "tests") continue;
          searchDir(fullPath);
        } else if (entry.isFile()) {
          const fileName = entry.name.toLowerCase();

          // Only check .py and .sh files
          if (!fileName.endsWith(".py") && !fileName.endsWith(".sh")) continue;

          // Skip test files
          if (
            fileName.startsWith("test_") ||
            fileName.endsWith("_test.py") ||
            fileName.endsWith("_test.sh")
          )
            continue;

          try {
            const content = readFileSync(fullPath, "utf-8");
            const lines = content.split("\n");
            for (let i = 0; i < lines.length; i++) {
              if (pattern.test(lines[i])) {
                const relPath = fullPath.replace(`${projectDir}/`, "");
                refs.push(`${relPath}:${i + 1}`);
                break; // One ref per file is enough
              }
            }
          } catch {
            continue;
          }

          if (refs.length >= 3) return; // Limit to 3 references
        }
      }
    };

    searchDir(claudeDir);
  } catch {
    // Directory traversal errors are non-fatal
  }

  return refs.slice(0, 3);
}

interface AlreadyFixedEvidence {
  merged_prs?: MergedPR[];
  code_refs?: string[];
}

/**
 * Check if issue appears to be already fixed.
 */
function checkAlreadyFixed(issueNumber: string): AlreadyFixedEvidence | null {
  const evidence: AlreadyFixedEvidence = {};

  const mergedPrs = getMergedPrsForIssue(issueNumber);
  if (mergedPrs.length > 0) {
    evidence.merged_prs = mergedPrs;
  }

  const codeRefs = searchIssueInCode(issueNumber);
  if (codeRefs.length > 0) {
    evidence.code_refs = codeRefs;
  }

  return Object.keys(evidence).length > 0 ? evidence : null;
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);
    sessionId = ctx.sessionId;
    const toolName = (data.tool_name as string) || "";

    // Only check Bash commands
    if (toolName !== "Bash") {
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    const toolInput = (data.tool_input as Record<string, unknown>) || {};
    const command = (toolInput.command as string) || "";

    // Check if this is git worktree add
    if (!isWorktreeAddCommand(command)) {
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // Extract issue number from command (strict mode: only issue-XXX patterns)
    // Matches branch name (-b flag) or worktree path (.worktrees/issue-123)
    const issueNumber = extractIssueNumberFromBranch(command, { strict: true });

    if (!issueNumber) {
      // Not an issue-related worktree, approve
      await logHookExecution(HOOK_NAME, "approve", "worktree作成を許可（Issue以外）", undefined, {
        sessionId,
      });
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    // Check if trying to create a branch that already exists (Issue #833)
    const branchName = extractBranchNameFromCommand(command);
    if (branchName && hasCreateBranchFlag(command) && !hasSkipBranchCheckEnv(command)) {
      if (checkLocalBranchExists(branchName)) {
        const branchInfo = getBranchInfo(branchName);
        const infoLines: string[] = [];

        if (branchInfo) {
          if (branchInfo.commits_ahead !== undefined) {
            infoLines.push(`- mainから ${branchInfo.commits_ahead} コミット先行`);
          }
          if (branchInfo.last_commit_time) {
            infoLines.push(`- 最終コミット: ${branchInfo.last_commit_time}`);
            if (branchInfo.last_commit_msg) {
              infoLines.push(`  → "${branchInfo.last_commit_msg}"`);
            }
          }
          if (branchInfo.worktree_path) {
            infoLines.push(`- worktree: ${branchInfo.worktree_path}`);
          }
          if (branchInfo.open_pr) {
            const pr = branchInfo.open_pr;
            infoLines.push(`- オープンPR: #${pr.number} "${pr.title}"`);
          }
        }

        const infoText = infoLines.length > 0 ? infoLines.join("\n") : "（詳細情報取得失敗）";

        const blockMessage = `⚠️ ブランチ '${branchName}' は既に存在します。

【ブランチ情報】
${infoText}

【競合リスク】
別のセッションが同じブランチで作業中の可能性があります。

【対応】
1. 既存ブランチの状態を確認: git log ${branchName} --oneline -5
2. 別セッションの作業中なら作業を中止
3. 自分の作業を再開するなら既存ブランチを使用:
   git worktree add .worktrees/issue-XXX ${branchName}
4. 新規作成が必要なら別名で作成するか、SKIP_BRANCH_CHECK=1 で続行

例: SKIP_BRANCH_CHECK=1 git worktree add ...`;

        await logHookExecution(
          HOOK_NAME,
          "block",
          `ブランチ '${branchName}' は既に存在（競合リスク）`,
          undefined,
          { sessionId },
        );
        const result = makeBlockResult(HOOK_NAME, blockMessage);
        console.log(JSON.stringify(result));
        process.exit(2);
      }
    }

    // Check if issue is already fixed (unless bypassed)
    if (!hasSkipAlreadyFixedEnv(command)) {
      const alreadyFixed = checkAlreadyFixed(issueNumber);
      if (alreadyFixed) {
        const hasMergedPrs = alreadyFixed.merged_prs !== undefined;
        const evidenceLines: string[] = [];

        if (hasMergedPrs && alreadyFixed.merged_prs) {
          for (const pr of alreadyFixed.merged_prs) {
            evidenceLines.push(`- マージ済みPR: #${pr.number} "${pr.title}"`);
          }
        }
        if (alreadyFixed.code_refs) {
          for (const ref of alreadyFixed.code_refs) {
            evidenceLines.push(`- コード内参照: ${ref}`);
          }
        }

        const evidenceText = evidenceLines.join("\n");

        // Only block if there are merged PRs (strong evidence)
        if (hasMergedPrs) {
          const blockMessage = `⚠️ Issue #${issueNumber} は既に解決済みの可能性があります。

【検出された証拠】
${evidenceText}

【対応】
1. Issueを確認して本当に追加作業が必要か判断
2. 不要なら作業を中止
3. 必要なら SKIP_ALREADY_FIXED=1 で続行

例: SKIP_ALREADY_FIXED=1 git worktree add ...`;

          await logHookExecution(
            HOOK_NAME,
            "block",
            `Issue #${issueNumber} は既に解決済みの可能性`,
            undefined,
            { sessionId },
          );
          const result = makeBlockResult(HOOK_NAME, blockMessage);
          console.log(JSON.stringify(result));
          process.exit(2);
        } else {
          // Code refs only - warn but don't block
          await logHookExecution(
            HOOK_NAME,
            "approve",
            `Issue #${issueNumber} worktree作成を許可（コード参照のみ、警告表示）`,
            undefined,
            { sessionId },
          );
          console.error(
            `[${HOOK_NAME}] Issue #${issueNumber}: 関連するコード参照が既にmainブランチに存在します。Issueが既に解決済みでないか確認してから作業を開始してください。`,
          );
          // Continue to plan file check (don't return here)
        }
      }
    }

    // Issue #2169: Skip plan check when using existing branch (work resumption)
    if (branchName && !hasCreateBranchFlag(command)) {
      if (checkLocalBranchExists(branchName)) {
        await logHookExecution(
          HOOK_NAME,
          "approve",
          `Issue #${issueNumber} worktree作成を許可（既存ブランチ使用）`,
          undefined,
          { sessionId },
        );
        const result = makeApproveResult(
          HOOK_NAME,
          `Issue #${issueNumber} worktree作成を許可（既存ブランチ '${branchName}' を使用）`,
        );
        console.log(JSON.stringify(result));
        return;
      }
      // Branch doesn't exist - warn and continue to plan check
      await logHookExecution(
        HOOK_NAME,
        "info",
        `Issue #${issueNumber} 指定ブランチ '${branchName}' がローカルに存在しない`,
        undefined,
        { sessionId },
      );
      console.error(
        `[${HOOK_NAME}] Issue #${issueNumber}: 指定されたブランチ '${branchName}' は ローカルに存在しない可能性があります。ブランチ名を確認するか、新規ブランチを作成する場合は \`-b\` フラグを使用してください。`,
      );
      // Continue to plan file check
    }

    // Check bypass conditions for plan requirement
    if (hasSkipPlanEnv(command)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Issue #${issueNumber} worktree作成を許可（SKIP_PLAN）`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(
        HOOK_NAME,
        `Issue #${issueNumber} worktree作成を許可（SKIP_PLAN）`,
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Check if plan file exists (Issue #3807: 全Issueでplan file必須)
    if (checkPlanFileExists(issueNumber)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Issue #${issueNumber} worktree作成を許可（plan file存在）`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME, `Issue #${issueNumber} plan file確認済み`);
      console.log(JSON.stringify(result));
      return;
    }

    // Block: No plan file found
    await logHookExecution(
      HOOK_NAME,
      "block",
      `Issue #${issueNumber} worktree作成をブロック（plan fileなし）`,
      undefined,
      { sessionId },
    );

    const blockMessage = `Plan fileが見つかりません。

**全IssueでPlan mode必須**（Issue #3807）

**通常のワークフロー:**
  1. EnterPlanMode で計画を作成
  2. 計画を他AIでレビュー（Gemini等）
  3. ユーザー承認後に作業開始
  Plan file: .claude/plans/issue-${issueNumber}.md

**緊急時のバイパス（P0障害対応等のみ）:**
  SKIP_PLAN=1 git worktree add .worktrees/issue-${issueNumber} -b fix/issue-${issueNumber} main
  ※ 使用時はIssue/PRにバイパス理由を記録すること`;

    const result = makeBlockResult(HOOK_NAME, blockMessage);
    console.log(JSON.stringify(result));
    process.exit(2);
  } catch (error) {
    // Invalid input or error - approve silently
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
  }
}

if (import.meta.main) {
  main();
}
