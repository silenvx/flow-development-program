#!/usr/bin/env bun
/**
 * except内での空コレクション返却アンチパターンを検出する。
 *
 * Why:
 *   例外発生時に空リストを返すと、「成功したが結果が空」と「失敗」の
 *   区別がつかなくなる。呼び出し側が失敗を「データなし」と誤解し、
 *   不正な処理を続行するバグの原因となる。
 *
 * What:
 *   - Python ファイルの Write/Edit を検出
 *   - 正規表現で except ブロック内の `return []` や `return {}` を検出
 *   - 検出時は None を返すことを推奨する警告を表示
 *
 * Remarks:
 *   - テストファイルは意図的なパターンの可能性があるため除外
 *   - ブロックはせず警告のみ（P2レベル）
 *   - Python版のAST解析より簡易だが、主要パターンは検出可能
 *   - Python版: empty_return_check.py
 *
 * Changelog:
 *   - silenvx/dekita#????: P2バグ再発防止の仕組み化
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { existsSync, readFileSync } from "node:fs";
import { basename } from "node:path";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "empty-return-check";

interface Issue {
  file: string;
  line: number;
  message: string;
}

/**
 * Check Python content for empty collection returns in except blocks.
 *
 * Uses a simplified regex-based approach instead of full AST parsing.
 * This catches the common patterns like:
 *   except ...:
 *       return []
 *       return {}
 *       return ()
 *       return set()
 *       return list()
 *       return dict()
 */
export function checkForEmptyReturns(filePath: string, content: string): Issue[] {
  const issues: Issue[] = [];
  const lines = content.split("\n");

  let inExceptBlock = false;
  let exceptIndent = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const lineNum = i + 1;

    // Calculate leading whitespace
    const leadingSpaces = line.match(/^(\s*)/)?.[1]?.length ?? 0;

    // Detect start of except block
    // Pattern: "except" followed by optional exception type and colon
    const exceptMatch = line.match(/^(\s*)except\s*(?:[^:]+)?:\s*$/);
    if (exceptMatch) {
      inExceptBlock = true;
      exceptIndent = exceptMatch[1].length;
      continue;
    }

    // If we're in an except block, check for empty returns
    if (inExceptBlock) {
      // Exit except block if indentation goes back to same or lower level
      // (and line is not empty or comment-only)
      const trimmed = line.trim();
      if (trimmed && !trimmed.startsWith("#") && leadingSpaces <= exceptIndent) {
        inExceptBlock = false;
        continue;
      }

      // Check for empty collection returns
      // Patterns: return [], return {}, return (), return set(), return list(), return dict(), return tuple()
      const emptyReturnPattern =
        /^\s*return\s*(\[\]|\{\}|\(\)|set\(\)|list\(\)|dict\(\)|tuple\(\))\s*(?:#.*)?$/;
      if (emptyReturnPattern.test(line)) {
        issues.push({
          file: filePath,
          line: lineNum,
          message:
            "Empty collection return in except block. " +
            "Consider returning None to distinguish failure from empty data.",
        });
      }
    }
  }

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
      await logHookExecution(HOOK_NAME, "approve", "Not Edit/Write", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    const filePath = (toolInput.file_path as string) || "";

    // Only check Python files
    if (!filePath.endsWith(".py")) {
      await logHookExecution(HOOK_NAME, "approve", "Not Python file", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Skip test files (the pattern may be intentional in tests)
    const filename = basename(filePath);
    if (
      filePath.includes("/tests/") ||
      filename.startsWith("test_") ||
      filename.endsWith("_test.py")
    ) {
      await logHookExecution(HOOK_NAME, "approve", "Test file skipped", undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Get the new content
    let content: string;
    if (toolName === "Write") {
      content = (toolInput.content as string) || "";
    } else {
      // Edit: read file and apply the edit
      const oldString = (toolInput.old_string as string) || "";
      const newString = (toolInput.new_string as string) || "";

      try {
        if (!existsSync(filePath)) {
          await logHookExecution(HOOK_NAME, "approve", "File not found", undefined, { sessionId });
          console.log(JSON.stringify(result));
          return;
        }
        const currentContent = readFileSync(filePath, "utf-8");
        content = currentContent.replace(oldString, newString);
      } catch {
        await logHookExecution(HOOK_NAME, "approve", "File read error", undefined, { sessionId });
        console.log(JSON.stringify(result));
        return;
      }
    }

    // Check for the antipattern
    const issues = checkForEmptyReturns(filePath, content);

    if (issues.length > 0) {
      const warnings = issues.map((issue) => `⚠️ ${issue.file}:${issue.line}: ${issue.message}`);

      result.systemMessage = `Empty collection return in except block detected:\n${warnings.join("\n")}\n\nThis pattern can cause bugs where failure is mistaken for empty data. Consider returning None instead to allow callers to distinguish.`;

      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Warning: ${issues.length} issue(s) found`,
        {
          file: filePath,
          issues: issues.length,
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
  main();
}
