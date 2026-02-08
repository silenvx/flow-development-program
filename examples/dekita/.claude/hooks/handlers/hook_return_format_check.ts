#!/usr/bin/env bun
/**
 * フック種類に応じた返却形式の誤用を検出する。
 *
 * Why:
 *   フック種類によって期待される返却形式が異なる。誤った形式を使用すると
 *   フックが正しく動作しない。PR #1632でStop hookに誤った形式を適用した
 *   問題の再発を防止する。
 *
 * What:
 *   - Stop hookでprint_continue_and_log_skip使用を検出
 *   - フック種類と期待される返却形式の対応をチェック
 *   - 不一致時に警告（ブロックしない）
 *
 * Remarks:
 *   - 非ブロック型（警告のみ）
 *   - Stop: print_approve_and_log_skip、PostToolUse: print_continue_and_log_skip
 *   - Python版: hook_return_format_check.py
 *
 * Changelog:
 *   - silenvx/dekita#1635: フック追加
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { existsSync, readFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "hook-return-format-check";

export interface Issue {
  file: string;
  lines: number[];
  hook_type: string;
  message: string;
  severity: "error" | "warning";
}

export interface HookConfig {
  command?: string;
  hooks?: HookConfig[];
}

export interface Settings {
  hooks?: {
    SessionStart?: HookConfig[];
    PreToolUse?: HookConfig[];
    PostToolUse?: HookConfig[];
    Stop?: HookConfig[];
  };
}

/**
 * Load settings.json from project directory.
 */
function loadSettings(): Settings {
  const projectDir = process.env.CLAUDE_PROJECT_DIR || "";
  let settingsPath: string;

  if (projectDir) {
    settingsPath = join(projectDir, ".claude", "settings.json");
  } else {
    // Fallback: relative to this file
    const hookDir = dirname(dirname(__dirname));
    settingsPath = join(hookDir, "settings.json");
  }

  try {
    if (existsSync(settingsPath)) {
      return JSON.parse(readFileSync(settingsPath, "utf-8")) as Settings;
    }
  } catch {
    // Return empty settings on error
  }
  return {};
}

/**
 * Determine which hook type a file belongs to.
 */
