#!/usr/bin/env bun
/**
 * AGENTS.md編集時にルール間の矛盾を検知する。
 *
 * Why:
 *   AGENTS.mdが大きくなると、あるセクションで「即座に実行」と書きつつ
 *   別セクションで「確認を求める」と書くような矛盾が生じやすい。
 *
 * What:
 *   - AGENTS.md編集時に発火
 *   - テーブル形式のルール行を抽出
 *   - 対立キーワードペアによる簡易矛盾検知
 *   - 矛盾候補をsystemMessageで警告
 *
 * Remarks:
 *   - PostToolUse:Edit/Write（AGENTS.mdの場合）で発火
 *   - 警告型フック（exit 0 + systemMessage）
 *   - キーワードベースのヒューリスティック検知
 *
 * Changelog:
 *   - silenvx/dekita#3976: 初期実装
 */

import { readFileSync } from "node:fs";
import { relative, resolve } from "node:path";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { approveAndExit } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "rule-consistency-check";

// Contradictory keyword pairs
export const CONTRADICTION_PAIRS: [string, string][] = [
  ["禁止", "推奨"],
  ["即座", "確認"],
  ["必須", "不要"],
  ["ブロック", "警告のみ"],
  ["自動", "手動"],
  ["NEVER", "SHOULD"],
  ["MUST", "OPTIONAL"],
];

// Auto-generate keyword pattern from CONTRADICTION_PAIRS (longer strings first to avoid partial matches)
const ALL_KEYWORDS = [...new Set(CONTRADICTION_PAIRS.flat())].sort((a, b) => b.length - a.length);
const RULE_KEYWORD_PATTERN = new RegExp(ALL_KEYWORDS.join("|"));

/**
 * Extract rule lines from AGENTS.md content.
 * Focuses on table rows and bullet points with enforcement keywords.
 */
export function extractRuleLines(
  content: string,
): { line: string; lineNum: number; section: string }[] {
  const lines = content.split("\n");
  const rules: { line: string; lineNum: number; section: string }[] = [];
  let currentSection = "";
  let inCodeBlock = false;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Skip fenced code blocks
    if (line.trim().startsWith("```")) {
      inCodeBlock = !inCodeBlock;
      continue;
    }
    if (inCodeBlock) continue;

    const headerMatch = line.match(/^#{1,3}\s+(.+)/);
    if (headerMatch) {
      currentSection = headerMatch[1].trim();
      continue;
    }

    const trimmed = line.trim();
    if (!trimmed) continue;

    const isTableRow = trimmed.startsWith("|") && trimmed.includes("|");
    const isBullet = /^([-*]|\d+\.)\s/.test(trimmed);

    if (isTableRow || isBullet) {
      if (RULE_KEYWORD_PATTERN.test(trimmed)) {
        rules.push({ line: trimmed, lineNum: i + 1, section: currentSection });
      }
    }
  }

  return rules;
}

/**
 * Calculate Jaccard similarity between two strings based on character bigrams.
 * Used to check if two rules are discussing the same topic.
 * Uses bigrams to support Japanese text which lacks spaces.
 */
function calculateJaccardSimilarity(strA: string, strB: string): number {
  // Normalize: remove noise (non-word chars) and lowercase
  // Keep ASCII words and CJK characters
  const clean = (s: string) =>
    s
      .toLowerCase()
      .replace(/[^\w\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf]/g, "");

  const s1 = clean(strA);
  const s2 = clean(strB);

  // Generate bigrams
  const getBigrams = (s: string) => {
    const bigrams = new Set<string>();
    for (let i = 0; i < s.length - 1; i++) {
      bigrams.add(s.substring(i, i + 2));
    }
    return bigrams;
  };

  const setA = getBigrams(s1);
  const setB = getBigrams(s2);

  if (setA.size === 0 || setB.size === 0) return 0;

  const intersection = new Set([...setA].filter((x) => setB.has(x)));
  const union = new Set([...setA, ...setB]);

  return intersection.size / union.size;
}

/**
 * Find potential contradictions between rule lines.
 */
