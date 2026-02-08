#!/usr/bin/env bun
/**
 * Bashでの既存ファイル上書き時に警告を表示する。
 *
 * Why:
 *   `Write`ツールはファイル読み込み必須で保護されているが、
 *   Bashの`cat >`や`echo >`には保護がない。意図せず既存ファイルを
 *   上書きして重要なコードを失うリスクがある。
 *
 * What:
 *   - Bashコマンドからリダイレクト先（cat >, echo >, tee等）を抽出
 *   - 対象ファイルが既存かを確認
 *   - 既存ファイルへの上書きの場合は警告メッセージを表示
 *
 * Remarks:
 *   - teeの-a/--appendオプションは追記なので除外
 *   - ブロックはせず警告のみ
 *   - 新規ファイル作成は警告なし
 *
 * Changelog:
 *   - silenvx/dekita#1018: テストファイル上書き事件を機にフック追加
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { existsSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { resolve } from "node:path";
import { getEffectiveCwd } from "../lib/cwd";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "file-overwrite-warning";

// ファイルへのリダイレクトパターン
const REDIRECT_PATTERNS = [
  // cat > file, cat >> file (>> は追記なので除外)
  /cat\s+(?:<<\s*['"]?\w+['"]?\s+)?>\s*([^\s;|&]+)/gi,
  // cat << 'EOF' > file (ヒアドキュメント)
  /cat\s+<<\s*['"]?\w+['"]?\s*>\s*([^\s;|&]+)/gi,
  // echo "..." > file (>> は追記なので除外)
  /echo\s+.*?>\s*([^\s;|&]+)/gi,
  // printf "..." > file
  /printf\s+.*?>\s*([^\s;|&]+)/gi,
];

// teeコマンド用パターン
const TEE_PATTERN = /\|\s*tee\s+([^\n;|&]+)/gi;

/**
 * Parse tee command arguments and return file names.
 * Returns empty list for append mode (-a, --append).
 */
export function parseTeeArguments(argsStr: string): string[] {
  const args = argsStr.trim().split(/\s+/);
  if (args.length === 0) {
    return [];
  }

  const files: string[] = [];
  let appendMode = false;
  let optionsEnded = false;

  for (const arg of args) {
    if (optionsEnded) {
      files.push(arg);
    } else if (arg === "--") {
      optionsEnded = true;
    } else if (!arg.startsWith("-")) {
      files.push(arg);
      optionsEnded = true;
    } else if (arg === "-a" || arg === "--append") {
      appendMode = true;
    } else if (arg.startsWith("-") && !arg.startsWith("--")) {
      // Short options (e.g., -ai, -ia)
      const optionChars = arg.slice(1);
      if (optionChars.includes("a")) {
        appendMode = true;
      }
    }
  }

  return appendMode ? [] : files;
}

/**
 * Extract redirect targets from command.
 */
export function extractRedirectTargets(command: string): string[] {
  const targets: string[] = [];

  // Standard redirect patterns
  for (const pattern of REDIRECT_PATTERNS) {
    pattern.lastIndex = 0;
    let match: RegExpExecArray | null = pattern.exec(command);
    while (match !== null) {
      targets.push(match[1]);
      match = pattern.exec(command);
    }
  }

  // tee command (separate handling for append mode)
  TEE_PATTERN.lastIndex = 0;
  let teeMatch: RegExpExecArray | null = TEE_PATTERN.exec(command);
  while (teeMatch !== null) {
    const teeFiles = parseTeeArguments(teeMatch[1]);
    targets.push(...teeFiles);
    teeMatch = TEE_PATTERN.exec(command);
  }

  return targets;
}

/**
 * Expand environment variables and ~ in path.
 */
export function expandPath(filePath: string): string {
  // Expand environment variables
  let expanded = filePath.replace(/\$(\w+)/g, (_, name) => process.env[name] || "");
  expanded = expanded.replace(/\$\{(\w+)\}/g, (_, name) => process.env[name] || "");

  // Expand ~
  if (expanded === "~") {
    return homedir();
  }
  if (expanded.startsWith("~/")) {
    return homedir() + expanded.slice(1);
  }

  return expanded;
}

/**
 * Resolve path considering cd in command.
 */
function resolvePath(filePath: string, command?: string): string {
  const expanded = expandPath(filePath);

  if (expanded.startsWith("/")) {
    return resolve(expanded);
  }

  const effectiveCwd = getEffectiveCwd(command);
  return resolve(effectiveCwd, expanded);
}

async function main(): Promise<void> {
  const hookInput = await parseHookInput();
  const sessionId = hookInput.session_id;
  const toolName = hookInput.tool_name || "";

  // Only process Bash tool
  if (toolName !== "Bash") {
    console.log(JSON.stringify({}));
    return;
  }

  const toolInput = hookInput.tool_input || {};
  const command = (toolInput.command as string) || "";

  // Extract redirect targets
  const targets = extractRedirectTargets(command);

  if (targets.length === 0) {
    console.log(JSON.stringify({}));
    return;
  }

  // Check for existing files
  const existingFiles: string[] = [];
  for (const target of targets) {
    try {
      const resolvedPath = resolvePath(target, command);
      if (existsSync(resolvedPath)) {
        const stat = statSync(resolvedPath);
        if (stat.isFile()) {
          existingFiles.push(resolvedPath);
        }
      }
    } catch {
      // Path resolution error - ignore (fail-open)
    }
  }

  if (existingFiles.length === 0) {
    // New file(s) - no warning
    await logHookExecution(HOOK_NAME, "approve", `New file(s): ${targets.join(", ")}`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify({}));
    return;
  }

  // Warn about existing file overwrite
  await logHookExecution(
    HOOK_NAME,
    "approve",
    `Existing file(s) will be overwritten: ${existingFiles.join(", ")}`,
    undefined,
    { sessionId },
  );

  const filesList = existingFiles.map((f) => `  - ${f}`).join("\n");
  const result = {
    message: `[${HOOK_NAME}] ⚠️ 既存ファイルを上書きしようとしています。\n\n**対象ファイル:**\n${filesList}\n\n**確認事項:**\n- 本当にこのファイルを上書きしますか？\n- 既存の内容を確認しましたか？\n- \`Write\`ツールを使用すると、既存内容の確認が必須になります。\n\n**推奨:**\n- 既存ファイルの編集には \`Edit\` または \`Write\` ツールを使用\n- 内容を追記する場合は \`>>\` を使用\n\nIssue #1018: テストファイル上書き事件を防止するための警告です。`,
  };
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
