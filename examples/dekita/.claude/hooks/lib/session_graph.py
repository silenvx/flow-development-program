#!/usr/bin/env python3
"""fork-sessionコラボレーション用のセッショングラフ構築を行う。

Why:
    複数セッション間の関係（親子・兄弟）とworktree所有関係を把握し、
    競合を避けた並行作業を可能にする。

What:
    - get_worktree_session_map(): worktreeとセッションIDのマッピング取得
    - get_sibling_sessions(): 兄弟セッション（共通祖先を持つ）を検出
    - get_active_worktree_sessions(): worktreeを関係性でグループ化

State:
    - reads: {worktree}/.claude-session（セッションマーカー）
    - reads: ~/.claude/projects/*/*.jsonl（transcript）

Remarks:
    - worktree変更ファイルはgit diff origin/main...HEADで取得
    - transcriptパスは検証してパストラバーサル防止
    - 結果はlru_cacheでセッション中キャッシュ

Changelog:
    - silenvx/dekita#2513: fork-sessionコラボレーション機能追加
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from lib.constants import SESSION_MARKER_FILE, TIMEOUT_LIGHT, TIMEOUT_MEDIUM


def _is_valid_transcript_path(transcript_path: str) -> bool:
    """Validate that transcript path is within expected directories.

    This provides defense-in-depth against path traversal, even though
    transcript_path comes from Claude Code's internal hook system.

    Expected locations:
    - ~/.claude/projects/<project>/... (Claude Code transcript storage)

    Args:
        transcript_path: Path to validate.

    Returns:
        True if path is within expected directories.
    """
    try:
        resolved = Path(transcript_path).resolve()

        # Check if path is under ~/.claude (standard Claude Code location)
        claude_dir = Path.home() / ".claude"
        if claude_dir.resolve() in resolved.parents or resolved == claude_dir.resolve():
            return True

        # Also allow paths that contain .claude in the path (e.g., project/.claude/...)
        if ".claude" in resolved.parts:
            return True

        return False
    except (OSError, ValueError):
        return False


def _debug_log(message: str) -> None:
    """Output debug message to stderr if CLAUDE_DEBUG=1."""
    if os.environ.get("CLAUDE_DEBUG") == "1":
        print(message, file=sys.stderr)


@dataclass
class WorktreeInfo:
    """Information about a worktree and its session."""

    path: Path
    branch: str
    session_id: str | None = None
    issue_number: int | None = None
    changed_files: set[str] = field(default_factory=set)


def get_worktree_list() -> list[tuple[Path, str, bool]]:
    """Get list of worktrees from git.

    Returns:
        List of (path, branch, is_locked) tuples.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode != 0:
            return []

        worktrees: list[tuple[Path, str, bool]] = []
        current_path: Path | None = None
        current_branch = ""
        is_locked = False

        for line in result.stdout.strip().split("\n"):
            if line.startswith("worktree "):
                if current_path is not None:
                    worktrees.append((current_path, current_branch, is_locked))
                current_path = Path(line[9:])
                current_branch = ""
                is_locked = False
            elif line.startswith("branch refs/heads/"):
                current_branch = line[18:]
            elif line == "locked":
                is_locked = True

        if current_path is not None:
            worktrees.append((current_path, current_branch, is_locked))

        return worktrees
    except (subprocess.TimeoutExpired, OSError):
        return []


def get_worktree_session_id(worktree_path: Path) -> str | None:
    """Get session ID from worktree's .claude-session marker.

    Args:
        worktree_path: Path to the worktree.

    Returns:
        Session ID if marker exists and is valid, None otherwise.
    """
    marker_file = worktree_path / SESSION_MARKER_FILE
    try:
        if not marker_file.exists():
            return None

        data = json.loads(marker_file.read_text())
        return data.get("session_id")
    except (json.JSONDecodeError, OSError):
        return None


