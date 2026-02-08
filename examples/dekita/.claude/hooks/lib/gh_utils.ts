/**
 * GitHub CLI（gh）コマンド関連のユーティリティ
 *
 * Why:
 *   複数のフック（issue_label_check, issue_priority_label_check）で
 *   重複していたgh コマンド検出ロジックを共通化する。
 *
 * What:
 *   - isGhCommand: トークンがghコマンドかどうかを判定
 *   - isGhIssueCreateCommand: コマンドがgh issue createかどうかを判定
 *
 * Changelog:
 *   - silenvx/dekita#3079: 初期実装（issue_label_check, issue_priority_label_checkから抽出）
 */

import { basename } from "node:path";
import { shellSplit } from "./labels";
import { skipEnvPrefixes } from "./option_parser";

/**
 * Check if a token represents the gh command (bare name or full path).
 *
 * @example
 * isGhCommand("gh")           // true
 * isGhCommand("/usr/bin/gh")  // true
 * isGhCommand("git")          // false
 */
export function isGhCommand(token: string): boolean {
  return basename(token) === "gh";
}

/**
 * Check if tokens represent `gh issue create` command.
 */
function matchesGhIssueCreate(tokens: string[]): boolean {
  const remaining = skipEnvPrefixes(tokens);
  if (remaining.length < 3) {
    return false;
  }
  return isGhCommand(remaining[0]) && remaining[1] === "issue" && remaining[2] === "create";
}

/**
 * Check if command is `gh issue create`.
 *
 * Handles:
 * - Environment variable prefixes (e.g., `GH_TOKEN=xxx gh issue create`)
 * - Full paths (e.g., `/usr/bin/gh issue create`)
 * - Quoted arguments (using shellSplit)
 *
 * @example
 * isGhIssueCreateCommand("gh issue create --title 'Test'")  // true
 * isGhIssueCreateCommand("gh issue list")                    // false
 * isGhIssueCreateCommand("GH_TOKEN=xxx gh issue create")    // true
 */
export function isGhIssueCreateCommand(command: string): boolean {
  // shellSplit handles unclosed quotes gracefully (doesn't throw)
  const tokens = shellSplit(command);
  return matchesGhIssueCreate(tokens);
}
