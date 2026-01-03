"""Worktree management for ci-monitor.

This module handles git worktree detection and cleanup operations.
Extracted from ci-monitor.py as part of Issue #1754 refactoring.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


def get_worktree_info() -> tuple[str | None, str | None]:
    """Get worktree information if current directory is inside a worktree.

    Returns:
        Tuple of (main_repo_path, worktree_path) if in worktree, (None, None) otherwise.
    """
    try:
        # Get current working directory
        cwd = os.getcwd()

        # Get git worktree list
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None, None

        # Parse worktree list
        # Format: worktree /path\nHEAD abc123\nbranch refs/heads/xxx\n\n
        worktrees: list[str] = []
        main_repo: str | None = None
        for line in result.stdout.strip().split("\n"):
            if line.startswith("worktree "):
                path = line[9:]  # Remove "worktree " prefix
                if main_repo is None:
                    main_repo = path  # First entry is main repo
                else:
                    worktrees.append(path)

        if main_repo is None:
            return None, None

        # Check if cwd is inside a worktree (not main repo)
        cwd_real = os.path.realpath(cwd)
        for wt_path in worktrees:
            wt_real = os.path.realpath(wt_path)
            if cwd_real == wt_real or cwd_real.startswith(wt_real + os.sep):
                return main_repo, wt_path

        return None, None
    except Exception:  # noqa: BLE001 - Catch all for subprocess errors
        return None, None


def _is_exact_worktree_match(branch: str, wt_name: str) -> bool:
    """Check if branch name contains exact worktree name as a segment.

    Examples:
        - "fix/issue-1366-cleanup" matches "issue-1366" -> True
        - "fix/issue-13669" matches "issue-1366" -> False
        - "feature-1234" matches "123" -> False

    Args:
        branch: Branch name to check.
        wt_name: Worktree name to match.

    Returns:
        True if the branch contains the exact worktree name as a segment.
    """
    # Check for exact segment match (surrounded by non-alphanumeric or at boundaries)
    # Escape special regex characters in wt_name
    escaped = re.escape(wt_name)
    # Match only if wt_name is a complete segment (not part of a larger number/word)
    pattern = rf"(^|[^a-zA-Z0-9]){escaped}([^a-zA-Z0-9]|$)"
    return bool(re.search(pattern, branch))


def cleanup_worktree_after_merge(
    wt_path: str,
    main_repo: str,
    *,
    json_mode: bool = False,
    log_fn: Callable[[str, bool, dict[str, Any] | None], None] | None = None,
) -> bool:
    """Cleanup worktree after successful merge.

    Issue #1366: Prevent worktree from remaining after cleanup phase is skipped.

    Note: Issue #1478 - This function is no longer called from --merge command
    because subprocess deletion makes parent process's cwd invalid. However, it
    is kept for potential use by hooks (e.g., worktree-auto-cleanup.py) that run
    in the correct process context.

    Args:
        wt_path: Path to the worktree to remove.
        main_repo: Path to the main repository.
        json_mode: If True, log() outputs to stderr instead of stdout (for JSON output).
        log_fn: Optional logging function. Signature: (message, json_mode, data) -> None.

    Returns:
        True if cleanup succeeded, False otherwise.
    """

    def _log(message: str) -> None:
        """Internal log wrapper."""
        if log_fn:
            log_fn(message, json_mode, None)
        else:
            print(message)

    def _cleanup_branch(wt_name: str) -> None:
        """Delete local branch matching the worktree name (best-effort)."""
        try:
            # Get branches with exact issue number match (e.g., issue-1366)
            # Using --list with pattern, then filter for exact match
            branch_result = subprocess.run(
                ["git", "branch", "--list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if branch_result.returncode == 0 and branch_result.stdout.strip():
                for line in branch_result.stdout.strip().split("\n"):
                    branch = line.strip().lstrip("* ")
                    # Exact match: branch must contain the full wt_name as a segment
                    # e.g., "fix/issue-1366-xxx" matches "issue-1366"
                    # but "fix/issue-13669" does NOT match "issue-1366"
                    if branch and _is_exact_worktree_match(branch, wt_name):
                        # -d is safe: only deletes fully merged branches
                        del_result = subprocess.run(
                            ["git", "branch", "-d", branch],
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        if del_result.returncode == 0:
                            _log(f"🗑️ ブランチ {branch} を削除しました")
        except Exception as e:  # noqa: BLE001 - Best-effort cleanup
            # Branch cleanup is best-effort - log error but don't fail
            _log(f"⚠️ ブランチ削除中にエラー: {e}")

    try:
        # If cwd is inside worktree, move to main repo first
        cwd = os.getcwd()
        cwd_real = os.path.realpath(cwd)
        wt_real = os.path.realpath(wt_path)
        if cwd_real == wt_real or cwd_real.startswith(wt_real + os.sep):
            os.chdir(main_repo)
            _log(f"🔄 Moved to main repo: {main_repo}")

        # Unlock worktree first (ignore errors - may not be locked)
        subprocess.run(
            ["git", "worktree", "unlock", wt_path],
            capture_output=True,
            timeout=10,
        )

        # Try to remove worktree
        result = subprocess.run(
            ["git", "worktree", "remove", wt_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        wt_name = os.path.basename(wt_path)

        if result.returncode == 0:
            _log(f"✅ Worktree {wt_path} を削除しました")
            _cleanup_branch(wt_name)
            return True

        # Check if there are uncommitted changes before force removal
        # Force removal is only safe after successful merge (all changes committed)
        error_msg = result.stderr.strip()
        _log(f"⚠️ 通常削除失敗、強制削除を試行: {error_msg}")

        result = subprocess.run(
            ["git", "worktree", "remove", "-f", wt_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            _log(f"✅ Worktree {wt_path} を強制削除しました")
            _cleanup_branch(wt_name)
            return True

        _log(f"❌ Worktree削除失敗: {result.stderr.strip()}")
        return False

    except subprocess.TimeoutExpired:
        _log("❌ Worktree削除がタイムアウトしました")
        return False
    except Exception as e:  # noqa: BLE001 - Catch all for cleanup errors
        _log(f"❌ Worktree削除エラー: {e}")
        return False
