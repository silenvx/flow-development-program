#!/usr/bin/env bun
/**
 * VITE_プレフィックスのシークレット更新を記録。
 *
 * Why:
 *   フロントエンドのシークレット（VITE_*）を更新した場合、
 *   デプロイしないと本番に反映されない。Stopフックで確認を促す。
 *
 * What:
 *   - gh secret set VITE_* コマンドを検出
 *   - 成功した場合、シークレット名を追跡ファイルに記録
 *   - Stopフックでデプロイ確認を促す
 *
 * When:
 *   - PostToolUse（Bashコマンド実行後）
 *
 * State:
 *   - writes: /tmp/claude-secret-updates.json
 *
 * Remarks:
 *   - 非ブロック型（記録のみ）
 *   - VITE_プレフィックスのみ対象（フロントエンドシークレット）
 *   - Python版: secret_deploy_trigger.py
 *
 * Changelog:
 *   - silenvx/dekita#2986: TypeScript版に移植
 */

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { getExitCode, getToolResult } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "secret-deploy-trigger";

// Only track frontend secrets (VITE_ prefix)
export const FRONTEND_SECRET_PREFIX = "VITE_";

// Tracking file location
const TRACKING_FILE = join(tmpdir(), "claude-secret-updates.json");

interface TrackingData {
  secrets: string[];
  updated_at: string | null;
}

/**
 * Check if command is a gh secret set command.
 */
export function isGhSecretSetCommand(command: string): boolean {
  return command.includes("gh secret set");
}

/**
 * Extract secret name from gh secret set command.
 * Returns null if not found.
 */
export function extractSecretName(command: string): string | null {
  const match = command.match(/gh secret set\s+(?:--\S+\s+)*([A-Z_][A-Z0-9_]*)/);
  return match ? match[1] : null;
}

/**
 * Check if a secret name is a frontend secret (VITE_ prefix).
 */
export function isFrontendSecret(secretName: string): boolean {
  return secretName.startsWith(FRONTEND_SECRET_PREFIX);
}

/**
 * Load existing tracking data.
 */
export function loadTrackingData(): TrackingData {
  if (existsSync(TRACKING_FILE)) {
    try {
      const content = readFileSync(TRACKING_FILE, "utf-8");
      return JSON.parse(content) as TrackingData;
    } catch {
      // Ignore corrupted/invalid JSON - start fresh
    }
  }
  return { secrets: [], updated_at: null };
}

/**
 * Save tracking data.
 */
export function saveTrackingData(data: TrackingData): void {
  writeFileSync(TRACKING_FILE, JSON.stringify(data, null, 2));
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;
    const toolInput = (inputData.tool_input as Record<string, unknown>) ?? {};
    const toolResult = getToolResult(inputData) ?? {};

    const command = (toolInput.command as string) ?? "";
    // Default to 0 (success) if exit_code not provided
    // Issue #1470: Previous default of -1 caused trigger to be skipped for successful commands
    const exitCode = getExitCode(toolResult, 0);

    // Only process successful gh secret set commands
    if (!isGhSecretSetCommand(command) || exitCode !== 0) {
      await logHookExecution(HOOK_NAME, "approve", "not gh secret set or failed", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Extract secret name from command
    const secretName = extractSecretName(command);
    if (!secretName) {
      await logHookExecution(HOOK_NAME, "approve", "secret name not found in command", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Only track frontend secrets
    if (!isFrontendSecret(secretName)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `not a frontend secret: ${secretName}`,
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Record the secret update with timestamp
    const data = loadTrackingData();
    if (!data.secrets.includes(secretName)) {
      data.secrets.push(secretName);
    }
    // Always update timestamp to latest secret update
    data.updated_at = new Date().toISOString();
    saveTrackingData(data);

    // Brief notification (not blocking)
    result.systemMessage = `📝 フロントエンドシークレット '${secretName}' を記録しました。作業完了時にデプロイを確認します。`;

    await logHookExecution(HOOK_NAME, "approve", `recorded: ${secretName}`, undefined, {
      sessionId,
    });
  } catch {
    // Best effort - tracking update may fail
    await logHookExecution(HOOK_NAME, "approve", "error", undefined, { sessionId });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
