/**
 * Format an unknown error value for logging.
 *
 * Using `${error}` in template literals produces `[object Object]` for
 * non-primitive values.  For `Error` instances this returns the stack
 * trace (or message if stack is unavailable).  For all other types it
 * delegates to `String()`.
 */
export function formatError(error: unknown): string {
  if (error instanceof Error) {
    return error.stack ?? error.message;
  }
  return String(error);
}
