#!/usr/bin/env python3
"""Git関連のユーティリティ関数を提供する。

Why:
    ブランチ、コミット、worktree操作で共通して必要なgit操作を
    一元化し、各フックでの重複実装を防ぐ。

What:
    - get_current_branch(): 現在のブランチ名取得
    - get_head_commit(): HEADコミットハッシュ取得
    - get_diff_hash(): 差分のハッシュ取得（リベース検出用）
    - get_default_branch(): デフォルトブランチ検出
    - check_recent_commits(): 直近コミットの有無確認
    - check_uncommitted_changes(): 未コミット変更の確認

Remarks:
    - タイムアウトはconstants.pyの定数を使用
    - エラー時はNone/False/0を返すfail-open設計
    - worktree判定に使用される重要なモジュール

Changelog:
    - silenvx/dekita#930: main分岐後のコミット判定を追加
    - silenvx/dekita#934: デフォルトブランチ動的検出を追加
"""

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .constants import RECENT_COMMIT_THRESHOLD_SECONDS, TIMEOUT_LIGHT, TIMEOUT_MEDIUM


def get_current_branch() -> str | None:
    """Get the current git branch name.

    Returns:
        Branch name or None if not in a git repository or on error.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        # Ignore all exceptions: failure to get branch is non-fatal
        pass
    return None


def get_head_commit() -> str | None:
    """Get the current HEAD commit hash (short form).

    Returns:
        Short commit hash (7 chars) or None if not in a git repository or on error.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        # Ignore all exceptions: failure to get commit is non-fatal
        pass
    return None


def get_diff_hash(base_branch: str = "main") -> str | None:
    """Get a hash of the current diff against the base branch.

    This is used to detect if the actual code changes are the same even after
    a rebase (which changes commit hashes but not the diff content).

    Args:
        base_branch: The base branch to compare against (default: "main").

    Returns:
        SHA-256 hash of the diff (first 12 chars) or None on error.
    """
    try:
        result = subprocess.run(
            ["git", "diff", base_branch],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            diff_content = result.stdout
            diff_hash = hashlib.sha256(diff_content.encode()).hexdigest()[:12]
            return diff_hash
    except Exception:
        # Ignore all exceptions: failure to get diff hash is non-fatal
        pass
    return None


def get_default_branch(worktree_path: Path) -> str | None:
    """Get the default branch name for the repository.

    Issue #934: Dynamically detect the default branch instead of hardcoding "main".

    Args:
        worktree_path: Path to the worktree to check.

    Returns:
        The default branch name (e.g., "main", "master"), or None if unable to determine.

    Detection strategy:
        1. Try `git symbolic-ref refs/remotes/origin/HEAD` (most reliable)
        2. Fallback to checking if "main" branch exists
        3. Fallback to checking if "master" branch exists
    """
    try:
        # Strategy 1: Use symbolic-ref to get the default branch from origin
        result = subprocess.run(
            [
                "git",
                "-C",
                str(worktree_path),
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )
        if result.returncode == 0:
            # Output is like "refs/remotes/origin/main"
            ref = result.stdout.strip()
            if ref.startswith("refs/remotes/origin/"):
                return ref.removeprefix("refs/remotes/origin/")

        # Strategy 2: Check if "main" branch exists
        result = subprocess.run(
            [
                "git",
                "-C",
                str(worktree_path),
                "rev-parse",
                "--verify",
                "main",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )
        if result.returncode == 0:
            return "main"

        # Strategy 3: Check if "master" branch exists
        result = subprocess.run(
            [
                "git",
                "-C",
                str(worktree_path),
                "rev-parse",
                "--verify",
                "master",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )
        if result.returncode == 0:
            return "master"

        return None
    except (subprocess.TimeoutExpired, OSError):
        return None


def get_commits_since_default_branch(worktree_path: Path) -> int | None:
    """Get the number of commits since diverging from the default branch.

    Issue #934: Dynamically detect the default branch instead of hardcoding "main".

    Args:
        worktree_path: Path to the worktree to check.

    Returns:
        Number of commits since default branch, or None if unable to determine.
        Returns None if the default branch cannot be detected or doesn't exist.
    """
    try:
        default_branch = get_default_branch(worktree_path)
        if not default_branch:
            return None

        result = subprocess.run(
            [
                "git",
                "-C",
                str(worktree_path),
                "rev-list",
                f"{default_branch}..HEAD",
                "--count",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
        return None
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def check_recent_commits(worktree_path: Path) -> tuple[bool, str | None]:
    """Check if there are recent commits (within threshold).

    Used by both locked-worktree-guard and worktree-removal-check
    to detect active work in a worktree.

    Issue #930: Only considers commits made after diverging from main branch.
    If no commits exist since main, returns False (no active work).
    This prevents false positives when a new worktree is created and immediately deleted.

    Args:
        worktree_path: Path to the worktree to check.

    Returns:
        Tuple of (has_recent_commits, last_commit_info).
        On timeout/error, returns (True, "(確認タイムアウト)") for fail-close.
    """
    try:
        # Issue #930: Check if there are any commits since diverging from main
        # If no diverged commits, this is a fresh worktree with no actual work
        diverged_count = get_commits_since_default_branch(worktree_path)
        if diverged_count == 0:
            # No commits since main = no actual work in this worktree
            return False, None

        # Use tab delimiter since %ar contains spaces (e.g., "5 minutes ago")
        result = subprocess.run(
            [
                "git",
                "-C",
                str(worktree_path),
                "log",
                "-1",
                "--format=%ct\t%ar\t%s",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if result.returncode != 0 or not result.stdout.strip():
            return False, None

        parts = result.stdout.strip().split("\t", 2)
        if len(parts) < 3:
            return False, None

        commit_timestamp = int(parts[0])
        relative_time = parts[1]
        subject = parts[2]

        now = datetime.now(UTC).timestamp()
        age_seconds = now - commit_timestamp

        if age_seconds < RECENT_COMMIT_THRESHOLD_SECONDS:
            return True, f"{relative_time}: {subject[:50]}"

        return False, None

    except (subprocess.TimeoutExpired, OSError, ValueError):
        # Fail-close: タイムアウト時は安全側に倒す（確認できなかった = 危険と判断）
        return True, "(確認タイムアウト)"


def check_uncommitted_changes(worktree_path: Path) -> tuple[bool, int]:
    """Check for uncommitted changes in a worktree.

    Used by both locked-worktree-guard and worktree-removal-check
    to detect active work in a worktree.

    Args:
        worktree_path: Path to the worktree to check.

    Returns:
        Tuple of (has_changes, change_count).
        On timeout/error, returns (True, -1) for fail-close.
        -1 indicates a timeout occurred.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree_path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if result.returncode != 0:
            return False, 0

        lines = [line for line in result.stdout.strip().split("\n") if line]
        return len(lines) > 0, len(lines)

    except (subprocess.TimeoutExpired, OSError):
        # Fail-close: タイムアウト時は安全側に倒す
        return True, -1  # -1 は確認タイムアウトを示す
