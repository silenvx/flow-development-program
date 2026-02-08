/**
 * Session management re-exports for ci-monitor.
 *
 * Why:
 *   Provide backward-compatible exports while keeping actual implementation in lib/session.ts.
 *   This resolves the dependency direction issue (lib should not depend on scripts).
 *
 * What:
 *   - Re-exports setCiMonitorSessionId as setSessionId
 *   - Re-exports getCiMonitorSessionId as getSessionId
 *
 * Remarks:
 *   - Original implementation moved to lib/session.ts (Issue #3261)
 *
 * Changelog:
 *   - silenvx/dekita#3261: TypeScript migration from Python
 */

export {
  setCiMonitorSessionId as setSessionId,
  getCiMonitorSessionId as getSessionId,
} from "../../hooks/lib/session";
