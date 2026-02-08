#!/usr/bin/env bun
/**
 * 回避行動を検知してセッションログに記録する。
 *
 * Why:
 *   フックのブロックやルールを回避する行動は、同じパターンが繰り返される原因となる。
 *   回避行動を検知して記録することで、振り返り時に分析・対策を行える。
 *
 * What:
 *   - PostToolUse:Bashで発火
 *   - 連続失敗→成功パターンを検出（オプション変更での回避）
 *   - 類似コマンドでのツール切り替えを検出
 *   - 回避パターンをセッションログに記録
 *
 * State:
 *   - reads/writes: /tmp/claude-hooks/bypass-tracker-{session}.json
 *   - writes: .claude/logs/metrics/bypass-patterns-{session}.jsonl
 *
 * Remarks:
 *   - 非ブロック型（記録のみ、振り返りで分析）
 *   - 正当な回避（フック指示に従った代替アクション）は除外
 *
 * Changelog:
 *   - silenvx/dekita#3009: 初期実装
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { METRICS_LOG_DIR, SESSION_DIR } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logToSessionFile } from "../lib/logging";
import { makeApproveResult } from "../lib/results";
import { getToolResult, isSafeSessionId, parseHookInput } from "../lib/session";

const HOOK_NAME = "bypass-detector";

// Tracking file location (use SESSION_DIR for consistency with other hooks)
const TRACKING_DIR = SESSION_DIR;

// Time window to consider commands as related (seconds)
const RELATED_COMMAND_WINDOW_SECONDS = 120;

// Tool manager patterns for detecting tool switching
const TOOL_MANAGERS = [
  "uv",
  "uvx",
  "npm",
  "npx",
  "pnpm",
  "yarn",
  "pip",
  "pipx",
  "cargo",
  "go",
  "brew",
  "bun",
  "bunx",
];

// Command similarity patterns
// Note: "uvx" is added to run family because it acts as an alias for "uv tool run"
const COMMAND_FAMILIES: Record<string, RegExp> = {
  install: /\b(install|add|i)\b/i,
  run: /\b(run|exec|x|uvx)\b/i,
  test: /\b(test|check)\b/i,
  build: /\b(build|compile)\b/i,
  format: /\b(format|fmt)\b/i,
  lint: /\b(lint|check)\b/i,
};

export interface CommandRecord {
  timestamp: string;
  command: string;
  exitCode: number;
  toolManager: string | null;
  commandFamily: string | null;
}

interface TrackingData {
  recentCommands: CommandRecord[];
  updatedAt: string | null;
}

export interface BypassPattern {
  type: "tool_switch" | "option_change";
  description: string;
  failedCommand: string;
  successCommand: string;
  toolManagerFrom?: string;
  toolManagerTo?: string;
}

/**
 * Get tracking file path for a session.
 */
function getTrackingFile(sessionId: string): string {
  // Sanitize session ID for file path safety
  const safeSessionId = sessionId.replace(/[^a-zA-Z0-9-]/g, "_");
  return join(TRACKING_DIR, `bypass-tracker-${safeSessionId}.json`);
}

/**
 * Load existing tracking data for a session.
 */
function loadTrackingData(sessionId: string): TrackingData {
  const trackingFile = getTrackingFile(sessionId);
  if (existsSync(trackingFile)) {
    try {
      const content = readFileSync(trackingFile, "utf-8");
      return JSON.parse(content) as TrackingData;
    } catch {
      // Best effort - corrupted tracking data is ignored
    }
  }
  return { recentCommands: [], updatedAt: null };
}

/**
 * Save tracking data for a session.
 */
function saveTrackingData(sessionId: string, data: TrackingData): void {
  mkdirSync(TRACKING_DIR, { recursive: true });
  const trackingFile = getTrackingFile(sessionId);
  writeFileSync(trackingFile, JSON.stringify(data, null, 2));
}

/**
 * Extract tool manager from command.
 * Only matches at the start of the command (possibly after env vars like KEY=VAL).
 */
export function extractToolManager(command: string): string | null {
  for (const manager of TOOL_MANAGERS) {
    // Match at start of command or after env var assignments (KEY=VAL)
    const pattern = new RegExp(`^(?:[A-Z0-9_]+=[^\\s]+\\s+)*${manager}\\b`, "i");
    if (pattern.test(command)) {
      return manager;
    }
  }
  return null;
}

/**
 * Extract command family (install, run, test, etc.).
 */
export function extractCommandFamily(command: string): string | null {
  for (const [family, pattern] of Object.entries(COMMAND_FAMILIES)) {
    if (pattern.test(command)) {
      return family;
    }
  }
  return null;
}

/**
 * Check if two commands are related (same tool family, different tool manager).
 */
export function areCommandsRelated(cmd1: CommandRecord, cmd2: CommandRecord): boolean {
  // Same command family
  if (cmd1.commandFamily && cmd1.commandFamily === cmd2.commandFamily) {
    return true;
  }

  // Similar packages/arguments (simple heuristic)
  // Using slice(1) to skip only the tool, since some tools like uvx don't have a subcommand
  const args1 = cmd1.command.split(/\s+/).slice(1);
  const args2 = cmd2.command.split(/\s+/).slice(1);
  const commonArgs = args1.filter((arg) => args2.includes(arg) && !arg.startsWith("-"));
  return commonArgs.length > 0;
}

