#!/usr/bin/env bun
/**
 * 連続Bash失敗を検知し、シェル破損時に引き継ぎプロンプト生成を提案する。
 *
 * Why:
 *   worktreeの自己削除等でカレントディレクトリが消えると、シェルが破損状態になり
 *   全てのコマンドが失敗し続ける。この状態を早期検知して回復策を提示する。
 *
 * What:
 *   - Bash失敗を連続カウント
 *   - シェル破損パターン（"No such file or directory"等）を検出
 *   - 閾値（3回連続）超過で警告と回復オプションを提示
 *
 * State:
 *   - writes: /tmp/claude-hooks/bash-failures.json
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、回復策を提示）
 *   - PostToolUse:Bashで発火
 *   - シェル破損パターンはre.IGNORECASEでマッチング
 *
 * Changelog:
 *   - silenvx/dekita#237: シェル破損時の自動回復メカニズム
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { logHookExecution } from "../lib/logging";
import { getToolResultAsObject, parseHookInput } from "../lib/session";

const HOOK_NAME = "bash-failure-tracker";

// Consecutive failure threshold for warning
const FAILURE_THRESHOLD = 3;

// Error patterns that indicate potential shell corruption
const SHELL_CORRUPTION_PATTERNS = [
  /No such file or directory/i,
  /Unable to read current working directory/i,
  /cannot access/i,
  /fatal: Unable to read/i,
];

// Tracking file location (use TMPDIR for sandbox compatibility)
const TRACKING_DIR = join(tmpdir(), "claude-hooks");
const TRACKING_FILE = join(TRACKING_DIR, "bash-failures.json");

interface TrackingData {
  consecutive_failures: number;
  last_errors: string[];
  updated_at: string | null;
}

/**
 * Load existing tracking data.
 */
function loadTrackingData(): TrackingData {
  if (existsSync(TRACKING_FILE)) {
    try {
      const content = readFileSync(TRACKING_FILE, "utf-8");
      return JSON.parse(content) as TrackingData;
    } catch {
      // Best effort - corrupted tracking data is ignored
    }
  }
  return { consecutive_failures: 0, last_errors: [], updated_at: null };
}

/**
 * Save tracking data.
 */
function saveTrackingData(data: TrackingData): void {
  mkdirSync(TRACKING_DIR, { recursive: true });
  writeFileSync(TRACKING_FILE, JSON.stringify(data, null, 2));
}

/**
 * Check if the output indicates potential shell corruption.
 */
export function isShellCorruptionError(output: string): boolean {
  for (const pattern of SHELL_CORRUPTION_PATTERNS) {
    if (pattern.test(output)) {
      return true;
    }
  }
  return false;
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;
    const toolResult = getToolResultAsObject(inputData);

    // exit_code不在時はundefinedとして扱い、失敗として検知する（安全側に倒す）
    const exitCode = typeof toolResult.exit_code === "number" ? toolResult.exit_code : undefined;
    const stdout = typeof toolResult.stdout === "string" ? toolResult.stdout : "";
    const stderr = typeof toolResult.stderr === "string" ? toolResult.stderr : "";
    const output = `${stdout}\n${stderr}`;

    const data = loadTrackingData();

    // exit_code不在またはexit_code !== 0 を失敗として扱う
    if (exitCode === undefined || exitCode !== 0) {
      // Bash command failed
      data.consecutive_failures += 1;
      const errorSummary = stderr ? stderr.slice(0, 200) : stdout.slice(0, 200);
      data.last_errors.push(errorSummary);
      // Keep only last 5 errors
      data.last_errors = data.last_errors.slice(-5);
      data.updated_at = new Date().toISOString();
      saveTrackingData(data);

      // Check if we've hit the threshold with shell corruption patterns
      if (data.consecutive_failures >= FAILURE_THRESHOLD) {
        if (isShellCorruptionError(output)) {
          result.systemMessage = `⚠️ 連続 ${data.consecutive_failures} 回のBash失敗を検知。\nシェル破損の可能性があります。\n【対応オプション】\n1. 引き継ぎプロンプトを生成して別セッションで継続\n2. メインリポジトリに移動してから再実行`;
        } else {
          result.systemMessage = `⚠️ 連続 ${data.consecutive_failures} 回のBash失敗。\n回復不能な場合は引き継ぎプロンプトの生成を検討してください。`;
        }
      }
    } else {
      // Bash command succeeded - reset counter
      if (data.consecutive_failures > 0) {
        data.consecutive_failures = 0;
        data.last_errors = [];
        data.updated_at = new Date().toISOString();
        saveTrackingData(data);
      }
    }
  } catch {
    // フック実行の失敗でClaude Codeをブロックしない
    // 追跡失敗は致命的ではなく、次の実行で回復可能
  }

  logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
