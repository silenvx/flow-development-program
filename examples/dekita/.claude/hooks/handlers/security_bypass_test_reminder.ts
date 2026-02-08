#!/usr/bin/env bun
/**
 * セキュリティガードファイル編集時にバイパステストの追加を促す。
 *
 * Why:
 *   Issue #1006の振り返りで発見された教訓:
 *   - branch_rename_guardが--color=alwaysでバイパスできた
 *   - セキュリティガード実装時は「バイパステスト」を意識すべき
 *
 * What:
 *   - ガードファイル（*_guard.py, *_block.py, *_check.py）の編集を検出
 *   - 対応するテストファイルにバイパステストがあるか確認
 *   - なければ警告メッセージを出力（ブロックはしない）
 *
 * Remarks:
 *   - PreToolUse:Edit/Write hookとして発火
 *   - 警告のみ、決定はエージェントに委ねる
 *
 * Changelog:
 *   - silenvx/dekita#1006: フック追加
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { existsSync, readFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { extractInputContext, mergeDetailsWithContext } from "../lib/input_context";
import { logHookExecution } from "../lib/logging";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "security-bypass-test-reminder";

// セキュリティガードファイルのパターン
const SECURITY_GUARD_PATTERNS = [
  /.*_guard\.py$/i,
  /.*_block\.py$/i,
  /.*_check\.py$/i,
  /.*-guard\.py$/i,
  /.*-block\.py$/i,
  /.*-check\.py$/i,
];

// テストファイルでバイパステストを示すキーワード
const BYPASS_TEST_KEYWORDS = ["bypass", "バイパス", "circumvent", "evade", "escape"];

/**
 * ファイルがセキュリティガードファイルかどうか判定する。
 */
export function isSecurityGuardFile(filePath: string): boolean {
  for (const pattern of SECURITY_GUARD_PATTERNS) {
    if (pattern.test(filePath)) {
      return true;
    }
  }
  return false;
}

/**
 * ガードファイルに対応するテストファイルのパスを取得する。
 */
function getTestFilePath(guardFilePath: string): string | null {
  const name = basename(guardFilePath);
  const dir = dirname(guardFilePath);
  const parentName = basename(dir);

  // ハイフン付きファイル名をアンダースコアに正規化
  const normalizedName = name.replace(/-/g, "_");

  // 両方のパターンを試す（オリジナルと正規化版）
  const testNames = [`test_${name}`];
  if (normalizedName !== name) {
    testNames.push(`test_${normalizedName}`);
  }

  // .claude/hooks/xxx_guard.py -> .claude/hooks/tests/test_xxx_guard.py
  if (parentName === "hooks") {
    for (const testName of testNames) {
      const testPath = join(dir, "tests", testName);
      if (existsSync(testPath)) {
        return testPath;
      }
    }
  }

  // 他のパターンも試す
  for (const testName of testNames) {
    // xxx_guard.py -> test_xxx_guard.py (同じディレクトリ)
    const sameDirTest = join(dir, testName);
    if (existsSync(sameDirTest)) {
      return sameDirTest;
    }

    // tests/test_xxx_guard.py
    const testsDirTest = join(dir, "tests", testName);
    if (existsSync(testsDirTest)) {
      return testsDirTest;
    }
  }

  return null;
}

/**
 * テストファイルにバイパステストがあるか確認する。
 */
function hasBypassTest(testFilePath: string): boolean {
  try {
    const content = readFileSync(testFilePath, "utf-8").toLowerCase();
    return BYPASS_TEST_KEYWORDS.some((keyword) => content.includes(keyword));
  } catch {
    return false; // ファイル読み取りエラー時はFail-open
  }
}

interface HookResult {
  decision?: string;
  message?: string;
}

async function main(): Promise<void> {
  const hookInput = await parseHookInput();
  const sessionId = hookInput.session_id;
  const _ctx = createHookContext(hookInput);
  const inputContext = extractInputContext(hookInput);
  const toolName = hookInput.tool_name || "";

  // Edit/Writeツールのみ対象
  if (toolName !== "Edit" && toolName !== "Write") {
    console.log(JSON.stringify({}));
    return;
  }

  const toolInput = hookInput.tool_input || {};
  const filePath = (toolInput as { file_path?: string }).file_path || "";

  // セキュリティガードファイル以外は無視
  if (!isSecurityGuardFile(filePath)) {
    console.log(JSON.stringify({}));
    return;
  }

  // テストファイル自体の編集は無視
  if (basename(filePath).includes("test_")) {
    console.log(JSON.stringify({}));
    return;
  }

  // 対応するテストファイルを探す
  const testFile = getTestFilePath(filePath);

  if (testFile === null) {
    // テストファイルがない場合は警告（ブロックはしない）
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `No test file found for ${filePath}`,
      mergeDetailsWithContext({ file_path: filePath }, inputContext),
      { sessionId },
    );
    const result: HookResult = {
      message: `[${HOOK_NAME}] ⚠️ セキュリティガードファイルを編集していますが、テストファイルが見つかりません。

**推奨アクション:**
1. テストファイルを作成: test_${basename(filePath)}
2. バイパステストケースを追加（例: test_*_bypass, test_*_circumvent）

**バイパステストの観点:**
- このガードを回避する方法はないか？
- 正規表現パターンを迂回できないか？
- エッジケースで誤ってapproveしないか？

参考: Issue #1006で--color=alwaysによるバイパスが発見された教訓`,
    };
    console.log(JSON.stringify(result));
    return;
  }

  // バイパステストがあるか確認
  if (hasBypassTest(testFile)) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Bypass test found in ${testFile}`,
      mergeDetailsWithContext({ file_path: filePath, test_file: testFile }, inputContext),
      { sessionId },
    );
    console.log(JSON.stringify({}));
    return;
  }

  // バイパステストがない場合は警告
  await logHookExecution(
    HOOK_NAME,
    "approve",
    `No bypass test in ${testFile}`,
    mergeDetailsWithContext({ file_path: filePath, test_file: testFile }, inputContext),
    { sessionId },
  );

  const result: HookResult = {
    message: `[${HOOK_NAME}] ⚠️ セキュリティガードファイルを編集していますが、バイパステストが見つかりません。

**対象ファイル:** ${filePath}
**テストファイル:** ${testFile}

**推奨アクション:**
テストファイルに以下のようなバイパステストを追加してください:

\`\`\`python
def test_<guard_name>_bypass_with_options(self):
    """Should block even with unusual options (bypass prevention)."""
    # このガードをバイパスしようとするケースをテスト
    ...
\`\`\`

**バイパステストの観点:**
- このガードを回避する方法はないか？
- 正規表現パターンを迂回できないか？（例: --opt=value形式）
- エッジケースで誤ってapproveしないか？

参考: Issue #1006で--color=alwaysによるバイパスが発見された教訓`,
  };
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
