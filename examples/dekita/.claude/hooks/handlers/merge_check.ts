#!/usr/bin/env bun
/**
 * マージ前の安全性チェックを強制する。
 *
 * Why:
 *   AIレビュー（Copilot/Codex）を確認せずにマージすると、品質問題を
 *   見逃す可能性がある。また、--auto/--adminオプションや、未解決の
 *   レビュースレッド、未完了の受け入れ基準があるままマージすると
 *   品質管理が形骸化する。
 *
 * What:
 *   - gh pr merge --auto/--adminをブロック
 *   - REST APIマージをブロック（フックバイパス防止）
 *   - AIレビュー進行中/エラー状態でのマージをブロック
 *   - 未解決レビュースレッド、未検証の修正主張をブロック
 *   - 未完了の受け入れ基準を持つIssueのCloseをブロック
 *   - --dry-runモードでマージ前チェックが可能
 *
 * Remarks:
 *   - 複数のブロック理由を一度に収集・表示（Issue #874）
 *   - モジュール分割: ai_review_checker, issue_checker, review_checker等
 *   - TypeScript移行版
 *
 * Tags:
 *   type: blocking
 *   category: quality-gate
 *
 * Changelog:
 *   - silenvx/dekita#263: AIレビューエラー検出追加
 *   - silenvx/dekita#457: 修正主張の検証チェック追加
 *   - silenvx/dekita#598: Issue受け入れ基準チェック追加
 *   - silenvx/dekita#858: 数値主張の検証チェック追加
 *   - silenvx/dekita#874: ブロック理由の一括収集・表示
 *   - silenvx/dekita#892: --dry-runモード追加
 *   - silenvx/dekita#1130: バグ別Issue化の警告追加
 *   - silenvx/dekita#1379: REST APIマージブロック追加
 *   - silenvx/dekita#2347: マージコミット背景リマインダー追加
 *   - silenvx/dekita#2377: --adminブロック時の詳細ステータス表示
 *   - silenvx/dekita#2384: --body内の誤検知防止
 *   - silenvx/dekita#3161: TypeScript移行
 */

import { formatError } from "../lib/format_error";
import {
  extractPrNumber,
  extractPrNumberFromUrl,
  getPrMergeStatus,
  parseAllGhPrCommands,
  runGhCommand,
} from "../lib/github";
import { logHookExecution } from "../lib/logging";
import { type BlockingReason, runAllPrChecks } from "../lib/merge_conditions";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { createHookContext, getBashCommand, parseHookInput } from "../lib/session";
import {
  splitCommandChainQuoteAware,
  splitShellArgs,
  stripEnvPrefix,
  stripQuotedStrings,
} from "../lib/strings";

const HOOK_NAME = "merge_check";

// =============================================================================
// CLI Mode Support (--dry-run)
// =============================================================================

/**
 * Parse command-line arguments.
 */
function parseArgs(): { dryRun: boolean; prNumber: number | null } {
  const args = process.argv.slice(2);

  let dryRun = false;
  let prNumber: number | null = null;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === "--dry-run") {
      dryRun = true;
    } else if (/^\d+$/.test(arg)) {
      prNumber = Number.parseInt(arg, 10);
    }
  }

  return { dryRun, prNumber };
}

/**
 * Run all merge checks and report issues without blocking (Issue #892).
 *
 * This mode allows checking merge readiness before attempting to merge,
 * preventing multiple failed merge attempts.
 */
