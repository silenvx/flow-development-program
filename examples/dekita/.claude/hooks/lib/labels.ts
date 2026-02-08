/**
 * gh CLIコマンドからのラベル抽出・分析ユーティリティを提供する。
 *
 * Why:
 *   Issue/PR作成時のラベル検証・優先度チェックのため、
 *   コマンドからラベルを正確に抽出する必要がある。
 *
 * What:
 *   - extractLabelsFromCommand(): --labelオプションからラベル抽出
 *   - hasPriorityLabel(): P0-P3優先度ラベルの有無を判定
 *   - suggestLabelsFromText(): タイトル/本文からラベルを提案
 *
 * Remarks:
 *   - shlex-like splitting for robust parsing
 *   - カンマ区切りラベル（--label="bug,P1"）にも対応
 *   - priority:P0形式も認識
 *
 * Changelog:
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

// Default priority labels
export const DEFAULT_PRIORITY_LABELS = new Set(["P0", "P1", "P2", "P3"]);

// Label suggestion patterns (label, description, keywords)
export const LABEL_SUGGESTION_PATTERNS: Array<{
  keywords: string[];
  label: string;
  description: string;
}> = [
  {
    keywords: ["バグ", "bug", "エラー", "error", "不具合", "動かない", "fix"],
    label: "bug",
    description: "バグ報告",
  },
  {
    keywords: [
      "機能",
      "機能追加",
      "新機能",
      "新規",
      "feature",
      "feat",
      "enhancement",
      "改善",
      "拡張",
    ],
    label: "enhancement",
    description: "新機能・改善",
  },
  {
    keywords: ["ドキュメント", "document", "readme", "説明", "文書", "documentation"],
    label: "documentation",
    description: "ドキュメント",
  },
  {
    keywords: [
      "リファクタ",
      "refactor",
      "cleanup",
      "リファクタリング",
      "コード整理",
      "コードの整理",
      "実装の整理",
    ],
    label: "refactor",
    description: "リファクタリング",
  },
];

/**
 * Simple shlex-like split for command parsing.
 * Handles quoted strings and basic escaping.
 */
export function shellSplit(command: string): string[] {
  const tokens: string[] = [];
  let current = "";
  let inQuote: string | null = null;
  let isEscaped = false;

  for (let i = 0; i < command.length; i++) {
    const char = command[i];

    if (isEscaped) {
      current += char;
      isEscaped = false;
      continue;
    }

    if (char === "\\") {
      isEscaped = true;
      continue;
    }

    if (inQuote) {
      if (char === inQuote) {
        inQuote = null;
      } else {
        current += char;
      }
      continue;
    }

    if (char === '"' || char === "'") {
      inQuote = char;
      continue;
    }

    if (/\s/.test(char)) {
      if (current) {
        tokens.push(current);
        current = "";
      }
      continue;
    }

    current += char;
  }

  if (current) {
    tokens.push(current);
  }

  return tokens;
}

/**
 * Extract all label values from gh issue/pr create command.
 */
export function extractLabelsFromCommand(command: string): string[] {
  const labels: string[] = [];
  let tokens: string[];

  try {
    tokens = shellSplit(command);
  } catch {
    return labels;
  }

  let i = 0;
  while (i < tokens.length) {
    const token = tokens[i];

    // --label value or -l value
    if ((token === "--label" || token === "-l") && i + 1 < tokens.length) {
      labels.push(tokens[i + 1]);
      i += 2;
      continue;
    }

    // --label=value
    if (token.startsWith("--label=")) {
      labels.push(token.slice("--label=".length));
      i += 1;
      continue;
    }

    // -l=value
    if (token.startsWith("-l=")) {
      labels.push(token.slice("-l=".length));
      i += 1;
      continue;
    }

    i += 1;
  }

  return labels;
}

/**
 * Split comma-separated labels into individual labels.
 */
export function splitCommaSeparatedLabels(labels: string[]): string[] {
  const result: string[] = [];
  for (const labelValue of labels) {
    for (const label of labelValue.split(",")) {
      const trimmed = label.trim();
      if (trimmed) {
        result.push(trimmed);
      }
    }
  }
  return result;
}

/**
 * Extract highest priority label from a list of labels.
 */
export function extractPriorityFromLabels(
  labels: string[],
  priorityLabels: Set<string> = DEFAULT_PRIORITY_LABELS,
): string | null {
  const foundPriorities = new Set<string>();
  const upperPriorityLabels = new Set(Array.from(priorityLabels).map((p) => p.toUpperCase()));

  for (const labelValue of labels) {
    for (const label of labelValue.split(",")) {
      const upper = label.trim().toUpperCase();

      // Direct match (P0, P1, etc.)
      if (upperPriorityLabels.has(upper)) {
        foundPriorities.add(upper);
      }

      // priority:P0 format
      if (upper.startsWith("PRIORITY:")) {
        const priorityPart = upper.slice("PRIORITY:".length);
        if (upperPriorityLabels.has(priorityPart)) {
          foundPriorities.add(priorityPart);
        }
      }
    }
  }

  // Return highest priority (P0 > P1 > P2 > P3)
  for (const priority of ["P0", "P1", "P2", "P3"]) {
    if (foundPriorities.has(priority)) {
      return priority;
    }
  }

  return null;
}

/**
 * Check if any label contains a priority label.
 */
export function hasPriorityLabel(
  labels: string[],
  priorityLabels: Set<string> = DEFAULT_PRIORITY_LABELS,
): boolean {
  return extractPriorityFromLabels(labels, priorityLabels) !== null;
}

/**
 * Suggest labels based on issue title and body content.
 */
export function suggestLabelsFromText(
  title: string,
  body?: string | null,
): Array<{ label: string; description: string }> {
  let combinedText = (title || "").toLowerCase();
  if (body) {
    combinedText += ` ${body.toLowerCase()}`;
  }

  const suggestions: Array<{ label: string; description: string }> = [];
  const seenLabels = new Set<string>();

  for (const { keywords, label, description } of LABEL_SUGGESTION_PATTERNS) {
    if (seenLabels.has(label)) {
      continue;
    }

    // Escape special regex characters and join keywords
    const pattern = keywords
      .map((k) => k.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .join("|");

    if (new RegExp(pattern).test(combinedText)) {
      suggestions.push({ label, description });
      seenLabels.add(label);
    }
  }

  return suggestions;
}

/**
 * Extract argument value from a command string.
 */
function extractArgFromCommand(command: string, longOpt: string, shortOpt: string): string | null {
  let tokens: string[];

  try {
    tokens = shellSplit(command);
  } catch {
    return null;
  }

  let i = 0;
  while (i < tokens.length) {
    const token = tokens[i];

    if ((token === longOpt || token === shortOpt) && i + 1 < tokens.length) {
      return tokens[i + 1];
    }

    if (token.startsWith(`${longOpt}=`)) {
      return token.slice(`${longOpt}=`.length);
    }

    if (token.startsWith(`${shortOpt}=`)) {
      return token.slice(`${shortOpt}=`.length);
    }

    i += 1;
  }

  return null;
}

/**
 * Extract --title value from gh issue create command.
 */
export function extractTitleFromCommand(command: string): string | null {
  return extractArgFromCommand(command, "--title", "-t");
}

/**
 * Extract --body value from gh issue create command.
 */
export function extractBodyFromCommand(command: string): string | null {
  return extractArgFromCommand(command, "--body", "-b");
}
