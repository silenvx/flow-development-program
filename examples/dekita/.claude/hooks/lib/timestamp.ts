/**
 * タイムスタンプ関連のユーティリティ関数
 *
 * Why:
 *   ログ記録・トラッキングで一貫したタイムスタンプ形式を使用するため。
 *
 * What:
 *   - getLocalTimestamp(): ローカルタイムゾーンでISO形式取得
 *   - parseIsoTimestamp(): ISO 8601文字列をDateにパース
 *   - generateTimestampId(): タイムスタンプベースの一意ID生成
 *
 * Remarks:
 *   - ローカルタイムゾーンでログを出力（分析しやすさ重視）
 *   - GitHub CLI形式（Z suffix）とISO標準形式両方に対応
 *
 * Changelog:
 *   - silenvx/dekita#2866: Python版から移行
 */

/**
 * Get current timestamp in local timezone ISO format.
 *
 * Issue #1245: Use local timezone for human-readable log analysis.
 * Returns ISO 8601 format with timezone offset (e.g., 2025-12-28T05:35:40+09:00).
 */
export function getLocalTimestamp(): string {
  const now = new Date();
  const offset = -now.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const absOffset = Math.abs(offset);
  const hours = String(Math.floor(absOffset / 60)).padStart(2, "0");
  const minutes = String(absOffset % 60).padStart(2, "0");

  // Format: YYYY-MM-DDTHH:mm:ss±HH:mm
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  const hour = String(now.getHours()).padStart(2, "0");
  const minute = String(now.getMinutes()).padStart(2, "0");
  const second = String(now.getSeconds()).padStart(2, "0");

  return `${year}-${month}-${day}T${hour}:${minute}:${second}${sign}${hours}:${minutes}`;
}

/**
 * Parse ISO 8601 timestamp string to Date.
 *
 * Handles both 'Z' suffix and '+00:00' offset formats commonly used by
 * GitHub CLI and other APIs.
 *
 * @param timestampStr - ISO 8601 formatted timestamp string.
 *                       Examples: "2025-12-16T12:00:00Z", "2025-12-16T12:00:00+00:00"
 * @returns Date object, or null if parsing fails or input is empty.
 */
export function parseIsoTimestamp(timestampStr: string | null | undefined): Date | null {
  if (!timestampStr) {
    return null;
  }

  try {
    // Handle 'Z' suffix (GitHub CLI format) - Date constructor handles this natively
    const date = new Date(timestampStr);

    // Check if the date is valid
    if (Number.isNaN(date.getTime())) {
      return null;
    }

    return date;
  } catch {
    return null;
  }
}

/**
 * Generate a unique ID combining timestamp and optional prefix.
 *
 * @param prefix - Optional prefix for the ID.
 * @returns A unique identifier in format: {prefix}_YYYYMMDD-HHMMSS-ffffff
 *          or YYYYMMDD-HHMMSS-ffffff if no prefix.
 */
export function generateTimestampId(prefix = ""): string {
  const now = new Date();

  // Format in UTC: YYYYMMDD-HHMMSS-ffffff
  const year = now.getUTCFullYear();
  const month = String(now.getUTCMonth() + 1).padStart(2, "0");
  const day = String(now.getUTCDate()).padStart(2, "0");
  const hour = String(now.getUTCHours()).padStart(2, "0");
  const minute = String(now.getUTCMinutes()).padStart(2, "0");
  const second = String(now.getUTCSeconds()).padStart(2, "0");
  const micro = String(now.getUTCMilliseconds() * 1000).padStart(6, "0");

  const timestamp = `${year}${month}${day}-${hour}${minute}${second}-${micro}`;

  if (prefix) {
    return `${prefix}_${timestamp}`;
  }
  return timestamp;
}
