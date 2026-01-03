#!/usr/bin/env python3
"""リサーチ・探索活動の追跡ユーティリティを提供する。

Why:
    PR作成前のリサーチ・コード探索が十分か判定するため、
    WebSearch/WebFetch/Read/Glob/Grepの使用状況を追跡する。

What:
    - check_research_done(): リサーチ実施有無を判定
    - get_exploration_depth(): 探索深度（Read/Glob/Grep回数）を取得
    - get_research_summary(): リサーチ活動のサマリーを取得

State:
    - reads: {session_dir}/research-activity-{session}.json
    - reads: {session_dir}/exploration-depth-{session}.json

Remarks:
    - MIN_EXPLORATION_FOR_BYPASS以上の探索で十分と判定
    - セッション毎にファイル分離で並行セッション対応
    - 破損ファイルは空として扱う（fail-open）

Changelog:
    - silenvx/dekita#613: リサーチ追跡を追加
    - silenvx/dekita#617: セッションIDでファイル分離
    - silenvx/dekita#1758: common.pyから分離
    - silenvx/dekita#2545: HookContextパターンに移行
"""

import json
from pathlib import Path

from lib.constants import MIN_EXPLORATION_FOR_BYPASS


def get_research_activity_file(session_dir: Path, session_id: str | None = None) -> Path:
    """Get session-specific research activity file path.

    Issue #617: Use session ID to isolate tracking between sessions.
    Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。

    Args:
        session_dir: Directory to store session files.
        session_id: Session ID for file isolation. Uses "unknown" if None.

    Returns:
        Path to the research activity file for the current session.
    """
    effective_session_id = session_id if session_id else "unknown"
    return session_dir / f"research-activity-{effective_session_id}.json"


def get_exploration_file(session_dir: Path, session_id: str | None = None) -> Path:
    """Get session-specific exploration file path.

    Issue #617: Use session ID to isolate tracking between sessions.
    Issue #2545: HookContextパターンに移行。session_idは呼び出し元から渡される。

    Args:
        session_dir: Directory to store session files.
        session_id: Session ID for file isolation. Uses "unknown" if None.

    Returns:
        Path to the exploration depth file for the current session.
    """
    effective_session_id = session_id if session_id else "unknown"
    return session_dir / f"exploration-depth-{effective_session_id}.json"


def check_research_done(session_dir: Path, session_id: str | None = None) -> bool:
    """Check if any research was done in this session.

    Args:
        session_dir: Directory where session files are stored.
        session_id: Session ID for file isolation. Uses "unknown" if None.

    Returns:
        True if WebSearch or WebFetch was used, False otherwise.
    """
    research_file = get_research_activity_file(session_dir, session_id)
    try:
        if research_file.exists():
            with open(research_file, encoding="utf-8") as f:
                data = json.load(f)
                return len(data.get("activities", [])) > 0
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable file - treat as no research done
        pass
    return False


def get_research_summary(session_dir: Path, session_id: str | None = None) -> dict:
    """Get summary of research activities in session.

    Args:
        session_dir: Directory where session files are stored.
        session_id: Session ID for file isolation. Uses "unknown" if None.

    Returns:
        dict with:
        - count: Number of research activities
        - tools_used: List of unique tools used (WebSearch, WebFetch)
        - has_research: True if any research was done
    """
    research_file = get_research_activity_file(session_dir, session_id)
    try:
        if research_file.exists():
            with open(research_file, encoding="utf-8") as f:
                data = json.load(f)
                activities = data.get("activities", [])
                return {
                    "count": len(activities),
                    "tools_used": list({a.get("tool") for a in activities if a.get("tool")}),
                    "has_research": len(activities) > 0,
                }
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable file - return empty summary
        pass
    return {"count": 0, "tools_used": [], "has_research": False}


def get_exploration_depth(session_dir: Path, session_id: str | None = None) -> dict:
    """Get current exploration depth stats.

    Args:
        session_dir: Directory where session files are stored.
        session_id: Session ID for file isolation. Uses "unknown" if None.

    Returns:
        dict with:
        - counts: {Read: N, Glob: N, Grep: N}
        - total: sum of all counts
        - sufficient: True if total >= MIN_EXPLORATION_FOR_BYPASS
    """
    exploration_file = get_exploration_file(session_dir, session_id)
    try:
        if exploration_file.exists():
            with open(exploration_file, encoding="utf-8") as f:
                data = json.load(f)
                counts = data.get("counts", {"Read": 0, "Glob": 0, "Grep": 0})
                total = sum(counts.values())
                return {
                    "counts": counts,
                    "total": total,
                    "sufficient": total >= MIN_EXPLORATION_FOR_BYPASS,
                }
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable file - return empty exploration stats
        pass
    return {
        "counts": {"Read": 0, "Glob": 0, "Grep": 0},
        "total": 0,
        "sufficient": False,
    }
