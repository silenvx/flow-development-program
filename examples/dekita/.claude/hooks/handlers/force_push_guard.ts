#!/usr/bin/env bun
/**
 * 危険な`git push --force`をブロックし、`--force-with-lease`を推奨する。
 *
 * Why:
 *   `git push --force`はリモートの状態を確認せずに上書きするため、
 *   他の変更を消失させる危険がある。`--force-with-lease`は安全に
 *   force pushできるため、こちらを推奨する。
 *
 * What:
 *   - git push --force または -f を検出
 *   - 検出時はブロックし、--force-with-leaseを推奨
 *   - --force-with-leaseは許可
 *
 * Remarks:
 *   - コマンドチェーン内の各サブコマンドを個別にチェック
 *   - クォート内の文字列は無視（echo "git push --force"等）
 *
 * Changelog:
 *   - silenvx/dekita#941: コマンドチェーン対応
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { splitCommandChain, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "force-push-guard";

/**
 * Check if a single command (not a chain) is a dangerous git push --force.
 *
 * This function should only be called with individual commands,
 * not command chains (those should be split first).
 */
export function isDangerousForcePushSingle(subcommand: string): boolean {
  if (!subcommand.trim()) {
    return false;
  }

  // Strip quoted strings to avoid false positives
  const stripped = stripQuotedStrings(subcommand);

  // Must be a git push command
  if (!/\bgit\s+push\b/.test(stripped)) {
    return false;
  }

  // Check for dangerous --force or -f first (before checking --force-with-lease)
  // Match --force that is NOT part of --force-with-lease
  // Note: --force-with-lease does NOT match this regex because of negative lookahead
  if (/--force\b(?!-with-lease)/.test(stripped)) {
    return true;
  }

  // Match -f flag, including combined short flags like -uf or -fu
  // Git allows combining short flags: -u -f can be written as -uf or -fu
  // Pattern matches: -f, -uf, -fu, -auf, etc.
  // IMPORTANT: Must NOT match --follow-tags, --filter, etc. (single dash only)
  if (/(?:^|\s)-(?!-)[a-z]*f[a-z]*(?:\s|$)/.test(stripped)) {
    return true;
  }

  // If we reach here, either no force flags or only --force-with-lease
  return false;
}

/**
 * Check if command is a dangerous git push --force (without --force-with-lease).
 *
 * Issue #941: Handles command chains (&&, ||, ;) by checking each subcommand individually.
 * This prevents false positives when --force is used in a different command in the chain.
 *
 * Returns true for:
 * - git push --force
 * - git push -f
 * - git push -uf (combined flags containing f)
 * - git push origin branch --force
 * - git push --force origin branch
 * - git push --force-with-lease --force (--force takes precedence)
 *
 * Returns false for:
 * - git push --force-with-lease (only)
 * - git push (normal push)
 * - git push -u (no f flag)
 * - Commands inside quoted strings (e.g., echo "git push --force")
 * - git worktree remove --force && git push (--force belongs to different command)
 */
export function isDangerousForcePush(command: string): boolean {
  if (!command.trim()) {
    return false;
  }

  // First, strip quoted strings to avoid splitting on operators inside quotes
  // e.g., 'echo "backup && git push --force"' should not be split
  const strippedForSplit = stripQuotedStrings(command);

  // Split the stripped command chain
  const subcommands = splitCommandChain(strippedForSplit);

  for (const subcommand of subcommands) {
    if (isDangerousForcePushSingle(subcommand)) {
      return true;
    }
  }

  return false;
}

async function main(): Promise<void> {
  let result: {
    decision?: string;
    reason?: string;
    systemMessage?: string;
  } = {};
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = data.tool_input || {};
    const command = (toolInput.command as string) || "";

    if (isDangerousForcePush(command)) {
      const reason =
        "`git push --force`は危険です。他の変更を上書きする可能性があります。\n\n" +
        "**代わりに以下の安全な方法を使用してください:**\n\n" +
        "```bash\n" +
        "# 1. リモートの状態を取得\n" +
        "git fetch origin\n\n" +
        "# 2. リモートの変更を確認（origin/<ブランチ名> を指定）\n" +
        "git log --oneline origin/<ブランチ名> -5\n\n" +
        "# 3. 必要ならリベース（origin/<ブランチ名> を指定）\n" +
        "git rebase origin/<ブランチ名>\n\n" +
        "# 4. 安全なforce push\n" +
        "git push --force-with-lease\n" +
        "```\n\n" +
        "`--force-with-lease`はリモートが予期した状態であることを確認してからプッシュします。\n" +
        "これにより、他の人の作業を誤って上書きすることを防げます。";
      result = makeBlockResult(HOOK_NAME, reason);
      await logHookExecution(HOOK_NAME, "block", "dangerous force push detected", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }
  } catch (error) {
    console.error(`[force-push-guard] Hook error: ${formatError(error)}`);
    result = {};
  }

  await logHookExecution(HOOK_NAME, result.decision ?? "approve", undefined, undefined, {
    sessionId,
  });
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