def extract_issue_number_from_branch(branch: str) -> int | None:
    """Extract issue number from branch name.

    Supports formats like:
    - feat/issue-123-description
    - fix/issue-456
    - issue-789

    Args:
        branch: Git branch name.

    Returns:
        Issue number if found, None otherwise.
    """
    import re

    match = re.search(r"\bissue-(\d+)\b", branch, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


@lru_cache(maxsize=32)
def get_worktree_changed_files(worktree_path: Path) -> frozenset[str]:
    """Get files changed in worktree compared to origin/main.

    Uses git diff to find all modified files. Results are cached
    for the duration of the session.

    Args:
        worktree_path: Path to the worktree.

    Returns:
        Frozen set of changed file paths (relative to repo root).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree_path), "diff", "--name-only", "origin/main...HEAD"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            # Fallback: try without the ... syntax (for uncommitted changes)
            result = subprocess.run(
                ["git", "-C", str(worktree_path), "diff", "--name-only", "origin/main"],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_MEDIUM,
            )
            if result.returncode != 0:
                return frozenset()

        files = {f.strip() for f in result.stdout.strip().split("\n") if f.strip()}
        return frozenset(files)
    except (subprocess.TimeoutExpired, OSError):
        return frozenset()


def get_worktree_session_map() -> dict[Path, WorktreeInfo]:
    """Get mapping of worktree paths to their session info.

    Returns:
        Dict mapping worktree path to WorktreeInfo.
    """
    worktrees = get_worktree_list()
    result: dict[Path, WorktreeInfo] = {}

    for path, branch, _is_locked in worktrees:
        # Skip main worktree (not in .worktrees directory)
        if ".worktrees" not in str(path):
            continue

        session_id = get_worktree_session_id(path)
        issue_number = extract_issue_number_from_branch(branch)
        changed_files = set(get_worktree_changed_files(path))

        result[path] = WorktreeInfo(
            path=path,
            branch=branch,
            session_id=session_id,
            issue_number=issue_number,
            changed_files=changed_files,
        )

    return result


def get_sibling_sessions(
    current_session_id: str,
    transcript_path: str | None,
) -> list[str]:
    """Find sibling sessions (sessions that share a common ancestor).

    A sibling session is one that:
    1. Has a different session ID from the current session
    2. Shares at least one common ancestor session ID

    Args:
        current_session_id: The current session's ID.
        transcript_path: Path to the current session's transcript.

    Returns:
        List of sibling session IDs.
    """
    from lib.session import get_session_ancestry

    if not transcript_path:
        return []

    # Validate transcript path (defense-in-depth against path traversal)
    if not _is_valid_transcript_path(transcript_path):
        _debug_log(f"Invalid transcript path rejected: {transcript_path}")
        return []

    # Get ancestry of current session
    current_ancestry = set(get_session_ancestry(transcript_path))
    if not current_ancestry:
        return []

    # Find transcript directory
    transcript_file = Path(transcript_path)
    if not transcript_file.exists():
        return []

    transcript_dir = transcript_file.parent

    # Scan other transcripts in the same directory
    siblings: list[str] = []
    try:
        for jsonl_file in transcript_dir.glob("*.jsonl"):
            file_session_id = jsonl_file.stem

            # Skip current session
            if file_session_id == current_session_id:
                continue

            # Get ancestry of this session
            other_ancestry = set(get_session_ancestry(str(jsonl_file)))

            # Check for common ancestors (excluding the sessions themselves)
            common = (current_ancestry - {current_session_id}) & (
                other_ancestry - {file_session_id}
            )
            if common:
                siblings.append(file_session_id)
    except OSError:
        # Fail silently if transcript directory is inaccessible (e.g., permissions)
        pass

    return siblings


def get_active_worktree_sessions(
    current_session_id: str,
    transcript_path: str | None,
) -> dict[str, list[WorktreeInfo]]:
    """Get worktrees grouped by their relationship to current session.

    Categories:
    - "ancestor": Worktrees owned by ancestor sessions
    - "sibling": Worktrees owned by sibling sessions
    - "unknown": Worktrees with unknown or no session marker

    Args:
        current_session_id: The current session's ID.
        transcript_path: Path to the current session's transcript.

    Returns:
        Dict with category keys and lists of WorktreeInfo values.
    """
    from lib.session import get_session_ancestry

    worktree_map = get_worktree_session_map()

    # Validate transcript path (defense-in-depth against path traversal)
    if transcript_path and not _is_valid_transcript_path(transcript_path):
        _debug_log(f"Invalid transcript path rejected: {transcript_path}")
        transcript_path = None  # Treat as no transcript

    # Get ancestry and siblings
    ancestry = set(get_session_ancestry(transcript_path) if transcript_path else [])
    siblings = set(get_sibling_sessions(current_session_id, transcript_path))

    result: dict[str, list[WorktreeInfo]] = {
        "ancestor": [],
        "sibling": [],
        "unknown": [],
    }

    for info in worktree_map.values():
        if info.session_id is None:
            result["unknown"].append(info)
        elif info.session_id == current_session_id:
            # Current session's worktree - skip
            continue
        elif info.session_id in ancestry:
            result["ancestor"].append(info)
        elif info.session_id in siblings:
            result["sibling"].append(info)
        else:
            result["unknown"].append(info)

    return result
