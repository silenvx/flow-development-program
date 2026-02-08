/**
 * GitHub API communication functions for ci-monitor.
 *
 * Why:
 *   Provide low-level GitHub CLI (gh) command execution with:
 *   - Consistent error handling
 *   - Rate limit detection and fallback
 *   - Repository information retrieval
 *
 * What:
 *   - runGhCommand(): Basic gh command execution (re-exported from lib/github.ts)
 *   - runGhCommandWithError(): Returns stderr for error diagnosis (re-exported)
 *   - isRateLimitError(): Detect rate limit errors
 *   - runGraphqlWithFallback(): GraphQL with automatic REST fallback
 *   - getRepoInfo(): Get owner/repo from current git repository
 *
 * Remarks:
 *   - Migrated from Python ci_monitor/github_api.py (Issue #3261)
 *   - Issue #3284: runGhCommand/runGhCommandWithError consolidated to lib/github.ts
 *
 * Changelog:
 *   - silenvx/dekita#3261: TypeScript migration from Python
 *   - silenvx/dekita#3284: Re-export from lib/github.ts to eliminate duplication
 */

// Import from shared library (Issue #3284)
import {
  GH_DEFAULT_TIMEOUT_MS,
  type GhCommandResult,
  type RulesetRequirements,
  getRulesetRequirements,
  runGhCommand,
  runGhCommandWithError,
} from "../../hooks/lib/github";

// Re-export for external consumers
export {
  type GhCommandResult,
  GH_DEFAULT_TIMEOUT_MS,
  getRulesetRequirements,
  type RulesetRequirements,
  runGhCommand,
  runGhCommandWithError,
};

/** Repository information */
export interface RepoInfo {
  owner: string;
  name: string;
}

// =============================================================================
// Constants
// =============================================================================

/** Rate limit error indicators (lowercase for comparison) */
const RATE_LIMIT_INDICATORS = [
  "rate_limited",
  "rate limit exceeded",
  "secondary rate limit",
  "abuse detection",
  "too many requests",
];

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Remove URLs from a line to allow rate limit pattern matching.
 *
 * Issue #1581: Instead of skipping entire lines with URLs, remove URL parts
 * to detect rate limit errors even when URL and error are on the same line.
 *
 * @param line - The line to process
 * @returns Line with URLs removed
 */
function removeUrlsFromLine(line: string): string {
  return line.replace(/https?:\/\/\S+/gi, "");
}

/**
 * Check if the error is due to GraphQL rate limiting.
 *
 * Issue #1096: Detect rate limit errors for automatic fallback.
 * Issue #1564: Improved detection to reduce false positives from URLs/docs.
 * Issue #1581: Remove URLs from lines instead of skipping entire lines.
 *
 * @param output - The stdout from gh command (may contain GraphQL error JSON)
 * @param stderr - The stderr from gh command
 * @returns True if the error is a rate limit error, false otherwise
 */
export function isRateLimitError(output: string, stderr = ""): boolean {
  const combined = output + stderr;

  for (const line of combined.split("\n")) {
    const lineWithoutUrls = removeUrlsFromLine(line);
    const lineLower = lineWithoutUrls.toLowerCase();

    if (RATE_LIMIT_INDICATORS.some((indicator) => lineLower.includes(indicator))) {
      return true;
    }
  }

  return false;
}

/**
 * Run a GraphQL command with automatic rate limit detection and optional fallback.
 *
 * Issue #1096: Automatic fallback when rate limited.
 *
 * @param args - Arguments for gh api graphql command
 * @param fallbackFn - Optional function to call if rate limited
 * @param timeout - Command timeout in milliseconds
 * @param printWarningFn - Optional function to print rate limit warning
 * @returns Tuple of [success, output, usedFallback]
 */
export async function runGraphqlWithFallback(
  args: string[],
  fallbackFn?: () => Promise<[boolean, string]>,
  timeout: number = GH_DEFAULT_TIMEOUT_MS,
  printWarningFn?: () => void,
): Promise<[boolean, string, boolean]> {
  const result = await runGhCommandWithError(args, timeout);

  if (result.success) {
    return [true, result.stdout, false];
  }

  if (isRateLimitError(result.stdout, result.stderr)) {
    if (printWarningFn) {
      printWarningFn();
    } else {
      console.error("Warning: GraphQL rate limit reached");
    }

    if (fallbackFn) {
      console.error("  -> Falling back to REST API...");
      const [fbSuccess, fbOutput] = await fallbackFn();
      if (fbSuccess) {
        return [true, fbOutput, true];
      }
      console.error("  Warning: Fallback also failed");
      // Return usedFallback=true even when fallback failed
      // so callers know fallback was attempted for logging/metrics
      return [false, result.stdout, true];
    }

    return [false, result.stdout, false];
  }

  return [false, result.stdout, false];
}

/**
 * Get the owner and repo name from the current git repository.
 *
 * @returns RepoInfo object or null if not in a git repository
 */
export async function getRepoInfo(): Promise<RepoInfo | null> {
  const [success, output] = await runGhCommand(["repo", "view", "--json", "owner,name"]);

  if (!success) {
    return null;
  }

  try {
    const data = JSON.parse(output) as { owner?: { login?: string }; name?: string };
    const owner = data.owner?.login;
    const name = data.name;

    if (owner && name) {
      return { owner, name };
    }
  } catch {
    // gh コマンドの出力が想定外（非 JSON 等）の場合は、リポジトリ情報なしとして扱う
  }

  return null;
}

/**
 * Get the full repository name (owner/repo) from the current git repository.
 *
 * @returns Full repository name or null if not in a git repository
 */
export async function getFullRepoName(): Promise<string | null> {
  const repoInfo = await getRepoInfo();
  if (repoInfo) {
    return `${repoInfo.owner}/${repoInfo.name}`;
  }
  return null;
}
