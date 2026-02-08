#!/usr/bin/env bun
/**
 * gh issue create時にIssue本文の必須項目をチェックする。
 *
 * Why:
 *   Creating issues without investigation causes delays during implementation.
 *   Enforce required sections (Why/What/How) to encourage proper investigation
 *   at issue creation time.
 *
 * What:
 *   - Detect gh issue create commands
 *   - Extract body from --body option
 *   - Check for required sections (Why/What/How)
 *   - Block if missing
 *
 * Remarks:
 *   - trivial/documentationラベルでスキップ可能
 *   - issue-investigation-reminderを置き換えた後継フック
 *   - Python版: issue_body_requirements_check.py
 *
 * Changelog:
 *   - silenvx/dekita#2455: フック追加
 *   - silenvx/dekita#2917: TypeScript版初期実装
 *   - silenvx/dekita#3052: 汎用オプションパーサー導入、-F=形式対応
 */

import { existsSync, readFileSync } from "node:fs";
import { basename, relative, resolve } from "node:path";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import {
  type OptionDef,
  getOptionValue,
  getOptionValues,
  parseOptions,
  skipEnvPrefixes,
  tokenize,
} from "../lib/option_parser";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "issue-body-requirements-check";

// Required sections patterns: [name, pattern, description]
const REQUIRED_SECTIONS: Array<[string, RegExp, string]> = [
  [
    "Why",
    /^(?:##|###)\s*(?:Why|Motivation|Background|Reason)/im,
    "Describe the motivation/background for this issue",
  ],
  [
    "What",
    /^(?:##|###)\s*(?:What|Current|Actual|Status)/im,
    "Describe the current state or what this issue is about",
  ],
  ["How", /^(?:##|###)\s*(?:How|Expected|Proposed|Solution)/im, "Describe the proposed solution"],
];

// Labels that skip the check
const SKIP_LABELS = ["trivial", "documentation", "docs"];

// オプション定義（宣言的に管理）
const OPTION_DEFS: OptionDef[] = [
  { long: "body", short: "b", hasValue: true },
  { long: "body-file", short: "F", hasValue: true },
  { long: "label", short: "l", hasValue: true, multiple: true },
];

/**
 * Check if a token represents the gh command (bare name or full path).
 */
function isGhCommand(token: string): boolean {
  return basename(token) === "gh";
}

/**
 * Check if command is gh issue create.
 */
function isGhIssueCreateCommand(command: string): boolean {
  const tokens = tokenize(command);
  if (tokens.length === 0) {
    return false;
  }

  const remaining = skipEnvPrefixes(tokens);

  if (remaining.length < 3) {
    return false;
  }
  return isGhCommand(remaining[0]) && remaining[1] === "issue" && remaining[2] === "create";
}

/**
 * Extract --body or --body-file option value from gh issue create command.
 */
function extractBodyFromCommand(command: string): string | null {
  const tokens = tokenize(command);
  if (tokens.length === 0) {
    return null;
  }

  const options = parseOptions(tokens, OPTION_DEFS);

  // --body takes precedence over --body-file
  const body = getOptionValue(options, "body");
  if (body !== null) {
    return body;
  }

  // If body-file was specified, read the file content with path traversal protection
  const bodyFile = getOptionValue(options, "body-file");
  if (bodyFile) {
    return readBodyFile(bodyFile);
  }

  return null;
}

/**
 * Read body content from file with path traversal protection.
 */
function readBodyFile(bodyFile: string): string | null {
  try {
    const safeDirectory = resolve(process.cwd());
    const filePath = resolve(bodyFile);

    // Path traversal protection: only allow files within cwd
    const rel = relative(safeDirectory, filePath);
    if (rel.startsWith("..") || rel.startsWith("/")) {
      // Path is outside the safe directory, reject it
      return null;
    }

    if (existsSync(filePath)) {
      return readFileSync(filePath, "utf-8");
    }
  } catch {
    // If file cannot be read, return null (will trigger block)
  }
  return null;
}

/**
 * Extract --label option values from gh issue create command.
 */
function extractLabelsFromCommand(command: string): string[] {
  const tokens = tokenize(command);
  if (tokens.length === 0) {
    return [];
  }

  const options = parseOptions(tokens, OPTION_DEFS);
  const labels = getOptionValues(options, "label");

  // Split comma-separated labels and normalize to lowercase
  return labels.flatMap((label) => label.split(",")).map((label) => label.trim().toLowerCase());
}

/**
 * Check if the requirements check should be skipped.
 */
function shouldSkipCheck(command: string): boolean {
  // Skip if trivial or documentation label is present
  const labels = extractLabelsFromCommand(command);
  return labels.some((label) => SKIP_LABELS.includes(label));
}

/**
 * Check if body contains all required sections.
 */
function checkRequiredSections(body: string): Array<{ name: string; description: string }> {
  const missing: Array<{ name: string; description: string }> = [];
  for (const [name, pattern, description] of REQUIRED_SECTIONS) {
    if (!pattern.test(body)) {
      missing.push({ name, description });
    }
  }
  return missing;
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const data = await parseHookInput();
    sessionId = data.session_id;
    const toolInput = (data.tool_input as Record<string, unknown>) || {};
    const command = (toolInput.command as string) || "";

    // Only check gh issue create commands
    if (!isGhIssueCreateCommand(command)) {
      process.exit(0);
    }

    // Check skip conditions
    if (shouldSkipCheck(command)) {
      const result = makeApproveResult(HOOK_NAME, "Skip condition matched (trivial/documentation)");
      await logHookExecution(HOOK_NAME, "approve", "skip condition matched", undefined, {
        sessionId,
      });
      console.log(JSON.stringify(result));
      process.exit(0);
    }

    const body = extractBodyFromCommand(command);

    // Block if no body
    if (!body) {
      const message = [
        "🚫 Issue body (--body) is not specified.",
        "",
        "Please include the following sections:",
        "- ## Why (motivation/background)",
        "- ## What (current state)",
        "- ## How (proposed solution)",
        "",
        "To skip, add --label trivial or --label documentation",
      ].join("\n");
      const result = makeBlockResult(HOOK_NAME, message);
      await logHookExecution(HOOK_NAME, "block", "no body specified", undefined, { sessionId });
      console.log(JSON.stringify(result));
      process.exit(2);
    }

    // Check required sections
    const missing = checkRequiredSections(body);
    if (missing.length > 0) {
      const missingList = missing
        .map(({ name, description }) => `- ${name}: ${description}`)
        .join("\n");
      const message = [
        "🚫 Issue body is missing required sections.",
        "",
        "Missing sections:",
        missingList,
        "",
        "To skip, add --label trivial or --label documentation",
      ].join("\n");
      const result = makeBlockResult(HOOK_NAME, message);
      await logHookExecution(
        HOOK_NAME,
        "block",
        `missing sections: ${JSON.stringify(missing.map((m) => m.name))}`,
        undefined,
        { sessionId },
      );
      console.log(JSON.stringify(result));
      process.exit(2);
    }

    // All required sections present
    const result = makeApproveResult(HOOK_NAME, "Issue body requirements verified");
    await logHookExecution(HOOK_NAME, "approve", "all required sections present", undefined, {
      sessionId,
    });
    console.log(JSON.stringify(result));
    process.exit(0);
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

// Only run main when executed directly, not when imported
if (import.meta.main) {
  main();
}

// Export for testing
export {
  isGhIssueCreateCommand,
  extractBodyFromCommand,
  extractLabelsFromCommand,
  shouldSkipCheck,
  checkRequiredSections,
  readBodyFile, // テスト用にエクスポート（内部ヘルパー）
};
