/**
 * Event emission and logging for ci-monitor.
 *
 * Why:
 *   Provide consistent event handling and logging for CI monitoring:
 *   - Structured JSON event emission
 *   - Human-readable console logging
 *   - Background task logger integration
 *
 * What:
 *   - emitEvent(): Emit event to stdout in JSON format
 *   - createEvent(): Create MonitorEvent with current timestamp
 *   - log(): Print log message (JSON or text format)
 *
 * Remarks:
 *   - Migrated from Python ci_monitor/events.py (Issue #3261)
 *
 * Changelog:
 *   - silenvx/dekita#3261: TypeScript migration from Python
 */

import type { EventType, MonitorEvent } from "../../hooks/lib/types";

// =============================================================================
// Types
// =============================================================================

/** Background task logger callback signature */
export type BackgroundLogFn = (
  taskName: string,
  eventType: string,
  details: Record<string, unknown>,
) => void;

// =============================================================================
// Event Functions
// =============================================================================

/**
 * Convert MonitorEvent to dictionary format.
 *
 * @param event - The event to convert
 * @returns Dictionary representation of the event
 */
export function eventToDict(event: MonitorEvent): Record<string, unknown> {
  return {
    event: event.eventType,
    pr_number: event.prNumber,
    timestamp: event.timestamp,
    message: event.message,
    details: event.details,
    suggested_action: event.suggestedAction,
  };
}

/**
 * Convert MonitorEvent to JSON string.
 *
 * @param event - The event to convert
 * @returns JSON string representation of the event
 */
export function eventToJson(event: MonitorEvent): string {
  return JSON.stringify(eventToDict(event), null, 2);
}

/**
 * Emit an event to stdout in JSON format.
 *
 * @param event - The MonitorEvent to emit
 */
export function emitEvent(event: MonitorEvent): void {
  console.log(eventToJson(event));
}

/**
 * Create a MonitorEvent with current timestamp.
 *
 * Issue #1663: Also logs the event to background task logger for persistence.
 *
 * @param eventType - The type of event (from EventType enum)
 * @param prNumber - The PR number this event relates to
 * @param message - Human-readable message describing the event
 * @param details - Optional dictionary with additional event details
 * @param suggestedAction - Optional suggested action for the user
 * @param logBackgroundFn - Optional callback to log to background task logger
 * @returns A new MonitorEvent instance with current timestamp
 */
export function createEvent(
  eventType: EventType,
  prNumber: string,
  message: string,
  details?: Record<string, unknown>,
  suggestedAction?: string,
  logBackgroundFn?: BackgroundLogFn,
): MonitorEvent {
  const event: MonitorEvent = {
    eventType,
    prNumber,
    timestamp: new Date().toISOString(),
    message,
    details: details ?? {},
    suggestedAction: suggestedAction ?? "",
  };

  // Issue #1663: Log to background task logger for persistence
  if (logBackgroundFn) {
    try {
      logBackgroundFn("ci-monitor", eventType, {
        pr_number: prNumber,
        message,
        ...(details ?? {}),
      });
    } catch (error) {
      // Don't interrupt monitoring - just warn
      console.error(`Warning: Failed to log background event: ${error}`);
    }
  }

  return event;
}

/**
 * Print a log message.
 *
 * @param message - The message to log
 * @param jsonMode - If true, output as JSON to stderr. If false, output to stdout
 * @param data - Optional additional data to include in JSON output
 */
export function log(message: string, jsonMode = false, data?: Record<string, unknown>): void {
  // Intentionally using local time for user-facing console output.
  // Users expect to see timestamps in their local timezone (e.g., "[03:12:12]").
  const now = new Date();
  const timestamp = now.toLocaleTimeString("ja-JP", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  if (jsonMode) {
    const logData: Record<string, unknown> = {
      timestamp,
      message,
      type: "log",
      ...(data ?? {}),
    };
    console.error(JSON.stringify(logData));
  } else {
    console.log(`[${timestamp}] ${message}`);
  }
}
