#!/usr/bin/env bun
/**
 * git commit時にコミットメッセージテンプレートを挿入する。
 *
 * Why:
 *   「なぜ」の背景情報が欠けたコミットメッセージが多い。テンプレートで
 *   Why/What/Refsの構造を提示することで、背景を書く習慣を促す。
 *
 * What:
 *   - git commit（-mオプションなし）時にテンプレートを挿入
 *   - 既にユーザーコンテンツがある場合はスキップ
 *   - merge/squash/amend等ではスキップ
 *
 * Remarks:
 *   - lefthook経由でprepare-commit-msgとして実行
 *   - Claude Codeフックではなく、Gitフック
 *   - -m/-F/--amend等オプション時は発火しない
 *   - commit-message-why-check.tsとセットで使用
 *   - Python版: commit_message_template.py からの移行
 *
 * Changelog:
 *   - silenvx/dekita#1535: フック追加
 *   - silenvx/dekita#3142: TypeScriptに移植
 */

import { existsSync, readFileSync, writeFileSync } from "node:fs";

/**
 * Template prompting for background context (Japanese, simple)
 */
const TEMPLATE = `# なぜ: この変更が必要な理由
#

# 何を: 変更内容（箇条書き推奨）
#

# 参照: 関連Issue/PR
# Fixes #
`;

/**
 * Check if template insertion should be skipped.
 *
 * @param source - The source of the commit message from Git.
 *   - "message": -m or -F option was given
 *   - "template": -t option or commit.template config
 *   - "merge": merge commit
 *   - "squash": squash commit
 *   - "commit": amend commit (-c, -C, --amend)
 * @returns True if template should be skipped, false otherwise.
 */
export function shouldSkipTemplate(source: string | undefined): boolean {
  const skipSources = new Set(["message", "template", "merge", "squash", "commit"]);
  return source ? skipSources.has(source) : false;
}

/**
 * Check if the content has user-written message (not just comments).
 *
 * @param content - The current content of the commit message file.
 * @returns True if there's user content (non-comment, non-whitespace lines).
 */
export function hasUserContent(content: string): boolean {
  for (const line of content.split("\n")) {
    const stripped = line.trim();
    // Skip empty lines and comment lines
    if (stripped && !stripped.startsWith("#")) {
      return true;
    }
  }
  return false;
}

/**
 * Get the commit message template.
 *
 * @returns The template string with sections for Why/What/Refs.
 */
export function getTemplate(): string {
  return TEMPLATE;
}

/**
 * Insert template into the commit message file if appropriate.
 *
 * @param filepath - Path to the commit message file (COMMIT_EDITMSG).
 */
export function insertTemplate(filepath: string): void {
  // Read existing content with proper encoding
  let currentContent = "";
  try {
    if (existsSync(filepath)) {
      currentContent = readFileSync(filepath, "utf-8");
    }
  } catch {
    // Permission error or other OS error - fail silently
    return;
  }

  // Don't insert if user already has content
  if (hasUserContent(currentContent)) {
    return;
  }

  // Insert template at the beginning, preserve existing content (Git comments)
  const newContent = `${getTemplate()}\n${currentContent}`;

  try {
    writeFileSync(filepath, newContent, "utf-8");
  } catch {
    // Disk full, permission error, etc. - fail silently
    // Git will still proceed with the original message
  }
}

/**
 * Main entry point for the hook.
 *
 * @returns Exit code (0 for success).
 */
function main(): number {
  const args = process.argv.slice(2);

  if (args.length < 1) {
    // No arguments - nothing to do
    return 0;
  }

  const msgFile = args[0];
  const source = args[1];

  if (shouldSkipTemplate(source)) {
    return 0;
  }

  insertTemplate(msgFile);
  return 0;
}

// Execute only when run directly
if (import.meta.main) {
  process.exit(main());
}
