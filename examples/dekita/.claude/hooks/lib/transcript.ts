/**
 * トランスクリプトファイルからの情報抽出を行う共通関数を提供する。
 *
 * Why:
 *   トランスクリプト処理ロジックを複数フックで重複実装しないため。
 *
 * What:
 *   - isInCodeBlock(): コードブロック内判定
 *   - extractAssistantResponses(): assistant応答テキスト抽出
 *   - extractBashCommands(): Bashコマンド抽出
 *   - loadTranscript(): JSON/JSONL両形式のトランスクリプト読み込み
 *
 * Remarks:
 *   - JSONL形式（1行1JSON）とJSON配列形式の両方に対応
 *   - 正規表現フォールバックでパース失敗時も可能な限り抽出
 *   - 空文字列のcontentは意図的に除外
 *
 * Changelog:
 *   - silenvx/dekita#1915: askuser-suggestionとdefer-keyword-checkから共通化
 *   - silenvx/dekita#2254: JSONL形式サポート追加
 *   - silenvx/dekita#2261: 4つのStop hookからload_transcriptを共通化
 *   - silenvx/dekita#2874: TypeScript移行
 *   - silenvx/dekita#3012: extractBashCommands()追加
 *   - silenvx/dekita#3744: 正規表現パターンを定数化
 */

import { existsSync, readFileSync } from "node:fs";
import { isSafeTranscriptPath } from "./path_validation";

// ============================================================================
// 共通正規表現パターン
// ============================================================================

/**
 * JSONエスケープ対応の文字列マッチングパターン
 *
 * JSON文字列内のエスケープシーケンス（\n, \", \\等）を正しく処理する。
 * 使用例: "value"\s*:\s*"(ESCAPED_STRING_PATTERN)"
 *
 * @remarks
 * - [^"\\] : 通常の文字（ダブルクォートとバックスラッシュ以外）
 * - \\.    : エスケープシーケンス（バックスラッシュ + 任意の文字）
 * - *      : 0回以上の繰り返し
 */
const ESCAPED_STRING_PATTERN = `(?:[^"\\\\]|\\\\.)*`;

/**
 * tool_use オブジェクトの検出パターン（type先行）
 *
 * {"type": "tool_use", ..., "name": "ToolName", ...} 形式を検出
 * [^{}]* でネストされた {} を含まない範囲をスキップ
 */
const TOOL_USE_TYPE_FIRST_PATTERN = `"type"\\s*:\\s*"tool_use"[^{}]*"name"\\s*:\\s*"(${ESCAPED_STRING_PATTERN})"`;

/**
 * tool_use オブジェクトの検出パターン（name先行）
 *
 * {"name": "ToolName", ..., "type": "tool_use", ...} 形式を検出
 * JSONはフィールド順序が不定なため、両方のパターンが必要
 */
const TOOL_USE_NAME_FIRST_PATTERN = `"name"\\s*:\\s*"(${ESCAPED_STRING_PATTERN})"\\s*[^{}]*"type"\\s*:\\s*"tool_use"`;

/**
 * マッチ位置がコードブロック内かチェック
 *
 * @param text 検索対象のテキスト
 * @param matchPos マッチ位置（文字列のインデックス）
 * @returns コードブロック内であれば true
 *
 * @example
 * const text = "Hello ```code``` world";
 * isInCodeBlock(text, 10);  // "code" の位置 -> true
 * isInCodeBlock(text, 20);  // "world" の位置 -> false
 */
