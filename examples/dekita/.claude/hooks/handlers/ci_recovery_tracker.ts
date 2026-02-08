#!/usr/bin/env bun
/**
 * CI失敗から復旧までの時間を追跡する。
 *
 * Why:
 *   CI失敗の復旧時間を計測することで、チームの対応速度を可視化し、
 *   改善の指標として活用できる。
 *
 * What:
 *   - CI失敗時にタイムスタンプを記録
 *   - CI成功時に復旧時間を計算してログ記録
 *   - 復旧時間をsystemMessageで表示
 *
 * State:
 *   - writes: {TMPDIR}/claude-hooks/ci-recovery.json
 *   - writes: .claude/logs/metrics/ci-recovery-metrics.log
 *
 * Remarks:
 *   - 記録型フック（ブロックしない、メトリクス記録）
 *   - PostToolUse:Bashで発火（gh pr checks結果を分析）
 *   - ブランチ別に失敗タイムスタンプを保持
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { METRICS_LOG_DIR } from "../lib/constants";
import { getCurrentBranch } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { createHookContext, getToolResult, parseHookInput } from "../lib/session";

const HOOK_NAME = "ci-recovery-tracker";

// Tracking file location (use TMPDIR for sandbox compatibility)
const TRACKING_DIR = join(tmpdir(), "claude-hooks");

/**
 * Get session-specific tracking file path.
 * Using session ID prevents race conditions between concurrent sessions.
 * Note: This means CI recovery tracking is per-session. If a session ends
 * before CI recovery, the tracking is lost, which is acceptable.
 */
function getCiTrackingFile(sessionId: string): string {
  // Sanitize sessionId to prevent path traversal attacks
  const safeSessionId = basename(sessionId);
  const shortId = safeSessionId.slice(0, 8);
  return join(TRACKING_DIR, `ci-recovery-${shortId}.json`);
}

// Get metrics log directory
function getMetricsLogDir(): string {
  const envDir = process.env.CLAUDE_PROJECT_DIR;
  if (envDir) {
    return join(envDir, METRICS_LOG_DIR);
  }
  return join(process.cwd(), METRICS_LOG_DIR);
}

// Patterns to detect CI commands
const CI_CHECK_PATTERNS = [/gh pr checks/i, /gh run view/i, /gh run watch/i, /gh run list/i];

// Patterns to detect CI status in output
const CI_FAILURE_PATTERNS = [/FAILURE/i, /fail/i, /failing/i, /❌/, /X\s+\w+/];
const CI_SUCCESS_PATTERNS = [/SUCCESS/i, /pass/i, /✓/, /✅/, /All checks have passed/i];

interface TrackingData {
  failure_time: string | null;
  branch: string | null;
  pr_number: string | null;
}

/**
 * Load CI tracking data from session-specific file.
 */
function loadCiTracking(trackingFile: string): TrackingData {
  if (existsSync(trackingFile)) {
    try {
      const content = readFileSync(trackingFile, "utf-8");
      return JSON.parse(content);
    } catch {
      // Best effort - corrupted tracking data is ignored
    }
  }
  return { failure_time: null, branch: null, pr_number: null };
}

/**
 * Save CI tracking data to session-specific file.
 */
function saveCiTracking(trackingFile: string, data: TrackingData): void {
  mkdirSync(TRACKING_DIR, { recursive: true });
  writeFileSync(trackingFile, JSON.stringify(data, null, 2));
}

/**
 * Log CI recovery event for later analysis.
 */
function logCiRecovery(
  failureTime: string,
  recoveryTime: string,
  recoverySeconds: number,
  branch: string | null,
  prNumber: string | null,
  sessionId: string,
): void {
  try {
    const logDir = getMetricsLogDir();
    mkdirSync(logDir, { recursive: true });

    const entry = {
      timestamp: recoveryTime,
      session_id: sessionId,
      type: "ci_recovery",
      failure_time: failureTime,
      recovery_time: recoveryTime,
      recovery_seconds: recoverySeconds,
      branch,
      pr_number: prNumber,
    };

    appendFileSync(join(logDir, "ci-recovery-metrics.log"), `${JSON.stringify(entry)}\n`);
  } catch {
    // ログ書き込み失敗はサイレントに無視
  }
}

/**
 * Log CI failure event.
 */
