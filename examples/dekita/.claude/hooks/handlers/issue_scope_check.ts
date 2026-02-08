#!/usr/bin/env bun
/**
 * Issue編集時のスコープ確認を強制する。
 *
 * Why:
 *   1つのIssueに異なるタスクを追加すると追跡性が低下する。
 *   1Issue1タスクの原則を強制することで、Issueの管理性を向上させる。
 *
 * What:
 *   - gh issue edit --bodyコマンドを検出
 *   - チェックボックスのみの変更は許可（進捗更新のため）
 *   - 内容追加時はスコープ確認を強制しブロック
 *   - SKIP_ISSUE_SCOPE_CHECK環境変数でバイパス可能
 *
 * Remarks:
 *   - ブロック型フック（内容追加時はブロック）
 *   - PreToolUse:Bashで発火（gh issue editコマンド）
 *   - issue-multi-problem-check.pyはIssue作成時のみ対象（責務分離）
 *   - forkセッションではSKIP環境変数を許可しない
 *   - Python版: issue_scope_check.py
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2423: チェックボックス更新を許可する機能を追加
 *   - silenvx/dekita#2431: SKIP環境変数サポートと拒否メッセージの改善
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { execSync } from "node:child_process";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { isForkSession, parseHookInput } from "../lib/session";
import { extractInlineSkipEnv, isSkipEnvEnabled } from "../lib/strings";

const HOOK_NAME = "issue-scope-check";
const SKIP_ENV_NAME = "SKIP_ISSUE_SCOPE_CHECK";

/**
 * Extract Issue number from command.
 */
