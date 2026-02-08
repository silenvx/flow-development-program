#!/usr/bin/env bun
/**
 * PRマージ後に動作確認Issueを自動作成。
 *
 * Why:
 *   PRマージ後の動作確認を体系的に追跡するため、
 *   軽量な確認Issueを自動作成する。
 *
 * What:
 *   - PRに非ドキュメント変更が含まれるかチェック
 *   - 軽量な確認Issue（チェックリスト付き）を作成
 *   - マージしたPRとリンク
 *
 * Remarks:
 *   - PostToolUseフック
 *   - post-merge-flow-completion.py と補完関係
 *   - 自動化型: マージ成功後に軽量な動作確認Issueを作成
 *   - Issue #2501参照
 *
 * Changelog:
 *   - silenvx/dekita#2501: 初期実装
 *   - silenvx/dekita#3161: TypeScript移行
 */

import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import {
  type PrDetailsForIssueExtraction,
  extractIssueFromPrDetails,
  extractPrNumberFromMergeCommand,
  isPrMergeCommand,
} from "../lib/github";
import { getExitCode } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { isMergeSuccess } from "../lib/repo";
import {
  createHookContext,
  getBashCommand,
  getToolResultAsObject,
  parseHookInput,
} from "../lib/session";
import { asyncSpawn } from "../lib/spawn";

const HOOK_NAME = "post-merge-observation-issue";

// File patterns that are docs-only
const DOCS_ONLY_PATTERNS = new Set([
  ".md",
  ".txt",
  ".rst",
  "CLAUDE.md",
  "AGENTS.md",
  "README",
  "CHANGELOG",
  "LICENSE",
  ".claude/prompts/",
  ".claude/skills/",
  ".claude/docs/",
]);

// Claude checklist item with optional command
export interface ClaudeCheckItem {
  description: string;
  command?: string;
}

// File checklist patterns: [pattern, claude_items, human_items]
const FILE_CHECKLIST_PATTERNS: Array<[string, ClaudeCheckItem[], string[]]> = [
  // Hooks
  [
    ".claude/hooks/",
    [
      {
        description: "フックが正しく発火する（該当操作後にログ確認）",
        command: "cat .claude/logs/execution/hook-execution-*.jsonl | grep <フック名> | tail -5",
      },
      { description: "エラーハンドリングが正しく動作する" },
    ],
    [],
  ],
  // Scripts
  [
    ".claude/scripts/",
    [
      {
        description: "スクリプトが正常に実行できる",
        command: "bun run .claude/scripts/<スクリプト名>/main.ts --help",
      },
      {
        description: "ヘルプオプション（--help）が動作する",
        command: "bun run .claude/scripts/<スクリプト名>/main.ts --help",
      },
    ],
    [],
  ],
  // Frontend components
  [
    "frontend/src/",
    [{ description: "ビルドが成功する", command: "pnpm build" }],
    [
      "UI表示が崩れていない（本番URL確認）",
      "モバイル表示に問題がない（実機またはDevTools確認）",
      "アクセシビリティに問題がない（キーボード操作確認）",
    ],
  ],
  // Worker/API
  [
    "worker/src/",
    [
      {
        description: "APIが正常にレスポンスを返す",
        command: "curl -s https://api.dekita.app/health | jq .",
      },
      { description: "エラーレスポンスが適切に返る" },
    ],
    ["レスポンス速度に問題がない（体感確認）"],
  ],
  // Tests
  [".test.", [{ description: "テストが全てパスする", command: "pnpm test:ci" }], []],
  // Shared types
  [
    "shared/",
    [{ description: "型定義の変更がfrontend/workerで正しく反映される", command: "pnpm typecheck" }],
    [],
  ],
  // GitHub workflows
  [
    ".github/workflows/",
    [{ description: "CIが正常に動作する", command: "gh run list --limit 3" }],
    [],
  ],
  // Config files
  ["settings.json", [{ description: "設定変更が反映される" }], []],
];

export interface FileInfo {
  path: string;
}

export interface PrDetails extends PrDetailsForIssueExtraction {
  files?: FileInfo[];
}

/**
 * Check if a file path matches a pattern.
 */
export function matchesPattern(path: string, pattern: string): boolean {
  const basename = path.split("/").pop() ?? "";

  if (pattern.includes("/")) {
    // Directory pattern - match as path substring
    return path.includes(pattern);
  }
  if (pattern.startsWith(".") && !pattern.endsWith("/")) {
    // File extension pattern (e.g., ".test.") - match in filename
    return basename.includes(pattern);
  }
  if (pattern.endsWith("_")) {
    // Filename prefix pattern (e.g., "test_") - match at start of filename
    return basename.startsWith(pattern);
  }
  // Exact filename match (e.g., "settings.json")
  return basename === pattern;
}