function logCiFailure(branch: string | null, prNumber: string | null, sessionId: string): void {
  try {
    const logDir = getMetricsLogDir();
    mkdirSync(logDir, { recursive: true });

    const entry = {
      timestamp: new Date().toISOString(),
      session_id: sessionId,
      type: "ci_failure",
      branch,
      pr_number: prNumber,
    };

    appendFileSync(join(logDir, "ci-recovery-metrics.log"), `${JSON.stringify(entry)}\n`);
  } catch {
    // ログ書き込み失敗はサイレントに無視
  }
}

/**
 * Check if the command is a CI status check.
 */
function isCiCheckCommand(command: string): boolean {
  return CI_CHECK_PATTERNS.some((pattern) => pattern.test(command));
}

/**
 * Extract PR number or run ID from CI command if present.
 */
function extractCiTargetNumber(command: string): string | null {
  const match = command.match(/(?:pr\s+(?:checks|view)|run\s+(?:view|watch))\s+(\d+)/);
  return match?.[1] ?? null;
}

/**
 * Detect CI status from output.
 */
function detectCiStatus(output: string): "failure" | "success" | null {
  // Check for failure patterns first (they're more definitive)
  for (const pattern of CI_FAILURE_PATTERNS) {
    if (pattern.test(output)) {
      return "failure";
    }
  }

  // Check for success patterns
  for (const pattern of CI_SUCCESS_PATTERNS) {
    if (pattern.test(output)) {
      return "success";
    }
  }

  return null;
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    const ctx = createHookContext(inputData);
    sessionId = ctx.sessionId;
    const toolInput = (inputData.tool_input ?? {}) as Record<string, unknown>;
    const rawResult = getToolResult(inputData);
    const toolResult =
      typeof rawResult === "object" && rawResult ? (rawResult as Record<string, unknown>) : {};

    const command = (toolInput.command as string) ?? "";
    const stdout = (toolResult.stdout as string) ?? "";
    const stderr = (toolResult.stderr as string) ?? "";
    const output = `${stdout}\n${stderr}`;

    // Only process CI check commands
    if (!isCiCheckCommand(command)) {
      await logHookExecution(HOOK_NAME, "skip", "not a CI check command", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // セッションIDがない場合はCI追跡をスキップ
    if (!sessionId) {
      console.log(JSON.stringify(result));
      return;
    }

    const now = new Date();
    const branch = await getCurrentBranch();
    const prNumber = extractCiTargetNumber(command);
    const ciStatus = detectCiStatus(output);
    const trackingFile = getCiTrackingFile(sessionId);

    const tracking = loadCiTracking(trackingFile);

    if (ciStatus === "failure") {
      // Record failure if not already tracking one, or if branch changed
      if (tracking.failure_time === null || tracking.branch !== branch) {
        tracking.failure_time = now.toISOString();
        tracking.branch = branch;
        tracking.pr_number = prNumber;
        saveCiTracking(trackingFile, tracking);
        logCiFailure(branch, prNumber, sessionId);
      }
    } else if (ciStatus === "success") {
      // Calculate recovery time if we were tracking a failure
      if (tracking.failure_time !== null && tracking.branch === branch) {
        const failureTime = new Date(tracking.failure_time);
        const recoverySeconds = (now.getTime() - failureTime.getTime()) / 1000;

        logCiRecovery(
          tracking.failure_time,
          now.toISOString(),
          recoverySeconds,
          tracking.branch,
          tracking.pr_number,
          sessionId,
        );

        // Format message
        let timeStr: string;
        if (recoverySeconds < 60) {
          timeStr = `${Math.round(recoverySeconds)}秒`;
        } else if (recoverySeconds < 3600) {
          timeStr = `${(recoverySeconds / 60).toFixed(1)}分`;
        } else {
          timeStr = `${(recoverySeconds / 3600).toFixed(1)}時間`;
        }

        result.systemMessage = `📊 CI復旧時間: ${timeStr}`;

        // Clear tracking
        saveCiTracking(trackingFile, { failure_time: null, branch: null, pr_number: null });
      }
    }
  } catch {
    // フック実行の失敗でClaude Codeをブロックしない
  }

  await logHookExecution(HOOK_NAME, "approve", undefined, { type: "ci_tracked" }, { sessionId });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
