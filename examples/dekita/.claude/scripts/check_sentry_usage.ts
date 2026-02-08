#!/usr/bin/env bun
/**
 * Sentryスコープリークパターンを検出する。
 *
 * Why:
 *   Cloudflare WorkersのisolateモデルでSentry.setTag()等を
 *   withScope()外で使用するとリクエスト間でリークするため。
 *
 * What:
 *   - checkFile(): ファイル内の禁止パターンを検出
 *   - main(): worker/src配下をスキャン
 *
 * Remarks:
 *   - 正しいパターン: Sentry.withScope((scope) => { scope.setTag(...); })
 *   - 検出対象: setTag, setContext, setUser, setExtra
 *
 * Changelog:
 *   - silenvx/dekita#1100: Sentryスコープリーク検出機能を追加
 *   - silenvx/dekita#3636: TypeScriptに移植
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Glob } from "bun";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Patterns that indicate potential scope leaks
// These should never be used directly in worker code
export const BANNED_PATTERNS: Array<[RegExp, string]> = [
  [/\bSentry\.setTag\s*\(/, "Sentry.setTag()"],
  [/\bSentry\.setContext\s*\(/, "Sentry.setContext()"],
  [/\bSentry\.setUser\s*\(/, "Sentry.setUser()"],
  [/\bSentry\.setExtra\s*\(/, "Sentry.setExtra()"],
];

// Directory to check (resolved relative to script location)
const WORKER_SRC = join(__dirname, "..", "..", "worker/src");

export interface Violation {
  lineNumber: number;
  lineContent: string;
  patternName: string;
}

/**
 * Check if the match position is inside a // comment.
 * Ignores // preceded by : (e.g., https://).
 * Ignores // inside string literals (single, double, or backtick quotes).
 * Ignores // only inside regex character classes within regex literals (e.g., /foo[//]bar/).
 * Does not handle block comments or nested template literals.
 */
export function isInComment(line: string, matchStart: number): boolean {
  // Step 1: Remove regex literals first (they may contain quotes)
  // Pattern: /.../ with optional flags, handling escaped characters and character classes
  // Regex must be preceded by: operators, keywords (return, case, etc.), or start of line
  // Also supports ), ], } to handle regex after function calls and array/object access
  // This helps distinguish from division operator
  // Character classes [...] are handled separately to allow slashes inside them
  const regexPattern =
    /(^|[=(:,;!|&?:\[{>+\-*%^~<)\]}]|\b(?:return|case|throw|else|new|typeof|void|yield|await))(\s*\/(?:\[(?:\\.|[^\]\\])*\]|\\.|[^/\\\n;])+\/[a-zA-Z]*)/g;
  const withoutRegex = line.replace(
    regexPattern,
    (_match, prefix, regexPart) => (prefix || "") + " ".repeat(regexPart.length),
  );

  // Step 2: Remove string literals to avoid false positives
  // Replace quoted strings with spaces of same length to preserve positions
  const withoutStrings = withoutRegex.replace(/(['"`])(?:\\.|(?!\1)[^\\])*\1/g, (match) =>
    " ".repeat(match.length),
  );

  // Find // that is NOT preceded by : (to exclude URLs like https://)
  const commentMatch = withoutStrings.match(/(?<!:)\/\//);
  const commentPos = commentMatch?.index ?? -1;
  return commentPos !== -1 && commentPos < matchStart;
}

/**
 * Check a file for banned patterns.
 * Returns list of violations.
 * Skips patterns that appear inside // comments.
 */
export function checkFile(filePath: string): Violation[] {
  const violations: Violation[] = [];

  try {
    const content = readFileSync(filePath, "utf-8");
    const lines = content.split("\n");

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const lineNumber = i + 1;

      for (const [pattern, name] of BANNED_PATTERNS) {
        const match = pattern.exec(line);
        if (match && !isInComment(line, match.index)) {
          violations.push({
            lineNumber,
            lineContent: line.trim(),
            patternName: name,
          });
        }
      }
    }
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    console.error(`⚠️ Warning: Could not read ${filePath}: ${message}`);
  }

  return violations;
}

async function main(): Promise<number> {
  if (!existsSync(WORKER_SRC)) {
    console.log(`Directory not found: ${WORKER_SRC}`);
    return 1;
  }

  const allViolations = new Map<string, Violation[]>();

  const glob = new Glob("**/*.ts");
  for await (const file of glob.scan(WORKER_SRC)) {
    const filePath = join(WORKER_SRC, file);
    const violations = checkFile(filePath);
    if (violations.length > 0) {
      allViolations.set(filePath, violations);
    }
  }

  if (allViolations.size === 0) {
    console.log("✅ No Sentry scope leak patterns detected");
    return 0;
  }

  console.log("❌ Sentry scope leak patterns detected!\n");
  console.log("The following methods should NOT be used directly in Cloudflare Workers:");
  console.log("  - Sentry.setTag()");
  console.log("  - Sentry.setContext()");
  console.log("  - Sentry.setUser()");
  console.log("  - Sentry.setExtra()");
  console.log("\nUse Sentry.withScope() instead:");
  console.log("  Sentry.withScope((scope) => {");
  console.log('    scope.setTag("key", "value");');
  console.log("    Sentry.captureException(err);");
  console.log("  });");
  console.log("\nViolations found:\n");

  const sortedFiles = [...allViolations.keys()].sort();
  for (const filePath of sortedFiles) {
    const violations = allViolations.get(filePath)!;
    for (const { lineNumber, lineContent, patternName } of violations) {
      console.log(`  ${filePath}:${lineNumber}: ${patternName}`);
      console.log(`    ${lineContent}\n`);
    }
  }

  return 1;
}

// Only run main when executed directly (not when imported for testing)
if (import.meta.main) {
  main()
    .then((code) => process.exit(code))
    .catch((error) => {
      console.error("Error:", error);
      process.exit(1);
    });
}
