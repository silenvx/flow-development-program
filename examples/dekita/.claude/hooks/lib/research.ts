/**
 * リサーチ・探索活動の追跡ユーティリティ
 *
 * Why:
 *   PR作成前のリサーチ・コード探索が十分か判定するため、
 *   WebSearch/WebFetch/Read/Glob/Grepの使用状況を追跡する。
 *
 * What:
 *   - checkResearchDone(): リサーチ実施有無を判定
 *   - getExplorationDepth(): 探索深度（Read/Glob/Grep回数）を取得
 *   - getResearchSummary(): リサーチ活動のサマリーを取得
 *
 * State:
 *   - reads: {session_dir}/research-activity-{session}.json
 *   - reads: {session_dir}/exploration-depth-{session}.json
 *
 * Remarks:
 *   - MIN_EXPLORATION_FOR_BYPASS以上の探索で十分と判定
 *   - セッション毎にファイル分離で並行セッション対応
 *   - 破損ファイルは空として扱う（fail-open）
 *   - Python lib/research.py との互換性を維持
 *
 * Changelog:
 *   - silenvx/dekita#613: リサーチ追跡を追加
 *   - silenvx/dekita#617: セッションIDでファイル分離
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { MIN_EXPLORATION_FOR_BYPASS } from "./constants";

/**
 * Get session directory path.
 */
export function getSessionDir(): string {
  return join(process.env.TMPDIR ?? tmpdir(), "claude-hooks");
}

/**
 * Get session-specific research activity file path.
 */
export function getResearchActivityFile(sessionDir: string, sessionId?: string | null): string {
  const effectiveSessionId = sessionId ?? "unknown";
  return join(sessionDir, `research-activity-${effectiveSessionId}.json`);
}

/**
 * Get session-specific exploration file path.
 */
export function getExplorationFile(sessionDir: string, sessionId?: string | null): string {
  const effectiveSessionId = sessionId ?? "unknown";
  return join(sessionDir, `exploration-depth-${effectiveSessionId}.json`);
}

/**
 * Check if any research was done in this session.
 *
 * @param sessionDir - Directory where session files are stored.
 * @param sessionId - Session ID for file isolation.
 * @returns True if WebSearch or WebFetch was used, False otherwise.
 */
export function checkResearchDone(sessionDir: string, sessionId?: string | null): boolean {
  const researchFile = getResearchActivityFile(sessionDir, sessionId);
  try {
    if (existsSync(researchFile)) {
      const data = JSON.parse(readFileSync(researchFile, "utf-8"));
      const activities = data.activities ?? [];
      return activities.length > 0;
    }
  } catch {
    // Corrupt or unreadable file - treat as no research done
  }
  return false;
}

interface ResearchSummary {
  count: number;
  tools_used: string[];
  has_research: boolean;
}

/**
 * Get summary of research activities in session.
 *
 * @param sessionDir - Directory where session files are stored.
 * @param sessionId - Session ID for file isolation.
 * @returns Object with count, tools_used, has_research.
 */
export function getResearchSummary(sessionDir: string, sessionId?: string | null): ResearchSummary {
  const researchFile = getResearchActivityFile(sessionDir, sessionId);
  try {
    if (existsSync(researchFile)) {
      const data = JSON.parse(readFileSync(researchFile, "utf-8"));
      const activities = (data.activities ?? []) as Array<{ tool?: string }>;
      const toolsUsed = [
        ...new Set(activities.map((a) => a.tool).filter((t): t is string => Boolean(t))),
      ];
      return {
        count: activities.length,
        tools_used: toolsUsed,
        has_research: activities.length > 0,
      };
    }
  } catch {
    // Corrupt or unreadable file - return empty summary
  }
  return { count: 0, tools_used: [], has_research: false };
}

interface ExplorationCounts {
  Read: number;
  Glob: number;
  Grep: number;
  [key: string]: number;
}

interface ExplorationDepth {
  counts: ExplorationCounts;
  total: number;
  sufficient: boolean;
}

/**
 * Get current exploration depth stats.
 *
 * @param sessionDir - Directory where session files are stored.
 * @param sessionId - Session ID for file isolation.
 * @returns Object with counts, total, sufficient.
 */
export function getExplorationDepth(
  sessionDir: string,
  sessionId?: string | null,
): ExplorationDepth {
  const explorationFile = getExplorationFile(sessionDir, sessionId);
  try {
    if (existsSync(explorationFile)) {
      const data = JSON.parse(readFileSync(explorationFile, "utf-8"));
      const counts: ExplorationCounts = data.counts ?? {
        Read: 0,
        Glob: 0,
        Grep: 0,
      };
      const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
      return {
        counts,
        total,
        sufficient: total >= MIN_EXPLORATION_FOR_BYPASS,
      };
    }
  } catch {
    // Corrupt or unreadable file - return empty exploration stats
  }
  return {
    counts: { Read: 0, Glob: 0, Grep: 0 },
    total: 0,
    sufficient: false,
  };
}