export function getHookTypeForFile(filePath: string, settings: Settings): string | null {
  const fileName = basename(filePath);
  const hooksConfig = settings.hooks || {};

  const hookTypes = ["SessionStart", "PreToolUse", "PostToolUse", "Stop"] as const;

  for (const hookType of hookTypes) {
    const hookList = hooksConfig[hookType] || [];
    for (const hookGroup of hookList) {
      // Handle both direct hooks and nested hooks structure
      const hooks = hookGroup.hooks || [hookGroup];
      for (const hook of hooks) {
        const command = hook.command || "";
        // Tokenize command and check each token as a path
        for (const token of command.split(/\s+/)) {
          try {
            const tokenName = basename(token.replace(/["']/g, ""));
            if (tokenName === fileName) {
              return hookType;
            }
          } catch {
            // 無効なパストークン、スキップ
          }
        }
      }
    }
  }

  return null;
}

/**
 * Check which return format functions are used in the file.
 *
 * Uses simple heuristics to detect function calls.
 */
export function checkReturnFormatUsage(content: string): Record<string, number[]> {
  const usage: Record<string, number[]> = {
    print_continue_and_log_skip: [],
    print_approve_and_log_skip: [],
  };

  let inDocstring = false;
  let docstringDelimiter: string | null = null;

  const lines = content.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const lineNum = i + 1;
    const stripped = line.trim();

    // Track docstring state (triple quotes)
    if (!inDocstring) {
      if (stripped.startsWith('"""') || stripped.startsWith("'''")) {
        docstringDelimiter = stripped.slice(0, 3);
        // Check if docstring ends on the same line
        if (stripped.split(docstringDelimiter).length > 2) {
          continue; // Single-line docstring, skip
        }
        inDocstring = true;
        continue;
      }
    } else {
      if (docstringDelimiter && stripped.includes(docstringDelimiter)) {
        inDocstring = false;
        docstringDelimiter = null;
      }
      continue;
    }

    // Skip comments and imports
    if (
      stripped.startsWith("#") ||
      stripped.startsWith("from ") ||
      stripped.startsWith("import ")
    ) {
      continue;
    }

    // Check for function calls (with opening parenthesis)
    for (const funcName of Object.keys(usage)) {
      if (line.includes(`${funcName}(`)) {
        usage[funcName].push(lineNum);
      }
    }
  }

  return usage;
}

/**
 * Analyze a hook file for return format mismatches.
 */
export function analyzeHookFile(filePath: string, content: string, settings: Settings): Issue[] {
  const issues: Issue[] = [];

  const hookType = getHookTypeForFile(filePath, settings);
  if (!hookType) {
    return issues; // Not a registered hook, skip
  }

  const usage = checkReturnFormatUsage(content);

  if (hookType === "Stop") {
    // Stop hooks should NOT use print_continue_and_log_skip
    if (usage.print_continue_and_log_skip.length > 0) {
      const lines = usage.print_continue_and_log_skip;
      issues.push({
        file: filePath,
        lines,
        hook_type: hookType,
        message: `Stop hook uses \`print_continue_and_log_skip\` at line(s) ${lines.join(", ")}. Stop hooks must return {"decision": "approve"}, not {"continue": true}. Use \`print_approve_and_log_skip\` instead.`,
        severity: "error",
      });
    }
  } else if (hookType === "PostToolUse") {
    // PostToolUse hooks should typically use print_continue_and_log_skip
    if (usage.print_approve_and_log_skip.length > 0) {
      const lines = usage.print_approve_and_log_skip;
      issues.push({
        file: filePath,
        lines,
        hook_type: hookType,
        message: `PostToolUse hook uses \`print_approve_and_log_skip\` at line(s) ${lines.join(", ")}. PostToolUse hooks typically return {"continue": true}. Consider using \`print_continue_and_log_skip\` unless you have a specific reason.`,
        severity: "warning",
      });
    }
  }

  // PreToolUse and SessionStart can use either, so no check needed

  return issues;
}

async function main(): Promise<void> {
  const result: { decision?: string; systemMessage?: string } = {};
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolName = (data.tool_name as string) || "";
    const toolInput = (data.tool_input as Record<string, unknown>) || {};

    // Only check Edit and Write operations
    if (toolName !== "Edit" && toolName !== "Write") {
      await logHookExecution(HOOK_NAME, "approve", `not Edit/Write: ${toolName}`, undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    const filePath = (toolInput.file_path as string) || "";

    // Only check hook Python files
    if (!filePath.endsWith(".py")) {
      await logHookExecution(HOOK_NAME, "approve", "not Python file", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    if (!filePath.includes(".claude/hooks/")) {
      await logHookExecution(HOOK_NAME, "approve", "not in .claude/hooks/", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Skip test files
    if (filePath.includes("/tests/")) {
      await logHookExecution(HOOK_NAME, "approve", "test file", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Get the new content
    let content: string;
    if (toolName === "Write") {
      content = (toolInput.content as string) || "";
    } else {
      // Edit
      const oldString = (toolInput.old_string as string) || "";
      const newString = (toolInput.new_string as string) || "";

      try {
        if (!existsSync(filePath)) {
          await logHookExecution(HOOK_NAME, "approve", "file not found", undefined, { sessionId });
          console.log(JSON.stringify(result));
          return;
        }
        const currentContent = readFileSync(filePath, "utf-8");
        content = currentContent.replace(oldString, newString);
      } catch {
        await logHookExecution(HOOK_NAME, "approve", "file read error", undefined, { sessionId });
        console.log(JSON.stringify(result));
        return;
      }
    }

    // Load settings and analyze
    const settings = loadSettings();
    const issues = analyzeHookFile(filePath, content, settings);

    if (issues.length > 0) {
      const warnings = issues.map((issue) => {
        const prefix = issue.severity === "error" ? "❌" : "⚠️";
        return `${prefix} ${issue.file}: ${issue.message}`;
      });

      result.systemMessage = `Hook return format check:\n${warnings.join("\n")}\n\nReference:\n- Stop hooks: must return {"decision": "approve"} → use print_approve_and_log_skip\n- PostToolUse hooks: should return {"continue": true} → use print_continue_and_log_skip\n- PreToolUse hooks: either format is valid\n- SessionStart hooks: typically no return value needed`;

      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Found ${issues.length} issue(s)`,
        {
          file: filePath,
          issues: issues.map((i) => i.message),
        },
        { sessionId },
      );
    } else {
      await logHookExecution(HOOK_NAME, "approve", undefined, { file: filePath }, { sessionId });
    }
  } catch (error) {
    const errorMsg = `Hook error: ${formatError(error)}`;
    console.error(`[${HOOK_NAME}] ${errorMsg}`);
    await logHookExecution(HOOK_NAME, "approve", errorMsg, undefined, { sessionId });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({}));
  });
}
