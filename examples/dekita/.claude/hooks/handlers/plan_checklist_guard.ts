#!/usr/bin/env bun
/**
 * 計画ファイルの未完了チェックリストを検出する。
 *
 * Why:
 *   Phase完了時に計画書のチェックリストを自動検証し、
 *   見落としを防止する。Issue #2849の振り返りで発見。
 *
 * What:
 *   - ~/.claude/plans/*.md と .claude/plans/*.md をスキャン
 *   - 未完了チェックボックス（- [ ], * [ ], + [ ], 1. [ ]）を検出
 *   - 警告を出力（ブロックしない）
 *
 * State:
 *   - reads: ~/.claude/plans/*.md
 *   - reads: .claude/plans/*.md
 *
 * Remarks:
 *   - Stopフックとして実行
 *   - コードブロック内のチェックボックスは除外
 *   - 直近1時間以内に更新されたファイルのみ対象
 *
 * Changelog:
 *   - silenvx/dekita#2853: フック追加
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { formatError } from "../lib/format_error";
import { parseHookInput } from "../lib/session";

const ONE_HOUR_SECONDS = 3600;
const MAX_ITEM_LENGTH = 60;
const TRUNCATE_LENGTH = 57;

interface IncompleteResult {
  file: string;
  items: string[];
}

/**
 * Split content by fenced code blocks.
 * Returns alternating segments: [outside, inside, outside, inside, ...]
 */
function splitByFencedCodeBlocks(content: string): Array<[boolean, string[]]> {
  const lines = content.split("\n");
  const segments: Array<[boolean, string[]]> = [];
  let currentLines: string[] = [];
  let inFencedBlock = false;
  let currentFence: string | null = null;

  for (const line of lines) {
    // Check for fence (``` or ~~~)
    const fenceMatch = line.match(/^\s*(`{3,}|~{3,})/);
    if (fenceMatch) {
      const fence = fenceMatch[1];
      if (inFencedBlock) {
        // Check if this fence closes the current block
        // Closing fence must be same character and at least as long as opening fence
        if (currentFence && fence[0] === currentFence[0] && fence.length >= currentFence.length) {
          currentLines.push(line);
          segments.push([true, currentLines]);
          currentLines = [];
          inFencedBlock = false;
          currentFence = null;
        } else {
          // Nested block or mismatched fence - treat as content
          currentLines.push(line);
        }
      } else {
        // Start of fenced block
        if (currentLines.length > 0) {
          segments.push([false, currentLines]);
        }
        currentLines = [line];
        inFencedBlock = true;
        currentFence = fence;
      }
    } else {
      currentLines.push(line);
    }
  }

  // Handle remaining lines
  if (currentLines.length > 0) {
    segments.push([inFencedBlock, currentLines]);
  }

  return segments;
}

/**
 * Check if a line is in an indented code block.
 */
function isInIndentedCodeBlock(lines: string[], lineIdx: number, isFirstSegment: boolean): boolean {
  const line = lines[lineIdx];

  // Not indented code if line doesn't start with 4+ spaces or tab
  if (!/^(\s{4,}|\t)/.test(line)) {
    return false;
  }

  // Look for a blank line before this indented block
  // (required for indented code blocks in markdown)
  for (let i = lineIdx - 1; i >= 0; i--) {
    const prevLine = lines[i].trim();
    if (prevLine === "") {
      // Found blank line, could be indented code
      return true;
    }
    if (/^(\s{4,}|\t)/.test(lines[i])) {
      // Previous line is also indented, continue checking
      continue;
    }
    // Previous line is not blank and not indented, this is just a nested list
    return false;
  }

  // Reached start of segment
  // If first segment, need blank line before to be code block
  // If not first segment, could be continuation of non-code content
  return isFirstSegment;
}

/**
 * Extract incomplete checklist items from content.
 */
function extractIncompleteItems(content: string): string[] {
  const items: string[] = [];
  const segments = splitByFencedCodeBlocks(content);
  let isFirstSegment = true;

  for (const [inFencedCodeBlock, segmentLines] of segments) {
    if (inFencedCodeBlock) {
      isFirstSegment = false;
      continue;
    }

    for (let lineIdx = 0; lineIdx < segmentLines.length; lineIdx++) {
      const line = segmentLines[lineIdx];

      // Skip if in indented code block
      if (isInIndentedCodeBlock(segmentLines, lineIdx, isFirstSegment)) {
        continue;
      }

      // Match incomplete checkbox list item
      const match = line.match(/^\s*(?:[-*+]|\d+\.)\s+\[\s*\]\s*(.+)/);
      if (match) {
        let itemText = match[1].trim();
        if (itemText.length > MAX_ITEM_LENGTH) {
          itemText = `${itemText.slice(0, TRUNCATE_LENGTH)}...`;
        }
        items.push(itemText);
      }
    }

    isFirstSegment = false;
  }

  return items;
}

/**
 * Find incomplete checklists in plan files.
 */
function findIncompleteChecklists(planDir: string): IncompleteResult[] {
  if (!existsSync(planDir)) {
    return [];
  }

  const results: IncompleteResult[] = [];
  const now = Date.now();
  const oneHourAgo = now - ONE_HOUR_SECONDS * 1000;

  try {
    const files = readdirSync(planDir);
    for (const file of files) {
      if (!file.endsWith(".md")) continue;

      const filePath = join(planDir, file);
      try {
        const stat = statSync(filePath);
        if (stat.mtimeMs < oneHourAgo) {
          continue;
        }

        const content = readFileSync(filePath, "utf-8");
        const incompleteItems = extractIncompleteItems(content);

        if (incompleteItems.length > 0) {
          results.push({
            file: filePath,
            items: incompleteItems,
          });
        }
      } catch {
        // 意図的に空 - ファイル読み取りエラーはスキップで対応
      }
    }
  } catch {
    // 意図的に空 - ディレクトリ読み取りエラーは空結果を返すことで対応
  }

  return results;
}

async function main(): Promise<void> {
  const hookInput = await parseHookInput();

  // Only run on Stop hook
  // Use stop_hook_active (preferred) or hook_event_name to detect Stop hook
  // Note: hook_type is not in HookInputSchema, so we use these fields instead
  const isStopHook = hookInput.stop_hook_active === true || hookInput.hook_event_name === "Stop";
  if (!isStopHook) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // Check both ~/.claude/plans/ and .claude/plans/
  const homePlansDir = join(homedir(), ".claude", "plans");
  const projectPlansDir = join(process.cwd(), ".claude", "plans");

  const incomplete = findIncompleteChecklists(homePlansDir);
  incomplete.push(...findIncompleteChecklists(projectPlansDir));

  if (incomplete.length === 0) {
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // Output warning
  const warningLines = ["[plan-checklist-guard] ⚠️ 計画ファイルに未完了項目があります:", ""];

  for (const result of incomplete) {
    const fileName = result.file.split("/").pop() ?? result.file;
    warningLines.push(`📋 ${fileName}:`);
    for (const item of result.items.slice(0, 5)) {
      warningLines.push(`  - [ ] ${item}`);
    }
    if (result.items.length > 5) {
      warningLines.push(`  ... 他 ${result.items.length - 5} 件`);
    }
    warningLines.push("");
  }

  warningLines.push("Phase完了前にチェックリストを確認してください。");

  // Print warning to stderr
  console.error(warningLines.join("\n"));

  // Approve (don't block session end)
  console.log(JSON.stringify({ continue: true }));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[plan-checklist-guard] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({ continue: true }));
  });
}
