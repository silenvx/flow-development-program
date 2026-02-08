/**
 * Command parsing utilities for hook scripts.
 */

import { stripQuotedStrings } from "./strings";

/**
 * Read content from a file.
 */
async function readBodyFromFile(filePath: string): Promise<string | null> {
  try {
    const file = Bun.file(filePath);
    return await file.text();
  } catch {
    return null;
  }
}

/**
 * Extract comment body from command.
 *
 * Handles:
 * - HEREDOC patterns (checked first, highest priority)
 * - --body-file <file> (reads from file)
 * - -F body=@<file> (reads from file, gh api format)
 * - -b "message" or --body "message"
 * - -b $'message' (bash quoting)
 * - GraphQL body parameter
 */
export async function extractCommentBody(command: string): Promise<string | null> {
  // HEREDOC pattern (check first - highest priority)
  let match = command.match(/<<['"]?EOF['"]?\s*\n?(.*?)EOF/s);
  if (match) {
    return match[1];
  }

  // --body-file <file> (gh pr comment / gh issue comment)
  match = command.match(/--body-file\s+["']?([^\s"']+)["']?/);
  if (match) {
    return await readBodyFromFile(match[1]);
  }

  // -F body=@<file> (gh api format, file reference)
  match = command.match(/-F\s+body=@["']?([^\s"']+)["']?/);
  if (match) {
    return await readBodyFromFile(match[1]);
  }

  // -f body="message" or -F body="message" (gh api inline format)
  // Check double quotes first, then single quotes to handle nested quotes correctly
  match = command.match(/-[fF]\s+body="((?:[^"\\]|\\.)*)"/s);
  if (match) {
    return match[1];
  }
  match = command.match(/-[fF]\s+body='((?:[^'\\]|\\.)*)'/s);
  if (match) {
    return match[1];
  }

  // -b/--body $'message' (bash $'' quoting)
  match = command.match(/(?:-b|--body)\s+\$'(.+?)'/s);
  if (match) {
    return match[1];
  }

  // -b/--body "message" (double quotes)
  match = command.match(/(?:-b|--body)\s+"((?:[^"\\]|\\.)*)"/s);
  if (match && !match[1].startsWith("$(cat <<")) {
    return match[1];
  }

  // -b/--body 'message' (single quotes)
  match = command.match(/(?:-b|--body)\s+'((?:[^'\\]|\\.)*)'/s);
  if (match) {
    return match[1];
  }

  // GraphQL body parameter (in mutation)
  match = command.match(/body:\s*"((?:[^"\\]|\\.)*)"/s);
  if (match) {
    return match[1];
  }
  match = command.match(/body:\s*'((?:[^'\\]|\\.)*)'/s);
  if (match) {
    return match[1];
  }

  return null;
}

/**
 * Synchronous version of extractCommentBody for hooks that don't need file reading.
 * Only handles inline body patterns (no --body-file or -F body=@file).
 */
export function extractCommentBodySync(command: string): string | null {
  // HEREDOC pattern (check first - highest priority)
  let match = command.match(/<<['"]?EOF['"]?\s*\n?(.*?)EOF/s);
  if (match) {
    return match[1];
  }

  // -f body="message" or -F body="message" (gh api inline format)
  // Check double quotes first, then single quotes to handle nested quotes correctly
  match = command.match(/-[fF]\s+body="((?:[^"\\]|\\.)*)"/s);
  if (match) {
    return match[1];
  }
  match = command.match(/-[fF]\s+body='((?:[^'\\]|\\.)*)'/s);
  if (match) {
    return match[1];
  }

  // -b/--body $'message' (bash $'' quoting)
  match = command.match(/(?:-b|--body)\s+\$'(.+?)'/s);
  if (match) {
    return match[1];
  }

  // -b/--body "message" (double quotes)
  match = command.match(/(?:-b|--body)\s+"((?:[^"\\]|\\.)*)"/s);
  if (match && !match[1].startsWith("$(cat <<")) {
    return match[1];
  }

  // -b/--body 'message' (single quotes)
  match = command.match(/(?:-b|--body)\s+'((?:[^'\\]|\\.)*)'/s);
  if (match) {
    return match[1];
  }

  // GraphQL body parameter (in mutation)
  match = command.match(/body:\s*"((?:[^"\\]|\\.)*)"/s);
  if (match) {
    return match[1];
  }
  match = command.match(/body:\s*'((?:[^'\\]|\\.)*)'/s);
  if (match) {
    return match[1];
  }

  return null;
}

// Regex patterns for quoted content with escape handling
// Use [\s\S] instead of . to match escaped newlines
const DQ_CONTENT = '([^"\\\\]*(?:\\\\[\\s\\S][^"\\\\]*)*)'; // Double-quoted
const SQ_CONTENT = "([^'\\\\]*(?:\\\\[\\s\\S][^'\\\\]*)*)"; // Single-quoted

