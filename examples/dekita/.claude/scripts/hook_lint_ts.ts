#!/usr/bin/env bun
/**
 * TypeScriptフック専用のカスタムLintルールを適用する。
 *
 * Why:
 *   TypeScriptフック実装の一貫性を保証し、よくあるミスや
 *   アンチパターンを検出するため。PR #3414のCopilotレビューで
 *   指摘されたパターン違反を自動検出する。
 *
 * What:
 *   - TSHOOK001: import.meta.main ガードの存在確認
 *   - TSHOOK002: process.cwd() の直接使用を検出（main関数内）
 *   - TSHOOK003: tool_input からの直接フィールドアクセス（Zodスキーマなし）
 *
 * Remarks:
 *   - ファイル指定なしで全TypeScriptフックをチェック
 *   - --check-only でサマリーのみ表示
 *
 * Changelog:
 *   - silenvx/dekita#3799: テンプレートリテラル内の正規表現リテラルを正しく処理
 *   - silenvx/dekita#3426: テンプレートリテラル内の${...}コードを保持
 *   - silenvx/dekita#3423: TypeScriptフック用Lint機能を追加
 */

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { basename, join } from "node:path";

/**
 * Check if '/' at current position is likely a regex start.
 * Returns true if the previous non-whitespace token suggests regex context.
 *
 * JavaScript uses '/' for three purposes:
 * - Division operator: a / b
 * - Regex literal: /pattern/
 * - Comments: // or /*
 *
 * Regex literals typically follow:
 * - Operators: =, (, [, ,, :, !, &, |, ?, {, ;, +, -, *, %, <, >, ^, ~
 * - Keywords: return, throw, case, yield, await, void, typeof, delete, else, do
 * Note: } is NOT a regex-preceding operator - `{ a: 1 } / 2` is division
 *
 * Division typically follows:
 * - ), ], identifiers (non-keyword), numbers
 */
