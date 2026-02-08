/**
 * GitHub API rate limit management for ci-monitor.
 *
 * Why:
 *   Prevent hitting GitHub API rate limits during CI monitoring by:
 *   - Caching rate limit data (in-memory and file-based)
 *   - Adjusting polling intervals based on remaining requests
 *   - Proactively switching to REST API when GraphQL quota is low
 *
 * What:
 *   - checkRateLimit(): Check current rate limit with caching
 *   - getAdjustedInterval(): Adjust polling interval based on rate limit
 *   - shouldPreferRestApi(): Determine if REST API should be preferred
 *   - logRateLimitEvent(): Log rate limit events for analysis
 *
 * Remarks:
 *   - Migrated from Python ci_monitor/rate_limit.py (Issue #3261)
 *   - Uses asyncSpawn for gh CLI calls
 *   - Thread-safety not needed in Bun (single-threaded)
 *
 * Changelog:
 *   - silenvx/dekita#3261: TypeScript migration from Python
 */

import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import {
  EXECUTION_LOG_DIR,
  RATE_LIMIT_ADJUST_THRESHOLD,
  RATE_LIMIT_CACHE_TTL,
  RATE_LIMIT_CRITICAL_THRESHOLD,
  RATE_LIMIT_REST_PRIORITY_THRESHOLD,
  RATE_LIMIT_WARNING_THRESHOLD,
} from "./constants";
import { logToSessionFile } from "./logging";
import { asyncSpawn } from "./spawn";
import type { RateLimitEventType, RateLimitInfo } from "./types";

// =============================================================================
// Cache Configuration
// =============================================================================

/** File path for cross-session rate limit cache */
const RATE_LIMIT_FILE_CACHE_PATH = ".claude/logs/execution/rate-limit-cache.json";

/** In-memory cache for rate limit data */
interface RateLimitCache {
  remaining: number;
  limit: number;
  resetTimestamp: number;
  cachedAt: number;
}

let rateLimitCache: RateLimitCache | null = null;

/** Track REST priority mode state to avoid repeated logging */
let restPriorityModeActive = false;

// =============================================================================
// Formatting Utilities
// =============================================================================

/**
 * Format reset timestamp to human-readable time.
 *
 * @param resetTimestamp - Unix timestamp when the limit resets
 * @returns Tuple of [seconds until reset, human-readable time string]
 */
export function formatResetTime(resetTimestamp: number): [number, string] {
  if (resetTimestamp === 0) {
    return [0, "不明"];
  }

  const now = Math.floor(Date.now() / 1000);
  const secondsUntilReset = Math.max(0, resetTimestamp - now);

  if (secondsUntilReset <= 0) {
    return [0, "まもなく"];
  }
  if (secondsUntilReset < 60) {
    return [secondsUntilReset, `${secondsUntilReset}秒`];
  }
  const minutes = Math.floor(secondsUntilReset / 60);
  return [secondsUntilReset, `${minutes}分`];
}

// =============================================================================
// File Cache Operations
// =============================================================================

/**
 * Read rate limit from file cache for cross-session sharing.
 *
 * @returns Cached rate limit data, or null if cache is invalid/stale
 */
async function readRateLimitFileCache(): Promise<RateLimitCache | null> {
  try {
    const content = await readFile(RATE_LIMIT_FILE_CACHE_PATH, "utf-8");
    const data = JSON.parse(content) as {
      timestamp: number;
      remaining: number;
      limit: number;
      reset: number;
    };

    const cachedAt = data.timestamp;

    // Validate timestamp: reject non-numeric, negative, or future values
    if (typeof cachedAt !== "number" || cachedAt < 0) {
      return null;
    }

    const now = Date.now() / 1000;
    if (cachedAt > now) {
      return null; // Future timestamp (clock skew or malicious data)
    }

    if (now - cachedAt < RATE_LIMIT_CACHE_TTL) {
      return {
        remaining: data.remaining,
        limit: data.limit,
        resetTimestamp: data.reset,
        cachedAt,
      };
    }
  } catch {
    // Cache file corrupted/missing/invalid - return null to trigger API call
  }
  return null;
}

