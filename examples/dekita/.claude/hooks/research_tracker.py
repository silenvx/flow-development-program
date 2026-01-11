#!/usr/bin/env python3
"""セッション内のWebSearch/WebFetch使用を追跡。

Why:
    Issue/PR作成前に調査が行われたかを検証するため、
    Web検索活動をセッション単位で記録する必要がある。

What:
    - WebSearch/WebFetchツール使用を検出
    - 検索クエリ/URLをセッションマーカーファイルに記録
    - research-requirement-checkが後で参照

State:
    - writes: /tmp/claude-hooks/research-activity-{session_id}.json

Remarks:
    - 非ブロック型（PostToolUse、記録のみ）
    - research-requirement-checkと連携
    - クエリは200文字に切り詰めて記録

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#2545: HookContextパターン移行
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add hooks directory to path for common imports
sys.path.insert(0, str(Path(__file__).parent))


from common import SESSION_DIR
from lib.execution import log_hook_execution
from lib.session import HookContext, create_hook_context, parse_hook_input

HOOK_NAME = "research-tracker"

# グローバルコンテキスト（Issue #2545: HookContextパターン移行）
_ctx: HookContext | None = None


def get_research_activity_file():
    """Get session-specific research activity file path.

    Issue #2545: HookContextパターンに移行。グローバルの_ctxからsession_idを取得。
    """
    session_id = _ctx.get_session_id() if _ctx else None
    if not session_id:
        session_id = "unknown"
    return SESSION_DIR / f"research-activity-{session_id}.json"


def load_research_data() -> dict:
    """Load existing research activity data or create empty structure."""
    research_file = get_research_activity_file()
    try:
        if research_file.exists():
            with open(research_file, encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable file - start fresh with empty structure
        pass
    return {"activities": [], "session_start": datetime.now(UTC).isoformat()}


def save_research_data(data: dict) -> None:
    """Save research activity data atomically."""
    research_file = get_research_activity_file()
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = research_file.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        temp_file.rename(research_file)
    except OSError:
        # Fail silently - tracking is non-critical
        pass


def record_research_activity(tool_name: str, query: str) -> None:
    """Record a research activity (WebSearch/WebFetch) to session marker."""
    data = load_research_data()
    data["activities"].append(
        {
            "tool": tool_name,
            "query": query[:200] if query else "",
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )
    data["last_updated"] = datetime.now(UTC).isoformat()
    save_research_data(data)


def extract_query(input_data: dict) -> str:
    """Extract the search query or URL from tool input."""
    tool_input = input_data.get("tool_input", {})
    if isinstance(tool_input, dict):
        # WebSearch uses 'query', WebFetch uses 'url'
        return tool_input.get("query", "") or tool_input.get("url", "")
    return ""


def main() -> None:
    """Main entry point for the hook."""
    global _ctx
    try:
        input_data = parse_hook_input()
        # Issue #2545: HookContextパターンでsession_idを取得
        _ctx = create_hook_context(input_data)
    except json.JSONDecodeError:
        # Invalid input - approve and exit
        print(json.dumps({"continue": True}))
        return

    tool_name = input_data.get("tool_name", "")

    # Only track WebSearch and WebFetch
    if tool_name in ("WebSearch", "WebFetch"):
        query = extract_query(input_data)
        record_research_activity(tool_name, query)
        log_hook_execution(
            HOOK_NAME,
            "track",
            reason=f"Recorded {tool_name} activity",
            details={"tool": tool_name, "query_preview": query[:50]},
        )

    # Always continue (PostToolUse hook)
    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