function isLikelyRegexStart(result: string[]): boolean {
  // Combine last few parts to get enough context to check keywords
  // Lookback limit of 100 chars is sufficient for even the longest keywords (instanceof = 10 chars)
  // plus potential whitespace and operators
  let context = "";
  for (let i = result.length - 1; i >= 0; i--) {
    context = result[i] + context;
    if (context.length > 100) break;
  }

  const trimmed = context.trimEnd();
  if (trimmed.length === 0) return true; // Start of content

  const lastChar = trimmed.charAt(trimmed.length - 1);

  // Special case: postfix ++ and -- imply division follows (e.g., i++ / 2)
  // These end with + or - which would otherwise match the operator check below
  if (trimmed.endsWith("++") || trimmed.endsWith("--")) {
    return false;
  }

  // 1. Check for operators that precede regex literals
  // Includes: =, (, [, ,, :, {, ;, !, &, |, ?, +, -, *, %, <, >, ^, ~
  // Note: } is NOT included - `{ a: 1 } / 2` is division, not regex
  // Note: ) is NOT included - `(a + b) / 2` is division. This means `if (x) /r/` (without braces) will not be detected as regex.
  if (/[=(\[,:{ ;!&|?+\-*%<>^~]/.test(lastChar)) {
    return true;
  }

  // 2. Check for keywords that precede regexes
  // These keywords can be followed by a regex literal
  if (
    /(^|[^a-zA-Z0-9_$])(return|throw|case|yield|await|void|typeof|delete|else|do|in|of|new|default|instanceof)$/.test(
      trimmed,
    )
  ) {
    return true;
  }

  return false;
}

interface LintError {
  file: string;
  line: number;
  code: string;
  message: string;
  level: "error" | "warning";
}

/**
 * Strip comments and strings from code to avoid false positives.
 * Uses a single-pass state machine to correctly handle:
 * - Single-line comments
 * - Multi-line comments
 * - Single/double quoted strings
 * - Template literals with ${...} interpolations preserved
 *
 * For template literals, preserves ${...} interpolations so that code
 * inside them can still be analyzed (e.g., `run ${toolInput.command}`).
 *
 * Changelog:
 *   - silenvx/dekita#3426: Preserve template literal interpolations
 */
function stripCommentsAndStrings(content: string): string {
  const chars = [...content];
  const result: string[] = [];
  let i = 0;

  // Stack tracks the context. We start in CODE mode.
  // CODE: standard parsing (looking for strings, comments, templates, braces)
  // TEMPLATE: inside backticks (looking for ${, backticks, escapes)
  const stack: { type: "CODE" | "TEMPLATE"; braceDepth: number }[] = [
    { type: "CODE", braceDepth: 0 },
  ];

  while (i < chars.length) {
    const char = chars[i];
    const ctx = stack[stack.length - 1];

    if (ctx.type === "TEMPLATE") {
      if (char === "`") {
        stack.pop();
        result.push(" "); // Replace closing backtick
        i++;
      } else if (char === "$" && i + 1 < chars.length && chars[i + 1] === "{") {
        stack.push({ type: "CODE", braceDepth: 0 });
        // Replace ${ with " (" to mimic expression start context
        // This signals to isLikelyRegexStart that `/` after this is likely a regex
        result.push(" (");
        i += 2;
      } else if (char === "\\") {
        // Handle escape sequences in template
        if (i + 1 >= chars.length) {
          // Trailing backslash at end of content
          result.push(" ");
          i++;
        } else if (chars[i + 1] === "\n") {
          result.push("\n");
          i += 2;
        } else {
          result.push("  ");
          i += 2;
        }
      } else {
        // Template content - replace with space
        if (char === "\n") result.push("\n");
        else result.push(" ");
        i++;
      }
    } else {
      // CODE context

      // Check for string start (must be before } check since strings can contain })
      // Issue #3799 Codex review: Preserve quotes to prevent division-after-string misclassification as regex
      // e.g., `"foo" / bar` should become `"   " / bar` (not `      / bar`)
      if (char === '"' || char === "'") {
        const quote = char;
        result.push(quote); // Preserve opening quote
        i++;
        while (i < chars.length) {
          if (chars[i] === "\\") {
            if (i + 1 >= chars.length) {
              // Trailing backslash at end of content
              result.push(" ");
              i++;
              break;
            }
            if (chars[i + 1] === "\n") result.push("\n");
            else result.push("  ");
            i += 2;
            continue;
          }
          if (chars[i] === quote) {
            result.push(quote); // Preserve closing quote
            i++;
            break;
          }
          if (chars[i] === "\n") result.push("\n");
          else result.push(" ");
          i++;
        }
        continue;
      }

      // Check for comment start
      if (char === "/" && i + 1 < chars.length && chars[i + 1] === "/") {
        result.push("  ");
        i += 2;
        while (i < chars.length && chars[i] !== "\n") {
          result.push(" ");
          i++;
        }
        continue;
      }
      if (char === "/" && i + 1 < chars.length && chars[i + 1] === "*") {
        result.push("  ");
        i += 2;
        while (i < chars.length && !(chars[i] === "*" && chars[i + 1] === "/")) {
          if (chars[i] === "\n") result.push("\n");
          else result.push(" ");
          i++;
        }
        if (i < chars.length) {
          result.push("  ");
          i += 2;
        }
        continue;
      }

      // Check for regex literal start
      // Must be after comment check (// and /* take precedence)
      // Must be before } check since regex can contain }
      if (char === "/") {
        // Check if this is likely a regex (not division)
        if (isLikelyRegexStart(result)) {
          result.push(char); // Keep the opening /
          i++;
          // Skip regex content until closing /
          // Note: JS regex literals cannot contain unescaped newlines
          while (i < chars.length) {
            const c = chars[i];
            // Newline means this was likely a false positive (division, not regex)
            // JS regex literals cannot span multiple lines
            if (c === "\n" || c === "\r") {
              result.push(c);
              i++;
              break;
            }
            if (c === "\\") {
              // Escape sequence in regex - replace with spaces to avoid false positives
              if (i + 1 < chars.length) {
                result.push("  ");
                i += 2;
              } else {
                result.push(" ");
                i++;
                break;
              }
            } else if (c === "/") {
              result.push(c); // Keep closing /
              i++;
              // Skip flags (g, i, m, s, u, y, d, v)
              while (i < chars.length && /[gimsudvy]/.test(chars[i])) {
                result.push(chars[i]);
                i++;
              }
              break;
            } else if (c === "[") {
              // Character class - replace content with spaces
              result.push(" ");
              i++;
              while (i < chars.length && chars[i] !== "]") {
                // Newline in character class also indicates false positive
                if (chars[i] === "\n" || chars[i] === "\r") break;
                if (chars[i] === "\\") {
                  if (i + 1 < chars.length) {
                    result.push("  ");
                    i += 2;
                  } else {
                    result.push(" ");
                    i++;
                    break;
                  }
                } else {
                  result.push(" ");
                  i++;
                }
              }
              if (i < chars.length && chars[i] === "]") {
                result.push(" "); // Replace ]
                i++;
              }
            } else {
              result.push(" "); // Replace regex content with space
              i++;
            }
          }
          continue;
        }
      }

      // Check for end of interpolation '}'
      // Only valid if we are inside an interpolation (stack > 1) and braces are balanced
      if (char === "}" && stack.length > 1 && ctx.braceDepth === 0) {
        stack.pop();
        result.push(")"); // Balance the '(' introduced at the start of interpolation (${)
        i++;
        continue;
      }

      // Check for template start
      if (char === "`") {
        stack.push({ type: "TEMPLATE", braceDepth: 0 });
        result.push(" ");
        i++;
        continue;
      }

      // Track braces (prevent negative values for malformed code)
      if (char === "{") ctx.braceDepth++;
      else if (char === "}" && ctx.braceDepth > 0) ctx.braceDepth--;

      // Keep code
      result.push(char);
      i++;
    }
  }

  return result.join("");
}

/**
 * Check if a file has an async function named 'main'.
 * Uses pre-cleaned content to avoid false positives from commented code.
 */
function hasMainFunction(cleanContent: string): boolean {
  // Match both 'async function main' and 'function main'
  return /\bfunction\s+main\s*\(/.test(cleanContent);
}

/**
 * Check if a file has 'import.meta.main' guard.
 * Uses pre-cleaned content to avoid false positives from commented code.
 */
function hasImportMetaMainGuard(cleanContent: string): boolean {
  // Match 'if (import.meta.main)' pattern
  return /if\s*\(\s*import\.meta\.main\s*\)/.test(cleanContent);
}

/**
 * TSHOOK001: Check that TypeScript hooks with main() have import.meta.main guard.
 *
 * Hooks should use:
 *   if (import.meta.main) {
 *     main().catch(...)
 *   }
 *
 * This allows safe imports in test files without executing the hook.
 */
function checkImportMetaMainGuard(cleanContent: string, filepath: string): LintError[] {
  const errors: LintError[] = [];

  if (hasMainFunction(cleanContent) && !hasImportMetaMainGuard(cleanContent)) {
    // Find the line number of main function using clean content lines
    const cleanLines = cleanContent.split("\n");
    let mainLine = 0;
    for (let i = 0; i < cleanLines.length; i++) {
      if (/\bfunction\s+main\s*\(/.test(cleanLines[i])) {
        mainLine = i + 1;
        break;
      }
    }

    errors.push({
      file: filepath,
      line: mainLine,
      code: "TSHOOK001",
      message:
        "Missing import.meta.main guard. " +
        "Hooks with main() should wrap the call in: if (import.meta.main) { main().catch(...) } " +
        "This allows safe imports in test files.",
      level: "error",
    });
  }

  return errors;
}

/**
 * TSHOOK002: Check for process.cwd() usage in main function.
 *
 * Hooks should prefer data.cwd from hook input instead of process.cwd().
 * Claude Code provides the working directory in the hook input.
 *
 * Acceptable pattern:
 *   const hookCwd = (data.cwd as string | undefined) ?? process.cwd();
 *
 * Problematic pattern (in main function):
 *   const cwd = process.cwd();
 */
function checkProcessCwdUsage(cleanContent: string, filepath: string): LintError[] {
  const errors: LintError[] = [];
  const cleanLines = cleanContent.split("\n");

  // Find main function boundaries
  let inMainFunction = false;
  let braceCount = 0;
  let hasBraces = false;

  for (let i = 0; i < cleanLines.length; i++) {
    const cleanLine = cleanLines[i];

    // Detect entering main function (use clean line)
    if (/\bfunction\s+main\s*\(/.test(cleanLine)) {
      inMainFunction = true;
      braceCount = 0;
      hasBraces = false;
    }

    if (inMainFunction) {
      // Count braces using clean line (comments/strings stripped)
      const openBraces = (cleanLine.match(/{/g) || []).length;
      if (openBraces > 0) hasBraces = true;
      braceCount += openBraces;
      braceCount -= (cleanLine.match(/}/g) || []).length;

      // Check for process.cwd() usage (use clean line to ignore commented code)
      if (/process\.cwd\(\)/.test(cleanLine)) {
        // Allow if it's a fallback pattern: ?? process.cwd()
        // Check current line or if previous line ended with ??
        const prevCleanLine = i > 0 ? cleanLines[i - 1].trim() : "";
        const isFallback =
          /\?\?\s*process\.cwd\(\)/.test(cleanLine) ||
          (prevCleanLine.endsWith("??") && cleanLine.trim().startsWith("process.cwd()"));

        if (isFallback) {
          // Acceptable: used as fallback for data.cwd
          continue;
        }

        errors.push({
          file: filepath,
          line: i + 1,
          code: "TSHOOK002",
          message:
            "Direct process.cwd() usage in main function. " +
            "Prefer using data.cwd from hook input: " +
            "const hookCwd = (data.cwd as string | undefined) ?? process.cwd();",
          level: "warning",
        });
      }

      // Exit main function when braces are balanced (after at least one brace seen)
      if (hasBraces && braceCount === 0) {
        inMainFunction = false;
      }
    }
  }

  return errors;
}

/**
 * TSHOOK003: Check for direct tool_input field access without Zod schema.
 *
 * Hooks should use Zod schema for type-safe parsing of tool_input.
 *
 * Problematic pattern:
 *   const command = toolInput.command ?? "";
 *   const filePath = toolInput.file_path;
 *
 * Recommended pattern:
 *   const parseResult = Schema.safeParse(toolInput);
 *   const command = parseResult.success ? parseResult.data.command : "";
 */
function checkToolInputAccess(cleanContent: string, filepath: string): LintError[] {
  const errors: LintError[] = [];
  const cleanLines = cleanContent.split("\n");

  // Note: We intentionally check for direct toolInput.xxx access patterns.
  // Safe patterns like `Schema.safeParse(toolInput).data.xxx` do not match
  // this regex because they access `.data.xxx`, not `toolInput.xxx` directly.
  // This catches cases where Zod is imported but not properly used.

  // Look for toolInput.xxx or tool_input.xxx patterns (including optional chaining)
  const toolInputAccessPattern = /\b(?:toolInput|tool_input)\s*(?:\.|\.?\?\.)\s*(\w+)/g;
  // Focus on common fields from PR #3414 patterns. Intentionally limited to avoid
  // excessive warnings on custom fields that may have legitimate use cases.
  const commonFields = ["command", "file_path", "filePath", "content", "old_string", "new_string"];

  for (let i = 0; i < cleanLines.length; i++) {
    const cleanLine = cleanLines[i];
    const matches = cleanLine.matchAll(toolInputAccessPattern);

    for (const match of matches) {
      const field = match[1];
      // Check if accessing known tool input fields that require validation
      if (commonFields.includes(field)) {
        errors.push({
          file: filepath,
          line: i + 1,
          code: "TSHOOK003",
          message: `Direct tool_input.${field} access without Zod schema validation. Consider using Zod for type-safe parsing: const parseResult = Schema.safeParse(toolInput);`,
          level: "warning",
        });
        break; // One warning per line
      }
    }
  }

  return errors;
}

/**
 * Lint a single TypeScript hook file.
 */
function lintFile(filepath: string): LintError[] {
  // Skip test files
  const filename = basename(filepath);
  if (filename.includes(".test.") || filename.startsWith("test_")) {
    return [];
  }

  // Read file content
  let content: string;
  try {
    content = readFileSync(filepath, "utf-8");
  } catch (e) {
    return [
      {
        file: filepath,
        line: 0,
        code: "TSHOOK000",
        message: `Failed to read file: ${e}`,
        level: "error",
      },
    ];
  }

  // Skip if marked with skip comment
  if (content.includes("// hook-lint-ts: skip")) {
    return [];
  }

  // Pre-compute clean content (comments and strings stripped) once for all checks
  const cleanContent = stripCommentsAndStrings(content);

  // Run checks
  return [
    ...checkImportMetaMainGuard(cleanContent, filepath),
    ...checkProcessCwdUsage(cleanContent, filepath),
    ...checkToolInputAccess(cleanContent, filepath),
  ];
}

/**
 * Get all TypeScript hook files.
 */
function getHookFiles(hooksDir: string): string[] {
  const files: string[] = [];

  if (!existsSync(hooksDir)) {
    return files;
  }

  const entries = readdirSync(hooksDir);
  for (const entry of entries) {
    const fullPath = join(hooksDir, entry);
    const stat = statSync(fullPath);

    if (stat.isFile() && entry.endsWith(".ts")) {
      files.push(fullPath);
    }
  }

  return files;
}

/**
 * Print summary of errors by code.
 */
function printSummary(errors: LintError[], numFiles: number): void {
  if (errors.length === 0) {
    console.log(`No violations found in ${numFiles} file(s)`);
    return;
  }

  const codeCounts = errors.reduce<Record<string, number>>((acc, error) => {
    acc[error.code] = (acc[error.code] || 0) + 1;
    return acc;
  }, {});

  const breakdown = Object.entries(codeCounts)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([code, count]) => `${code}: ${count}`)
    .join(", ");

  console.log(`${errors.length} violations found (${breakdown})`);
}

/**
 * Main entry point.
 */
async function main(): Promise<number> {
  const args = process.argv.slice(2);
  const checkOnly = args.includes("--check-only");
  const warningsEnabled = args.includes("--warnings");
  const fileArgs = args.filter((a) => !a.startsWith("--"));

  // Get files to check
  const files =
    fileArgs.length > 0
      ? fileArgs.filter((f) => f.endsWith(".ts"))
      : getHookFiles(".claude/hooks/handlers");

  if (files.length === 0) {
    console.log("No TypeScript hook files found");
    return 0;
  }

  // Lint all files
  const allErrors = files.sort().flatMap(lintFile);

  // Output based on mode
  if (checkOnly) {
    printSummary(allErrors, files.length);
  } else {
    // Filter errors/warnings based on --warnings flag
    const filteredErrors = warningsEnabled
      ? allErrors
      : allErrors.filter((e) => e.level === "error");

    for (const error of filteredErrors) {
      const prefix = error.level === "warning" ? "[warning] " : "[error] ";
      console.log(`${error.file}:${error.line}: ${prefix}[${error.code}] ${error.message}`);
    }

    const errorCount = allErrors.filter((e) => e.level === "error").length;
    const warningCount = allErrors.filter((e) => e.level === "warning").length;

    if (filteredErrors.length > 0) {
      console.error(`\nFound ${errorCount} error(s), ${warningCount} warning(s)`);
    } else {
      console.log(`Checked ${files.length} file(s), no errors found`);
      if (!warningsEnabled && warningCount > 0) {
        console.log(`(${warningCount} warning(s) hidden, use --warnings to show)`);
      }
    }
  }

  return allErrors.some((e) => e.level === "error") ? 1 : 0;
}

// Execute
if (import.meta.main) {
  main()
    .then((code) => process.exit(code))
    .catch((e) => {
      console.error("Fatal error:", e);
      process.exit(1);
    });
}

// Export for testing
export {
  checkImportMetaMainGuard,
  checkProcessCwdUsage,
  checkToolInputAccess,
  hasImportMetaMainGuard,
  hasMainFunction,
  lintFile,
  stripCommentsAndStrings,
};