/**
 * Write rate limit to file cache for cross-session sharing.
 *
 * @param remaining - Remaining API calls
 * @param limit - Total API call limit
 * @param resetTimestamp - Unix timestamp when the limit resets
 */
async function writeRateLimitFileCache(
  remaining: number,
  limit: number,
  resetTimestamp: number,
): Promise<void> {
  try {
    const cacheDir = dirname(RATE_LIMIT_FILE_CACHE_PATH);
    await mkdir(cacheDir, { recursive: true });
    const data = {
      timestamp: Date.now() / 1000,
      remaining,
      limit,
      reset: resetTimestamp,
    };
    // Use atomic write pattern (write to temp + rename) to prevent corruption on interruption
    const tempFile = `${RATE_LIMIT_FILE_CACHE_PATH}.tmp`;
    await writeFile(tempFile, JSON.stringify(data), "utf-8");
    await rename(tempFile, RATE_LIMIT_FILE_CACHE_PATH);
  } catch {
    // Cache write failure is non-critical
  }
}

// =============================================================================
// Core Rate Limit Functions
// =============================================================================

/**
 * Check GitHub API rate limit.
 *
 * Uses a two-level cache (in-memory, then file) to reduce API calls.
 * Cache is valid for RATE_LIMIT_CACHE_TTL seconds.
 *
 * @param useCache - If true, return cached value if available and fresh
 * @returns Rate limit info: { remaining, limit, resetTimestamp }
 */
export async function checkRateLimit(
  useCache = true,
): Promise<{ remaining: number; limit: number; resetTimestamp: number }> {
  // 1. Check in-memory cache first
  if (useCache && rateLimitCache !== null) {
    const now = Date.now() / 1000;
    if (now - rateLimitCache.cachedAt < RATE_LIMIT_CACHE_TTL) {
      return {
        remaining: rateLimitCache.remaining,
        limit: rateLimitCache.limit,
        resetTimestamp: rateLimitCache.resetTimestamp,
      };
    }
  }

  // 2. Check file cache for cross-session sharing
  if (useCache) {
    const fileCache = await readRateLimitFileCache();
    if (fileCache !== null) {
      // Update in-memory cache from file cache
      rateLimitCache = fileCache;
      return {
        remaining: fileCache.remaining,
        limit: fileCache.limit,
        resetTimestamp: fileCache.resetTimestamp,
      };
    }
  }

  // 3. API call
  const result = await asyncSpawn("gh", [
    "api",
    "rate_limit",
    "--jq",
    ".resources.graphql | [.remaining, .limit, .reset] | @tsv",
  ]);

  if (result.success && result.stdout) {
    const parts = result.stdout.trim().split("\t");
    if (parts.length >= 3) {
      const remaining = Number.parseInt(parts[0], 10);
      const limit = Number.parseInt(parts[1], 10);
      const resetTimestamp = Number.parseInt(parts[2], 10);

      if (!Number.isNaN(remaining) && !Number.isNaN(limit) && !Number.isNaN(resetTimestamp)) {
        // Update both caches
        const now = Date.now() / 1000;
        rateLimitCache = { remaining, limit, resetTimestamp, cachedAt: now };
        await writeRateLimitFileCache(remaining, limit, resetTimestamp);

        return { remaining, limit, resetTimestamp };
      }
    }
  }

  // API check failed
  return { remaining: 0, limit: 0, resetTimestamp: 0 };
}

/**
 * Get the time until rate limit resets.
 *
 * @returns Tuple of [seconds until reset, human-readable time string]
 */
export async function getRateLimitResetTime(): Promise<[number, string]> {
  const { resetTimestamp } = await checkRateLimit();
  return formatResetTime(resetTimestamp);
}

/**
 * Get adjusted polling interval based on rate limit.
 *
 * @param baseInterval - The base polling interval in seconds
 * @param remaining - The remaining API calls
 * @returns Adjusted interval in seconds
 */