export function findContradictions(
  rules: { line: string; lineNum: number; section: string }[],
): { ruleA: string; ruleB: string; pair: [string, string]; sectionA: string; sectionB: string }[] {
  const contradictions: {
    ruleA: string;
    ruleB: string;
    pair: [string, string];
    sectionA: string;
    sectionB: string;
  }[] = [];

  for (let i = 0; i < rules.length; i++) {
    for (let j = i + 1; j < rules.length; j++) {
      const a = rules[i];
      const b = rules[j];

      // Skip same section: tables often list both 禁止 and 推奨 patterns side by side,
      // which would cause false positives. Cross-section contradictions are more likely real.
      if (a.section === b.section) continue;

      // Report only the first contradiction per line pair to avoid noise
      let foundForPair = false;
      for (const [kwA, kwB] of CONTRADICTION_PAIRS) {
        if (foundForPair) break;
        const aHasFirst = a.line.includes(kwA);
        const aHasSecond = a.line.includes(kwB);
        const bHasFirst = b.line.includes(kwA);
        const bHasSecond = b.line.includes(kwB);

        if ((aHasFirst && bHasSecond) || (aHasSecond && bHasFirst)) {
          // Calculate similarity to avoid false positives (e.g. "Login is REQUIRED" vs "Logout is OPTIONAL")
          // Only flag if lines share significant vocabulary (topic)
          if (calculateJaccardSimilarity(a.line, b.line) > 0.2) {
            contradictions.push({
              ruleA: a.line.substring(0, 100),
              ruleB: b.line.substring(0, 100),
              pair: [kwA, kwB],
              sectionA: a.section,
              sectionB: b.section,
            });
            foundForPair = true;
          }
        }
      }
    }
  }

  return contradictions;
}

function isAgentsMdFile(filePath: string): boolean {
  const projectRoot = process.env.CLAUDE_PROJECT_DIR || process.cwd();
  const resolved = resolve(filePath);
  const rel = relative(resolve(projectRoot), resolved);
  return rel === "AGENTS.md";
}

async function main(): Promise<void> {
  let sessionId: string | undefined;

  try {
    const input = await parseHookInput();
    const ctx = createHookContext(input);
    sessionId = ctx.sessionId;
    const toolInput = input.tool_input as Record<string, unknown> | undefined;
    const filePath = (toolInput?.file_path as string) ?? "";

    if (!filePath || !isAgentsMdFile(filePath)) {
      approveAndExit(HOOK_NAME);
    }

    let content: string;
    try {
      content = readFileSync(resolve(filePath), "utf-8");
    } catch {
      approveAndExit(HOOK_NAME);
      return;
    }

    const rules = extractRuleLines(content);
    const contradictions = findContradictions(rules);

    if (contradictions.length === 0) {
      approveAndExit(HOOK_NAME);
    }

    const details = contradictions
      .slice(0, 3)
      .map(
        (c) =>
          `  対立ペア: 「${c.pair[0]}」↔「${c.pair[1]}」\n    セクション「${c.sectionA}」: ${c.ruleA}\n    セクション「${c.sectionB}」: ${c.ruleB}`,
      )
      .join("\n\n");
    const moreCount =
      contradictions.length > 3
        ? `\n\n  ... and ${contradictions.length - 3} more potential contradictions`
        : "";

    const systemMessage = `⚠️ rule-consistency-check: AGENTS.mdに矛盾の可能性がある記述を検出しました。

${details}${moreCount}

💡 意図的な記述（例外規定等）であれば問題ありません。矛盾が本物であれば修正してください。`;

    await logHookExecution(
      HOOK_NAME,
      "approve",
      undefined,
      {
        contradictions_count: contradictions.length,
        rules_count: rules.length,
      },
      { sessionId },
    );

    console.log(JSON.stringify({ systemMessage }));
    process.exit(0);
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    await logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(error)}`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify({ reason: `Hook error: ${formatError(error)}` }));
    process.exit(0);
  }
}

if (import.meta.main) {
  main();
}
