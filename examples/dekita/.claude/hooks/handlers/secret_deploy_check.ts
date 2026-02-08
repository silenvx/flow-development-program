#!/usr/bin/env bun
/**
 * セッション終了時に未デプロイのフロントエンドシークレットがないか確認。
 *
 * Why:
 *   フロントエンドシークレット（広告ID等）を更新した場合、
 *   デプロイしないと本番に反映されない。セッション終了前に確認する。
 *
 * What:
 *   - セッション終了時（Stopフック）に発火
 *   - secret-deploy-trigger.pyが作成した追跡ファイルを確認
 *   - シークレット更新後にCIが成功していなければブロック
 *   - デプロイ済みなら追跡ファイルをクリア
 *
 * State:
 *   - reads: /tmp/claude-secret-updates.json
 *
 * Remarks:
 *   - ブロック型（未デプロイのシークレットがあればブロック）
 *   - secret-deploy-trigger.pyと連携
 *   - CI成功確認はGitHub API経由
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { execSync } from "node:child_process";
import { existsSync, readFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CONTINUATION_HINT, TIMEOUT_HEAVY } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "secret-deploy-check";

// Tracking file location (shared with secret-deploy-trigger.py)
const TRACKING_FILE = join(tmpdir(), "claude-secret-updates.json");

// Production URL for verification
const PRODUCTION_URL = "https://dekita.app";

export interface TrackingData {
  secrets: string[];
  updated_at: string | null;
}

/**
 * Load tracking data.
 */
export function loadTrackingData(): TrackingData {
  if (existsSync(TRACKING_FILE)) {
    try {
      return JSON.parse(readFileSync(TRACKING_FILE, "utf-8"));
    } catch {
      // Ignore corrupted/invalid JSON - start fresh
    }
  }
  return { secrets: [], updated_at: null };
}

/**
 * Clear tracking data after successful deploy.
 */
function clearTrackingData(): void {
  try {
    if (existsSync(TRACKING_FILE)) {
      unlinkSync(TRACKING_FILE);
    }
  } catch {
    // Silently ignore deletion errors
  }
}

/**
 * Parse ISO timestamp to Date.
 * Returns null for invalid timestamps (including "Invalid Date").
 */
export function parseIsoTimestamp(timestamp: string): Date | null {
  try {
    const date = new Date(timestamp);
    // new Date() returns "Invalid Date" for invalid strings instead of throwing
    if (Number.isNaN(date.getTime())) {
      return null;
    }
    return date;
  } catch {
    return null;
  }
}

/**
 * Check if a successful deploy was triggered AFTER the secret update.
 */
function checkDeployAfterUpdate(updatedAt: string | null): boolean {
  if (!updatedAt) {
    return false;
  }

  const updateTime = parseIsoTimestamp(updatedAt);
  if (!updateTime) {
    return false;
  }

  try {
    // Get recent CI runs on main branch
    const result = execSync(
      "gh run list --workflow ci.yml --branch main --limit 10 --json createdAt,conclusion",
      { encoding: "utf-8", timeout: TIMEOUT_HEAVY * 1000 },
    );

    const runs = JSON.parse(result.trim());

    // Check if ANY successful run occurred after the secret update
    for (const run of runs) {
      const createdAt = run.createdAt ?? "";
      const conclusion = run.conclusion ?? "";

      if (createdAt && conclusion === "success") {
        const runTime = parseIsoTimestamp(createdAt);
        if (runTime && runTime > updateTime) {
          return true;
        }
      }
    }
  } catch {
    // gh command failed or JSON parse error - assume not deployed
  }

  return false;
}

/**
 * Format block reason message.
 */
export function formatBlockReason(secrets: string[]): string {
  const secretsList = secrets.join(", ");

  return `フロントエンドシークレットが更新されましたが、デプロイされていません。\n\n更新されたシークレット: ${secretsList}\n\nデプロイを実行してください:\n\`\`\`bash\ngh workflow run ci.yml --ref main\nbun run .claude/scripts/ci_monitor_ts/main.ts main --session-id <SESSION_ID>\n\`\`\`\n\n本番確認URL: ${PRODUCTION_URL}${CONTINUATION_HINT}`;
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    // Read context from stdin
    const hookInput = await parseHookInput();
    sessionId = hookInput.session_id;

    // Prevent infinite loops
    if (hookInput.stop_hook_active) {
      await logHookExecution(HOOK_NAME, "approve", "stop_hook_active", undefined, { sessionId });
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Load tracking data
    const data = loadTrackingData();
    const secrets = data.secrets ?? [];
    const updatedAt = data.updated_at;

    // No secrets updated, nothing to check
    if (secrets.length === 0) {
      await logHookExecution(HOOK_NAME, "approve", "no secrets to check", undefined, { sessionId });
      console.log(JSON.stringify({ continue: true }));
      return;
    }

    // Check if deploy was performed AFTER the secret update
    if (checkDeployAfterUpdate(updatedAt)) {
      clearTrackingData();
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "secrets deployed",
        { secrets, deployed: true },
        { sessionId },
      );
      console.log(
        JSON.stringify({
          continue: true,
          systemMessage: `✅ フロントエンドシークレットがデプロイされました: ${secrets.join(", ")}`,
        }),
      );
      return;
    }

    // Secrets updated but not deployed - block
    const reason = formatBlockReason(secrets);
    await logHookExecution(HOOK_NAME, "block", reason, { secrets }, { sessionId });
    console.log(JSON.stringify({ continue: false, reason }));
  } catch (error) {
    // On error, don't block
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", `error: ${formatError(error)}`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify({ continue: true }));
  }
}

if (import.meta.main) {
  main().catch(console.error);
}