/**
 * Generate checklist items based on changed files.
 */
export function generateChecklistItems(files: FileInfo[]): [ClaudeCheckItem[], string[]] {
  const claudeItems: ClaudeCheckItem[] = [];
  const humanItems: string[] = [];
  const seenPatterns = new Set<string>();

  for (const file of files) {
    const path = file.path ?? "";
    for (const [pattern, cItems, hItems] of FILE_CHECKLIST_PATTERNS) {
      if (matchesPattern(path, pattern) && !seenPatterns.has(pattern)) {
        seenPatterns.add(pattern);
        claudeItems.push(...cItems);
        humanItems.push(...hItems);
      }
    }
  }

  // Deduplicate while preserving order (by description)
  const seenDescriptions = new Set<string>();
  const uniqueClaudeItems = claudeItems.filter((item) => {
    if (seenDescriptions.has(item.description)) return false;
    seenDescriptions.add(item.description);
    return true;
  });
  const uniqueHumanItems = [...new Set(humanItems)];

  return [uniqueClaudeItems, uniqueHumanItems];
}

/**
 * Get PR details including files changed and title.
 */
async function getPrDetails(prNumber: number): Promise<PrDetails | null> {
  try {
    const result = await asyncSpawn(
      "gh",
      ["pr", "view", String(prNumber), "--json", "title,body,files,headRefName"],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (!result.success) {
      return null;
    }

    return JSON.parse(result.stdout);
  } catch {
    return null;
  }
}

/**
 * Check if all changed files are documentation only.
 */
export function isDocsOnly(files: FileInfo[]): boolean {
  if (!files || files.length === 0) {
    return false;
  }

  for (const file of files) {
    const path = file.path ?? "";
    let isDoc = false;

    for (const pattern of DOCS_ONLY_PATTERNS) {
      if (pattern.includes("/")) {
        // Directory match
        if (path.includes(pattern)) {
          isDoc = true;
          break;
        }
      } else if (pattern.startsWith(".")) {
        // Extension match
        if (path.endsWith(pattern)) {
          isDoc = true;
          break;
        }
      } else {
        // Filename match
        const basename = path.split("/").pop() ?? "";
        if (pattern === basename || basename.startsWith(`${pattern}.`)) {
          isDoc = true;
          break;
        }
      }
    }

    if (!isDoc) {
      return false;
    }
  }

  return true;
}

/**
 * Format checklist section for observation issue body.
 * Items with commands become [Claude] items, items without become [人間] items.
 */
export function formatChecklistSection(
  claudeItems: ClaudeCheckItem[],
  humanItems: string[],
): string[] {
  const lines: string[] = ["## 確認コマンド", ""];
  const withCmd = claudeItems.filter((item) => item.command);
  const withoutCmd = claudeItems.filter((item) => !item.command);
  if (withCmd.length > 0) {
    for (const item of withCmd) {
      lines.push(`- [ ] [Claude] \`${item.command}\` - ${item.description}`);
    }
  } else {
    lines.push("- [ ] [Claude] 新セッションで変更が反映されている");
    lines.push("- [ ] [Claude] 期待通りの動作をしている");
  }
  const allHumanItems = [...withoutCmd.map((item) => item.description), ...humanItems];
  for (const item of allHumanItems) {
    lines.push(`- [ ] [人間] ${item}`);
  }
  lines.push("");
  return lines;
}

// Re-export for backward compatibility with tests
export { extractIssueFromPrDetails as extractIssueNumber };

/**
 * Check if an observation issue already exists for this PR.
 */
async function hasExistingObservationIssue(prNumber: number): Promise<boolean> {
  try {
    const result = await asyncSpawn(
      "gh",
      [
        "issue",
        "list",
        "--label",
        "observation",
        "--state",
        "all",
        "--search",
        `動作確認: #${prNumber} in:title`,
        "--json",
        "number",
        "--limit",
        "1",
      ],
      { timeout: TIMEOUT_LIGHT * 1000 },
    );

    if (!result.success) {
      return false;
    }

    const issues = JSON.parse(result.stdout);
    return issues.length > 0;
  } catch {
    // On error, allow creation (fail-open)
    return false;
  }
}

/**
 * Create an observation issue for post-merge verification.
 */
async function createObservationIssue(
  prNumber: number,
  prTitle: string,
  linkedIssue: number | null,
  files: FileInfo[],
): Promise<number | null> {
  // Sanitize PR title: remove newlines
  const sanitizedTitle = prTitle.replace(/[\r\n]+/g, " ").trim();
  const title = `動作確認: ${sanitizedTitle} (#${prNumber})`;

  // Sanitize PR title to prevent markdown injection in body
  // Escape backticks to prevent breaking markdown code spans
  const escapedTitle = sanitizedTitle.replace(/`/g, "\\`");
  const safePrTitle = `\`${escapedTitle}\``;

  // Generate checklist items based on changed files
  const [claudeItems, humanItems] = generateChecklistItems(files);
  const checklistLines = formatChecklistSection(claudeItems, humanItems);

  const bodyLines = ["## 概要", "", `PR #${prNumber} (${safePrTitle}) のマージ後確認。`, ""];
  bodyLines.push(...checklistLines);

  // Related links
  bodyLines.push("## 関連", "", `- マージしたPR: #${prNumber}`);
  if (linkedIssue) {
    bodyLines.push(`- 関連Issue: #${linkedIssue}`);
  }
  bodyLines.push(
    "",
    "## 備考",
    "",
    "- 確認完了後、このIssueをクローズしてください",
    "- 問題があった場合は別途Issueを作成してください",
    "",
    "---",
    "*このIssueは post-merge-observation-issue フックにより自動作成されました*",
  );

  const body = bodyLines.join("\n");

  try {
    const result = await asyncSpawn(
      "gh",
      ["issue", "create", "--title", title, "--body", body, "--label", "observation,P3"],
      { timeout: TIMEOUT_MEDIUM * 1000 },
    );

    if (!result.success) {
      return null;
    }

    // Extract issue number from output
    // Output format: https://github.com/owner/repo/issues/123
    const match = result.stdout.match(/\/issues\/(\d+)/);
    if (match) {
      return Number.parseInt(match[1], 10);
    }

    return null;
  } catch {
    return null;
  }
}

async function main(): Promise<void> {
  const inputData = await parseHookInput();
  const ctx = createHookContext(inputData);
  const sessionId = ctx.sessionId;

  const toolName = inputData.tool_name ?? "";
  if (toolName !== "Bash") {
    return;
  }

  const command = getBashCommand(inputData);

  if (!isPrMergeCommand(command)) {
    return;
  }

  const rawToolOutput = inputData.tool_output;
  const toolOutput = typeof rawToolOutput === "string" ? rawToolOutput : "";
  const toolResult = getToolResultAsObject(inputData);
  const exitCode = getExitCode(toolResult);

  if (!isMergeSuccess(exitCode, toolOutput, command)) {
    await logHookExecution(
      HOOK_NAME,
      "skip",
      `is_merge_success returned False (exit_code=${exitCode})`,
      { output_preview: toolOutput.slice(0, 200) },
      { sessionId },
    );
    return;
  }

  const prNumber = await extractPrNumberFromMergeCommand(command);
  if (!prNumber) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      "skipped: could not extract PR number",
      undefined,
      { sessionId },
    );
    return;
  }

  const prDetails = await getPrDetails(prNumber);
  if (!prDetails) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `skipped: could not get PR #${prNumber} details`,
      undefined,
      { sessionId },
    );
    return;
  }

  // Skip docs-only changes
  const files = prDetails.files ?? [];
  if (isDocsOnly(files)) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `skipped: PR #${prNumber} is docs-only`,
      undefined,
      { sessionId },
    );
    return;
  }

  // Check for existing observation issue to prevent duplicates
  if (await hasExistingObservationIssue(prNumber)) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `skipped: observation issue already exists for PR #${prNumber}`,
      undefined,
      { sessionId },
    );
    return;
  }

  const prTitle = prDetails.title ?? "";
  const linkedIssue = extractIssueFromPrDetails(prDetails);

  const observationIssue = await createObservationIssue(prNumber, prTitle, linkedIssue, files);
  if (observationIssue) {
    console.log(`\n[${HOOK_NAME}] 動作確認Issue #${observationIssue} を作成しました`);
    console.log(`  - 対象PR: #${prNumber}`);
    console.log("  - 新セッションで動作確認し、問題なければクローズしてください");
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `created observation issue #${observationIssue} for PR #${prNumber}`,
      undefined,
      { sessionId },
    );
  } else {
    console.log(`\n[${HOOK_NAME}] 動作確認Issue作成に失敗しました (PR #${prNumber})`);
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `failed to create observation issue for PR #${prNumber}`,
      undefined,
      { sessionId },
    );
  }
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    process.exit(1);
  });
}