export function isInCodeBlock(text: string, matchPos: number): boolean {
  const beforeMatch = text.slice(0, matchPos);
  const codeBlockStarts = beforeMatch.match(/```/g);
  return codeBlockStarts ? codeBlockStarts.length % 2 === 1 : false;
}

export interface ContentBlock {
  type?: string;
  text?: string;
  name?: string;
  input?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface TranscriptEntry {
  role?: string;
  content?: string | ContentBlock[];
  [key: string]: unknown;
}

/**
 * トランスクリプトからassistantの応答を抽出
 *
 * 以下の形式に対応:
 * - JSONL形式（1行1JSON）
 * - JSON配列形式
 * - 正規表現によるフォールバック（上記が失敗した場合）
 *
 * @param content トランスクリプトファイルの内容
 * @returns assistantの応答テキストのリスト
 *
 * @example
 * const content = '{"role": "assistant", "content": "Hello"}\n';
 * extractAssistantResponses(content);  // ['Hello']
 *
 * @remarks
 * 空文字列のcontentは意図的に除外される（Issue #1933）。
 *
 * 設計根拠:
 * 1. 呼び出し元（defer-keyword-check、askuser-suggestion等）は
 *    応答テキスト内のキーワードを検索する。空文字列では
 *    検索対象がなく、処理しても意味がない。
 * 2. 空文字列を返すと呼び出し側で空チェックが必要になり、
 *    コードが複雑化する。
 * 3. JSONL処理、JSON配列処理、正規表現フォールバック全てで
 *    同じ動作（空を除外）を保証することで一貫性を維持。
 */
export function extractAssistantResponses(content: string): string[] {
  const responses: string[] = [];

  // JSONLフォーマット（1行1JSON）の場合
  for (const line of content.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    try {
      const obj = JSON.parse(trimmed) as TranscriptEntry;
      // objがdictの場合のみ処理（配列の場合はスキップ）
      if (obj && typeof obj === "object" && !Array.isArray(obj)) {
        if (obj.role === "assistant" && obj.content) {
          // contentが文字列の場合
          if (typeof obj.content === "string") {
            responses.push(obj.content);
          } else if (Array.isArray(obj.content)) {
            // contentがブロック配列の場合（Claude APIレスポンス形式）
            for (const block of obj.content) {
              if (
                typeof block === "object" &&
                block !== null &&
                "text" in block &&
                typeof block.text === "string"
              ) {
                responses.push(block.text);
              }
            }
          }
        }
      }
    } catch {
      // Skip invalid JSON lines
    }
  }

  // JSON配列フォーマットの場合
  if (responses.length === 0) {
    try {
      const data = JSON.parse(content) as TranscriptEntry[];
      if (Array.isArray(data)) {
        for (const item of data) {
          if (item.role === "assistant" && item.content) {
            // contentが文字列の場合
            if (typeof item.content === "string") {
              responses.push(item.content);
            } else if (Array.isArray(item.content)) {
              // contentがブロック配列の場合（Claude APIレスポンス形式）
              for (const block of item.content) {
                if (
                  typeof block === "object" &&
                  block !== null &&
                  "text" in block &&
                  typeof block.text === "string"
                ) {
                  responses.push(block.text);
                }
              }
            }
          }
        }
      }
    } catch {
      // JSONLでもJSON配列でもない場合は正規表現にフォールバック
    }
  }

  // フォールバック: 正規表現（エスケープ対応）
  if (responses.length === 0) {
    // JSONエスケープされた文字列を考慮（ESCAPED_STRING_PATTERN使用）
    const pattern = new RegExp(
      `"role"\\s*:\\s*"assistant"[^}]*"content"\\s*:\\s*"(${ESCAPED_STRING_PATTERN})"`,
      "g",
    );

    for (const match of content.matchAll(pattern)) {
      try {
        // JSONエスケープをデコード
        const decoded = JSON.parse(`"${match[1]}"`) as string;
        if (decoded) {
          // 空文字列を除外（JSONL処理と一貫）
          responses.push(decoded);
        }
      } catch {
        if (match[1]) {
          // 空文字列を除外
          responses.push(match[1]);
        }
      }
    }
  }

  return responses;
}

/**
 * トランスクリプトからBashコマンドを抽出
 *
 * @param content トランスクリプトファイルの内容
 * @returns Bashコマンドのリスト
 *
 * @example
 * const content = '{"role": "assistant", "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "gh issue create ..."}}]}';
 * extractBashCommands(content);  // ['gh issue create ...']
 */
export function extractBashCommands(content: string): string[] {
  const commands: string[] = [];

  // JSONLフォーマット（1行1JSON）の場合
  for (const line of content.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    try {
      const obj = JSON.parse(trimmed) as TranscriptEntry;
      if (obj && typeof obj === "object" && !Array.isArray(obj)) {
        if (obj.role === "assistant" && Array.isArray(obj.content)) {
          for (const block of obj.content as ContentBlock[]) {
            if (
              typeof block === "object" &&
              block !== null &&
              block.type === "tool_use" &&
              block.name === "Bash" &&
              block.input &&
              typeof block.input.command === "string"
            ) {
              commands.push(block.input.command);
            }
          }
        }
      }
    } catch {
      // Skip invalid JSON lines
    }
  }

  // JSON配列フォーマットの場合
  if (commands.length === 0) {
    try {
      const data = JSON.parse(content) as TranscriptEntry[];
      if (Array.isArray(data)) {
        for (const item of data) {
          if (item.role === "assistant" && Array.isArray(item.content)) {
            for (const block of item.content as ContentBlock[]) {
              if (
                typeof block === "object" &&
                block !== null &&
                block.type === "tool_use" &&
                block.name === "Bash" &&
                block.input &&
                typeof block.input.command === "string"
              ) {
                commands.push(block.input.command);
              }
            }
          }
        }
      }
    } catch {
      // Parse error, try regex fallback
    }
  }

  // フォールバック: 正規表現（tool_use Bashコマンドを検出）
  if (commands.length === 0) {
    // Bash tool_use パターンを検出
    // [^{}]* を使用してネストされたオブジェクトを正しく処理
    // ESCAPED_STRING_PATTERN で文字列内のエスケープに対応
    const pattern = new RegExp(
      `"type"\\s*:\\s*"tool_use"[^{}]*"name"\\s*:\\s*"Bash"[^{}]*"input"\\s*:\\s*\\{[^{}]*"command"\\s*:\\s*"(${ESCAPED_STRING_PATTERN})"`,
      "g",
    );

    for (const match of content.matchAll(pattern)) {
      try {
        const decoded = JSON.parse(`"${match[1]}"`) as string;
        if (decoded) {
          commands.push(decoded);
        }
      } catch {
        if (match[1]) {
          commands.push(match[1]);
        }
      }
    }
  }

  return commands;
}

/**
 * ツール使用の抽出結果
 */
export interface ToolUse {
  name: string;
  input?: Record<string, unknown>;
}

/**
 * ContentBlockからツール使用を抽出するヘルパー
 */
function extractToolUsesFromContentBlocks(
  blocks: ContentBlock[],
  toolName: string | undefined,
  result: ToolUse[],
): void {
  for (const block of blocks) {
    if (
      typeof block === "object" &&
      block !== null &&
      block.type === "tool_use" &&
      typeof block.name === "string"
    ) {
      if (!toolName || block.name === toolName) {
        result.push({
          name: block.name,
          input: block.input,
        });
      }
    }
  }
}

/**
 * トランスクリプトから指定ツールの使用を抽出
 *
 * 以下の形式に対応:
 * - JSON配列形式
 * - 単一JSONオブジェクト形式
 * - JSONL形式（1行1JSON）
 * - 正規表現によるフォールバック（上記が失敗した場合）
 *
 * @param content トランスクリプトファイルの内容
 * @param toolName 抽出対象のツール名（省略時は全ツール）
 * @returns ツール使用のリスト
 *
 * @remarks
 * 正規表現フォールバック使用時は `input` が `undefined` となる。
 * `input` に依存する処理（例: `isPlanFileWrite`）は、フォールバック経由の
 * ツール使用では期待通りに動作しない点に注意。
 *
 * @example
 * const content = '{"role": "assistant", "content": [{"type": "tool_use", "name": "ExitPlanMode", "input": {}}]}';
 * extractToolUses(content, "ExitPlanMode");  // [{ name: "ExitPlanMode", input: {} }]
 *
 * Changelog:
 *   - silenvx/dekita#3454: plan_mode_exit_check用に追加
 *   - silenvx/dekita#3717: 正規表現フォールバック追加
 */
export function extractToolUses(content: string, toolName?: string): ToolUse[] {
  const toolUses: ToolUse[] = [];

  // まずJSON配列または単一JSONオブジェクトとしてパースを試行
  // パース成功時は早期リターンし、正規表現フォールバックは使用しない
  // 理由: ユーザーメッセージ内のコード例などから誤検出を防ぐ
  try {
    const data = JSON.parse(content);

    // JSON配列フォーマットの場合
    if (Array.isArray(data)) {
      for (const item of data as TranscriptEntry[]) {
        if (item.role === "assistant" && Array.isArray(item.content)) {
          extractToolUsesFromContentBlocks(item.content as ContentBlock[], toolName, toolUses);
        }
      }
      return toolUses;
    }

    // 単一オブジェクトの場合
    if (typeof data === "object" && data !== null) {
      // トランスクリプトエントリ（roleを持つ）であれば処理して返す
      if ("role" in data) {
        const obj = data as TranscriptEntry;
        if (obj.role === "assistant" && Array.isArray(obj.content)) {
          extractToolUsesFromContentBlocks(obj.content as ContentBlock[], toolName, toolUses);
        }
        return toolUses;
      }
      // 直接のtool_useオブジェクトの場合（inputを保持）
      // 有効なJSONオブジェクトなら正規表現フォールバックは使用しない
      const toolUseObj = data as {
        type?: string;
        name?: string;
        input?: Record<string, unknown>;
      };
      if (
        toolUseObj.type === "tool_use" &&
        toolUseObj.name &&
        typeof toolUseObj.name === "string"
      ) {
        if (!toolName || toolUseObj.name === toolName) {
          toolUses.push({ name: toolUseObj.name, input: toolUseObj.input });
        }
      }
      // 有効なJSONオブジェクトとしてパース成功したので早期リターン
      return toolUses;
    }
  } catch {
    // 全体パース失敗、JSONLとして行ごとにパース
  }

  // JSONLフォーマット（1行1JSON）の場合
  // 有効なトランスクリプトエントリ（role/contentを持つ）があれば、正規表現フォールバックは使用しない
  // 理由: 有効なJSONL内のユーザーメッセージからの誤検出を防ぐ
  let validTranscriptEntryFound = false;
  for (const line of content.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    try {
      const obj = JSON.parse(trimmed);
      if (obj && typeof obj === "object" && !Array.isArray(obj)) {
        // トランスクリプトエントリ（roleとcontentを持つ）の場合
        // contentは配列または文字列（ユーザーメッセージなど）どちらでも有効
        // 誤検出防止のため、有効なエントリがあればフォールバックは使用しない
        if (
          "role" in obj &&
          "content" in obj &&
          (Array.isArray(obj.content) || typeof obj.content === "string")
        ) {
          validTranscriptEntryFound = true;
          const entry = obj as TranscriptEntry;
          if (entry.role === "assistant" && Array.isArray(entry.content)) {
            extractToolUsesFromContentBlocks(entry.content as ContentBlock[], toolName, toolUses);
          }
        } else if (!("role" in obj)) {
          // 直接のtool_useオブジェクトの場合（inputを保持）
          const toolUseObj = obj as {
            type?: string;
            name?: string;
            input?: Record<string, unknown>;
          };
          if (
            toolUseObj.type === "tool_use" &&
            toolUseObj.name &&
            typeof toolUseObj.name === "string"
          ) {
            if (!toolName || toolUseObj.name === toolName) {
              toolUses.push({ name: toolUseObj.name, input: toolUseObj.input });
            }
          }
        }
      }
    } catch {
      // Skip invalid JSON lines
    }
  }

  // 有効なトランスクリプトエントリが見つかった、または直接のtool_useが見つかった場合は結果を返す
  // 正規表現フォールバックは使用しない（重複抽出を防ぐ）
  if (validTranscriptEntryFound || toolUses.length > 0) {
    return toolUses;
  }

  // フォールバック: 正規表現（tool_useを検出）
  // JSONパースが完全に失敗した場合のみ使用
  // tool_use パターン: {"type": "tool_use", "name": "ToolName", "input": {...}}
  // [^{}]* では入れ子オブジェクトを処理できないため、nameのみを抽出
  // 注: inputは抽出困難なためundefinedとなる。呼び出し元はこれを考慮する必要がある
  // typeとnameの順序が逆の場合も考慮（JSONは順序不定）
  // 共通パターン定数（TOOL_USE_TYPE_FIRST_PATTERN, TOOL_USE_NAME_FIRST_PATTERN）を使用
  const patternTypeFirst = new RegExp(TOOL_USE_TYPE_FIRST_PATTERN, "g");
  const patternNameFirst = new RegExp(TOOL_USE_NAME_FIRST_PATTERN, "g");

  // 同一位置での重複マッチを防ぐ（同じtool_useが両パターンでマッチする可能性があるため）
  // 同一ツールの複数回呼び出しは許容する（countPlanFileWrites等で必要）
  const matches: { index: number; name: string }[] = [];

  for (const pattern of [patternTypeFirst, patternNameFirst]) {
    for (const match of content.matchAll(pattern)) {
      let name: string;
      try {
        name = JSON.parse(`"${match[1]}"`) as string;
      } catch {
        // JSONデコード失敗時は生の値を使用
        name = match[1];
      }

      if (name && (!toolName || name === toolName)) {
        // 同じ位置・同じ名前でのマッチは重複とみなす（両パターンでマッチした場合）
        const matchIndex = match.index ?? 0;
        if (!matches.some((m) => m.index === matchIndex && m.name === name)) {
          matches.push({ index: matchIndex, name });
        }
      }
    }
  }

  // ファイル内の出現順序を維持して結果に追加
  for (const m of matches.sort((a, b) => a.index - b.index)) {
    // 正規表現フォールバックではinputの抽出が困難なため、undefinedとする
    toolUses.push({ name: m.name, input: undefined });
  }

  return toolUses;
}

/**
 * Load and parse the transcript file.
 *
 * Supports both JSON (.json) and JSON Lines (.jsonl) formats.
 *
 * @param transcriptPath Path to the transcript file.
 * @returns Parsed transcript as list of message dicts, or null on error.
 *
 * @remarks
 * Issue #2254: Added JSONL format support.
 * Issue #2261: Extracted to common utility from 4 Stop hooks.
 */
export function loadTranscript(transcriptPath: string): TranscriptEntry[] | null {
  if (!isSafeTranscriptPath(transcriptPath)) {
    return null;
  }

  try {
    if (!existsSync(transcriptPath)) {
      return null;
    }

    const content = readFileSync(transcriptPath, "utf-8");

    // Issue #2254: Support JSONL format (one JSON object per line)
    if (transcriptPath.endsWith(".jsonl")) {
      const lines = content.trim().split("\n");
      return lines.filter((line) => line.trim()).map((line) => JSON.parse(line) as TranscriptEntry);
    }

    // Standard JSON format
    return JSON.parse(content) as TranscriptEntry[];
  } catch (error) {
    // Log parse/read errors for debugging (ENOENT is expected in some cases)
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      console.error(`[transcript] Failed to load or parse transcript at ${transcriptPath}:`, error);
    }
    return null;
  }
}
