#!/usr/bin/env python3
"""タイムスタンプ関連のユーティリティ関数を提供する。

Why:
    ログ記録・トラッキングで一貫したタイムスタンプ形式を使用するため。

What:
    - get_local_timestamp(): ローカルタイムゾーンでISO形式取得
    - parse_iso_timestamp(): ISO 8601文字列をdatetimeにパース
    - generate_timestamp_id(): タイムスタンプベースの一意ID生成

Remarks:
    - ローカルタイムゾーンでログを出力（分析しやすさ重視）
    - GitHub CLI形式（Z suffix）とISO標準形式両方に対応

Changelog:
    - silenvx/dekita#1245: ローカルタイムゾーン対応
    - silenvx/dekita#1758: common.pyから分離
"""

from datetime import UTC, datetime


def get_local_timestamp() -> str:
    """Get current timestamp in local timezone ISO format.

    Issue #1245: Use local timezone for human-readable log analysis.
    Returns ISO 8601 format with timezone offset (e.g., 2025-12-28T05:35:40+09:00).
    """
    return datetime.now().astimezone().isoformat()


def parse_iso_timestamp(timestamp_str: str) -> datetime | None:
    """Parse ISO 8601 timestamp string to datetime.

    Handles both 'Z' suffix and '+00:00' offset formats commonly used by
    GitHub CLI and other APIs.

    Args:
        timestamp_str: ISO 8601 formatted timestamp string.
                      Examples: "2025-12-16T12:00:00Z", "2025-12-16T12:00:00+00:00"

    Returns:
        datetime object with timezone info, or None if parsing fails or input is empty.
    """
    if not timestamp_str:
        return None
    try:
        # Handle 'Z' suffix (GitHub CLI format)
        if timestamp_str.endswith("Z"):
            timestamp_str = timestamp_str[:-1] + "+00:00"
        return datetime.fromisoformat(timestamp_str)
    except (ValueError, TypeError):
        return None


def generate_timestamp_id(prefix: str = "") -> str:
    """Generate a unique ID combining timestamp and optional prefix.

    Args:
        prefix: Optional prefix for the ID.

    Returns:
        A unique identifier in format: {prefix}_YYYYMMDD-HHMMSS-ffffff
        or YYYYMMDD-HHMMSS-ffffff if no prefix.
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    if prefix:
        return f"{prefix}_{timestamp}"
    return timestamp
