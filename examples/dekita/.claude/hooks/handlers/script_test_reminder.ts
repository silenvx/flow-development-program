#!/usr/bin/env bun
/**
 * PostToolUse hook: Remind to add tests when new functions are added to scripts.
 *
 * Why:
 *   When editing .claude/scripts/*.py files and adding new function
 *   definitions, this hook reminds the developer to add corresponding tests
 *   if a test file already exists.
 *
 * What:
 *   - Single responsibility: Remind to add tests when new functions are added
 *   - Uses systemMessage (non-blocking) as this is a reminder, not enforcement
 *   - Does not overlap with existing hooks
 *
 * Remarks:
 *   - PostToolUse:Edit hookとして発火
 *   - .claude/scripts/*.pyファイルへの編集時のみ対象
 *
 * Changelog:
 *   - silenvx/dekita#1247: フック追加
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { existsSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { logHookExecution } from "../lib/logging";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "script-test-reminder";

/**
 * Detect newly added function definitions (including async functions and class methods).
 *
 * Uses occurrence counting to detect new methods even when a method with the same name
 * already exists (e.g., `__init__`, `run` in different classes).
 *
 * @param oldString - Original code content
 * @param newString - New code content after edit
 * @returns List of newly added function names (deduplicated and sorted)
 */
export function detectNewFunctions(oldString: string, newString: string): string[] {
  // Helper to extract all function names as an array (preserving duplicates)
  const getFuncs = (str: string): string[] => {
    const funcs: string[] = [];
    for (const match of str.matchAll(/^\s*(?:async\s+)?def\s+(\w+)\s*\(/gm)) {
      funcs.push(match[1]);
    }
    return funcs;
  };

  const oldFuncs = getFuncs(oldString);
  const newFuncs = getFuncs(newString);

  // Count occurrences in old string
  const oldCounts = new Map<string, number>();
  for (const func of oldFuncs) {
    oldCounts.set(func, (oldCounts.get(func) || 0) + 1);
  }

  // Track new occurrences and detect additions
  const added: string[] = [];
  const newCounts = new Map<string, number>();

  for (const func of newFuncs) {
    const count = (newCounts.get(func) || 0) + 1;
    newCounts.set(func, count);
    // If this occurrence count exceeds old count, it's a new addition
    if (count > (oldCounts.get(func) || 0)) {
      added.push(func);
    }
  }

  // Return unique names, sorted
  return [...new Set(added)].sort();
}

/**
 * Find corresponding test file for a script.
 *
 * @param scriptPath - Path to the script file
 * @returns Path to test file if it exists, null otherwise
 */
function findTestFile(scriptPath: string): string | null {
  // Convert hyphens to underscores for test file naming convention
  const stem = basename(scriptPath, ".py");
  const normalizedName = stem.replace(/-/g, "_");
  const testFile = join(dirname(scriptPath), "tests", `test_${normalizedName}.py`);
  return existsSync(testFile) ? testFile : null;
}

interface ContinueResult {
  continue: boolean;
  systemMessage?: string;
}

async function main(): Promise<void> {
  const result: ContinueResult = { continue: true };

  try {
    const inputData = await parseHookInput();
    const ctx = createHookContext(inputData);
    const toolInput = inputData.tool_input || {};
    const filePath = (toolInput as { file_path?: string }).file_path || "";

    // Only target .claude/scripts/*.py files
    if (!filePath.includes(".claude/scripts/") || !filePath.endsWith(".py")) {
      logHookExecution(HOOK_NAME, "approve", "not a script file", {
        session_id: ctx.sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Exclude files in tests directory
    if (filePath.includes("/tests/")) {
      logHookExecution(HOOK_NAME, "approve", "test file excluded", {
        session_id: ctx.sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    const oldString = (toolInput as { old_string?: string }).old_string || "";
    const newString = (toolInput as { new_string?: string }).new_string || "";

    // Detect new function definitions
    const newFunctions = detectNewFunctions(oldString, newString);

    if (newFunctions.length === 0) {
      logHookExecution(HOOK_NAME, "approve", "no new functions detected", {
        session_id: ctx.sessionId,
      });
      console.log(JSON.stringify(result));
      return;
    }

    // Check if corresponding test file exists
    const testFile = findTestFile(filePath);

    if (testFile) {
      const funcList = newFunctions.join(", ");
      result.systemMessage = `💡 テストファイルが存在します: ${testFile}\n   新しい関数を追加した場合、テストも追加してください。\n   追加された関数: ${funcList}`;
      logHookExecution(HOOK_NAME, "approve", `New functions: ${funcList}`, {
        file: filePath,
        test_file: testFile,
        functions: newFunctions,
      });
    }
  } catch {
    // Never fail the hook - just skip reminder
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
