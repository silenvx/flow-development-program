/**
 * JSON parsing utilities.
 *
 * Why:
 *   Provide common JSON parsing functions used across hooks and scripts.
 *   Centralize NDJSON (Newline Delimited JSON) parsing for gh api --paginate output.
 *
 * What:
 *   - parsePaginatedJson(): Parse NDJSON output from gh api --paginate
 *
 * Remarks:
 *   - Issue #3284: Consolidated from ci_monitor_ai_review.ts and review_comments.ts
 *   - Handles both single objects and arrays per line (gh api output variation)
 *
 * Changelog:
 *   - silenvx/dekita#3284: Initial creation from code consolidation
 */

// =============================================================================
// NDJSON Parsing
// =============================================================================

/**
 * Parse paginated JSON output from gh api --paginate.
 *
 * GitHub CLI's paginate flag outputs newline-delimited JSON (NDJSON).
 * Each line may contain a single object or an array of objects.
 *
 * @param output - The raw output from gh api --paginate
 * @returns Array of parsed objects
 *
 * @example
 * ```ts
 * // Single object per line
 * const items = parsePaginatedJson<{ id: number }>('{"id": 1}\n{"id": 2}');
 * // items = [{ id: 1 }, { id: 2 }]
 *
 * // Array per line (some gh api endpoints)
 * const items = parsePaginatedJson<{ id: number }>('[{"id": 1}, {"id": 2}]');
 * // items = [{ id: 1 }, { id: 2 }]
 * ```
 */
export function parsePaginatedJson<T = Record<string, unknown>>(output: string): T[] {
  const result: T[] = [];
  const stripped = output.trim();

  if (!stripped) {
    return result;
  }

  for (const rawLine of stripped.split("\n")) {
    const line = rawLine.trim();
    if (!line) {
      continue;
    }

    try {
      const parsed = JSON.parse(line) as T | T[];

      // Handle both single objects and arrays per line
      if (Array.isArray(parsed)) {
        result.push(...parsed);
      } else {
        result.push(parsed);
      }
    } catch (parseError) {
      // Log error message only, not raw data (security: avoid PII leakage)
      console.error(
        `Warning: Failed to parse JSON line: ${parseError instanceof Error ? parseError.message : parseError}`,
      );
    }
  }

  return result;
}
