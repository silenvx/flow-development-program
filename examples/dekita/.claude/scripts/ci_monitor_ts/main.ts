#!/usr/bin/env bun
/**
 * CI Monitor TypeScript CLI Entry Point
 *
 * Why:
 *   TypeScript migration of ci_monitor.py CLI for unified TypeScript/Bun codebase.
 *   Provides the same functionality as the Python version.
 *
 * What:
 *   - CLI argument parsing
 *   - Single PR monitoring
 *   - Multi-PR parallel monitoring
 *
 * Usage:
 *   bun run .claude/scripts/ci_monitor_ts/main.ts <PR番号> [options]
 *
 * Options:
 *   --timeout <minutes>   Timeout in minutes (default: 30)
 *   --early-exit          Exit immediately when review comments are detected
 *   --session-id <uuid>   Claude session ID for log tracking
 *
 * Remarks:
 *   - Issue #3261: TypeScript migration from ci_monitor.py
 *   - Issue #2454: Simplified options (removed rarely-used options)
 *
 * Changelog:
 *   - silenvx/dekita#3261: Initial TypeScript CLI implementation
 */

import { parseArgs } from "node:util";
import { DEFAULT_TIMEOUT_MINUTES } from "../../hooks/lib/constants";
import { monitorPr } from "./main_loop";
import { checkSelfReference, monitorMultiplePrs } from "./monitor";
import { validatePrNumber } from "./pr_operations";
import { setSessionId } from "./session";

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Validate UUID format.
 */
function isValidSessionId(sessionId: string): boolean {
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  return uuidPattern.test(sessionId);
}

/**
 * Parse positive integer from string.
 */
function parsePositiveInt(value: string, name: string): number {
  const num = Number.parseInt(value, 10);
  if (Number.isNaN(num) || num < 1) {
    console.error(`Error: ${name} must be a positive integer, got: ${value}`);
    process.exit(1);
  }
  return num;
}

/**
 * Print usage information.
 */
function printUsage(): void {
  console.log(`
Usage: bun run .claude/scripts/ci_monitor_ts/main.ts <PR番号> [options]

Arguments:
  PR番号                One or more PR numbers to monitor

Options:
  --timeout <minutes>   Timeout in minutes (default: ${DEFAULT_TIMEOUT_MINUTES})
  --early-exit          Exit immediately when review comments are detected
  --session-id <uuid>   Claude session ID for log tracking
  --help, -h            Show this help message
`);
}

// =============================================================================
// Main Function
// =============================================================================

async function main(): Promise<void> {
  // Parse command line arguments
  const { values, positionals } = parseArgs({
    args: process.argv.slice(2),
    options: {
      timeout: {
        type: "string",
        default: String(DEFAULT_TIMEOUT_MINUTES),
      },
      "early-exit": {
        type: "boolean",
        default: false,
      },
      "session-id": {
        type: "string",
      },
      help: {
        type: "boolean",
        short: "h",
        default: false,
      },
    },
    allowPositionals: true,
  });

  // Show help
  if (values.help) {
    printUsage();
    process.exit(0);
  }

  // Validate PR numbers
  const prNumbers = positionals;
  if (prNumbers.length === 0) {
    console.error("Error: At least one PR number is required");
    printUsage();
    process.exit(1);
  }

  // Parse timeout
  const timeoutMinutes = parsePositiveInt(values.timeout as string, "--timeout");

  // Validate and set session ID
  const sessionId = values["session-id"] as string | undefined;
  if (sessionId) {
    if (!isValidSessionId(sessionId)) {
      console.error(`Error: --session-id must be a valid UUID, got: ${sessionId}`);
      process.exit(1);
    }
    setSessionId(sessionId);
  }

  // Validate PR numbers
  for (const prNumber of prNumbers) {
    const [valid, error] = validatePrNumber(prNumber);
    if (!valid) {
      console.error(`Error: Invalid PR number '${prNumber}': ${error}`);
      process.exit(1);
    }
  }

  // Check if any PR modifies ci-monitor itself
  for (const prNumber of prNumbers) {
    if (await checkSelfReference(prNumber)) {
      console.error(`
⚠️  Warning: PR #${prNumber} modifies ci-monitor itself.
   The running monitor may behave differently from the changes being tested.

   Recommended actions:
   1. Even if CI passes, consider re-verifying with the updated script
   2. Confirm tests are running against the changed code
   3. Monitor script behavior after merge
`);
    }
  }

  // Early exit flag
  const earlyExit = values["early-exit"] as boolean;

  // Single PR mode
  if (prNumbers.length === 1) {
    const prNumber = prNumbers[0];

    const result = await monitorPr(prNumber, timeoutMinutes, earlyExit);

    // Final output (always JSON - Issue #2454)
    const output: Record<string, unknown> = {
      success: result.success,
      message: result.message,
      rebase_count: result.rebase_count,
      review_completed: result.review_completed,
      ci_passed: result.ci_passed,
    };

    if (result.final_state) {
      output.final_state = {
        merge_state: result.final_state.mergeState,
        check_status: result.final_state.checkStatus,
        pending_reviewers: result.final_state.pendingReviewers,
        unresolved_threads: result.final_state.unresolvedThreads?.length || 0,
        review_comments_count: result.final_state.reviewComments?.length || 0,
      };
    }

    if (result.details) {
      output.details = result.details;
    }

    console.log(JSON.stringify(output, null, 2));
    process.exit(result.success ? 0 : 1);
  }

  // Multi-PR mode
  const events = await monitorMultiplePrs(prNumbers, 30, timeoutMinutes, true);

  // Output results
  const output = {
    mode: "multi-pr",
    pr_count: prNumbers.length,
    events: events.map((e) => ({
      pr_number: e.prNumber,
      event_type: e.event?.eventType || null,
      message: e.event?.message || null,
      state: e.state
        ? {
            merge_state: e.state.mergeState,
            check_status: e.state.checkStatus,
          }
        : null,
    })),
  };

  console.log(JSON.stringify(output, null, 2));

  // Exit with error if any event indicates failure
  // Include BEHIND/DIRTY/REVIEW_ERROR to match Python version behavior
  const hasFailure = events.some(
    (e) =>
      e.event?.eventType === "CI_FAILED" ||
      e.event?.eventType === "TIMEOUT" ||
      e.event?.eventType === "ERROR" ||
      e.event?.eventType === "BEHIND_DETECTED" ||
      e.event?.eventType === "DIRTY_DETECTED" ||
      e.event?.eventType === "REVIEW_ERROR",
  );
  process.exit(hasFailure ? 1 : 0);
}

// Run main
main().catch((error) => {
  console.error("Fatal error:", error);
  process.exit(1);
});
