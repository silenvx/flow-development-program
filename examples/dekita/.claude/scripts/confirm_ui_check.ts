#!/usr/bin/env bun
/**
 * UI確認完了を記録しコミットをアンブロックする。
 *
 * Why:
 *   UI変更時のブラウザ確認完了を記録し、
 *   commit-msg-checkerのブロックを解除するため。
 *
 * What:
 *   - main(): 確認完了マーカーファイルを作成
 *
 * State:
 *   - writes: .claude/logs/markers/ui-check-{branch}.done
 *
 * Remarks:
 *   - feature branchでのみ実行可能
 *   - main/masterでは警告して終了
 *
 * Changelog:
 *   - silenvx/dekita#1050: UI確認完了記録機能を追加
 *   - silenvx/dekita#3636: TypeScriptに移植
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { MARKERS_LOG_DIR } from "../hooks/lib/common";
import { getCurrentBranch } from "../hooks/lib/git";
import { sanitizeBranchName } from "../hooks/lib/strings";

/**
 * Check if a branch is allowed for UI confirmation.
 * Returns false for main/master branches.
 */
export function isAllowedBranch(branch: string): boolean {
  return branch !== "main" && branch !== "master";
}

/**
 * Get the marker file path for a branch.
 */
export function getMarkerPath(branch: string): string {
  const safeBranch = sanitizeBranchName(branch);
  return `${MARKERS_LOG_DIR}/ui-check-${safeBranch}.done`;
}

async function main(): Promise<void> {
  const branch = await getCurrentBranch();

  if (branch === null) {
    console.error("Error: Could not determine current branch.");
    console.error("Make sure you are in a git repository.");
    process.exit(1);
  }

  if (!isAllowedBranch(branch)) {
    console.error(`Warning: Currently on ${branch} branch.`);
    console.error("UI verification confirmation is only needed for feature branches.");
    process.exit(1);
  }

  // Create log directory if needed
  mkdirSync(MARKERS_LOG_DIR, { recursive: true });

  // Create confirmation file
  const logFile = getMarkerPath(branch);
  writeFileSync(logFile, branch);

  console.log(`UI verification confirmed for branch: ${branch}`);
  console.log(`Confirmation file: ${logFile}`);
  console.log();
  console.log("You can now commit your locale file changes.");
}

// Only run main when executed directly (not when imported for testing)
if (import.meta.main) {
  main().catch((error) => {
    console.error("Error:", error);
    process.exit(1);
  });
}