export function getAdjustedInterval(baseInterval: number, remaining: number): number {
  if (remaining < RATE_LIMIT_CRITICAL_THRESHOLD) {
    return baseInterval * 6; // 3 minutes if critical
  }
  if (remaining < RATE_LIMIT_WARNING_THRESHOLD) {
    return baseInterval * 4; // 2 minutes if low
  }
  if (remaining < RATE_LIMIT_ADJUST_THRESHOLD) {
    return baseInterval * 2; // 1 minute if moderately low
  }
  return baseInterval;
}

/**
 * Check if we should proactively use REST API instead of GraphQL.
 *
 * When remaining GraphQL requests fall below RATE_LIMIT_REST_PRIORITY_THRESHOLD,
 * this function returns true, indicating that REST API should be preferred.
 *
 * Note: REST API does not provide isResolved status for review threads.
 * Callers that need accurate resolution status should use GraphQL directly.
 *
 * @param logTransition - If true, log when entering/exiting REST priority mode
 * @param logEventFn - Optional callback function to log rate limit events
 * @returns True if REST API should be preferred, false otherwise
 */
export async function shouldPreferRestApi(
  logTransition = true,
  logEventFn?: (
    eventType: RateLimitEventType,
    remaining: number,
    limit: number,
    resetTimestamp: number,
    details: Record<string, unknown> | null,
  ) => void,
): Promise<boolean> {
  const { remaining, limit, resetTimestamp } = await checkRateLimit();

  // API check failed - don't switch modes
  if (limit === 0) {
    return false;
  }

  const shouldPreferRest = remaining < RATE_LIMIT_REST_PRIORITY_THRESHOLD;

  // Log state transitions
  if (logTransition) {
    if (shouldPreferRest && !restPriorityModeActive) {
      // Entering REST priority mode
      restPriorityModeActive = true;
      console.error(`⚡ REST優先モードに切り替え (残り: ${remaining}/${limit})`);
      if (logEventFn) {
        logEventFn("rest_priority_entered", remaining, limit, resetTimestamp, {
          threshold: RATE_LIMIT_REST_PRIORITY_THRESHOLD,
        });
      }
    } else if (!shouldPreferRest && restPriorityModeActive) {
      // Exiting REST priority mode
      restPriorityModeActive = false;
      console.error(`✓ GraphQLモードに復帰 (残り: ${remaining}/${limit})`);
      if (logEventFn) {
        logEventFn("rest_priority_exited", remaining, limit, resetTimestamp, {
          threshold: RATE_LIMIT_REST_PRIORITY_THRESHOLD,
        });
      }
    }
  }

  return shouldPreferRest;
}

// =============================================================================
// Warning and Logging Functions
// =============================================================================

/**
 * Print a warning message when rate limited.
 *
 * @param logEventFn - Optional callback function to log rate limit events
 */
export async function printRateLimitWarning(
  logEventFn?: (
    eventType: RateLimitEventType,
    remaining: number,
    limit: number,
    resetTimestamp: number,
    details: Record<string, unknown> | null,
  ) => void,
): Promise<void> {
  const { remaining, limit, resetTimestamp } = await checkRateLimit();
  const [, humanTime] = formatResetTime(resetTimestamp);
  console.error(`⚠️ GraphQL APIレート制限に達しました。リセットまで: ${humanTime}`);

  if (logEventFn) {
    logEventFn("limit_reached", remaining, limit, resetTimestamp, null);
  }
}

/**
 * Log a warning about rate limit status to console.
 *
 * @param remaining - Remaining API calls
 * @param limit - Total API call limit
 * @param resetTimestamp - Unix timestamp when the limit resets
 * @param jsonMode - If true, output structured JSON instead of plain text
 * @param logFn - Optional logging function for JSON output
 * @param logEventFn - Optional callback function to log rate limit events
 */