/**
 * Build patterns for extracting flag values from commands.
 *
 * @param longFlag - Long flag name (e.g., "body", "title")
 * @param shortFlag - Short flag name (e.g., "b", "t")
 * @param excludeSuffix - Optional suffix to exclude via negative lookahead (e.g., "-file" to avoid --body-file)
 */
function buildFlagPatterns(longFlag: string, shortFlag: string, excludeSuffix?: string): RegExp[] {
  const lookahead = excludeSuffix ? `(?!${excludeSuffix})` : "";

  return [
    // Quoted patterns: --flag="..." / --flag='...' / -f="..." / -f='...'
    // Apply excludeSuffix lookahead to all --longFlag patterns for consistency
    new RegExp(`(?:^|\\s)--${longFlag}${lookahead}="${DQ_CONTENT}"`),
    new RegExp(`(?:^|\\s)--${longFlag}${lookahead}='${SQ_CONTENT}'`),
    new RegExp(`(?:^|\\s)-${shortFlag}="${DQ_CONTENT}"`),
    new RegExp(`(?:^|\\s)-${shortFlag}='${SQ_CONTENT}'`),
    // Space-separated quoted: --flag "..." / --flag '...' / -f "..." / -f '...'
    new RegExp(`(?:^|\\s)--${longFlag}${lookahead}\\s+"${DQ_CONTENT}"`),
    new RegExp(`(?:^|\\s)--${longFlag}${lookahead}\\s+'${SQ_CONTENT}'`),
    new RegExp(`(?:^|\\s)-${shortFlag}\\s+"${DQ_CONTENT}"`),
    new RegExp(`(?:^|\\s)-${shortFlag}\\s+'${SQ_CONTENT}'`),
    // Unquoted: --flag=value / -f=value
    new RegExp(`(?:^|\\s)--${longFlag}${lookahead}=([^\\s"'][^\\s]*)`),
    new RegExp(`(?:^|\\s)-${shortFlag}=([^\\s"'][^\\s]*)`),
    // Space-separated unquoted: --flag value / -f value
    new RegExp(`(?:^|\\s)--${longFlag}${lookahead}\\s+([^\\s"'-][^\\s]*)`),
    new RegExp(`(?:^|\\s)-${shortFlag}\\s+([^\\s"'-][^\\s]*)`),
  ];
}

/**
 * Extract a flag value from a command using the given patterns.
 */
function extractFlagValue(command: string, patterns: RegExp[]): string | null {
  for (const pattern of patterns) {
    const match = command.match(pattern);
    if (match) {
      return match[1];
    }
  }
  return null;
}

// Pre-built patterns for body and title extraction
const BODY_PATTERNS = buildFlagPatterns("body", "b", "-file");
const TITLE_PATTERNS = buildFlagPatterns("title", "t");

/**
 * Extract PR body from gh pr create command.
 *
 * Issue #3242: Shared utility for robust PR body extraction.
 *
 * Handles:
 * - HEREDOC patterns: --body "$(cat <<'EOF' ... EOF)"
 * - Double-quoted: --body="..." or --body "..."
 * - Single-quoted: --body='...' or --body '...'
 * - Short form: -b="..." or -b "..."
 * - Unquoted: --body=value or --body value (until next flag or end)
 *
 * Returns null if body is not explicitly specified inline.
 * Does not handle --body-file (use hasBodyFileOption to check).
 */
export function extractPrBody(command: string): string | null {
  // HEREDOC pattern first (most complex, highest priority)
  // Use (?:^|\s) for word boundary, [^\s'"]+ for hyphenated delimiters like END-OF-MESSAGE
  const heredocMatch = command.match(
    /(?:^|\s)--body\s+"\$\(cat\s+<<['\"]?([^\s'"]+)['\"]?\s*([\s\S]*?)\s*\1\s*\)"/,
  );
  if (heredocMatch) {
    return heredocMatch[2];
  }

  return extractFlagValue(command, BODY_PATTERNS);
}

/**
 * Extract PR title from gh pr create command.
 *
 * Issue #3242: Shared utility for robust PR title extraction.
 *
 * Handles:
 * - Double-quoted: --title="..." or --title "..."
 * - Single-quoted: --title='...' or --title '...'
 * - Short form: -t="..." or -t "..."
 * - Unquoted: --title=value or --title value (until next flag or end)
 *
 * Returns null if title is not explicitly specified.
 */
export function extractPrTitle(command: string): string | null {
  return extractFlagValue(command, TITLE_PATTERNS);
}

/**
 * Check if command uses --body-file or -F option.
 *
 * Issue #3242: Detect when body content comes from a file.
 * These options load body from file/template, so inline extraction won't work.
 */
export function hasBodyFileOption(command: string): boolean {
  const stripped = stripQuotedStrings(command);
  // Note: -F can be followed by value directly (e.g., -Ffile.txt)
  // Both --body-file and -F must be preceded by whitespace or start-of-string
  // to avoid matching words like "Feature" or flags like "--custom--body-file"
  return /(?:(?:^|\s)--body-file\b|(?:^|\s)-F)/.test(stripped);
}
