#!/usr/bin/env python3
"""探索深度（Read/Glob/Grep使用回数）を追跡する。

Why:
    十分なコード探索が行われていれば、明示的なWeb検索がなくても
    十分な調査が行われたと見なせる。探索深度を記録することで、
    research-requirement-check.pyがバイパス判断に利用できる。

What:
    - Read/Glob/Grepツールの使用を検出
    - セッションごとにカウントを記録
    - 合計探索回数を更新

State:
    - writes: .claude/state/session/exploration-depth-{session}.json

Remarks:
    - 記録型フック（ブロックしない、カウント記録）
    - PostToolUse:Read/Glob/Grepで発火
    - research-requirement-check.pyが探索深度をバイパス判断に利用
    - セッションごとに独立したカウント管理

Changelog:
    - silenvx/dekita#2545: HookContextパターンに移行
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

HOOK_NAME = "exploration-tracker"

# グローバルコンテキスト（Issue #2545: HookContextパターン移行）
_ctx: HookContext | None = None


def get_exploration_file():
    """Get session-specific exploration file path.

    Issue #2545: HookContextパターンに移行。グローバルの_ctxからsession_idを取得。
    """
    session_id = _ctx.get_session_id() if _ctx else None
    if not session_id:
        session_id = "unknown"
    return SESSION_DIR / f"exploration-depth-{session_id}.json"


def load_exploration_data() -> dict:
    """Load existing exploration data or create empty structure."""
    exploration_file = get_exploration_file()
    try:
        if exploration_file.exists():
            with open(exploration_file, encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable file - start fresh with empty structure
        pass
    return {
        "counts": {"Read": 0, "Glob": 0, "Grep": 0},
        "session_start": datetime.now(UTC).isoformat(),
    }


def save_exploration_data(data: dict) -> None:
    """Save exploration data atomically."""
    exploration_file = get_exploration_file()
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = exploration_file.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        temp_file.rename(exploration_file)
    except OSError:
        # Fail silently - tracking is non-critical
        pass


def increment_exploration(tool_name: str) -> dict:
    """Increment exploration count for a tool and return updated stats."""
    data = load_exploration_data()
    if tool_name in data["counts"]:
        data["counts"][tool_name] += 1
    data["total"] = sum(data["counts"].values())
    data["last_updated"] = datetime.now(UTC).isoformat()
    save_exploration_data(data)
    return data


def main() -> None:
    """Main entry point for the hook."""
    global _ctx
    try:
        input_data = parse_hook_input()
        # Issue #2545: HookContextパターンでsession_idを取得
        _ctx = create_hook_context(input_data)
    except json.JSONDecodeError:
        # Invalid input - continue without tracking
        print(json.dumps({"continue": True}))
        return

    tool_name = input_data.get("tool_name", "")

    # Only track exploration tools
    if tool_name in ("Read", "Glob", "Grep"):
        stats = increment_exploration(tool_name)
        log_hook_execution(
            HOOK_NAME,
            "track",
            reason=f"Recorded {tool_name} exploration",
            details={
                "tool": tool_name,
                "total": stats.get("total", 0),
            },
        )

    # Always continue (PostToolUse hook)
    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
