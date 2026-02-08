#!/usr/bin/env bun
/**
 * Issue/PR作成前にWeb調査を強制する。
 *
 * Why:
 *   十分な調査なしにIssue/PRを作成すると、既存の解決策を見落としたり、
 *   同様のIssueが既に存在する可能性がある。事前調査を強制する。
 *
 * What:
 *   - gh issue create / gh pr create コマンドを検出
 *   - セッション内のWebSearch/WebFetch履歴を確認
 *   - 調査なしの場合はブロック
 *   - バイパス条件: documentation/trivialラベル、十分な探索、本文に「調査不要」
 *
 * Remarks:
 *   - ブロック型フック（PreToolUse:Bash）
 *   - research-trackerがWeb調査を記録、本フックが検証
 *   - コードベース探索（Grep/Glob 5回以上）もWeb調査の代替として許可
 *   - Python版: research_requirement_check.py
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#1957: ラベル抽出をlib/labels.pyに共通化
 *   - silenvx/dekita#2578: heredocパターンを先にチェック
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

import { formatError } from "../lib/format_error";
import { extractLabelsFromCommand, splitCommaSeparatedLabels } from "../lib/labels";
import { logHookExecution } from "../lib/logging";
import { checkResearchDone, getExplorationDepth, getSessionDir } from "../lib/research";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";
import { stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "research-requirement-check";

// Labels that bypass research requirement
const BYPASS_LABELS = new Set(["documentation", "trivial"]);

// Keywords in body that bypass research requirement
const BYPASS_KEYWORDS = ["no research needed", "skip research", "research complete"];

/**
 * Check if command is gh issue create.
 */
export function isGhIssueCreate(command: string): boolean {
  const stripped = stripQuotedStrings(command);
  return /\bgh\s+issue\s+create\b/.test(stripped);
}

/**
 * Check if command is gh pr create.
 */
export function isGhPrCreate(command: string): boolean {
  const stripped = stripQuotedStrings(command);
  return /\bgh\s+pr\s+create\b/.test(stripped);
}

/**
 * Extract --label values from command.
 */
export function extractLabels(command: string): Set<string> {
  const rawLabels = extractLabelsFromCommand(command);
  const splitLabels = splitCommaSeparatedLabels(rawLabels);
  return new Set(splitLabels.map((l) => l.toLowerCase()));
}

/**
 * Extract --body value from command.
 *
 * Heredoc pattern must be checked FIRST because:
 * - Heredoc content may contain nested quotes
 * - Simple quote pattern would incorrectly match at the first nested quote
 */
export function extractBody(command: string): string {
  // Check for heredoc style FIRST: --body "$(cat <<'DELIMITER' ... DELIMITER)"
  // Use dynamic delimiter capture (group 1) and backreference (\1) to support
  // any delimiter (EOF, END, TEXT, END-OF-FILE, etc.), not just hardcoded 'EOF'
  // Use [ \t]*\r?\n to strictly match horizontal whitespace and required newline
  // (prevents consuming body content indentation with greedy \s*)
  // Use (?:([\s\S]*?)\r?\n)? to handle empty bodies where delimiter immediately follows opening newline
  // Use \1 to ensure delimiter matches at line start (prevents matching delimiter word inside body)
  // Use lookahead (?=\r?\n|\)|$) to ensure delimiter is the only content on the line
  const heredocMatch = command.match(
    /(?:--body|-b)\s+"?\$\(cat\s+<<\s*['"]?([\w-]+)['"]?[ \t]*\r?\n(?:([\s\S]*?)\r?\n)?\1(?=\r?\n|\)|$)/,
  );
  if (heredocMatch) {
    return heredocMatch[2] || "";
  }

  // Match --body "..." (handles escaped quotes)
  const dqMatch = command.match(/(?:--body|-b)\s+"((?:[^"\\]|\\.)*)"/);
  if (dqMatch) {
    return dqMatch[1].replace(/\\"/g, '"');
  }

  // Match --body '...'
  const sqMatch = command.match(/(?:--body|-b)\s+'((?:[^'\\]|\\.)*)'/);
  if (sqMatch) {
    return sqMatch[1].replace(/\\'/g, "'");
  }

  return "";
}

/**
 * Check if command has a bypass label.
 */
export function hasBypassLabel(command: string): boolean {
  const labels = extractLabels(command);
  for (const label of labels) {
    if (BYPASS_LABELS.has(label)) {
      return true;
    }
  }
  return false;
}

/**
 * Check if command body contains bypass keyword.
 */
export function hasBypassKeyword(command: string): boolean {
  const body = extractBody(command).toLowerCase();
  return BYPASS_KEYWORDS.some((keyword) => body.includes(keyword.toLowerCase()));
}

async function main(): Promise<void> {
  let sessionId: string | undefined;

  try {
    const data = await parseHookInput();
    const ctx = createHookContext(data);
    sessionId = ctx.sessionId;
    const toolName = (data.tool_name as string) || "";

    // Only check Bash commands
    if (toolName !== "Bash") {
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    const toolInput = (data.tool_input as Record<string, unknown>) || {};
    const command = (toolInput.command as string) || "";

    // Check if this is gh issue create or gh pr create
    const isIssue = isGhIssueCreate(command);
    const isPr = isGhPrCreate(command);

    if (!isIssue && !isPr) {
      const result = makeApproveResult(HOOK_NAME);
      console.log(JSON.stringify(result));
      return;
    }

    const actionType = isIssue ? "Issue" : "PR";

    // Check bypass conditions
    if (hasBypassLabel(command)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `${actionType} creation approved (bypass label)`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(HOOK_NAME, `${actionType} creation approved (bypass label)`);
      console.log(JSON.stringify(result));
      return;
    }

    if (hasBypassKeyword(command)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `${actionType} creation approved (bypass keyword)`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(
        HOOK_NAME,
        `${actionType} creation approved (no research needed)`,
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Get session info for research check
    const sessionIdForResearch = sessionId || undefined;
    const sessionDir = getSessionDir();

    // Check if research was done
    if (checkResearchDone(sessionDir, sessionIdForResearch)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `${actionType} creation approved (research done)`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(
        HOOK_NAME,
        `${actionType} creation approved (research done)`,
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Check if exploration is sufficient (alternative to web research)
    const exploration = getExplorationDepth(sessionDir, sessionIdForResearch);
    if (exploration.sufficient) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `${actionType} creation approved (sufficient exploration)`,
        undefined,
        { sessionId },
      );
      const result = makeApproveResult(
        HOOK_NAME,
        `${actionType} creation approved (${exploration.total} explorations)`,
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Block: No research or exploration done
    await logHookExecution(
      HOOK_NAME,
      "block",
      `${actionType} creation blocked (no research)`,
      undefined,
      { sessionId },
    );

    const blockMessage = `No web search/research has been performed.

Before creating ${actionType}, please investigate using the following tools:

**Recommended tools:**
- Context7: Get latest library documentation
  → mcp__context7__resolve-library-id, mcp__context7__get-library-docs
- WebSearch: Search for latest information and best practices
- Grep/Glob: Investigate existing codebase patterns (currently ${exploration.total}/5 times)
- Task(Explore): Understand codebase structure

**If no research is needed:**
- Add --label documentation or --label trivial
- Include "no research needed" in the body`;

    const result = makeBlockResult(HOOK_NAME, blockMessage);
    console.log(JSON.stringify(result));
    process.exit(2);
  } catch (error) {
    // Invalid input or error - approve silently
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
  }
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({}));
  });
}
