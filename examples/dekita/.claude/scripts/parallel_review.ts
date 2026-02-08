#!/usr/bin/env bun
/**
 * codex review と gemini /code-review を並列実行するスクリプト
 *
 * Why:
 *   各レビューは30秒〜2分かかるため、順次実行だと待ち時間が発生する。
 *   並列実行により、合計待ち時間を短縮する。
 *
 * What:
 *   - codex review: Codex CLIでコードレビュー
 *   - gemini /code-review: Gemini CLIでコードレビュー
 *   - 両方をバックグラウンドで並列実行し、完了を待機
 *   - Gemini/Codexレビュー成功時にマーカーファイルを作成
 *
 * Remarks:
 *   - gemini CLIは --yolo -e code-review フラグで非対話モード実行
 *   - 出力は一時ファイルに保存し、完了後に表示
 *   - どちらかが失敗しても両方の結果を表示
 *
 * Changelog:
 *   - silenvx/dekita#3078: parallel_review.shをTypeScriptに移行
 *   - silenvx/dekita#3106: markers.tsからgetMarkersDirAsyncを使用し重複コード削減
 */

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";

import {
  createRateLimitMarker,
  isCodexRateLimited,
  removeRateLimitMarker,
} from "../hooks/handlers/codex_review_output_logger";
import {
  BLOCKING_SEVERITIES,
  CODEX_PRIORITY_BADGES,
  GEMINI_PRIORITY_BADGES,
  GEMINI_SECURITY_BADGES,
  PENDING_REVIEW_MARKER_PREFIX,
  WARNING_SEVERITIES,
} from "../hooks/lib/constants";
import { getCurrentBranch, getHeadCommitFull } from "../hooks/lib/git";
import { getMarkersDirAsync, parseCycleCount } from "../hooks/lib/markers";

// =============================================================================
// Types
// =============================================================================

interface ReviewResult {
  name: string;
  output: string;
  exitCode: number;
  success: boolean;
}

interface Options {
  baseBranch: string;
  verbose: boolean;
  help?: boolean;
  error?: string;
}

/**
 * Represents a finding from AI review that requires response.
 * Issue #3106: Track MEDIUM+ findings for response enforcement.
 */
interface ReviewFinding {
  severity: string;
  source: "codex" | "gemini";
  snippet: string; // First 100 chars of the finding context
}

/**
 * Pending review marker data structure.
 * Written to pending-review-{branch}.json when MEDIUM+ findings are detected.
 */
interface PendingReviewMarker {
  branch: string;
  commit: string;
  timestamp: string;
  findings: ReviewFinding[];
}

// =============================================================================
// Constants
// =============================================================================

const DEFAULT_BASE_BRANCH = "main";
const TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes

// Track active processes for cleanup on SIGINT
// biome-ignore lint/suspicious/noExplicitAny: Subprocess type varies between Bun versions
const activeProcesses: any[] = [];

// =============================================================================
// Utility Functions
// =============================================================================

function log(verbose: boolean, message: string): void {
  if (verbose) {
    const time = new Date().toTimeString().slice(0, 8);
    console.log(`[${time}] ${message}`);
  }
}

/**
 * Sanitize branch name for use in filenames (Gemini format).
 * Replaces characters not in [a-zA-Z0-9._-] with hyphens.
 * Matches gemini_review_logger.ts behavior.
 */
export function sanitizeBranchNameGemini(branch: string): string {
  return branch.replace(/[^a-zA-Z0-9._-]/g, "-");
}

/**
 * Sanitize branch name for use in filenames (Codex format).
 * Matches lib/strings.py behavior.
 */
