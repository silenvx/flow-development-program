#!/usr/bin/env bun
/**
 * コミットメッセージに「なぜ」の背景が含まれているかをチェックする。
 *
 * Why:
 *   コードの差分は「何を」変更したかを示すが、「なぜ」その変更が必要だったかは
 *   時間とともに失われる。git blameで追跡できるよう背景の記録を強制する。
 *
 * What:
 *   - コミットメッセージから「なぜ」を示すキーワードを検索
 *   - Issue参照があればコンテキストありと判定
 *   - 不足の場合はコミットをブロック
 *
 * Remarks:
 *   - lefthook経由でcommit-msgとして実行
 *   - Claude Codeフックではなく、Gitフック
 *   - merge/revert/WIP/fixup等は自動スキップ
 *   - commit-message-template.pyとセットで使用
 *
 * Changelog:
 *   - silenvx/dekita#1896: フック追加
 *   - silenvx/dekita#2874: TypeScript移行
 */

import { readFileSync } from "node:fs";
import { formatError } from "../lib/format_error";

// Keywords indicating "Why" context is present (case-insensitive)
const WHY_KEYWORDS_JA = [
  "なぜ",
  "理由",
  "原因",
  "背景",
  "目的",
  "ため", // 〜のため
  "必要", // 〜が必要
];

const WHY_KEYWORDS_EN = [
  "why",
  "reason",
  "because",
  "background",
  "purpose",
  "motivation",
  "in order to",
  "so that",
  "to fix",
  "to prevent",
  "to avoid",
  "to enable",
  "to support",
];

// Section headers that indicate structured context
const SECTION_HEADERS = [
  "## 背景",
  "## Background",
  "## Why",
  "## Motivation",
  "## 理由",
  "## Summary", // Summary often contains motivation
];

// Issue/PR references (context is in the linked issue)
const ISSUE_PATTERNS = [
  /(?:closes?|fixes?|resolves?)\s*#\d+/i, // Closes #123
  /#\d+/, // Any issue reference
];

/**
 * Extract the subject line (first non-comment, non-empty line).
 */
export function getSubjectLine(content: string): string {
  for (const line of content.split("\n")) {
    if (isGitComment(line)) {
      continue;
    }
    const stripped = line.trim();
    if (stripped) {
      return stripped;
    }
  }
  return "";
}

/**
 * Determine if the why-check should be skipped.
 */
export function shouldSkipCheck(content: string): { skip: boolean; reason: string } {
  const subject = getSubjectLine(content);

  // Merge commits
  if (subject.startsWith("Merge ")) {
    return { skip: true, reason: "merge commit" };
  }

  // Revert commits
  if (subject.startsWith("Revert ")) {
    return { skip: true, reason: "revert commit" };
  }

  // WIP commits
  if (subject.toLowerCase().startsWith("wip")) {
    return { skip: true, reason: "WIP commit" };
  }

  // fixup! commits
  if (subject.startsWith("fixup!") || subject.startsWith("squash!")) {
    return { skip: true, reason: "fixup/squash commit" };
  }

  // Very short subject (likely incomplete)
  if (subject.length < 10) {
    return { skip: true, reason: "subject too short" };
  }

  return { skip: false, reason: "" };
}

/**
 * Check if the message references an issue (which has context).
 */
export function hasIssueReference(content: string): boolean {
  const contentLower = content.toLowerCase();
  for (const pattern of ISSUE_PATTERNS) {
    if (pattern.test(contentLower)) {
      return true;
    }
  }
  return false;
}

/**
 * Check if the message includes "Why" context.
 */
export function hasWhyContext(content: string): boolean {
  const contentLower = content.toLowerCase();

  // Check section headers
  for (const header of SECTION_HEADERS) {
    if (contentLower.includes(header.toLowerCase())) {
      return true;
    }
  }

  // Check Japanese keywords (Japanese is case-insensitive anyway)
  for (const keyword of WHY_KEYWORDS_JA) {
    if (content.includes(keyword)) {
      return true;
    }
  }

  // Check English keywords
  for (const keyword of WHY_KEYWORDS_EN) {
    if (contentLower.includes(keyword)) {
      return true;
    }
  }

  return false;
}

/**
 * Check if a line is a Git comment (not a Markdown header).
 *
 * Git comments start with "# " (hash + space) or are just "#".
 * Markdown headers start with "##" or more hashes.
 */
export function isGitComment(line: string): boolean {
  // Empty comment line
  if (line === "#") {
    return true;
  }
  // Git comment: "# " followed by anything
  if (line.startsWith("# ")) {
    return true;
  }
  return false;
}

/**
 * Remove Git comment lines from content, preserving Markdown headers.
 */
export function stripComments(content: string): string {
  const lines = content.split("\n").filter((line) => !isGitComment(line));
  return lines.join("\n");
}

/**
 * Check if commit message has adequate "Why" context.
 */
function checkCommitMessage(filepath: string): { isValid: boolean; message: string } {
  let content: string;

  try {
    content = readFileSync(filepath, "utf-8");
  } catch (e) {
    // If we can't read the file, allow the commit (fail-open for this check)
    return { isValid: true, message: `Could not read file: ${formatError(e)}` };
  }

  // Check skip conditions
  const { skip, reason } = shouldSkipCheck(content);
  if (skip) {
    return { isValid: true, message: `Skipped: ${reason}` };
  }

  // Remove comments for analysis
  const cleanContent = stripComments(content);

  // Issue reference provides context
  if (hasIssueReference(cleanContent)) {
    return { isValid: true, message: "Has issue reference" };
  }

  // Check for why context
  if (hasWhyContext(cleanContent)) {
    return { isValid: true, message: "Has why context" };
  }

  // No context found
  return { isValid: false, message: "" };
}

/**
 * Generate helpful error message for missing context.
 */
function formatErrorMessage(): string {
  return `
コミットメッセージに「なぜ」の説明がありません。

コードの差分は「何を」変更したかを示しますが、「なぜ」その変更が
必要だったかは時間とともに失われます。git blameで追跡できるよう、
背景を記録してください。

以下のいずれかを追加してください:
- 背景/理由の説明（「〜のため」「〜が原因」など）
- Issue参照（Closes #123）
- セクションヘッダー（## 背景, ## Background）

例:
  fix: セッション切れ時の無限リダイレクトを修正

  原因: トークン更新失敗時にリトライが無限ループしていた
  対応: 最大3回のリトライ制限を追加

  Fixes #123

詳細: coding-standards Skill の「コミットメッセージ」セクションを参照
`.trim();
}

/**
 * Main entry point for the hook.
 */
function main(): number {
  const args = process.argv.slice(2);

  if (args.length < 1) {
    // No arguments - nothing to check
    return 0;
  }

  const msgFile = args[0];
  const { isValid } = checkCommitMessage(msgFile);

  if (isValid) {
    return 0;
  }

  // Print error and block commit
  console.error(formatErrorMessage());
  return 1;
}

// Only run when executed directly, not when imported
if (import.meta.main) {
  process.exit(main());
}