async function dryRunCheck(prNumber: number): Promise<number> {
  console.log(`[DRY-RUN] PR #${prNumber} のマージ前チェックを実行中...`);
  console.log();

  try {
    const [blockingReasons, warnings] = await runAllPrChecks(String(prNumber), true);

    // Display warnings first (Issue #630)
    for (const warning of warnings) {
      console.error(warning);
    }

    if (blockingReasons.length > 0) {
      console.log(`⚠️  ${blockingReasons.length}件の問題が見つかりました:`);
      console.log();
      const separator = "=".repeat(60);

      for (let i = 0; i < blockingReasons.length; i++) {
        const br = blockingReasons[i];
        console.log(`【問題 ${i + 1}/${blockingReasons.length}】${br.title}`);
        console.log(br.details);
        if (i < blockingReasons.length - 1) {
          console.log(separator);
        }
        console.log();
      }

      console.log(`全${blockingReasons.length}件の問題を解決後、マージを実行してください。`);
      return 1;
    }
    console.log(`✅ PR #${prNumber} はマージ可能です`);
    return 0;
  } catch (e) {
    console.error(`❌ チェック中にエラーが発生しました: ${formatError(e)}`);
    return 2;
  }
}

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Resolve PR number from a parsed merge command tuple.
 *
 * Issue #3345: Supports branch name, URL, and current branch fallback.
 * AI Review fix: Takes parsed command tuple directly to avoid extractMergeTarget
 * returning the wrong command's target in chained commands.
 * AI Review fix 2: Uses repo from the specific command tuple, not global repoOption.
 *
 * Resolution order:
 * 1. Explicit PR number from tuple (e.g., `gh pr merge 123`)
 * 2. PR number from URL in mergeTarget (e.g., `gh pr merge https://github.com/.../pull/123`)
 * 3. Branch name to PR via gh pr view (e.g., `gh pr merge feature-branch`)
 * 4. Current branch's PR via gh pr view
 *
 * @param parsedCommand - Tuple from parseAllGhPrCommands: [subcommand, prNumber, repo, cdTarget, mergeTarget, hasDeleteBranch]
 */
async function resolvePrNumberFromParsedCommand(
  parsedCommand: [
    string | null,
    string | null,
    string | null,
    string | null,
    string | null,
    boolean,
  ],
): Promise<string | null> {
  const [, prNumber, repo, , mergeTarget] = parsedCommand;

  // 1. Try explicit PR number from parsed command
  if (prNumber) {
    return prNumber;
  }

  // 2. Try merge target (branch name or URL) from this specific command
  if (mergeTarget) {
    // Try URL extraction first (no API call needed)
    const fromUrl = extractPrNumberFromUrl(mergeTarget);
    if (fromUrl) {
      return fromUrl;
    }

    // Try resolving branch name via API (using this command's repo)
    // 明示的なターゲット指定時は、取得失敗した場合でも現在ブランチにフォールバックしない
    return fetchPrNumber(mergeTarget, repo);
  }

  // 3. Fallback to current branch's PR (only when no explicit target specified)
  return fetchPrNumber(null, repo);
}

/**
 * Fetch PR number via gh pr view.
 *
 * @param target - Branch name or null for current branch
 * @param repoOption - Repository option for -R flag
 */
async function fetchPrNumber(
  target: string | null,
  repoOption: string | null,
): Promise<string | null> {
  const args = target
    ? ["pr", "view", target, "--json", "number"]
    : ["pr", "view", "--json", "number"];
  if (repoOption) {
    args.splice(0, 0, "-R", repoOption);
  }

  const [success, stdout] = await runGhCommand(args);
  if (!success) {
    return null;
  }

  try {
    const data = JSON.parse(stdout);
    return data.number ? String(data.number) : null;
  } catch {
    return null;
  }
}

/**
 * Strip values of options that may contain text like '--admin' or '--auto'.
 *
 * Issue #2384: Prevents false positives from --body "The '--admin' option".
 */
function stripOptionValues(cmd: string): string {
  let result = cmd;

  // Regex pattern for double-quoted strings with escaped quotes: "(?:[^"\\]|\\.)*"
  // Regex pattern for single-quoted strings with escaped quotes: '(?:[^'\\]|\\.)*'

  // --body "x", --body="x", -b "x", -b="x"
  result = result.replace(/(--body(?:\s+|=))"(?:[^"\\]|\\.)*"/g, '$1""');
  result = result.replace(/(--body(?:\s+|=))'(?:[^'\\]|\\.)*'/g, "$1''");
  result = result.replace(/(-b(?:\s+|=))"(?:[^"\\]|\\.)*"/g, '$1""');
  result = result.replace(/(-b(?:\s+|=))'(?:[^'\\]|\\.)*'/g, "$1''");

  // --subject "x", --subject="x", -t "x", -t="x"
  result = result.replace(/(--subject(?:\s+|=))"(?:[^"\\]|\\.)*"/g, '$1""');
  result = result.replace(/(--subject(?:\s+|=))'(?:[^'\\]|\\.)*'/g, "$1''");
  result = result.replace(/(-t(?:\s+|=))"(?:[^"\\]|\\.)*"/g, '$1""');
  result = result.replace(/(-t(?:\s+|=))'(?:[^'\\]|\\.)*'/g, "$1''");

  // -m "x", -m="x" (message option)
  result = result.replace(/(-m(?:\s+|=))"(?:[^"\\]|\\.)*"/g, '$1""');
  result = result.replace(/(-m(?:\s+|=))'(?:[^'\\]|\\.)*'/g, "$1''");

  return result;
}

