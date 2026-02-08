#!/usr/bin/env bun
/**
 * セッション開始時にgit設定の整合性を確認し、問題があれば自動修正する。
 *
 * Why:
 *   Worktree操作後にgit configのcore.bare=trueになることがあり、
 *   gitコマンドが`fatal: this operation must be run in a work tree`で
 *   失敗する。この既知の問題を自動検出・修正する。
 *
 * What:
 *   - core.bareの値を確認
 *   - trueになっている場合は自動的にfalseに修正
 *   - 修正した場合は警告を出力
 *
 * Remarks:
 *   - ブロックはしない（自動修正のみ）
 *   - git設定のチェックは他のフックにない（重複なし）
 *
 * Changelog:
 *   - silenvx/dekita#975: フック追加
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { spawnSync } from "node:child_process";
import { TIMEOUT_LIGHT } from "../lib/constants";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "git-config-check";

/**
 * Get the value of core.bare.
 */
export function getCoreBare(): string | null {
  try {
    const result = spawnSync("git", ["config", "core.bare"], {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT,
    });
    if (result.status === 0 && result.stdout) {
      return result.stdout.trim().toLowerCase();
    }
  } catch {
    // Best effort - git command may fail
  }
  return null;
}

/**
 * Fix core.bare to false.
 */
function fixCoreBare(): boolean {
  try {
    const result = spawnSync("git", ["config", "core.bare", "false"], {
      encoding: "utf-8",
      timeout: TIMEOUT_LIGHT,
    });
    return result.status === 0;
  } catch {
    // Best effort - git config fix may fail
  }
  return false;
}

async function main(): Promise<void> {
  // Parse hook input to get session ID
  const data = await parseHookInput();
  const sessionId = data.session_id;

  const bareValue = getCoreBare();

  if (bareValue === "true") {
    // Attempt auto-fix
    if (fixCoreBare()) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "Auto-fixed core.bare=true to false",
        undefined,
        {
          sessionId,
        },
      );
      console.log(`⚠️ [${HOOK_NAME}] git設定の問題を自動修正しました

**修正内容:**
- \`core.bare=true\` → \`core.bare=false\`

**原因:**
worktree操作後にgit設定が壊れることがある既知の問題です。
詳細: Issue #975

**影響:**
この問題により、gitコマンドが \`fatal: this operation must be run in a work tree\` で
失敗する可能性がありました。自動修正により正常に動作するようになりました。
`);
    } else {
      await logHookExecution(HOOK_NAME, "approve", "Failed to auto-fix core.bare=true", undefined, {
        sessionId,
      });
      console.log(`⚠️ [${HOOK_NAME}] git設定に問題がありますが、自動修正に失敗しました

**問題:**
- \`core.bare=true\` が設定されています

**手動修正方法:**
\`\`\`bash
git config core.bare false
\`\`\`

詳細: Issue #975
`);
    }
  }
}

if (import.meta.main) {
  main();
}
