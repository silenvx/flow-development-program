#!/usr/bin/env bun
/**
 * 依存関係追加時にContext7/Web検索での最新情報確認を促す。
 *
 * Why:
 *   古いAPIや非推奨メソッドの使用を防ぐため、パッケージ追加時に
 *   最新のドキュメントを確認する習慣を促進する。
 *
 * What:
 *   - pnpm add, npm install, pip install等を検出
 *   - Context7やWeb検索での最新情報確認を促すメッセージを表示
 *   - セッション内で同じパッケージへの重複リマインドを防止
 *
 * State:
 *   - writes: $TMPDIR/claude-hooks/dependency-check-reminded-{session}.json
 *
 * Remarks:
 *   - 情報提供型フック（ブロックしない、systemMessageでリマインド）
 *   - PreToolUse:Bashで発火（pnpm/npm/pip/uv等）
 *   - セッション内で同じパッケージへの重複リマインド防止
 *   - requirements.txtインストール（-r）は除外
 *   - Python版: dependency_check_reminder.py
 *
 * Changelog:
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "dependency-check-reminder";

// Package manager command patterns
const DEPENDENCY_COMMANDS: Array<[RegExp, string]> = [
  // JavaScript/TypeScript
  [/pnpm\s+add\s+/, "pnpm add"],
  [/npm\s+install\s+\S/, "npm install"],
  [/npm\s+i\s+\S/, "npm i"],
  [/yarn\s+add\s+/, "yarn add"],
  // Python
  [/pip\s+install\s+\S/, "pip install"],
  [/uv\s+add\s+/, "uv add"],
  [/poetry\s+add\s+/, "poetry add"],
  // Rust
  [/cargo\s+add\s+/, "cargo add"],
];

// Commands to exclude (requirements file installs)
const EXCLUDE_PATTERNS: RegExp[] = [/pip\s+install\s+.*(?:-r|--requirement)\s/i];

// Patterns to extract package names (supports scoped packages like @types/node)
const PACKAGE_EXTRACTORS: Record<string, RegExp> = {
  "pnpm add": /pnpm\s+add\s+(?:-\S+\s+)*(\S+)/i,
  "npm install": /npm\s+(?:install|i)\s+(?:-\S+\s+)*(\S+)/i,
  "npm i": /npm\s+i\s+(?:-\S+\s+)*(\S+)/i,
  "yarn add": /yarn\s+add\s+(?:-\S+\s+)*(\S+)/i,
  "pip install": /pip\s+install\s+(?:-\S+\s+)*(\S+)/i,
  "uv add": /uv\s+add\s+(?:-\S+\s+)*(\S+)/i,
  "poetry add": /poetry\s+add\s+(?:-\S+\s+)*(\S+)/i,
  "cargo add": /cargo\s+add\s+(?:-\S+\s+)*(\S+)/i,
};

function getSessionDir(): string {
  return join(process.env.TMPDIR ?? tmpdir(), "claude-hooks");
}

function getRemindedPackagesFile(sessionId: string): string {
  const safeSessionId = basename(sessionId);
  return join(getSessionDir(), `dependency-check-reminded-${safeSessionId}.json`);
}

function loadRemindedPackages(sessionId: string): Set<string> {
  const filePath = getRemindedPackagesFile(sessionId);
  try {
    if (existsSync(filePath)) {
      const data = JSON.parse(readFileSync(filePath, "utf-8"));
      return new Set(data.packages ?? []);
    }
  } catch {
    // Silently fail if file is missing, corrupt, or unreadable
  }
  return new Set();
}

function saveRemindedPackages(sessionId: string, packages: Set<string>): void {
  const dir = getSessionDir();
  try {
    mkdirSync(dir, { recursive: true });
    const filePath = getRemindedPackagesFile(sessionId);
    writeFileSync(filePath, JSON.stringify({ packages: Array.from(packages) }));
  } catch {
    // Silently fail if file cannot be written
  }
}

export function detectDependencyCommand(command: string): {
  cmdType: string | null;
  packageName: string | null;
} {
  // Check exclusion patterns first
  for (const excludePattern of EXCLUDE_PATTERNS) {
    if (excludePattern.test(command)) {
      return { cmdType: null, packageName: null };
    }
  }

  for (const [pattern, cmdType] of DEPENDENCY_COMMANDS) {
    if (pattern.test(command)) {
      const extractor = PACKAGE_EXTRACTORS[cmdType];
      if (extractor) {
        const match = command.match(extractor);
        if (match) {
          let packageName = match[1];
          // Clean up package name (remove version specifiers)
          if (packageName.startsWith("@")) {
            // For scoped packages, find the second @ (version) if exists
            const atPos = packageName.indexOf("@", 1);
            if (atPos !== -1) {
              packageName = packageName.slice(0, atPos);
            }
          } else {
            // For regular packages, remove version after @ or ^
            packageName = packageName.replace(/[@^~>=<].*$/, "");
          }
          return { cmdType, packageName };
        }
      }
      return { cmdType, packageName: null };
    }
  }
  return { cmdType: null, packageName: null };
}

export function formatReminderMessage(_cmdType: string, packageName: string | null): string {
  const lines: string[] = ["📦 **依存関係追加を検出**", ""];

  if (packageName) {
    lines.push(
      `パッケージ \`${packageName}\` を追加しようとしています。`,
      "",
      "**最新情報を確認してください:**",
      "",
      `1. **Context7**: \`${packageName}\` のドキュメントを参照`,
      "   - `mcp__context7__resolve-library-id` でライブラリIDを取得",
      "   - `mcp__context7__get-library-docs` でドキュメントを取得",
      "",
      "2. **Web検索**: 最新バージョン・変更履歴を確認",
      `   - 「${packageName} latest version」で検索`,
      "",
    );
  } else {
    lines.push(
      "依存関係を追加しようとしています。",
      "",
      "**最新情報を確認してください:**",
      "",
      "- Context7でライブラリのドキュメントを参照",
      "- Web検索で最新バージョン・変更履歴を確認",
      "",
    );
  }

  lines.push("💡 古いAPIや非推奨メソッドの使用を防ぐため、最新情報の確認を推奨します。");

  return lines.join("\n");
}

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};

  try {
    const inputData = await parseHookInput();
    const toolInput = (inputData.tool_input as Record<string, unknown>) ?? {};
    const command = (toolInput.command as string) ?? "";
    const sessionId = inputData.session_id ?? "unknown";

    // Check if this is a dependency command
    const { cmdType, packageName } = detectDependencyCommand(command);

    if (cmdType) {
      // Check if we already reminded about this package
      const reminded = loadRemindedPackages(sessionId);
      const remindKey = packageName ?? cmdType;

      // Only remind once per package per session
      if (!reminded.has(remindKey)) {
        result.systemMessage = formatReminderMessage(cmdType, packageName);
        reminded.add(remindKey);
        saveRemindedPackages(sessionId, reminded);
      }
    }
  } catch (error) {
    // Don't block on errors, just skip the reminder
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  await logHookExecution(HOOK_NAME, result.decision ?? "approve", undefined);
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
