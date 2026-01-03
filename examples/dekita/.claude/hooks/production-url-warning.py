#!/usr/bin/env python3
"""本番環境URLへのアクセス前に警告・確認を促す。

Why:
    本番環境への誤アクセスは意図しない副作用を起こす可能性がある。
    また、類似ドメイン（dekita.pages.dev等）への誤アクセスを防ぐ。

What:
    - mcp__chrome-devtools__navigate_page/new_page を検出
    - URLが本番環境（dekita.app, api.dekita.app）なら警告表示
    - 間違ったURL（dekita.pages.dev等）の場合はブロック

Remarks:
    - 本番URL: 警告のみ（approve with systemMessage）
    - 間違ったURL: ブロック
    - CUSTOMIZE: PRODUCTION_HOSTNAMESを自プロジェクトに合わせて変更

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import sys
from urllib.parse import urlparse

from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input

# CUSTOMIZE: Production hostnames - Set these to your project's production domain(s)
PRODUCTION_HOSTNAMES = [
    "dekita.app",
    "api.dekita.app",
]

# CUSTOMIZE: Wrong hostnames to block - Add domains easily confused with production
WRONG_HOSTNAMES = [
    "dekita.pages.dev",  # Different app with same-ish name
]


def is_production_url(url: str) -> bool:
    """Check if URL is a production URL using precise hostname matching."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        return hostname.lower() in PRODUCTION_HOSTNAMES
    except Exception:
        return False


def is_wrong_url(url: str) -> str | None:
    """Check if URL is a known wrong URL. Returns correct URL suggestion if wrong."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if hostname.lower() in WRONG_HOSTNAMES:
            return "https://dekita.app"
    except Exception:
        pass  # Best effort - URL parsing may fail
    return None


def main():
    """PreToolUse hook for chrome-devtools navigation tools."""
    result = {"decision": "approve"}

    try:
        data = parse_hook_input()
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})

        # Only check navigation tools
        if tool_name in [
            "mcp__chrome-devtools__navigate_page",
            "mcp__chrome-devtools__new_page",
        ]:
            url = tool_input.get("url", "")

            # Check for wrong URLs first (block)
            correct_url = is_wrong_url(url)
            if correct_url:
                reason = (
                    f"⚠️ 間違ったURLが指定されています。\n\n"
                    f"指定URL: {url}\n"
                    f"正しいURL: {correct_url}\n\n"
                    f"dekita.pages.dev は別のアプリです。\n"
                    f"本プロジェクトの本番環境は dekita.app です。"
                )
                result = make_block_result("production-url-warning", reason)
            # Check for production URLs (warn, but allow)
            elif is_production_url(url):
                result = {
                    "decision": "approve",
                    "systemMessage": (
                        f"📍 本番環境にアクセスします: {url}\n"
                        "AGENTS.md「環境情報」セクションを参照してください。"
                    ),
                }

    except Exception as e:
        print(f"[production-url-warning] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve"}

    log_hook_execution(
        "production-url-warning",
        result.get("decision", "approve"),
        result.get("reason") or result.get("systemMessage"),
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