export function extractIssueNumber(command: string): string | null {
  // gh issue edit 123 --body "..." or gh issue edit #123 -b "..."
  const match = command.match(/gh\s+issue\s+edit\s+#?(\d+)/);
  if (match) {
    return match[1];
  }
  return null;
}

/**
 * Get current Issue body via GitHub API.
 */
function getCurrentIssueBody(issueNumber: string): string | null {
  try {
    const result = execSync(`gh issue view ${issueNumber} --json body --jq ".body"`, {
      encoding: "utf-8",
      timeout: 10000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return result.trim();
  } catch {
    // gh CLI not installed or timeout
    return null;
  }
}

/**
 * Extract --body option value from command.
 */
export function extractBodyFromCommand(command: string): string | null {
  // --body "$(cat <<'EOF' ... EOF)" pattern (check first)
  const heredocMatch = command.match(
    /--body\s+"\$\(\s*cat\s+<<['"]?EOF['"]?\s*\n(?<body>.*?)\nEOF(?:\)"|"\)|\)|"|\s|$)/s,
  );
  if (heredocMatch?.groups?.body) {
    return heredocMatch.groups.body;
  }

  // --body "value" or --body 'value' pattern
  const quoteMatch = command.match(/--body\s+(['"])(?<body>.*?)\1/s);
  if (quoteMatch?.groups?.body) {
    return quoteMatch.groups.body;
  }

  return null;
}

/**
 * Check if change is checkbox status change only.
 */
export function isCheckboxOnlyChange(oldBody: string, newBody: string): boolean {
  // None or empty body is not "checkbox only change"
  if (!oldBody || !newBody) {
    return false;
  }

  const oldLines = oldBody.split("\n");
  const newLines = newBody.split("\n");

  // Different line count means content added/removed
  if (oldLines.length !== newLines.length) {
    return false;
  }

  // Markdown list markers: -, *, +
  const checkboxPattern = /^(\s*[-*+]\s*)\[([ xX])\](.*)$/;

  for (let i = 0; i < oldLines.length; i++) {
    if (oldLines[i] === newLines[i]) {
      continue;
    }

    const oldMatch = oldLines[i].match(checkboxPattern);
    const newMatch = newLines[i].match(checkboxPattern);

    if (oldMatch && newMatch) {
      // Same prefix and content, only check status differs → OK
      if (oldMatch[1] === newMatch[1] && oldMatch[3] === newMatch[3]) {
        continue;
      }
    }

    // Non-checkbox change found
    return false;
  }

  return true;
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = (data.tool_input as Record<string, unknown>) || {};
    const command = (toolInput.command as string) || "";

    // Detect gh issue edit command
    if (!command.includes("gh issue edit")) {
      process.exit(0);
    }

    // Only when changing content with --body option
    if (!command.includes("--body")) {
      process.exit(0);
    }

    // Issue #2458: Fork session check (before SKIP check)
    const source = (data.source as string) || "";
    const transcriptPath = data.transcript_path as string | undefined;
    const isFork = isForkSession(sessionId ?? "", source, transcriptPath);

    // Issue #2431: SKIP env check (export and inline)
    // Issue #2458: Don't allow SKIP in fork session
    const skipRequested =
      isSkipEnvEnabled(process.env[SKIP_ENV_NAME]) ||
      isSkipEnvEnabled(extractInlineSkipEnv(command, SKIP_ENV_NAME));

    if (skipRequested) {
      if (isFork) {
        // Don't allow SKIP in fork session
        await logHookExecution(
          HOOK_NAME,
          "block",
          `fork-session: ${SKIP_ENV_NAME} not allowed`,
          undefined,
          { sessionId },
        );
        const result = makeBlockResult(
          HOOK_NAME,
          `[issue-scope-check] 🚫 forkセッションではSKIP不可

forkセッションでは${SKIP_ENV_NAME}は使用できません。
forkセッションは別タスクとして扱うべきです。

【対応方法】
新しいIssueを作成してください:
gh issue create --title "..." --body "..."`,
        );
        console.log(JSON.stringify(result));
        process.exit(0);
      }

      // Allow SKIP in normal session
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `${SKIP_ENV_NAME}=1: スコープ確認をスキップ`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME, `${SKIP_ENV_NAME}=1`);
      console.log(JSON.stringify(result));
      process.exit(0);
    }

    // Issue #2423: Allow checkbox status change only
    const issueNumber = extractIssueNumber(command);
    if (issueNumber) {
      const currentBody = getCurrentIssueBody(issueNumber);
      const newBody = extractBodyFromCommand(command);

      if (currentBody && newBody && isCheckboxOnlyChange(currentBody, newBody)) {
        // Checkbox update only, allow
        const result = makeApproveResult(HOOK_NAME, "checkbox status change only");
        await logHookExecution(HOOK_NAME, "approve", "checkbox status change only", undefined, {
          sessionId,
        });
        console.log(JSON.stringify(result));
        process.exit(0);
      }

      // Log why checkbox check was skipped
      if (!currentBody) {
        await logHookExecution(HOOK_NAME, "skip", "Failed to get current issue body", undefined, {
          sessionId,
        });
      }
      if (!newBody) {
        await logHookExecution(
          HOOK_NAME,
          "skip",
          "Failed to extract new body from command",
          undefined,
          { sessionId },
        );
      }
    }

    // Force scope confirmation (block)
    const issueNumForMsg = issueNumber || "<Issue番号>";
    const blockMessage = `🚫 Issue編集時のスコープ確認

Issueに内容を追加する前に確認が必要です:
- 追加しようとしている内容は、元のIssueと同じタスクですか？
- 異なるタスクであれば、別のIssueとして作成すべきです
- 1 Issue = 1 タスク の原則を守ってください

【対応方法】
1. 同じタスクの場合: ユーザーに確認してから編集を続行
2. 異なるタスクの場合: gh issue create --title "..." --body "..." で新規作成

【スキップ方法】（ユーザー確認済みの場合）
\`\`\`
SKIP_ISSUE_SCOPE_CHECK=1 gh issue edit ${issueNumForMsg} --body "..."
\`\`\`

【補足】
- チェックボックスのステータス変更のみの場合は自動許可されます
- 行数が変わる変更（セクション追加など）はブロックされます`;

    const result = makeBlockResult(HOOK_NAME, blockMessage);
    console.log(JSON.stringify(result));
    process.exit(2);
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    const result = makeApproveResult(HOOK_NAME, `Hook error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(error)}`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify(result));
    process.exit(0);
  }
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({}));
  });
}
