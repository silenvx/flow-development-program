/**
 * AIレビューコメントの矛盾可能性を検出する。
 *
 * Why:
 *   同一ファイルの近接行に複数コメントがある場合、
 *   矛盾の可能性を警告しレビュー品質を向上させるため。
 *
 * What:
 *   - detectPotentialContradictions(): 矛盾候補を検出
 *   - formatContradictionWarnings(): 警告メッセージをフォーマット
 *
 * Remarks:
 *   - 10行以内の近接コメントを矛盾候補として検出
 *   - 意味解析は行わず、人間のレビュー用にフラグのみ
 *   - ci_monitor_ts から呼び出される
 *
 * Changelog:
 *   - silenvx/dekita#1399: 矛盾コメント検出機能を追加
 *   - silenvx/dekita#1596: 同一バッチ内の近接検出を追加
 *   - silenvx/dekita#3643: TypeScriptに移植
 */

/** Distance threshold for "close" lines (comments less than this many lines apart may contradict) */
const PROXIMITY_THRESHOLD = 10;

/** Review comment structure */
export interface ReviewComment {
  path?: string;
  line?: number;
  body?: string;
}

/** Warning structure for potential contradictions */
export interface ContradictionWarning {
  /** The file path */
  file: string;
  /** Line number of previous comment (or first comment in batch) */
  prevLine: number;
  /** Line number of new comment (or second comment in batch) */
  newLine: number;
  /** Truncated body of previous comment (max 100 chars) */
  prevBody: string;
  /** Truncated body of new comment (max 100 chars) */
  newBody: string;
  /** True if prevBody was truncated */
  prevTruncated: boolean;
  /** True if newBody was truncated */
  newTruncated: boolean;
  /** True if both comments are from the same batch (first review) */
  sameBatch: boolean;
}

/**
 * Detect potential contradictions within a single batch of comments.
 *
 * Issue #1596: For first review batch where previousComments is empty,
 * check if multiple comments target the same file at close lines.
 *
 * @param comments - List of comments to check for internal proximity.
 * @returns List of warnings for comments at close lines within the same file.
 */
function detectWithinBatch(comments: ReviewComment[]): ContradictionWarning[] {
  const warnings: ContradictionWarning[] = [];

  // Compare each pair of comments (avoid duplicates by using i < j)
  for (let i = 0; i < comments.length; i++) {
    const first = comments[i];
    const firstPath = first.path;
    const firstLine = first.line;
    const firstBody = first.body ?? "";

    if (!firstPath || firstLine == null) {
      continue;
    }

    for (let j = i + 1; j < comments.length; j++) {
      const second = comments[j];
      const secondPath = second.path;
      const secondLine = second.line;
      const secondBody = second.body ?? "";

      if (secondPath !== firstPath || secondLine == null) {
        continue;
      }

      const distance = Math.abs(firstLine - secondLine);
      if (distance < PROXIMITY_THRESHOLD) {
        warnings.push({
          file: firstPath,
          prevLine: firstLine,
          newLine: secondLine,
          prevBody: firstBody.slice(0, 100),
          newBody: secondBody.slice(0, 100),
          prevTruncated: firstBody.length > 100,
          newTruncated: secondBody.length > 100,
          sameBatch: true,
        });
      }
    }
  }

  return warnings;
}

/**
 * Detect potential contradictions between new and previous review comments.
 *
 * Checks for comments on the same file within close line proximity.
 * Does NOT attempt semantic analysis - only flags for human review.
 *
 * Issue #1596: Always detects proximity within newComments (same-batch),
 * and additionally checks against previousComments if provided (cross-batch).
 *
 * @param newComments - List of new review comments.
 * @param previousComments - List of previous review comments.
 * @returns List of potential contradiction warnings.
 */
export function detectPotentialContradictions(
  newComments: ReviewComment[],
  previousComments: ReviewComment[] = [],
): ContradictionWarning[] {
  const warnings: ContradictionWarning[] = [];

  // Issue #1596: Always check for proximity within the current batch
  warnings.push(...detectWithinBatch(newComments));

  // If no previous comments, skip cross-batch check
  if (previousComments.length === 0) {
    return warnings;
  }

  for (const newComment of newComments) {
    const newPath = newComment.path;
    const newLine = newComment.line;
    const newBody = newComment.body ?? "";

    if (!newPath) {
      continue;
    }

    for (const prev of previousComments) {
      const prevPath = prev.path;
      const prevLine = prev.line;
      const prevBody = prev.body ?? "";

      if (prevPath !== newPath) {
        continue;
      }

      // Check line proximity (skip null/undefined line numbers from file-level or outdated comments)
      if (newLine != null && prevLine != null) {
        const distance = Math.abs(newLine - prevLine);
        if (distance < PROXIMITY_THRESHOLD) {
          warnings.push({
            file: newPath,
            prevLine: prevLine,
            newLine: newLine,
            prevBody: prevBody.slice(0, 100),
            newBody: newBody.slice(0, 100),
            prevTruncated: prevBody.length > 100,
            newTruncated: newBody.length > 100,
            sameBatch: false,
          });
        }
      }
    }
  }

  return warnings;
}

/**
 * Format contradiction warnings for display.
 *
 * @param warnings - List of warning dicts from detectPotentialContradictions.
 * @returns Formatted warning message string, or empty string if no warnings.
 */
export function formatContradictionWarnings(warnings: ContradictionWarning[]): string {
  if (warnings.length === 0) {
    return "";
  }

  const lines = ["⚠️ 同一ファイル・近接行への複数指摘を検出:"];

  for (const warning of warnings) {
    const prevSuffix = warning.prevTruncated ? "..." : "";
    const newSuffix = warning.newTruncated ? "..." : "";

    lines.push(`   ファイル: ${warning.file}`);
    if (warning.sameBatch) {
      // Issue #1596: First review batch - both comments are new
      lines.push(`   指摘1 (line ${warning.prevLine}): "${warning.prevBody}${prevSuffix}"`);
      lines.push(`   指摘2 (line ${warning.newLine}): "${warning.newBody}${newSuffix}"`);
      lines.push("   → 同一バッチ内で近接行に複数指摘。整合性を確認してください。");
    } else {
      lines.push(`   前回指摘 (line ${warning.prevLine}): "${warning.prevBody}${prevSuffix}"`);
      lines.push(`   今回指摘 (line ${warning.newLine}): "${warning.newBody}${newSuffix}"`);
      lines.push("   → 矛盾の可能性あり。人間の判断を優先してください。");
    }
    lines.push("");
  }

  return lines.join("\n");
}
