#!/usr/bin/env bun
/**
 * Issue作成必要性のキーワードを検出し、即座にIssue作成を強制する
 *
 * Why:
 *   作業途中で「Issue化すべき」と認識しても、後回しにして忘れるリスクがある。
 *   キーワード検出により、Issue作成を即座に強制する。
 *
 * What:
 *   - トランスクリプトから直近のassistant発言を取得
 *   - 「Issue作成が必要」「Issue化すべき」等のキーワードを検出
 *   - 検出時に[IMMEDIATE: gh issue create]を出力してブロック
 *
 * Remarks:
 *   - UserPromptSubmitで実行（ユーザーの次の入力前にチェック）
 *   - 実際にIssue作成済みの場合は誤検知を避けるため除外
 *
 * Changelog:
 *   - silenvx/dekita#2823: TypeScript版初期実装
 */

import { existsSync, readFileSync } from "node:fs";
import { formatError } from "../lib/format_error";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "issue-creation-detector";

/** Issue作成の必要性を示すキーワードパターン */
const ISSUE_CREATION_PATTERNS = [
  // 明示的な「Issue化すべき」表現
  /issue[を]?作成(?:する|します|が必要|すべき)/i,
  /issue化(?:する|します|が必要|すべき)/i,
  /issue[を]?立て(?:る|ます|るべき)/i,
  /github\s*issue[を]?(?:作成|立て)/i,
  // 「後で対応」系（Issue参照なし）
  // Copilot review: カタストロフィックバックトラッキング防止のため量指定子を追加
  /(?:後で|将来的に|今後|別途)[^#]{0,200}(?:対応|検討|実装|修正)/,
  /フォローアップ(?:として|が必要)/,
  /スコープ外(?:なので|のため|として)/,
  // 問題発見系（Issue参照なし）
  /(?:問題|バグ|課題)[を]?(?:発見|見つけ|検出)/,
];

/** Issue作成済みを示すパターン（除外） */
const ISSUE_CREATED_PATTERNS = [
  /issue\s*#\d+\s*(?:を|が)?\s*作成(?:しました|済み)/i,
  /#\d+\s*(?:を|が)?\s*作成(?:しました|済み)/i,
  /gh\s+issue\s+create.*#\d+/i,
  // Gemini review: 「参照」パターンは誤検知の原因になるため削除
];

/** Issue番号への参照パターン */
const ISSUE_REFERENCE_PATTERN = /#\d+|issue\s+\d+/i;

interface TranscriptEntry {
  role?: string;
  content?: unknown;
}

interface ContentBlock {
  type?: string;
  text?: string;
}

/**
 * トランスクリプトから直近のassistant発言を取得
 */
function loadRecentAssistantMessages(transcriptPath: string, limit = 3): string[] {
  if (!transcriptPath || !existsSync(transcriptPath)) {
    return [];
  }

  const messages: string[] = [];

  try {
    const content = readFileSync(transcriptPath, "utf-8");
    const lines = content.trim().split("\n").reverse();

    for (const line of lines) {
      if (!line.trim()) continue;

      try {
        const entry = JSON.parse(line) as TranscriptEntry;
        if (entry.role !== "assistant") continue;

        const entryContent = entry.content;
        if (Array.isArray(entryContent)) {
          for (const block of entryContent) {
            if (
              typeof block === "object" &&
              block !== null &&
              (block as ContentBlock).type === "text"
            ) {
              const text = (block as ContentBlock).text;
              // Gemini review: 型安全性のためtypeof checkを追加
              if (typeof text === "string") {
                messages.push(text);
              }
            }
          }
        } else if (typeof entryContent === "string") {
          messages.push(entryContent);
        }

        if (messages.length >= limit) break;
      } catch {
        // JSON parse error, skip line
      }
    }
  } catch {
    // File read error
  }

  return messages;
}

/**
 * テキスト内にIssue番号への参照があるかチェック
 */
function hasIssueReference(text: string): boolean {
  return ISSUE_REFERENCE_PATTERN.test(text);
}

/**
 * Issue作成済みを示す表現があるかチェック
 */
function isIssueAlreadyCreated(text: string): boolean {
  return ISSUE_CREATED_PATTERNS.some((pattern) => pattern.test(text));
}

/**
 * マッチ箇所の周辺にIssue参照があるかチェック
 * 「Issue #123 で後で対応」のようなケースを除外するため
 */
function hasNearbyIssueReference(text: string, matchStart: number, matchEnd: number): boolean {
  // マッチ箇所の前後100文字を確認
  const contextStart = Math.max(0, matchStart - 100);
  const contextEnd = Math.min(text.length, matchEnd + 100);
  const context = text.slice(contextStart, contextEnd);
  return ISSUE_REFERENCE_PATTERN.test(context);
}

/**
 * マッチ箇所の周辺にIssue作成済み表現があるかチェック
 * 「Issue #123 を作成しました。残りは後で対応」のようなケースで、
 * Issue作成報告の近くにある別の「後で対応」は許可するため
 *
 * Issue #2828: mixed messagesでの偽陰性防止
 */
function hasNearbyIssueCreatedExpression(
  text: string,
  matchStart: number,
  matchEnd: number,
): boolean {
  // マッチ箇所の前後150文字を確認（Issue作成報告は長くなりがち）
  const contextStart = Math.max(0, matchStart - 150);
  const contextEnd = Math.min(text.length, matchEnd + 150);
  const context = text.slice(contextStart, contextEnd);
  return ISSUE_CREATED_PATTERNS.some((pattern) => pattern.test(context));
}

/**
 * Issue作成の必要性を検出
 *
 * Codex review対応: Issue参照があってもスキップしない。
 * ただし、マッチ箇所の近くにIssue参照またはIssue作成済み表現がある場合は除外
 * （例: 「Issue #123 で後で対応」はOK、「残りのバグは後で対応」はNG）
 *
 * Issue #2828: mixed messagesでの偽陰性防止
 * メッセージ全体をスキップするのではなく、各マッチ箇所ごとに周辺をチェック
 */
function detectIssueCreationNeed(messages: string[]): string | null {
  for (const message of messages) {
    // キーワード検出
    // Codex review: matchAll()で全マッチを検出（最初のマッチのみスキップで2つ目以降が漏れる問題を修正）
    for (const pattern of ISSUE_CREATION_PATTERNS) {
      // gフラグを追加したパターンでmatchAllを使用
      // Copilot review: gフラグチェックを変数に格納して可読性向上
      const flags = pattern.flags.includes("g") ? pattern.flags : `${pattern.flags}g`;
      const globalPattern = new RegExp(pattern.source, flags);
      const matches = message.matchAll(globalPattern);

      for (const match of matches) {
        if (match.index === undefined) continue;

        const matchStart = match.index;
        const matchEnd = match.index + match[0].length;

        // マッチ箇所の近くにIssue参照があればスキップ
        if (hasNearbyIssueReference(message, matchStart, matchEnd)) {
          continue;
        }

        // マッチ箇所の近くにIssue作成済み表現があればスキップ
        // Issue #2828: 「Issue #123 を作成しました。残りは後で対応」のケースを許可
        if (hasNearbyIssueCreatedExpression(message, matchStart, matchEnd)) {
          continue;
        }

        // マッチした周辺のテキストを抽出（コンテキスト）
        const start = Math.max(0, matchStart - 50);
        const end = Math.min(message.length, matchEnd + 50);
        return message.slice(start, end).trim();
      }
    }
  }

  return null;
}

/**
 * メイン処理
 */
async function main(): Promise<void> {
  let result: Record<string, unknown> = { continue: true };

  try {
    const hookInput = await parseHookInput();
    const transcriptPath = hookInput.transcript_path ?? "";

    if (!transcriptPath) {
      console.log(JSON.stringify(result));
      return;
    }

    // 直近のassistant発言を取得
    const messages = loadRecentAssistantMessages(transcriptPath, 3);
    if (messages.length === 0) {
      console.log(JSON.stringify(result));
      return;
    }

    // Issue作成の必要性を検出
    const detectedContext = detectIssueCreationNeed(messages);

    if (detectedContext) {
      const contextPreview =
        detectedContext.length > 200 ? `${detectedContext.slice(0, 200)}...` : detectedContext;

      result = {
        decision: "block",
        reason: `⚠️ Issue作成が必要な発言を検出しました

**検出箇所**: ${contextPreview}

**対応**: 以下のコマンドでIssueを作成してください:
\`\`\`
gh issue create --title "..." --body "..." --label P2
\`\`\`

Issue作成後、作業を続行できます。

[IMMEDIATE: gh issue create]`,
      };
    }
  } catch (error) {
    // エラー時はfail-open（ブロックしない）
    console.error(`[${HOOK_NAME}] Error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

// 実行（テスト時はスキップ）
if (import.meta.main) {
  main();
}

// テスト用にエクスポート
export {
  loadRecentAssistantMessages,
  hasIssueReference,
  isIssueAlreadyCreated,
  hasNearbyIssueReference,
  hasNearbyIssueCreatedExpression,
  detectIssueCreationNeed,
};
