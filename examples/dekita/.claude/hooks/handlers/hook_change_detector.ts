#!/usr/bin/env bun
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { approveAndExit } from "../lib/results";
import { parseHookInput } from "../lib/session";
import { splitCommandChain, stripQuotedStrings } from "../lib/strings";

const HOOK_NAME = "hook-change-detector";

// =============================================================================
// Command Detection
// =============================================================================

/**
 * Check if command contains git add or git commit.
 */
function isGitAddOrCommitCommand(command: string): boolean {
  const stripped = stripQuotedStrings(command);
  const subcommands = splitCommandChain(stripped);
  return subcommands.some((subcmd) => /^git\s+(add|commit)(\s|$)/.test(subcmd));
}

// =============================================================================
// Staged Files
// =============================================================================

/**
 * Get list of all staged files.
 */
async function getStagedFiles(): Promise<string[]> {
  // Test mode
  const testFiles = process.env._TEST_STAGED_FILES;
  if (testFiles !== undefined) {
    return testFiles ? testFiles.split(",") : [];
  }

  try {
    const proc = Bun.spawn(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"], {
      stdout: "pipe",
      stderr: "pipe",
    });

    const output = await new Response(proc.stdout).text();
    const exitCode = await proc.exited;

    if (exitCode !== 0) return [];
    return output.trim() ? output.trim().split("\n") : [];
  } catch {
    return [];
  }
}

/**
 * Check if a file is a hook file (excluding tests and lib).
 */
function isHookFile(filePath: string): boolean {
  const path = filePath.replace(/\\/g, "/");

  // Check if in hooks directory
  if (!path.startsWith(".claude/hooks/")) return false;

  // Exclude test files
  if (path.startsWith(".claude/hooks/tests/")) return false;

  // Exclude lib files (utility modules)
  if (path.startsWith(".claude/hooks/lib/")) return false;

  // Only Python and TypeScript files
  if (!path.endsWith(".py") && !path.endsWith(".ts")) return false;

  return true;
}

/**
 * Classify staged files into hook files and non-hook files.
 */
function classifyStagedFiles(files: string[]): { hookFiles: string[]; nonHookFiles: string[] } {
  const hookFiles: string[] = [];
  const nonHookFiles: string[] = [];

  for (const f of files) {
    if (isHookFile(f)) {
      hookFiles.push(f);
    } else {
      nonHookFiles.push(f);
    }
  }

  return { hookFiles, nonHookFiles };
}

// =============================================================================
// Pattern Detection
// =============================================================================

/**
 * Get the staged content of a file.
 */
