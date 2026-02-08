#!/usr/bin/env bun
/**
 * マージ済みPRのworktreeからgit push/gh pr createを実行しようとした際にブロックする。
 *
 * Why:
 *   別セッションでPRがマージされた後、古いworktreeで作業を再開すると
 *   重複PRを作成してしまう問題を防止する（Issue #3900）。
 *
 * What:
 *   - git push または gh pr create コマンドを検出
 *   - 現在のworktreeのブランチに関連するPRがマージ済みか確認
 *   - マージ済みならブロックし、worktree削除を促す
 *
 * State:
 *   - stateless（外部状態を持たない）
 *
 * Remarks:
 *   - ブロック型フック（exit 2でブロック）
 *   - merged_worktree_check.tsは警告のみ（責務分離）
 *   - このフックは実際の操作時にブロックする
 *   - SKIP_WORKTREE_RESUME_CHECK=1 でバイパス可能
 *
 * Changelog:
 *   - silenvx/dekita#3900: 初期実装
 */

import { execSync } from "node:child_process";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { CONTINUATION_HINT, TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { extractIssueNumberFromBranch } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { getRepoRoot } from "../lib/repo";
import { makeBlockResult } from "../lib/results";
import { getBashCommand, parseHookInput } from "../lib/session";
import { shellQuote } from "../lib/shell_tokenizer";
import {
  checkSkipEnv,
  splitCommandChainQuoteAware,
  stripEnvPrefix,
  stripQuotedStrings,
} from "../lib/strings";

const HOOK_NAME = "worktree-resume-check";
const SKIP_ENV = "SKIP_WORKTREE_RESUME_CHECK";

/**
 * Check if the command is git push or gh pr create.
 */
export function isTargetCommand(command: string): boolean {
  if (!command.trim()) {
    return false;
  }

  // Split command chain and check each subcommand
  const subcommands = splitCommandChainQuoteAware(command);
  for (const sub of subcommands) {
    // Strip env prefix and quotes for pattern matching
    const stripped = stripQuotedStrings(stripEnvPrefix(sub));

    // Match git push (but not --dry-run)
    // Anchor to start (^\s*) to avoid false positives like "echo git push"
    // The pattern matches: git [optional global flags] push [optional args]
    // Global flags start with - and may have values (e.g., -c key=value)
    if (
      /^\s*git\b(?:\s+(?:-[a-zA-Z](?:\s+\S+)?|--[a-zA-Z][a-zA-Z0-9-]*(?:=\S+)?))*\s+push\b/.test(
        stripped,
      ) &&
      !/(?:^|\s)(?:--dry-run|-n)(?:$|\s)/.test(stripped)
    ) {
      return true;
    }

    // Match gh pr create
    // Anchor to start (^\s*) to avoid false positives like "echo gh pr create"
    // Allow global flags between gh and pr (e.g., gh -R repo pr create)
    if (
      /^\s*gh\b(?:\s+(?:-[a-zA-Z](?:\s+\S+)?|--[a-zA-Z][a-zA-Z0-9-]*(?:=\S+)?))*\s+pr\s+create\b/.test(
        stripped,
      )
    ) {
      return true;
    }
  }

  return false;
}

/**
 * Check if current directory is inside a worktree.
 */
export function isInsideWorktree(cwd: string): boolean {
  return cwd.includes(".worktrees/");
}

/**
 * Get the worktree name from the current directory.
 */
export function getWorktreeName(cwd: string): string | null {
  // Find the .worktrees/ portion and extract the worktree name
  // Use lastIndexOf to handle edge cases like /backups/.worktrees/repo/.worktrees/feature
  const worktreesIdx = cwd.lastIndexOf(".worktrees/");
  if (worktreesIdx === -1) {
    return null;
  }

  const afterWorktrees = cwd.slice(worktreesIdx + ".worktrees/".length);
  const worktreeName = afterWorktrees.split("/")[0];
  return worktreeName || null;
}

/**
 * Get the current branch name.
 */
function getCurrentBranch(cwd: string): string | null {
  try {
    const result = execSync("git branch --show-current", {
      encoding: "utf-8",
      cwd,
      timeout: TIMEOUT_LIGHT * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return result.trim() || null;
  } catch {
    return null;
  }
}

/**
 * Expand ~ in paths to the user's home directory.
 */
function expandHome(p: string): string {
  if (p.startsWith("~/")) {
    return join(homedir(), p.slice(2));
  }
  if (p === "~") {
    return homedir();
  }
  return p;
}

interface PrInfo {
  number: number;
  title: string;
  mergedAt: string;
}

/**
 * Check if there's a merged PR for the given branch.
 */
export function checkPrMerged(branch: string, cwd: string): PrInfo | null {
  try {
    const result = execSync(`gh pr view ${shellQuote(branch)} --json number,title,mergedAt`, {
      encoding: "utf-8",
      cwd,
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    if (result.trim()) {
      const data = JSON.parse(result);
      if (data.mergedAt) {
        return {
          number: data.number,
          title: data.title ?? "",
          mergedAt: data.mergedAt,
        };
      }
    }
  } catch {
    // gh CLI unavailable or no PR found
  }
  return null;
}

/**
 * Check if there's a merged PR for the given issue number.
 * Used when branch name is main/master (after PR merge with --delete-branch).
 */
export function checkPrMergedForIssue(issueNumber: string, cwd: string): PrInfo | null {
  try {
    // Search for merged PRs that reference the issue number in title (e.g., "#3900")
    const result = execSync(
      `gh pr list --state merged --search "#${issueNumber} in:title" --json number,title,mergedAt --limit 1`,
      {
        encoding: "utf-8",
        cwd,
        timeout: TIMEOUT_MEDIUM * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    if (result.trim()) {
      const data = JSON.parse(result);
      if (data.length > 0 && data[0].mergedAt) {
        return {
          number: data[0].number,
          title: data[0].title ?? "",
          mergedAt: data[0].mergedAt,
        };
      }
    }
  } catch {
    // gh CLI unavailable or no PR found
  }
  return null;
}

/**
 * Format the merged date for display.
 */
function formatMergedDate(mergedAt: string): string {
  try {
    const date = new Date(mergedAt);
    return date.toISOString().split("T")[0];
  } catch {
    return mergedAt;
  }
}

async function main(): Promise<void> {
  let result: { decision?: string; reason?: string } = {};
  let sessionId: string | undefined;

  try {
    const input = await parseHookInput();
    sessionId = input.session_id;
    const command = getBashCommand(input);

    // Check skip env
    if (checkSkipEnv(HOOK_NAME, SKIP_ENV, { input_preview: command })) {
      console.log(JSON.stringify(result));
      return;
    }

    // Skip if not a target command
    if (!isTargetCommand(command)) {
      console.log(JSON.stringify(result));
      return;
    }

    // Get current working directory, respecting tool input if provided
    // This handles cases where agents run commands in specific directories
    // using dir_path without changing the process-level CWD
    const toolInput = input.tool_input || {};
    const cwd = toolInput.dir_path
      ? resolve(process.cwd(), expandHome(toolInput.dir_path as string))
      : process.cwd();

    // Check if inside worktree
    if (!isInsideWorktree(cwd)) {
      console.log(JSON.stringify(result));
      return;
    }

    // Get the repository root
    const projectDir = process.env.CLAUDE_PROJECT_DIR ?? "";
    const repoRoot = getRepoRoot(projectDir);

    if (!repoRoot) {
      console.log(JSON.stringify(result));
      return;
    }

    // Get the worktree name for the cleanup command
    const worktreeName = getWorktreeName(cwd);

    // Get the current branch
    const branch = getCurrentBranch(cwd);

    // Try to find merged PR info
    let prInfo: PrInfo | null = null;

    if (!branch || branch === "main" || branch === "master") {
      // Branch is main/master or couldn't be determined
      // This happens when PR is merged with --delete-branch, resetting worktree to main
      // Try to find merged PR by extracting issue number from worktree name
      if (worktreeName) {
        const issueNumber = extractIssueNumberFromBranch(worktreeName, { strict: true });
        if (issueNumber) {
          prInfo = checkPrMergedForIssue(issueNumber, repoRoot);
        }
      }
    } else {
      // Normal case: check PR by branch name
      prInfo = checkPrMerged(branch, repoRoot);
    }

    if (!prInfo) {
      console.log(JSON.stringify(result));
      return;
    }

    // PR is merged - block the operation
    const mergedDate = formatMergedDate(prInfo.mergedAt);
    const worktreePath = worktreeName
      ? shellQuote(`.worktrees/${worktreeName}`)
      : shellQuote(`.worktrees/${(branch ?? "unknown").replace(/\//g, "-")}`);

    const reason = [
      "🚫 このworktreeのPRは既にマージ済みです。",
      "",
      `PR #${prInfo.number}: ${prInfo.title} (merged at ${mergedDate})`,
      "",
      "このworktreeを削除してから作業してください:",
      "```",
      `cd ${shellQuote(repoRoot)} && git worktree unlock ${worktreePath} 2>/dev/null; git worktree remove ${worktreePath}`,
      "git pull",
      "```",
      CONTINUATION_HINT,
    ].join("\n");

    result = makeBlockResult(HOOK_NAME, reason);
    await logHookExecution(
      HOOK_NAME,
      "block",
      reason,
      {
        command,
        branch,
        pr_number: prInfo.number,
        merged_at: prInfo.mergedAt,
      },
      { sessionId },
    );
  } catch (error) {
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