export function sanitizeBranchNameCodex(branch: string): string {
  return branch
    .replace(/[/\\]/g, "-") // Replace slash and backslash with -
    .replace(/[:<>"|?*]/g, "-") // Replace [:<>"|?*] with -
    .replace(/ /g, "_") // Replace space with _
    .replace(/-+/g, "-") // Remove consecutive dashes
    .replace(/^-|-$/g, ""); // Remove leading/trailing dashes
}

/**
 * Get diff hash for Gemini marker (3-dot diff, 16 chars).
 * Uses origin/{baseBranch}...HEAD format.
 */
async function getDiffHashGemini(baseBranch: string): Promise<string | null> {
  try {
    const proc = Bun.spawn(["git", "diff", `origin/${baseBranch}...HEAD`], {
      stdout: "pipe",
      stderr: "ignore",
    });

    const hash = createHash("sha256");
    let hasContent = false;
    for await (const chunk of proc.stdout) {
      hasContent = true;
      hash.update(chunk);
    }

    const exitCode = await proc.exited;
    // Return null for empty diff (no changes)
    if (!hasContent) {
      return null;
    }
    return exitCode <= 1 ? hash.digest("hex").slice(0, 16) : null;
  } catch {
    return null;
  }
}

/**
 * Get diff hash for Codex marker (2-dot diff, 12 chars).
 * Uses main branch for compatibility with lib/git.py.
 */
async function getDiffHashCodex(): Promise<string | null> {
  try {
    // Verify "main" branch exists
    const verifyProc = Bun.spawn(["git", "rev-parse", "--verify", "main"], {
      stdout: "ignore",
      stderr: "ignore",
    });
    if ((await verifyProc.exited) !== 0) {
      return null;
    }

    const proc = Bun.spawn(["git", "diff", "main"], {
      stdout: "pipe",
      stderr: "ignore",
    });

    const hash = createHash("sha256");
    let hasContent = false;
    for await (const chunk of proc.stdout) {
      hasContent = true;
      hash.update(chunk);
    }

    const exitCode = await proc.exited;
    // Return null for empty diff (no changes)
    if (!hasContent) {
      return null;
    }
    return exitCode <= 1 ? hash.digest("hex").slice(0, 12) : null;
  } catch {
    return null;
  }
}

// =============================================================================
// Marker Creation Functions
// =============================================================================

/**
 * Create Gemini review marker file.
 * Called after successful gemini review execution.
 */
async function createGeminiMarker(baseBranch: string, verbose: boolean): Promise<boolean> {
  const branch = await getCurrentBranch();
  if (!branch) {
    console.error("Warning: Failed to get current branch, skipping marker creation");
    return false;
  }

  // Skip for base branch
  if (branch === baseBranch || branch === "master") {
    log(verbose, `Skipping marker for base branch: ${branch}`);
    return true;
  }

  const commit = await getHeadCommitFull();
  if (!commit) {
    console.error("Warning: Failed to get HEAD commit, skipping marker creation");
    return false;
  }

  const diffHash = await getDiffHashGemini(baseBranch);
  const markersDir = await getMarkersDirAsync();

  try {
    mkdirSync(markersDir, { recursive: true });
  } catch {
    console.error(`Warning: Failed to create markers directory: ${markersDir}`);
    return false;
  }

  const safeBranch = sanitizeBranchNameGemini(branch);
  const markerFile = `${markersDir}/gemini-review-${safeBranch}.done`;

  // Read existing cycle count and increment (Issue #3984)
  const previousCycleCount = parseCycleCount(markerFile);
  const cycleCount = previousCycleCount + 1;

  // Build content: branch:commit:diffHash:cycleCount
  // Always write 4-field format to avoid field confusion with legacy 2-field markers (Issue #3984)
  const content = `${branch}:${commit}:${diffHash ?? ""}:${cycleCount}`;

  try {
    writeFileSync(markerFile, content);
    log(verbose, `Created Gemini marker: ${markerFile} (cycle: ${cycleCount})`);
    return true;
  } catch {
    console.error(`Warning: Failed to write marker file: ${markerFile}`);
    return false;
  }
}

/**
 * Create Codex review marker file.
 * Called after successful codex review execution.
 */
async function createCodexMarker(baseBranch: string, verbose: boolean): Promise<boolean> {
  const branch = await getCurrentBranch();
  if (!branch) {
    console.error("Warning: Failed to get current branch, skipping marker creation");
    return false;
  }

  // Skip for base branch
  if (branch === baseBranch || branch === "master") {
    log(verbose, `Skipping marker for base branch: ${branch}`);
    return true;
  }

  // Use full hash for reliable comparison independent of core.abbrev
  const commit = await getHeadCommitFull();
  if (!commit) {
    console.error("Warning: Failed to get HEAD commit, skipping marker creation");
    return false;
  }

  const diffHash = await getDiffHashCodex();
  const markersDir = await getMarkersDirAsync();

  try {
    mkdirSync(markersDir, { recursive: true });
  } catch {
    console.error(`Warning: Failed to create markers directory: ${markersDir}`);
    return false;
  }

  const safeBranch = sanitizeBranchNameCodex(branch);
  const markerFile = `${markersDir}/codex-review-${safeBranch}.done`;

  // Read existing cycle count and increment (Issue #3984)
  const previousCycleCount = parseCycleCount(markerFile);
  const cycleCount = previousCycleCount + 1;

  // Build content: branch:commit:diffHash:cycleCount
  // Always write 4-field format to avoid field confusion with legacy 2-field markers (Issue #3984)
  const content = `${branch}:${commit}:${diffHash ?? ""}:${cycleCount}`;

  try {
    writeFileSync(markerFile, content);
    log(verbose, `Created Codex marker: ${markerFile} (cycle: ${cycleCount})`);
    return true;
  } catch {
    console.error(`Warning: Failed to write marker file: ${markerFile}`);
    return false;
  }
}

// =============================================================================
// Finding Detection Functions (Issue #3106)
// =============================================================================

/**
 * Detect blocking findings (MEDIUM+) from review output.
 * Returns findings that require response before push.
 */
function detectBlockingFindings(output: string, source: "codex" | "gemini"): ReviewFinding[] {
  const findings: ReviewFinding[] = [];
  const lines = output.split("\n");

  // Choose badge patterns based on source
  const priorityBadges = source === "codex" ? CODEX_PRIORITY_BADGES : GEMINI_PRIORITY_BADGES;

  // Check priority badges
  for (const [severity, pattern] of Object.entries(priorityBadges)) {
    if (!BLOCKING_SEVERITIES.has(severity)) continue;

    // Find all matches with context
    for (let i = 0; i < lines.length; i++) {
      if (pattern.test(lines[i])) {
        // Get context: current line and next line if available
        const snippet = lines
          .slice(i, i + 2)
          .join(" ")
          .slice(0, 100);
        findings.push({ severity, source, snippet });
      }
    }
  }

  // Check security badges (Gemini only)
  if (source === "gemini") {
    for (const [severity, pattern] of Object.entries(GEMINI_SECURITY_BADGES)) {
      for (let i = 0; i < lines.length; i++) {
        if (pattern.test(lines[i])) {
          const snippet = lines
            .slice(i, i + 2)
            .join(" ")
            .slice(0, 100);
          findings.push({ severity, source, snippet });
        }
      }
    }
  }

  return findings;
}

/**
 * Detect warning findings (P2/low) from review output.
 * These don't block push but should be addressed or tracked in an Issue.
 * Issue #3167: Added to ensure P2 findings are not silently ignored.
 */
function detectWarningFindings(output: string, source: "codex" | "gemini"): ReviewFinding[] {
  const findings: ReviewFinding[] = [];
  const lines = output.split("\n");

  // Choose badge patterns based on source
  const priorityBadges = source === "codex" ? CODEX_PRIORITY_BADGES : GEMINI_PRIORITY_BADGES;

  // Check priority badges for warning severities
  for (const [severity, pattern] of Object.entries(priorityBadges)) {
    if (!WARNING_SEVERITIES.has(severity)) continue;

    // Find all matches with context
    for (let i = 0; i < lines.length; i++) {
      if (pattern.test(lines[i])) {
        const snippet = lines
          .slice(i, i + 2)
          .join(" ")
          .slice(0, 100);
        findings.push({ severity, source, snippet });
      }
    }
  }

  return findings;
}

/**
 * Get reviewer status string based on result and findings.
 * Prioritizes: FAILED > BLOCKED > PASSED with warnings > PASSED
 */
function getReviewerStatus(
  result: ReviewResult,
  blockingFindings: ReviewFinding[],
  warnings: ReviewFinding[],
  source: "codex" | "gemini",
): string {
  if (!result.success) {
    return `FAILED (exit: ${result.exitCode})`;
  }
  if (blockingFindings.some((f) => f.source === source)) {
    return "BLOCKED";
  }
  if (warnings.some((w) => w.source === source)) {
    return "PASSED with warnings";
  }
  return "PASSED";
}

/**
 * Create pending review marker file when MEDIUM+ findings are detected.
 * This marker is checked by review_response_check.ts on git push.
 */
async function createPendingReviewMarker(
  findings: ReviewFinding[],
  verbose: boolean,
): Promise<boolean> {
  if (findings.length === 0) {
    return true; // No findings, no marker needed
  }

  const branch = await getCurrentBranch();
  if (!branch) {
    console.error("Warning: Failed to get current branch for pending review marker");
    return false;
  }

  // Use full hash for reliable comparison independent of core.abbrev
  const commit = await getHeadCommitFull();
  if (!commit) {
    console.error("Warning: Failed to get HEAD commit for pending review marker");
    return false;
  }

  const markersDir = await getMarkersDirAsync();
  try {
    mkdirSync(markersDir, { recursive: true });
  } catch {
    console.error(`Warning: Failed to create markers directory: ${markersDir}`);
    return false;
  }

  const safeBranch = sanitizeBranchNameGemini(branch);
  const markerFile = `${markersDir}/${PENDING_REVIEW_MARKER_PREFIX}${safeBranch}.json`;

  const markerData: PendingReviewMarker = {
    branch,
    commit,
    timestamp: new Date().toISOString(),
    findings,
  };

  try {
    writeFileSync(markerFile, JSON.stringify(markerData, null, 2));
    log(verbose, `Created pending review marker: ${markerFile}`);
    // Note: Console output is handled centrally in main() for consistent formatting
    return true;
  } catch (error) {
    // Issue #3199: Log error details for debugging
    const errorMessage = error instanceof Error ? error.message : String(error);
    console.error(
      `Warning: Failed to write pending review marker: ${markerFile} (${errorMessage})`,
    );
    return false;
  }
}

/**
 * Remove pending review marker (called when no blocking findings).
 */
async function removePendingReviewMarker(verbose: boolean): Promise<void> {
  const branch = await getCurrentBranch();
  if (!branch) return;

  const markersDir = await getMarkersDirAsync();
  const safeBranch = sanitizeBranchNameGemini(branch);
  const markerFile = `${markersDir}/${PENDING_REVIEW_MARKER_PREFIX}${safeBranch}.json`;

  try {
    if (existsSync(markerFile)) {
      const { unlinkSync } = await import("node:fs");
      unlinkSync(markerFile);
      log(verbose, `Removed pending review marker: ${markerFile}`);
    }
  } catch {
    // Ignore errors when removing marker
  }
}

// =============================================================================
// Review Execution Functions
// =============================================================================

/**
 * Run a review command and capture output.
 */
async function runReview(
  name: string,
  command: string,
  args: string[],
  verbose: boolean,
): Promise<ReviewResult> {
  log(verbose, `Starting ${name}...`);

  let output = `=== ${name} ===\n`;

  try {
    const proc = Bun.spawn([command, ...args], {
      stdout: "pipe",
      stderr: "pipe",
      stdin: "ignore", // Prevent interactive prompts from hanging the process
    });

    // Track process for cleanup on SIGINT
    activeProcesses.push(proc);

    // Read stdout and stderr concurrently
    const stdoutPromise = new Response(proc.stdout).text();
    const stderrPromise = new Response(proc.stderr).text();

    // Race between process completion and timeout
    let timeoutId: ReturnType<typeof setTimeout>;
    const timeoutPromise = new Promise<"timeout">((resolve) => {
      timeoutId = setTimeout(() => resolve("timeout"), TIMEOUT_MS);
    });

    try {
      const result = await Promise.race([proc.exited, timeoutPromise]);

      if (result === "timeout") {
        proc.kill();
        output += "\n[TIMEOUT]\n";
        log(verbose, `${name} timed out`);
        return { name, output, exitCode: -1, success: false };
      }

      const [stdout, stderr] = await Promise.all([stdoutPromise, stderrPromise]);
      output += stdout + stderr;

      const exitCode = result;
      const success = exitCode === 0;
      output += success ? "\n[SUCCESS]\n" : `\n[FAILED] (exit: ${exitCode})\n`;
      log(verbose, `${name} completed (exit: ${exitCode})`);

      return { name, output, exitCode, success };
    } finally {
      clearTimeout(timeoutId!);
      // Remove from active processes
      const idx = activeProcesses.indexOf(proc);
      if (idx !== -1) {
        activeProcesses.splice(idx, 1);
      }
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    output += `\n[ERROR] ${message}\n`;
    log(verbose, `${name} error: ${message}`);
    return { name, output, exitCode: -1, success: false };
  }
}

// =============================================================================
// Main
// =============================================================================

export function parseArgs(args: string[]): Options {
  const options: Options = {
    baseBranch: DEFAULT_BASE_BRANCH,
    verbose: false,
  };

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    switch (arg) {
      case "--base": {
        if (i + 1 >= args.length) {
          return { ...options, error: "--base requires a branch name argument" };
        }
        const branch = args[i + 1];
        if (branch.startsWith("-")) {
          return {
            ...options,
            error: `Invalid branch name '${branch}'. Branch names cannot start with a hyphen.`,
          };
        }
        options.baseBranch = branch;
        i++;
        break;
      }
      case "--verbose":
      case "-v":
        options.verbose = true;
        break;
      case "--help":
      case "-h":
        return { ...options, help: true };
      default:
        return { ...options, error: `Unknown option: ${arg}` };
    }
  }

  return options;
}

async function checkCommand(command: string): Promise<boolean> {
  try {
    const proc = Bun.spawn(["which", command], {
      stdout: "ignore",
      stderr: "ignore",
    });
    return (await proc.exited) === 0;
  } catch {
    return false;
  }
}

const HELP_MESSAGE = `Usage: bun run .claude/scripts/parallel_review.ts [OPTIONS]

Run codex review and gemini /code-review in parallel.

Options:
  --base <branch>  Compare against branch (default: main)
  --verbose, -v    Show detailed progress
  --help, -h       Show this help message

Examples:
  bun run .claude/scripts/parallel_review.ts                    # Review current branch vs main
  bun run .claude/scripts/parallel_review.ts --base develop     # Review against develop branch`;

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const options = parseArgs(args);

  // Handle help and error
  if (options.help) {
    console.log(HELP_MESSAGE);
    process.exit(0);
  }
  if (options.error) {
    console.error(`Error: ${options.error}`);
    console.error("Use --help for usage information");
    process.exit(1);
  }

  // Check if codex is available
  if (!(await checkCommand("codex"))) {
    console.error("Error: codex CLI is not installed or not in PATH");
    process.exit(1);
  }

  // Check if gemini is available
  if (!(await checkCommand("gemini"))) {
    console.error("Error: gemini CLI is not installed or not in PATH");
    process.exit(1);
  }

  console.log("Starting parallel review...");
  console.log(`Base branch: ${options.baseBranch}`);
  console.log("");

  // Start both reviews in parallel
  const codexPromise = runReview(
    "Codex Review",
    "codex",
    ["review", "--base", options.baseBranch],
    options.verbose,
  );

  const geminiPromise = runReview(
    "Gemini Review",
    "gemini",
    ["/code-review", "--yolo", "-e", "code-review"],
    options.verbose,
  );

  log(options.verbose, "Waiting for reviews to complete...");

  // Wait for both to complete
  const [codexResult, geminiResult] = await Promise.all([codexPromise, geminiPromise]);

  // Create markers for successful reviews
  if (codexResult.success) {
    // Remove stale rate limit marker on success
    const branch = await getCurrentBranch();
    if (branch) {
      removeRateLimitMarker(branch);
    }
    const markerSuccess = await createCodexMarker(options.baseBranch, options.verbose);
    if (!markerSuccess) {
      codexResult.output += "[WARNING] Marker creation failed, but review succeeded\n";
    }
  } else if (isCodexRateLimited(codexResult.output)) {
    const branch = await getCurrentBranch();
    if (branch && branch !== options.baseBranch && branch !== "master") {
      const success = createRateLimitMarker(branch);
      if (success) {
        log(options.verbose, "Created Codex rate limit marker");
      } else {
        console.error("Warning: Failed to create Codex rate limit marker");
      }
    }
  }

  if (geminiResult.success) {
    const markerSuccess = await createGeminiMarker(options.baseBranch, options.verbose);
    if (!markerSuccess) {
      geminiResult.output += "[WARNING] Marker creation failed, but review succeeded\n";
    }
  }

  // Issue #3106: Detect MEDIUM+ findings and create pending review marker
  const blockingFindings: ReviewFinding[] = [];
  const warningFindings: ReviewFinding[] = [];
  const atLeastOneReviewSucceeded = codexResult.success || geminiResult.success;

  if (codexResult.success) {
    const codexFindings = detectBlockingFindings(codexResult.output, "codex");
    const codexWarnings = detectWarningFindings(codexResult.output, "codex");
    blockingFindings.push(...codexFindings);
    warningFindings.push(...codexWarnings);
  }

  if (geminiResult.success) {
    const geminiFindings = detectBlockingFindings(geminiResult.output, "gemini");
    const geminiWarnings = detectWarningFindings(geminiResult.output, "gemini");
    blockingFindings.push(...geminiFindings);
    warningFindings.push(...geminiWarnings);
  }

  // Issue #3199: Track marker creation result for conditional message display
  let markerCreated = false;
  if (blockingFindings.length > 0) {
    markerCreated = await createPendingReviewMarker(blockingFindings, options.verbose);
  } else if (atLeastOneReviewSucceeded) {
    // No blocking findings AND at least one review succeeded - remove any existing pending marker
    // Don't remove if both reviews failed (findings might still exist from previous run)
    await removePendingReviewMarker(options.verbose);
  }

  // Display results
  console.log("");
  console.log("==========================================");
  console.log("           REVIEW RESULTS");
  console.log("==========================================");
  console.log("");

  console.log(codexResult.output);
  console.log("------------------------------------------");
  console.log("");
  console.log(geminiResult.output);
  console.log("==========================================");

  // Summary with blocking and warning detection (Issue #3167, #3189)
  const hasBlockingFindings = blockingFindings.length > 0;
  const hasWarnings = warningFindings.length > 0;

  console.log("");
  console.log("Summary:");

  // Show status for each reviewer
  console.log(
    `  - Codex:  ${getReviewerStatus(codexResult, blockingFindings, warningFindings, "codex")}`,
  );
  console.log(
    `  - Gemini: ${getReviewerStatus(geminiResult, blockingFindings, warningFindings, "gemini")}`,
  );

  // Issue #3189: Show blocking findings (P0/P1/HIGH/MEDIUM)
  // Issue #4085: Show snippet (first ~100 chars of finding context) in summary for tail users
  // Note: snippet may contain severity badges from review output; stripped before display
  if (hasBlockingFindings) {
    // Group findings by source for cleaner output
    const codexCount = blockingFindings.filter((f) => f.source === "codex").length;
    const geminiCount = blockingFindings.filter((f) => f.source === "gemini").length;

    console.log("");
    // Strip severity/security badges from snippet (first ~100 chars of finding context)
    // to avoid duplication with the [severity] prefix we prepend
    const stripBadge = (s: string): string =>
      s
        .trimStart()
        .replace(/^(?:###\s+L\d+:\s*)?/, "")
        .replace(
          /^!?\[(?:P[0-3]|HIGH|MEDIUM|LOW|security-(?:critical|high|medium))(?:\s+Badge)?\](?:\([^)]*\))?\s*/i,
          "",
        )
        .trim();

    console.log("🚫 ブロック対象の指摘があります（対応必須）:");
    if (codexCount > 0) {
      console.log(`   - Codex: ${codexCount}件`);
      for (const f of blockingFindings.filter((f) => f.source === "codex")) {
        console.log(`     - [${f.severity}] ${stripBadge(f.snippet)}`);
      }
    }
    if (geminiCount > 0) {
      console.log(`   - Gemini: ${geminiCount}件`);
      for (const f of blockingFindings.filter((f) => f.source === "gemini")) {
        console.log(`     - [${f.severity}] ${stripBadge(f.snippet)}`);
      }
    }
    // Issue #3199: Only show push-blocking message if the marker was created
    if (markerCreated) {
      console.log("   プッシュ前に対応が必要です。");
    }
    console.log("");
  }

  // Issue #3167: Show warning findings that need attention
  // Issue #3199: Don't log snippet content to avoid exposing sensitive information in CI logs
  if (hasWarnings) {
    // Group warnings by source for cleaner output
    const codexWarnings = warningFindings.filter((f) => f.source === "codex").length;
    const geminiWarnings = warningFindings.filter((f) => f.source === "gemini").length;

    console.log("");
    console.log("⚠️  P2/低優先度の指摘があります（対応推奨）:");
    if (codexWarnings > 0) {
      console.log(`   - Codex: ${codexWarnings}件`);
    }
    if (geminiWarnings > 0) {
      console.log(`   - Gemini: ${geminiWarnings}件`);
    }
    console.log("");
    console.log("   詳細は上記のレビュー出力を確認してください。");
    console.log("   以下のいずれかの対応を推奨します:");
    console.log("   1. コード修正してコミット");
    console.log("   2. Issueを作成して追跡（gh issue create）");
    console.log("   3. 対応不要な理由をコミットメッセージに記載");
    console.log("");
  }

  // Show success only when all reviews succeeded and no findings
  const allReviewsSucceeded = codexResult.success && geminiResult.success;
  if (allReviewsSucceeded && !hasBlockingFindings && !hasWarnings) {
    console.log("");
    console.log("All reviews completed successfully.");
  }

  // Exit with error if either failed (moved to end to ensure findings are visible)
  if (!codexResult.success || !geminiResult.success) {
    process.exit(1);
  }
}

// Only run main when executed directly, not when imported
if (import.meta.main) {
  // Handle SIGINT (Ctrl+C) - kill all active processes to prevent orphans
  // Registered inside import.meta.main to avoid side effects on import (Issue #3078)
  process.on("SIGINT", () => {
    console.log("\nInterrupted. Cleaning up...");
    for (const proc of activeProcesses) {
      try {
        proc.kill();
      } catch {
        // Process may have already exited
      }
    }
    process.exit(130); // Standard exit code for SIGINT
  });

  main().catch((error) => {
    console.error(`Error: ${error}`);
    process.exit(1);
  });
}