/**
 * Quote a shell argument if it contains special characters.
 *
 * Ensures arguments with spaces, tabs, newlines, or quotes are properly quoted
 * when reconstructing a command string from parsed arguments.
 *
 * Issue #3365: Required for proper quoting when re-parsing joined commands.
 */
function shellQuoteIfNeeded(arg: string): string {
  if (/[ \t\n'"\\]/.test(arg)) {
    return `"${arg.replace(/["\\]/g, "\\$&")}"`;
  }
  return arg;
}

/**
 * Strip gh global flags from a command using proper shell argument parsing.
 *
 * Issue #3365: Previous regex-based approach failed with quoted values.
 */
function stripGhGlobalFlags(command: string): string {
  try {
    const args = splitShellArgs(command.replace(/[\r\n]+/g, " "));
    const result: string[] = [];

    for (let i = 0; i < args.length; i++) {
      const arg = args[i];

      // Global flags with separate value: -R owner/repo, --repo owner/repo, -E enterprise, --config file
      if (
        arg === "-R" ||
        arg === "--repo" ||
        arg === "--hostname" ||
        arg === "-E" ||
        arg === "--enterprise" ||
        arg === "--config"
      ) {
        if (i + 1 < args.length) {
          i++; // Skip the value
        }
        continue;
      }

      // Combined flags: -Rowner/repo, --repo=owner/repo, -Eenterprise, --config=file
      if (
        (arg.startsWith("-R") && arg.length > 2) ||
        (arg.startsWith("-E") && arg.length > 2) ||
        arg.startsWith("--repo=") ||
        arg.startsWith("--hostname=") ||
        arg.startsWith("--enterprise=") ||
        arg.startsWith("--config=")
      ) {
        continue;
      }

      // Standalone flags without values
      if (arg === "--help" || arg === "-h" || arg === "--version") {
        continue;
      }

      result.push(shellQuoteIfNeeded(arg));
    }

    return result.join(" ");
  } catch {
    return command;
  }
}

/**
 * Check if any part of the command is a gh pr merge invocation.
 *
 * Handles gh global flags like -R/--repo including quoted values (Issue #3365).
 */
function containsMergeCommand(cmd: string): boolean {
  for (const part of splitCommandChainQuoteAware(cmd)) {
    const normalized = stripGhGlobalFlags(stripEnvPrefix(part)).replace(/\s+/g, " ").trim();
    if (/^gh\s+pr\s+merge\b/.test(normalized)) {
      return true;
    }
  }
  return false;
}

/** Flags that take a value in gh api command */
const GH_API_VALUE_FLAGS =
  /^(-[XfFHtqi]|--field|--header|--raw-field|--input|--jq|--template|--method|--cache)$/;

/**
 * Check if any part of the command is a REST API merge invocation.
 *
 * Issue #1379: Block REST API merge to prevent bypassing hooks.
 * Handles gh global flags including quoted values (Issue #3365).
 */
function containsRestApiMerge(cmd: string): boolean {
  const mergePathPattern = /(?:\/?repos\/[^/]+\/[^/]+\/)?pulls\/\d+\/merge(?:\s|$|[-/])/;

  for (const part of splitCommandChainQuoteAware(cmd)) {
    const normalized = stripGhGlobalFlags(stripEnvPrefix(part));

    if (!/^\s*gh\s+api\s+/.test(normalized)) {
      continue;
    }

    try {
      const args = splitShellArgs(normalized);
      const apiIndex = args.findIndex((a) => a === "api");
      if (apiIndex === -1) continue;

      // Find the first positional argument after "api" (skip flags)
      for (let i = apiIndex + 1; i < args.length; i++) {
        const arg = args[i];
        if (arg.startsWith("-")) {
          if (GH_API_VALUE_FLAGS.test(arg)) {
            i++; // Skip the value
          }
          continue;
        }
        // This is the API endpoint
        if (mergePathPattern.test(arg)) {
          return true;
        }
        break;
      }
    } catch {
      // Fallback to simple pattern match on unquoted portion
      if (mergePathPattern.test(stripQuotedStrings(normalized))) {
        return true;
      }
    }
  }

  return false;
}

// =============================================================================
// PR Status Summary Formatting (Issue #3844)
// =============================================================================

// Module-level lookup tables for performance (Gemini review)
const MERGE_STATE_EMOJI: Record<string, string> = {
  BLOCKED: "❌",
  BEHIND: "⏳",
  CLEAN: "✅",
  DIRTY: "⚠️",
  DRAFT: "📝",
  HAS_HOOKS: "🔗",
  UNKNOWN: "❓",
  UNSTABLE: "⚠️",
};

const MERGE_STATE_DESC: Record<string, string> = {
  BLOCKED: "ブロック中",
  BEHIND: "リベース必要",
  CLEAN: "マージ可能",
  DIRTY: "コンフリクトあり",
  DRAFT: "ドラフト",
  HAS_HOOKS: "フック実行中",
  UNKNOWN: "不明",
  UNSTABLE: "不安定",
};

/**
 * Format unresolved threads line with emoji based on requiredThreadResolution.
 * Issue #3844: Consistent format with --admin block display.
 */
function formatUnresolvedThreads(count: number, required: boolean): string {
  if (required) {
    const emoji = count === 0 ? "✅" : "❌";
    return `  - 未解決スレッド: ${emoji} ${count}件（解決必須）\n`;
  }
  const emoji = count === 0 ? "✅" : "ℹ️";
  return `  - 未解決スレッド: ${emoji} ${count}件\n`;
}

/**
 * Format merge state line with emoji and description.
 * Issue #3844: Consistent format with --admin block display.
 */
function formatMergeState(state: string): string {
  const emoji = MERGE_STATE_EMOJI[state] ?? "❓";
  const desc = MERGE_STATE_DESC[state] ?? state;
  return `  - マージ状態: ${emoji} ${desc}\n`;
}

/**
 * Options for formatting review status.
 * Issue #3849: Unified interface for review status display.
 */
interface ReviewStatusOptions {
  pullRequestRuleFound: boolean;
  requiredApprovals: number;
  currentApprovals: number;
  reviewDecision: string;
}

/**
 * Format review status with consistent logic for both --admin block and PR status summary.
 * Issue #3849: Unified review display logic extracted from --admin block.
 *
 * Decision logic:
 * - If pullRequestRuleFound:
 *   - If requiredApprovals > 0: Show approval count (e.g., "2/2件")
 *   - If requiredApprovals === 0: Check for Code Owners or other requirements
 * - If !pullRequestRuleFound: Show simple reviewDecision with "(Ruleset未検出)"
 */
function formatReviewStatus(options: ReviewStatusOptions): string {
  const { pullRequestRuleFound, requiredApprovals, currentApprovals, reviewDecision } = options;

  if (pullRequestRuleFound) {
    if (requiredApprovals > 0) {
      // Check for CHANGES_REQUESTED or REVIEW_REQUIRED even if approval count is met
      // REVIEW_REQUIRED can occur when Code Owners haven't approved
      const isBlocked =
        reviewDecision === "CHANGES_REQUESTED" || reviewDecision === "REVIEW_REQUIRED";
      const reviewEmoji = !isBlocked && currentApprovals >= requiredApprovals ? "✅" : "❌";
      const suffix =
        reviewDecision === "CHANGES_REQUESTED"
          ? "（変更リクエストあり）"
          : reviewDecision === "REVIEW_REQUIRED"
            ? "（コードオーナー未承認）"
            : "";
      return `  - レビュー承認: ${reviewEmoji} ${currentApprovals}/${requiredApprovals}件${suffix}\n`;
    }
    // requiredApprovals === 0: check for Code Owners or other requirements
    if (reviewDecision === "REVIEW_REQUIRED") {
      return "  - レビュー承認: ❌ 必要（コードオーナー等）\n";
    }
    if (reviewDecision === "CHANGES_REQUESTED") {
      return "  - レビュー承認: ❌ 変更がリクエストされています\n";
    }
    return "  - レビュー承認: ➖ 不要（Rulesetで0件に設定）\n";
  }

  // Ruleset not found: show reviewDecision with localized text
  const reviewEmoji = reviewDecision === "APPROVED" ? "✅" : "❌";
  const reviewDesc: Record<string, string> = {
    APPROVED: "承認済み",
    CHANGES_REQUESTED: "変更リクエストあり",
    REVIEW_REQUIRED: "レビュー待ち",
  };
  const reviewText =
    reviewDesc[reviewDecision] ??
    (reviewDecision === "UNKNOWN" ? "取得失敗" : reviewDecision || "未承認");
  return `  - レビュー承認: ${reviewEmoji} ${reviewText}（Ruleset未検出）\n`;
}

// =============================================================================
// Main Hook Logic
// =============================================================================

async function main(): Promise<void> {
  // Check CLI arguments first
  const cliArgs = parseArgs();

  // Dry-run mode
  if (cliArgs.dryRun) {
    if (!cliArgs.prNumber) {
      console.error("Error: PR number is required for --dry-run mode");
      console.error("Usage: merge_check.ts --dry-run <pr_number>");
      process.exit(2);
    }
    const exitCode = await dryRunCheck(cliArgs.prNumber);
    process.exit(exitCode);
  }

  // Hook mode: read from stdin
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);
    sessionId = ctx.sessionId;
    const command = getBashCommand(data);

    const isMergeCommand = containsMergeCommand(command);

    // Check 0: Block REST API merge (Issue #1379)
    if (containsRestApiMerge(command)) {
      const reason =
        "[merge_check] REST APIによるマージは禁止されています（Issue #1379）。\n\n" +
        "理由: REST APIマージはフックをバイパスし、レビューチェックをスキップします。\n\n" +
        "代わりに以下のコマンドを使用してください:\n" +
        "  gh pr merge {PR番号} --squash\n\n" +
        "rate limit時は待機してから再試行してください。";

      await logHookExecution(HOOK_NAME, "block", "REST API merge blocked", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(makeBlockResult(HOOK_NAME, reason)));
      return;
    }

    // Not a merge command, skip with JSON output (Issue #2940)
    if (!isMergeCommand) {
      await logHookExecution(HOOK_NAME, "skip", "Not a merge command", undefined, { sessionId });
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Strip option values and quoted strings for option detection
    const strippedCommand = stripQuotedStrings(command);
    const commandWithoutBody = stripOptionValues(command);

    // Check 1: Block auto-merge
    // Issue #3263: Use word boundary regex to prevent false positives (e.g., --auto-merge-method)
    const quotedAuto = /(?:^|\s)(?:"--auto"|'--auto')(?:\s|$)/.test(commandWithoutBody);
    const hasAuto = /(?:^|\s)--auto(?:\s|$|=)/.test(strippedCommand) || quotedAuto;

    if (isMergeCommand && hasAuto) {
      const reason =
        "auto-mergeは使用しないでください。\n" +
        "Copilot/Codexレビューを確認してから手動でマージしてください:\n" +
        "1. gh api repos/:owner/:repo/pulls/{PR番号} " +
        "--jq '.requested_reviewers[].login' で進行中確認\n" +
        "2. gh api repos/:owner/:repo/pulls/{PR番号}/reviews " +
        "でレビュー確認\n" +
        "3. gh pr merge {PR番号} --squash で手動マージ";

      await logHookExecution(HOOK_NAME, "block", "--auto option blocked", undefined, { sessionId });
      console.log(JSON.stringify(makeBlockResult(HOOK_NAME, reason)));
      return;
    }

    // Check 2: Block admin merge (Issue #2377)
    // Issue #3263: Use word boundary regex to prevent false positives (e.g., --administrator)
    const quotedAdmin = /(?:^|\s)(?:"--admin"|'--admin')(?:\s|$)/.test(commandWithoutBody);
    const hasAdmin = /(?:^|\s)--admin(?:\s|$|=)/.test(strippedCommand) || quotedAdmin;

    if (isMergeCommand && hasAdmin) {
      const prNumber = extractPrNumber(command);
      const reasonParts: string[] = [
        "--adminオプションは使用しないでください。",
        "ブランチ保護ルールを迂回するマージは禁止されています。",
        "",
      ];

      // Get PR status for detailed guidance
      if (prNumber) {
        const status = await getPrMergeStatus(prNumber);

        reasonParts.push(`📋 PR #${prNumber} の現在の状態:`);

        const rawCiStatus = status.statusCheckStatus ?? "UNKNOWN";
        const ciStatusEmoji: Record<string, string> = {
          SUCCESS: "✅",
          FAILURE: "❌",
          ERROR: "💥", // Issue #3263: Handle GitHub Check Run API ERROR status
          PENDING: "⏳",
          NONE: "➖",
          UNKNOWN: "❓",
        };
        const ciStatusText =
          rawCiStatus === "UNKNOWN" ? "取得失敗（GitHub APIエラーの可能性）" : rawCiStatus;
        reasonParts.push(`  - CI: ${ciStatusEmoji[rawCiStatus] ?? "❓"} ${ciStatusText}`);

        // Issue #3633, #3761, #3849: Show review requirements based on ruleset
        // Use unified formatReviewStatus for consistent display
        const reviewStatusLine = formatReviewStatus({
          pullRequestRuleFound: status.pullRequestRuleFound ?? false,
          requiredApprovals: status.requiredApprovals ?? 0,
          currentApprovals: status.currentApprovals ?? 0,
          reviewDecision: status.reviewDecision ?? "",
        });
        // Remove trailing newline since reasonParts.push adds its own formatting
        reasonParts.push(reviewStatusLine.trimEnd());

        // Issue #3633: Show unresolved thread count
        const unresolvedThreads = status.unresolvedThreads ?? 0;
        const requiredThreadResolution = status.requiredThreadResolution ?? false;
        if (requiredThreadResolution) {
          const threadEmoji = unresolvedThreads === 0 ? "✅" : "❌";
          reasonParts.push(`  - 未解決スレッド: ${threadEmoji} ${unresolvedThreads}件`);
        }

        const rawMergeState = status.mergeStateStatus ?? "UNKNOWN";
        const mergeStateText = rawMergeState === "UNKNOWN" ? "取得失敗" : rawMergeState;
        reasonParts.push(`  - マージ状態: ${mergeStateText}`);
        reasonParts.push("");

        // Show blocking reasons if any
        if (status.blockingReasons.length > 0) {
          reasonParts.push("⚠️ ブロック理由:");
          for (const br of status.blockingReasons) {
            reasonParts.push(`  - ${br}`);
          }
          reasonParts.push("");
        }

        // Show suggested actions
        if (status.suggestedActions.length > 0) {
          reasonParts.push("🔧 解決方法:");
          for (let i = 0; i < status.suggestedActions.length; i++) {
            reasonParts.push(`  ${i + 1}. ${status.suggestedActions[i]}`);
          }
          reasonParts.push("");
        }

        // If no specific blocking reasons detected, show generic guidance
        if (status.blockingReasons.length === 0) {
          reasonParts.push("マージがブロックされている場合は、原因を確認してください:");
          reasonParts.push("1. CIが失敗していないか確認");
          reasonParts.push("2. 未解決のレビュースレッドがないか確認");
          reasonParts.push("3. 必要な承認が得られているか確認");
          reasonParts.push("");
        }
      } else {
        reasonParts.push("マージがブロックされている場合は、原因を確認してください:");
        reasonParts.push("1. CIが失敗していないか確認");
        reasonParts.push("2. 未解決のレビュースレッドがないか確認");
        reasonParts.push("3. 必要な承認が得られているか確認");
        reasonParts.push("");
      }

      reasonParts.push("問題を解決してから、通常のマージを実行してください:");
      reasonParts.push(`gh pr merge ${prNumber ?? "{PR番号}"} --squash`);

      await logHookExecution(HOOK_NAME, "block", "--admin option blocked", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(makeBlockResult(HOOK_NAME, reasonParts.join("\n"))));
      return;
    }

    // Collect all blocking reasons for PR state checks (Issue #874)
    let blockingReasons: BlockingReason[] = [];
    let allWarnings: string[] = [];

    // Track first merge command's PR info for status summary (Issue #3585)
    let firstMergePrNumber: string | null = null;
    let firstMergeRepo: string | null = null;

    // Check 3-9: Run all PR checks for merge commands
    // Issue #3263: Also handle `gh pr merge --squash` without explicit PR number
    // Issue #3345: Also handle branch name or URL specified merge commands
    // AI Review fix: Iterate through ALL merge commands in chained commands
    //   to prevent bypass via `gh pr merge A && gh pr merge B`
    if (isMergeCommand) {
      const allCommands = parseAllGhPrCommands(command);

      // Filter for merge commands and check each one
      const mergeCommands = allCommands.filter(([subcommand]) => subcommand === "merge");

      for (const parsedCommand of mergeCommands) {
        // Each command uses its own repo from the parsed tuple
        const [, , repo] = parsedCommand;
        const prNumber = await resolvePrNumberFromParsedCommand(parsedCommand);

        if (prNumber) {
          // Store first merge command's info for status summary
          if (firstMergePrNumber === null) {
            firstMergePrNumber = prNumber;
            firstMergeRepo = repo;
          }
          const [reasons, warnings] = await runAllPrChecks(prNumber, false, repo);
          // Accumulate all blocking reasons and warnings from all merge commands
          blockingReasons = [...blockingReasons, ...reasons];
          allWarnings = [...allWarnings, ...warnings];
        }
      }
    }

    // Log warnings (non-blocking but should be visible)
    for (const warning of allWarnings) {
      console.error(warning);
      await logHookExecution(HOOK_NAME, "warning", warning, undefined, { sessionId });
    }

    // If there are blocking reasons, display all at once (Issue #874)
    if (blockingReasons.length > 0) {
      const prNumberStr = extractPrNumber(command) ?? "?";
      const header = `マージがブロックされました（PR #${prNumberStr}）。以下の問題を解決してください:\n`;
      const separator = `\n${"=".repeat(60)}\n`;

      const reasonParts: string[] = [header];

      // Issue #3585: Show PR status summary including unresolved threads count
      // to help users identify the actual cause of merge blocking
      // Note: Skip if repo is specified (getPrMergeStatus doesn't support repo arg yet)
      // Issue #3844: Format improvements for consistency with --admin block display
      if (firstMergePrNumber && !firstMergeRepo) {
        const status = await getPrMergeStatus(firstMergePrNumber);
        reasonParts.push("\n📋 PR状態サマリ:\n");

        // Unresolved threads with emoji based on requiredThreadResolution
        reasonParts.push(
          formatUnresolvedThreads(
            status.unresolvedThreads ?? 0,
            status.requiredThreadResolution ?? false,
          ),
        );

        // Merge state with emoji and description
        reasonParts.push(formatMergeState(status.mergeStateStatus ?? "UNKNOWN"));

        // Review status with unified logic (Issue #3849)
        reasonParts.push(
          formatReviewStatus({
            pullRequestRuleFound: status.pullRequestRuleFound ?? false,
            requiredApprovals: status.requiredApprovals ?? 0,
            currentApprovals: status.currentApprovals ?? 0,
            reviewDecision: status.reviewDecision ?? "",
          }),
        );
        reasonParts.push("\n");
      }

      for (let i = 0; i < blockingReasons.length; i++) {
        const br = blockingReasons[i];
        reasonParts.push(`\n【問題 ${i + 1}/${blockingReasons.length}】${br.title}\n`);
        reasonParts.push(br.details);
        if (i < blockingReasons.length - 1) {
          reasonParts.push(separator);
        }
      }

      reasonParts.push(
        `\n\n全${blockingReasons.length}件の問題を解決後、再度マージを実行してください。`,
      );
      const combinedReason = reasonParts.join("");

      await logHookExecution(
        HOOK_NAME,
        "block",
        `Blocked by: ${blockingReasons.map((br) => br.checkName).join(", ")}`,
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(makeBlockResult(HOOK_NAME, combinedReason)));
      return;
    }

    // All checks passed - remind about commit message background (Issue #2347)
    if (isMergeCommand && /\d+/.test(command)) {
      const reminderMessage = [
        "[REMINDER] マージコミットに背景（Why）を含めてください。",
        '例: gh pr merge {PR番号} --squash --body "背景: ..."',
        "詳細: managing-development Skill の「コミットメッセージ規約」参照",
      ].join("\n");
      console.error(reminderMessage);
    }

    await logHookExecution(HOOK_NAME, "approve", "All checks passed", undefined, { sessionId });
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME, "All checks passed")));
  } catch (e) {
    // On error, approve to avoid blocking
    const errorMsg = `Hook error: ${formatError(e)}`;
    console.error(`[${HOOK_NAME}] ${errorMsg}`);
    await logHookExecution(HOOK_NAME, "approve", errorMsg, undefined, { sessionId });
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME, errorMsg)));
  }
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
  });
}

// =============================================================================
// Exports for Testing (Issue #3365)
// =============================================================================
// These internal functions are exported for unit testing purposes only.
// They should not be used directly by other modules.

export const _testExports = {
  stripGhGlobalFlags,
  containsMergeCommand,
  containsRestApiMerge,
  // Issue #3844, #3849: Format helpers for PR status summary
  formatUnresolvedThreads,
  formatMergeState,
  formatReviewStatus,
};
