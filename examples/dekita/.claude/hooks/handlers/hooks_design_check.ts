#!/usr/bin/env bun
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { approveAndExit, blockAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "hooks-design-check";

// =============================================================================
// Constants
// =============================================================================

// Keywords that indicate remediation instructions in block messages
const REMEDIATION_KEYWORDS = [
  "【対処法】",
  "【解決方法】",
  "【回避方法】",
  "対処法:",
  "解決方法:",
  "回避方法:",
  "## 対処法",
  "## 解決方法",
  "## 回避方法",
];

const HOOK_DELETION_WARNING = `⚠️ フックファイルの削除が検出されました。

## 重要な警告 (Issue #193)

セッション中にフックファイルを削除すると、セッション終了時に
Stopフックがそのファイルを見つけられずエラーループに陥ります。

## 正しい手順

1. **このセッションを終了**: /exit または Ctrl+C で終了
2. **新しいセッションで作業**: claude コマンドで新規セッション開始
3. **新しいセッションでフックを削除**: rm や git rm を実行

## 理由

- settings.json のフック設定はセッション開始時に読み込まれる
- セッション中の削除は設定に反映されない
- 結果、存在しないファイルへの参照が残りエラーになる

今すぐフック削除が必要な場合は、上記の手順に従ってください。`;

const SRP_CHECKLIST_WARNING = `⚠️ 新しいフックファイルが追加されています。設計原則を確認してください。

## 単一責任の原則（SRP）チェックリスト

1. **このフックの責務は1つだけか？**
   - 1つのフックは1つの責務のみを持つ
   - 複数の責務を混ぜない

2. **既存フックと責務が重複していないか？**
   - 既存フックの一覧: .claude/hooks/*.py, .claude/hooks/handlers/*.ts
   - 重複がある場合は既存フックを拡張するか、責務を再整理

3. **「推奨」ではなく「ブロック」で強制しているか？**
   - systemMessage（推奨）は無視される可能性がある
   - decision: "block" で強制する

4. **AGENTS.mdに責務を明記したか？**
   - 「設定済みのフック」セクションに追加
   - 責務と動作を簡潔に説明`;

const LOG_EXECUTION_MISSING_MSG = `⚠️ logHookExecution() の呼び出しがありません (Issue #2589)

以下のファイルで logHookExecution() が使用されていません:
{files}

## 対処法

フックのmain()関数内で logHookExecution() を呼び出してください。

**TypeScript例**:
\`\`\`typescript
import { logHookExecution } from "../lib/logging";

async function main(): Promise<void> {
  // ... 処理 ...
  await logHookExecution("hook-name", "approve", "reason");
  // または
  await logHookExecution("hook-name", "block", "reason");
}
\`\`\`

**なぜ必要か**:
- セッションメトリクスでフック実行を追跡するため
- フックの動作確認・デバッグに必要
- collect_session_metrics.py で hooks_triggered として記録される`;

const REMEDIATION_MISSING_WARNING = `⚠️ ブロック関数に対処法セクションがありません (Issue #1111)

以下のファイルでブロックメッセージに対処法が見つかりませんでした:
{files}

## 推奨アクション

ブロック時のエラーメッセージには「対処法」セクションを含めてください。

**例**:
\`\`\`typescript
const reason = \`マージできません: CIが失敗しています

【対処法】
1. CIログを確認: gh run list
2. 失敗を修正してプッシュ
3. CIが成功したら再度マージを試行\`;
blockAndExit("hook-name", reason);
\`\`\`

**認識されるキーワード**: 【対処法】, 【解決方法】, 【回避方法】, または ## 対処法 など

**補足**: これは警告のみでブロックしません。既存コードへの影響を避けるため。`;

// =============================================================================
// Git Operations
// =============================================================================

/**
 * Get staged new hook files.
 *
 * Issue #3242: Added 'R' filter to detect renamed files (git mv).
 * When a file is renamed, git shows "Rxx\told_name\tnew_name" where xx is similarity.
 * For renames, we treat the new name as a "new" hook.
 */
async function getStagedNewHooks(): Promise<string[]> {
  try {
    // AR = Added or Renamed (git mv creates R entries)
    const proc = Bun.spawn(["git", "diff", "--cached", "--name-status", "--diff-filter=AR"], {
      stdout: "pipe",
      stderr: "pipe",
    });

    const output = await new Response(proc.stdout).text();
    const exitCode = await proc.exited;

    if (exitCode !== 0) return [];

    const newHooks: string[] = [];
    for (const line of output.trim().split("\n")) {
      if (!line) continue;
      const parts = line.split("\t");
      if (parts.length >= 2) {
        // For A (added): "A\tfilepath"
        // For R (renamed): "Rxx\told_path\tnew_path" (parts[2] is new path)
        const status = parts[0];
        const filepath = status.startsWith("R") && parts.length >= 3 ? parts[2] : parts[1];
        if (isHookFile(filepath)) {
          newHooks.push(filepath);
        }
      }
    }
    return newHooks;
  } catch {
    return [];
  }
}

async function getStagedModifiedHooks(): Promise<string[]> {
  try {
    const proc = Bun.spawn(["git", "diff", "--cached", "--name-status", "--diff-filter=M"], {
      stdout: "pipe",
      stderr: "pipe",
    });

    const output = await new Response(proc.stdout).text();
    const exitCode = await proc.exited;

    if (exitCode !== 0) return [];

    const modifiedHooks: string[] = [];
    for (const line of output.trim().split("\n")) {
      if (!line) continue;
      const parts = line.split("\t");
      if (parts.length >= 2) {
        const filepath = parts[1];
        if (isHookFile(filepath)) {
          modifiedHooks.push(filepath);
        }
      }
    }
    return modifiedHooks;
  } catch {
    return [];
  }
}

function isHookFile(filepath: string): boolean {
  // Python hooks
  if (
    filepath.startsWith(".claude/hooks/") &&
    filepath.endsWith(".py") &&
    !filepath.includes("/tests/") &&
    !filepath.includes("/lib/") &&
    !filepath.includes("/scripts/")
  ) {
    return true;
  }

  // TypeScript hooks
  if (
    filepath.startsWith(".claude/hooks/handlers/") &&
    filepath.endsWith(".ts") &&
    !filepath.includes("/tests/")
  ) {
    return true;
  }

  return false;
}

async function getStagedFileContent(filepath: string): Promise<string | null> {
  try {
    const proc = Bun.spawn(["git", "show", `:${filepath}`], {
      stdout: "pipe",
      stderr: "pipe",
    });

    const output = await new Response(proc.stdout).text();
    const exitCode = await proc.exited;

    if (exitCode !== 0) return null;
    return output;
  } catch {
    return null;
  }
}

// =============================================================================
// Check Functions
// =============================================================================

function checkHookDeletion(command: string): string[] {
  const deletedHooks: string[] = [];

  // Check if this is a rm command
  if (!/(?:^|\s|&&|\|\||[;|(])(?:git\s+)?rm(?:\s|$)/.test(command)) {
    return deletedHooks;
  }

  // Extract hook file paths (Python)
  const pyPatterns = [
    /(?<!['\"])\.claude\/hooks\/([\w-]+\.py)(?!['\"])/g,
    /'\.claude\/hooks\/([\w-]+\.py)'/g,
    /"\.claude\/hooks\/([\w-]+\.py)"/g,
  ];

  // Extract hook file paths (TypeScript)
  const tsPatterns = [
    /(?<!['\"])\.claude\/hooks\/handlers\/([\w-]+\.ts)(?!['\"])/g,
    /'\.claude\/hooks\/handlers\/([\w-]+\.ts)'/g,
    /"\.claude\/hooks\/handlers\/([\w-]+\.ts)"/g,
  ];

  const allPatterns = [...pyPatterns, ...tsPatterns];

  for (const pattern of allPatterns) {
    let match = pattern.exec(command);
    while (match !== null) {
      if (!deletedHooks.includes(match[1])) {
        deletedHooks.push(match[1]);
      }
      match = pattern.exec(command);
    }
  }

  // Check for directory deletion
  const dirPatterns = [
    /(?<!['\"])\.claude\/hooks\/?(?!['\"])(?:\s|$)/,
    /'\.claude\/hooks\/?'(?:\s|$)/,
    /"\.claude\/hooks\/?"(?:\s|$)/,
  ];

  if (dirPatterns.some((p) => p.test(command)) && deletedHooks.length === 0) {
    deletedHooks.push(".claude/hooks/ (directory)");
  }

  return deletedHooks;
}

/**
 * Check if hook content uses logHookExecution.
 */
function hasLogHookExecution(content: string, filepath: string): boolean {
  if (filepath.endsWith(".ts")) {
    // TypeScript: check for logHookExecution or approveAndExit/blockAndExit (which log internally)
    return (
      content.includes("logHookExecution") ||
      content.includes("approveAndExit") ||
      content.includes("blockAndExit")
    );
  }
  // Python
  return content.includes("log_hook_execution");
}

/**
 * Check if hook content has block calls with remediation.
 *
 * Returns list of lines with block calls missing remediation.
 *
 * Note: When block calls use variables for reason (e.g., blockAndExit(HOOK_NAME, reason)),
 * we check if the file contains remediation keywords anywhere, since we can't extract
 * the variable content via regex.
 */
function findBlocksWithoutRemediation(content: string, filepath: string): string[] {
  const issues: string[] = [];

  // Check if file contains any remediation keywords (for variable-based calls)
  const fileHasRemediation = REMEDIATION_KEYWORDS.some((keyword) => content.includes(keyword));

  // Patterns to detect block calls with string literal as 2nd argument (reason)
  // Match regardless of 1st arg (can be string literal or variable like HOOK_NAME)
  const literalPatterns = filepath.endsWith(".ts")
    ? [
        /blockAndExit\s*\(\s*[^,]+\s*,\s*["'`]([^"'`]+)["'`]/g,
        /makeBlockResult\s*\(\s*[^,]+\s*,\s*["'`]([^"'`]+)["'`]/g,
      ]
    : [/make_block_result\s*\(\s*[^,]+\s*,\s*["']([^"']+)["']/g];

  // Check string literal patterns
  for (const pattern of literalPatterns) {
    let match = pattern.exec(content);
    while (match !== null) {
      const reason = match[1];
      // Check if reason contains remediation keywords
      if (!REMEDIATION_KEYWORDS.some((keyword) => reason.includes(keyword))) {
        // Find line number (approximate)
        const beforeMatch = content.substring(0, match.index);
        const lineNumber = beforeMatch.split("\n").length;
        issues.push(`${filepath}:${lineNumber}: ${reason.slice(0, 50)}...`);
      }
      match = pattern.exec(content);
    }
  }

  // Patterns to detect block calls with variable arguments
  // These match calls like blockAndExit(HOOK_NAME, reason)
  const variablePatterns = filepath.endsWith(".ts")
    ? [/blockAndExit\s*\(\s*\w+\s*,\s*\w+\s*\)/g, /makeBlockResult\s*\(\s*\w+\s*,\s*\w+\s*\)/g]
    : [/make_block_result\s*\(\s*\w+\s*,\s*\w+\s*\)/g];

  // For variable-based calls, warn if file doesn't contain remediation keywords
  if (!fileHasRemediation) {
    for (const pattern of variablePatterns) {
      let match = pattern.exec(content);
      while (match !== null) {
        const beforeMatch = content.substring(0, match.index);
        const lineNumber = beforeMatch.split("\n").length;
        issues.push(
          `${filepath}:${lineNumber}: (variable-based call, no remediation keywords in file)`,
        );
        match = pattern.exec(content);
      }
    }
  }

  return issues;
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  let sessionId: string | undefined;
  try {
    const input = await parseHookInput();
    sessionId = input.session_id;
    const toolInput = input.tool_input as Record<string, unknown> | undefined;
    const command = (toolInput?.command as string) ?? "";

    // Check for hook file deletion
    const deletedHooks = checkHookDeletion(command);
    if (deletedHooks.length > 0) {
      const filesList = deletedHooks.join(", ");
      const result = {
        systemMessage: `フックファイル削除を検出: ${filesList}\n${HOOK_DELETION_WARNING}`,
      };
      await logHookExecution(
        HOOK_NAME,
        "approve",
        undefined,
        { deletion_detected: deletedHooks },
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Only check design review on git commit
    if (!/git\s+commit/.test(command)) {
      await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      approveAndExit(HOOK_NAME);
    }

    // Get newly added and modified hook files
    const newHooks = await getStagedNewHooks();
    const modifiedHooks = await getStagedModifiedHooks();
    const allHooks = [...newHooks, ...modifiedHooks];

    // If no hook files are being committed, approve
    if (allHooks.length === 0) {
      await logHookExecution(HOOK_NAME, "approve", undefined, undefined, { sessionId });
      approveAndExit(HOOK_NAME);
    }

    // Collect warnings
    const warnings: string[] = [];

    // Show SRP checklist for new hooks
    if (newHooks.length > 0) {
      warnings.push(SRP_CHECKLIST_WARNING);
    }

    // Check for missing remediation in all hook files
    const remediationIssues: string[] = [];
    for (const hookFile of allHooks) {
      const content = await getStagedFileContent(hookFile);
      if (content) {
        const issues = findBlocksWithoutRemediation(content, hookFile);
        remediationIssues.push(...issues);
      }
    }

    if (remediationIssues.length > 0) {
      const filesStr = remediationIssues.map((f) => `  - ${f}`).join("\n");
      warnings.push(REMEDIATION_MISSING_WARNING.replace("{files}", filesStr));
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `Remediation warning: ${remediationIssues.length} issues`,
        {
          remediation_issues: remediationIssues,
        },
        { sessionId },
      );
    }

    // Check if all new hooks use logHookExecution (blocking check)
    if (newHooks.length > 0) {
      const missingLogExecution: string[] = [];
      for (const hookFile of newHooks) {
        const content = await getStagedFileContent(hookFile);
        if (content && !hasLogHookExecution(content, hookFile)) {
          missingLogExecution.push(hookFile);
        }
      }

      if (missingLogExecution.length > 0) {
        const filesList = missingLogExecution.map((f) => `  - ${f}`).join("\n");
        let reason = LOG_EXECUTION_MISSING_MSG.replace("{files}", filesList);
        if (warnings.length > 0) {
          reason += `\n\n${warnings.join("\n\n")}`;
        }
        await logHookExecution(
          HOOK_NAME,
          "block",
          "Missing logHookExecution",
          {
            missing_log_execution: missingLogExecution,
          },
          { sessionId },
        );
        blockAndExit(HOOK_NAME, reason);
      }
    }

    // No blocking issues, but may have warnings
    if (warnings.length > 0) {
      const result = {
        systemMessage: warnings.join("\n\n"),
      };
      await logHookExecution(
        HOOK_NAME,
        "approve",
        undefined,
        {
          new_hooks: newHooks,
          modified_hooks: modifiedHooks,
        },
        { sessionId },
      );
      console.log(JSON.stringify(result));
      return;
    }

    await logHookExecution(
      HOOK_NAME,
      "approve",
      undefined,
      {
        new_hooks: newHooks,
        modified_hooks: modifiedHooks,
      },
      { sessionId },
    );
    approveAndExit(HOOK_NAME);
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    const result = { reason: `Hook error: ${formatError(error)}` };
    await logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(error)}`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify(result));
  }
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({}));
  });
}
