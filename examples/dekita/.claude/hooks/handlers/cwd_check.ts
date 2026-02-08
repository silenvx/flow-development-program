#!/usr/bin/env bun
/**
 * カレントディレクトリの存在を確認し、削除されていればセッションをブロックする。
 *
 * Why:
 *   worktree削除などでカレントディレクトリが消失すると、
 *   以降のファイル操作が予期せず失敗し、データ損失につながる可能性がある。
 *
 * What:
 *   - カレントディレクトリの存在を確認
 *   - 存在しない場合はセッションをブロック
 *   - Claude Code再起動の案内を表示
 *
 * Remarks:
 *   - ブロック型フック（cwd消失時はブロック）
 *   - PreToolUseで発火（全ツール対象）
 *   - stop_hook_active時は無限ループ防止のためスキップ
 *   - worktree削除後のセッション継続防止が主な用途
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "cwd-check";

/**
 * Check if the current working directory exists.
 * Returns [exists, info] tuple.
 */
export function checkCwdExists(): [boolean, string] {
  try {
    const cwd = process.cwd();
    return [true, cwd];
  } catch (error) {
    return [false, String(error)];
  }
}

/**
 * Generate a handoff message for the next agent when cwd is lost.
 */
export function generateHandoffMessage(): string {
  return `
## カレントディレクトリ消失を検知

**問題**: カレントディレクトリが存在しません。

**推定原因**: ディレクトリが削除された、ファイルシステムがアンマウントされた、権限が変更された、または worktree 内から \`git worktree remove\` を実行した等、様々な理由が考えられます。

**対応が必要**:
1. Claude Codeを再起動してください
2. オリジナルディレクトリまたは有効な作業ディレクトリから作業を再開してください

**次のエージェントへの引き継ぎ**:
- 前のエージェントはカレントディレクトリが存在しない状態で処理を継続しようとしました
- これは worktree の削除やディレクトリの消失など、複数の原因が考えられます
- ディレクトリや worktree の状態を確認し、必要に応じてクリーンアップや再作成を行ってください
`;
}

async function main(): Promise<void> {
  let result: {
    ok?: boolean;
    decision?: string;
    reason?: string;
    systemMessage?: string;
  } = {};
  let sessionId: string | undefined;

  try {
    const inputJson = await parseHookInput();
    sessionId = inputJson.session_id;

    // If stop_hook_active is set, approve immediately to avoid infinite retry loops
    if (inputJson.stop_hook_active) {
      result = {
        ok: true,

        reason: "stop_hook_active is set; approving to avoid infinite retry loop.",
      };
    } else {
      const [exists, info] = checkCwdExists();

      if (exists) {
        result = {
          ok: true,

          reason: `Current working directory exists: ${info}`,
          systemMessage: "✅ cwd-check: カレントディレクトリ存在確認OK",
        };
      } else {
        const reason = generateHandoffMessage();
        result = {
          ...makeBlockResult(HOOK_NAME, reason),
          ok: false,
          systemMessage: "⚠️ カレントディレクトリが存在しません。Claude Codeの再起動が必要です。",
        };
      }
    }
  } catch (error) {
    // On error, approve to avoid blocking, but log for debugging
    console.error(`[cwd-check] Hook error: ${formatError(error)}`);
    result = { ok: true, reason: `Hook error: ${formatError(error)}` };
  }

  logHookExecution(HOOK_NAME, result.decision ?? "approve", result.reason, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