async function getStagedFileContent(filePath: string): Promise<string | null> {
  // Test mode
  const safeName = filePath.replace(/\//g, "_").replace(/\./g, "_");
  const testContent = process.env[`_TEST_FILE_CONTENT_${safeName}`];
  if (testContent !== undefined) {
    return testContent;
  }

  try {
    const proc = Bun.spawn(["git", "show", `:${filePath}`], {
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

// Pattern detection patterns - detect hooks that contain keyword/pattern lists
const PATTERN_LIST_INDICATORS = [
  // Variable names ending with _KEYWORDS, _PATTERNS, etc.
  /^[A-Z_]+_KEYWORDS\s*=\s*\[/m,
  /^[A-Z_]+_PATTERNS\s*=\s*\[/m,
  /^[A-Z_]+_REGEX\s*=\s*\[/m,
  // Raw string regex patterns with metacharacters (Python)
  /r"[^"]*\\[sdwbBSWDnrt]/,
  /r'[^']*\\[sdwbBSWDnrt]/,
  // Regular regex patterns (TypeScript)
  /\/[^/]+\\[sdwbBSWDnrt][^/]*\//,
  // re.compile patterns (Python)
  /re\.compile\s*\(/,
  // new RegExp patterns (TypeScript)
  /new RegExp\s*\(/,
  // re.search/match/finditer with pattern variable (Python)
  /re\.(search|match|findall|finditer)\s*\(\s*pattern/,
];

/**
 * Check if a hook file contains pattern detection logic.
 */
function isPatternDetectionHook(content: string): boolean {
  return PATTERN_LIST_INDICATORS.some((pattern) => pattern.test(content));
}

/**
 * Detect which hook files are pattern-detection hooks.
 */
async function detectPatternHooks(hookFiles: string[]): Promise<string[]> {
  const patternHooks: string[] = [];
  for (const hookFile of hookFiles) {
    const content = await getStagedFileContent(hookFile);
    if (content && isPatternDetectionHook(content)) {
      patternHooks.push(hookFile);
    }
  }
  return patternHooks;
}

// =============================================================================
// Warning Message Builders
// =============================================================================

function buildPatternAnalysisWarning(patternHooks: string[]): string {
  let hookList = patternHooks
    .slice(0, 5)
    .map((f) => `  - ${f}`)
    .join("\n");
  if (patternHooks.length > 5) {
    hookList += `\n  ... and ${patternHooks.length - 5} more`;
  }

  return `📊 hook-change-detector: パターン検出フックが変更されています。

【実データ分析チェックリスト】
パターン検出フック作成・変更時は、以下を確認してください:

□ 実データソースを特定したか
  - GitHub PR comments
  - Issue comments
  - セッションログ

□ 実データからパターンを抽出したか
  - 仮説ベースではなく実際のデータを分析
  - 頻度・コンテキストを確認

□ 作成したパターンをテストしたか
  - 検出率（実際に検出すべきものを検出できているか）
  - 誤検知率（検出すべきでないものを検出していないか）

対象フック:
${hookList}

【分析ツール】
.claude/scripts/analyze_pattern_data.py を使用してパターンを分析できます:
  python3 analyze_pattern_data.py search --pattern "検索パターン" --show-matches
  python3 analyze_pattern_data.py analyze --pattern "分析パターン"
  python3 analyze_pattern_data.py validate --patterns-file patterns.txt`;
}

function buildHooksSkillReminder(hookFiles: string[]): string {
  let hookList = hookFiles
    .slice(0, 5)
    .map((f) => `  - ${f}`)
    .join("\n");
  if (hookFiles.length > 5) {
    hookList += `\n  ... and ${hookFiles.length - 5} more`;
  }

  return `📚 hook-change-detector: フックファイルが変更されています。

【hooks-reference Skill 参照リマインダー】
フック修正・新規作成時は \`hooks-reference\` Skill を参照してください。

**確認すべき内容:**
□ 既存の実装パターン（例: ZoneInfoNotFoundError の例外処理）
□ フック出力フォーマット（makeBlockResult, makeApproveResult）
□ ログ記録パターン（logHookExecution）
□ SKIP環境変数のサポート
□ テストの実装パターン

対象フック:
${hookList}

**Skill呼び出し方法:**
  /hooks-reference

💡 「単純な修正だからSkill不要」は誤った判断です。
   既存パターンを見落とすリスクを回避するため、常に参照してください。`;
}

function buildMixedStagingWarning(hookFiles: string[], nonHookFiles: string[]): string {
  let hookList = hookFiles
    .slice(0, 5)
    .map((f) => `  - ${f}`)
    .join("\n");
  if (hookFiles.length > 5) {
    hookList += `\n  ... and ${hookFiles.length - 5} more`;
  }

  let nonHookList = nonHookFiles
    .slice(0, 5)
    .map((f) => `  - ${f}`)
    .join("\n");
  if (nonHookFiles.length > 5) {
    nonHookList += `\n  ... and ${nonHookFiles.length - 5} more`;
  }

  return `⚠️ hook-change-detector: フックファイルと非フックファイルが同時にステージされています。

【Chicken-and-egg問題の警告】
フックファイルの変更とそれに依存するコードを同じPRに含めると、
CIではmainのフックが使用されるため、意図しないブロック/失敗が発生する可能性があります。

フックファイル:
${hookList}

非フックファイル:
${nonHookList}

【推奨対応】
1. フックの変更を先に別PRでマージ
2. その後、依存するコードをPRに含める

【安全に続行できるケース】
- テストファイルとの混在: 通常は安全（警告は表示されますが問題なし）
- フックに影響しない独立した変更: 問題なし
- 緊急時: このまま続行可（自己責任）`;
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

    // Only check git add/commit commands
    if (!isGitAddOrCommitCommand(command)) {
      approveAndExit(HOOK_NAME);
    }

    // Get staged files
    const stagedFiles = await getStagedFiles();
    if (stagedFiles.length === 0) {
      approveAndExit(HOOK_NAME);
    }

    // Classify files
    const { hookFiles, nonHookFiles } = classifyStagedFiles(stagedFiles);

    // Collect all warnings
    const warnings: string[] = [];

    // Check for mixed staging
    if (hookFiles.length > 0 && nonHookFiles.length > 0) {
      const mixedWarning = buildMixedStagingWarning(hookFiles, nonHookFiles);
      warnings.push(mixedWarning);
    }

    // Check for pattern-detection hooks
    if (hookFiles.length > 0) {
      const patternHooks = await detectPatternHooks(hookFiles);
      if (patternHooks.length > 0) {
        const patternWarning = buildPatternAnalysisWarning(patternHooks);
        warnings.push(patternWarning);
        await logHookExecution(
          HOOK_NAME,
          "approve",
          undefined,
          {
            pattern_hooks: patternHooks,
            warning: "pattern_detection_hook",
          },
          { sessionId },
        );
      }

      // Always remind about hooks-reference Skill
      const skillReminder = buildHooksSkillReminder(hookFiles);
      warnings.push(skillReminder);
      await logHookExecution(
        HOOK_NAME,
        "approve",
        undefined,
        {
          hook_files: hookFiles,
          warning: "hooks_skill_reminder",
        },
        { sessionId },
      );
    }

    // Return with warnings if any
    if (warnings.length > 0) {
      const combinedWarning = warnings.join("\n\n---\n\n");
      const result = {
        systemMessage: combinedWarning,
      };

      if (hookFiles.length > 0 && nonHookFiles.length > 0) {
        await logHookExecution(
          HOOK_NAME,
          "approve",
          undefined,
          {
            hook_files: hookFiles,
            non_hook_files_count: nonHookFiles.length,
            warning: "mixed_staging",
          },
          { sessionId },
        );
      }

      console.log(JSON.stringify(result));
      process.exit(0);
    }

    // No warnings - all good
    approveAndExit(HOOK_NAME);
  } catch (error) {
    console.error(`[${HOOK_NAME}] Hook error: ${formatError(error)}`);
    const result = { reason: `Hook error: ${formatError(error)}` };
    await logHookExecution(HOOK_NAME, "approve", `Hook error: ${formatError(error)}`, undefined, {
      sessionId,
    });
    console.log(JSON.stringify(result));
    process.exit(0);
  }
}

if (import.meta.main) {
  main();
}
