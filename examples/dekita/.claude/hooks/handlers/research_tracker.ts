#!/usr/bin/env bun
/**
 * セッション内のWebSearch/WebFetch使用を追跡。
 *
 * Why:
 *   Issue/PR作成前に調査が行われたかを検証するため、
 *   Web検索活動をセッション単位で記録する必要がある。
 *
 * What:
 *   - WebSearch/WebFetchツール使用を検出
 *   - 検索クエリ/URLをセッションマーカーファイルに記録
 *   - research-requirement-checkが後で参照
 *
 * State:
 *   - writes: /tmp/claude-hooks/research-activity-{session_id}.json
 *
 * Remarks:
 *   - 非ブロック型（PostToolUse、記録のみ）
 *   - research-requirement-checkと連携
 *   - クエリは200文字に切り詰めて記録
 *   - Python版: research_tracker.py
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#2545: HookContextパターン移行
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { SESSION_DIR } from "../lib/constants";
import { logHookExecution } from "../lib/logging";
import { createContext, getSessionId, parseHookInput } from "../lib/session";

const HOOK_NAME = "research-tracker";

interface ResearchActivity {
  tool: string;
  query: string;
  timestamp: string;
}

interface ResearchData {
  activities: ResearchActivity[];
  session_start: string;
  last_updated?: string;
}

/**
 * Get session-specific research activity file path.
 */
function getResearchActivityFile(sessionId: string): string {
  return `${SESSION_DIR}/research-activity-${sessionId || "unknown"}.json`;
}

/**
 * Load existing research activity data or create empty structure.
 */
function loadResearchData(sessionId: string): ResearchData {
  const researchFile = getResearchActivityFile(sessionId);
  try {
    if (existsSync(researchFile)) {
      const content = readFileSync(researchFile, "utf-8");
      return JSON.parse(content);
    }
  } catch {
    // Corrupt or unreadable file - start fresh with empty structure
  }
  return {
    activities: [],
    session_start: new Date().toISOString(),
  };
}

/**
 * Save research activity data atomically.
 */
function saveResearchData(sessionId: string, data: ResearchData): void {
  const researchFile = getResearchActivityFile(sessionId);
  mkdirSync(SESSION_DIR, { recursive: true });
  const tempFile = `${researchFile}.tmp`;
  try {
    writeFileSync(tempFile, JSON.stringify(data, null, 2), "utf-8");
    renameSync(tempFile, researchFile);
  } catch {
    // Fail silently - tracking is non-critical
  }
}

/**
 * Record a research activity (WebSearch/WebFetch) to session marker.
 */
function recordResearchActivity(sessionId: string, toolName: string, query: string): void {
  const data = loadResearchData(sessionId);
  data.activities.push({
    tool: toolName,
    query: query.slice(0, 200), // Truncate to 200 chars
    timestamp: new Date().toISOString(),
  });
  data.last_updated = new Date().toISOString();
  saveResearchData(sessionId, data);
}

/**
 * Extract the search query or URL from tool input.
 */
export function extractQuery(toolInput: Record<string, unknown> | undefined): string {
  if (!toolInput || typeof toolInput !== "object") {
    return "";
  }
  // WebSearch uses 'query', WebFetch uses 'url'
  const query = toolInput.query;
  const url = toolInput.url;
  if (typeof query === "string" && query) {
    return query;
  }
  if (typeof url === "string" && url) {
    return url;
  }
  return "";
}

async function main(): Promise<void> {
  try {
    const inputData = await parseHookInput();
    const ctx = createContext(inputData);
    const sessionId = getSessionId(ctx) ?? "unknown";

    const toolName = inputData.tool_name ?? "";

    // Only track WebSearch and WebFetch
    if (toolName === "WebSearch" || toolName === "WebFetch") {
      const toolInput = inputData.tool_input as Record<string, unknown> | undefined;
      const query = extractQuery(toolInput);
      recordResearchActivity(sessionId, toolName, query);

      await logHookExecution(
        HOOK_NAME,
        "track",
        `Recorded ${toolName} activity`,
        { tool: toolName, query_preview: query.slice(0, 50) },
        { sessionId },
      );
    }

    // Always continue (PostToolUse hook)
    console.log(JSON.stringify({ continue: true }));
  } catch {
    // Invalid input or error - approve and exit
    console.log(JSON.stringify({ continue: true }));
  }
}

if (import.meta.main) {
  main();
}
