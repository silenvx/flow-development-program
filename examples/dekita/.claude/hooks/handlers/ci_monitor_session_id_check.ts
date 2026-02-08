#!/usr/bin/env bun
/**
 * ci_monitor（TypeScript版）のsession-idオプション指定を検出する。
 *
 * Why:
 *   session-idが指定されないと、ci_monitorはppidベースのセッション特定に
 *   フォールバックし、ログがClaude Codeセッションと正しく紐付かなくなる。
 *
 * What:
 *   - ci_monitor_ts/main.ts呼び出しを検出
 *   - --session-idオプションの有無を確認
 *   - 未指定の場合は警告メッセージを表示
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、警告のみ）
 *   - PreToolUse:Bashで発火
 *   - AGENTS.mdにsession-id指定が必須と記載されている
 *
 * Changelog:
 *   - silenvx/dekita#2389: フック追加（Python）
 *   - silenvx/dekita#3148: TypeScriptに移行
 *   - silenvx/dekita#3294: Python版ci_monitor削除に伴いTypeScript版を検出
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "ci-monitor-session-id-check";

function approve(): void {
  console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
}

async function main(): Promise<void> {
  try {
    const inputJson = await parseHookInput();
    const toolInput = inputJson?.tool_input ?? {};
    const command = (toolInput.command as string) ?? "";
    const sessionId = inputJson?.session_id;

    // Not a ci_monitor call - approve silently
    if (!/ci_monitor_ts\/main\.ts/.test(command)) {
      approve();
      return;
    }

    // --session-id provided (space or = separator) - approve with log
    // Pattern: --session-id=value or --session-id value
    if (/--session-id(?:=\S+|\s+\S+)/.test(command)) {
      await logHookExecution(HOOK_NAME, "approve", "--session-id provided", {}, { sessionId });
      approve();
      return;
    }

    // ci_monitor called without --session-id - warn
    const warning = `[${HOOK_NAME}] 警告: --session-id が指定されていません

ログが正しいセッションと紐付かなくなります。
--session-id を追加してください:

  bun run .claude/scripts/ci_monitor_ts/main.ts {PR} --session-id <SESSION_ID>

※ <SESSION_ID>はUserPromptSubmit hookで提供されるセッションID
  例: 3f03a042-a9ef-44a2-839a-d17badc44b0a`;

    // Include warning in systemMessage for display
    const result = {
      ...makeApproveResult(HOOK_NAME),
      systemMessage: warning,
    };
    await logHookExecution(
      HOOK_NAME,
      "approve_with_warning",
      "--session-id missing",
      {
        command: command.slice(0, 100),
      },
      { sessionId },
    );
    console.log(JSON.stringify(result));
  } catch (error) {
    // On error, approve to avoid blocking legitimate commands
    console.error(`[${HOOK_NAME}] Hook error:`, error);
    await logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(error)}`);
    approve();
  }
}

if (import.meta.main) {
  main().catch((error) => {
    console.error(`[${HOOK_NAME}] Unexpected error:`, error);
    console.log(JSON.stringify({}));
    process.exit(0); // Don't block on error
  });
}
