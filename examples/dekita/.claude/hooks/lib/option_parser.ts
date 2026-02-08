/**
 * 汎用CLIオプションパーサー
 *
 * Why:
 *   gh/gitコマンドのオプション解析を統一的に行うため。
 *   各フックで重複するオプション解析ロジックを共通化し、
 *   バグ（値トークンのスキップ忘れ、オプション形式の不統一、複数回指定時の処理の不整合など）を防ぐ。
 *
 * What:
 *   - 宣言的なオプション定義
 *   - --option=value, --option value, -o=value, -o value 全形式対応
 *   - 値トークンの確実なスキップ
 *   - 複数回指定可能なオプション対応
 *   - ブールフラグ（hasValue: false）対応
 *
 * Changelog:
 *   - silenvx/dekita#3058: 初期実装
 *   - silenvx/dekita#3059: ブールフラグ対応追加
 *   - silenvx/dekita#3060: issue_label_check, issue_priority_label_check をライブラリに移行
 */

import { shellSplit } from "./labels";

/**
 * オプション定義
 */
export interface OptionDef {
  /** ロングオプション名（--なし）例: "body" */
  long: string;
  /** ショートオプション名（-なし）例: "b" */
  short?: string;
  /** 値を取るかどうか */
  hasValue: boolean;
  /** 複数回指定可能か（デフォルト: false = 最初の値のみ保持） */
  multiple?: boolean;
}

/**
 * パース結果
 * キー: ロングオプション名
 * 値: 値の配列
 *   - hasValue=true: 指定された値の配列（未指定なら空配列）
 *   - hasValue=false: フラグ指定時は["true"]、未指定なら空配列
 */
export type ParsedOptions = Map<string, string[]>;

/**
 * コマンド文字列をトークンに分割
 */
export function tokenize(command: string): string[] {
  try {
    return shellSplit(command);
  } catch {
    return [];
  }
}

/**
 * VAR=value形式の環境変数プレフィックスをスキップ
 */
export function skipEnvPrefixes(tokens: string[]): string[] {
  let cmdStart = 0;
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (token.includes("=") && !token.startsWith("-")) {
      cmdStart = i + 1;
    } else {
      break;
    }
  }
  return tokens.slice(cmdStart);
}

/**
 * トークン配列からオプションをパース
 *
 * @param tokens - トークン配列
 * @param definitions - オプション定義配列
 * @returns パース結果のMap
 *
 * @example
 * ```typescript
 * const defs: OptionDef[] = [
 *   { long: "body", short: "b", hasValue: true },
 *   { long: "label", short: "l", hasValue: true, multiple: true },
 * ];
 * const options = parseOptions(tokens, defs);
 * const body = options.get("body")?.[0];
 * const labels = options.get("label") ?? [];
 * ```
 */
export function parseOptions(tokens: string[], definitions: OptionDef[]): ParsedOptions {
  const result: ParsedOptions = new Map();

  // 初期化
  for (const def of definitions) {
    result.set(def.long, []);
  }

  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];

    for (const def of definitions) {
      let matched = false;
      let value: string | undefined;

      // --option=value
      const longPrefix = `--${def.long}=`;
      if (token.startsWith(longPrefix)) {
        // hasValue: false のオプションは = 形式の値を無視
        if (def.hasValue) {
          value = token.slice(longPrefix.length);
        }
        matched = true;
      }
      // --option value
      else if (token === `--${def.long}`) {
        if (def.hasValue) {
          if (i + 1 < tokens.length) {
            value = tokens[++i]; // インクリメントして値トークンをスキップ
          }
        }
        matched = true;
      }
      // -o=value (short form)
      else if (def.short) {
        const shortPrefix = `-${def.short}=`;
        if (token.startsWith(shortPrefix)) {
          // hasValue: false のオプションは = 形式の値を無視
          if (def.hasValue) {
            value = token.slice(shortPrefix.length);
          }
          matched = true;
        }
        // -o value (short form)
        else if (token === `-${def.short}`) {
          if (def.hasValue) {
            if (i + 1 < tokens.length) {
              value = tokens[++i]; // インクリメントして値トークンをスキップ
            }
          }
          matched = true;
        }
      }

      if (matched) {
        const values = result.get(def.long)!;
        if (value !== undefined) {
          if (def.multiple) {
            values.push(value);
          } else if (values.length === 0) {
            // 最初の値のみ保持
            values.push(value);
          }
        } else if (!def.hasValue) {
          // ブールフラグが指定された場合、"true"を追加
          if (def.multiple || values.length === 0) {
            values.push("true");
          }
        }
        break; // このトークンは処理済み
      }
    }
  }

  return result;
}

/**
 * パース結果から単一値を取得（最初の値）
 */
export function getOptionValue(options: ParsedOptions, name: string): string | null {
  const values = options.get(name);
  return values && values.length > 0 ? values[0] : null;
}

/**
 * パース結果から複数値を取得
 */
export function getOptionValues(options: ParsedOptions, name: string): string[] {
  return options.get(name) ?? [];
}

/**
 * パース結果からオプションが指定されたかどうかを確認
 * 主にブールフラグ（hasValue: false）の存在確認に使用
 */
export function hasOption(options: ParsedOptions, name: string): boolean {
  const values = options.get(name);
  return values !== undefined && values.length > 0;
}
