#!/usr/bin/env bun
/**
 * Bash失敗時にドキュメント参照の古さを検出して警告する。
 *
 * Why:
 *   削除されたスクリプトがドキュメントに参照として残っていると、
 *   そのドキュメントを信じて実行したコマンドが失敗する。早期検知が必要。
 *
 * What:
 *   - Bashコマンド失敗時にトランスクリプトを分析
 *   - 最近読み込んだ.mdファイルで失敗コマンドを検索
 *   - ドキュメントに記載されていた場合は古い可能性を警告
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、systemMessageで警告）
 *   - PostToolUse:Bashで発火（exit_code != 0時のみ）
 *   - "No such file or directory"エラー時のみ動作
 *   - .claude/scripts/, .claude/hooks/のパターンを検索
 *
 * Changelog:
 *   - silenvx/dekita#2213: 発端となった問題
 *   - silenvx/dekita#2220: フック追加
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { existsSync, readFileSync } from "node:fs";
import { relative, resolve } from "node:path";
import { logHookExecution } from "../lib/logging";
import { getBashCommand, getToolResultAsObject, parseHookInput } from "../lib/session";

const HOOK_NAME = "doc-reference-warning";

export interface TranscriptEntry {
  type?: string;
  name?: string;
  input?: {
    file_path?: string;
  };
}

/**
 * Read and parse the JSONL transcript file.
 */
function readTranscript(transcriptPath: string): TranscriptEntry[] {
  const entries: TranscriptEntry[] = [];
  try {
    if (!existsSync(transcriptPath)) {
      return entries;
    }
    const content = readFileSync(transcriptPath, "utf-8");
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (trimmed) {
        try {
          entries.push(JSON.parse(trimmed) as TranscriptEntry);
        } catch {
          // Error ignored - fail-open pattern
        }
      }
    }
  } catch {
    // File read errors are non-fatal; return empty list
  }
  return entries;
}

/**
 * Extract .md file paths from Read tool calls in transcript.
 */
export function extractReadMdFiles(transcript: TranscriptEntry[]): string[] {
  const mdFiles: string[] = [];
  for (const entry of transcript) {
    // Look for tool_use entries with Read tool
    if (entry.type === "tool_use" && entry.name === "Read") {
      const filePath = entry.input?.file_path || "";
      if (filePath.endsWith(".md")) {
        mdFiles.push(filePath);
      }
    }
  }
  return mdFiles;
}

/**
 * Extract a searchable pattern from the failed command.
 *
 * Focus on script paths like:
 * - .claude/scripts/xxx.py
 * - .claude/hooks/xxx.py
 * - scripts/xxx.sh
 */
export function extractCommandPattern(command: string): string | null {
  // Match .claude/scripts/*.py or .claude/scripts/*.sh
  let match = command.match(/\.claude\/scripts\/[\w-]+\.(py|sh)/);
  if (match) {
    return match[0];
  }

  // Match .claude/hooks/*.py
  match = command.match(/\.claude\/hooks\/[\w-]+\.py/);
  if (match) {
    return match[0];
  }

  // Match scripts/*.sh at root
  match = command.match(/scripts\/[\w-]+\.sh/);
  if (match) {
    return match[0];
  }

  // Match any python3 .claude/... path
  match = command.match(/\.claude\/[\w/\-]+\.(py|sh)/);
  if (match) {
    return match[0];
  }

  return null;
}

/**
 * Search for pattern in file and return matching line numbers.
 */
function searchPatternInFile(filePath: string, pattern: string): number[] {
  const matchingLines: number[] = [];
  try {
    if (!existsSync(filePath)) {
      return matchingLines;
    }
    const content = readFileSync(filePath, "utf-8");
    const lines = content.split("\n");
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].includes(pattern)) {
        matchingLines.push(i + 1);
      }
    }
  } catch {
    // File read errors are non-fatal; return empty list
  }
  return matchingLines;
}

/**
 * Get project root from CLAUDE_PROJECT_DIR or fallback.
 */
function getProjectRoot(): string {
  const projectDir = process.env.CLAUDE_PROJECT_DIR;
  if (projectDir) {
    return projectDir;
  }
  // Fallback to current directory
  return process.cwd();
}

async function main(): Promise<void> {
  const result: { continue: boolean; systemMessage?: string } = { continue: true };
  let sessionId: string | undefined;

  try {
    const inputData = await parseHookInput();
    sessionId = inputData.session_id;
    const toolResult = getToolResultAsObject(inputData);
    const command = getBashCommand(inputData);

    const exitCode = typeof toolResult.exit_code === "number" ? toolResult.exit_code : 0;
    const stderr = typeof toolResult.stderr === "string" ? toolResult.stderr : "";

    // Only process failed commands
    if (exitCode === 0) {
      logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Check for "No such file or directory" type errors
    if (
      !stderr.includes("No such file or directory") &&
      !stderr.toLowerCase().includes("not found")
    ) {
      logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Extract searchable pattern from command
    const pattern = extractCommandPattern(command);
    if (!pattern) {
      logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Read transcript
    const transcriptPath = (inputData.transcript_path as string) || "";
    if (!transcriptPath) {
      logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    const transcript = readTranscript(transcriptPath);
    if (transcript.length === 0) {
      logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Extract recently read .md files
    const mdFiles = extractReadMdFiles(transcript);
    if (mdFiles.length === 0) {
      logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      console.log(JSON.stringify(result));
      return;
    }

    // Search for the pattern in read .md files
    const projectRoot = getProjectRoot();
    const foundReferences: string[] = [];

    for (const mdFile of mdFiles) {
      const matchingLines = searchPatternInFile(mdFile, pattern);
      if (matchingLines.length > 0) {
        let displayPath: string;
        try {
          displayPath = relative(projectRoot, resolve(mdFile));
        } catch {
          displayPath = mdFile;
        }
        for (const lineNum of matchingLines) {
          foundReferences.push(`  - ${displayPath}:${lineNum}`);
        }
      }
    }

    if (foundReferences.length > 0) {
      // Deduplicate references
      const uniqueRefs = [...new Set(foundReferences)].slice(0, 5);
      const message = `[${HOOK_NAME}] ドキュメント参照の確認が必要かもしれません\n\n失敗したコマンド内のパス \`${pattern}\` は以下のドキュメントに記載されています:\n${uniqueRefs.join("\n")}\n\nドキュメントが古い可能性があります。確認・修正を検討してください。`;
      result.systemMessage = message;
      logHookExecution(
        HOOK_NAME,
        "approve",
        "outdated_doc_reference_detected",
        {
          pattern,
          references: uniqueRefs,
        },
        { sessionId },
      );
    } else {
      logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
    }
  } catch {
    // Don't block Claude Code on hook failure
    logHookExecution(HOOK_NAME, "approve", "hook_error", undefined, { sessionId });
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
