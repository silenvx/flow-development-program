#!/usr/bin/env python3
"""セッションID検証ユーティリティを提供する。

Why:
    セッションIDをファイルパスに使用する際のパストラバーサル攻撃を防止し、
    UUID/英数字ハッシュ形式のみを許可する。

What:
    - is_safe_session_id(): セッションIDの安全性検証

Remarks:
    - 許可文字: 英数字とハイフンのみ（UUID形式）
    - 空文字列、パス区切り文字、特殊文字は拒否
    - reflection-self-check.pyから共通化

Changelog:
    - silenvx/dekita#2282: パストラバーサル対策として追加
"""

import re


def is_safe_session_id(session_id: str) -> bool:
    """Validate session ID to prevent path traversal attacks.

    Session IDs should be UUIDs or alphanumeric hashes, containing only
    safe characters (alphanumeric and hyphens).

    Args:
        session_id: The session ID to validate.

    Returns:
        True if the session ID is safe to use in file paths, False otherwise.

    Examples:
        >>> is_safe_session_id("aac956f9-4701-4bca-98f4-d4f166716c73")
        True
        >>> is_safe_session_id("../../../etc/passwd")
        False
        >>> is_safe_session_id("")
        False
    """
    if not session_id:
        return False
    # Only allow alphanumeric characters and hyphens (UUID format)
    return bool(re.fullmatch(r"[a-zA-Z0-9-]+", session_id))
