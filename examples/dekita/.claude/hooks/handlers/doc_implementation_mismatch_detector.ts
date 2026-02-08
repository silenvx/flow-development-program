#!/usr/bin/env bun
/**
 * ドキュメントと実装の乖離を検出してIssue作成を強制する
 *
 * Why:
 *   AGENTS.mdやSkillsで記載されているツール/プラグインが実際には動作しない場合、
 *   ユーザーが混乱し、手動で回避するだけでは問題が残り続ける。
 *   乖離を検出して即座にIssue化することで、問題を追跡可能にする。
 *
 * What:
 *   - PostToolUse:Skill/Task で発火
 *   - "Unknown skill" や "Agent type not found" エラーを検出
 *   - 該当名がAGENTS.mdに記載されているか確認
 *   - 記載されているのに動作しない場合、Issue作成を強制
 *
 * Detection patterns:
 *   - Skill: "Unknown skill: XXX" or "Error: Unknown skill: XXX"
 *   - Task: "Agent type 'XXX' not found"
 *
 * Remarks:
 *   - ブロック型（Issue作成を強制）
 *   - AGENTS.mdとの照合で誤検知を防止
 *
 * Changelog:
 *   - silenvx/dekita#3090: 初期実装
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { getToolResult, parseHookInput } from "../lib/session";
import type { HookResult } from "../lib/types";

const HOOK_NAME = "doc-implementation-mismatch-detector";

interface MismatchResult {
  isMismatch: boolean;
  toolType: "Skill" | "Task" | null;
  attemptedName: string;
  errorMessage: string;
}

/**
 * Detect documentation/implementation mismatch from tool result.
 */
export function detectMismatch(toolName: string, toolResult: unknown): MismatchResult {
  const noMismatch: MismatchResult = {
    isMismatch: false,
    toolType: null,
    attemptedName: "",
    errorMessage: "",
  };

  if (!toolResult) {
    return noMismatch;
  }

  const resultText = typeof toolResult === "string" ? toolResult : JSON.stringify(toolResult);

  // Skill: "Unknown skill: XXX" pattern
  if (toolName === "Skill") {
    const skillMatch = resultText.match(/Unknown skill:\s*([^\s"']+)/i);
    if (skillMatch) {
      return {
        isMismatch: true,
        toolType: "Skill",
        attemptedName: skillMatch[1],
        errorMessage: `Unknown skill: ${skillMatch[1]}`,
      };
    }
  }

  // Task: "Agent type 'XXX' not found" pattern
  if (toolName === "Task") {
    const taskMatch = resultText.match(/Agent type ['"]?([^'"]+)['"]? not found/i);
    if (taskMatch) {
      return {
        isMismatch: true,
        toolType: "Task",
        attemptedName: taskMatch[1],
        errorMessage: `Agent type '${taskMatch[1]}' not found`,
      };
    }
  }

  return noMismatch;
}

/**
 * Check if the name is documented in AGENTS.md.
 */
export function isDocumentedInAgentsMd(name: string): boolean {
  try {
    const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
    const agentsMdPath = resolve(projectDir, "AGENTS.md");
    const content = readFileSync(agentsMdPath, "utf-8");

    // Check if the name appears in AGENTS.md
    // Use case-insensitive search and word boundaries
    const patterns = [
      new RegExp(`\\b${escapeRegExp(name)}\\b`, "i"),
      new RegExp(`/${escapeRegExp(name)}\\b`, "i"), // /skillname pattern
      new RegExp(`\`${escapeRegExp(name)}\``, "i"), // `name` in code blocks
    ];

    return patterns.some((pattern) => pattern.test(content));
  } catch {
    return false;
  }
}

/**
 * Escape special regex characters.
 */
function escapeRegExp(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function main(): Promise<void> {
  let result: HookResult = {};

  try {
    const inputData = await parseHookInput();
    const toolName = inputData.tool_name || "";

    // Only check Skill and Task tools
    if (toolName !== "Skill" && toolName !== "Task") {
      console.log(JSON.stringify(result));
      return;
    }

    const toolResult = getToolResult(inputData);
    const mismatch = detectMismatch(toolName, toolResult);

    if (!mismatch.isMismatch) {
      console.log(JSON.stringify(result));
      return;
    }

    // Check if documented in AGENTS.md
    const isDocumented = isDocumentedInAgentsMd(mismatch.attemptedName);

    if (isDocumented) {
      logHookExecution(
        HOOK_NAME,
        "block",
        `Documentation mismatch: ${mismatch.attemptedName}`,
        {
          toolType: mismatch.toolType,
          attemptedName: mismatch.attemptedName,
          documented: true,
        },
        { sessionId: inputData.session_id },
      );

      const message = `## Documentation/Implementation Mismatch Detected

**Tool Type**: ${mismatch.toolType}
**Attempted Name**: \`${mismatch.attemptedName}\`
**Error**: ${mismatch.errorMessage}

This name is documented in AGENTS.md but does not work in practice.

**Required Action**:
1. Create an issue using the following format:

\`\`\`bash
gh issue create --title "fix: ${mismatch.toolType} '${mismatch.attemptedName}' mismatches documentation" --body "$(cat <<'EOF'
## Why
\`${mismatch.attemptedName}\` is documented in AGENTS.md as a ${mismatch.toolType === "Skill" ? "Skill/plugin" : "Task agent"}, but it does not work.

## What
- \`${mismatch.errorMessage}\`

## How
- Either make it work as documented, or
- Update the documentation to match the implementation

### Approach
1. ${mismatch.toolType === "Skill" ? "Register as a Skill" : "Correct the Task agent name"}
2. Update AGENTS.md documentation
EOF
)" --label "P2"
\`\`\`

2. After creating the issue, continue work using alternative methods`;

      result = {
        decision: "block",
        reason: message,
        systemMessage: `[${HOOK_NAME}] ${mismatch.toolType} '${mismatch.attemptedName}' is documented but not working. Issue creation required.`,
      };
    }
  } catch (e) {
    logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(e)}`);
  }

  console.log(JSON.stringify(result));
}

// テスト時はスキップ
if (import.meta.main) {
  main();
}
