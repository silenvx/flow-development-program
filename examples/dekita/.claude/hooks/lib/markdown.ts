/**
 * Markdown parsing utilities.
 *
 * Common utilities for detecting markdown structure elements like
 * list items and indented code blocks.
 *
 * Why:
 *   Multiple hooks need to parse markdown structure (e.g., plan_file_updater,
 *   plan_checklist_guard). Centralizing the logic ensures consistency.
 *
 * Changelog:
 *   - silenvx/dekita#2863: Python版追加
 *   - silenvx/dekita#2873: TypeScript移植
 */

/**
 * Strip only spaces and tabs from the start of a line.
 * Unlike trimStart(), this does not strip newlines or other whitespace.
 * This matches Python's lstrip() behavior for space/tab.
 */
function lstripSpacesAndTabs(line: string): string {
  let i = 0;
  while (i < line.length && (line[i] === " " || line[i] === "\t")) {
    i++;
  }
  return line.slice(i);
}

/**
 * Check if a line is a list item (-, *, +, or numbered).
 *
 * Markdown list markers require at least one space or tab after the marker.
 * Valid: "- item", "* item", "+ item", "1. item"
 * Invalid: "-item", "*bold*", "1.5 number"
 */
export function isListItem(line: string): boolean {
  const stripped = lstripSpacesAndTabs(line);
  if (!stripped) {
    return false;
  }
  if (
    ["-", "*", "+"].includes(stripped[0]) &&
    stripped.length > 1 &&
    [" ", "\t"].includes(stripped[1])
  ) {
    return true;
  }
  if (/^\d+\.\s/.test(stripped)) {
    return true;
  }
  return false;
}

/**
 * Check if a line is inside an indented code block.
 *
 * Indented code blocks in markdown:
 * - Have 4+ spaces of indentation
 * - Appear at file start or after a blank line (if preceded by non-list content)
 * - List-like lines after a blank line that follows a list item = nested list
 * - List-like lines after a blank line that follows non-list content = code block
 *
 * This function is designed to work with segments split by fenced code blocks (```).
 * Each segment is a contiguous portion of text between fenced code blocks.
 */
export function isInIndentedCodeBlock(
  lines: string[],
  lineIdx: number,
  isFirstSegment = true,
): boolean {
  const line = lines[lineIdx];

  // Must have 4+ spaces or tab indentation
  if (!/^(\s{4,}|\t)/.test(line)) {
    return false;
  }

  // At segment start with 4+ spaces:
  // - First segment (file start): indented code block
  // - After fenced block: assume nested list (not code)
  if (lineIdx === 0) {
    return isFirstSegment;
  }

  // Look backwards for context
  for (let i = lineIdx - 1; i >= 0; i--) {
    const prevLine = lines[i];
    if (prevLine.trim() === "") {
      // Found blank line - continue looking for context
      continue;
    }
    if (/^(\s{4,}|\t)/.test(prevLine)) {
      // Previous line is also indented - continue looking backwards
      continue;
    }
    // Found non-indented content
    // Check if this is list context or plain content context
    if (isListItem(prevLine)) {
      // Previous non-indented line is a list item
      // Current line could be a nested list or continuation
      if (isListItem(line)) {
        // Current line is also a list item = nested list, not code
        return false;
      }
      // Current line is not a list item, could be list continuation text
      return false;
    }
    // Previous non-indented line is not a list item
    // Check if there was a blank line between (indicating code block)
    for (let j = i + 1; j < lineIdx; j++) {
      if (lines[j].trim() === "") {
        // Blank line after non-list content = code block
        return true;
      }
    }
    // No blank line = not a code block (nested indentation)
    return false;
  }

  // Can't find non-indented context (all previous lines are 4+ indented)
  // - First segment: continuous code block from file start
  // - After fenced block: assume nested list to avoid false positives
  return isFirstSegment;
}

/**
 * Segment type for fenced code block splitting.
 */
export interface MarkdownSegment {
  isCodeBlock: boolean;
  lines: string[];
}

/**
 * Split content into segments separated by fenced code blocks.
 *
 * Each segment is an object with isCodeBlock and lines.
 */
export function splitByFencedCodeBlocks(content: string): MarkdownSegment[] {
  const segments: MarkdownSegment[] = [];
  let currentLines: string[] = [];
  let inCodeBlock = false;

  for (const line of content.split(/\r?\n/)) {
    if (line.trim().startsWith("```")) {
      // A code block fence indicates the end of the current segment.
      // Always append the segment, even if it's empty.
      segments.push({ isCodeBlock: inCodeBlock, lines: currentLines });
      currentLines = [];
      inCodeBlock = !inCodeBlock;
    } else {
      currentLines.push(line);
    }
  }

  // Always append the last segment, even if it's empty.
  segments.push({ isCodeBlock: inCodeBlock, lines: currentLines });

  return segments;
}