/**
 * Detect bypass patterns from command history.
 */
export function detectBypassPatterns(
  currentCommand: CommandRecord,
  recentCommands: CommandRecord[],
): BypassPattern[] {
  const patterns: BypassPattern[] = [];

  // Only analyze if current command succeeded
  if (currentCommand.exitCode !== 0) {
    return patterns;
  }

  const now = new Date(currentCommand.timestamp).getTime();

  for (const prevCmd of recentCommands) {
    // Only consider recent failures
    if (prevCmd.exitCode === 0) continue;

    const prevTime = new Date(prevCmd.timestamp).getTime();
    const elapsedSeconds = (now - prevTime) / 1000;

    if (elapsedSeconds > RELATED_COMMAND_WINDOW_SECONDS) continue;

    // Pattern 1: Tool manager switch (e.g., uv run → uvx)
    if (
      prevCmd.toolManager &&
      currentCommand.toolManager &&
      prevCmd.toolManager !== currentCommand.toolManager &&
      areCommandsRelated(prevCmd, currentCommand)
    ) {
      patterns.push({
        type: "tool_switch",
        description: `Tool switched from ${prevCmd.toolManager} to ${currentCommand.toolManager}`,
        failedCommand: prevCmd.command.slice(0, 100),
        successCommand: currentCommand.command.slice(0, 100),
        toolManagerFrom: prevCmd.toolManager,
        toolManagerTo: currentCommand.toolManager,
      });
    }

    // Pattern 2: Option change on similar command
    // Require at least one of toolManager or commandFamily to be non-null
    // to avoid false positives from unrelated commands (like ls, cat, etc.)
    if (
      (prevCmd.toolManager || prevCmd.commandFamily) &&
      prevCmd.toolManager === currentCommand.toolManager &&
      prevCmd.commandFamily === currentCommand.commandFamily &&
      prevCmd.command !== currentCommand.command
    ) {
      // If no tool manager is detected, ensure the base command is the same
      // to avoid matching unrelated tools that share a family (e.g. "node test.js" and "python test.py")
      if (!prevCmd.toolManager) {
        const prevBase = prevCmd.command.trim().split(/\s+/)[0];
        const currBase = currentCommand.command.trim().split(/\s+/)[0];
        if (prevBase !== currBase) continue;
      }

      patterns.push({
        type: "option_change",
        description: "Same tool/family but different options succeeded after failure",
        failedCommand: prevCmd.command.slice(0, 100),
        successCommand: currentCommand.command.slice(0, 100),
      });
    }
  }

  return patterns;
}

/**
 * Get project directory.
 */
function getProjectDir(): string {
  return process.env.CLAUDE_PROJECT_DIR ?? process.cwd();
}

async function main(): Promise<void> {
  try {
    const data = await parseHookInput();

    // Only process Bash PostToolUse
    if (data.tool_name !== "Bash") {
      console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
      return;
    }

    const rawResult = getToolResult(data);
    const toolResult =
      rawResult && typeof rawResult === "object" && !Array.isArray(rawResult)
        ? (rawResult as Record<string, unknown>)
        : null;
    const toolInput = data.tool_input || {};
    const command = (toolInput as { command?: string }).command || "";
    const sessionId = data.session_id || process.env.CLAUDE_SESSION_ID || "unknown";

    // Validate session ID to prevent path traversal attacks
    if (!isSafeSessionId(sessionId)) {
      console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
      return;
    }

    // Get exit code
    const rawExitCode = toolResult?.exit_code;
    const exitCode = typeof rawExitCode === "number" ? rawExitCode : command ? 0 : -1;

    // Extract command metadata
    const toolManager = extractToolManager(command);
    const commandFamily = extractCommandFamily(command);

    // Create current command record
    const currentCommand: CommandRecord = {
      timestamp: new Date().toISOString(),
      command: command.slice(0, 500), // Limit command length
      exitCode,
      toolManager,
      commandFamily,
    };

    // Load tracking data
    const trackingData = loadTrackingData(sessionId);

    // Detect bypass patterns
    const bypassPatterns = detectBypassPatterns(currentCommand, trackingData.recentCommands);

    // Log detected patterns
    if (bypassPatterns.length > 0) {
      const projectDir = getProjectDir();
      const metricsDir = join(projectDir, METRICS_LOG_DIR);

      for (const pattern of bypassPatterns) {
        await logToSessionFile(metricsDir, "bypass-patterns", sessionId, {
          type: "bypass_detected",
          pattern_type: pattern.type,
          description: pattern.description,
          failed_command: pattern.failedCommand,
          success_command: pattern.successCommand,
          tool_manager_from: pattern.toolManagerFrom,
          tool_manager_to: pattern.toolManagerTo,
        });
      }
    }

    // Update tracking data
    trackingData.recentCommands.push(currentCommand);
    // Keep only recent commands (last 20)
    trackingData.recentCommands = trackingData.recentCommands.slice(-20);
    trackingData.updatedAt = new Date().toISOString();
    saveTrackingData(sessionId, trackingData);

    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
  } catch (e) {
    // Fail-open: don't block on hook errors
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(e)}`);
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
  }
}

// Only run when executed directly, not when imported for testing
if (import.meta.main) {
  main();
}