export function logRateLimitWarningToConsole(
  remaining: number,
  limit: number,
  resetTimestamp: number,
  jsonMode = false,
  logFn?: (message: string, jsonMode: boolean, data: Record<string, unknown> | null) => void,
  logEventFn?: (
    eventType: RateLimitEventType,
    remaining: number,
    limit: number,
    resetTimestamp: number,
    details: Record<string, unknown> | null,
  ) => void,
): void {
  // Format reset time in local timezone for user display
  const resetTime = new Date(resetTimestamp * 1000).toLocaleTimeString("ja-JP", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const percentage = limit > 0 ? (remaining / limit) * 100 : 0;

  if (remaining < RATE_LIMIT_CRITICAL_THRESHOLD) {
    const message = `[CRITICAL] API rate limit very low: ${remaining}/${limit} (${percentage.toFixed(1)}%), resets at ${resetTime}`;
    const data = { remaining, limit, reset_time: resetTime, level: "critical" };

    if (jsonMode && logFn) {
      logFn(message, jsonMode, data);
    } else {
      console.log(`⚠️  ${message}`);
      console.log("   Consider pausing operations.");
    }

    if (logEventFn) {
      logEventFn("warning", remaining, limit, resetTimestamp, { level: "critical" });
    }
  } else if (remaining < RATE_LIMIT_WARNING_THRESHOLD) {
    const message = `API rate limit low: ${remaining}/${limit} (${percentage.toFixed(1)}%), resets at ${resetTime}`;
    const data = { remaining, limit, reset_time: resetTime, level: "warning" };

    if (jsonMode && logFn) {
      logFn(message, jsonMode, data);
    } else {
      console.log(`⚠️  ${message}`);
    }

    if (logEventFn) {
      logEventFn("warning", remaining, limit, resetTimestamp, { level: "warning" });
    }
  }
}

/**
 * Log rate limit event to session file.
 *
 * Records rate limit events for post-session analysis.
 *
 * @param eventType - Type of event (warning, limit_reached, etc.)
 * @param remaining - Remaining API calls
 * @param limit - Total API call limit
 * @param resetTimestamp - Unix timestamp when the limit resets
 * @param sessionId - Claude session identifier
 * @param details - Additional details to include in the log entry
 */
export async function logRateLimitEvent(
  eventType: RateLimitEventType,
  remaining: number,
  limit: number,
  resetTimestamp: number,
  sessionId: string | null,
  details?: Record<string, unknown> | null,
): Promise<void> {
  if (!sessionId) {
    return;
  }

  const resetAt = new Date(resetTimestamp * 1000).toISOString();
  const usagePercent = limit > 0 ? Math.round((1 - remaining / limit) * 1000) / 10 : 0;

  const logEntry: Record<string, unknown> = {
    timestamp: new Date().toISOString(),
    session_id: sessionId,
    type: "rate_limit",
    operation: eventType,
    success: true, // Mark as success so analyze scripts don't count as failure
    details: {
      remaining,
      limit,
      reset_timestamp: resetTimestamp,
      reset_at: resetAt,
      usage_percent: usagePercent,
      ...details,
    },
  };

  await logToSessionFile(EXECUTION_LOG_DIR, "api-operations", sessionId, logEntry);
}

// =============================================================================
// Utility Functions
// =============================================================================

/**
 * Convert rate limit data to RateLimitInfo interface.
 *
 * @param remaining - Remaining API calls
 * @param limit - Total API call limit
 * @param resetTimestamp - Unix timestamp when the limit resets
 * @param resource - API resource type (core or graphql)
 * @returns RateLimitInfo object
 */
export function toRateLimitInfo(
  remaining: number,
  limit: number,
  resetTimestamp: number,
  resource: "core" | "graphql" = "graphql",
): RateLimitInfo {
  return {
    remaining,
    limit,
    resetTime: resetTimestamp,
    resource,
  };
}

/**
 * Clear the in-memory rate limit cache.
 *
 * Useful for testing or when forcing a fresh API call.
 */
export function clearRateLimitCache(): void {
  rateLimitCache = null;
  restPriorityModeActive = false;
}
